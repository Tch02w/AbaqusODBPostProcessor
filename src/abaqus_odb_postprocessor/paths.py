"""Filesystem locations for code, user state, persistent cache, and results."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


APP_NAME = "AbaqusODBPostProcessor"


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
    base = Path(os.environ.get("APPDATA", tempfile.gettempdir()))
    path = base / APP_NAME / "project_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def result_root_for_odb(odb_path: Path) -> Path:
    path = odb_path.resolve().parent / f"{APP_NAME}_Results"
    path.mkdir(parents=True, exist_ok=True)
    return path
