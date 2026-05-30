import ctypes
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk

import customtkinter as ctk


def get_physical_cpu_count():
    """Return the physical CPU core count, falling back to logical CPUs."""
    if os.name == "nt":
        try:
            relation_processor_core = 0
            returned_length = ctypes.c_uint32(0)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            get_processor_info = kernel32.GetLogicalProcessorInformationEx
            get_processor_info.argtypes = [
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32)
            ]
            get_processor_info.restype = ctypes.c_int

            get_processor_info(
                relation_processor_core,
                None,
                ctypes.byref(returned_length)
            )
            if returned_length.value <= 0:
                raise OSError("CPU core information is unavailable.")

            buffer = ctypes.create_string_buffer(returned_length.value)
            success = get_processor_info(
                relation_processor_core,
                buffer,
                ctypes.byref(returned_length)
            )
            if not success:
                raise OSError("Failed to read CPU core information.")

            offset = 0
            core_count = 0
            while offset < returned_length.value:
                entry_size = ctypes.c_uint32.from_buffer_copy(buffer, offset + 4).value
                if entry_size <= 0:
                    break
                core_count += 1
                offset += entry_size

            if core_count:
                return core_count
        except (OSError, AttributeError, ValueError):
            pass

    return os.cpu_count() or 1


MAX_CPUS = get_physical_cpu_count()
DEFAULT_CPUS = max(1, MAX_CPUS // 2)
STA_POLL_INTERVAL_MS = 5000
STA_FILE_ENCODING = "GBK"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
LEFT_PANEL_WIDTH = 416
RIGHT_PANEL_MIN_WIDTH = 488
LOG_SEPARATOR_WIDTH = 64
LOG_TEXT_WIDTH = LOG_SEPARATOR_WIDTH
WINDOW_HORIZONTAL_PADDING = 24
LEFT_ONLY_GEOMETRY = f"{LEFT_PANEL_WIDTH + WINDOW_HORIZONTAL_PADDING}x720"
FULL_GEOMETRY = f"{LEFT_PANEL_WIDTH + RIGHT_PANEL_MIN_WIDTH + WINDOW_HORIZONTAL_PADDING}x720"
LEFT_ONLY_MIN_SIZE = (LEFT_PANEL_WIDTH + WINDOW_HORIZONTAL_PADDING, 640)
FULL_MIN_SIZE = (LEFT_PANEL_WIDTH + RIGHT_PANEL_MIN_WIDTH + WINDOW_HORIZONTAL_PADDING, 640)
INP_FILE_PLACEHOLDER = "点击选择 INP 文件"
OLDJOB_PLACEHOLDER = "点击选择重启动 ODB（可选）"
FOR_FILE_PLACEHOLDER = "点击选择 Fortran 子程序（可选）"
COMPLETE_MARKERS = (
    "THE ANALYSIS HAS COMPLETED SUCCESSFULLY",
    "THE ANALYSIS HAS BEEN COMPLETED SUCCESSFULLY",
)

TERMINATE_MARKERS = (
    "THE ANALYSIS HAS BEEN TERMINATED",
    "TERMINATED",
    "USER REQUESTED TERMINATION",
)

ERROR_MARKERS = (
    "ABAQUS ERROR",
    "***ERROR",
    "ERROR:",
    "ABORTED",
    "EXITED WITH ERRORS",
    "EXITED WITH ERROR",
    "ABAQUS/ANALYSIS EXITED WITH ERROR",
    "THE ANALYSIS HAS NOT BEEN COMPLETED",
    "LICENSE ERROR",
    "LICENSE MANAGER ERROR",
    "UNABLE TO CHECKOUT",
    "NO LICENSE",
    "PROBLEM DURING COMPILATION",
    "PROBLEM DURING LINKING",
    "LINK FATAL ERROR",
    "TOO MANY ATTEMPTS",
    "NUMERICAL SINGULARITY",
    "ZERO PIVOT",
    "TIME INCREMENT REQUIRED IS LESS THAN",
    "EXCESSIVE DISTORTION",
    "DUE TO ERRORS",
    "ERRORS DETECTED",
)

JOB_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
DIAGNOSTIC_EXTENSIONS = (".sta", ".msg", ".dat", ".log")
OVERWRITE_PROMPT_MARKERS = (
    "OLD JOB FILES EXIST",
    "OVERWRITE?",
    "OVERWRITE",
    "ALREADY EXISTS",
    "EXISTING",
    "Y/N",
    "(Y/N)",
)
MEMORY_OPTIONS = ("默认", "%", "GB", "MB")

# ================= 字体统一设置 =================
FONT_FAMILY = "Microsoft YaHei"

FONT_TITLE = (FONT_FAMILY, 19, "bold")
FONT_SUBTITLE = (FONT_FAMILY, 9)
FONT_LABEL = (FONT_FAMILY, 12, "bold")
FONT_HINT = (FONT_FAMILY, 12)
FONT_ENTRY = (FONT_FAMILY, 10)
FONT_NUMERIC_ENTRY = (FONT_FAMILY, 12)
FONT_MEMORY_MENU = (FONT_FAMILY, 11)
FONT_BUTTON = (FONT_FAMILY, 12)
FONT_BUTTON_BOLD = (FONT_FAMILY, 15, "bold")
FONT_LOG = ("Consolas", 10)

APP_BG = "#ffffff"
CARD_BG = "#ffffff"
LOG_BG = "#ffffff"
BTN_LIGHT_FG = "#dbe3ee"
BTN_LIGHT_HOVER = "#cbd5e1"
BTN_LIGHT_TEXT = "#111827"

BTN_PAUSE_FG = "#facc15"      # 黄色：暂停
BTN_PAUSE_HOVER = "#eab308"
BTN_RESUME_FG = "#22c55e"     # 绿色：继续
BTN_RESUME_HOVER = "#16a34a"
BTN_STATUS_TEXT_DARK = "#111827"
BTN_STATUS_TEXT_LIGHT = "#ffffff"

log_tab_counter = 0
right_panel_visible = False
active_jobs = {}


def load_config():
    """读取本地配置。"""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config():
    """保存当前界面配置。"""
    config = {
        "cpus": cpus_var.get().strip(),
        "memory_mode": memory_mode_var.get().strip(),
        "custom_memory": get_custom_memory_value(),
        "interactive": interactive_var.get(),
        "datacheck": datacheck_var.get(),
        "complete_notify": complete_notify_var.get(),
        "window_geometry": root.geometry(),
    }

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as file:
            json.dump(config, file, ensure_ascii=False, indent=2)
    except OSError:
        pass


def append_log(log_widget, text):
    """向指定日志页追加文本并滚动到底部。"""
    try:
        if log_widget.winfo_exists():
            insert_start = log_widget.index("end-1c")
            log_widget.insert(tk.END, text)
            log_widget.see(tk.END)
            job_state = getattr(log_widget, "job_state", None)
            if job_state is not None:
                remember_sta_header_index(log_widget, job_state, insert_start, text)
                update_sta_fixed_header_visibility(job_state)
    except tk.TclError:
        pass


def cache_console_output(job_state, text):
    """缓存 Abaqus 控制台输出，用于没有 .sta 时判断提交错误。"""
    if job_state is None or job_state.get("finalized"):
        return

    console_output = job_state.get("console_output", "")
    job_state["console_output"] = (console_output + text)[-12000:]


def maybe_finalize_from_console_output(job_state, text):
    """控制台检测到错误时只做标记，不直接结束作业。"""
    if job_state is None or job_state.get("finalized") or job_state.get("terminating"):
        return

    final_status, detail = classify_job_text(text)

    if final_status == "失败":
        job_state["console_failed"] = True
        job_state["console_failed_detail"] = detail


def remember_sta_header_index(log_widget, job_state, insert_start, text):
    """记录原始 STA 表头在日志框中的位置。"""
    if job_state.get("sta_header_index"):
        return

    header_text = build_sta_table_header()
    if header_text not in text:
        return

    prefix = text.split(header_text, 1)[0]
    line_offset = prefix.count("\n")
    job_state["sta_header_index"] = log_widget.index(
        f"{insert_start}+{line_offset} lines linestart"
    )
    job_state["sta_fixed_header_ready"] = True


def update_sta_fixed_header_visibility(job_state):
    """只有原始 STA 表头滚出可视区域后才显示固定表头。"""
    header_label = job_state.get("sta_header_label")
    log_widget = job_state.get("log_widget")
    header_index = job_state.get("sta_header_index")

    if header_label is None or log_widget is None:
        return

    try:
        if not header_label.winfo_exists():
            return

        if not job_state.get("sta_fixed_header_ready") or not header_index:
            header_label.grid_remove()
            return

        top_index = log_widget.index("@0,0")
        if log_widget.compare(header_index, "<", top_index):
            header_label.configure(
                text=build_sta_table_header() + "\n" + "-" * LOG_SEPARATOR_WIDTH
            )
            header_label.grid()
        else:
            header_label.grid_remove()
    except tk.TclError:
        pass


def append_history_text(text, tag=None):
    """向提交记录追加文本。"""
    if history_text.get("1.0", "end-1c").strip() == "等待提交作业...":
        history_text.delete("1.0", tk.END)

    if tag:
        history_text.insert(tk.END, text, tag)
    else:
        history_text.insert(tk.END, text)

    history_text.see(tk.END)


def append_submit_history(job_state):
    """在左侧提交记录中追加作业配置摘要。"""
    submit_time = time.strftime("%Y-%m-%d %H:%M:%S")
    job_name = job_state["job_name"]
    cpus = job_state["cpus"]
    oldjob_name = job_state["oldjob_name"]
    for_file_path = job_state["for_file_path"]
    cpus_text = cpus if cpus != 0 else "默认"
    options = []

    if oldjob_name:
        options.append(f"ODB: {oldjob_name}")

    if for_file_path:
        options.append(f"FOR: {os.path.basename(for_file_path)}")

    odb_action = job_state.get("odb_action", "")
    backup_odb_path = job_state.get("backup_odb_path", "")
    backup_sta_path = job_state.get("backup_sta_path", "")
    deleted_sta_path = job_state.get("deleted_sta_path", "")

    if odb_action == "overwrite":
        options.append("同名ODB: 已覆盖")
    elif odb_action == "backup" and backup_odb_path:
        options.append(f"同名ODB备份: {os.path.basename(backup_odb_path)}")

    if backup_sta_path:
        options.append(f"旧STA备份: {os.path.basename(backup_sta_path)}")
    if deleted_sta_path:
        options.append(f"旧STA已删: {os.path.basename(deleted_sta_path)}")

    option_text = "；".join(options) if options else "无可选文件"
    output_mode = "交互输出" if job_state["interactive_mode"] else "后台提交"
    memory_text = job_state["memory_argument"] if job_state["memory_argument"] else "默认内存配置"
    datacheck_text = "datacheck" if job_state["datacheck_mode"] else "正式计算"
    odb_action = job_state.get("odb_action", "")

    if odb_action == "overwrite":
        overwrite_text = "同名ODB已覆盖"
    elif odb_action == "backup":
        overwrite_text = "同名ODB已备份"
    else:
        overwrite_text = "无同名ODB处理"

    append_history_text(f"[{submit_time}]\n", "history_time")

    append_history_text(
        f"作业名称：{job_name}\n"
        f"核心数量：{cpus_text}\n"
        f"内存限制：{memory_text}\n"
        f"计算类型：{datacheck_text}\n"
        f"可选文件：{option_text}\n"
        f"提交方式：{output_mode}\n"
        f"已有结果：{overwrite_text}\n"
        f"\n提交命令：\n{job_state['cmd']}\n\n"
        f"{'-' * 36}\n\n"
    )


def append_job_final_history(job_state, status, detail=""):
    """在提交记录中保留作业最终状态。"""
    finish_time = time.strftime("%Y-%m-%d %H:%M:%S")
    elapsed_text = format_duration(
        job_state["end_time"] - job_state["start_time"]
    ) if job_state.get("start_time") and job_state.get("end_time") else "未知"
    detail_text = f" | {detail}" if detail else ""
    append_history_text(f"[{finish_time}]\n", "history_time")

    append_history_text(
        f"作业名称：{job_state['job_name']}\n"
        f"结束状态：{status}\n"
        f"作业耗时：{elapsed_text}\n"
        f"{'详细信息：' + detail + chr(10) if detail else ''}"
        f"{'-' * 36}\n"
    )


def append_preview_command(cmd):
    """在提交记录中显示命令预览。"""
    preview_time = time.strftime("%Y-%m-%d %H:%M:%S")
    append_history_text(f"[{preview_time}] 命令预览：\n{cmd}\n\n")


def format_duration(seconds):
    """格式化运行耗时。"""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours} h {minutes} min {seconds} s"

    if minutes:
        return f"{minutes} min {seconds} s"

    return f"{seconds} s"


