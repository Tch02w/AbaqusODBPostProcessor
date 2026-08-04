"""External Abaqus job scanning and queue merge helpers."""

from __future__ import annotations

import os
import time

from .abaqus_diagnostics import inspect_job_files, parse_datetime_from_abaqus_text, read_file_head
from .constants import ACTIVE_STATUSES, STATUS_PENDING_CONFIRM, STATUS_RUNNING, STATUS_UNKNOWN, TERMINAL_STATUSES
from .models import QueueItem
from .process_scanner import (
    EXTERNAL_SCAN_MAX_CHILD_DEPTH,
    classify_external_job_runtime,
    fetch_psutil_process_rows_for_external_scan,
    normalize_work_dir,
    scan_running_abaqus_jobs_by_psutil,
    work_dir_matches_scan_root,
)
from .qt_compat import QtCore, Signal
from .runtime_record import RuntimeRecord
from .queue_scheduler import (
    effective_queue_item_work_dir,
    managed_job_key,
    managed_active_statuses,
    queue_item_conflict_key,
)


class ExternalJobScanWorker(QtCore.QObject):
    """Run the external Abaqus process scan outside the UI thread."""

    finished = Signal(str, list, list)
    failed = Signal(str, str)

    def __init__(
        self,
        work_dir: str,
        known_external_jobs: list[dict] | None = None,
        process_rows: list[dict] | None = None,
    ):
        super().__init__()
        self.work_dir = work_dir
        self.known_external_jobs = known_external_jobs or []
        self.process_rows = process_rows

    def run(self) -> None:
        try:
            scan_roots = [self.work_dir]
            seen_roots = {normalize_work_dir(self.work_dir)}
            for known in self.known_external_jobs:
                known_work_dir = known.get("work_dir") or os.path.dirname(known.get("inp_path", ""))
                if not known_work_dir:
                    continue
                if work_dir_matches_scan_root(
                    known_work_dir,
                    self.work_dir,
                    EXTERNAL_SCAN_MAX_CHILD_DEPTH,
                ):
                    continue
                normalized_known_root = normalize_work_dir(known_work_dir)
                if not normalized_known_root or normalized_known_root in seen_roots:
                    continue
                seen_roots.add(normalized_known_root)
                scan_roots.append(known_work_dir)

            process_rows = self.process_rows
            if process_rows is None:
                process_rows = fetch_psutil_process_rows_for_external_scan(force=True)

            merged_jobs = {}
            skipped = []
            for scan_root in scan_roots:
                root_jobs, root_skipped = scan_running_abaqus_jobs_by_psutil(
                    scan_root,
                    force=True,
                    known_external_jobs=self.known_external_jobs,
                    process_rows=process_rows,
                )
                skipped.extend(root_skipped)
                for job in root_jobs:
                    job_name = str(job.get("job_name") or "").strip().lower()
                    job_work_dir = normalize_work_dir(job.get("work_dir") or scan_root)
                    if not job_name or not job_work_dir:
                        continue
                    merged_jobs[(job_work_dir, job_name)] = job
        except Exception as exc:  # keep the worker boundary from leaking exceptions
            self.failed.emit(self.work_dir, str(exc))
            return
        self.finished.emit(self.work_dir, list(merged_jobs.values()), skipped)


