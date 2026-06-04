"""Command building helpers shared by the Qt frontend."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .constants import DEFAULT_CPUS, MAX_CPUS
from .models import QueueItem


MEMORY_OPTIONS = ("%", "GB", "MB")


@dataclass(frozen=True)
class SubmitOptions:
    inp_file: str = ""
    job_name: str = ""
    cpus: int = DEFAULT_CPUS
    oldjob_path: str = ""
    for_file: str = ""
    interactive: bool = False
    datacheck: bool = False
    memory_value: str = ""
    memory_unit: str = "%"
    ask_delete_off: bool = False


def derive_job_name(inp_file: str) -> str:
    """Return the default Abaqus job name for an INP file."""
    if not inp_file:
        return ""
    return Path(inp_file).stem


def derive_oldjob_name(oldjob_path: str) -> str:
    """Return the oldjob argument from an ODB path or raw name."""
    if not oldjob_path:
        return ""
    path = Path(oldjob_path)
    if path.suffix.lower() == ".odb":
        return path.stem
    return oldjob_path.strip()


def validate_job_name(job_name: str) -> tuple[bool, str]:
    """Validate an Abaqus job name."""
    if not job_name:
        return False, "作业名称不能为空。"
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_\-]*", job_name):
        return False, "作业名称只能包含字母、数字、下划线或短横线，且不能以短横线开头。"
    return True, ""


def validate_cpus(cpus_text: str) -> tuple[bool, int, str]:
    """Validate the CPU field and return the parsed value."""
    try:
        cpus = int(cpus_text.strip())
    except ValueError:
        return False, 0, "Core 必须是整数。"
    if cpus < 0 or cpus > MAX_CPUS:
        return False, cpus, f"Core 必须在 0 到 {MAX_CPUS} 之间。"
    return True, cpus, ""


def memory_argument(memory_value: str, memory_unit: str) -> str:
    """Build the Abaqus memory argument."""
    unit = (memory_unit or "%").strip()
    value = (memory_value or "").strip()
    if not value:
        return ""
    if unit == "%":
        return f"{value}%"
    return f"{value}{unit.lower()}"


def validate_memory_argument(argument: str) -> tuple[bool, str]:
    """Validate an Abaqus memory argument."""
    if not argument:
        return True, ""
    if re.fullmatch(r"\d+%", argument):
        return True, ""
    if re.fullmatch(r"\d+(\.\d+)?\s*(mb|gb|MB|GB)", argument):
        return True, ""
    return False, "Mem 支持整数百分比，或 MB/GB 数值，例如 80%、4096MB、8GB。"


def build_abaqus_command(options: SubmitOptions) -> str:
    """Build the Abaqus command line from UI options."""
    parts = [f"abaqus job={options.job_name}"]

    if options.datacheck and options.inp_file:
        parts.append(f"input={os.path.basename(options.inp_file)}")

    oldjob_name = derive_oldjob_name(options.oldjob_path)
    if oldjob_name:
        parts.append(f"oldjob={oldjob_name}")

    if options.for_file:
        parts.append(f'user="{options.for_file}"')

    if options.cpus != 0:
        parts.append(f"cpus={options.cpus}")

    memory = memory_argument(options.memory_value, options.memory_unit)
    if memory:
        parts.append(f"memory={memory}")

    if options.ask_delete_off:
        parts.append("ask_delete=OFF")

    if options.datacheck:
        parts.append("datacheck")

    if options.interactive:
        parts.append("interactive")

    return " ".join(parts)


def validate_options(options: SubmitOptions) -> tuple[bool, str]:
    """Validate a submit request."""
    if not options.inp_file:
        return False, "请先选择 INP 文件。"
    if not Path(options.inp_file).exists():
        return False, "INP 文件不存在。"

    ok, message = validate_job_name(options.job_name)
    if not ok:
        return False, message

    oldjob_name = derive_oldjob_name(options.oldjob_path)
    if oldjob_name:
        ok, message = validate_job_name(oldjob_name)
        if not ok:
            return False, f"重启动作业名称无效：{message}"
        if oldjob_name == options.job_name:
            return False, "当前作业名称不能与 oldjob 名称相同。"

    memory = memory_argument(options.memory_value, options.memory_unit)
    ok, message = validate_memory_argument(memory)
    if not ok:
        return False, message

    return True, ""


def parse_memory_text(memory: str) -> tuple[str, str]:
    value = (memory or "").strip()
    if not value:
        return "", "%"
    lower = value.lower()
    if lower.endswith("gb"):
        return value[:-2].strip(), "GB"
    if lower.endswith("mb"):
        return value[:-2].strip(), "MB"
    if lower.endswith("%"):
        return value[:-1].strip(), "%"
    return value, "%"


def queue_item_to_options(
    item: QueueItem,
    *,
    default_cpus: int,
) -> SubmitOptions:
    memory_value, memory_unit = parse_memory_text(item.memory)
    return SubmitOptions(
        inp_file=item.inp_path,
        job_name=item.job_name or derive_job_name(item.inp_path),
        cpus=item.cores or default_cpus,
        oldjob_path=item.oldjob_path,
        for_file=item.fortran_path,
        interactive=item.interactive,
        datacheck=item.datacheck_only,
        memory_value=memory_value,
        memory_unit=memory_unit,
    )


def build_direct_submit_queue_item(
    options: SubmitOptions,
    *,
    notify: bool,
) -> QueueItem:
    memory = ""

    if options.memory_value:
        memory = f"{options.memory_value}{'%' if options.memory_unit == '%' else options.memory_unit.lower()}"

    return QueueItem(
        inp_path=options.inp_file,
        source_inp_path=options.inp_file,
        job_name=options.job_name,
        source="direct_submit",
        status="启动中",
        selected=False,
        valid=True,
        message="正在提交",
        run_mode=("restart" if options.oldjob_path else "normal"),
        oldjob_name=derive_oldjob_name(options.oldjob_path),
        oldjob_path=options.oldjob_path,
        fortran_path=options.for_file,
        cores=options.cpus,
        memory=memory,
        interactive=options.interactive,
        datacheck_only=options.datacheck,
        complete_notify=notify,
        is_external=False,
    )
