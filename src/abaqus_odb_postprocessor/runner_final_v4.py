from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Iterable

from .config import abaqus_script


LogCallback = Callable[[str], None]


def _bat_command(arguments: Iterable[str]) -> str:
    return subprocess.list2cmdline([str(item) for item in arguments])


def run_process(arguments: list[str], cwd: Path, log: LogCallback | None = None) -> None:
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
    assert process.stdout is not None
    for line in process.stdout:
        if log:
            log(line.rstrip())
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"Command failed with exit code {return_code}: {command}")


def scan_folder(
    abaqus_command: str,
    odb_folder: Path,
    cache_dir: Path,
    log: LogCallback | None = None,
) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_path = cache_dir / "scan_report.json"
    run_process(
        [abaqus_command, "python", str(abaqus_script("scan_odb_fixed.py")), "--folder", str(odb_folder), "--output", str(output_path)],
        cwd=cache_dir,
        log=log,
    )
    with output_path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def run_job(
    abaqus_command: str,
    job_config_path: Path,
    log: LogCallback | None = None,
) -> None:
    run_process(
        [abaqus_command, "cae", f"noGUI={abaqus_script('extract_job_compat_v3.py')}", "--", str(job_config_path)],
        cwd=job_config_path.parent,
        log=log,
    )

