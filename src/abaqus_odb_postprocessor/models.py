from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def normalized_name(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def choose_name(candidates: list[str], preferred: str, fallback: str = "") -> str:
    target = normalized_name(preferred)
    for name in candidates:
        if normalized_name(name) == target:
            return name
    return fallback if fallback in candidates else (candidates[0] if candidates else "")


@dataclass
class OdbScan:
    path: Path
    steps: list[str]
    assembly_node_sets: list[str]
    assembly_element_sets: list[str]
    field_outputs: list[str]
    set_details: dict[str, Any] = field(default_factory=dict)
    history_output_details: list[dict[str, Any]] = field(default_factory=list)
    content_fingerprint: str = ""
    file_size: int = 0
    mtime_ns: int = 0
    error: str = ""

    @property
    def assembly_sets(self) -> list[str]:
        return sorted(set(self.assembly_node_sets) | set(self.assembly_element_sets))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OdbScan":
        return cls(
            path=Path(payload["path"]),
            steps=list(payload.get("steps", [])),
            assembly_node_sets=list(payload.get("assembly_node_sets", [])),
            assembly_element_sets=list(payload.get("assembly_element_sets", [])),
            field_outputs=list(payload.get("field_outputs", [])),
            set_details=dict(payload.get("set_details", {})),
            history_output_details=list(
                payload.get("history_output_details", [])
            ),
            content_fingerprint=str(payload.get("content_fingerprint", "")),
            file_size=int(payload.get("file_size", 0)),
            mtime_ns=int(payload.get("mtime_ns", 0)),
            error=str(payload.get("error", "")),
        )
