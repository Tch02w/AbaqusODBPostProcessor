"""Cancellable orchestration of multiple independent Abaqus CAE processes."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Callable

from .config import abaqus_script
from .runner_selection_base import ProcessCancelled, run_process


LogCallback = Callable[[str], None]


class MultiProcessController:
    """Track and cancel every Abaqus subprocess belonging to one GUI batch."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen[str]] = set()
        self.cancel_requested = False

    def attach(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._processes.add(process)
            cancel_now = self.cancel_requested
        if cancel_now:
            self._terminate_tree(process)

    def detach(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._processes.discard(process)

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(process.poll() is None for process in self._processes)

    def cancel(self) -> None:
        with self._lock:
            self.cancel_requested = True
            processes = list(self._processes)
        for process in processes:
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


def scan_field_ranges(
    abaqus_command: str,
    job_config_path: Path,
    output_path: Path,
    log: LogCallback | None = None,
    controller: MultiProcessController | None = None,
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
        controller,
    )
    with output_path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def run_job(
    abaqus_command: str,
    job_config_path: Path,
    log: LogCallback | None = None,
    controller: MultiProcessController | None = None,
) -> None:
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
        controller,
    )


def render_group_contours(
    abaqus_command: str,
    job_config_path: Path,
    log: LogCallback | None = None,
    controller: MultiProcessController | None = None,
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
        controller,
    )


__all__ = [
    "MultiProcessController",
    "ProcessCancelled",
    "render_group_contours",
    "run_job",
    "scan_field_ranges",
]