def get_job_key(work_dir, job_name):
    """用工作目录和作业名生成运行中作业的唯一键。"""
    return os.path.normcase(os.path.abspath(os.path.join(work_dir, job_name)))


def validate_abaqus_job_name(job_name, label="作业名称"):
    """检查 Abaqus 命令行中的 job/oldjob 名称是否安全。"""
    if JOB_NAME_PATTERN.fullmatch(job_name):
        return True

    messagebox.showerror(
        "错误",
        f"{label}“{job_name}”不符合 Abaqus 命令行名称要求。\n"
        "建议只使用英文字母、数字、下划线或短横线，并且不要以数字开头。"
    )
    return False

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

        combined_text += "\n" + read_file_tail(path)

    return classify_job_text(combined_text)

def collect_job_signals(job_state, monitor_state, sta_path, process):
    """集中收集当前 Abaqus 作业的关键信号。"""
    work_dir = job_state["work_dir"]
    job_name = job_state["job_name"]

    sta_exists = os.path.exists(sta_path)
    lock_exists = job_lock_exists(job_state)
    stage_started = abaqus_stage_started(job_state)
    process_exited = process.poll() is not None
    wait_seconds = time.time() - monitor_state["submitted_at"]

    file_status, file_detail = inspect_job_files(
        work_dir,
        job_name,
        monitor_state["submitted_at"]
    )

    return {
        "sta_exists": sta_exists,
        "lock_exists": lock_exists,
        "stage_started": stage_started,
        "pre_started": job_state.get("pre_started", False),
        "pre_finished": job_state.get("pre_finished", False),
        "standard_started": job_state.get("standard_started", False),
        "process_exited": process_exited,
        "process_returncode": process.returncode,
        "wait_seconds": wait_seconds,
        "file_status": file_status,
        "file_detail": file_detail,
        "console_failed": job_state.get("console_failed", False),
        "console_failed_detail": job_state.get("console_failed_detail", ""),
    }


def decide_job_status_before_sta(signals):
    """
    在 .sta 尚未生成时，根据作业信号判断是否可以结束作业。
    返回 (status, detail)，如果还不能判断则返回 ("", "")。
    """

    # 1. 最高优先级：诊断文件中已经明确出现完成、失败或终止信息
    if signals["file_status"]:
        return signals["file_status"], signals["file_detail"]

    # 2. .lck 存在，认为 Abaqus 作业仍可能在运行，不判失败
    if signals["lock_exists"]:
        return "", ""

    # 3. 控制台明确报错，并且 pre/standard 均未启动，才判定提交失败
    if signals["console_failed"] and not signals["stage_started"]:
        return (
            "失败",
            signals["console_failed_detail"] or "控制台检测到错误信息"
        )

    # 4. 启动进程非 0 退出，并且 pre/standard 均未启动，才判定失败
    if (
        signals["process_exited"]
        and signals["process_returncode"] not in (0, None)
        and not signals["stage_started"]
    ):
        return (
            "失败",
            f"Abaqus 启动进程异常结束，pre/standard 均未启动，返回码 {signals['process_returncode']}"
        )

    # 5. 长时间没有 .lck、.sta、pre、standard 任何信号，判定疑似未启动
    if (
        signals["wait_seconds"] > 90
        and not signals["sta_exists"]
        and not signals["stage_started"]
        and not signals["lock_exists"]
    ):
        return (
            "失败",
            "提交后 90 秒内未检测到 pre/standard、lck 或 sta，疑似未真正启动"
        )

    return "", ""

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


def format_progress_status(job_state, prefix="Running"):
    """格式化作业顶部状态栏文本。"""
    progress = job_state.get("progress") or {}

    start_time = job_state.get("start_time")
    if start_time:
        elapsed_text = format_duration(time.time() - start_time)
    else:
        elapsed_text = "未知"

    if job_state.get("waiting_sta") and not progress:
        return f"{prefix} | 等待 sta | {elapsed_text}"

    if not progress:
        return f"{prefix} | {elapsed_text}"

    step = progress.get("step", "-")
    total_time = progress.get("total_time", "-")

    return f"{prefix} | Step {step} | Time {total_time} | {elapsed_text}"


def set_job_status(job_state, text):
    """更新作业日志页顶部状态。"""
    status_var = job_state.get("status_var")
    if status_var is not None:
        status_var.set(text)


def ask_overwrite_existing_job(job_state):
    """由 Abaqus 提示触发，询问是否覆盖旧作业结果。"""
    return messagebox.askyesno(
        "已有结果文件",
        f"Abaqus 提示作业“{job_state['job_name']}”已有结果文件。\n\n"
        "是否覆盖并继续提交？\n"
        "选择“否”将取消本次提交。"
    )


def maybe_answer_overwrite_prompt(process, job_state, output_buffer):
    """检测 Abaqus 覆盖提示，询问后向 stdin 写入 y/n。"""
    if (
        job_state is None
        or job_state.get("overwrite_answer_sent")
        or job_state.get("overwrite_prompt_pending")
    ):
        return

    prompt_text = output_buffer.upper()
    has_overwrite_context = any(
        marker in prompt_text for marker in OVERWRITE_PROMPT_MARKERS
    )
    has_choice = "Y" in prompt_text and "N" in prompt_text

    if not has_overwrite_context or not has_choice or process.stdin is None:
        return

    job_state["overwrite_prompt_pending"] = True

    try:
        should_overwrite = ask_overwrite_existing_job(job_state)
        answer = "y" if should_overwrite else "n"
        action_text = "覆盖已有结果文件" if answer == "y" else "不覆盖已有结果文件"

        process.stdin.write(answer + "\n")
        process.stdin.flush()
        job_state["overwrite_answer_sent"] = True
        job_state["overwrite_existing"] = should_overwrite
        append_log(
            job_state["log_widget"],
            f"\n状态：检测到 Abaqus 覆盖提示，已自动回复 {answer}（{action_text}）。\n"
        )
    except (OSError, ValueError) as e:
        append_log(
            job_state["log_widget"],
            f"\n状态：检测到覆盖提示，但自动回复失败：{e}\n"
        )
    finally:
        job_state["overwrite_prompt_pending"] = False


def detect_abaqus_command():
    """后台检测 Abaqus 命令是否可用。"""
    def worker():
        available, release_text = check_abaqus_available()
        status = (
            f"Abaqus 状态：已检测到 {release_text}"
            if available and release_text
            else "Abaqus 状态：已检测到"
            if available
            else "Abaqus 状态：未检测到，请检查环境变量"
        )
        root.after(0, abaqus_status_var.set, status)

    threading.Thread(target=worker, daemon=True).start()


def check_abaqus_available(show_error=False):
    """检测 abaqus 命令是否可用。"""
    try:
        result = subprocess.run(
            "abaqus information=release",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False
        )

        output = result.stdout.strip()
        if result.returncode == 0:
            first_line = output.splitlines()[0].strip() if output else ""
            return True, first_line
    except (OSError, subprocess.SubprocessError):
        pass

    if show_error:
        messagebox.showerror(
            "Abaqus 未检测到",
            "未检测到 abaqus 命令，请检查 Abaqus Command 是否加入环境变量。"
        )

    return False, ""

def job_lock_exists(job_state):
    """判断当前作业的 .lck 文件是否存在。只要存在，就认为 Abaqus 仍在运行。"""
    lock_path = os.path.join(job_state["work_dir"], job_state["job_name"] + ".lck")
    return os.path.exists(lock_path)

def open_path(path):
    """打开目录或文件。"""
    if not os.path.exists(path):
        messagebox.showwarning("无法打开", f"路径不存在：\n{path}")
        return

    try:
        os.startfile(path)
    except OSError as e:
        messagebox.showerror("无法打开", str(e))


def open_job_artifact(job_state, extension):
    """打开作业目录或指定结果文件。"""
    if extension == "dir":
        open_path(job_state["work_dir"])
        return

    open_path(os.path.join(job_state["work_dir"], job_state["job_name"] + extension))


