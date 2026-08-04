"""Atomic UTF-8 JSON settings shared by desktop modules."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class JsonSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self, default: Mapping[str, Any] | None = None) -> dict[str, Any]:
        fallback = dict(default or {})
        if not self.path.exists():
            return fallback
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return fallback
        return dict(payload) if isinstance(payload, dict) else fallback

    def save(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(dict(payload), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()
