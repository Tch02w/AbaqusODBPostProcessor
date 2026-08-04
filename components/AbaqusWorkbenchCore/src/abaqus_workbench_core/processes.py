"""UI-neutral process request, result, cancellation, and log filtering."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProcessRequest:
    arguments: Sequence[str]
    working_directory: Path
    environment: Mapping[str, str] = field(default_factory=dict)
    task_id: str = ""


@dataclass(frozen=True, slots=True)
class ProcessResult:
    return_code: int
    output: tuple[str, ...] = ()


class CancellationToken:
    """Thread-safe cancellation request shared by process adapters."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise ProcessCancelled("Process cancelled by user")


class ProcessCancelled(RuntimeError):
    pass


def is_environment_startup_noise(line: str) -> bool:
    """Filter compiler-environment banners emitted before Abaqus output."""

    text = line.strip()
    lowered = text.casefold()
    if len(text) >= 20 and set(text) == {"*"}:
        return True
    if "developer command prompt" in lowered and "visual studio" in lowered:
        return True
    if lowered.startswith("** copyright (c)") and "microsoft corporation" in lowered:
        return True
    if lowered.startswith("[debug:ext\\vcvars.bat]"):
        return True
    if lowered.startswith("[vcvarsall.bat] environment initialized for:"):
        return True
    return "warning: vars.bat does not set up dependencies when invoked directly" in lowered