def on_close():
    """关闭窗口时保存配置。"""
    save_config()
    root.destroy()


def create_job_log_tab(job_state):
    """为一次作业提交创建独立日志页。"""
    global log_tab_counter
    global right_panel_visible

    job_name = job_state["job_name"]
    log_tab_counter += 1
    tab_title = f"{job_name}"

    if not right_panel_visible:
        root.geometry(FULL_GEOMETRY)
        root.minsize(*FULL_MIN_SIZE)
        body_frame.columnconfigure(0, minsize=LEFT_PANEL_WIDTH, weight=0)
        body_frame.columnconfigure(1, minsize=RIGHT_PANEL_MIN_WIDTH, weight=0)
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel_visible = True

    if log_tab_counter == 1:
        log_notebook.forget(welcome_tab)
    elif log_tab_counter == 2:
        log_notebook.configure(style="TNotebook")

    tab_frame = ttk.Frame(log_notebook, style="Card.TFrame")
    tab_frame.rowconfigure(0, weight=1)
    tab_frame.columnconfigure(0, weight=0)
    tab_frame.columnconfigure(1, weight=1)

    content_frame = ttk.Frame(tab_frame, style="Card.TFrame")
    content_frame.grid(row=0, column=0, sticky="nsw")
    content_frame.rowconfigure(3, weight=1)
    content_frame.columnconfigure(0, weight=0)

    toolbar = ttk.Frame(content_frame, style="Card.TFrame")
    toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
    toolbar.columnconfigure(0, weight=0)
    toolbar.columnconfigure(1, weight=1)

    toolbar_info = ttk.Frame(toolbar, style="Card.TFrame")
    toolbar_info.grid(row=0, column=0, columnspan=2, sticky="ew")
    toolbar_info.columnconfigure(1, weight=1)

    ttk.Label(
        toolbar_info,
        text=f"Job：{job_name}",
        style="Normal.TLabel"
    ).grid(row=0, column=0, sticky="w")

    status_var = tk.StringVar(value="Pending")
    ttk.Label(
        toolbar_info,
        textvariable=status_var,
        style="Hint.TLabel"
    ).grid(row=0, column=1, sticky="w", padx=(16, 0))

    sta_header_label = tk.Label(
        content_frame,
        text="",
        bg=LOG_BG,
        fg="#111827",
        font=FONT_LOG,
        anchor="w",
        justify="left",
        padx=0,
        pady=4
    )

    sta_header_label.grid(row=2, column=0, sticky="w")
    sta_header_label.grid_remove()

    log_widget = tk.Text(
        content_frame,
        height=5,
        width=LOG_TEXT_WIDTH,
        bg="#f9fafb",
        fg="#111827",
        insertbackground="#111827",
        selectbackground="#dbeafe",
        selectforeground="#111827",
        inactiveselectbackground="#e5e7eb",
        relief="flat",
        font=FONT_LOG,
        padx=0,
        pady=8
    )
    log_widget.grid(row=3, column=0, sticky="nsw")
    log_widget.job_tab_frame = tab_frame
    log_widget.job_tab_closed = False
    log_widget.job_state = job_state
    log_widget.bind(
        "<MouseWheel>",
        lambda event: root.after_idle(
            lambda: update_sta_fixed_header_visibility(job_state)
        )
    )

    filebar = ttk.Frame(content_frame, style="Card.TFrame")
    filebar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
    filebar.columnconfigure(4, weight=1)
    filebar.columnconfigure(6, weight=1)
    filebar.columnconfigure(8, weight=1)

    for index, (label, extension) in enumerate(
            (("打开目录", "dir"), ("STA", ".sta"), ("MSG", ".msg"), ("DAT", ".dat"))
    ):
        ctk.CTkButton(
            filebar,
            text=label,
            width=70 if extension == "dir" else 52,
            height=28,
            corner_radius=7,
            font=FONT_HINT,
            fg_color=BTN_LIGHT_FG,
            hover_color=BTN_LIGHT_HOVER,
            text_color=BTN_LIGHT_TEXT,
            bg_color="#ffffff",
            command=lambda ext=extension: open_job_artifact(job_state, ext)
        ).grid(row=0, column=index, sticky="w", padx=(0, 8))

    suspend_btn = ctk.CTkButton(
        filebar,
        text="暂停",
        width=64,
        height=28,
        corner_radius=8,
        font=FONT_BUTTON,
        fg_color=BTN_PAUSE_FG,
        hover_color=BTN_PAUSE_HOVER,
        text_color=BTN_STATUS_TEXT_DARK,
        bg_color="#ffffff",
        command=lambda: toggle_job_suspend(job_state)
    )
    suspend_btn.grid(row=0, column=5)

    terminate_btn = ctk.CTkButton(
        filebar,
        text="终止",
        width=64,
        height=28,
        corner_radius=8,
        font=FONT_BUTTON,
        fg_color="#dc2626",
        hover_color="#b91c1c",
        text_color="white",
        bg_color="#ffffff",
        command=lambda: terminate_job(job_state)
    )
    terminate_btn.grid(row=0, column=7)

    log_notebook.add(tab_frame, text=tab_title)
    log_notebook.select(tab_frame)

    job_state["log_widget"] = log_widget
    job_state["status_var"] = status_var
    job_state["suspend_btn"] = suspend_btn
    job_state["terminate_btn"] = terminate_btn
    job_state["sta_header_label"] = sta_header_label
    job_state["tab_frame"] = tab_frame

    return log_widget


def disable_job_controls(job_state):
    """禁用作业控制按钮。"""
    for button_name in ("suspend_btn", "terminate_btn"):
        button = job_state.get(button_name)
        if button is not None:
            try:
                button.configure(state="disabled")
            except tk.TclError:
                pass

def collapse_right_panel_if_empty():
    """当右侧没有任何作业日志页时，隐藏右侧面板并恢复为左侧单栏。"""
    global right_panel_visible
    global log_tab_counter

    try:
        if not right_panel_visible:
            return

        # 如果仍然存在作业日志页，不收起
        if log_notebook.tabs():
            return

        # 隐藏右侧面板
        right_panel.grid_remove()
        right_panel_visible = False

        # 恢复窗口为左侧单栏
        body_frame.columnconfigure(0, minsize=LEFT_PANEL_WIDTH, weight=0)
        body_frame.columnconfigure(1, minsize=0, weight=0)

        root.geometry(LEFT_ONLY_GEOMETRY)
        root.minsize(*LEFT_ONLY_MIN_SIZE)

        # 重置页签计数，下一次提交作业时重新按第一次提交处理
        log_tab_counter = 0

        # 恢复欢迎页，但继续使用隐藏页签样式
        if str(welcome_tab) not in log_notebook.tabs():
            log_notebook.add(welcome_tab, text="待提交作业")

        log_notebook.configure(style="Hidden.TNotebook")

    except tk.TclError:
        pass

def close_job_log_tab(job_state, delay_ms=2500):
    """作业结束后自动关闭对应运行日志页。"""
    def close_tab():
        try:
            log_widget = job_state.get("log_widget")
            if log_widget is None:
                return

            if getattr(log_widget, "job_tab_closed", False):
                return

            tab_frame = log_widget.job_tab_frame
            log_widget.job_tab_closed = True

            if tab_frame.winfo_exists():
                log_notebook.forget(tab_frame)
                tab_frame.destroy()

            root.after(0, collapse_right_panel_if_empty)

        except tk.TclError:
            pass

    root.after(delay_ms, close_tab)


def should_auto_close_job_log(status):
    """只有明确正常完成的作业才自动关闭运行日志页。"""
    return status in ("完成", "Datacheck Completed")


def notify_job_finished(job_state, status, detail):
    """作业结束提醒。"""
    if not complete_notify_var.get():
        return

    try:
        if os.name == "nt":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except (OSError, RuntimeError):
        pass

    elapsed_text = format_duration(
        job_state["end_time"] - job_state["start_time"]
    ) if job_state.get("start_time") and job_state.get("end_time") else "未知"
    detail_text = f"\n{detail}" if detail else ""
    messagebox.showinfo(
        "作业结束",
        f"作业：{job_state['job_name']}\n"
        f"状态：{status}\n"
        f"耗时：{elapsed_text}{detail_text}"
    )

def format_final_status_for_display(status, detail=""):
    """格式化顶部状态栏的最终显示文本。"""
    display_map = {
        "完成": "计算完成",
        "失败": "计算失败",
        "终止": "已终止",
        "状态未知": "状态未知",
        "Datacheck Completed": "数据检查完成",
        "Datacheck Failed": "数据检查失败",
    }

    display_status = display_map.get(status, status)

    if status in ("Datacheck Completed", "完成"):
        return display_status

    if detail:
        return f"{display_status} | {detail}"

    return display_status

def finalize_job(job_state, status, detail=""):
    """记录作业最终状态，释放运行中保护，并按状态决定是否关闭日志页。"""
    if job_state.get("finalized"):
        return

    job_state["finalized"] = True
    job_state["end_time"] = time.time()
    active_jobs.pop(job_state["job_key"], None)
    disable_job_controls(job_state)
    set_job_status(job_state, format_final_status_for_display(status, detail))
    append_job_final_history(job_state, status, detail)

    log_widget = job_state.get("log_widget")
    if log_widget is not None:
        status_detail = f"状态：{status}{'，' + detail if detail else ''}"
        if should_auto_close_job_log(status):
            append_log(log_widget, f"{status_detail}，运行日志页即将关闭。\n")
        else:
            append_log(log_widget, f"{status_detail}，运行日志页已保留。\n")

    notify_job_finished(job_state, status, detail)
    if should_auto_close_job_log(status):
        close_job_log_tab(job_state)


def send_abaqus_job_control(action, job_state):
    """对指定 Abaqus 作业执行 suspend、resume 或 terminate。"""
    job_name = job_state["job_name"]
    work_dir = job_state["work_dir"]
    log_widget = job_state["log_widget"]
    cmd = f"abaqus {action} job={job_name}"
    action_names = {
        "suspend": "暂停",
        "resume": "恢复",
        "terminate": "终止",
    }
    action_name = action_names[action]
    append_log(log_widget, f"状态：发送{action_name}命令：{cmd}\n")

    try:
        control_process = run_command_hidden(cmd, work_dir)
        start_process_output_monitor(control_process, log_widget)
    except Exception as e:
        append_log(log_widget, f"状态：{action_name}命令执行失败：{e}\n")


