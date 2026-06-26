"""SSD work-directory preparation, result handling, and archive coordination."""

from __future__ import annotations

import shutil
import time
import os
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .command import SubmitOptions, derive_oldjob_name
from .models import QueueItem
from .diagnostics import hang_probe_log
from .qt_compat import QtCore, Signal, Slot
from .queue_scheduler import unfinished_restart_dependents
from .workspace_prepare import (
    RESTART_DEPENDENCY_EXTENSIONS,
    SerialTaskService,
    WorkspacePreparePlan,
    WorkspacePrepareResult,
    copy_restart_dependency_files,
    execute_workspace_prepare,
)


ARCHIVE_EXTENSIONS = (
    ".inp",
    ".odb",
    ".sta",
    ".msg",
    ".dat",
    ".log",
    ".com",
    ".prt",
    ".sim",
    ".res",
    ".mdl",
    ".stt",
    ".fil",
    ".pac",
)

EXISTING_RESULT_EXTENSIONS = (".odb", ".sta")


@dataclass(frozen=True)
class ArchiveMovePlan:
    source_work_dir: str
    archive_root: str
    job_name: str


@dataclass(frozen=True)
class ArchiveMoveResult:
    source_work_dir: str
    archive_destination: str


class ArchiveMoveBlockedError(RuntimeError):
    """Raised when a calculation directory is not safe to move yet."""


def normalized_path(path: str) -> str:
    if not str(path or "").strip():
        return ""
    return os.path.normcase(os.path.abspath(str(Path(path).resolve(strict=False))))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolved(path: str | Path) -> Path:
    return Path(path).resolve(strict=False)


def unique_archive_destination(
    archive_root: str,
    job_name: str,
    *,
    now: datetime | None = None,
) -> Path:
    root = Path(archive_root)
    destination = root / job_name
    if not destination.exists():
        return destination

    time_tag = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    destination = root / f"{job_name}_{time_tag}"
    if not destination.exists():
        return destination

    index = 1
    while True:
        candidate = root / f"{job_name}_{time_tag}_{index}"
        if not candidate.exists():
            return candidate
        index += 1


def validate_archive_move_plan(plan: ArchiveMovePlan) -> None:
    source = Path(plan.source_work_dir)
    if not str(plan.source_work_dir or "").strip():
        raise ValueError("源计算目录不能为空。")
    if not source.exists():
        raise ValueError(f"源计算目录不存在：{source}")
    if not source.is_dir():
        raise ValueError(f"源路径不是目录：{source}")
    if not str(plan.archive_root or "").strip():
        raise ValueError("归档根目录不能为空。")
    archive_root = Path(plan.archive_root)
    if archive_root.exists() and not archive_root.is_dir():
        raise ValueError(f"归档根路径不是目录：{archive_root}")
    if not str(plan.job_name or "").strip():
        raise ValueError("作业名不能为空。")

    source_resolved = _resolved(source)
    archive_root_resolved = _resolved(plan.archive_root)
    if normalized_path(str(source_resolved)) == normalized_path(str(archive_root_resolved)):
        raise ValueError("源计算目录不能与归档根目录相同。")
    if _is_relative_to(archive_root_resolved, source_resolved):
        raise ValueError("归档根目录不能位于源计算目录内部。")


def validate_archive_destination(source_work_dir: str, destination: str | Path) -> None:
    source_resolved = _resolved(source_work_dir)
    destination_resolved = _resolved(destination)
    if normalized_path(str(source_resolved)) == normalized_path(str(destination_resolved)):
        raise ValueError("源计算目录不能与归档目标目录相同。")
    if _is_relative_to(destination_resolved, source_resolved):
        raise ValueError("归档目标目录不能位于源计算目录内部。")
    if _is_relative_to(source_resolved, destination_resolved):
        raise ValueError("源计算目录不能位于归档目标目录内部。")


def get_archive_blocking_lck_file(source_work_dir: str, job_name: str) -> str:
    lck_path = Path(source_work_dir) / f"{job_name}.lck"
    return str(lck_path) if lck_path.exists() else ""


