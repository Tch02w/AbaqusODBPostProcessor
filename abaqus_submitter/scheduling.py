"""无 Qt 依赖的单机调度核心。

该 Module 借鉴 Slurm 的提交、调度、执行回报分层，但仍在本机运行。
调用者提交不可变 Job Specification，并通过 Execution Event 驱动作业状态机。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Protocol
from uuid import uuid4


class JobState(str, Enum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETING = "COMPLETING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    LOST = "LOST"


class PendingReason(str, Enum):
    NONE = ""
    DEPENDENCY = "DEPENDENCY"
    DEPENDENCY_FAILED = "DEPENDENCY_FAILED"
    USER_HOLD = "USER_HOLD"
    SLOT_LIMIT = "SLOT_LIMIT"
    CPU_LIMIT = "CPU_LIMIT"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    ACTIVE_CONFLICT = "ACTIVE_CONFLICT"
    ARCHIVE_RESERVED = "ARCHIVE_RESERVED"


class ExecutionEventKind(str, Enum):
    STARTING = "STARTING"
    STARTED = "STARTED"
    COMPLETING = "COMPLETING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    LOST = "LOST"
    MESSAGE = "MESSAGE"


TERMINAL_JOB_STATES = frozenset(
    {
        JobState.COMPLETED,
        JobState.FAILED,
        JobState.CANCELED,
        JobState.LOST,
    }
)

ACTIVE_JOB_STATES = frozenset(
    {
        JobState.DISPATCHED,
        JobState.STARTING,
        JobState.RUNNING,
        JobState.COMPLETING,
    }
)


@dataclass(frozen=True)
class ResourceRequest:
    slots: int = 1
    cores: int = 0
    memory_bytes: int = 0
    license_tokens: int = 0

    def normalized(self) -> ResourceRequest:
        return ResourceRequest(
            slots=max(1, int(self.slots or 1)),
            cores=max(0, int(self.cores or 0)),
            memory_bytes=max(0, int(self.memory_bytes or 0)),
            license_tokens=max(0, int(self.license_tokens or 0)),
        )


@dataclass(frozen=True)
class ResourceSnapshot:
    available_slots: int
    available_cores: int | None = None
    available_memory_bytes: int | None = None
    available_license_tokens: int | None = None


@dataclass(frozen=True)
class JobSpecification:
    job_id: str
    job_name: str
    work_dir: str
    resources: ResourceRequest = ResourceRequest()
    priority: int = 0
    submitted_order: int = 0
    submitted_at: float = 0.0
    dependency_job_ids: tuple[str, ...] = ()
    conflict_key: tuple[str, str] = ("", "")
    held: bool = False
    metadata: dict[str, object] = field(default_factory=dict, compare=False)

    def normalized(self) -> JobSpecification:
        job_id = str(self.job_id or "").strip()
        job_name = str(self.job_name or "").strip()
        if not job_id:
            raise ValueError("Job Specification 的 job_id 不能为空")
        if not job_name:
            raise ValueError("Job Specification 的 job_name 不能为空")
        return replace(
            self,
            job_id=job_id,
            job_name=job_name,
            work_dir=str(self.work_dir or ""),
            resources=self.resources.normalized(),
            priority=int(self.priority or 0),
            submitted_order=max(0, int(self.submitted_order or 0)),
            submitted_at=float(self.submitted_at or time.time()),
            dependency_job_ids=tuple(
                dict.fromkeys(
                    str(value).strip()
                    for value in self.dependency_job_ids
                    if str(value).strip() and str(value).strip() != job_id
                )
            ),
            conflict_key=(
                str(self.conflict_key[0] or ""),
                str(self.conflict_key[1] or "").lower(),
            ),
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True)
class SchedulerJobSnapshot:
    specification: JobSpecification
    state: JobState
    pending_reason: PendingReason = PendingReason.NONE
    attempt_id: str = ""
    message: str = ""
    version: int = 0
    updated_at: float = 0.0

    @property
    def job_id(self) -> str:
        return self.specification.job_id


@dataclass(frozen=True)
class Allocation:
    job_id: str
    attempt_id: str
    specification: JobSpecification


@dataclass(frozen=True)
class SchedulePlan:
    allocations: tuple[Allocation, ...]
    jobs: tuple[SchedulerJobSnapshot, ...]


@dataclass(frozen=True)
class ExecutionEvent:
    job_id: str
    attempt_id: str
    kind: ExecutionEventKind
    message: str = ""
    occurred_at: float = 0.0


class SchedulerRepository(Protocol):
    def transaction(self) -> Iterator[None]: ...

    def load_jobs(self) -> list[SchedulerJobSnapshot]: ...

    def save_job(self, snapshot: SchedulerJobSnapshot) -> None: ...

    def append_event(
        self,
        *,
        job_id: str,
        event_type: str,
        state: JobState,
        message: str,
        attempt_id: str,
        occurred_at: float,
    ) -> None: ...

    def close(self) -> None: ...


class InMemorySchedulerRepository:
    """测试和无持久化场景使用的 Repository Adapter。"""

    def __init__(self) -> None:
        self.jobs: dict[str, SchedulerJobSnapshot] = {}
        self.events: list[dict[str, object]] = []

    def load_jobs(self) -> list[SchedulerJobSnapshot]:
        return list(self.jobs.values())

    @contextmanager
    def transaction(self) -> Iterator[None]:
        original_jobs = dict(self.jobs)
        original_events = list(self.events)
        try:
            yield
        except BaseException:
            self.jobs = original_jobs
            self.events = original_events
            raise

    def save_job(self, snapshot: SchedulerJobSnapshot) -> None:
        self.jobs[snapshot.job_id] = snapshot

    def append_event(
        self,
        *,
        job_id: str,
        event_type: str,
        state: JobState,
        message: str,
        attempt_id: str,
        occurred_at: float,
    ) -> None:
        self.events.append(
            {
                "job_id": job_id,
                "event_type": event_type,
                "state": state,
                "message": message,
                "attempt_id": attempt_id,
                "occurred_at": occurred_at,
            }
        )

    def close(self) -> None:
        return


class StateTransitionError(RuntimeError):
    pass


class SchedulerCore:
    """拥有排队顺序、资源预留和作业状态机的 Deep Module。"""

    _TRANSITIONS: dict[JobState, frozenset[JobState]] = {
        JobState.PENDING: frozenset(
            {
                JobState.DISPATCHED,
                JobState.CANCELED,
                JobState.FAILED,
            }
        ),
        JobState.DISPATCHED: frozenset(
            {
                JobState.STARTING,
                JobState.RUNNING,
                JobState.PENDING,
                JobState.FAILED,
                JobState.CANCELED,
                JobState.LOST,
            }
        ),
        JobState.STARTING: frozenset(
            {
                JobState.PENDING,
                JobState.RUNNING,
                JobState.COMPLETING,
                JobState.FAILED,
                JobState.CANCELED,
                JobState.LOST,
            }
        ),
        JobState.RUNNING: frozenset(
            {
                JobState.COMPLETING,
                JobState.COMPLETED,
                JobState.FAILED,
                JobState.CANCELED,
                JobState.LOST,
            }
        ),
        JobState.COMPLETING: frozenset(
            {
                JobState.COMPLETED,
                JobState.FAILED,
                JobState.CANCELED,
                JobState.LOST,
            }
        ),
        JobState.COMPLETED: frozenset(),
        JobState.FAILED: frozenset({JobState.PENDING}),
        JobState.CANCELED: frozenset({JobState.PENDING}),
        JobState.LOST: frozenset({JobState.PENDING, JobState.FAILED, JobState.CANCELED}),
    }

    def __init__(self, repository: SchedulerRepository | None = None) -> None:
        self.repository = repository or InMemorySchedulerRepository()
        self._jobs = {snapshot.job_id: snapshot for snapshot in self.repository.load_jobs()}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        original_jobs = dict(self._jobs)
        try:
            with self.repository.transaction():
                yield
        except BaseException:
            self._jobs = original_jobs
            raise

    def submit(
        self,
        specification: JobSpecification,
        *,
        state: JobState = JobState.PENDING,
        pending_reason: PendingReason = PendingReason.NONE,
        attempt_id: str = "",
        message: str = "",
        replace_existing: bool = False,
    ) -> SchedulerJobSnapshot:
        specification = specification.normalized()
        existing = self._jobs.get(specification.job_id)
        if existing is not None and not replace_existing:
            if existing.specification != specification:
                snapshot = replace(
                    existing,
                    specification=specification,
                    version=existing.version + 1,
                    updated_at=time.time(),
                )
                return self._save(snapshot, "SPECIFICATION_UPDATED")
            return existing
        snapshot = SchedulerJobSnapshot(
            specification=specification,
            state=state,
            pending_reason=pending_reason if state == JobState.PENDING else PendingReason.NONE,
            attempt_id=attempt_id,
            message=message,
            version=(existing.version + 1 if existing else 1),
            updated_at=time.time(),
        )
        return self._save(snapshot, "JOB_SUBMITTED" if existing is None else "JOB_RECONCILED")

    def reconcile(
        self,
        specification: JobSpecification,
        *,
        state: JobState,
        pending_reason: PendingReason = PendingReason.NONE,
        attempt_id: str = "",
        message: str = "",
    ) -> SchedulerJobSnapshot:
        specification = specification.normalized()
        existing = self._jobs.get(specification.job_id)
        if (
            existing is not None
            and existing.specification == specification
            and existing.state == state
            and existing.pending_reason == pending_reason
            and existing.attempt_id == attempt_id
            and existing.message == message
        ):
            return existing
        return self.submit(
            specification,
            state=state,
            pending_reason=pending_reason,
            attempt_id=attempt_id,
            message=message,
            replace_existing=True,
        )

    def plan(
        self,
        resources: ResourceSnapshot,
        *,
        archive_reserved_keys: set[tuple[str, str]] | None = None,
        eligible_job_ids: set[str] | None = None,
    ) -> SchedulePlan:
        with self.transaction():
            return self._build_plan(
                resources,
                archive_reserved_keys=archive_reserved_keys,
                eligible_job_ids=eligible_job_ids,
            )

    def _build_plan(
        self,
        resources: ResourceSnapshot,
        *,
        archive_reserved_keys: set[tuple[str, str]] | None = None,
        eligible_job_ids: set[str] | None = None,
    ) -> SchedulePlan:
        archive_reserved_keys = archive_reserved_keys or set()
        available_slots = max(0, int(resources.available_slots or 0))
        available_cores = self._optional_capacity(resources.available_cores)
        available_memory = self._optional_capacity(resources.available_memory_bytes)
        available_licenses = self._optional_capacity(resources.available_license_tokens)
        active_conflicts = {
            snapshot.specification.conflict_key
            for snapshot in self._jobs.values()
            if snapshot.state in ACTIVE_JOB_STATES and any(snapshot.specification.conflict_key)
        }
        allocations: list[Allocation] = []
        candidates = sorted(
            (
                snapshot
                for snapshot in self._jobs.values()
                if snapshot.state == JobState.PENDING
                and (eligible_job_ids is None or snapshot.job_id in eligible_job_ids)
            ),
            key=lambda snapshot: (
                -snapshot.specification.priority,
                snapshot.specification.submitted_order,
                snapshot.specification.submitted_at,
                snapshot.job_id,
            ),
        )

        for snapshot in candidates:
            specification = snapshot.specification
            reason = self._pending_reason(
                specification,
                available_slots=available_slots,
                available_cores=available_cores,
                available_memory=available_memory,
                available_licenses=available_licenses,
                active_conflicts=active_conflicts,
                archive_reserved_keys=archive_reserved_keys,
            )
            if reason != PendingReason.NONE:
                if snapshot.pending_reason != reason:
                    self._replace_snapshot(
                        snapshot,
                        pending_reason=reason,
                        message=pending_reason_text(reason),
                        event_type="JOB_WAITING",
                    )
                continue

            request = specification.resources
            attempt_id = uuid4().hex
            allocated = self._replace_snapshot(
                snapshot,
                state=JobState.DISPATCHED,
                pending_reason=PendingReason.NONE,
                attempt_id=attempt_id,
                message="已分配本机执行资源",
                event_type="JOB_DISPATCHED",
            )
            allocations.append(Allocation(specification.job_id, attempt_id, specification))
            available_slots -= request.slots
            available_cores = self._consume(available_cores, request.cores)
            available_memory = self._consume(available_memory, request.memory_bytes)
            available_licenses = self._consume(available_licenses, request.license_tokens)
            if any(specification.conflict_key):
                active_conflicts.add(specification.conflict_key)
            self._jobs[allocated.job_id] = allocated

        return SchedulePlan(
            allocations=tuple(allocations),
            jobs=tuple(self.snapshots()),
        )

    def apply_execution_event(self, event: ExecutionEvent) -> SchedulerJobSnapshot | None:
        snapshot = self._jobs.get(event.job_id)
        if snapshot is None:
            return None
        if event.attempt_id and snapshot.attempt_id and event.attempt_id != snapshot.attempt_id:
            return snapshot
        if event.kind == ExecutionEventKind.MESSAGE:
            return self._replace_snapshot(
                snapshot,
                message=event.message,
                event_type="EXECUTION_MESSAGE",
                occurred_at=event.occurred_at,
            )
        target = {
            ExecutionEventKind.STARTING: JobState.STARTING,
            ExecutionEventKind.STARTED: JobState.RUNNING,
            ExecutionEventKind.COMPLETING: JobState.COMPLETING,
            ExecutionEventKind.SUCCEEDED: JobState.COMPLETED,
            ExecutionEventKind.FAILED: JobState.FAILED,
            ExecutionEventKind.CANCELED: JobState.CANCELED,
            ExecutionEventKind.LOST: JobState.LOST,
        }[event.kind]
        return self.transition(
            event.job_id,
            target,
            message=event.message,
            attempt_id=event.attempt_id,
            event_type=f"EXECUTION_{event.kind.value}",
            occurred_at=event.occurred_at,
        )

    def transition(
        self,
        job_id: str,
        target: JobState,
        *,
        message: str = "",
        attempt_id: str = "",
        event_type: str = "JOB_STATE_CHANGED",
        occurred_at: float = 0.0,
        force: bool = False,
    ) -> SchedulerJobSnapshot:
        snapshot = self._jobs[job_id]
        if not force and target != snapshot.state and target not in self._TRANSITIONS[snapshot.state]:
            raise StateTransitionError(f"不允许从 {snapshot.state.value} 转换到 {target.value}")
        return self._replace_snapshot(
            snapshot,
            state=target,
            pending_reason=(snapshot.pending_reason if target == JobState.PENDING else PendingReason.NONE),
            attempt_id=attempt_id or snapshot.attempt_id,
            message=message or snapshot.message,
            event_type=event_type,
            occurred_at=occurred_at,
        )

    def requeue(self, job_id: str, message: str = "重新排队") -> SchedulerJobSnapshot:
        snapshot = self._jobs[job_id]
        if snapshot.state not in {JobState.FAILED, JobState.CANCELED, JobState.LOST, JobState.STARTING}:
            raise StateTransitionError(f"状态 {snapshot.state.value} 的作业不能重新排队")
        return self._replace_snapshot(
            snapshot,
            state=JobState.PENDING,
            pending_reason=PendingReason.NONE,
            attempt_id="",
            message=message,
            event_type="JOB_REQUEUED",
        )

    def hold(self, job_id: str, held: bool = True) -> SchedulerJobSnapshot:
        snapshot = self._jobs[job_id]
        if snapshot.state != JobState.PENDING:
            raise StateTransitionError("只能暂停或恢复等待调度的作业")
        specification = replace(snapshot.specification, held=held)
        return self._replace_snapshot(
            snapshot,
            specification=specification,
            pending_reason=(PendingReason.USER_HOLD if held else PendingReason.NONE),
            message=("用户暂停调度" if held else "已解除调度暂停"),
            event_type="JOB_HELD" if held else "JOB_RELEASED",
        )

    def get(self, job_id: str) -> SchedulerJobSnapshot | None:
        return self._jobs.get(job_id)

    def snapshots(self) -> list[SchedulerJobSnapshot]:
        return sorted(
            self._jobs.values(),
            key=lambda snapshot: (
                snapshot.specification.submitted_order,
                snapshot.specification.submitted_at,
                snapshot.job_id,
            ),
        )

    def recover_orphaned_attempts(self, current_job_ids: set[str]) -> list[SchedulerJobSnapshot]:
        recovered: list[SchedulerJobSnapshot] = []
        with self.transaction():
            for snapshot in tuple(self._jobs.values()):
                if snapshot.job_id in current_job_ids or snapshot.state not in ACTIVE_JOB_STATES:
                    continue
                recovered.append(
                    self.transition(
                        snapshot.job_id,
                        JobState.LOST,
                        message="程序恢复时未找到对应队列记录",
                        event_type="ORPHANED_ATTEMPT_RECOVERED",
                        force=True,
                    )
                )
        return recovered

    def close(self) -> None:
        self.repository.close()

    def _pending_reason(
        self,
        specification: JobSpecification,
        *,
        available_slots: int,
        available_cores: int | None,
        available_memory: int | None,
        available_licenses: int | None,
        active_conflicts: set[tuple[str, str]],
        archive_reserved_keys: set[tuple[str, str]],
    ) -> PendingReason:
        if specification.held:
            return PendingReason.USER_HOLD
        dependency_states = [
            self._jobs[dependency_id].state
            for dependency_id in specification.dependency_job_ids
            if dependency_id in self._jobs
        ]
        if any(state in {JobState.FAILED, JobState.CANCELED, JobState.LOST} for state in dependency_states):
            return PendingReason.DEPENDENCY_FAILED
        if len(dependency_states) != len(specification.dependency_job_ids) or any(
            state != JobState.COMPLETED for state in dependency_states
        ):
            return PendingReason.DEPENDENCY
        if specification.conflict_key in archive_reserved_keys:
            return PendingReason.ARCHIVE_RESERVED
        if any(specification.conflict_key) and specification.conflict_key in active_conflicts:
            return PendingReason.ACTIVE_CONFLICT
        request = specification.resources
        if request.slots > available_slots:
            return PendingReason.SLOT_LIMIT
        if available_cores is not None and request.cores > available_cores:
            return PendingReason.CPU_LIMIT
        if available_memory is not None and request.memory_bytes > available_memory:
            return PendingReason.MEMORY_LIMIT
        if available_licenses is not None and request.license_tokens > available_licenses:
            return PendingReason.SLOT_LIMIT
        return PendingReason.NONE

    def _replace_snapshot(
        self,
        snapshot: SchedulerJobSnapshot,
        *,
        event_type: str,
        occurred_at: float = 0.0,
        **changes: object,
    ) -> SchedulerJobSnapshot:
        now = float(occurred_at or time.time())
        updated = replace(
            snapshot,
            version=snapshot.version + 1,
            updated_at=now,
            **changes,
        )
        return self._save(updated, event_type, occurred_at=now)

    def _save(
        self,
        snapshot: SchedulerJobSnapshot,
        event_type: str,
        *,
        occurred_at: float = 0.0,
    ) -> SchedulerJobSnapshot:
        with self.repository.transaction():
            self.repository.save_job(snapshot)
            self.repository.append_event(
                job_id=snapshot.job_id,
                event_type=event_type,
                state=snapshot.state,
                message=snapshot.message,
                attempt_id=snapshot.attempt_id,
                occurred_at=float(occurred_at or snapshot.updated_at or time.time()),
            )
        self._jobs[snapshot.job_id] = snapshot
        return snapshot

    @staticmethod
    def _optional_capacity(value: int | None) -> int | None:
        return None if value is None else max(0, int(value or 0))

    @staticmethod
    def _consume(capacity: int | None, amount: int) -> int | None:
        return None if capacity is None else max(0, capacity - max(0, int(amount or 0)))


def pending_reason_text(reason: PendingReason) -> str:
    return {
        PendingReason.NONE: "等待调度",
        PendingReason.DEPENDENCY: "等待前置作业完成",
        PendingReason.DEPENDENCY_FAILED: "前置作业未成功完成",
        PendingReason.USER_HOLD: "用户暂停调度",
        PendingReason.SLOT_LIMIT: "等待可用作业槽位",
        PendingReason.CPU_LIMIT: "等待可用 CPU 资源",
        PendingReason.MEMORY_LIMIT: "等待可用内存资源",
        PendingReason.ACTIVE_CONFLICT: "等待同目录同名作业结束",
        PendingReason.ARCHIVE_RESERVED: "等待同名计算目录归档完成",
    }[reason]