def toggle_job_suspend(job_state):
    """在暂停和继续之间切换。"""
    if job_state.get("finalized") or job_state.get("terminating"):
        return

    suspend_btn = job_state["suspend_btn"]

    if job_state.get("suspended"):
        # 当前已经暂停，点击后发送 resume，按钮恢复为黄色“暂停”
        send_abaqus_job_control("resume", job_state)
        job_state["suspended"] = False

        suspend_btn.configure(
            text="暂停",
            fg_color=BTN_PAUSE_FG,
            hover_color=BTN_PAUSE_HOVER,
            text_color=BTN_STATUS_TEXT_DARK
        )

        set_job_status(job_state, format_progress_status(job_state, "Running"))

    else:
        # 当前正在运行，点击后发送 suspend，按钮变为绿色“继续”
        send_abaqus_job_control("suspend", job_state)
        job_state["suspended"] = True

        suspend_btn.configure(
            text="继续",
            fg_color=BTN_RESUME_FG,
            hover_color=BTN_RESUME_HOVER,
            text_color=BTN_STATUS_TEXT_LIGHT
        )

        set_job_status(job_state, format_progress_status(job_state, "Suspended"))


def terminate_job(job_state):
    """终止指定作业，并立即禁用控制按钮。"""
    if job_state.get("finalized") or job_state.get("terminating"):
        return

    job_state["terminating"] = True
    job_state["terminating_at"] = time.time()
    send_abaqus_job_control("terminate", job_state)
    disable_job_controls(job_state)
    set_job_status(job_state, "Terminating")


def start_sta_monitor(sta_path, process, submitted_at, job_state):
    """提交成功后开始读取 Abaqus sta 文件。"""
    monitor_state = {
        "path": sta_path,
        "process": process,
        "position": 0,
        "missing_logged": False,
        "log_widget": job_state["log_widget"],
        "job_state": job_state,
        "lock_path": os.path.splitext(sta_path)[0] + ".lck",
        "last_size": 0,
        "stable_no_lock_polls": 0,
        "seen_sta": False,
        "seen_lock": False,
        "submitted_at": submitted_at,
    }

    if os.path.exists(sta_path) and os.path.getmtime(sta_path) < submitted_at:
        monitor_state["position"] = os.path.getsize(sta_path)
        append_log(job_state["log_widget"], "状态：检测到已有 .sta 文件，将只显示本次提交后的新增进度。\n")

    append_log(job_state["log_widget"], f"状态：开始监控计算进度文件：{sta_path}\n")
    monitor_sta_file(monitor_state)

def start_datacheck_monitor(process, submitted_at, job_state):
    """仅数据检查模式不等待 .sta，改为监控 dat/msg/log。"""
    monitor_state = {
        "process": process,
        "submitted_at": submitted_at,
        "job_state": job_state,
        "log_widget": job_state["log_widget"],
        "last_sizes": {},
        "stable_after_exit_polls": 0,
    }

    append_log(
        job_state["log_widget"],
        "状态：仅数据检查模式，不等待 .sta 文件，开始监控 .dat/.msg/.log。\n"
    )
    monitor_datacheck_files(monitor_state)


def should_hide_console_line(line):
    """过滤子程序编译环境初始化中的冗余控制台输出。"""
    stripped = line.strip()
    if stripped and set(stripped) <= {"*", "="}:
        return True

    upper_line = line.upper()

    hide_markers = (
        "VISUAL STUDIO",
        "DEVELOPER COMMAND PROMPT",
        "COPYRIGHT (C)",
        "[DEBUG:EXT\\VCVARS.BAT]",
        "[VCVARSALL.BAT] ENVIRONMENT INITIALIZED",
        "VARS.BAT DOES NOT SET UP DEPENDENCIES",
        "DPC++ APPLICATIONS",
        "MICROSOFT.VCTOOLSVERSION",
        "VCVARSALL.BAT",
        "VCVARS.BAT",
    )

    return any(marker in upper_line for marker in hide_markers)

def start_process_output_monitor(process, log_widget, job_state=None):
    """后台读取 Abaqus 控制台输出，过滤 VS 编译环境冗余信息后写入日志页。"""
    def read_output():
        if process.stdout is None:
            return

        output_buffer = ""
        line_buffer = ""

        while True:
            chunk = process.stdout.read(1)
            if not chunk:
                break

            # 这个 buffer 仍然保留原始输出，用于识别 Abaqus 覆盖提示
            output_buffer = (output_buffer + chunk)[-1000:]

            if job_state is not None:
                root.after(
                    0,
                    maybe_answer_overwrite_prompt,
                    process,
                    job_state,
                    output_buffer
                )

            # 下面是显示用 buffer，按行过滤
            line_buffer += chunk

            if chunk in ("\n", "\r"):
                line = line_buffer
                line_buffer = ""

                if job_state is not None:
                    root.after(0, cache_console_output, job_state, line)
                    root.after(0, update_abaqus_stage_from_text, job_state, line)
                    root.after(0, maybe_finalize_from_console_output, job_state, line)

                if not should_hide_console_line(line):
                    root.after(0, append_log, log_widget, line)

        # 处理最后一段没有换行的输出
        if line_buffer:
            if job_state is not None:
                root.after(0, cache_console_output, job_state, line_buffer)
                root.after(0, update_abaqus_stage_from_text, job_state, line_buffer)
                root.after(0, maybe_finalize_from_console_output, job_state, line_buffer)

            if not should_hide_console_line(line_buffer):
                root.after(0, append_log, log_widget, line_buffer)

    threading.Thread(target=read_output, daemon=True).start()


def monitor_datacheck_files(monitor_state):
    """监控 datacheck 的 dat/msg/log 文件，并在进程结束后判断状态。"""
    process = monitor_state["process"]
    job_state = monitor_state["job_state"]
    log_widget = monitor_state["log_widget"]
    work_dir = job_state["work_dir"]
    job_name = job_state["job_name"]

    if job_state.get("finalized"):
        return

    # datacheck 主要看这些文件，不依赖 sta
    watched_extensions = (".dat", ".msg", ".log")
    any_file_seen = False
    any_file_changed = False

    for extension in watched_extensions:
        path = os.path.join(work_dir, job_name + extension)
        if not os.path.exists(path):
            continue

        any_file_seen = True

        try:
            size = os.path.getsize(path)
        except OSError:
            continue

        old_size = monitor_state["last_sizes"].get(path, -1)

        if size != old_size:
            any_file_changed = True
            monitor_state["last_sizes"][path] = size

    if any_file_seen:
        job_state["datacheck_phase"] = "Datacheck | Running"
    else:
        job_state["datacheck_phase"] = "Datacheck | Waiting"

    set_job_status(
        job_state,
        format_progress_status(job_state, job_state["datacheck_phase"])
    )

    # 每轮都检查 dat/msg/log 中是否已经出现明确状态
    final_status, detail = inspect_job_files(
        work_dir,
        job_name,
        monitor_state["submitted_at"]
    )

    if final_status:
        finalize_job(job_state, final_status, detail)
        return

    # datacheck 的 abaqus 提交进程结束后，再等待几轮文件稳定
    if process.poll() is not None:
        if any_file_changed:
            monitor_state["stable_after_exit_polls"] = 0
        else:
            monitor_state["stable_after_exit_polls"] += 1

        if monitor_state["stable_after_exit_polls"] >= 3:
            final_status, detail = inspect_job_files(
                work_dir,
                job_name,
                monitor_state["submitted_at"]
            )

            if final_status:
                finalize_job(job_state, final_status, detail)
                return

            if job_state.get("console_failed"):
                finalize_job(
                    job_state,
                    "Datacheck Failed",
                    job_state.get("console_failed_detail", "控制台检测到错误信息")
                )
                return

            console_output = job_state.get("console_output", "")
            final_status, detail = classify_job_text(console_output)

            if final_status:
                finalize_job(job_state, final_status, detail)
            elif process.returncode == 0:
                finalize_job(job_state, "Datacheck Completed", "Datacheck 进程结束，未检测到错误信息")
            else:
                finalize_job(job_state, "Datacheck Failed", f"Datacheck 进程异常结束，返回码 {process.returncode}")
            return

    root.after(
        STA_POLL_INTERVAL_MS,
        lambda: monitor_datacheck_files(monitor_state)
    )

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
                cutback_note = "  cutback"

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

def refresh_runtime_status(job_state):
    """定时刷新实际运行时间。"""
    if job_state.get("finalized"):
        return

    if job_state.get("terminating"):
        set_job_status(job_state, "Terminating")
        return

    prefix = "Suspended" if job_state.get("suspended") else "Running"

    if job_state.get("datacheck_mode"):
        prefix = job_state.get("datacheck_phase", "Datacheck | Running")

    set_job_status(job_state, format_progress_status(job_state, prefix))

    root.after(
        1000,
        lambda: refresh_runtime_status(job_state)
    )