def execute_archive_move(plan: ArchiveMovePlan) -> ArchiveMoveResult:
    validate_archive_move_plan(plan)

    blocking_lck = get_archive_blocking_lck_file(plan.source_work_dir, plan.job_name)
    if blocking_lck:
        raise ArchiveMoveBlockedError(f"检测到 LCK 文件，暂不移动计算目录：{blocking_lck}")

    archive_root = Path(plan.archive_root)
    archive_root.mkdir(parents=True, exist_ok=True)
    destination = unique_archive_destination(plan.archive_root, plan.job_name)
    validate_archive_destination(plan.source_work_dir, destination)

    try:
        shutil.move(plan.source_work_dir, str(destination))
    except OSError as exc:
        raise OSError(
            f"移动计算目录失败：{exc}。跨盘移动失败时，源目录或目标目录可能存在部分文件，请人工检查。"
        ) from exc

    return ArchiveMoveResult(
        source_work_dir=plan.source_work_dir,
        archive_destination=str(destination),
    )


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


def get_existing_odb_file(work_dir: str | Path, job_name: str) -> Path | None:
    """Return the existing ODB path for a job, if Abaqus would overwrite it."""
    path = Path(work_dir) / f"{job_name}.odb"
    return path if path.exists() else None


def get_existing_lck_file(work_dir: str | Path, job_name: str) -> Path | None:
    """Return the existing LCK path for a job, if one is present."""
    path = Path(work_dir) / f"{job_name}.lck"
    return path if path.exists() else None


def _unique_backup_path(path: Path, time_tag: str) -> Path:
    backup = path.with_name(f"{path.stem}_bak_{time_tag}{path.suffix}")
    index = 1
    while backup.exists():
        backup = path.with_name(f"{path.stem}_bak_{time_tag}_{index}{path.suffix}")
        index += 1
    return backup


def _unique_sibling_path(path: Path) -> Path:
    candidate = path
    index = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        index += 1
    return candidate


def backup_existing_result_files(work_dir: str | Path, job_name: str) -> dict:
    """Back up the existing ODB and matching STA before a new submit."""
    probe_start = time.monotonic()
    root = Path(work_dir)
    time_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = {"odb": "", "sta": ""}
    hang_probe_log(
        "backup_existing_result_files.start",
        threshold=0.0,
        job_name=job_name,
        work_dir=root,
    )

    odb_path = root / f"{job_name}.odb"
    sta_path = root / f"{job_name}.sta"

    backup_odb_path: Path | None = None
    if odb_path.exists():
        backup_odb_path = _unique_backup_path(odb_path, time_tag)
        odb_path.rename(backup_odb_path)
        result["odb"] = str(backup_odb_path)

    if sta_path.exists():
        if backup_odb_path is not None:
            backup_sta_path = _unique_sibling_path(backup_odb_path.with_suffix(".sta"))
        else:
            backup_sta_path = _unique_backup_path(sta_path, time_tag)
        sta_path.rename(backup_sta_path)
        result["sta"] = str(backup_sta_path)

    moved_count = sum(1 for value in result.values() if value)
    hang_probe_log(
        "backup_existing_result_files.end",
        time.monotonic() - probe_start,
        threshold=0.0,
        job_name=job_name,
        work_dir=root,
        files=moved_count,
    )
    return result


def delete_existing_result_files(work_dir: str | Path, job_name: str) -> dict:
    """Delete the existing ODB and matching STA before a new submit."""
    probe_start = time.monotonic()
    root = Path(work_dir)
    result = {"odb": "", "sta": ""}
    hang_probe_log(
        "delete_existing_result_files.start",
        threshold=0.0,
        job_name=job_name,
        work_dir=root,
    )
    for extension in EXISTING_RESULT_EXTENSIONS:
        path = root / f"{job_name}{extension}"
        if not path.exists():
            continue
        path.unlink()
        result[extension.lstrip(".")] = str(path)
    deleted_count = sum(1 for value in result.values() if value)
    hang_probe_log(
        "delete_existing_result_files.end",
        time.monotonic() - probe_start,
        threshold=0.0,
        job_name=job_name,
        work_dir=root,
        files=deleted_count,
    )
    return result


