"""UI-independent local job draft collected by the primary workbench."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .command import SubmitOptions, derive_job_name, derive_oldjob_name
from .models import QueueItem


@dataclass(frozen=True)
class LocalJobDraft:
    """One source of truth for local submission fields."""

    inp_file: str = ""
    job_name: str = ""
    cpus: int = 0
    memory_value: str = ""
    memory_unit: str = "%"
    oldjob_path: str = ""
    fortran_path: str = ""
    interactive: bool = False
    datacheck: bool = False
    notify: bool = True
    abaqus_command: str = "abaqus"
    priority: int = 0
    max_parallel: int = 1
    calculation_root_dir: str = ""
    archive_dir: str = ""

    def effective_job_name(self) -> str:
        return self.job_name.strip() or derive_job_name(self.inp_file)

    def to_submit_options(self) -> SubmitOptions:
        return SubmitOptions(
            inp_file=self.inp_file.strip(),
            job_name=self.effective_job_name(),
            cpus=int(self.cpus),
            oldjob_path=self.oldjob_path.strip(),
            for_file=self.fortran_path.strip(),
            interactive=bool(self.interactive),
            datacheck=bool(self.datacheck),
            memory_value=self.memory_value.strip(),
            memory_unit=self.memory_unit.strip() or "%",
            abaqus_command=self.abaqus_command.strip() or "abaqus",
        )

    def validate_local_paths(self) -> tuple[bool, str]:
        for label, value in (
            ("SSD 工作目录", self.calculation_root_dir),
            ("结果归档目录", self.archive_dir),
        ):
            if value.strip() and not Path(value.strip()).is_dir():
                return False, f"{label}不存在或不是文件夹：{value.strip()}"
        return True, ""

    def apply_to_queue_item(self, item: QueueItem) -> None:
        """Apply scheduling and workspace choices not carried by SubmitOptions."""

        memory = ""
        if self.memory_value.strip():
            memory = (
                f"{self.memory_value.strip()}"
                f"{'%' if self.memory_unit == '%' else self.memory_unit.lower()}"
            )
        item.job_name = self.effective_job_name()
        item.run_mode = "restart" if self.oldjob_path.strip() else "normal"
        item.oldjob_path = self.oldjob_path.strip()
        item.oldjob_name = derive_oldjob_name(item.oldjob_path)
        item.fortran_path = self.fortran_path.strip()
        item.cores = int(self.cpus)
        item.memory = memory
        item.interactive = bool(self.interactive)
        item.datacheck_only = bool(self.datacheck)
        item.complete_notify = bool(self.notify)
        item.priority = int(self.priority)
        item.calculation_root_dir = self.calculation_root_dir.strip()
        item.archive_dir = self.archive_dir.strip()
        item.archive_after_complete = bool(item.archive_dir)
        item.cleanup_after_archive = bool(
            item.calculation_root_dir and item.archive_after_complete
        )
        item.abaqus_command = self.abaqus_command.strip() or "abaqus"


__all__ = ["LocalJobDraft"]