def monitor_sta_file(monitor_state):
    """定时读取单个 sta 文件新增内容并显示到对应日志页。"""
    sta_path = monitor_state["path"]
    process = monitor_state["process"]
    log_widget = monitor_state["log_widget"]
    job_state = monitor_state["job_state"]

    if job_state.get("finalized"):
        return

    if job_state.get("terminating"):
        lock_exists = job_lock_exists(job_state)
        set_job_status(job_state, "Terminating")

        final_status, detail = inspect_job_files(
            job_state["work_dir"],
            job_state["job_name"],
            monitor_state["submitted_at"]
        )
        if final_status:
            finalize_job(job_state, "终止", "手动终止")
            return

        if not lock_exists:
            finalize_job(job_state, "终止", "手动终止，lck文件已释放")
            return

        root.after(
            STA_POLL_INTERVAL_MS,
            lambda: monitor_sta_file(monitor_state)
        )
        return

    if not os.path.exists(sta_path):
        job_state["waiting_sta"] = True

        signals = collect_job_signals(
            job_state,
            monitor_state,
            sta_path,
            process
        )

        if signals["lock_exists"]:
            monitor_state["seen_lock"] = True

        prefix = "Suspended" if job_state.get("suspended") else "Running"
        set_job_status(job_state, format_progress_status(job_state, prefix))

        if not monitor_state["missing_logged"]:
            append_log(log_widget, "状态：等待 Abaqus 生成 .sta 文件...\n")
            monitor_state["missing_logged"] = True

        # 用户主动终止，并且 lck 文件已经释放，才判定终止
        if (
                job_state.get("terminating")
                and time.time() - job_state.get("terminating_at", time.time()) > 20
                and not signals["lock_exists"]
        ):
            finalize_job(job_state, "终止", "终止命令后 lck 文件已释放")
            return

        # Abaqus 询问覆盖旧结果时，如果用户选择否，结束为取消
        if (
                job_state.get("overwrite_answer_sent")
                and not job_state["overwrite_existing"]
        ):
            finalize_job(job_state, "取消", "已有旧结果文件，已选择不覆盖")
            return

        status, detail = decide_job_status_before_sta(signals)

        if status:
            finalize_job(job_state, status, detail)
            return

        root.after(
            STA_POLL_INTERVAL_MS,
            lambda: monitor_sta_file(monitor_state)
        )
        return

    try:
        job_state["waiting_sta"] = False
        current_size = os.path.getsize(sta_path)
        if current_size < monitor_state["position"]:
            monitor_state["position"] = 0

        with open(sta_path, "r", encoding=STA_FILE_ENCODING, errors="replace") as sta_file:
            sta_file.seek(monitor_state["position"])
            new_text = sta_file.read()
            monitor_state["position"] = sta_file.tell()

    except OSError as e:
        append_log(log_widget, f"状态：暂时无法读取 .sta 文件，稍后重试：{e}\n")
        root.after(
            STA_POLL_INTERVAL_MS,
            lambda: monitor_sta_file(monitor_state)
        )
        return

    monitor_state["seen_sta"] = True

    if new_text:
        display_text = format_sta_output_for_log(new_text, job_state)

        if display_text:
            append_log(log_widget, display_text)
        if not new_text.endswith("\n"):
            append_log(log_widget, "\n")

        progress = parse_sta_progress(new_text)
        if progress:
            job_state["progress"] = progress
            prefix = "Suspended" if job_state.get("suspended") else "Running"
            set_job_status(job_state, format_progress_status(job_state, prefix))

    final_status, detail = classify_job_text(new_text)
    if final_status:
        finalize_job(job_state, final_status, detail)
        return

    final_status, detail = inspect_job_files(
        job_state["work_dir"],
        job_state["job_name"],
        monitor_state["submitted_at"]
    )
    if final_status:
        finalize_job(job_state, final_status, detail)
        return

    lock_exists = job_lock_exists(job_state)

    # 只要 .lck 存在，就认为 Abaqus 仍在运行
    if lock_exists:
        monitor_state["seen_lock"] = True
        monitor_state["stable_no_lock_polls"] = 0

        prefix = "Suspended" if job_state.get("suspended") else "Running"
        set_job_status(job_state, format_progress_status(job_state, prefix))

        monitor_state["last_size"] = current_size

        root.after(
            STA_POLL_INTERVAL_MS,
            lambda: monitor_sta_file(monitor_state)
        )
        return

    # 走到这里说明 .lck 已经不存在，再考虑收尾判断
    if current_size == monitor_state["last_size"]:
        monitor_state["stable_no_lock_polls"] += 1
    else:
        monitor_state["stable_no_lock_polls"] = 0

    monitor_state["last_size"] = current_size

    if monitor_state["seen_sta"] and monitor_state["stable_no_lock_polls"] >= 3:
        final_status, detail = inspect_job_files(
            job_state["work_dir"],
            job_state["job_name"],
            monitor_state["submitted_at"]
        )

        if final_status:
            finalize_job(job_state, final_status, detail)
        elif job_state.get("terminating"):
            finalize_job(job_state, "终止", "终止命令后lck文件已释放")
        elif job_state.get("console_failed"):
            finalize_job(
                job_state,
                "失败",
                job_state.get("console_failed_detail", "控制台检测到错误信息")
            )
        else:
            finalize_job(job_state, "状态未知", "lck 文件已释放，但未检测到明确完成或错误信息")
        return


def select_inp_file():
    """选择 Abaqus inp 文件，并自动读取路径和作业名。"""
    file_path = filedialog.askopenfilename(
        title="选择 Abaqus INP 文件",
        filetypes=[
            ("Abaqus INP 文件", "*.inp"),
            ("所有文件", "*.*")
        ]
    )

    if file_path:
        inp_file_var.set(file_path)
        job_name_var.set(os.path.splitext(os.path.basename(file_path))[0])
        set_optional_file_entry(
            inp_file_entry,
            file_path,
            INP_FILE_PLACEHOLDER,
            prefix="INP"
        )
        update_command_preview()


def select_restart_odb():
    """选择重启动作业使用的 odb 文件。"""
    file_path = filedialog.askopenfilename(
        title="选择重启动 ODB 文件",
        filetypes=[
            ("Abaqus ODB 文件", "*.odb"),
            ("所有文件", "*.*")
        ]
    )

    if file_path:
        oldjob_var.set(file_path)
        set_optional_file_entry(
            oldjob_entry,
            file_path,
            OLDJOB_PLACEHOLDER,
            prefix="ODB"
        )
        update_command_preview()


def select_for_file():
    """选择 Abaqus FOR 子程序文件。"""
    file_path = filedialog.askopenfilename(
        title="选择 FOR 子程序文件",
        filetypes=[
            ("FOR 子程序文件", "*.for *.f *.f90"),
            ("所有文件", "*.*")
        ]
    )

    if file_path:
        for_file_var.set(file_path)
        set_optional_file_entry(
            for_file_entry,
            file_path,
            FOR_FILE_PLACEHOLDER,
            prefix="FOR"
        )
        update_command_preview()


def set_optional_file_entry(entry, value, placeholder, prefix=""):
    """显示文件名或占位提示。内部变量仍保存完整路径。"""
    entry.delete(0, tk.END)

    if value:
        filename = os.path.basename(value)
        entry.insert(0, filename)
        entry.configure(fg="#111827", justify="center")
    else:
        entry.insert(0, placeholder)
        entry.configure(fg="#64748b", justify="center")


def select_restart_odb_from_entry(event):
    """点击 ODB 输入框时选择文件。"""
    select_restart_odb()
    return "break"


def select_inp_file_from_entry(event):
    """点击 INP 输入框时选择文件。"""
    select_inp_file()
    return "break"


def select_for_file_from_entry(event):
    """点击 FOR 输入框时选择文件。"""
    select_for_file()
    return "break"


def get_oldjob_name():
    """从 odb 文件路径中读取 oldjob 名称。"""
    oldjob_path = oldjob_var.get().strip()
    return os.path.splitext(os.path.basename(oldjob_path))[0]


def get_custom_memory_value():
    """读取内存输入框中的数值。"""
    try:
        return custom_memory_entry.get().strip()
    except (NameError, tk.TclError):
        return custom_memory_var.get().strip()


def get_memory_argument():
    """读取内存设置。"""
    memory_mode = memory_mode_var.get().strip()
    memory_value = get_custom_memory_value()

    if memory_mode == "默认":
        return ""

    if not memory_value:
        return ""

    if memory_mode == "%":
        return f"{memory_value}%"

    return f"{memory_value}{memory_mode.lower()}"


def update_memory_entry_state(*args):
    """内存输入框始终可选，不输入时按 Abaqus 默认。"""
    try:
        custom_memory_entry.configure(state="normal")
    except (NameError, tk.TclError):
        pass


def validate_memory_argument(memory_argument, show_error=True):
    """检查 Abaqus memory 参数。"""
    if not memory_argument:
        return True

    if re.fullmatch(r"\d+%", memory_argument):
        return True

    if re.fullmatch(r"\d+(\.\d+)?\s*(mb|gb|MB|GB)", memory_argument):
        return True

    if show_error:
        messagebox.showerror(
            "错误",
            "内存参数格式不正确。\n"
            "可使用百分比，例如 90%；或容量，例如 64gb。"
        )
    return False


def build_abaqus_command(
    job_name,
    cpus,
    oldjob_name="",
    for_file_path="",
    interactive_mode=False,
    memory_argument="",
    datacheck_mode=False,
    inp_file="",
    ask_delete_off=False
):
    """根据输入项生成 Abaqus 提交命令。"""
    command_parts = [f"abaqus job={job_name}"]

    if datacheck_mode and inp_file:
        command_parts.append(f"input={os.path.basename(inp_file)}")

    if oldjob_name:
        command_parts.append(f"oldjob={oldjob_name}")

    if for_file_path:
        command_parts.append(f'user="{for_file_path}"')

    if cpus != 0:
        command_parts.append(f"cpus={cpus}")

    if memory_argument:
        command_parts.append(f"memory={memory_argument}")

    if ask_delete_off:
        command_parts.append("ask_delete=OFF")

    if datacheck_mode:
        command_parts.append("datacheck")

    if interactive_mode:
        command_parts.append("interactive")

    return " ".join(command_parts)


def run_command_hidden(cmd, work_dir):
    """在指定目录后台执行命令，Windows 下不显示 cmd 窗口。"""
    popen_kwargs = {
        "cwd": work_dir,
        "shell": True,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "mbcs",
        "errors": "replace",
    }

    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    return subprocess.Popen(cmd, **popen_kwargs)


def update_command_preview(*args):
    """实时更新命令预览"""
    inp_file = inp_file_var.get().strip()
    job_name = job_name_var.get().strip()
    cpus_text = cpus_var.get().strip()
    oldjob_name = get_oldjob_name() if oldjob_var.get().strip() else ""
    for_file_path = for_file_var.get().strip()
    interactive_mode = interactive_var.get()
    memory_argument = get_memory_argument()
    datacheck_mode = datacheck_var.get()

    if not inp_file or not job_name:
        command_var.set("")
        return

    try:
        cpus = int(cpus_text)
    except ValueError:
        command_var.set("")
        return

    if cpus < 0 or cpus > MAX_CPUS:
        command_var.set("")
        return

    if memory_argument and not validate_memory_argument(memory_argument, show_error=False):
        command_var.set("")
        return

    command_var.set(
        build_abaqus_command(
            job_name,
            cpus,
            oldjob_name,
            for_file_path,
            interactive_mode,
            memory_argument,
            datacheck_mode,
            inp_file,
            ask_delete_off=False
        )
    )


