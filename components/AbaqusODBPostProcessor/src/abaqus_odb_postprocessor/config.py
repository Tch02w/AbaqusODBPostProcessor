from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .file_attributes import clear_windows_hidden


PACKAGE_DIR = Path(__file__).resolve().parent


def load_defaults() -> dict[str, Any]:
    with (PACKAGE_DIR / "defaults.json").open("r", encoding="utf-8") as stream:
        return json.load(stream)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        clear_windows_hidden(path)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def project_root() -> Path:
    return PACKAGE_DIR.parent.parent


def abaqus_script(name: str) -> Path:
    return PACKAGE_DIR / "abaqus_scripts" / name
