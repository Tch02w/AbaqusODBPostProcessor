from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterable

from .config import abaqus_script

LogCallback = Callable[[str], None]


class ProcessCancelled(RuntimeError):
    pass


class ProcessController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self.cancel_requested = False

    def attach(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._process = process
            cancel_now = self.cancel_requested
        if cancel_now:
            self._terminate_tree(process)

    def detach(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if self._process is process:
                self._process = None

    def cancel(self) -> None:
        with self._lock:
            self.cancel_requested = True
            process = self._process
        if process is not None:
            self._terminate_tree(process)

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def _bat_command(arguments: Iterable[str]) -> str:
    return subprocess.list2cmdline([str(item) for item in arguments])


def run_process(
    arguments: list[str],
    cwd: Path,
    log: LogCallback | None = None,
    controller: ProcessController | None = None,
) -> None:
    command = _bat_command(arguments)
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if controller is not None:
        controller.attach(process)
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if log:
                log(line.rstrip())
        code = process.wait()
    finally:
        if controller is not None:
            controller.detach(process)
    if controller is not None and controller.cancel_requested:
        raise ProcessCancelled("Process cancelled by user")
    if code:
        raise RuntimeError(f"Command failed with exit code {code}: {command}")


def scan_folder(
    abaqus_command: str,
    odb_folder: Path,
    cache_dir: Path,
    log: LogCallback | None = None,
    controller: ProcessController | None = None,
) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = cache_dir / "scan_report.json"
    try:
        run_process(
            [
                abaqus_command,
                "python",
                str(abaqus_script("scan_odb_fixed.py")),
                "--folder",
                str(odb_folder),
                "--output",
                str(output),
            ],
            cache_dir,
            log,
            controller,
        )
    except ProcessCancelled:
        if not output.exists():
            raise
        with output.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        payload["cancelled"] = True
        return payload
    with output.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def scan_field_ranges(
    abaqus_command: str,
    job_config_path: Path,
    output_path: Path,
    log: LogCallback | None = None,
) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_process(
        [
            abaqus_command,
            "cae",
            f"noGUI={abaqus_script('scan_legend_limits_compat_v3.py')}",
            "--",
            "--config",
            str(job_config_path),
            "--output",
            str(output_path),
        ],
        job_config_path.parent,
        log,
    )
    with output_path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def run_job(abaqus_command: str, job_config_path: Path, log: LogCallback | None = None) -> None:
    run_process(
        [
            abaqus_command,
            "cae",
            f"noGUI={abaqus_script('extract_job_final_v23.py')}",
            "--",
            str(job_config_path),
        ],
        job_config_path.parent,
        log,
    )


def render_group_contours(
    abaqus_command: str,
    job_config_path: Path,
    log: LogCallback | None = None,
) -> None:
    run_process(
        [
            abaqus_command,
            "cae",
            f"noGUI={abaqus_script('render_group_contours.py')}",
            "--",
            str(job_config_path),
        ],
        job_config_path.parent,
        log,
    )
