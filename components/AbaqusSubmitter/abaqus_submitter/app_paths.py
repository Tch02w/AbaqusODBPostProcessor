"""应用运行数据目录的唯一 Interface。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AbaqusSubmitter"
APP_DATA_DIR_ENV = "ABAQUS_SUBMITTER_DATA_DIR"


def resolve_app_data_dir() -> Path:
    override = os.environ.get(APP_DATA_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Local") / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    base = os.environ.get("XDG_DATA_HOME", "").strip()
    return (Path(base).expanduser() if base else Path.home() / ".local" / "share") / APP_NAME


APP_DATA_DIR = resolve_app_data_dir()
CONFIG_PATH = APP_DATA_DIR / "config.json"
JOBLIST_PATH = APP_DATA_DIR / "joblist.json"
SCHEDULER_STATE_PATH = APP_DATA_DIR / "scheduler_state.db"


__all__ = [
    "APP_DATA_DIR",
    "APP_DATA_DIR_ENV",
    "CONFIG_PATH",
    "JOBLIST_PATH",
    "SCHEDULER_STATE_PATH",
    "resolve_app_data_dir",
]