def collect_known_external_jobs(
    queue_items: list[QueueItem],
    work_dir: str,
) -> list[dict]:
    jobs = []
    for item in queue_items:
        if not item.is_external and item.status not in ACTIVE_STATUSES and item.status != STATUS_UNKNOWN:
            continue
        item_work_dir = effective_queue_item_work_dir(item)
        if not item_work_dir:
            continue
        if not item.is_external and item.status == STATUS_UNKNOWN:
            pass
        elif not work_dir_matches_scan_root(
            item_work_dir,
            work_dir,
            EXTERNAL_SCAN_MAX_CHILD_DEPTH,
        ):
            continue
        jobs.append(
            {
                "item_id": item.item_id,
                "job_name": item.job_name,
                "work_dir": item_work_dir,
                "inp_path": item.inp_path,
                "is_external": bool(item.is_external),
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


def resolve_unmatched_restored_unknown_items(
    *,
    queue_items: list[QueueItem],
    scanned_keys: set[tuple[str, str]],
) -> tuple[list[QueueItem], list[dict]]:
    updated_items = []
    terminal_records = []
    for item in queue_items:
        if item.status != STATUS_UNKNOWN:
            continue
        key = queue_item_conflict_key(item)
        if not key[0] or key in scanned_keys:
            continue
        work_dir = effective_queue_item_work_dir(item)
        if not work_dir:
            item.status = STATUS_PENDING_CONFIRM
            item.message = "程序重启后缺少运行目录，请人工确认"
            updated_items.append(item)
            continue
        diagnostics_status, diagnostics_detail = inspect_job_files(work_dir, item.job_name)
        runtime = classify_external_job_runtime(
            job_name=item.job_name,
            work_dir=work_dir,
            process_names=[],
            process_cmdlines=[],
            diagnostics_status=diagnostics_status,
            diagnostics_detail=diagnostics_detail,
        )
        status = runtime["status"]
        if status == STATUS_UNKNOWN:
            item.status = STATUS_PENDING_CONFIRM
            item.message = "程序重启后未发现运行进程或终态证据，请人工确认"
            updated_items.append(item)
            continue
        item.status = status
        item.message = runtime["message"]
        updated_items.append(item)
        if item.status in TERMINAL_STATUSES:
            terminal_records.append(
                {
                    "item": item,
                    "job": {},
                    "key": key,
                    "work_dir": work_dir,
                    "previous_status": STATUS_UNKNOWN,
                    "status": item.status,
                }
            )
    return updated_items, terminal_records


class ExternalJobCoordinator:
    """Apply external scan side effects through a narrow host interface."""

    def __init__(self, host):
        self.host = host

    def apply_scan_merge_result(
        self,
        *,
        merge_result: dict,
        operation: str,
    ) -> None:
        terminal_external_records = merge_result.get("terminal_external_records") or []
        self.update_memory_and_runtime_records(merge_result.get("memory_records") or [], operation)
        self.handle_terminal_records(terminal_external_records)
        self.append_debug_records(merge_result.get("debug_records") or [])

        if terminal_external_records:
            self.host.refresh_queue_dependencies()
            self.host.request_dispatch_queue()

    def update_memory_and_runtime_records(
        self,
        memory_records: list[dict],
        operation: str,
    ) -> None:
        for record in memory_records:
            target_item = record["item"]
            job = record["job"]
            key = record["key"]
            job_work_dir = record["work_dir"]
            self.host.memory_monitor_service.update_external_job_estimate(
                job_name=target_item.job_name,
                rss_bytes=int(target_item.rss_bytes or 0),
                process_count=len(target_item.pids or []),
                process_names=", ".join(job.get("process_names") or []),
            )
            if target_item.status in managed_active_statuses():
                ext_key = f"{key[0]}::{target_item.job_name.lower()}"
                self.host.memory_adapter.register_job(
                    job_key=ext_key,
                    job_name=target_item.job_name,
                    work_dir=target_item.effective_work_dir or job_work_dir,
                )
                self.host.memory_adapter.activate_job(ext_key)
                self.attach_external_runtime_job(
                    item=target_item,
                    key=key,
                    job=job,
                    job_work_dir=job_work_dir,
                    operation=operation,
                )

    def external_solver_pids_from_scan(self, job: dict) -> tuple[int, ...]:
        pids = []
        for value in job.get("solver_pids") or ():
            try:
                pids.append(int(value))
            except (TypeError, ValueError):
                continue
        return tuple(sorted(set(pids)))

    def infer_scanned_job_submitted_at(self, work_dir: str, job_name: str, job: dict | None = None) -> float:
        for suffix in (".log", ".dat", ".msg", ".sta"):
            path = os.path.join(work_dir, job_name + suffix)
            if not os.path.exists(path):
                continue
            try:
                parsed = parse_datetime_from_abaqus_text(read_file_head(path))
            except OSError:
                parsed = None
            if parsed is not None:
                try:
                    return float(parsed.timestamp())
                except (OSError, OverflowError, ValueError):
                    pass

        create_times = []
        for value in (job or {}).get("pid_create_times", {}).values():
            try:
                timestamp = float(value)
            except (TypeError, ValueError):
                continue
            if timestamp > 0:
                create_times.append(timestamp)
        return min(create_times) if create_times else 0.0

    def update_attached_external_run(self, job_key: str, job: dict, item: QueueItem) -> bool:
        run = self.host.active_runs.get(job_key)
        if not run:
            return False
        if not run.get("is_external") and not run.get("is_scan_attached"):
            return True

        solver_pids = self.external_solver_pids_from_scan(job)
        if solver_pids:
            known_pids = set()
            for value in run.get("known_solver_pids") or ():
                try:
                    known_pids.add(int(value))
                except (TypeError, ValueError):
                    continue
            known_pids.update(solver_pids)
            now = time.monotonic()
            run["known_solver_pids"] = tuple(sorted(known_pids))
            run.setdefault("solver_pid_seen_at", now)
            run["solver_pid_last_seen_at"] = now
            run["solver_pid_confidence"] = "high"

        rss_bytes = int(item.rss_bytes or job.get("rss_bytes") or 0)
        if rss_bytes:
            run["memory_current"] = rss_bytes
            run["memory_peak"] = max(int(run.get("memory_peak", 0) or 0), rss_bytes)
        run["process_snapshot_available"] = False
        return True

    def attach_external_runtime_job(
        self,
        *,
        item: QueueItem,
        key: tuple[str, str],
        job: dict,
        job_work_dir: str,
        operation: str | None = None,
    ) -> bool:
        if item.status != STATUS_RUNNING:
            return False
        work_dir = item.effective_work_dir or item.external_work_dir or job_work_dir
        if not work_dir:
            return False

        job_key = f"{key[0]}::{item.job_name.lower()}"
        if self.update_attached_external_run(job_key, job, item):
            return False

        now = time.monotonic()
        solver_pids = self.external_solver_pids_from_scan(job)
        rss_bytes = int(item.rss_bytes or job.get("rss_bytes") or 0)
        submitted_at = self.infer_scanned_job_submitted_at(work_dir, item.job_name, job)
        process_names = " ".join(job.get("process_names") or []).lower()
        solver_kind = ""
        if "explicit" in process_names:
            solver_kind = "explicit"
        elif "standard" in process_names:
            solver_kind = "standard"
        attach_source_text = "external" if item.is_external else "restored"

        run = RuntimeRecord.for_attached(
            key=job_key,
            item=item,
            work_dir=work_dir,
            source_label=attach_source_text,
            solver_pids=solver_pids,
            solver_kind=solver_kind,
            rss_bytes=rss_bytes,
            submitted_at=submitted_at,
            monotonic_now=now,
        )

        self.host.run_records[job_key] = run
        self.host.active_runs[job_key] = run
        item.status = STATUS_RUNNING
        item.active_job_key = job_key
        item.effective_work_dir = work_dir
        if not item.message:
            item.message = f"{attach_source_text.capitalize()} job attached to runtime monitor"

        if not self.host.runtime_controller.start_external_monitor(job_key=job_key, run=run):
            self.host.active_runs.pop(job_key, None)
            self.host.run_records.pop(job_key, None)
            item.active_job_key = ""
            return False

        self.host.refresh_job_selector()
        self.host.show_runtime_panel()
        if not self.host.current_job_key or self.host.current_job_key not in self.host.active_runs:
            self.host.select_run(job_key)
        self.host.update_process_buttons(True)
        self.host.append_history(
            f"{attach_source_text.capitalize()} job attached to runtime monitor: {item.job_name}",
            operation=operation or f"external-scan:{job_work_dir}",
        )
        return True

    def handle_terminal_records(self, terminal_external_records: list[dict]) -> None:
        for record in terminal_external_records:
            target_item = record.get("item")
            key = record.get("key")
            if target_item is None or not key:
                continue
            ext_key = f"{key[0]}::{target_item.job_name.lower()}"
            self.host.memory_adapter.finalize_job(ext_key)
            if target_item.status == STATUS_UNKNOWN:
                target_item.status = STATUS_PENDING_CONFIRM
                target_item.message = "程序重启后未发现运行进程或终态证据，请人工确认"
            if not target_item.message:
                target_item.message = f"外部作业状态：{target_item.status}"

    def append_debug_records(self, debug_records: list[dict]) -> None:
        for record in debug_records:
            self.host.append_external_scan_debug_log(
                job_name=record["job_name"],
                job_work_dir=record["job_work_dir"],
                job=record["job"],
                runtime_status=record["runtime_status"],
                runtime_message=record["runtime_message"],
            )


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
    terminal_external_records: list[dict] = []
    debug_records: list[dict] = []
    memory_records: list[dict] = []
    scanned_keys: set[tuple[str, str]] = set()

    for job in jobs:
        job_name = job.get("job_name", "")
        job_work_dir = job.get("work_dir") or work_dir
        if not job_name:
            continue

        key = managed_job_key(
            job_work_dir,
            job_name,
        )
        scanned_keys.add(key)
        matched = queue_item_by_key.get(key)
        runtime_status = job.get("runtime_status") or STATUS_UNKNOWN
        runtime_message = job.get("runtime_message") or "外部作业状态待确认"
        previous_status = matched.status if matched is not None else ""

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
                restored_active_without_run = previous_status in ACTIVE_STATUSES and not matched.active_job_key
                track_scan_runtime = (
                    matched.is_external
                    or previous_status == STATUS_UNKNOWN
                    or restored_active_without_run
                )
                if track_scan_runtime and matched.status in ACTIVE_STATUSES:
                    active_external_items.append(matched)
                if (
                    track_scan_runtime
                    and (previous_status in ACTIVE_STATUSES or previous_status == STATUS_UNKNOWN)
                    and matched.status in TERMINAL_STATUSES
                ):
                    terminal_external_records.append(
                        {
                            "item": matched,
                            "job": job,
                            "key": key,
                            "work_dir": job_work_dir,
                            "previous_status": previous_status,
                            "status": matched.status,
                        }
                    )
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
            if matched.is_external:
                matched.external_work_dir = job_work_dir
            matched.effective_work_dir = job_work_dir
            target_item = matched
            updated += 1

        updated_items.append(target_item)
        restored_active_without_run = previous_status in ACTIVE_STATUSES and not target_item.active_job_key
        track_scan_runtime = (
            target_item.is_external
            or previous_status == STATUS_UNKNOWN
            or restored_active_without_run
        )
        if track_scan_runtime:
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
            if (
                (previous_status in ACTIVE_STATUSES or previous_status == STATUS_UNKNOWN)
                and target_item.status in TERMINAL_STATUSES
            ):
                terminal_external_records.append(
                    {
                        "item": target_item,
                        "job": job,
                        "key": key,
                        "work_dir": job_work_dir,
                        "previous_status": previous_status,
                        "status": target_item.status,
                    }
                )

        debug_records.append(
            {
                "job_name": job_name,
                "job_work_dir": job_work_dir,
                "job": job,
                "runtime_status": runtime_status,
                "runtime_message": runtime_message,
            }
        )

    restored_updates, restored_terminal_records = resolve_unmatched_restored_unknown_items(
        queue_items=queue_items,
        scanned_keys=scanned_keys,
    )
    if restored_updates:
        status_only_updates += len(restored_updates)
        updated_items.extend(restored_updates)
    terminal_external_records.extend(restored_terminal_records)

    return {
        "added": added,
        "updated": updated,
        "status_only_updates": status_only_updates,
        "updated_items": updated_items,
        "active_external_items": active_external_items,
        "terminal_external_records": terminal_external_records,
        "debug_records": debug_records,
        "memory_records": memory_records,
    }
