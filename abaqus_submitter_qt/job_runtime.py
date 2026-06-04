"""Single-job runtime controller for QProcess and STA polling."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .abaqus_diagnostics import (
    classify_job_text,
    format_sta_output_for_log,
    parse_sta_progress,
    update_abaqus_stage_from_text,
)
from .constants import STA_FILE_ENCODING
from .qt_compat import QtCore, Signal


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

    def register_run(
        self,
        job_key: str,
        run: dict,
    ) -> None:
        self.runs[job_key] = run

    def unregister_run(
        self,
        job_key: str,
    ) -> None:
        self.runs.pop(
            job_key,
            None,
        )

    def start_process(
        self,
        *,
        job_key: str,
        run: dict,
        command: str,
    ) -> bool:
        process = QtCore.QProcess(self)
        process.setWorkingDirectory(run["work_dir"])
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)

        timer = QtCore.QTimer(self)
        timer.setInterval(5000)

        run["process"] = process
        run["timer"] = timer
        self.register_run(
            job_key,
            run,
        )

        timer.timeout.connect(lambda key=job_key: self.poll_sta_file(key))
        process.readyReadStandardOutput.connect(lambda key=job_key: self.read_process_output(key))
        process.finished.connect(
            lambda exit_code, exit_status, key=job_key: self.on_process_finished(
                key,
                exit_code,
                exit_status,
            )
        )
        process.errorOccurred.connect(lambda error, key=job_key: self.processError.emit(key, str(error)))

        if os.name == "nt":
            process.start("cmd.exe", ["/c", command])
        else:
            process.start("/bin/sh", ["-lc", command])

        if not process.waitForStarted(3000):
            self.unregister_run(job_key)
            return False

        timer.start()
        return True

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
        run = self.runs.get(job_key)
        if not run:
            return
        run["launcher_finished"] = True
        run["launcher_exit_code"] = exit_code
        run["launcher_exit_status"] = exit_status
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
        self.poll_sta_file(job_key)

    def poll_sta_file(
        self,
        job_key: str,
    ) -> None:
        run = self.runs.get(job_key or "")
        if not run:
            return
        lck_path = Path(run["work_dir"]) / f"{run['job_name']}.lck"
        if lck_path.exists():
            run["seen_lck"] = True
            run["stable_no_lck_polls"] = 0
        else:
            run["stable_no_lck_polls"] = int(run.get("stable_no_lck_polls", 0)) + 1

        if run.get(
            "terminating",
            False,
        ):
            self.jobUpdated.emit(job_key)

            if not lck_path.exists():
                self.jobFinished.emit(job_key)

            return

        sta_path = Path(run["work_dir"]) / f"{run['job_name']}.sta"
        if not sta_path.exists():
            if run.get("launcher_finished") and int(run.get("stable_no_lck_polls", 0)) >= 3:
                self.jobFinished.emit(job_key)
            return
        if not run.get("memory_monitor_activated"):
            run["memory_monitor_activated"] = True
            self.memory_adapter.activate_job(job_key)

        try:
            size = sta_path.stat().st_size
            if size < run["sta_position"]:
                run["sta_position"] = 0
            with sta_path.open("r", encoding=STA_FILE_ENCODING, errors="replace") as stream:
                stream.seek(run["sta_position"])
                text = stream.read()
                run["sta_position"] = stream.tell()
        except OSError as exc:
            self.historyEvent.emit(f"读取 STA 失败：{exc}")
            return

        if not text:
            if run.get("launcher_finished") and int(run.get("stable_no_lck_polls", 0)) >= 3:
                self.jobFinished.emit(job_key)
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
        if run.get("launcher_finished") and int(run.get("stable_no_lck_polls", 0)) >= 3:
            self.jobFinished.emit(job_key)

    def terminate_job(
        self,
        job_key: str,
    ) -> None:
        run = self.runs.get(job_key)
        if run is None:
            return
        run["terminating"] = True
        run["terminating_at"] = time.time()
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
