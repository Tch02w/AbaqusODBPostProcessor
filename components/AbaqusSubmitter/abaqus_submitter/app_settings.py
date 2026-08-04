"""Persistent application settings stored in the Application Data Directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from abaqus_workbench_core.settings import JsonSettingsStore

from .app_paths import CONFIG_PATH


def load_app_settings(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return JsonSettingsStore(path).load()


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

    JsonSettingsStore(path).save(values)


__all__ = [
    "load_app_settings",
    "load_settings_section",
    "save_app_settings",
    "save_settings_section",
]
