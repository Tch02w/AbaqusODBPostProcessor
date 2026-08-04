"""Qt adapter for UI-independent Abaqus memory monitoring."""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass
from typing import Iterable, Mapping

try:
    import psutil
except ImportError:  # pragma: no cover - depends on user environment
    psutil = None

from .memory_monitor import MemoryMonitorService, format_memory_size
from .process_observation import ProcessObservationService
from .process_scanner import build_abaqus_job_memory_usage, get_abaqus_job_memory_usage
from .qt_compat import QtCore, Signal, Slot


class MemoryStatusEx(ctypes.Structure):
    """Windows GlobalMemoryStatusEx structure used by this adapter."""

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


@dataclass(frozen=True)
class TrackedMemoryJob:
    """Small serializable description passed to the scan worker."""

    job_key: str
    job_name: str
    work_dir: str = ""


class MemoryScanWorker(QtCore.QObject):
    """Run one global Abaqus memory scan away from the UI thread."""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        tracked_jobs: Iterable[TrackedMemoryJob | dict],
        process_rows: list[dict] | None = None,
    ):
        super().__init__()
        self.tracked_jobs = list(tracked_jobs)
        self.process_rows = process_rows

    @Slot()
    def run(self) -> None:
        try:
            usage_by_job = (
                build_abaqus_job_memory_usage(self.process_rows)
                if self.process_rows is not None
                else get_abaqus_job_memory_usage(force=True)
            )
            self.finished.emit(
                {
                    "usage_by_job": usage_by_job,
                    "system_memory": get_system_memory_info(),
                    "tracked_jobs": self.tracked_jobs,
                }
            )
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            self.failed.emit(str(exc))


