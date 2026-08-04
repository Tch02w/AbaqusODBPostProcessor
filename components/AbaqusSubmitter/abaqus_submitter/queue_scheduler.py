"""Pure queue scheduling helpers for the Qt submitter."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .constants import (
    ACTIVE_STATUSES,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING_RUN,
    STATUS_WAITING_DEPENDENCY,
    TERMINAL_STATUSES,
)
from .models import QueueItem


@dataclass(frozen=True)
class RestartOldjobReference:
    oldjob_name: str
    source_dir: str
    source_kind: str
    oldjob_arg: str
    reference_key: str


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
    blocked_by = ""
    for run in active_runs.values():
        if managed_job_key(run.get("work_dir", ""), run.get("job_name", "")) == target_key:
            blocked_by = "active_runs"
            break
    if not blocked_by:
        exclude_item_id = queue_item.item_id if queue_item is not None else ""
        for item in queue_items:
            if exclude_item_id and item.item_id == exclude_item_id:
                continue
            if item.status not in ACTIVE_STATUSES:
                continue
            if queue_item_conflict_key(item) == target_key:
                blocked_by = "external_active_item" if item.is_external else "active_queue_item"
                break
    if not blocked_by:
        return ""
    return (
        f"无法提交作业 {job_name}：\n"
        "同一计算目录中已经存在同名运行作业。\n"
        f"冲突来源：{blocked_by}"
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


def queue_item_matches_oldjob_path(item: QueueItem, candidate: QueueItem) -> bool:
    oldjob_path = (item.oldjob_path or "").strip()
    if not oldjob_path:
        return False
    try:
        path = Path(oldjob_path)
        oldjob_dir = normalize_work_dir(str(path.parent))
    except (OSError, ValueError):
        return False
    if path.stem.lower() != candidate.job_name.lower():
        return False
    candidate_dirs = [
        candidate.archive_destination,
        candidate.effective_work_dir,
        candidate.external_work_dir,
        os.path.dirname(candidate.inp_path),
    ]
    return any(oldjob_dir == normalize_work_dir(candidate_dir) for candidate_dir in candidate_dirs if candidate_dir)


def find_queue_oldjob_item(
    oldjob_name: str,
    queue_items: list[QueueItem],
    current_item: QueueItem | None = None,
) -> QueueItem | None:
    if not oldjob_name:
        return None
    candidates = []
    for item in queue_items:
        if current_item is not None and item.item_id == current_item.item_id:
            continue
        if item.job_name.lower() == oldjob_name.lower():
            candidates.append(item)
    if not candidates:
        return None
    if current_item is not None:
        path_matches = [item for item in candidates if queue_item_matches_oldjob_path(current_item, item)]
        completed_path_matches = [item for item in path_matches if item.status == STATUS_COMPLETED]
        if completed_path_matches:
            return completed_path_matches[0]
        if path_matches:
            return path_matches[0]
    completed_candidates = [item for item in candidates if item.status == STATUS_COMPLETED]
    if completed_candidates:
        return completed_candidates[0]
    return candidates[0]


def _oldjob_odb_exists(source_dir: str, oldjob_name: str) -> bool:
    if not source_dir or not oldjob_name:
        return False
    try:
        return (Path(source_dir) / f"{oldjob_name}.odb").exists()
    except OSError:
        return False


def oldjob_source_dir_from_path(raw_path: str, oldjob_name: str) -> str:
    if not raw_path or not oldjob_name:
        return ""
    try:
        path = Path(raw_path)
    except (OSError, ValueError):
        return ""
    if path.suffix.lower() == ".odb" and path.stem.lower() == oldjob_name.lower() and path.exists():
        return str(path.parent)
    if path.suffix == "" and path.name.lower() == oldjob_name.lower():
        candidate_odb = path.with_suffix(".odb")
        if candidate_odb.exists():
            return str(candidate_odb.parent)
    return ""


def oldjob_stem_arg(source_dir: str, oldjob_name: str) -> str:
    if not source_dir or not oldjob_name:
        return ""
    return str(Path(source_dir) / oldjob_name)


def restart_oldjob_reference_key(source_dir: str, oldjob_name: str) -> str:
    normalized_source, normalized_oldjob = managed_job_key(source_dir, oldjob_name)
    if not normalized_source or not normalized_oldjob:
        return ""
    return f"{normalized_source}::{normalized_oldjob}"


def resolve_oldjob_source_dir(
    oldjob_name: str,
    queue_items: list[QueueItem],
    *,
    current_item: QueueItem | None = None,
    run_records: Iterable[dict] = (),
    candidate_paths: Iterable[str] = (),
) -> str:
    if not oldjob_name:
        return ""
    dependency = find_queue_oldjob_item(oldjob_name, queue_items, current_item)
    if dependency is not None and dependency.status == STATUS_COMPLETED:
        dependency_name = dependency.job_name
        archive_destination = (dependency.archive_destination or "").strip()
        if _oldjob_odb_exists(archive_destination, dependency_name):
            return archive_destination
        for run in reversed(list(run_records)):
            if (run.get("job_name") or "").lower() != dependency_name.lower():
                continue
            run_archive_destination = (run.get("archive_destination") or "").strip()
            if _oldjob_odb_exists(run_archive_destination, dependency_name):
                return run_archive_destination
            run_work_dir = (run.get("work_dir") or "").strip()
            if _oldjob_odb_exists(run_work_dir, dependency_name):
                return run_work_dir
        for source_dir in (
            dependency.effective_work_dir,
            dependency.external_work_dir,
            os.path.dirname(dependency.inp_path),
        ):
            if _oldjob_odb_exists(source_dir, dependency_name):
                return source_dir

    for raw_path in candidate_paths:
        source_dir = oldjob_source_dir_from_path(raw_path, oldjob_name)
        if source_dir:
            return source_dir
    return ""


def restart_oldjob_source_kind(
    oldjob_name: str,
    source_dir: str,
    queue_items: list[QueueItem],
    *,
    current_item: QueueItem | None = None,
    run_records: Iterable[dict] = (),
    manual_candidate_paths: Iterable[str] = (),
    external_candidate_paths: Iterable[str] = (),
) -> str:
    if not oldjob_name or not source_dir:
        return ""
    normalized_source = managed_job_key(source_dir, "")[0]
    for item in queue_items:
        if current_item is not None and item.item_id == current_item.item_id:
            continue
        if item.job_name.lower() != oldjob_name.lower():
            continue
        archive_destination = (item.archive_destination or "").strip()
        if archive_destination and managed_job_key(archive_destination, "")[0] == normalized_source:
            return "archive"
        for item_source in (
            item.effective_work_dir,
            item.external_work_dir,
            os.path.dirname(item.inp_path),
        ):
            if item_source and managed_job_key(item_source, "")[0] == normalized_source:
                return "queue-workdir"
    for run in reversed(list(run_records)):
        if (run.get("job_name") or "").lower() != oldjob_name.lower():
            continue
        archive_destination = (run.get("archive_destination") or "").strip()
        if archive_destination and managed_job_key(archive_destination, "")[0] == normalized_source:
            return "archive"
        work_dir = (run.get("work_dir") or "").strip()
        if work_dir and managed_job_key(work_dir, "")[0] == normalized_source:
            return "queue-workdir"
    for raw_path in manual_candidate_paths:
        candidate_source = oldjob_source_dir_from_path(raw_path, oldjob_name)
        if candidate_source and managed_job_key(candidate_source, "")[0] == normalized_source:
            return "manual"
    for raw_path in external_candidate_paths:
        candidate_source = oldjob_source_dir_from_path(raw_path, oldjob_name)
        if candidate_source and managed_job_key(candidate_source, "")[0] == normalized_source:
            return "external"
    return "external"


def build_restart_oldjob_reference(
    oldjob_name: str,
    source_dir: str,
    queue_items: list[QueueItem],
    *,
    current_item: QueueItem | None = None,
    run_records: Iterable[dict] = (),
    manual_candidate_paths: Iterable[str] = (),
    external_candidate_paths: Iterable[str] = (),
) -> RestartOldjobReference:
    if not oldjob_name or not source_dir:
        return RestartOldjobReference("", "", "", "", "")
    source_kind = restart_oldjob_source_kind(
        oldjob_name,
        source_dir,
        queue_items,
        current_item=current_item,
        run_records=run_records,
        manual_candidate_paths=manual_candidate_paths,
        external_candidate_paths=external_candidate_paths,
    )
    return RestartOldjobReference(
        oldjob_name=oldjob_name,
        source_dir=source_dir,
        source_kind=source_kind,
        oldjob_arg=oldjob_stem_arg(source_dir, oldjob_name),
        reference_key=restart_oldjob_reference_key(source_dir, oldjob_name),
    )


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


def restart_dependents_using_current_work_dir(
    run: dict,
    queue_items: list[QueueItem],
) -> list[QueueItem]:
    """Return Restart dependents that may already be using this run's current work dir."""
    dependents = []
    for item in unfinished_restart_dependents(run, queue_items):
        if item.status in ACTIVE_STATUSES or item.active_job_key:
            dependents.append(item)
    return dependents


def get_managed_active_job_keys(
    active_runs: dict[str, dict],
    queue_items: list[QueueItem],
    *,
    exclude_item_id: str = "",
    include_external: bool = True,
) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for run in active_runs.values():
        if run.get("is_external") and not include_external:
            continue
        keys.add(
            managed_job_key(
                run.get("work_dir", ""),
                run.get("job_name", ""),
            )
        )
    for item in queue_items:
        if exclude_item_id and item.item_id == exclude_item_id:
            continue
        if item.is_external and not include_external:
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
