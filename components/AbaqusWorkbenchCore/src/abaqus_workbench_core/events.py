"""Small cross-module task event contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class TaskPhase(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class TaskEvent:
    source: str
    task_id: str
    phase: TaskPhase
    message: str = ""
    progress_current: int = 0
    progress_total: int = 0
    metadata: Mapping[str, object] = field(default_factory=dict)
