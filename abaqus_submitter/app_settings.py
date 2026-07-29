"""Persistent application settings stored in the Application Data Directory."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .app_paths import CONFIG_PATH


def load_app_settings(path: Path = CONFIG_PATH) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_settings_section(
    section: str,
    *,
    path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    payload = load_app_settings(path)
    values = payload.get(section, {})
    return dict(values) if isinstance(values, dict) else {}


def save_settings_section(
    section: str,
    values: dict[str, Any],
    *,
    path: Path = CONFIG_PATH,
) -> None:
    payload = load_app_settings(path)
    payload[section] = dict(values)
    save_app_settings(payload, path=path)


def save_app_settings(
    values: dict[str, Any],
    *,
    path: Path = CONFIG_PATH,
) -> None:
    """Atomically replace the settings payload with UTF-8 JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(values, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary_path, path)


__all__ = [
    "load_app_settings",
    "load_settings_section",
    "save_app_settings",
    "save_settings_section",
]
