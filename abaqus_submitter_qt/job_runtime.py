"""Single-job runtime controller for QProcess and STA polling."""

from __future__ import annotations

import os
import sys
import time

from .abaqus_diagnostics import (
    classify_job_text,
    format_sta_output_for_log,
    parse_sta_progress,
    update_abaqus_stage_from_text,
)
from .constants import SOLVER_START_GRACE_SECONDS, STA_POLL_INTERVAL_MS
from .process_scanner import get_psutil_process_snapshot
from .qt_compat import QtCore, Signal, hang_probe, hang_probe_function, hang_probe_log
from .runtime_evidence import (
    collect_runtime_evidence,
    runtime_completion_ready,
    runtime_termination_ready,
    update_file_stability,
    update_runtime_phase,
)


VS_INIT_NOISE_PREFIXES = (
    "[DEBUG:ext\\vcvars.bat] Found potential",
    "[DEBUG:ext\\vcvars.bat] Testing",
    "[vcvarsall.bat] Environment initialized for:",
)

VS_INIT_NOISE_SUBSTRINGS = (
    "Visual Studio 2026 Developer Command Prompt",
    "Copyright (c) 2025 Microsoft Corporation",
    "WARNING: vars.bat does not set up dependencies when invoked directly.",
)

MAX_CONSOLE_OUTPUT_CHARS = 12000
LAUNCHER_START_TIMEOUT_MS = 3000
PROCESS_SCAN_ACTIVE_INTERVAL_MS = 8000
PROCESS_SCAN_IDLE_INTERVAL_MS = 10000
PROCESS_SNAPSHOT_MAX_AGE_SECONDS = 20.0
_ACTIVE_PROCESS_SCAN_THREADS: set[QtCore.QThread] = set()


class ProcessScanWorker(QtCore.QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    done = Signal()

    def run(self) -> None:
        try:
            rows = get_psutil_process_snapshot(force=True, include_details=True)
        except Exception as exc:  # pragma: no cover - defensive for psutil edge cases
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(rows)
        finally:
            self.done.emit()


class ProcessScanService(QtCore.QObject):
    snapshotReady = Signal(object)
    scanFailed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(PROCESS_SCAN_IDLE_INTERVAL_MS)
        self._timer.timeout.connect(self.request_scan)
        self._active = False
        self._closing = False
        self._thread: QtCore.QThread | None = None
        self._worker: ProcessScanWorker | None = None
        self._latest_rows: list[dict] | None = None
        self._latest_at: float = 0.0

    def set_active(self, active: bool) -> None:
        if self._closing:
            return
        self._active = active
        self._timer.setInterval(PROCESS_SCAN_ACTIVE_INTERVAL_MS if active else PROCESS_SCAN_IDLE_INTERVAL_MS)
        if active:
            if not self._timer.isActive():
                self._timer.start()
            self.request_scan()
        else:
            self._timer.stop()

    def latest_snapshot(self, *, max_age: float = PROCESS_SNAPSHOT_MAX_AGE_SECONDS) -> list[dict] | None:
        if self._latest_rows is None:
            return None
        if time.monotonic() - self._latest_at > max_age:
            return None
        return self._latest_rows

    def request_scan(self) -> None:
        if self._closing or self._thread is not None:
            return
        thread = QtCore.QThread()
        worker = ProcessScanWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self.on_snapshot_ready)
        worker.failed.connect(self.on_scan_failed)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(self.on_thread_finished)
        thread.finished.connect(lambda thread=thread: _ACTIVE_PROCESS_SCAN_THREADS.discard(thread))
        thread.finished.connect(thread.deleteLater)
        _ACTIVE_PROCESS_SCAN_THREADS.add(thread)
        self._thread = thread
        self._worker = worker
        thread.start()

    def on_snapshot_ready(self, rows: object) -> None:
        if self._closing:
            return
        self._latest_rows = list(rows or [])
        self._latest_at = time.monotonic()
        self.snapshotReady.emit(self._latest_rows)

    def on_scan_failed(self, message: str) -> None:
        if self._closing:
            return
        self.scanFailed.emit(message)

    def on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        if self._active and not self._closing and not self._timer.isActive():
            self._timer.start()

    def shutdown(self) -> None:
        self._closing = True
        self._active = False
        self._timer.stop()