def is_ssd_independent_work_dir(
    *,
    work_dir: str,
    calculation_root_dir: str,
    job_name: str,
    cleanup_after_archive: bool,
) -> bool:
    if not cleanup_after_archive:
        return False
    if not str(work_dir or "").strip():
        return False
    if not str(calculation_root_dir or "").strip():
        return False
    if not str(job_name or "").strip():
        return False
    expected_work_dir = Path(calculation_root_dir) / job_name
    return normalized_path(work_dir) == normalized_path(str(expected_work_dir))


def build_archive_move_plan(
    *,
    work_dir: str,
    archive_root: str,
    job_name: str,
) -> ArchiveMovePlan:
    return ArchiveMovePlan(
        source_work_dir=work_dir,
        archive_root=archive_root,
        job_name=job_name,
    )


def prepare_calculation_workspace(
    options: SubmitOptions,
    queue_item: QueueItem | None,
    oldjob_source_dir: str = "",
) -> tuple[SubmitOptions, dict]:
    """Copy an INP to the optional SSD work directory before submitting."""
    source_inp = Path(options.inp_file)
    workspace_info = build_workspace_info(options, queue_item)
    plan = build_workspace_prepare_plan(
        options,
        queue_item,
        oldjob_source_dir,
    )
    if not plan.enabled:
        return options, workspace_info

    try:
        result = execute_workspace_prepare(plan)
    except OSError as exc:
        if getattr(exc, "copied_inp_path", ""):
            queue_item.source_inp_path = str(source_inp)
        raise

    return apply_workspace_prepare_result(
        options,
        queue_item,
        workspace_info,
        result,
    )


def build_workspace_info(
    options: SubmitOptions,
    queue_item: QueueItem | None,
) -> dict:
    source_inp = Path(options.inp_file)
    workspace_info = {
        "source_inp_path": str(source_inp),
        "archive_dir": "",
        "cleanup_after_archive": False,
        "copied_inp_path": "",
        "copied_oldjob_files": [],
    }
    if queue_item is None:
        return workspace_info

    archive_dir = (queue_item.archive_dir or "").strip()
    workspace_info["archive_dir"] = archive_dir
    workspace_info["cleanup_after_archive"] = bool(queue_item.cleanup_after_archive)
    return workspace_info


def apply_workspace_prepare_result(
    options: SubmitOptions,
    queue_item: QueueItem | None,
    workspace_info: dict,
    result: WorkspacePrepareResult,
) -> tuple[SubmitOptions, dict]:
    source_inp = Path(workspace_info.get("source_inp_path") or options.inp_file)
    workspace_info = dict(workspace_info)
    if queue_item is not None:
        queue_item.source_inp_path = str(source_inp)

    workspace_info["copied_inp_path"] = result.copied_inp_path
    workspace_info["copied_oldjob_files"] = list(result.copied_oldjob_files)

    return replace(options, inp_file=result.prepared_inp_path), workspace_info


def build_workspace_prepare_plan(
    options: SubmitOptions,
    queue_item: QueueItem | None,
    oldjob_source_dir: str = "",
) -> WorkspacePreparePlan:
    source_inp = Path(options.inp_file)
    if queue_item is None:
        return WorkspacePreparePlan(
            enabled=False,
            job_name=options.job_name,
            source_inp_path=str(source_inp),
        )

    ssd_root = (queue_item.calculation_root_dir or "").strip()
    if not ssd_root:
        return WorkspacePreparePlan(
            enabled=False,
            job_name=options.job_name,
            source_inp_path=str(source_inp),
        )

    oldjob_name = derive_oldjob_name(options.oldjob_path)
    oldjob_source = ""
    if oldjob_name:
        oldjob_source = str(Path(oldjob_source_dir) if oldjob_source_dir else Path(options.oldjob_path).parent)

    return WorkspacePreparePlan(
        enabled=True,
        job_name=options.job_name,
        source_inp_path=str(source_inp),
        target_work_dir=str(Path(ssd_root) / options.job_name),
        oldjob_name=oldjob_name,
        oldjob_source_dir=oldjob_source,
    )


