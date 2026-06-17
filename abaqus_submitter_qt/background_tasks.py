"""Serial Qt background tasks for workspace preparation and archive moves."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .archive_move import (
    ArchiveMoveBlockedError,
    ArchiveMovePlan,
    execute_archive_move,
)
from .qt_compat import QtCore, Signal, Slot
from .workspace_prepare import WorkspacePreparePlan, execute_workspace_prepare


@dataclass(frozen=True)
class WorkspacePrepareTask:
    task_id: str
    item_id: str
    plan: WorkspacePreparePlan


class WorkspacePrepareWorker(QtCore.QObject):
    """Run one workspace preparation plan outside the UI thread."""

    succeeded = Signal(object, object)
    failed = Signal(object, str, str)
    done = Signal()

    def __init__(self, task: WorkspacePrepareTask):
        super().__init__()
        self.task = task

    @Slot()
    def run(self) -> None:
        try:
            result = execute_workspace_prepare(self.task.plan)
        except Exception as exc:
            self.failed.emit(self.task, str(exc), str(getattr(exc, "copied_inp_path", "")))
        else:
            self.succeeded.emit(self.task, result)
        finally:
            self.done.emit()


@dataclass(frozen=True)
class ArchiveMoveTask:
    task_id: str
    run_key: str
    job_name: str
    plan: ArchiveMovePlan
    queue_item_id: str = ""


class ArchiveMoveWorker(QtCore.QObject):
    """Move one completed calculation directory outside the UI thread."""

    succeeded = Signal(object, object)
    blocked = Signal(object, str)
    failed = Signal(object, str)
    done = Signal()

    def __init__(self, task: ArchiveMoveTask):
        super().__init__()
        self.task = task

    @Slot()
    def run(self) -> None:
        try:
            result = execute_archive_move(self.task.plan)
        except ArchiveMoveBlockedError as exc:
            self.blocked.emit(self.task, str(exc))
        except Exception as exc:
            self.failed.emit(self.task, str(exc))
        else:
            self.succeeded.emit(self.task, result)
        finally:
            self.done.emit()


class SerialTaskService(QtCore.QObject):
    """Own one pending queue and run at most one worker thread at a time."""

    idle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending_tasks: deque[object] = deque()
        self._active_task = None
        self._active_thread: QtCore.QThread | None = None
        self._active_worker: QtCore.QObject | None = None
        self._closing = False

    def enqueue(self, task) -> bool:  # noqa: ANN001
        if self._closing:
            return False
        self._pending_tasks.append(task)
        self._start_next_if_idle()
        return True

    def is_busy(self) -> bool:
        return self._active_thread is not None or bool(self._pending_tasks)

    def cancel_pending(self, task_id: str) -> bool:
        for task in list(self._pending_tasks):
            if getattr(task, "task_id", "") != task_id:
                continue
            self._pending_tasks.remove(task)
            return True
        return False

    def shutdown(self) -> None:
        self._closing = True
        self._pending_tasks.clear()

    def _create_worker(self, task) -> QtCore.QObject:  # noqa: ANN001
        raise NotImplementedError

    def _connect_worker_signals(self, worker: QtCore.QObject) -> None:
        raise NotImplementedError

    def _start_next_if_idle(self) -> None:
        if self._closing or self._active_thread is not None or not self._pending_tasks:
            return

        task = self._pending_tasks.popleft()
        thread_parent = QtCore.QCoreApplication.instance()
        thread = QtCore.QThread(thread_parent)
        worker = self._create_worker(task)
        worker.moveToThread(thread)

        self._active_task = task
        self._active_thread = thread
        self._active_worker = worker

        thread.started.connect(worker.run)
        self._connect_worker_signals(worker)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    @Slot()
    def _on_thread_finished(self) -> None:
        self._active_task = None
        self._active_thread = None
        self._active_worker = None
        if self._closing:
            self.idle.emit()
            return
        if self._pending_tasks:
            self._start_next_if_idle()
            return
        self.idle.emit()


class WorkspacePrepareService(SerialTaskService):
    """Serial QThread service for workspace preparation."""

    succeeded = Signal(object, object)
    failed = Signal(object, str, str)

    def _create_worker(self, task: WorkspacePrepareTask) -> WorkspacePrepareWorker:
        return WorkspacePrepareWorker(task)

    def _connect_worker_signals(self, worker: WorkspacePrepareWorker) -> None:
        worker.succeeded.connect(self._on_worker_succeeded)
        worker.failed.connect(self._on_worker_failed)

    @Slot(object, object)
    def _on_worker_succeeded(self, task: WorkspacePrepareTask, result) -> None:
        if not self._closing:
            self.succeeded.emit(task, result)

    @Slot(object, str, str)
    def _on_worker_failed(self, task: WorkspacePrepareTask, message: str, copied_inp_path: str) -> None:
        if not self._closing:
            self.failed.emit(task, message, copied_inp_path)


class ArchiveMoveService(SerialTaskService):
    """Serial QThread service for archive directory moves."""

    succeeded = Signal(object, object)
    blocked = Signal(object, str)
    failed = Signal(object, str)

    def _create_worker(self, task: ArchiveMoveTask) -> ArchiveMoveWorker:
        return ArchiveMoveWorker(task)

    def _connect_worker_signals(self, worker: ArchiveMoveWorker) -> None:
        worker.succeeded.connect(self._on_worker_succeeded)
        worker.blocked.connect(self._on_worker_blocked)
        worker.failed.connect(self._on_worker_failed)

    @Slot(object, object)
    def _on_worker_succeeded(self, task: ArchiveMoveTask, result) -> None:
        if not self._closing:
            self.succeeded.emit(task, result)

    @Slot(object, str)
    def _on_worker_blocked(self, task: ArchiveMoveTask, message: str) -> None:
        if not self._closing:
            self.blocked.emit(task, message)

    @Slot(object, str)
    def _on_worker_failed(self, task: ArchiveMoveTask, message: str) -> None:
        if not self._closing:
            self.failed.emit(task, message)


__all__ = [
    "ArchiveMoveService",
    "ArchiveMoveTask",
    "ArchiveMoveWorker",
    "SerialTaskService",
    "WorkspacePrepareService",
    "WorkspacePrepareTask",
    "WorkspacePrepareWorker",
]
