"""全局进程快照生命周期、索引与消费者协调。"""

from __future__ import annotations

import atexit
import time
from dataclasses import dataclass

from .process_scanner import get_psutil_process_snapshot, is_active_solver_process
from .qt_compat import QtCore, Signal, Slot


PROCESS_SCAN_ACTIVE_INTERVAL_MS = 8000
PROCESS_SCAN_IDLE_INTERVAL_MS = 10000
PROCESS_SNAPSHOT_MAX_AGE_SECONDS = 20.0
_ACTIVE_PROCESS_SCAN_THREADS: set[QtCore.QThread] = set()
_ACTIVE_PROCESS_SCAN_WORKERS: set[QtCore.QObject] = set()


def _shutdown_process_scan_threads() -> None:
    """确保解释器退出前不遗留仍在运行的 QThread。"""
    for thread in tuple(_ACTIVE_PROCESS_SCAN_THREADS):
        try:
            if thread.isRunning():
                thread.quit()
                thread.wait(5000)
        except RuntimeError:
            pass


atexit.register(_shutdown_process_scan_threads)


@dataclass(frozen=True)
class ProcessSnapshot:
    """一次进程观察及其共享索引。"""

    rows: list[dict]
    by_pid: dict[int, dict]
    solver_rows: list[dict]
    observed_at: float

    def bundle(self) -> tuple[list[dict], dict[int, dict], list[dict]]:
        return self.rows, self.by_pid, self.solver_rows


def build_process_snapshot(rows: object, *, observed_at: float | None = None) -> ProcessSnapshot:
    """一次构建所有消费者需要的索引。"""
    row_list = list(rows or [])
    by_pid: dict[int, dict] = {}
    solver_rows: list[dict] = []
    for row in row_list:
        try:
            by_pid[int(row.get("ProcessId") or 0)] = row
        except (TypeError, ValueError):
            pass
        if is_active_solver_process(
            row.get("Name") or "",
            row.get("CommandLine") or "",
        ):
            solver_rows.append(row)
    return ProcessSnapshot(
        rows=row_list,
        by_pid=by_pid,
        solver_rows=solver_rows,
        observed_at=time.monotonic() if observed_at is None else observed_at,
    )


class ProcessObservationWorker(QtCore.QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    done = Signal()

    @Slot()
    def run(self) -> None:
        try:
            rows = get_psutil_process_snapshot(force=True, include_details=True)
        except Exception as exc:  # pragma: no cover - defensive for psutil edge cases
            try:
                self.failed.emit(str(exc))
            except RuntimeError:
                pass
        else:
            try:
                self.succeeded.emit(rows)
            except RuntimeError:
                pass
        finally:
            try:
                self.done.emit()
            except RuntimeError:
                pass


class ProcessObservationService(QtCore.QObject):
    """维护单一进程扫描循环，并向多个消费者提供共享快照。"""

    snapshotReady = Signal(object)
    scanFailed = Signal(str)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(PROCESS_SCAN_IDLE_INTERVAL_MS)
        self._timer.timeout.connect(self.request_scan)
        self._active_consumers: set[str] = set()
        self._closing = False
        self._thread: QtCore.QThread | None = None
        self._worker: ProcessObservationWorker | None = None
        self._latest: ProcessSnapshot | None = None

    def set_consumer_active(self, consumer: str, active: bool) -> None:
        if self._closing:
            return
        if active:
            self._active_consumers.add(consumer)
        else:
            self._active_consumers.discard(consumer)
        if self._active_consumers:
            self._timer.setInterval(PROCESS_SCAN_ACTIVE_INTERVAL_MS)
            if not self._timer.isActive():
                self._timer.start()
            self.request_scan()
        else:
            self._timer.stop()

    def set_active(self, active: bool) -> None:
        """提供运行监控所需的进程快照 Interface。"""
        self.set_consumer_active("runtime", active)

    def latest_indexed_snapshot(
        self,
        *,
        max_age: float = PROCESS_SNAPSHOT_MAX_AGE_SECONDS,
    ) -> ProcessSnapshot | None:
        latest = self._latest
        if latest is None or time.monotonic() - latest.observed_at > max_age:
            return None
        return latest

    def latest_snapshot(self, *, max_age: float = PROCESS_SNAPSHOT_MAX_AGE_SECONDS) -> list[dict] | None:
        latest = self.latest_indexed_snapshot(max_age=max_age)
        return latest.rows if latest is not None else None

    def latest_snapshot_bundle(
        self,
        *,
        max_age: float = PROCESS_SNAPSHOT_MAX_AGE_SECONDS,
    ) -> tuple[list[dict], dict[int, dict], list[dict]] | None:
        latest = self.latest_indexed_snapshot(max_age=max_age)
        return latest.bundle() if latest is not None else None

    def request_scan(self) -> None:
        if self._closing or self._thread is not None:
            return
        thread = QtCore.QThread()
        worker = ProcessObservationWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self.on_snapshot_ready)
        worker.failed.connect(self.on_scan_failed)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(self.on_thread_finished)
        thread.finished.connect(lambda thread=thread: _ACTIVE_PROCESS_SCAN_THREADS.discard(thread))
        thread.finished.connect(lambda worker=worker: _ACTIVE_PROCESS_SCAN_WORKERS.discard(worker))
        thread.finished.connect(thread.deleteLater)
        _ACTIVE_PROCESS_SCAN_THREADS.add(thread)
        _ACTIVE_PROCESS_SCAN_WORKERS.add(worker)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def on_snapshot_ready(self, rows: object) -> None:
        if self._closing:
            return
        self._latest = build_process_snapshot(rows)
        self.snapshotReady.emit(self._latest.rows)

    @Slot(str)
    def on_scan_failed(self, message: str) -> None:
        if not self._closing:
            self.scanFailed.emit(message)

    @Slot()
    def on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        if self._active_consumers and not self._closing and not self._timer.isActive():
            self._timer.start()

    def shutdown(self) -> None:
        self._closing = True
        self._active_consumers.clear()
        self._timer.stop()
        thread = self._thread
        if thread is not None:
            try:
                thread.quit()
                if thread.isRunning():
                    thread.wait(5000)
            except RuntimeError:
                pass
