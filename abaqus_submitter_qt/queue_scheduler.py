"""Pure queue scheduling helpers for the Qt submitter."""

from __future__ import annotations

import os
from pathlib import Path

from .constants import (
    ACTIVE_STATUSES,
    STATUS_COMPLETED,
    STATUS_DATACHECK_COMPLETED,
    STATUS_DATACHECK_FAILED,
    STATUS_FAILED,
    STATUS_PENDING_RUN,
    STATUS_WAITING_DEPENDENCY,
    TERMINAL_STATUSES,
)
from .models import QueueItem


def normalize_work_dir(work_dir: str) -> str:
    value = str(work_dir or "").strip()
    if not value:
        return ""
    return os.path.normcase(os.path.abspath(os.path.normpath(value)))


def managed_job_key(work_dir: str, job_name: str) -> tuple[str, str]:
    return (
        normalize_work_dir(work_dir),
        (job_name or "").lower(),
    )


def effective_queue_item_work_dir(item: QueueItem) -> str:
    effective_work_dir = (item.effective_work_dir or "").strip()
    if effective_work_dir:
        return effective_work_dir
    if item.is_external:
        return item.external_work_dir or os.path.dirname(item.inp_path)
    calculation_root = (item.calculation_root_dir or "").strip()
    if calculation_root:
        return str(Path(calculation_root) / item.job_name)
    return os.path.dirname(item.inp_path)


def queue_item_conflict_key(item: QueueItem) -> tuple[str, str]:
    return managed_job_key(
        effective_queue_item_work_dir(item),
        item.job_name,
    )


def submit_effective_work_dir(inp_file: str, job_name: str, queue_item: QueueItem | None = None) -> str:
    if queue_item is not None:
        effective_work_dir = (queue_item.effective_work_dir or "").strip()
        if effective_work_dir:
            return effective_work_dir
        calculation_root = (queue_item.calculation_root_dir or "").strip()
        if calculation_root:
            return str(Path(calculation_root) / job_name)
    return str(Path(inp_file).parent) if inp_file else ""


def submit_conflict_key(inp_file: str, job_name: str, queue_item: QueueItem | None = None) -> tuple[str, str]:
    return managed_job_key(
        submit_effective_work_dir(inp_file, job_name, queue_item),
        job_name,
    )


def find_formal_queue_conflict(
    target_item: QueueItem,
    queue_items: list[QueueItem],
) -> QueueItem | None:
    target_key = queue_item_conflict_key(target_item)
    if not target_key[0] or not target_key[1]:
        return None
    for item in queue_items:
        if item.item_id == target_item.item_id:
            continue
        if queue_item_is_finished(item):
            continue
        if queue_item_conflict_key(item) == target_key:
            return item
    return None


def find_queue_item_by_key(
    *,
    work_dir: str,
    job_name: str,
    queue_items: list[QueueItem],
) -> QueueItem | None:
    target_key = managed_job_key(work_dir, job_name)
    for item in queue_items:
        if queue_item_conflict_key(item) == target_key:
            return item
    return None


