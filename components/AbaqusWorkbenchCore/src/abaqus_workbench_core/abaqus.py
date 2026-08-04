"""Abaqus command identity shared by submission and postprocessing."""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AbaqusCommandProfile:
    """Describe one host-side Abaqus command without launching it."""

    command: str = "abaqus"
    release: str = ""

    def arguments(self, *arguments: object) -> list[str]:
        return [self.command, *(str(argument) for argument in arguments)]

    def resolved_executable(self) -> str:
        return shutil.which(self.command) or ""

    @property
    def is_available(self) -> bool:
        return bool(self.resolved_executable())