def preview_command():
    """生成命令并在提交记录中展示。"""
    update_command_preview()
    cmd = command_var.get().strip()
    job_name = job_name_var.get().strip()
    oldjob_name = get_oldjob_name() if oldjob_var.get().strip() else ""

    if not cmd:
        messagebox.showerror("错误", "请先选择 INP 文件并检查核心数设置。")
        return

    if not validate_abaqus_job_name(job_name):
        return

    if oldjob_name and not validate_abaqus_job_name(oldjob_name, "重启动作业名称"):
        return

    if oldjob_name and oldjob_name == job_name:
        messagebox.showerror(
            "重启动作业名称错误",
            f"当前作业名称和重启动 oldjob 名称不能相同：\n\n"
            f"job = {job_name}\n"
            f"oldjob = {oldjob_name}"
        )
        return

    append_preview_command(cmd)

def get_unique_backup_path(work_dir, base_name, extension, time_tag=None):
    """生成不重名的备份路径。"""
    time_tag = time_tag or time.strftime("%Y%m%d%H%M")
    backup_name = f"{base_name}_bak_{time_tag}{extension}"
    backup_path = os.path.join(work_dir, backup_name)

    index = 1
    while os.path.exists(backup_path):
        backup_name = f"{base_name}_bak_{time_tag}_{index}{extension}"
        backup_path = os.path.join(work_dir, backup_name)
        index += 1

    return backup_path


def backup_existing_sta_file(work_dir, job_name, backup_odb_path="", time_tag=None):
    """将当前作业已有的同名 STA 文件备份，避免读取旧进度。"""
    sta_path = os.path.join(work_dir, job_name + ".sta")

    if not os.path.exists(sta_path):
        return ""

    if backup_odb_path:
        backup_base = os.path.splitext(os.path.basename(backup_odb_path))[0]
        backup_path = os.path.join(work_dir, backup_base + ".sta")
        index = 1
        while os.path.exists(backup_path):
            backup_path = os.path.join(work_dir, f"{backup_base}_{index}.sta")
            index += 1
    else:
        backup_path = get_unique_backup_path(work_dir, job_name, ".sta", time_tag=time_tag)

    os.rename(sta_path, backup_path)
    return backup_path


def delete_existing_sta_file(work_dir, job_name):
    """删除当前作业已有的同名 STA 文件，避免覆盖提交时读取旧进度。"""
    sta_path = os.path.join(work_dir, job_name + ".sta")

    if not os.path.exists(sta_path):
        return ""

    os.remove(sta_path)
    return sta_path

def get_existing_odb_file(work_dir, job_name):
    """只检查当前作业是否已有同名 ODB 文件。"""
    odb_path = os.path.join(work_dir, job_name + ".odb")
    return odb_path if os.path.exists(odb_path) else ""

def backup_existing_odb(odb_path, time_tag=None):
    """将已有 ODB 重命名为 原名_bak_作业时间.odb。"""
    if not odb_path or not os.path.exists(odb_path):
        return ""

    work_dir = os.path.dirname(odb_path)
    base_name = os.path.splitext(os.path.basename(odb_path))[0]
    backup_path = get_unique_backup_path(work_dir, base_name, ".odb", time_tag=time_tag)

    os.rename(odb_path, backup_path)
    return backup_path

def ask_existing_odb_action(job_name, odb_path):
    """检测到同名 ODB 时，返回 overwrite / backup / cancel。"""
    dialog = tk.Toplevel(root)
    dialog.title("已有同名 ODB 文件")
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.grab_set()
    dialog.configure(bg="white")

    result = tk.StringVar(value="cancel")

    main = tk.Frame(dialog, bg="white")
    main.pack(fill="both", expand=True, padx=18, pady=16)

    message = (
        f"检测到作业 “{job_name}” 已经存在同名 ODB 文件：\n\n"
        f"{odb_path}\n\n"
        "请选择处理方式："
    )

    tk.Label(
        main,
        text=message,
        bg="white",
        fg="#111827",
        justify="left",
        anchor="w",
        wraplength=400,
        font=FONT_ENTRY
    ).pack(fill="x", anchor="w", pady=(0, 16))

    button_row = tk.Frame(main, bg="white")
    button_row.pack(fill="x", anchor="w")

    def choose(value):
        result.set(value)
        dialog.destroy()

    ctk.CTkButton(
        button_row,
        text="覆盖",
        width=100,
        height=34,
        corner_radius=8,
        font=FONT_BUTTON,
        fg_color="#dc2626",
        hover_color="#b91c1c",
        text_color="white",
        command=lambda: choose("overwrite")
    ).pack(side="left", padx=(0, 10))

    ctk.CTkButton(
        button_row,
        text="备份",
        width=100,
        height=34,
        corner_radius=8,
        font=FONT_BUTTON,
        fg_color="#2563eb",
        hover_color="#1d4ed8",
        text_color="white",
        command=lambda: choose("backup")
    ).pack(side="left", padx=(0, 10))

    ctk.CTkButton(
        button_row,
        text="取消",
        width=100,
        height=34,
        corner_radius=8,
        font=FONT_BUTTON,
        fg_color="#e5e7eb",
        hover_color="#d1d5db",
        text_color="#111827",
        command=lambda: choose("cancel")
    ).pack(side="left")

    dialog.protocol("WM_DELETE_WINDOW", lambda: choose("cancel"))

    dialog_width = 460
    dialog_height = 210

    root.update_idletasks()
    x = root.winfo_x() + (root.winfo_width() - dialog_width) // 2
    y = root.winfo_y() + (root.winfo_height() - dialog_height) // 2
    dialog.geometry(f"{dialog_width}x{dialog_height}+{max(x, 0)}+{max(y, 0)}")

    root.wait_window(dialog)
    return result.get()