def run_is_datacheck(run: dict) -> bool:
    queue_item = run.get("queue_item")
    return bool(run.get("datacheck_only") or getattr(queue_item, "datacheck_only", False))


def solver_start_grace_elapsed(run: dict, now: float | None = None) -> bool:
    launcher_finished_at = run.get("launcher_finished_monotonic")
    if launcher_finished_at is None:
        return False
    try:
        elapsed = (time.monotonic() if now is None else now) - float(launcher_finished_at)
    except (TypeError, ValueError):
        return False
    return elapsed >= SOLVER_START_GRACE_SECONDS


def mark_solver_start_timeout(run: dict) -> None:
    run["solver_start_timeout"] = True
    run["solver_start_timeout_detail"] = f"后台求解器未在 {SOLVER_START_GRACE_SECONDS} 秒内启动"


def mark_runtime_completion_confirmed(run: dict, reason: str) -> None:
    run["runtime_completion_confirmed"] = True
    run["runtime_completion_reason"] = reason
    run["runtime_phase"] = "COMPLETED"


def is_visual_studio_init_noise_line(line: str) -> bool:
    stripped = line.strip()
    if any(stripped.startswith(prefix) for prefix in VS_INIT_NOISE_PREFIXES):
        return True
    return any(marker in stripped for marker in VS_INIT_NOISE_SUBSTRINGS)


