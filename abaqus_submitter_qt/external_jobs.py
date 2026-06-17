"""External Abaqus job scanning and queue merge helpers."""

from __future__ import annotations

import os

from .constants import ACTIVE_STATUSES, STATUS_UNKNOWN
from .models import QueueItem
from .process_scanner import scan_running_abaqus_jobs_by_psutil
from .qt_compat import QtCore, Signal
from .queue_scheduler import (
    effective_queue_item_work_dir,
    managed_job_key,
    normalize_work_dir,
    queue_item_conflict_key,
)


class ExternalJobScanWorker(QtCore.QObject):
    """Run the external Abaqus process scan outside the UI thread."""

    finished = Signal(str, list, list)
    failed = Signal(str, str)

    def __init__(self, work_dir: str, known_external_jobs: list[dict] | None = None):
        super().__init__()
        self.work_dir = work_dir
        self.known_external_jobs = known_external_jobs or []

    def run(self) -> None:
        try:
            jobs, skipped = scan_running_abaqus_jobs_by_psutil(
                self.work_dir,
                force=True,
                known_external_jobs=self.known_external_jobs,
            )
        except Exception as exc:  # keep the worker boundary from leaking exceptions
            self.failed.emit(self.work_dir, str(exc))
            return
        self.finished.emit(self.work_dir, jobs, skipped)


def collect_known_external_jobs(
    queue_items: list[QueueItem],
    work_dir: str,
) -> list[dict]:
    normalized_work_dir = normalize_work_dir(work_dir)
    jobs = []
    for item in queue_items:
        if not item.is_external:
            continue
        item_work_dir = effective_queue_item_work_dir(item)
        if not item_work_dir:
            continue
        if normalize_work_dir(item_work_dir) != normalized_work_dir:
            continue
        jobs.append(
            {
                "item_id": item.item_id,
                "job_name": item.job_name,
                "work_dir": item_work_dir,
                "inp_path": item.inp_path,
            }
        )
    return jobs


def build_queue_item_index(
    queue_items: list[QueueItem],
) -> dict[tuple[str, str], QueueItem]:
    index = {}
    for item in queue_items:
        key = queue_item_conflict_key(item)
        if not key[0]:
            continue
        index[key] = item
    return index


def merge_external_scan_results(
    *,
    queue_items: list[QueueItem],
    work_dir: str,
    jobs: list[dict],
) -> dict:
    queue_item_by_key = build_queue_item_index(queue_items)
    added = 0
    updated = 0
    status_only_updates = 0
    updated_items: list[QueueItem] = []
    active_external_items: list[QueueItem] = []
    debug_records: list[dict] = []
    memory_records: list[dict] = []

    for job in jobs:
        job_name = job.get("job_name", "")
        job_work_dir = job.get("work_dir") or work_dir
        if not job_name:
            continue

        key = managed_job_key(
            job_work_dir,
            job_name,
        )
        matched = queue_item_by_key.get(key)
        runtime_status = job.get("runtime_status") or STATUS_UNKNOWN
        runtime_message = job.get("runtime_message") or "外部作业状态待确认"

        if job.get("status_only_update"):
            if matched is not None:
                matched.pids = job.get("pids", [])
                matched.pid_create_times = job.get("pid_create_times", {})
                matched.rss_bytes = int(job.get("rss_bytes") or 0)
                matched.status = runtime_status
                matched.message = runtime_message
                status_only_updates += 1
                updated_items.append(matched)
                memory_records.append(
                    {
                        "item": matched,
                        "job": job,
                        "key": key,
                        "work_dir": job_work_dir,
                    }
                )
                if matched.status in ACTIVE_STATUSES:
                    active_external_items.append(matched)
                debug_records.append(
                    {
                        "job_name": job_name,
                        "job_work_dir": job_work_dir,
                        "job": job,
                        "runtime_status": runtime_status,
                        "runtime_message": runtime_message,
                    }
                )
            continue

        if matched is None:
            new_item = QueueItem(
                inp_path=job.get("inp_path") or os.path.join(job_work_dir, f"{job_name}.inp"),
                job_name=job_name,
                source="external_psutil",
                status=runtime_status,
                selected=False,
                valid=True,
                message=runtime_message,
                run_mode="restart" if job.get("oldjob_path") else "normal",
                oldjob_name=job.get("restart_dependency", ""),
                oldjob_path=job.get("oldjob_path", ""),
                fortran_path=job.get("for_file", ""),
                cores=int(job.get("cores") or 0) if str(job.get("cores") or "").isdigit() else 0,
                memory=job.get("memory_setting", ""),
                job_type=job.get("job_type", "Abaqus"),
                is_external=True,
                external_work_dir=job_work_dir,
                effective_work_dir=job_work_dir,
                pids=job.get("pids", []),
                pid_create_times=job.get("pid_create_times", {}),
                rss_bytes=int(job.get("rss_bytes") or 0),
            )
            queue_items.append(new_item)
            queue_item_by_key[key] = new_item
            target_item = new_item
            added += 1
        else:
            matched.pids = job.get("pids", matched.pids)
            matched.pid_create_times = job.get("pid_create_times", matched.pid_create_times)
            matched.rss_bytes = int(job.get("rss_bytes") or matched.rss_bytes or 0)
            matched.status = runtime_status
            matched.message = runtime_message
            matched.external_work_dir = job_work_dir
            matched.effective_work_dir = job_work_dir
            target_item = matched
            updated += 1

        updated_items.append(target_item)
        if target_item.is_external:
            memory_records.append(
                {
                    "item": target_item,
                    "job": job,
                    "key": key,
                    "work_dir": job_work_dir,
                }
            )
            if target_item.status in ACTIVE_STATUSES:
                active_external_items.append(target_item)

        debug_records.append(
            {
                "job_name": job_name,
                "job_work_dir": job_work_dir,
                "job": job,
                "runtime_status": runtime_status,
                "runtime_message": runtime_message,
            }
        )

    return {
        "added": added,
        "updated": updated,
        "status_only_updates": status_only_updates,
        "updated_items": updated_items,
        "active_external_items": active_external_items,
        "debug_records": debug_records,
        "memory_records": memory_records,
    }