def submit_job():
    """提交 Abaqus 作业"""
    inp_file = inp_file_var.get().strip()
    cpus_text = cpus_var.get().strip()
    oldjob_path = oldjob_var.get().strip()
    oldjob_name = get_oldjob_name() if oldjob_path else ""
    for_file_path = for_file_var.get().strip()
    interactive_mode = interactive_var.get()
    memory_argument = get_memory_argument()
    datacheck_mode = datacheck_var.get()

    if not inp_file:
        messagebox.showerror("错误", "请选择 Abaqus INP 文件。")
        return

    if not os.path.isfile(inp_file):
        messagebox.showerror("错误", f"INP 文件不存在：\n{inp_file}")
        return

    if os.path.splitext(inp_file)[1].lower() != ".inp":
        messagebox.showerror("错误", "请选择 .inp 后缀的文件。")
        return

    work_dir = os.path.dirname(inp_file)
    job_name = os.path.splitext(os.path.basename(inp_file))[0]
    job_name_var.set(job_name)

    if not validate_abaqus_job_name(job_name):
        return

    if oldjob_path:
        if not os.path.isfile(oldjob_path):
            messagebox.showerror("错误", f"ODB 文件不存在：\n{oldjob_path}")
            return

        if os.path.splitext(oldjob_path)[1].lower() != ".odb":
            messagebox.showerror("错误", "请选择 .odb 后缀的文件。")
            return

        if not validate_abaqus_job_name(oldjob_name, "重启动作业名称"):
            return

        if oldjob_name and oldjob_name == job_name:
            messagebox.showerror(
                "重启动作业名称错误",
                f"当前作业名称和重启动 oldjob 名称不能相同：\n\n"
                f"job = {job_name}\n"
                f"oldjob = {oldjob_name}\n\n"
                "请将当前 INP 文件改成新的作业名，例如：\n"
                f"{job_name}_restart.inp"
            )
            return

    if for_file_path:
        if not os.path.isfile(for_file_path):
            messagebox.showerror("错误", f"FOR 子程序文件不存在：\n{for_file_path}")
            return

        if os.path.splitext(for_file_path)[1].lower() not in (".for", ".f", ".f90"):
            messagebox.showerror("错误", "请选择 .for、.f 或 .f90 后缀的子程序文件。")
            return

    try:
        cpus = int(cpus_text)
    except ValueError:
        messagebox.showerror("错误", "核心数必须是整数。")
        return

    if cpus < 0 or cpus > MAX_CPUS:
        messagebox.showerror("错误", f"核心数范围应为 0–{MAX_CPUS}。")
        return

    if not validate_memory_argument(memory_argument):
        return

    sta_file = os.path.join(work_dir, job_name + ".sta")
    job_key = get_job_key(work_dir, job_name)

    if job_key in active_jobs:
        messagebox.showwarning(
            "作业正在运行",
            f"作业“{job_name}”已经在运行中，请等待结束后再提交。"
        )
        return
    lck_file = os.path.join(work_dir, job_name + ".lck")
    if os.path.exists(lck_file):
        messagebox.showwarning(
            "作业可能正在运行",
            f"检测到lck文件：\n{lck_file}\n\n"
            "该作业可能仍在运行或 ODB 正被占用。\n"
            "请确认作业结束后再提交。"
        )
        return

    existing_odb_file = get_existing_odb_file(work_dir, job_name)
    backup_time_tag = ""
    odb_action = ""
    backup_odb_path = ""
    backup_sta_path = ""
    deleted_sta_path = ""

    if existing_odb_file:
        backup_time_tag, _ = get_existing_job_backup_time_tag(
            work_dir,
            job_name,
            existing_odb_file
        )
        odb_action = ask_existing_odb_action(job_name, existing_odb_file)


        if odb_action == "cancel":
            messagebox.showinfo("已取消", "已取消本次提交。")
            return

        if odb_action == "overwrite":
            try:
                os.remove(existing_odb_file)
            except OSError as e:
                messagebox.showerror(
                    "删除失败",
                    f"无法删除原 ODB 文件：\n\n"
                    f"ODB：{existing_odb_file}\n\n"
                    f"错误信息：\n{e}\n\n"
                    "请确认该 ODB 没有被 Abaqus/CAE 打开，也没有正在计算。"
                )
                return

            try:
                deleted_sta_path = delete_existing_sta_file(work_dir, job_name)
            except OSError as e:
                messagebox.showwarning(
                    "旧 STA 删除失败",
                    f"原 ODB 已删除，但旧 STA 文件删除失败：\n\n"
                    f"STA：{os.path.join(work_dir, job_name + '.sta')}\n\n"
                    f"错误信息：\n{e}\n\n"
                    "程序将继续提交作业，但可能会读取到旧 STA。"
                )


        elif odb_action == "backup":
            try:
                backup_odb_path = backup_existing_odb(existing_odb_file, time_tag=backup_time_tag)
            except OSError as e:
                messagebox.showerror(
                    "备份失败",
                    f"无法重命名原 ODB 文件：\n\n"
                    f"ODB：{existing_odb_file}\n\n"
                    f"错误信息：\n{e}\n\n"
                    "请确认该 ODB 没有被 Abaqus/CAE 打开，也没有正在计算。"
                )
                return

            try:
                backup_sta_path = backup_existing_sta_file(
                    work_dir,
                    job_name,
                    backup_odb_path,
                    time_tag=backup_time_tag
                )
            except OSError as e:
                messagebox.showwarning(
                    "旧 STA 备份失败",
                    f"原 ODB 已备份，但旧 STA 文件备份失败：\n\n"
                    f"STA：{os.path.join(work_dir, job_name + '.sta')}\n\n"
                    f"错误信息：\n{e}\n\n"
                    "程序将继续提交作业，但可能会读取到旧 STA。"
                )

    abaqus_available, release_text = check_abaqus_available(show_error=True)
    if not abaqus_available:
        abaqus_status_var.set("Abaqus 状态：未检测到，请检查环境变量")
        return

    abaqus_status_var.set(
        f"Abaqus 状态：已检测到 {release_text}"
        if release_text else "Abaqus 状态：已检测到"
    )

    cmd = build_abaqus_command(
        job_name,
        cpus,
        oldjob_name,
        for_file_path,
        interactive_mode,
        memory_argument,
        datacheck_mode,
        inp_file,
        ask_delete_off=bool(odb_action)
    )
    command_var.set(cmd)

    job_state = {
        "job_key": job_key,
        "job_name": job_name,
        "work_dir": work_dir,
        "inp_file": inp_file,
        "cpus": cpus,
        "oldjob_name": oldjob_name,
        "oldjob_path": oldjob_path,
        "for_file_path": for_file_path,
        "interactive_mode": interactive_mode,
        "memory_argument": memory_argument,
        "datacheck_mode": datacheck_mode,
        "overwrite_existing": True if odb_action == "overwrite" else False,
        "odb_action": odb_action,
        "backup_odb_path": backup_odb_path,
        "backup_sta_path": backup_sta_path,
        "deleted_sta_path": deleted_sta_path,
        "overwrite_answer_sent": False,
        "overwrite_prompt_pending": False,
        "cmd": cmd,
        "console_output": "",
        "console_failed": False,
        "console_failed_detail": "",
        "pre_started": False,
        "pre_finished": False,
        "standard_started": False,
        "process": None,
        "start_time": None,
        "end_time": None,
        "suspended": False,
        "terminating": False,
        "finalized": False,
        "progress": None,
        "waiting_sta": False,
        "sta_separator_printed": False,
        "sta_title_printed": False,
        "sta_header_printed": False,
        "sta_fixed_header_ready": False,
        "sta_header_index": "",
    }
    log_widget = create_job_log_tab(job_state)

    try:
        append_log(log_widget, f"工作目录：{work_dir}\n")
        append_log(log_widget, f"INP 文件：{inp_file}\n")
        append_log(log_widget, f"作业名称：{job_name}\n")

        if oldjob_name:
            append_log(log_widget, f"重启动作业：{oldjob_name}\n")
            append_log(log_widget, f"ODB 文件：{oldjob_path}\n")

        if for_file_path:
            append_log(log_widget, f"FOR 子程序：{for_file_path}\n")

        append_log(log_widget, f"核心数：{cpus if cpus != 0 else '默认'}\n")
        append_log(log_widget, f"内存：{memory_argument if memory_argument else '默认'}\n")
        append_log(log_widget, f"Datacheck：{'是' if datacheck_mode else '否'}\n")

        if odb_action == "overwrite":
            append_log(log_widget, "已有结果处理：检测到同名 ODB，用户选择覆盖，原 ODB 已删除。\n")
        elif odb_action == "backup":
            append_log(log_widget, f"已有结果处理：检测到同名 ODB，原 ODB 已备份为：{backup_odb_path}\n")
        else:
            append_log(log_widget, "已有结果处理：未检测到同名 ODB。\n")

        if backup_sta_path:
            append_log(log_widget, f"旧 STA 已备份为：{backup_sta_path}\n")
        if deleted_sta_path:
            append_log(log_widget, f"旧 STA 已删除：{deleted_sta_path}\n")

        append_log(log_widget, f"提交命令：{cmd}\n")
        append_log(log_widget, "状态：正在提交作业...\n")

        submitted_at = time.time()
        job_state["start_time"] = submitted_at
        process = run_command_hidden(cmd, work_dir)
        job_state["process"] = process
        active_jobs[job_key] = job_state
        refresh_runtime_status(job_state)

        append_log(log_widget, "状态：提交命令已发送，正在等待 Abaqus 响应。\n")
        start_process_output_monitor(process, log_widget, job_state)
        append_submit_history(job_state)

        if datacheck_mode:
            set_job_status(job_state, "Datacheck | Running")
            start_datacheck_monitor(process, submitted_at, job_state)
        else:
            set_job_status(job_state, "Running")
            start_sta_monitor(sta_file, process, submitted_at, job_state)

        messagebox.showinfo("已提交", "Abaqus 作业已后台提交。")

    except Exception as e:
        messagebox.showerror("提交失败", str(e))
        append_log(log_widget, f"提交失败：{e}\n")
        finalize_job(job_state, "失败", "提交命令执行失败")


# ================= 主窗口 =================

root = tk.Tk()
root.title("Abaqus 作业提交工具")
root.geometry(LEFT_ONLY_GEOMETRY)
root.minsize(*LEFT_ONLY_MIN_SIZE)
root.resizable(True, True)
root.configure(bg=APP_BG)
root.option_add("*Font", FONT_ENTRY)
saved_config = load_config()

# ================= 变量 =================

inp_file_var = tk.StringVar()
job_name_var = tk.StringVar()
cpus_var = tk.StringVar(value=saved_config.get("cpus", str(DEFAULT_CPUS)))
command_var = tk.StringVar()
oldjob_var = tk.StringVar()
for_file_var = tk.StringVar()
saved_memory_mode = saved_config.get("memory_mode", "默认")
saved_custom_memory = saved_config.get("custom_memory", "")
if saved_memory_mode in ("70%", "80%", "90%"):
    saved_custom_memory = saved_memory_mode.rstrip("%")
    saved_memory_mode = "%"
elif saved_memory_mode == "自定义":
    saved_memory_mode = "%"
memory_mode_var = tk.StringVar(value=saved_memory_mode)
custom_memory_var = tk.StringVar(value=saved_custom_memory)
interactive_var = tk.BooleanVar(value=saved_config.get("interactive", False))
datacheck_var = tk.BooleanVar(value=saved_config.get("datacheck", False))
complete_notify_var = tk.BooleanVar(value=saved_config.get("complete_notify", True))
abaqus_status_var = tk.StringVar(value="Abaqus 状态：检测中...")

inp_file_var.trace_add("write", update_command_preview)
job_name_var.trace_add("write", update_command_preview)
cpus_var.trace_add("write", update_command_preview)
oldjob_var.trace_add("write", update_command_preview)
for_file_var.trace_add("write", update_command_preview)
memory_mode_var.trace_add("write", update_command_preview)
memory_mode_var.trace_add("write", update_memory_entry_state)
custom_memory_var.trace_add("write", update_command_preview)

# ================= customtkinter 设置 =================

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ================= ttk 样式 =================

style = ttk.Style()
style.theme_use("clam")

style.configure(
    "Main.TFrame",
    background=APP_BG
)

style.configure(
    "Card.TFrame",
    background=CARD_BG,
    relief="flat"
)

style.configure(
    "Title.TLabel",
    background=APP_BG,
    foreground="#111827",
    font=FONT_TITLE
)

style.configure(
    "SubTitle.TLabel",
    background=APP_BG,
    foreground="#64748b",
    font=FONT_SUBTITLE
)

style.configure(
    "Normal.TLabel",
    background="#ffffff",
    foreground="#111827",
    font=FONT_LABEL
)

style.configure(
    "Hint.TLabel",
    background="#ffffff",
    foreground="#64748b",
    font=FONT_HINT
)

style.configure(
    "TEntry",
    padding=6,
    font=FONT_ENTRY
)

style.configure(
    "TNotebook",
    background=APP_BG,
    borderwidth=0,
    tabmargins=(0, 0, 0, 0)
)

style.configure(
    "TNotebook.Tab",
    padding=(8, 3),
    font=(FONT_FAMILY, 9),
    background="#f1f5f9",
    foreground="#334155",
    borderwidth=0,
    focuscolor=APP_BG
)

style.map(
    "TNotebook.Tab",
    background=[
        ("selected", "#dbeafe"),
        ("active", "#e0f2fe"),
        ("!selected", "#f1f5f9")
    ],
    foreground=[
        ("selected", "#1e3a8a"),
        ("active", "#1e40af"),
        ("!selected", "#334155")
    ]
)

style.configure(
    "Hidden.TNotebook",
    background="#ffffff",
    borderwidth=0
)

style.layout("Hidden.TNotebook.Tab", [])

# ================= 页面布局 =================

main_frame = ttk.Frame(root, style="Main.TFrame")
main_frame.pack(fill="both", expand=True, padx=12, pady=18)

body_frame = ttk.Frame(main_frame, style="Main.TFrame")
body_frame.pack(fill="both", expand=True)
body_frame.columnconfigure(0, minsize=LEFT_PANEL_WIDTH, weight=0)
body_frame.columnconfigure(1, minsize=0, weight=0)
body_frame.rowconfigure(0, weight=1)

left_panel = ttk.Frame(body_frame, style="Main.TFrame", width=LEFT_PANEL_WIDTH)
left_panel.grid(row=0, column=0, sticky="nsw", padx=(0, 0))
left_panel.grid_propagate(False)
left_panel.pack_propagate(False)

right_panel = ttk.Frame(body_frame, style="Main.TFrame")

# ================= 左侧提交表单 =================

card = ttk.Frame(left_panel, style="Card.TFrame")
card.pack(fill="x", pady=(0, 14))

