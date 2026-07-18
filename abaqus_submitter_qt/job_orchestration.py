"""作业编排与界面宿主之间的显式接口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .models import QueueItem


class JobOrchestrationHost(Protocol):
    """JobController 所需的最小宿主能力，取代运行时反射代理。"""

    queue_items: list[QueueItem]
    active_runs: dict[str, dict]
    run_records: dict[str, dict]
    queue_active: bool
    queue_stop_requested: bool
    current_job_key: str
    current_job_name: str
    current_work_dir: str
    command_preview: str
    deferred_archive_runs: dict[str, dict]
    _archive_move_contexts: dict[str, dict]
    _archive_move_reserved_keys: set[tuple[str, str]]
    _workspace_prepare_contexts: dict[str, dict]
    _closing: bool
    archive_move_service: Any
    workspace_prepare_service: Any
    memory_adapter: Any
    runtime_controller: Any
    restart_dependencies: Any
    cpus_spin: Any
    max_parallel_spin: Any
    pause_btn: Any
    status_label: Any

    def append_history(self, message: str, **kwargs: Any) -> None: ...

    def update_queue_status_label(self) -> None: ...

    def refresh_visible_queue_manager(self, *args: Any) -> None: ...

    def estimate_effective_available_slots(self) -> dict: ...

    def refresh_queue_dependencies(self) -> None: ...

    def process_deferred_archives(self) -> None: ...

    def request_dispatch_queue(self) -> None: ...

    def ask_existing_odb_action(self, job_name: str, odb_path: Path, queue_mode: bool) -> str: ...

    def submit_requires_restart_dependency(self, options: Any, queue_item: QueueItem | None) -> bool: ...

    def resolve_oldjob_source_dir(self, options: Any, queue_item: QueueItem | None) -> str: ...

    def archive_move_conflict_message(self, options: Any, queue_item: QueueItem | None) -> str: ...

    def refresh_job_selector(self) -> None: ...

    def show_runtime_panel(self) -> None: ...

    def select_run(self, job_key: str) -> None: ...

    def update_process_buttons(self, is_running: bool) -> None: ...

    def selected_job_key(self) -> str: ...

    def refresh_selected_run_status(self, job_key: str) -> None: ...

    def refresh_job_stats(self) -> None: ...

    def inspect_finished_job(self, job_key: str) -> tuple[str, str]: ...

    def notify_job_finished(self, run: dict, status: str, detail: str = "") -> None: ...