def archive_finished_job_files(run: dict) -> dict:
    """Move Abaqus result files from the calculation work dir to archive dir."""
    archive_root = (run.get("archive_dir") or "").strip()
    if not archive_root:
        return {"status": "", "message": "", "destination": "", "error": "", "moved": []}

    work_dir = Path(run["work_dir"])
    job_name = run["job_name"]
    archive_root_path = Path(archive_root)
    archive_root_path.mkdir(parents=True, exist_ok=True)

    destination = archive_root_path / job_name
    if destination.exists():
        time_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = archive_root_path / f"{job_name}_{time_tag}"
    destination.mkdir(parents=True, exist_ok=False)

    moved = []
    errors = []
    for extension in ARCHIVE_EXTENSIONS:
        source = work_dir / f"{job_name}{extension}"
        if not source.exists():
            continue
        try:
            shutil.move(str(source), str(destination / source.name))
            moved.append(source.name)
        except OSError as exc:
            errors.append(f"{source}: {exc}")

    if not moved:
        return {
            "status": "无文件",
            "message": f"未找到可归档结果文件：{job_name}",
            "destination": str(destination),
            "error": "",
            "moved": [],
        }

    if run.get("cleanup_after_archive"):
        try:
            work_dir.rmdir()
        except OSError:
            pass

    status = "部分失败" if errors else "已归档"
    message = f"结果已移动到存档目录：{job_name}\n{destination}\n文件数：{len(moved)}"
    return {
        "status": status,
        "message": message,
        "destination": str(destination),
        "error": "\n".join(errors),
        "moved": moved,
    }


class ArchiveCoordinator:
    def __init__(
        self,
        queue_items: list[QueueItem],
        deferred_archive_runs: dict[str, dict],
    ) -> None:
        self.queue_items = queue_items
        self.deferred_archive_runs = deferred_archive_runs

    def should_defer_archive(
        self,
        run: dict,
    ) -> tuple[bool, list[QueueItem]]:
        if not (run.get("archive_dir") or "").strip():
            return False, []
        dependents = unfinished_restart_dependents(
            run,
            self.queue_items,
        )
        return bool(dependents), dependents

    def archive_or_defer_run(
        self,
        run: dict,
    ) -> dict:
        should_defer, dependents = self.should_defer_archive(run)
        if should_defer:
            run["archive_deferred"] = True
            run["archive_deferred_reason"] = ", ".join(item.job_name for item in dependents)
            self.deferred_archive_runs[run["key"]] = run
            self.mark_archive_result(
                run,
                "等待重启动依赖",
                f"等待依赖作业结束：{run['archive_deferred_reason']}",
            )
            return {
                "action": "deferred",
                "run": run,
                "dependents": dependents,
                "result": {},
            }

        return {
            "action": "archived",
            "run": run,
            "dependents": [],
            "result": self.archive_run(run),
        }

    def archive_run(
        self,
        run: dict,
    ) -> dict:
        try:
            result = archive_finished_job_files(run)
        except OSError as exc:
            result = {
                "status": "失败",
                "message": "",
                "destination": "",
                "error": str(exc),
                "moved": [],
                "exception": exc,
            }
            self.mark_archive_result(
                run,
                "失败",
                str(exc),
            )
            return result

        if result.get("destination"):
            run["archive_destination"] = result["destination"]

        if result.get("status"):
            self.mark_archive_result(
                run,
                result["status"],
                result.get("error", ""),
            )

        return result

    def process_deferred_archives(
        self,
    ) -> list[dict]:
        processed = []
        for key, run in list(self.deferred_archive_runs.items()):
            if unfinished_restart_dependents(
                run,
                self.queue_items,
            ):
                continue
            self.deferred_archive_runs.pop(key, None)
            run["archive_deferred"] = False
            processed.append(
                {
                    "action": "archived",
                    "run": run,
                    "result": self.archive_run(run),
                }
            )
        return processed

    def mark_archive_result(self, run: dict, status: str, error: str) -> None:
        queue_item = run.get("queue_item")
        if queue_item is None:
            return
        queue_item.archive_status = status
        queue_item.archive_error = error