def is_star_separator_line(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 20 and set(stripped) == {"*"}


def filter_launcher_output_noise(text: str, run: dict) -> str:
    """Hide Visual Studio environment banner lines while keeping compiler errors."""
    kept_lines: list[str] = []
    pending_star_separator = run.pop("_pending_star_separator", "")
    suppress_next_vs_star_separator = bool(run.get("_suppress_next_vs_star_separator"))

    for line in text.splitlines():
        if is_star_separator_line(line):
            if suppress_next_vs_star_separator:
                run["_suppress_next_vs_star_separator"] = False
                suppress_next_vs_star_separator = False
                continue
            if pending_star_separator:
                kept_lines.append(pending_star_separator)
            pending_star_separator = line
            continue

        if is_visual_studio_init_noise_line(line):
            pending_star_separator = ""
            if "Visual Studio" in line or "Copyright" in line:
                run["_suppress_next_vs_star_separator"] = True
                suppress_next_vs_star_separator = True
            continue

        if pending_star_separator:
            kept_lines.append(pending_star_separator)
            pending_star_separator = ""

        kept_lines.append(line)

    if pending_star_separator:
        run["_pending_star_separator"] = pending_star_separator
    else:
        run.pop("_pending_star_separator", None)

    return "\n".join(kept_lines)


def cache_console_output(run: dict, text: str) -> None:
    """缓存 Abaqus 控制台输出，用于没有 .sta 时判断提交错误。"""
    if run.get("finalized"):
        return

    console_output = run.get("console_output", "")
    run["console_output"] = (console_output + "\n" + text)[-MAX_CONSOLE_OUTPUT_CHARS:]


def mark_console_failure_from_text(run: dict, text: str) -> None:
    """控制台检测到错误时只做标记，不直接结束作业。"""
    if run.get("finalized") or run.get("terminating"):
        return

    final_status, detail = classify_job_text(text)

    if final_status == "失败":
        run["console_failed"] = True
        run["console_failed_detail"] = detail


class JobRuntimeController(QtCore.QObject):
    jobLogReceived = Signal(str, str)
    historyEvent = Signal(str)
    jobUpdated = Signal(str)
    jobFinished = Signal(str)
    processError = Signal(str, str)

    def __init__(
        self,
        *,
        memory_adapter,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.memory_adapter = memory_adapter
        self.runs: dict[str, dict] = {}
        self.process_scan_service = ProcessScanService(self)
        self.process_scan_service.scanFailed.connect(
            lambda message: self.historyEvent.emit(f"Process snapshot scan failed: {message}")
        )

    def register_run(
        self,
        job_key: str,
        run: dict,
    ) -> None:
        self.runs[job_key] = run
        self.process_scan_service.set_active(True)

    def unregister_run(
        self,
        job_key: str,
    ) -> None:
        run = self.runs.pop(
            job_key,
            None,
        )
        if run is None:
            self.process_scan_service.set_active(bool(self.runs))
            return

        timer = run.get("timer")
        run["timer"] = None
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass
            try:
                timer.deleteLater()
            except RuntimeError:
                pass

        process_connections = tuple(run.pop("_process_connections", ()) or ())
        for signal, callback in process_connections:
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass

        process = run.get("process")
        run["process"] = None
        if process is None:
            self.process_scan_service.set_active(bool(self.runs))
            return

        try:
            process_running = process.state() != QtCore.QProcess.ProcessState.NotRunning
        except RuntimeError:
            self.process_scan_service.set_active(bool(self.runs))
            return

        if process_running:
            try:
                process.finished.connect(process.deleteLater)
            except (RuntimeError, TypeError):
                pass
            self.process_scan_service.set_active(bool(self.runs))
            return

        try:
            process.deleteLater()
        except RuntimeError:
            pass
        self.process_scan_service.set_active(bool(self.runs))

    def shutdown(self) -> None:
        self.process_scan_service.shutdown()

    def start_process(
        self,
        *,
        job_key: str,
        run: dict,
        command: str,
    ) -> bool:
        probe_start = time.monotonic()
        process = QtCore.QProcess(self)
        process.setWorkingDirectory(run["work_dir"])
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)

        timer = QtCore.QTimer(self)
        timer.setInterval(STA_POLL_INTERVAL_MS)

        run["process"] = process
        run["timer"] = timer
        run.setdefault("activity_seen", False)
        run.setdefault("solver_started", False)
        run.setdefault("solver_kind", "")
        run.setdefault("known_solver_pids", ())
        run.setdefault("solver_pid_seen_at", None)
        run.setdefault("solver_pid_last_seen_at", None)
        run.setdefault("solver_pid_confidence", "")
        run.setdefault("runtime_phase", "STARTING")
        run.setdefault("runtime_started_monotonic", time.monotonic())
        run.setdefault("last_runtime_activity_at", time.monotonic())
        run.setdefault("runtime_phase_text_pending", "")
        run.setdefault("runtime_diagnostic_status", "")
        run.setdefault("runtime_diagnostic_detail", "")
        run.setdefault("log_position", 0)
        run.setdefault("msg_position", 0)
        run.setdefault("dat_position", 0)
        run.setdefault("launcher_finished_at", None)
        run.setdefault("launcher_finished_monotonic", None)
        run.setdefault("launcher_started", False)
        run.setdefault("launcher_started_monotonic", None)
        run.setdefault("solver_start_timeout", False)
        run.setdefault("solver_start_timeout_detail", "")
        run.setdefault("seen_sta", False)
        run.setdefault("sta_valid", False)
        run.setdefault("datacheck_stable_polls", 0)
        run.setdefault("sta_signature", None)
        run.setdefault("sta_stable_polls", 0)
        run.setdefault("finish_candidate_since", None)
        run.setdefault("termination_stable_polls", 0)
        run.setdefault("termination_candidate_since", None)
        run.setdefault("runtime_completion_confirmed", False)
        run.setdefault("runtime_completion_reason", "")
        run.setdefault("finish_emitted", False)
        self.register_run(
            job_key,
            run,
        )

        timer_callback = lambda key=job_key: self.poll_sta_file(key)
        output_callback = lambda key=job_key: self.read_process_output(key)
        started_callback = lambda key=job_key: self.on_process_started(key)
        finished_callback = (
            lambda exit_code, exit_status, key=job_key: self.on_process_finished(
                key,
                exit_code,
                exit_status,
            )
        )
        error_callback = lambda error, key=job_key: self.on_process_error(key, error)
        timer.timeout.connect(timer_callback)
        process.readyReadStandardOutput.connect(output_callback)
        process.started.connect(started_callback)
        process.finished.connect(finished_callback)
        process.errorOccurred.connect(error_callback)
        run["_process_connections"] = (
            (process.readyReadStandardOutput, output_callback),
            (process.started, started_callback),
            (process.finished, finished_callback),
            (process.errorOccurred, error_callback),
        )

        start_step = time.monotonic()
        if os.name == "nt":
            process.start("cmd.exe", ["/c", command])
        else:
            process.start("/bin/sh", ["-lc", command])
        hang_probe_log(
            "JobRuntimeController.start_process.step",
            time.monotonic() - start_step,
            threshold=0.0,
            job_key=job_key,
            job_name=run.get("job_name"),
            step="process.start",
        )

        QtCore.QTimer.singleShot(
            LAUNCHER_START_TIMEOUT_MS,
            lambda key=job_key: self.on_process_start_timeout(key),
        )

        hang_probe_log(
            "JobRuntimeController.start_process",
            time.monotonic() - probe_start,
            threshold=0.2,
            job_key=job_key,
            job_name=run.get("job_name"),
        )
        return True

    def on_process_started(self, job_key: str) -> None:
        with hang_probe("JobRuntimeController.on_process_started", job_key=job_key):
            run = self.runs.get(job_key)
            if not run:
                return
            run["launcher_started"] = True
            run["launcher_started_monotonic"] = time.monotonic()
            timer = run.get("timer")
            if timer is not None and not timer.isActive():
                timer.start()
            self.jobUpdated.emit(job_key)

    def on_process_error(self, job_key: str, error) -> None:  # noqa: ANN001
        with hang_probe("JobRuntimeController.on_process_error", job_key=job_key):
            self.handle_process_start_failure(
                job_key,
                f"QProcess 启动或运行错误：{error}",
                queue_message="Abaqus launcher 启动失败",
            )

    def on_process_start_timeout(self, job_key: str) -> None:
        with hang_probe("JobRuntimeController.on_process_start_timeout", job_key=job_key):
            run = self.runs.get(job_key)
            if not run or run.get("launcher_started") or run.get("finish_emitted") or run.get("finalized"):
                return
            process = run.get("process")
            if process is not None:
                try:
                    if process.state() != QtCore.QProcess.ProcessState.NotRunning:
                        return
                except RuntimeError:
                    return
            self.handle_process_start_failure(
                job_key,
                "QProcess 启动请求发出后超时，未收到 started/errorOccurred/finished",
                queue_message="Abaqus launcher 启动超时",
            )

    def handle_process_start_failure(
        self,
        job_key: str,
        message: str,
        *,
        queue_message: str,
    ) -> None:
        run = self.runs.get(job_key)
        self.processError.emit(job_key, message)
        if not run or run.get("finish_emitted") or run.get("finalized"):
            return
        run["launcher_finished"] = True
        run["launcher_exit_code"] = -1
        run["launcher_finished_at"] = time.time()
        run["launcher_finished_monotonic"] = time.monotonic()
        run["console_failed"] = True
        run["console_failed_detail"] = message
        queue_item = run.get("queue_item")
        if queue_item is not None:
            queue_item.message = queue_message
        self.emit_job_finished_once(job_key, run)

    def read_process_output(
        self,
        job_key: str,
    ) -> None:
        run = self.runs.get(job_key)

        if not run:
            return

        process = run["process"]

        encoding = "mbcs" if os.name == "nt" else sys.getdefaultencoding()

        data = bytes(process.readAllStandardOutput()).decode(
            encoding,
            errors="replace",
        )

        data = data.rstrip("\r\n")
        data = filter_launcher_output_noise(data, run)

        if not data:
            return

        cache_console_output(run, data)
        update_abaqus_stage_from_text(run, data)
        pending_text = str(run.get("runtime_phase_text_pending", "") or "")
        run["runtime_phase_text_pending"] = (pending_text + "\n" + data)[
            -MAX_CONSOLE_OUTPUT_CHARS:
        ]
        mark_console_failure_from_text(
            run,
            run.get("console_output", data),
        )
        self.jobUpdated.emit(job_key)

        self.jobLogReceived.emit(
            job_key,
            data,
        )

    def on_process_finished(self, job_key: str, exit_code: int, exit_status) -> None:  # noqa: ANN001
        with hang_probe(
            "JobRuntimeController.on_process_finished",
            job_key=job_key,
            exit_code=exit_code,
        ):
            run = self.runs.get(job_key)
            if not run:
                return
            run["launcher_finished"] = True
            run["launcher_exit_code"] = exit_code
            run["launcher_exit_status"] = exit_status
            run["launcher_finished_at"] = time.time()
            run["launcher_finished_monotonic"] = time.monotonic()
            queue_item = run.get("queue_item")
            if queue_item is not None:
                queue_item.message = "Launcher command finished; monitoring background solver state."
            self.historyEvent.emit(
                f"{run['job_name']} launcher command finished: exit_code={exit_code}; monitoring background solver state."
            )
            timer = run.get("timer")
            if timer is not None and not timer.isActive():
                timer.start()
            self.jobUpdated.emit(job_key)

    def emit_job_finished_once(self, job_key: str, run: dict, *, reason: str = "") -> None:
        if run.get("finish_emitted") or run.get("finalized"):
            return
        if reason:
            mark_runtime_completion_confirmed(run, reason)
        run["finish_emitted"] = True
        self.jobFinished.emit(job_key)

    def handle_runtime_not_ready(self, job_key: str, run: dict) -> None:
        if not run.get("launcher_finished"):
            return

        if run_is_datacheck(run):
            if int(run.get("datacheck_stable_polls", 0)) >= 3:
                self.emit_job_finished_once(job_key, run)
            return

        launcher_exit_code = run.get("launcher_exit_code")
        if launcher_exit_code not in (None, 0):
            self.emit_job_finished_once(job_key, run)
            return

        if not run.get("solver_started"):
            if solver_start_grace_elapsed(run):
                if not run.get("solver_start_timeout"):
                    mark_solver_start_timeout(run)
                self.historyEvent.emit(
                    f"{run['job_name']} {run.get('solver_start_timeout_detail') or '后台求解器未启动'}。"
                )
                self.emit_job_finished_once(job_key, run)
                return

            queue_item = run.get("queue_item")
            if queue_item is not None:
                if run.get("runtime_phase") == "PREPROCESSING":
                    queue_item.message = "预处理进行中，等待后台求解器启动"
                else:
                    queue_item.message = "等待后台求解器启动"
            self.jobUpdated.emit(job_key)
            return

        queue_item = run.get("queue_item")
        if queue_item is not None:
            queue_item.message = "等待 STA 文件生成"
        self.jobUpdated.emit(job_key)

    @hang_probe_function("JobRuntimeController.poll_sta_file")
    def poll_sta_file(
        self,
        job_key: str,
    ) -> None:
        probe_start = time.monotonic()
        run = self.runs.get(job_key or "")
        if not run:
            return

        process_snapshot = self.process_scan_service.latest_snapshot()
        run["process_snapshot"] = process_snapshot if process_snapshot is not None else ()
        run["process_snapshot_available"] = process_snapshot is not None

        evidence_start = time.monotonic()
        evidence = collect_runtime_evidence(run)
        hang_probe_log(
            "JobRuntimeController.poll_sta_file.step",
            time.monotonic() - evidence_start,
            threshold=0.2,
            job_key=job_key,
            job_name=run.get("job_name"),
            step="collect_runtime_evidence",
        )
        update_file_stability(run, evidence)
        update_runtime_phase(run, evidence)

        if run.get(
            "terminating",
            False,
        ):
            self.jobUpdated.emit(job_key)
            if runtime_termination_ready(run, evidence):
                self.emit_job_finished_once(job_key, run)
            return

        if run_is_datacheck(run):
            self.handle_runtime_not_ready(job_key, run)
            return

        launcher_exit_code = run.get("launcher_exit_code")
        if run.get("launcher_finished") and launcher_exit_code not in (None, 0):
            self.emit_job_finished_once(job_key, run)
            return

        diagnostic_status = str(
            evidence.get("diagnostic_status")
            or run.get("runtime_diagnostic_status")
            or ""
        )
        if (
            run.get("launcher_finished")
            and diagnostic_status in {"失败", "终止"}
            and not evidence.get("lck_exists")
            and not evidence.get("solver_pid_active")
        ):
            self.emit_job_finished_once(job_key, run)
            return

        if not evidence.get("sta_valid"):
            self.handle_runtime_not_ready(job_key, run)
            return

        if not run.get("memory_monitor_activated"):
            run["memory_monitor_activated"] = True
            self.memory_adapter.activate_job(job_key)

        text = str(evidence.get("sta_delta", "") or "")
        if not text:
            if run.get("launcher_finished") and runtime_completion_ready(run, evidence):
                self.emit_job_finished_once(job_key, run, reason="sta/lck stable")
            return

        progress = parse_sta_progress(text) or {}

        if progress.get("step"):
            run["current_step"] = f"Step {progress['step']}"

        if progress.get("total_time") != "":
            run["total_time"] = progress.get(
                "total_time",
                "",
            )

        self.jobUpdated.emit(job_key)
        formatted = format_sta_output_for_log(text, run["sta_state"])
        if formatted:
            self.jobLogReceived.emit(
                job_key,
                formatted,
            )
        status, detail = classify_job_text(text)
        if status:
            self.jobUpdated.emit(job_key)
            if detail:
                self.historyEvent.emit(detail)
        if run.get("launcher_finished") and runtime_completion_ready(run, evidence):
            self.emit_job_finished_once(job_key, run, reason="sta/lck stable")
        hang_probe_log(
            "JobRuntimeController.poll_sta_file",
            time.monotonic() - probe_start,
            threshold=0.2,
            job_key=job_key,
            job_name=run.get("job_name"),
        )

    def terminate_job(
        self,
        job_key: str,
    ) -> None:
        run = self.runs.get(job_key)
        if run is None:
            return
        run["terminating"] = True
        run["terminating_at"] = time.monotonic()
        self.run_abaqus_control(
            job_key,
            "terminate",
        )

    def suspend_job(
        self,
        job_key: str,
    ) -> None:
        self.run_abaqus_control(
            job_key,
            "suspend",
        )

    def resume_job(
        self,
        job_key: str,
    ) -> None:
        self.run_abaqus_control(
            job_key,
            "resume",
        )

    def run_abaqus_control(
        self,
        job_key: str,
        action: str,
    ) -> None:
        run = self.runs.get(job_key)
        if not run:
            return
        job_name = run["job_name"]
        work_dir = run["work_dir"]
        if not job_name or not work_dir:
            return
        command = f"abaqus {action} job={job_name}"
        self.historyEvent.emit(f"执行控制命令：{command}")
        QtCore.QProcess.startDetached(
            "cmd.exe" if os.name == "nt" else "/bin/sh",
            ["/c", command] if os.name == "nt" else ["-lc", command],
            work_dir,
        )
