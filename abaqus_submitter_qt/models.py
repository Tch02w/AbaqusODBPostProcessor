import ctypes
from dataclasses import dataclass, field
from uuid import uuid4

from .constants import STATUS_PENDING_CONFIRM


@dataclass
class QueueItem:
    item_id: str = field(default_factory=lambda: uuid4().hex)
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
    fortran_path: str = ""
    cores: int = 1
    memory: str = ""
    interactive: bool = False
    datacheck_only: bool = False
    complete_notify: bool = False
    source_inp_path: str = ""
    calculation_work_dir: str = ""
    archive_dir: str = ""
    archive_after_complete: bool = False
    cleanup_after_archive: bool = False
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


class MemoryStatusEx(ctypes.Structure):
    """Windows GlobalMemoryStatusEx structure reused across queue checks."""

    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


__all__ = ["QueueItem", "MemoryStatusEx"]
