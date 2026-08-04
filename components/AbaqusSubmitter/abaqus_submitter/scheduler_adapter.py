"""Queue Item 与 Scheduler Core 之间的 Adapter。"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from uuid import uuid4

from .constants import (
    STATUS_CANCELED,
    STATUS_COMPLETED,
    STATUS_CONFIRMING,
    STATUS_DATACHECK_COMPLETED,
    STATUS_DATACHECK_FAILED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING_CONFIRM,
    STATUS_PENDING_RUN,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_TERMINATED,
    STATUS_TERMINATING,
    STATUS_UNKNOWN,
    STATUS_WAITING_DEPENDENCY,
)
from .models import QueueItem
from .queue_scheduler import (
    effective_queue_item_work_dir,
    oldjob_name_from_item,
    queue_item_conflict_key,
)
from .scheduling import (
    ACTIVE_JOB_STATES,
    JobSpecification,
    JobState,
    PendingReason,
    ResourceRequest,
    SchedulerCore,
    SchedulerJobSnapshot,
    pending_reason_text,
)


_MEMORY_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)?\s*$", re.IGNORECASE)


def scheduler_state_from_queue_status(status: str) -> JobState:
    return {
        STATUS_PENDING_CONFIRM: JobState.PENDING,
        STATUS_PENDING_RUN: JobState.PENDING,
        STATUS_WAITING_DEPENDENCY: JobState.PENDING,
        STATUS_STARTING: JobState.STARTING,
        STATUS_RUNNING: JobState.RUNNING,
        STATUS_CONFIRMING: JobState.COMPLETING,
        STATUS_TERMINATING: JobState.COMPLETING,
        STATUS_COMPLETED: JobState.COMPLETED,
        STATUS_DATACHECK_COMPLETED: JobState.COMPLETED,
        STATUS_FAILED: JobState.FAILED,
        STATUS_DATACHECK_FAILED: JobState.FAILED,
        STATUS_CANCELED: JobState.CANCELED,
        STATUS_TERMINATED: JobState.CANCELED,
        STATUS_UNKNOWN: JobState.LOST,
        STATUS_INTERRUPTED: JobState.LOST,
    }.get(status, JobState.PENDING)


def pending_reason_from_queue_item(item: QueueItem) -> PendingReason:
    if item.held:
        return PendingReason.USER_HOLD
    if item.status == STATUS_WAITING_DEPENDENCY:
        return PendingReason.DEPENDENCY
    try:
        return PendingReason(item.pending_reason or "")
    except ValueError:
        return PendingReason.NONE


def parse_memory_request_bytes(value: object) -> int:
    text = str(value or "").strip()
    if not text or text.endswith("%"):
        return 0
    match = _MEMORY_PATTERN.fullmatch(text)
    if match is None:
        return 0
    number = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    multiplier = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }[unit]
    return max(0, int(number * multiplier))


def build_dependency_job_ids(
    item: QueueItem,
    queue_items: Iterable[QueueItem],
) -> tuple[str, ...]:
    explicit = [str(job_id) for job_id in item.dependency_job_ids if str(job_id)]
    if explicit:
        return tuple(dict.fromkeys(explicit))
    oldjob_name = oldjob_name_from_item(item).lower()
    if not oldjob_name:
        return ()
    for candidate in queue_items:
        if candidate.item_id == item.item_id:
            continue
        if candidate.job_name.lower() == oldjob_name:
            return (candidate.job_id or candidate.item_id,)
    return ()


def queue_item_to_job_specification(
    item: QueueItem,
    *,
    queue_items: Iterable[QueueItem],
    submitted_order: int,
    estimated_memory_bytes: int = 0,
) -> JobSpecification:
    job_id = item.job_id or item.item_id
    item.job_id = job_id
    if item.submitted_at <= 0:
        item.submitted_at = time.time()
    dependency_job_ids = build_dependency_job_ids(item, queue_items)
    if list(dependency_job_ids) != item.dependency_job_ids:
        item.dependency_job_ids = list(dependency_job_ids)
    requested_memory = parse_memory_request_bytes(item.memory)
    return JobSpecification(
        job_id=job_id,
        job_name=item.job_name,
        work_dir=effective_queue_item_work_dir(item),
        resources=ResourceRequest(
            slots=1,
            cores=max(0, int(item.cores or 0)),
            memory_bytes=max(requested_memory, int(estimated_memory_bytes or 0)),
        ),
        priority=int(item.priority or 0),
        submitted_order=submitted_order,
        submitted_at=item.submitted_at,
        dependency_job_ids=dependency_job_ids,
        conflict_key=queue_item_conflict_key(item),
        held=bool(item.held),
        metadata={
            "item_id": item.item_id,
            "source": item.source,
            "run_mode": item.run_mode,
        },
    )


def reconcile_scheduler_from_queue(
    scheduler: SchedulerCore,
    queue_items: list[QueueItem],
    *,
    estimated_memory_by_job: Mapping[str, int] | None = None,
) -> None:
    estimates = estimated_memory_by_job or {}
    with scheduler.transaction():
        for order, item in enumerate(queue_items):
            if not item.job_name:
                continue
            specification = queue_item_to_job_specification(
                item,
                queue_items=queue_items,
                submitted_order=order,
                estimated_memory_bytes=int(estimates.get(item.job_name) or 0),
            )
            state = scheduler_state_from_queue_status(item.status)
            if state in ACTIVE_JOB_STATES and not item.attempt_id:
                item.attempt_id = uuid4().hex
            scheduler.reconcile(
                specification,
                state=state,
                pending_reason=pending_reason_from_queue_item(item),
                attempt_id=item.attempt_id,
                message=item.message,
            )
            item.scheduler_state = scheduler.get(specification.job_id).state.value


def apply_scheduler_snapshot_to_queue_item(
    snapshot: SchedulerJobSnapshot,
    item: QueueItem,
    *,
    update_message: bool = True,
    update_status: bool = True,
) -> None:
    item.scheduler_state = snapshot.state.value
    item.pending_reason = snapshot.pending_reason.value
    item.attempt_id = snapshot.attempt_id
    if update_status:
        item.status = queue_status_from_scheduler_snapshot(snapshot, item)
    if update_message and snapshot.state == JobState.PENDING and snapshot.pending_reason != PendingReason.NONE:
        item.message = pending_reason_text(snapshot.pending_reason)


def queue_status_from_scheduler_snapshot(
    snapshot: SchedulerJobSnapshot,
    item: QueueItem,
) -> str:
    if snapshot.state == JobState.PENDING:
        if snapshot.pending_reason in {PendingReason.DEPENDENCY, PendingReason.DEPENDENCY_FAILED}:
            return STATUS_WAITING_DEPENDENCY
        return STATUS_PENDING_RUN
    if snapshot.state in {JobState.DISPATCHED, JobState.STARTING}:
        return STATUS_STARTING
    if snapshot.state == JobState.RUNNING:
        return STATUS_RUNNING
    if snapshot.state == JobState.COMPLETING:
        return STATUS_TERMINATING if item.status == STATUS_TERMINATING else STATUS_CONFIRMING
    if snapshot.state == JobState.COMPLETED:
        return STATUS_DATACHECK_COMPLETED if item.datacheck_only else STATUS_COMPLETED
    if snapshot.state == JobState.FAILED:
        return STATUS_DATACHECK_FAILED if item.datacheck_only else STATUS_FAILED
    if snapshot.state == JobState.CANCELED:
        return STATUS_TERMINATED if item.status in {STATUS_TERMINATING, STATUS_TERMINATED} else STATUS_CANCELED
    return STATUS_UNKNOWN
