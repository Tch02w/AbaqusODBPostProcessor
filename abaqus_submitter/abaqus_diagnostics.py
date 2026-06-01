"""Abaqus diagnostic file parsing and STA log formatting helpers.

This module is deliberately independent from the Tkinter GUI and queue globals.
It reads Abaqus output files, classifies job status, and formats STA progress text.
"""

import os
import re
import time
from datetime import datetime

from abaqus_submitter.constants import (
    COMPLETE_MARKERS,
    DIAGNOSTIC_EXTENSIONS,
    ERROR_MARKERS,
    LOG_SEPARATOR_WIDTH,
    TERMINATE_MARKERS,
)
from abaqus_submitter.ui_performance import measure_ui_callback


diagnostic_file_cache = {}


def clear_diagnostic_file_cache(work_dir="", job_name=""):
    """Clear cached diagnostic file tails for one job, or all cache entries."""
    if not work_dir and not job_name:
        diagnostic_file_cache.clear()
        return

    if not work_dir or not job_name:
        return

    for extension in DIAGNOSTIC_EXTENSIONS:
        path = os.path.join(work_dir, job_name + extension)
        diagnostic_file_cache.pop(path, None)
        diagnostic_file_cache.pop(os.path.abspath(path), None)


def decode_abaqus_text(data):
    """优先按 GBK 解码 Abaqus 文本，失败时退回 UTF-8。"""
    for encoding in ("gbk", "utf-8", "mbcs"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    return data.decode("gbk", errors="replace")


def read_file_tail(path, max_bytes=65536):
    """读取诊断文件尾部，避免大文件阻塞界面。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as file:
            if size > max_bytes:
                file.seek(size - max_bytes)
            data = file.read()

        return decode_abaqus_text(data)
    except OSError:
        return ""


def read_file_tail_cached(path, max_bytes=65536):
    """Read a diagnostic file tail only when the file changed."""
    try:
        stat_result = os.stat(path)
    except OSError:
        diagnostic_file_cache.pop(path, None)
        return ""

    signature = (stat_result.st_mtime_ns, stat_result.st_size)
    cached = diagnostic_file_cache.get(path)
    if cached and cached.get("signature") == signature:
        return cached.get("text", "")

    text = read_file_tail(path, max_bytes=max_bytes)
    diagnostic_file_cache[path] = {
        "signature": signature,
        "text": text,
    }
    return text


def read_file_head(path, max_bytes=262144):
    """读取文件开头部分，用于提取 Abaqus 提交时间。"""
    try:
        with open(path, "rb") as file:
            data = file.read(max_bytes)
        return decode_abaqus_text(data)
    except OSError:
        return ""


def format_backup_time_tag(timestamp):
    """将时间戳格式化为备份文件名使用的时间标签。"""
    return time.strftime("%Y%m%d%H%M", time.localtime(timestamp))


def parse_datetime_from_abaqus_text(text):
    """从 Abaqus sta/msg/dat/log 文本中提取作业时间。"""
    if not text:
        return None

    # 形式 1：
    # .log 中常见：
    # Begin Analysis Input File Processor
    # 5/30/2026 1:13:49 AM
    match = re.search(
        r"BEGIN\s+ANALYSIS\s+INPUT\s+FILE\s+PROCESSOR\s*[\r\n]+"
        r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s+"
        r"(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)?",
        text,
        re.IGNORECASE
    )
    if match:
        month, day, year, hour, minute, second, ampm = match.groups()
        hour = int(hour)

        if ampm:
            ampm = ampm.upper()
            if ampm == "PM" and hour != 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0

        try:
            return datetime(
                int(year),
                int(month),
                int(day),
                hour,
                int(minute),
                int(second)
            )
        except ValueError:
            pass

    # 形式 2：
    # Abaqus 2025 Date 30-5月-2026 Time 01:13:54
    # Abaqus/Standard 2025 DATE 29-5月-2026 TIME 18:57:22
    match = re.search(
        r"\bDATE\s+(\d{1,2})[-/](\d{1,2})\D*[-/](\d{4})\s+"
        r"TIME\s+(\d{1,2}):(\d{2}):(\d{2})",
        text,
        re.IGNORECASE
    )
    if match:
        day, month, year, hour, minute, second = match.groups()

        try:
            return datetime(
                int(year),
                int(month),
                int(day),
                int(hour),
                int(minute),
                int(second)
            )
        except ValueError:
            pass

    # 形式 3：
    # 普通英文日期时间：
    # 5/30/2026 1:13:49 AM
    match = re.search(
        r"\b(\d{1,2})/(\d{1,2})/(\d{4})\s+"
        r"(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)?\b",
        text,
        re.IGNORECASE
    )
    if match:
        month, day, year, hour, minute, second, ampm = match.groups()
        hour = int(hour)

        if ampm:
            ampm = ampm.upper()
            if ampm == "PM" and hour != 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0

        try:
            return datetime(
                int(year),
                int(month),
                int(day),
                hour,
                int(minute),
                int(second)
            )
        except ValueError:
            pass

    # 形式 4：
    # 2026-05-30 01:13:38
    match = re.search(
        r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+"
        r"(\d{1,2}):(\d{2}):(\d{2})\b",
        text
    )
    if match:
        year, month, day, hour, minute, second = match.groups()

        try:
            return datetime(
                int(year),
                int(month),
                int(day),
                int(hour),
                int(minute),
                int(second)
            )
        except ValueError:
            pass

    return None


def get_existing_job_backup_time_tag(work_dir, job_name, odb_path=""):
    """
    获取旧作业备份时间标签。
    优先从旧 sta/msg/log 中读取 Abaqus 作业时间；
    如果读取不到，则使用旧 ODB 文件创建时间；
    如果仍失败，则退回当前时间。
    """
    for extension in (".log", ".dat", ".msg", ".sta"):
        path = os.path.join(work_dir, job_name + extension)

        if not os.path.exists(path):
            continue

        text = read_file_head(path)
        job_datetime = parse_datetime_from_abaqus_text(text)

        if job_datetime:
            return job_datetime.strftime("%Y%m%d%H%M"), extension

    if odb_path and os.path.exists(odb_path):
        try:
            return format_backup_time_tag(os.path.getctime(odb_path)), "odb创建时间"
        except OSError:
            pass

    return time.strftime("%Y%m%d%H%M"), "当前时间"


def extract_key_diagnostic_line(text):
    """从 Abaqus 输出中提取最关键的一行诊断信息。"""
    important_words = ERROR_MARKERS + TERMINATE_MARKERS + (
        "TOO MANY ATTEMPTS",
        "NUMERICAL SINGULARITY",
        "ZERO PIVOT",
        "THE SYSTEM MATRIX HAS",
        "TIME INCREMENT REQUIRED IS LESS THAN",
        "EXCESSIVE DISTORTION",
        "DUE TO ERRORS",
        "ERRORS DETECTED",
    )

    for line in text.splitlines():
        upper_line = line.upper()
        if any(word in upper_line for word in important_words):
            return line.strip()

    return ""


def classify_job_text(text):
    """根据 Abaqus 输出文本判断作业最终状态。"""
    upper_text = text.upper()

    # 失败优先判断，避免 THE ANALYSIS HAS NOT BEEN COMPLETED 被 COMPLETED 误伤
    if any(marker in upper_text for marker in ERROR_MARKERS):
        detail = extract_key_diagnostic_line(text) or "检测到错误信息"
        return "失败", detail

    if any(marker in upper_text for marker in TERMINATE_MARKERS):
        detail = extract_key_diagnostic_line(text) or "检测到终止信息"
        return "终止", detail

    # .log 中常见的真正完成标志
    if re.search(r"ABAQUS\s+JOB\s+.+\s+COMPLETED", upper_text):
        return "完成", "检测到 Abaqus Job 完成信息"

    # .sta/.dat 中真正完成标志
    if any(marker in upper_text for marker in COMPLETE_MARKERS):
        return "完成", "检测到分析成功完成信息"

    return "", ""


def update_abaqus_stage_from_text(job_state, text):
    """根据控制台输出记录 Abaqus 是否进入 pre 或 standard 阶段。"""
    if job_state is None:
        return

    upper_text = text.upper()

    if (
        "BEGIN ANALYSIS INPUT FILE PROCESSOR" in upper_text
        or "RUN PRE.EXE" in upper_text
        or "END ANALYSIS INPUT FILE PROCESSOR" in upper_text
    ):
        job_state["pre_started"] = True

    if "END ANALYSIS INPUT FILE PROCESSOR" in upper_text:
        job_state["pre_finished"] = True

    if (
        "BEGIN ABAQUS/STANDARD ANALYSIS" in upper_text
        or "RUN STANDARD.EXE" in upper_text
    ):
        job_state["standard_started"] = True


def abaqus_stage_started(job_state):
    """只要 pre 或 standard 启动过，就不能因为没有 sta 直接判失败。"""
    return (
        job_state.get("pre_started")
        or job_state.get("pre_finished")
        or job_state.get("standard_started")
    )


def inspect_job_files(work_dir, job_name, submitted_after=0):
    """读取 sta/msg/dat/log 文件判断最终状态。"""
    with measure_ui_callback("inspect_job_files"):
        combined_text = ""
    
        for extension in DIAGNOSTIC_EXTENSIONS:
            path = os.path.join(work_dir, job_name + extension)
    
            if not os.path.exists(path):
                continue
    
            if submitted_after:
                try:
                    if os.path.getmtime(path) < submitted_after:
                        continue
                except OSError:
                    continue
    
            combined_text += "\n" + read_file_tail_cached(path)
    
        return classify_job_text(combined_text)


def inspect_job_files_throttled(monitor_state, force=False, interval_seconds=20):
    """Throttle diagnostic file inspection during normal polling."""
    with measure_ui_callback("inspect_job_files_throttled"):
        now = time.time()
        if (
                not force
                and now - monitor_state.get("last_file_inspection_at", 0) < interval_seconds
        ):
            return "", ""
    
        monitor_state["last_file_inspection_at"] = now
        job_state = monitor_state["job_state"]
        return inspect_job_files(
            job_state["work_dir"],
            job_state["job_name"],
            monitor_state["submitted_at"]
        )


def parse_sta_progress(text):
    """从 sta 新增内容中提取 Step 和 Abaqus 总时间。"""
    progress = None

    for line in text.splitlines():
        parts = line.split()

        if len(parts) < 9:
            continue

        if not parts[0].isdigit() or not parts[1].isdigit():
            continue

        progress = {
            "step": parts[0],
            "total_time": parts[6],
        }

    return progress


def is_sta_progress_line(line):
    """判断一行是否为 Abaqus .sta 中的增量进度行。"""
    parts = line.split()

    if len(parts) < 9:
        return False

    return parts[0].isdigit() and parts[1].isdigit()


def append_sta_separator_once(output_lines, job_state):
    """在日志中只插入一次 STA 输出区分隔线。"""
    if not job_state.get("sta_separator_printed"):
        output_lines.append("*" * LOG_SEPARATOR_WIDTH)
        job_state["sta_separator_printed"] = True


def build_sta_table_header():
    return (
        f"{'STEP':>4} {'INC':>3} {'ATT':>3} "
        f"{'SEV':>3} {'EQUIL':>5} {'TOTAL':>5} "
        f"{'TOTAL_TIME':>10} {'STEP_TIME':>9} {'INC_TIME':>8}"
    )


def get_display_width(text):
    """估算等宽日志中含中文文本的显示宽度。"""
    return sum(2 if ord(char) > 127 else 1 for char in text)


def format_abaqus_standard_title(line):
    """将 Abaqus 标题右侧日期时间对齐到日志分隔线右边界。"""
    match = re.match(r"^(Abaqus/Standard\s+\S+)\s+(DATE\s+.+)$", line, re.IGNORECASE)
    if match:
        left_text = match.group(1)
        right_text = match.group(2)
        gap_width = max(
            2,
            LOG_SEPARATOR_WIDTH
            - get_display_width(left_text)
            - get_display_width(right_text)
        )
        return [f"{left_text}{' ' * gap_width}{right_text}"]

    return [line]


def format_sta_output_for_log(text, job_state):
    """将 Abaqus .sta 原始输出整理为紧凑表格显示。"""
    output_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        upper_line = stripped.upper()

        if not stripped:
            continue

        # Abaqus 标题行
        if "ABAQUS/STANDARD" in upper_line and "DATE" in upper_line:
            if not job_state.get("sta_title_printed"):
                append_sta_separator_once(output_lines, job_state)
                output_lines.extend(format_abaqus_standard_title(stripped))
                output_lines.append("")
                job_state["sta_title_printed"] = True
            continue

        # 原始 SUMMARY 表头替换为自定义短表头
        if "SUMMARY OF JOB INFORMATION" in upper_line:
            if not job_state.get("sta_header_printed"):
                append_sta_separator_once(output_lines, job_state)
                output_lines.append(build_sta_table_header())
                output_lines.append("-" * LOG_SEPARATOR_WIDTH)
                job_state["sta_header_printed"] = True
            continue

        # 跳过 Abaqus 原始英文多行表头
        skip_keywords = (
            "STEP  INC ATT",
            "DISCON ITERS",
            "ITERS",
            "TIME/",
            "LPF",
            "MONITOR RIKS",
            "FREQ",
            "DOF",
        )

        if any(keyword in upper_line for keyword in skip_keywords):
            continue

        # 整理增量行
        if is_sta_progress_line(stripped):
            if not job_state.get("sta_header_printed"):
                append_sta_separator_once(output_lines, job_state)
                output_lines.append(build_sta_table_header())
                output_lines.append("-" * LOG_SEPARATOR_WIDTH)
                job_state["sta_header_printed"] = True

            parts = stripped.split()

            step = parts[0]
            inc = parts[1]
            att = parts[2]
            severe = parts[3]
            equil = parts[4]
            total_iter = parts[5]
            total_time = parts[6]
            step_time = parts[7]
            inc_time = parts[8]

            cutback_note = ""
            if att.upper().endswith("U"):
                cutback_note = "  cut"

            output_lines.append(
                f"{step:>4} {inc:>3} {att:>3} "
                f"{severe:>3} {equil:>5} {total_iter:>5} "
                f"{total_time:>10} {step_time:>9} {inc_time:>8}"
                f"{cutback_note}"
            )
            continue

        # 关键结束/错误信息保留
        important_keywords = (
            "THE ANALYSIS HAS",
            "ERROR",
            "WARNING",
            "ABAQUS JOB",
            "COMPLETED",
            "ABORTED",
            "TERMINATED",
        )

        if any(keyword in upper_line for keyword in important_keywords):
            append_sta_separator_once(output_lines, job_state)
            output_lines.append(stripped)

    if output_lines:
        return "\n".join(output_lines) + "\n"

    return ""


__all__ = [
    "clear_diagnostic_file_cache",
    "decode_abaqus_text",
    "read_file_tail",
    "read_file_tail_cached",
    "read_file_head",
    "format_backup_time_tag",
    "parse_datetime_from_abaqus_text",
    "get_existing_job_backup_time_tag",
    "extract_key_diagnostic_line",
    "classify_job_text",
    "update_abaqus_stage_from_text",
    "abaqus_stage_started",
    "inspect_job_files",
    "inspect_job_files_throttled",
    "parse_sta_progress",
    "is_sta_progress_line",
    "append_sta_separator_once",
    "build_sta_table_header",
    "get_display_width",
    "format_abaqus_standard_title",
    "format_sta_output_for_log",
]
