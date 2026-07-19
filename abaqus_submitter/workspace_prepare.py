"""Calculation workspace preparation plans, file I/O, and Qt background service."""

from __future__ import annotations

import os
import shutil
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .qt_compat import QtCore, Signal, Slot


RESTART_DEPENDENCY_EXTENSIONS = (
    ".odb",
    ".res",
    ".stt",
    ".sim",
    ".mdl",
    ".prt",
    ".sta",
    ".msg",
    ".dat",
    ".log",
    ".com",
)


@dataclass(frozen=True)
class WorkspacePreparePlan:
    enabled: bool
    job_name: str
    source_inp_path: str
    target_work_dir: str = ""
    oldjob_name: str = ""
    oldjob_source_dir: str = ""
    copy_oldjob_dependencies: bool = True


@dataclass(frozen=True)
class WorkspacePrepareResult:
    prepared_inp_path: str
    prepared_work_dir: str = ""
    copied_inp_path: str = ""
    copied_oldjob_files: tuple[str, ...] = ()


def restart_dependency_target_is_current(source: Path, target: Path) -> bool:
    if not target.exists() or not target.is_file():
        return False
    try:
        if source.samefile(target):
            return True
    except OSError:
        pass
    try:
        source_stat = source.stat()
        target_stat = target.stat()
    except OSError:
        return False
    return source_stat.st_size == target_stat.st_size and source_stat.st_mtime_ns == target_stat.st_mtime_ns


def prepare_restart_dependency_file(source: Path, target: Path) -> bool:
    if restart_dependency_target_is_current(source, target):
        return False
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return True


def copy_restart_dependency_files(source_dir: Path, target_dir: Path, oldjob_name: str) -> list[Path]:
    """Copy restart dependency files by exact stem match only."""
    if not oldjob_name or not source_dir:
        return []
    if not source_dir.exists():
        return []

    copied = []
    for extension in RESTART_DEPENDENCY_EXTENSIONS:
        source = source_dir / f"{oldjob_name}{extension}"
        if not source.exists() or not source.is_file():
            continue
        target = target_dir / source.name
        if source.resolve() == target.resolve():
            continue
        if prepare_restart_dependency_file(source, target):
            copied.append(target)
    return copied


def execute_workspace_prepare(plan: WorkspacePreparePlan) -> WorkspacePrepareResult:
    """Execute the file I/O part of preparing a calculation workspace."""
    if not plan.enabled:
        return WorkspacePrepareResult(prepared_inp_path=plan.source_inp_path)

    source_inp = Path(plan.source_inp_path)
    calc_dir = Path(plan.target_work_dir)
    calc_dir.mkdir(parents=True, exist_ok=True)

    copied_inp = calc_dir / source_inp.name
    copied_inp_done = False
    try:
        shutil.copy2(source_inp, copied_inp)
        copied_inp_done = True
        copied_oldjob_files = []
        if plan.copy_oldjob_dependencies:
            copied_oldjob_files = copy_restart_dependency_files(
                Path(plan.oldjob_source_dir),
                calc_dir,
                plan.oldjob_name,
            )
    except OSError as exc:
        if copied_inp_done:
            setattr(exc, "copied_inp_path", str(copied_inp))
        raise

    return WorkspacePrepareResult(
        prepared_inp_path=str(copied_inp),
        prepared_work_dir=str(calc_dir),
        copied_inp_path=str(copied_inp),
        copied_oldjob_files=tuple(str(path) for path in copied_oldjob_files),
    )


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
