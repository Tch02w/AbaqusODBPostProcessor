from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .config import abaqus_script
from .runner_selection_base import *
from .runner_selection_base import ProcessCancelled, ProcessController, run_process


def scan_folder(
    abaqus_command: str,
    odb_folder: Path,
    cache_dir: Path,
    log: LogCallback | None = None,
    controller: ProcessController | None = None,
    selected_paths: Iterable[Path | str] | None = None,
) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = cache_dir / "scan_report.json"
    arguments = [
        abaqus_command,
        "python",
        str(abaqus_script("scan_odb_fixed.py")),
        "--folder",
        str(odb_folder),
        "--output",
        str(output),
    ]
    if selected_paths is not None:
        selection_path = cache_dir / "scan_selection.json"
        paths = [str(Path(value).resolve()) for value in selected_paths]
        selection_path.write_text(
            json.dumps({"folder": str(odb_folder.resolve()), "paths": paths}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        arguments.extend(["--selection", str(selection_path)])
    try:
        run_process(arguments, cache_dir, log, controller)
    except ProcessCancelled:
        if not output.exists():
            raise
        with output.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        payload["cancelled"] = True
        return payload
    with output.open("r", encoding="utf-8") as stream:
        return json.load(stream)
