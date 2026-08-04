"""Shared, UI-independent infrastructure for Abaqus desktop tools."""

from .abaqus import AbaqusCommandProfile
from .events import TaskEvent, TaskPhase
from .paths import ApplicationPaths, resolve_application_data_dir
from .processes import CancellationToken, ProcessRequest, ProcessResult

__all__ = [
    "AbaqusCommandProfile",
    "ApplicationPaths",
    "CancellationToken",
    "ProcessRequest",
    "ProcessResult",
    "TaskEvent",
    "TaskPhase",
    "resolve_application_data_dir",
]
