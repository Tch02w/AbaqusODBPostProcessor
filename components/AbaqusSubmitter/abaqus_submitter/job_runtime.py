"""Single-job runtime controller for QProcess and STA polling."""

from __future__ import annotations

import os
import sys
import time

from abaqus_workbench_core.processes import is_environment_startup_noise

from .abaqus_diagnostics import (
    classify_job_text,
    format_sta_output_for_log,
    parse_sta_progress,
    update_abaqus_stage_from_text,
)
from .constants import SOLVER_START_GRACE_SECONDS, STA_POLL_INTERVAL_MS, STATUS_CONFIRMING
from .diagnostics import hang_probe, hang_probe_function, hang_probe_log
from .process_observation import ProcessObservationService
from .qt_compat import QtCore, Signal
from .runtime_evidence import (
    collect_runtime_evidence,
    runtime_completion_ready,
    runtime_orphaned_after_external_stop_ready,
    runtime_termination_ready,
    update_file_stability,
    update_runtime_phase,
)
from .runtime_record import RuntimeRecord
from .scheduling import ExecutionEvent, ExecutionEventKind

MAX_CONSOLE_OUTPUT_CHARS = 12000
LAUNCHER_START_TIMEOUT_MS = 3000
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
    return not is_star_separator_line(line) and is_environment_startup_noise(line)


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
    executionEvent = Signal(object)

    def __init__(
        self,
        *,
        memory_adapter,
        process_observation: ProcessObservationService | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.memory_adapter = memory_adapter
        self.runs: dict[str, dict] = {}
        self._owns_process_observation = process_observation is None
        self.process_scan_service = process_observation or ProcessObservationService(self)
        self.process_scan_service.scanFailed.connect(
            lambda message: self.historyEvent.emit(f"进程快照扫描失败：{message}")
        )

    def register_run(
        self,
        job_key: str,
        run: dict,
    ) -> None:
        self.runs[job_key] = run
        self._set_process_observation_active(True)

    def _set_process_observation_active(self, active: bool) -> None:
        # 主窗口注入共享服务；独立控制器只读取快照 Interface，不擅自创建后台线程。
        if not self._owns_process_observation:
            self.process_scan_service.set_active(active)

    def unregister_run(
        self,
        job_key: str,
    ) -> None:
        run = self.runs.pop(
            job_key,
            None,
        )
        if run is None:
            self._set_process_observation_active(bool(self.runs))
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
            self._set_process_observation_active(bool(self.runs))
            return

        try:
            process_running = process.state() != QtCore.QProcess.ProcessState.NotRunning
        except RuntimeError:
            self._set_process_observation_active(bool(self.runs))
            return

        if process_running:
            try:
                process.finished.connect(process.deleteLater)
            except (RuntimeError, TypeError):
                pass
            self._set_process_observation_active(bool(self.runs))
            return

        try:
            process.deleteLater()
        except RuntimeError:
            pass
        self._set_process_observation_active(bool(self.runs))

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

        RuntimeRecord.prepare_internal_monitor(
            run,
            process=process,
            timer=timer,
        )
        self.register_run(
            job_key,
            run,
        )

        def timer_callback(key=job_key):
            self.poll_sta_file(key)

        def output_callback(key=job_key):
            self.read_process_output(key)

        def started_callback(key=job_key):
            self.on_process_started(key)

        def finished_callback(exit_code, exit_status, key=job_key):
            self.on_process_finished(
                key,
                exit_code,
                exit_status,
            )

        def error_callback(error, key=job_key):
            self.on_process_error(key, error)
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

    def start_external_monitor(
        self,
        *,
        job_key: str,
        run: dict,
    ) -> bool:
        if job_key in self.runs:
            return True

        now = time.monotonic()
        timer = QtCore.QTimer(self)
        timer.setInterval(STA_POLL_INTERVAL_MS)

        RuntimeRecord.prepare_external_monitor(
            run,
            timer=timer,
            monotonic_now=now,
        )

        self.register_run(
            job_key,
            run,
        )

        def timer_callback(key=job_key):
            self.poll_sta_file(key)

        timer.timeout.connect(timer_callback)
        run["_timer_callback"] = timer_callback
        timer.start()
        QtCore.QTimer.singleShot(0, timer_callback)
        self.jobUpdated.emit(job_key)
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
            self.emit_execution_event(run, ExecutionEventKind.STARTED, "Abaqus launcher 已启动")
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
                queue_item.message = "启动命令已结束，正在监测后台求解状态"
            self.historyEvent.emit(
                f"{run['job_name']} 启动命令已结束：退出码={exit_code}；正在监测后台求解状态。"
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
        self.emit_execution_event(run, ExecutionEventKind.COMPLETING, reason or "正在确认最终状态")
        run["finish_emitted"] = True
        self.jobFinished.emit(job_key)

    def emit_execution_event(
        self,
        run: dict,
        kind: ExecutionEventKind,
        message: str = "",
    ) -> None:
        job_id = str(run.get("scheduler_job_id", "") or "")
        if not job_id:
            return
        self.executionEvent.emit(
            ExecutionEvent(
                job_id=job_id,
                attempt_id=str(run.get("attempt_id", "") or ""),
                kind=kind,
                message=message,
            )
        )

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

    def update_runtime_queue_message(self, job_key: str, run: dict) -> None:
        queue_item = run.get("queue_item")
        if queue_item is None:
            return

        completion_detected = str(run.get("runtime_diagnostic_status") or "") == "完成"
        if run.get("runtime_phase") == "FINISH_CANDIDATE":
            message = "状态确认中，等待 STA/LCK 稳定"
        elif completion_detected:
            message = "检测到完成信息，等待 STA/LCK/PID 稳定"
        else:
            message = "正式求解中"
        status_changed = False
        if completion_detected and queue_item.status != STATUS_CONFIRMING:
            queue_item.status = STATUS_CONFIRMING
            status_changed = True
        if queue_item.message != message:
            queue_item.message = message
            status_changed = True
        if status_changed:
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

        process_snapshot_bundle = self.process_scan_service.latest_snapshot_bundle()
        if process_snapshot_bundle is None:
            run["process_snapshot"] = ()
            run["process_snapshot_by_pid"] = {}
            run["process_snapshot_solver_rows"] = ()
            run["process_snapshot_available"] = False
        else:
            process_snapshot, process_by_pid, solver_rows = process_snapshot_bundle
            run["process_snapshot"] = process_snapshot
            run["process_snapshot_by_pid"] = process_by_pid
            run["process_snapshot_solver_rows"] = solver_rows
            run["process_snapshot_available"] = True

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

        if runtime_orphaned_after_external_stop_ready(run, evidence):
            run["runtime_diagnostic_status"] = "终止"
            run["runtime_diagnostic_detail"] = "未发现 LCK、求解器进程或有效 STA，判断作业已在外部停止"
            self.emit_job_finished_once(job_key, run)
            return

        if not evidence.get("sta_valid"):
            self.handle_runtime_not_ready(job_key, run)
            return

        self.update_runtime_queue_message(job_key, run)

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
