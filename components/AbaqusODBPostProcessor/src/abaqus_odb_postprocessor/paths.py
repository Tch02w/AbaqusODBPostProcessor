"""Filesystem locations for code, user state, persistent cache, and results."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from abaqus_workbench_core.paths import resolve_application_data_dir

APP_NAME = "AbaqusODBPostProcessor"
APP_DATA_DIR_ENV = "ABAQUS_POSTPROCESSOR_DATA_DIR"


def temp_root() -> Path:
    root = Path(tempfile.gettempdir()) / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def scan_cache_dir(odb_root: Path | str) -> Path:
    """Return the single persistent cache directory for an ODB project root."""

    path = Path(odb_root).resolve() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def batch_temp_dir(batch_id: str) -> Path:
    path = temp_root() / "batches" / batch_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_file() -> Path:
    override = os.environ.get(APP_DATA_DIR_ENV, "").strip()
    if override:
        base = resolve_application_data_dir(
            APP_NAME,
            env_var=APP_DATA_DIR_ENV,
            windows_scope="roaming",
        )
    elif os.name == "nt" and not os.environ.get("APPDATA", "").strip():
        base = Path(tempfile.gettempdir()) / APP_NAME
    else:
        base = resolve_application_data_dir(APP_NAME, windows_scope="roaming")
    path = base / "project_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def result_root_for_odb(odb_path: Path) -> Path:
    path = odb_path.resolve().parent / f"{APP_NAME}_Results"
    path.mkdir(parents=True, exist_ok=True)
    return path
