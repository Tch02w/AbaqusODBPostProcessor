"""Pure queue scheduling helpers for the Qt submitter."""

from __future__ import annotations

import os
from pathlib import Path

from .constants import (
    STATUS_CONFIRMING,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_TERMINATING,
)
from .models import QueueItem


def managed_job_key(work_dir: str, job_name: str) -> tuple[str, str]:
    return (
        os.path.normcase(os.path.abspath(work_dir or "")),
        (job_name or "").lower(),
    )


def managed_active_statuses() -> set[str]:
    return {
        STATUS_RUNNING,
        STATUS_STARTING,
        STATUS_CONFIRMING,
        STATUS_TERMINATING,
    }


def queue_item_is_finished(item: QueueItem) -> bool:
    return item.status in {
        "已完成",
        "运行失败",
        "已取消",
        "已终止",
        "疑似异常中断",
        "状态未知",
    }


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
    if dependency.status == "已完成":
        return "ready", dependency
    if queue_item_is_finished(dependency):
        return "failed", dependency
    return "waiting", dependency


def refresh_queue_dependencies(
    queue_items: list[QueueItem],
) -> None:
    for item in queue_items:
        if item.status not in {"待运行", "等待前置"}:
            continue
        state, dependency = queue_item_dependency_state(
            item,
            queue_items,
        )
        if state == "ready":
            if item.status == "等待前置":
                item.status = "待运行"
                item.message = "前置作业已完成，等待提交"
            continue
        if dependency is None:
            continue
        if state == "waiting":
            item.status = "等待前置"
            item.message = f"等待前置作业完成：{dependency.job_name}"
            continue
        item.status = "运行失败"
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
        if not item.is_external:
            continue
        if item.status not in managed_active_statuses():
            continue
        work_dir = item.external_work_dir or os.path.dirname(item.inp_path)
        keys.add(managed_job_key(work_dir, item.job_name))
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
        if not item.is_external:
            continue

        if item.status not in managed_active_statuses():
            continue

        job_name = str(item.job_name or "").strip()

        if job_name:
            names.add(job_name)

    return names