def queue_status_counts(queue_items: list[QueueItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in queue_items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


def active_submit_conflict_message(
    *,
    inp_file: str,
    job_name: str,
    queue_item: QueueItem | None,
    active_runs: dict[str, dict],
    queue_items: list[QueueItem],
) -> str:
    target_key = submit_conflict_key(
        inp_file,
        job_name,
        queue_item,
    )
    active_keys = get_managed_active_job_keys(
        active_runs,
        queue_items,
        exclude_item_id=queue_item.item_id if queue_item is not None else "",
    )
    if target_key not in active_keys:
        return ""
    return (
        f"无法提交作业 {job_name}：\n"
        "同一计算目录中已经存在同名运行作业。"
    )


def managed_active_statuses() -> set[str]:
    return set(ACTIVE_STATUSES)


def queue_item_is_finished(item: QueueItem) -> bool:
    return item.status in TERMINAL_STATUSES


def oldjob_name_from_item(item: QueueItem) -> str:
    oldjob_name = (item.oldjob_name or "").strip()
    if oldjob_name:
        return oldjob_name
    oldjob_path = (item.oldjob_path or "").strip()
    return Path(oldjob_path).stem if oldjob_path else ""


def find_queue_oldjob_item(
    oldjob_name: str,
    queue_items: list[QueueItem],
    current_item: QueueItem | None = None,
) -> QueueItem | None:
    if not oldjob_name:
        return None
    for item in queue_items:
        if current_item is not None and item.item_id == current_item.item_id:
            continue
        if item.job_name.lower() == oldjob_name.lower():
            return item
    return None


def queue_item_dependency_state(
    item: QueueItem,
    queue_items: list[QueueItem],
) -> tuple[str, QueueItem | None]:
    dependency = find_queue_oldjob_item(
        oldjob_name_from_item(item),
        queue_items,
        item,
    )
    if dependency is None:
        return "ready", None
    if dependency.status == STATUS_COMPLETED:
        return "ready", dependency
    if queue_item_is_finished(dependency):
        return "failed", dependency
    return "waiting", dependency


def refresh_queue_dependencies(
    queue_items: list[QueueItem],
) -> None:
    for item in queue_items:
        if item.status not in {STATUS_PENDING_RUN, STATUS_WAITING_DEPENDENCY}:
            continue
        state, dependency = queue_item_dependency_state(
            item,
            queue_items,
        )
        if state == "ready":
            if item.status == STATUS_WAITING_DEPENDENCY:
                item.status = STATUS_PENDING_RUN
                item.message = "前置作业已完成，等待提交"
            continue
        if dependency is None:
            continue
        if state == "waiting":
            item.status = STATUS_WAITING_DEPENDENCY
            item.message = f"等待前置作业完成：{dependency.job_name}"
            continue
        item.status = STATUS_FAILED
        item.message = f"前置作业未完成，跳过重启动：{dependency.job_name} ({dependency.status})"


def queue_item_depends_on_job(
    item: QueueItem,
    job_name: str,
    work_dir: str,
) -> bool:
    if not job_name:
        return False
    oldjob_name = (item.oldjob_name or "").strip()
    if oldjob_name and oldjob_name.lower() == job_name.lower():
        return True
    oldjob_path = (item.oldjob_path or "").strip()
    if not oldjob_path:
        return False
    oldjob_stem = Path(oldjob_path).stem
    if oldjob_stem.lower() == job_name.lower():
        return True
    try:
        expected_odb = (Path(work_dir) / f"{job_name}.odb").resolve()
        return Path(oldjob_path).resolve() == expected_odb
    except OSError:
        return False


def unfinished_restart_dependents(
    run: dict,
    queue_items: list[QueueItem],
) -> list[QueueItem]:
    job_name = run.get("job_name", "")
    work_dir = run.get("work_dir", "")
    dependents = []
    for item in queue_items:
        if item.job_name == job_name:
            continue
        if not queue_item_depends_on_job(item, job_name, work_dir):
            continue
        if not queue_item_is_finished(item):
            dependents.append(item)
    return dependents


def get_managed_active_job_keys(
    active_runs: dict[str, dict],
    queue_items: list[QueueItem],
    *,
    exclude_item_id: str = "",
) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for run in active_runs.values():
        keys.add(
            managed_job_key(
                run.get("work_dir", ""),
                run.get("job_name", ""),
            )
        )
    for item in queue_items:
        if exclude_item_id and item.item_id == exclude_item_id:
            continue
        if item.status not in ACTIVE_STATUSES:
            continue
        keys.add(queue_item_conflict_key(item))
    return keys


def get_managed_active_job_names(
    active_runs: dict[str, dict],
    queue_items: list[QueueItem],
) -> set[str]:
    names: set[str] = set()

    for run in active_runs.values():
        job_name = str(
            run.get(
                "job_name",
                "",
            )
            or ""
        ).strip()

        if job_name:
            names.add(job_name)

    for item in queue_items:
        if item.status not in ACTIVE_STATUSES:
            continue

        job_name = str(item.job_name or "").strip()

        if job_name:
            names.add(job_name)

    return names
