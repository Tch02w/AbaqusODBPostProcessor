"""Pure file I/O helpers for moving completed SSD calculation directories."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


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
