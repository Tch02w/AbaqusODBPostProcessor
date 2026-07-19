from dataclasses import dataclass, field
from uuid import uuid4

from .constants import STATUS_PENDING_CONFIRM


@dataclass
class QueueItem:
    item_id: str = field(default_factory=lambda: uuid4().hex)
    job_id: str = ""
    inp_path: str = ""
    job_name: str = ""
    source: str = ""
    status: str = STATUS_PENDING_CONFIRM
    selected: bool = True
    valid: bool = True
    message: str = "可加入"
    run_mode: str = "normal"
    oldjob_name: str = ""
    oldjob_dir: str = ""
    oldjob_path: str = ""
    resolved_oldjob_arg: str = ""
    resolved_oldjob_source: str = ""
    resolved_oldjob_dir: str = ""
    resolved_oldjob_reference_key: str = ""
    fortran_path: str = ""
    cores: int = 0
    memory: str = ""
    interactive: bool = False
    datacheck_only: bool = False
    complete_notify: bool = False
    source_inp_path: str = ""
    calculation_root_dir: str = ""
    effective_work_dir: str = ""
    archive_dir: str = ""
    archive_after_complete: bool = False
    cleanup_after_archive: bool = False
    archive_destination: str = ""
    archive_status: str = ""
    archive_error: str = ""
    active_job_key: str = ""
    elapsed: str = ""
    job_type: str = ""
    is_external: bool = False
    external_work_dir: str = ""
    pids: list = field(default_factory=list)
    pid_create_times: dict = field(default_factory=dict)
    rss_bytes: int = 0
    priority: int = 0
    held: bool = False
    scheduler_state: str = ""
    pending_reason: str = ""
    attempt_id: str = ""
    dependency_job_ids: list[str] = field(default_factory=list)
    submitted_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.job_id:
            self.job_id = self.item_id


__all__ = ["QueueItem"]