class QtMemoryMonitorAdapter(QtCore.QObject):
    """Coordinate QTimer/QThread with :class:`MemoryMonitorService`."""

    scanFinished = Signal(object)
    scanFailed = Signal(str)
    memorySnapshotApplied = Signal(object)
    memoryScanFailed = Signal(str)
    memorySlotEstimateChanged = Signal(object)

    def __init__(
        self,
        service: MemoryMonitorService | None = None,
        process_observation: ProcessObservationService | None = None,
        parent: QtCore.QObject | None = None,
    ):
        super().__init__(parent)
        self.service = service or MemoryMonitorService()
        self.process_observation = process_observation
        self.memory_scan_running = False
        self.closing = False
        self.last_slot_signature: tuple[int, int, int] | None = None
        self._thread: QtCore.QThread | None = None
        self._worker: MemoryScanWorker | None = None

        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.schedule_memory_scan)

    def start(self) -> None:
        if self.closing:
            return
        delay = self.service.get_next_delay_ms()
        if delay is not None:
            self.timer.start(delay)

    def stop(self) -> None:
        self.closing = True
        self.timer.stop()
        if self.process_observation is not None:
            self.process_observation.set_consumer_active("memory", False)
        self.memory_scan_running = False
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(1500)
        elif thread is not None:
            self._cleanup_thread()

    def schedule_memory_scan(self) -> None:
        if self.closing:
            return
        if self.memory_scan_running:
            self.start()
            return

        due_states = self.service.get_due_jobs()
        if not due_states:
            self.start()
            return

        self.memory_scan_running = True
        tracked_jobs = []
        for state in due_states:
            tracked_jobs.append(
                TrackedMemoryJob(
                    job_key=state.job_key,
                    job_name=state.job_name,
                    work_dir=state.work_dir,
                )
            )

        self._thread = QtCore.QThread(self)
        process_rows = None
        if self.process_observation is not None:
            process_rows = self.process_observation.latest_snapshot()
        self._worker = MemoryScanWorker(tracked_jobs, process_rows=process_rows)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._handle_finished)
        self._worker.failed.connect(self._handle_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    @Slot(object)
    def _handle_finished(self, payload: object) -> None:
        self.memory_scan_running = False
        self.scanFinished.emit(payload)
        self.start()

    @Slot(str)
    def _handle_failed(self, message: str) -> None:
        self.memory_scan_running = False
        self.scanFailed.emit(message)
        self.memoryScanFailed.emit(message)
        self.start()

    @Slot()
    def _cleanup_thread(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    def register_job(
        self,
        *,
        job_key: str,
        job_name: str,
        work_dir: str,
        monitor_mode: str = "learning",
    ) -> None:
        self.service.register_job(
            job_key=job_key,
            job_name=job_name,
            work_dir=work_dir,
            monitor_mode=monitor_mode,
        )

    def activate_job(self, job_key: str) -> None:
        self.service.activate_job(job_key)
        if self.process_observation is not None:
            self.process_observation.set_consumer_active("memory", True)
        self.start()

    def finalize_job(self, job_key: str) -> None:
        self.service.finalize_job(job_key)
        if self.process_observation is not None:
            has_active_jobs = any(
                state.monitor_active and not state.finalized
                for state in self.service.tracking_states.values()
            )
            self.process_observation.set_consumer_active("memory", has_active_jobs)
        self.start()

    def apply_scan_payload(
        self,
        payload: object,
        *,
        step_by_job_key: Mapping[str, str] | None = None,
        active_job_names: set[str] | None = None,
    ) -> dict:
        """Apply one worker result to the memory service in the Qt thread."""
        if not isinstance(payload, dict):
            return {}

        usage_by_job = payload.get("usage_by_job") or {}
        system_memory = payload.get("system_memory") or {}
        due_states = self.service.get_due_jobs()
        due_job_keys = [state.job_key for state in due_states]
        events = self.service.apply_usage_snapshot(
            due_job_keys=due_job_keys,
            usage_by_job=usage_by_job,
            step_by_job_key=step_by_job_key or {},
            now=time.monotonic(),
        )
        updated_job_keys = {event.get("job_key", "") for event in events if event.get("job_key")}
        updated_jobs = []
        for job_key in sorted(updated_job_keys):
            state = self.service.tracking_states.get(job_key)
            if state is None:
                continue
            estimate = self.service.job_estimates.get(state.job_name)
            updated_jobs.append(
                {
                    "job_key": job_key,
                    "job_name": state.job_name,
                    "rss_bytes": int(state.memory_samples[-1]) if state.memory_samples else 0,
                    "peak_memory": int(state.memory_peak or 0),
                    "estimated_memory": int((estimate.estimated_memory if estimate else 0) or 0),
                    "monitor_mode": state.monitor_mode,
                    "stable": bool(estimate.stable if estimate else False),
                }
            )

        available_memory = int(system_memory.get("available") or 0)
        slot_estimate = self.service.estimate_available_slots(
            available_memory=available_memory,
            usage_by_job=usage_by_job,
            active_job_names=active_job_names or set(),
        )
        result = {
            "updated_jobs": updated_jobs,
            "updated_job_keys": updated_job_keys,
            "slot_estimate": slot_estimate,
            "events": events,
            "usage_by_job": usage_by_job,
            "system_memory": system_memory,
        }
        self.memorySnapshotApplied.emit(result)
        signature = (
            int(slot_estimate.slots),
            int(slot_estimate.per_job_memory),
            int(slot_estimate.current_abaqus_memory),
        )
        if signature != self.last_slot_signature:
            self.last_slot_signature = signature
            self.memorySlotEstimateChanged.emit(slot_estimate)
        return result

    @staticmethod
    def format_memory(size_bytes: int) -> str:
        return format_memory_size(size_bytes)


def get_system_memory_info() -> dict[str, int]:
    """Return total/available physical memory without touching Qt objects."""
    if psutil is not None:
        try:
            memory = psutil.virtual_memory()
            return {"total": int(memory.total), "available": int(memory.available)}
        except Exception:
            pass

    if hasattr(ctypes, "windll"):
        try:
            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return {"total": int(status.ullTotalPhys), "available": int(status.ullAvailPhys)}
        except Exception:
            pass

    return {"total": 0, "available": 0}
