"""SSD work-directory preparation, result handling, and archive coordination."""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .command import SubmitOptions, derive_oldjob_name
from .models import QueueItem
from .queue_scheduler import unfinished_restart_dependents


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
    root = Path(work_dir)
    time_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = {"odb": "", "sta": ""}

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

    return result


def delete_existing_result_files(work_dir: str | Path, job_name: str) -> dict:
    """Delete the existing ODB and matching STA before a new submit."""
    root = Path(work_dir)
    result = {"odb": "", "sta": ""}
    for extension in EXISTING_RESULT_EXTENSIONS:
        path = root / f"{job_name}{extension}"
        if not path.exists():
            continue
        path.unlink()
        result[extension.lstrip(".")] = str(path)
    return result


def prepare_calculation_workspace(
    options: SubmitOptions,
    queue_item: QueueItem | None,
    oldjob_source_dir: str = "",
) -> tuple[SubmitOptions, dict]:
    """Copy an INP to the optional SSD work directory before submitting."""
    source_inp = Path(options.inp_file)
    workspace_info = {
        "source_inp_path": str(source_inp),
        "archive_dir": "",
        "cleanup_after_archive": False,
        "copied_inp_path": "",
        "copied_oldjob_files": [],
    }
    if queue_item is None:
        return options, workspace_info

    archive_dir = (queue_item.archive_dir or "").strip()
    workspace_info["archive_dir"] = archive_dir
    workspace_info["cleanup_after_archive"] = bool(queue_item.cleanup_after_archive)

    ssd_root = (queue_item.calculation_work_dir or "").strip()
    if not ssd_root:
        return options, workspace_info

    calc_dir = Path(ssd_root) / options.job_name
    calc_dir.mkdir(parents=True, exist_ok=True)
    copied_inp = calc_dir / source_inp.name
    shutil.copy2(source_inp, copied_inp)
    queue_item.source_inp_path = str(source_inp)
    workspace_info["copied_inp_path"] = str(copied_inp)

    oldjob_name = derive_oldjob_name(options.oldjob_path)
    if oldjob_name:
        source_dir = Path(oldjob_source_dir) if oldjob_source_dir else Path(options.oldjob_path).parent
        copied_oldjob_files = copy_restart_dependency_files(source_dir, calc_dir, oldjob_name)
        workspace_info["copied_oldjob_files"] = [str(path) for path in copied_oldjob_files]

    return replace(options, inp_file=str(copied_inp)), workspace_info


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
        shutil.copy2(source, target)
        copied.append(target)
    return copied


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