ttk.Label(
    card,
    textvariable=abaqus_status_var,
    style="Hint.TLabel"
).pack(anchor="w", padx=16, pady=(12, 0))

inner = ttk.Frame(card, style="Card.TFrame")
inner.pack(fill="x", padx=16, pady=(12, 16))
inner.columnconfigure(0, weight=0)
inner.columnconfigure(1, weight=1)

ttk.Label(
    inner,
    text="INP",
    width=4,
    anchor="w",
    style="Normal.TLabel"
).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 18))

inp_file_entry = tk.Entry(
    inner,
    font=FONT_ENTRY,
    bg="#ffffff",
    fg="#64748b",
    relief="solid",
    bd=1,
    highlightthickness=0,
    width=40
)
inp_file_entry.grid(row=0, column=1, sticky="ew", ipady=6, pady=(0, 18))
set_optional_file_entry(
    inp_file_entry,
    "",
    INP_FILE_PLACEHOLDER,
    prefix="INP"
)
inp_file_entry.bind("<Button-1>", select_inp_file_from_entry)


ttk.Label(
    inner,
    text="ODB",
    width=4,
    anchor="w",
    style="Normal.TLabel"
).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(0, 18))

oldjob_entry = tk.Entry(
    inner,
    font=FONT_ENTRY,
    bg="#ffffff",
    fg="#64748b",
    relief="solid",
    bd=1,
    highlightthickness=0,
    width=40
)
oldjob_entry.grid(row=1, column=1, sticky="ew", ipady=6, pady=(0, 18))
set_optional_file_entry(
    oldjob_entry,
    "",
    OLDJOB_PLACEHOLDER,
    prefix="ODB"
)
oldjob_entry.bind("<Button-1>", select_restart_odb_from_entry)


ttk.Label(
    inner,
    text="FOR",
    width=4,
    anchor="w",
    style="Normal.TLabel"
).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(0, 20))

for_file_entry = tk.Entry(
    inner,
    font=FONT_ENTRY,
    bg="#ffffff",
    fg="#64748b",
    relief="solid",
    bd=1,
    highlightthickness=0,
    width=40
)
for_file_entry.grid(row=2, column=1, sticky="ew", ipady=6, pady=(0, 20))
set_optional_file_entry(
    for_file_entry,
    "",
    FOR_FILE_PLACEHOLDER,
    prefix="FOR"
)
for_file_entry.bind("<Button-1>", select_for_file_from_entry)

settings_row = ttk.Frame(inner, style="Card.TFrame")
settings_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 16))
settings_row.columnconfigure(3, weight=1)

ttk.Label(
    settings_row,
    text="核心数",
    style="Normal.TLabel"
).grid(row=0, column=0, sticky="w")

cpus_entry = ctk.CTkEntry(
    settings_row,
    textvariable=cpus_var,
    width=68,
    height=34,
    corner_radius=0,
    border_width=1,
    border_color="#9ca3af",
    fg_color="#ffffff",
    text_color="#111827",
    justify="center",
    font=FONT_NUMERIC_ENTRY
)
cpus_entry.grid(row=0, column=1, sticky="w", padx=(8, 8))

ttk.Label(
    settings_row,
    text=f"最大 {MAX_CPUS}",
    style="Hint.TLabel"
).grid(row=0, column=2, sticky="w")

ttk.Label(
    settings_row,
    text="内存",
    style="Normal.TLabel"
).grid(row=0, column=4, sticky="e", padx=(16, 6))

memory_group = ttk.Frame(settings_row, style="Card.TFrame")
memory_group.grid(row=0, column=5, sticky="e")

custom_memory_entry = ctk.CTkEntry(
    memory_group,
    width=52,
    height=34,
    corner_radius=0,
    border_width=1,
    border_color="#9ca3af",
    fg_color="#ffffff",
    text_color="#111827",
    placeholder_text="可选",
    placeholder_text_color="#64748b",
    justify="center",
    font=FONT_NUMERIC_ENTRY
)
custom_memory_entry.grid(row=0, column=0, sticky="w")
if custom_memory_var.get().strip():
    custom_memory_entry.insert(0, custom_memory_var.get().strip())
custom_memory_entry.bind("<KeyRelease>", lambda event: update_command_preview())

memory_combo = ctk.CTkOptionMenu(
    memory_group,
    variable=memory_mode_var,
    values=MEMORY_OPTIONS,
    width=72,
    height=34,
    corner_radius=0,
    fg_color="#ffffff",
    button_color="#ffffff",
    button_hover_color="#f3f4f6",
    text_color="#111827",
    dropdown_fg_color="#ffffff",
    dropdown_hover_color="#e5e7eb",
    dropdown_text_color="#111827",
    font=FONT_MEMORY_MENU,
    dropdown_font=FONT_MEMORY_MENU,
    anchor="center",
    dynamic_resizing=False,
    command=lambda _: update_command_preview()
)
memory_combo.grid(row=0, column=1, sticky="w")

options_row = ttk.Frame(inner, style="Card.TFrame")
options_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 16))

options_row.columnconfigure(0, weight=1, uniform="options")
options_row.columnconfigure(1, weight=1, uniform="options")
options_row.columnconfigure(2, weight=1, uniform="options")

option_cell_1 = ttk.Frame(options_row, style="Card.TFrame")
option_cell_2 = ttk.Frame(options_row, style="Card.TFrame")
option_cell_3 = ttk.Frame(options_row, style="Card.TFrame")

option_cell_1.grid(row=0, column=0, sticky="ew")
option_cell_2.grid(row=0, column=1, sticky="ew")
option_cell_3.grid(row=0, column=2, sticky="ew")

interactive_check = ctk.CTkCheckBox(
    option_cell_1,
    text="交互输出",
    variable=interactive_var,
    onvalue=True,
    offvalue=False,
    checkbox_width=16,
    checkbox_height=16,
    corner_radius=4,
    font=FONT_HINT,
    fg_color="#2563eb",
    hover_color="#1d4ed8",
    text_color="#111827",
    bg_color="#ffffff",
    command=update_command_preview
)
interactive_check.pack(anchor="center")

datacheck_check = ctk.CTkCheckBox(
    option_cell_2,
    text="仅数据检查",
    variable=datacheck_var,
    onvalue=True,
    offvalue=False,
    checkbox_width=16,
    checkbox_height=16,
    corner_radius=4,
    font=FONT_HINT,
    fg_color="#2563eb",
    hover_color="#1d4ed8",
    text_color="#111827",
    bg_color="#ffffff",
    command=update_command_preview
)
datacheck_check.pack(anchor="center")

notify_check = ctk.CTkCheckBox(
    option_cell_3,
    text="结束提醒",
    variable=complete_notify_var,
    onvalue=True,
    offvalue=False,
    checkbox_width=16,
    checkbox_height=16,
    corner_radius=4,
    font=FONT_HINT,
    fg_color="#2563eb",
    hover_color="#1d4ed8",
    text_color="#111827",
    bg_color="#ffffff"
)
notify_check.pack(anchor="center")

button_row = ttk.Frame(inner, style="Card.TFrame")
button_row.grid(row=5, column=0, columnspan=2, sticky="w")

generate_cmd_btn = ctk.CTkButton(
    button_row,
    text="预览命令",
    width=176,
    height=38,
    corner_radius=8,
    font=FONT_BUTTON_BOLD,
    fg_color=BTN_LIGHT_FG,
    hover_color=BTN_LIGHT_HOVER,
    text_color=BTN_LIGHT_TEXT,
    bg_color="#ffffff",
    command=preview_command
)
generate_cmd_btn.grid(row=0, column=0, sticky="w", padx=(0, 16))

submit_btn = ctk.CTkButton(
    button_row,
    text="提交作业",
    width=176,
    height=38,
    corner_radius=8,
    font=FONT_BUTTON_BOLD,
    fg_color="#2563eb",
    hover_color="#1d4ed8",
    text_color="white",
    bg_color="#ffffff",
    command=submit_job
)
submit_btn.grid(row=0, column=1, sticky="w")

# ================= 左侧提交记录 =================

history_card = ttk.Frame(left_panel, style="Card.TFrame")
history_card.pack(fill="both", expand=True)

history_inner = ttk.Frame(history_card, style="Card.TFrame")
history_inner.pack(fill="both", expand=True, padx=18, pady=14)
history_inner.rowconfigure(1, weight=1)
history_inner.columnconfigure(0, weight=1)

ttk.Label(
    history_inner,
    text="提交记录",
    style="Normal.TLabel"
).grid(row=0, column=0, sticky="w", pady=(0, 8))

history_text = tk.Text(
    history_inner,
    height=8,
    width=44,
    bg=LOG_BG,
    fg="#111827",
    insertbackground="#111827",
    relief="flat",
    font=FONT_LOG,
    padx=10,
    pady=8,
    wrap="word"
)
history_text.tag_configure(
    "history_time",
    font=("Consolas", 10, "bold"),
    foreground="#2563eb"
)
history_text.grid(row=1, column=0, sticky="nsew")
history_text.insert(tk.END, "等待提交作业...\n")

# ================= 右侧运行日志 =================

log_card = ttk.Frame(right_panel, style="Card.TFrame")
log_card.pack(fill="both", expand=True)

log_inner = ttk.Frame(log_card, style="Card.TFrame")
log_inner.pack(fill="both", expand=True, padx=18, pady=14)

ttk.Label(
    log_inner,
    text="作业运行情况",
    style="Normal.TLabel"
).pack(anchor="w", pady=(0, 8))

log_notebook = ttk.Notebook(log_inner, style="Hidden.TNotebook")
log_notebook.pack(fill="both", expand=True)

welcome_tab = ttk.Frame(log_notebook, style="Card.TFrame")
welcome_tab.rowconfigure(0, weight=1)
welcome_tab.columnconfigure(0, weight=1)

welcome_text = tk.Text(
    welcome_tab,
    height=5,
    width=LOG_TEXT_WIDTH,
    bg="#f9fafb",
    fg="#111827",
    insertbackground="#111827",
    relief="flat",
    font=FONT_LOG,
    padx=10,
    pady=8
)
welcome_text.grid(row=0, column=0, sticky="nsew")
welcome_text.insert(tk.END, "提交作业后，将在这里显示对应作业的运行日志。\n")

log_notebook.add(welcome_tab, text="待提交作业")

# ================= 启动程序 =================

detect_abaqus_command()
root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()
