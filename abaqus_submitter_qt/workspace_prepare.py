"""Pure file I/O helpers for preparing calculation work directories."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class WorkspacePrepareResult:
    prepared_inp_path: str
    prepared_work_dir: str = ""
    copied_inp_path: str = ""
    copied_oldjob_files: tuple[str, ...] = ()


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
