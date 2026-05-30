import ctypes
import codecs
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

try:
    import psutil
except ImportError:
    psutil = None


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
MAX_THREADS = os.cpu_count() or MAX_CPUS
DEFAULT_CPUS = max(1, MAX_CPUS // 2)
STA_POLL_INTERVAL_MS = 5000
RUNTIME_STATUS_INTERVAL_MS = 3000
OUTPUT_FLUSH_INTERVAL_MS = 150
ABAQUS_MEMORY_POLL_INTERVAL_SECONDS = 10
JOB_MEMORY_MONITOR_INTERVAL_MS = 15000
JOB_MEMORY_MIN_SAMPLES = 4
JOB_MEMORY_STABLE_POLLS = 4
JOB_MEMORY_STABLE_RELATIVE_DELTA = 0.08
JOB_MEMORY_BASE_SAFETY_FACTOR = 1.10
JOB_MEMORY_MAX_SAFETY_FACTOR = 1.50
JOB_MEMORY_SAFETY_FACTOR_STEP = 0.10
JOB_MEMORY_MAX_SAMPLES = 40
STA_FILE_ENCODING = "GBK"
LEFT_PANEL_WIDTH = 416
LOG_SEPARATOR_WIDTH = 64
LOG_TEXT_WIDTH = LOG_SEPARATOR_WIDTH
LOG_TEXT_PIXEL_WIDTH = 488
RIGHT_PANEL_HORIZONTAL_PADDING = 0
RIGHT_PANEL_MIN_WIDTH = LOG_TEXT_PIXEL_WIDTH + RIGHT_PANEL_HORIZONTAL_PADDING
UNLIMITED_JOB_SLOTS = 10 ** 9
WINDOW_HORIZONTAL_PADDING = 24
WINDOW_VERTICAL_PADDING = 24
WINDOW_HEIGHT = 720
PANEL_HEIGHT = WINDOW_HEIGHT - WINDOW_VERTICAL_PADDING
LEFT_ONLY_WIDTH = LEFT_PANEL_WIDTH + WINDOW_HORIZONTAL_PADDING
FULL_WIDTH = LEFT_PANEL_WIDTH + RIGHT_PANEL_MIN_WIDTH + WINDOW_HORIZONTAL_PADDING + 16
LEFT_ONLY_GEOMETRY = f"{LEFT_ONLY_WIDTH}x{WINDOW_HEIGHT}"
FULL_GEOMETRY = f"{FULL_WIDTH}x{WINDOW_HEIGHT}"
LEFT_ONLY_MIN_SIZE = (LEFT_ONLY_WIDTH, WINDOW_HEIGHT)
FULL_MIN_SIZE = (FULL_WIDTH, WINDOW_HEIGHT)
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


def calculate_default_joblist_parallel(cpus=None):
    """Return default queue parallel count from 1.5x logical threads and per-job cores."""
    if cpus is None:
        cpus = DEFAULT_CPUS

    try:
        cpus = int(str(cpus).strip())
    except (TypeError, ValueError):
        cpus = DEFAULT_CPUS

    requested_cpus = MAX_CPUS if cpus == 0 else max(1, cpus)
    return max(1, (MAX_THREADS * 3) // (2 * requested_cpus))

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
ABAQUS_PROCESS_NAME_MARKERS = (
    "abaqus",
    "standard",
    "explicit",
    "pre",
    "package",
    "sma",
    "solver",
    "mpiexec",
)

# ================= 字体统一设置 =================
FONT_FAMILY = "Microsoft YaHei"

FONT_TITLE = (FONT_FAMILY, 19, "bold")
FONT_SUBTITLE = (FONT_FAMILY, 9)
FONT_LABEL = (FONT_FAMILY, 12, "bold")
FONT_HINT = (FONT_FAMILY, 10)
FONT_ENTRY = (FONT_FAMILY, 10)
FONT_NUMERIC_ENTRY = (FONT_FAMILY, 12)
FONT_MEMORY_MENU = (FONT_FAMILY, 11)
FONT_BUTTON = (FONT_FAMILY, 12)
FONT_BUTTON_BOLD = (FONT_FAMILY, 13, "bold")
FONT_LOG = ("Consolas", 10)

APP_BG = "#ffffff"
CARD_BG = "#ffffff"
LOG_BG = "#f9fafb"
JOBLIST_FILENAME = "joblist.json"
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
diagnostic_file_cache = {}
joblist_state = {
    "active": False,
    "work_dir": "",
    "jobs": [],
    "statuses": {},
    "running": set(),
    "joblist_path": "",
    "max_parallel": 1,
    "restart_jobs": [],
    "oldjob_paths": {},
    "dependencies": {},
    "existing_odb_action": "",
}
job_tab_records = {}
job_selector_var = None
job_selector = None
job_stats_var = None
abaqus_memory_cache = {
    "timestamp": 0.0,
    "usage": {},
}
memory_monitor_state = {
    "running": False,
}
runtime_status_state = {
    "running": False,
}
job_memory_estimates = {}
memory_safety_factor_state = {
    "value": JOB_MEMORY_BASE_SAFETY_FACTOR,
}


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


def get_system_memory_info():
    """读取系统总物理内存和当前可用物理内存。"""
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)

        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys), int(status.ullAvailPhys)
        except (AttributeError, OSError):
            pass

    return 0, 0


def format_memory_size(size_bytes):
    """格式化内存大小。"""
    if size_bytes <= 0:
        return "未知"

    gib = size_bytes / 1024 ** 3
    if gib >= 1:
        return f"{gib:.1f} GB"

    return f"{size_bytes / 1024 ** 2:.0f} MB"


def format_job_slot_count(slot_count):
    """格式化队列并行数估算。"""
    if slot_count >= UNLIMITED_JOB_SLOTS:
        return "不限制"

    return str(slot_count)


def text_display_width(text):
    """Return approximate monospace display width, counting CJK as double width."""
    width = 0
    for char in str(text):
        width += 2 if "\u4e00" <= char <= "\u9fff" else 1

    return width


def pad_display_text(text, width):
    """Pad text so table separators align in the history log."""
    text = str(text)
    padding = max(0, width - text_display_width(text))
    return text + " " * padding


def format_history_table_row(values, widths):
    """Format a compact pipe-separated history table row."""
    return " | ".join(
        pad_display_text(value, width)
        for value, width in zip(values, widths)
    )


def get_memory_safety_factor():
    """Return current adaptive memory safety factor."""
    return float(memory_safety_factor_state.get("value", JOB_MEMORY_BASE_SAFETY_FACTOR))


def increase_memory_safety_factor(reason=""):
    """Increase memory safety factor after memory-related job failures."""
    current = get_memory_safety_factor()
    updated = min(JOB_MEMORY_MAX_SAFETY_FACTOR, current + JOB_MEMORY_SAFETY_FACTOR_STEP)
    if updated <= current:
        return

    memory_safety_factor_state["value"] = updated
    append_history_text(
        f"检测到疑似内存相关失败，内存安全系数已调整：{current:.2f} -> {updated:.2f}"
        f"{' | ' + reason if reason else ''}\n\n"
    )


def is_memory_related_failure(status, detail=""):
    """Return True when a failed job likely stopped due to memory pressure."""
    if status not in ("失败", "Datacheck Failed", "状态未知"):
        return False

    text = (detail or "").lower()
    markers = (
        "memory",
        "out of memory",
        "insufficient memory",
        "allocation",
        "allocate",
        "std_alloc",
        "virtual memory",
        "pagefile",
        "page file",
        "system error code",
    )
    return any(marker in text for marker in markers)


def infer_model_group(job_name):
    """Best-effort model group name; never required for memory estimation."""
    if not job_name:
        return ""

    if "_" in job_name:
        return job_name.split("_", 1)[0]

    return ""


def get_job_memory_step(job_state):
    """Return current Abaqus step for step-level memory peaks."""
    progress = job_state.get("progress") or {}
    step = progress.get("step")
    return str(step) if step not in (None, "") else "unknown"


def get_current_abaqus_memory_total(usage_by_job):
    """Return total memory currently used by all detected Abaqus jobs."""
    total = 0
    for usage in usage_by_job.values():
        total += int(usage.get("private_memory") or usage.get("working_set") or 0)

    return total


def parse_job_name_from_command_line(command_line):
    """Extract Abaqus job name from a process command line."""
    if not command_line:
        return ""

    patterns = (
        r'(?i)(?:^|\s)-?job\s*=\s*["\']?([^"\'\s]+)',
        r'(?i)(?:^|\s)-job\s+["\']?([^"\'\s]+)',
        r'(?i)(?:^|\s)-?input\s*=\s*["\']?([^"\'\s]+)',
    )

    for pattern in patterns:
        match = re.search(pattern, command_line)
        if match:
            return os.path.splitext(os.path.basename(match.group(1)))[0]

    return ""


def find_process_abaqus_job_name(process_row, process_by_pid):
    """Trace process parents until a command line containing job/input is found."""
    try:
        current_pid = int(process_row.get("ProcessId") or 0)
    except (TypeError, ValueError):
        return ""

    visited = set()

    while current_pid and current_pid in process_by_pid:
        if current_pid in visited:
            break

        visited.add(current_pid)
        current_process = process_by_pid[current_pid]
        job_name = parse_job_name_from_command_line(
            current_process.get("CommandLine") or ""
        )
        if job_name:
            return job_name

        try:
            current_pid = int(current_process.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            break

    return ""


def fetch_psutil_process_rows():
    """Read process rows with psutil, avoiding PowerShell startup overhead."""
    if psutil is None:
        return []

    rows = []
    rows_by_pid = {}
    for process in psutil.process_iter(["pid", "ppid", "name", "memory_info"]):
        try:
            info = process.info
            memory_info = info.get("memory_info")
            row = {
                "Name": info.get("name") or "",
                "ProcessId": info.get("pid") or 0,
                "ParentProcessId": info.get("ppid") or 0,
                "CommandLine": "",
                "WorkingSetSize": getattr(memory_info, "rss", 0) if memory_info else 0,
                "PrivatePageCount": getattr(memory_info, "private", 0) if memory_info else 0,
            }
            rows.append(row)
            rows_by_pid[int(row["ProcessId"])] = row
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue

    cmdline_pids = set()
    for row in rows:
        process_name = str(row.get("Name") or "").lower()
        if not any(marker in process_name for marker in ABAQUS_PROCESS_NAME_MARKERS):
            continue

        current_pid = int(row.get("ProcessId") or 0)
        visited = set()
        while current_pid and current_pid in rows_by_pid and current_pid not in visited:
            visited.add(current_pid)
            cmdline_pids.add(current_pid)
            current_pid = int(rows_by_pid[current_pid].get("ParentProcessId") or 0)

    for pid in cmdline_pids:
        try:
            cmdline = psutil.Process(pid).cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue

        if isinstance(cmdline, (list, tuple)):
            command_line = " ".join(str(part) for part in cmdline)
        else:
            command_line = str(cmdline or "")

        if pid in rows_by_pid:
            rows_by_pid[pid]["CommandLine"] = command_line

    return rows


def fetch_windows_process_rows():
    """Read Windows process rows through PowerShell CIM."""
    if os.name != "nt":
        return []

    powershell_command = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_Process | "
        "Select-Object Name,ProcessId,ParentProcessId,CommandLine,WorkingSetSize,PrivatePageCount | "
        "ConvertTo-Json -Compress -Depth 3"
    )

    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                powershell_command,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if result.returncode != 0 or not result.stdout.strip():
        return []

    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    if isinstance(rows, dict):
        return [rows]

    if isinstance(rows, list):
        return rows

    return []


def get_abaqus_job_memory_usage(force=False):
    """Return memory usage grouped by Abaqus job name."""
    now = time.monotonic()
    if (
            not force
            and abaqus_memory_cache["usage"]
            and now - abaqus_memory_cache["timestamp"] < ABAQUS_MEMORY_POLL_INTERVAL_SECONDS
    ):
        return abaqus_memory_cache["usage"]

    rows = fetch_psutil_process_rows()
    if not rows:
        rows = fetch_windows_process_rows()

    process_by_pid = {}
    for row in rows:
        try:
            process_by_pid[int(row.get("ProcessId") or 0)] = row
        except (TypeError, ValueError):
            continue

    usage_by_job = {}

    for row in rows:
        job_name = find_process_abaqus_job_name(row, process_by_pid)
        if not job_name:
            continue

        try:
            working_set = int(row.get("WorkingSetSize") or 0)
        except (TypeError, ValueError):
            working_set = 0

        try:
            private_memory = int(row.get("PrivatePageCount") or 0)
        except (TypeError, ValueError):
            private_memory = 0

        usage = usage_by_job.setdefault(
            job_name,
            {
                "working_set": 0,
                "private_memory": 0,
                "process_count": 0,
                "process_names": set(),
            }
        )
        usage["working_set"] += working_set
        usage["private_memory"] += private_memory
        usage["process_count"] += 1
        if row.get("Name"):
            usage["process_names"].add(row["Name"])

    for usage in usage_by_job.values():
        usage["process_names"] = ", ".join(sorted(usage["process_names"]))

    abaqus_memory_cache["timestamp"] = now
    abaqus_memory_cache["usage"] = usage_by_job
    return usage_by_job


def estimate_per_job_memory_from_running_jobs():
    """Estimate one Abaqus job memory need from saved peaks and running samples."""
    usage_by_job = get_abaqus_job_memory_usage()
    active_job_names = {
        state.get("job_name", "")
        for state in active_jobs.values()
        if not state.get("finalized")
    }

    samples = []

    for job_name, estimate in job_memory_estimates.items():
        if active_job_names and job_name in active_job_names:
            continue

        memory = int(estimate.get("estimated_memory") or estimate.get("peak_memory") or 0)
        if memory > 0:
            samples.append(memory)

        for step_peak in (estimate.get("step_peaks") or {}).values():
            step_memory = int(step_peak or 0)
            if step_memory > 0:
                samples.append(int(step_memory * get_memory_safety_factor()))

    for job_name, usage in usage_by_job.items():
        if active_job_names and job_name not in active_job_names:
            continue

        memory = int(usage.get("private_memory") or usage.get("working_set") or 0)
        if memory > 0:
            samples.append(memory)

    if not samples:
        return 0, 0, {}

    return max(samples), len(samples), usage_by_job


def update_job_memory_sample(job_state, usage):
    """Update one running job from a shared memory sample."""
    if (
            job_state.get("finalized")
            or job_state.get("memory_monitor_stopped")
            or not usage
    ):
        return

    job_name = job_state["job_name"]
    memory = int(usage.get("private_memory") or usage.get("working_set") or 0)
    if memory <= 0:
        return

    samples = job_state.setdefault("memory_samples", [])
    peak_before = int(job_state.get("memory_peak", 0))
    peak_after = max(peak_before, memory)
    job_state["memory_peak"] = peak_after
    samples.append(memory)
    if len(samples) > JOB_MEMORY_MAX_SAMPLES:
        del samples[:-JOB_MEMORY_MAX_SAMPLES]

    step = get_job_memory_step(job_state)
    estimate = job_memory_estimates.setdefault(
        job_name,
        {
            "group": infer_model_group(job_name),
            "step_peaks": {},
        }
    )
    step_peaks = estimate.setdefault("step_peaks", {})
    step_peaks[step] = max(int(step_peaks.get(step, 0)), memory)

    previous_estimate = estimate.get("estimated_memory", 0)
    estimated_memory = int(peak_after * get_memory_safety_factor())
    estimate.update(
        {
            "estimated_memory": max(estimated_memory, previous_estimate),
            "peak_memory": peak_after,
            "sample_count": len(samples),
            "process_count": usage.get("process_count", 0),
            "process_names": usage.get("process_names", ""),
            "updated_at": time.time(),
            "stable": False,
        }
    )

    if peak_after > peak_before * (1 + JOB_MEMORY_STABLE_RELATIVE_DELTA):
        job_state["memory_stable_polls"] = 0
    elif len(samples) >= JOB_MEMORY_MIN_SAMPLES:
        job_state["memory_stable_polls"] = job_state.get("memory_stable_polls", 0) + 1

    if (
            job_state.get("memory_stable_polls", 0) >= JOB_MEMORY_STABLE_POLLS
            or len(samples) >= JOB_MEMORY_MAX_SAMPLES
    ):
        job_memory_estimates[job_name]["stable"] = True
        job_state["memory_monitor_stopped"] = True
        log_widget = job_state.get("log_widget")
        if log_widget is not None:
            append_log(
                log_widget,
                "状态：内存监测已稳定，"
                f"峰值 {format_memory_size(peak_after)}，"
                f"估算 {format_memory_size(job_memory_estimates[job_name]['estimated_memory'])}。\n"
            )


def start_job_memory_monitor(job_state):
    """Initialize memory monitor fields; sampling starts after .sta appears."""
    job_state["memory_samples"] = []
    job_state["memory_peak"] = 0
    job_state["memory_stable_polls"] = 0
    job_state["memory_monitor_stopped"] = False


def activate_job_memory_monitor(job_state):
    """Begin shared memory sampling once the job has generated its .sta file."""
    if (
            job_state.get("finalized")
            or job_state.get("memory_monitor_stopped")
            or job_state.get("memory_monitor_active")
    ):
        return

    job_state["memory_monitor_active"] = True
    start_global_memory_monitor()


def start_global_memory_monitor():
    """Start one shared memory sampler for all active jobs."""
    if memory_monitor_state.get("running"):
        return

    memory_monitor_state["running"] = True
    root.after(JOB_MEMORY_MONITOR_INTERVAL_MS, run_global_memory_monitor)


def run_global_memory_monitor():
    """Sample all Abaqus process memory once and distribute it to active jobs."""
    tracked_jobs = [
        state for state in active_jobs.values()
        if (
                not state.get("finalized")
                and state.get("memory_monitor_active")
                and not state.get("memory_monitor_stopped")
        )
    ]

    if not tracked_jobs:
        memory_monitor_state["running"] = False
        return

    usage_by_job = get_abaqus_job_memory_usage(force=True)
    for job_state in tracked_jobs:
        update_job_memory_sample(
            job_state,
            usage_by_job.get(job_state.get("job_name", ""))
        )

    still_tracking = any(
        not state.get("finalized")
        and state.get("memory_monitor_active")
        and not state.get("memory_monitor_stopped")
        for state in active_jobs.values()
    )
    if still_tracking:
        root.after(JOB_MEMORY_MONITOR_INTERVAL_MS, run_global_memory_monitor)
    else:
        memory_monitor_state["running"] = False


def get_active_job_count():
    """Return the number of active jobs tracked by this GUI."""
    return sum(
        1 for state in active_jobs.values()
        if not state.get("finalized")
    )


def estimate_available_job_slots():
    """根据实测内存估算当前还能并行提交几个队列作业。"""
    _, available_memory = get_system_memory_info()
    usable_memory = int(available_memory * 0.85) if available_memory else 0
    process_memory, memory_sample_count, job_memory_usage = estimate_per_job_memory_from_running_jobs()
    per_job_memory = process_memory
    current_abaqus_memory = get_current_abaqus_memory_total(job_memory_usage)
    memory_available_for_new_jobs = max(0, usable_memory - current_abaqus_memory)
    memory_limited = per_job_memory > 0 and memory_available_for_new_jobs > 0

    if memory_limited:
        memory_slots = memory_available_for_new_jobs // per_job_memory
    else:
        memory_slots = UNLIMITED_JOB_SLOTS

    slots = max(0, memory_slots)

    return slots, {
        "memory_slots": memory_slots,
        "memory_limited": memory_limited,
        "available_memory": available_memory,
        "usable_memory": usable_memory,
        "current_abaqus_memory": current_abaqus_memory,
        "memory_available_for_new_jobs": memory_available_for_new_jobs,
        "per_job_memory": per_job_memory,
        "process_per_job_memory": process_memory,
        "memory_sample_count": memory_sample_count,
        "job_memory_usage": job_memory_usage,
    }


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


def collect_job_signals(job_state, monitor_state, sta_path, process):
    """集中收集当前 Abaqus 作业的关键信号。"""
    sta_exists = os.path.exists(sta_path)
    lock_exists = job_lock_exists(job_state)
    stage_started = abaqus_stage_started(job_state)
    process_exited = process.poll() is not None
    wait_seconds = time.time() - monitor_state["submitted_at"]

    file_status, file_detail = inspect_job_files_throttled(
        monitor_state,
        interval_seconds=20
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
    job_state["full_status_text"] = text
    if status_var is not None:
        visible_text = text if len(text) <= 52 else text[:49] + "..."
        if job_state.get("visible_status_text") != visible_text:
            status_var.set(visible_text)
            job_state["visible_status_text"] = visible_text


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
    """关闭窗口。"""
    root.destroy()


def create_job_log_tab(job_state):
    """为一次作业提交创建独立日志页。"""
    global log_tab_counter
    global right_panel_visible

    ensure_right_panel_ui()

    job_name = job_state["job_name"]
    log_tab_counter += 1
    tab_title = make_unique_job_selector_title(job_name)

    if not right_panel_visible:
        root.geometry(FULL_GEOMETRY)
        root.minsize(*FULL_MIN_SIZE)
        body_frame.columnconfigure(0, minsize=LEFT_PANEL_WIDTH, weight=0)
        body_frame.columnconfigure(1, minsize=RIGHT_PANEL_MIN_WIDTH, weight=0)
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel_visible = True

    log_notebook.configure(style="Hidden.TNotebook")

    tab_frame = ttk.Frame(log_notebook, style="Card.TFrame")
    tab_frame.rowconfigure(0, weight=1)
    tab_frame.columnconfigure(0, weight=0)

    content_frame = ttk.Frame(tab_frame, style="Card.TFrame")
    content_frame.grid(row=0, column=0, sticky="nsw")
    content_frame.rowconfigure(3, weight=1)
    content_frame.columnconfigure(0, weight=0)
    content_frame.grid_propagate(False)

    toolbar = ttk.Frame(content_frame, style="Card.TFrame")
    toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

    toolbar_info = ttk.Frame(toolbar, style="Card.TFrame")
    toolbar_info.grid(row=0, column=0, sticky="ew")
    toolbar_info.columnconfigure(0, weight=0)
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
        style="Hint.TLabel",
        width=32
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
        padx=2,
        pady=8
    )
    log_widget.grid(row=3, column=0, sticky="ns")
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
    for control_column in (4, 6, 8):
        filebar.columnconfigure(control_column, weight=1, uniform="job_control_gap")
    for control_column in (5, 7):
        filebar.columnconfigure(control_column, weight=0, minsize=68, uniform="job_control_button")

    artifact_button_width = 58
    for index, (label, extension) in enumerate(
            (("目录", "dir"), ("STA", ".sta"), ("MSG", ".msg"), ("DAT", ".dat"))
    ):
        left_pad = 4 if index == 0 else 0
        right_pad = 8 if index < 3 else 0
        ctk.CTkButton(
            filebar,
            text=label,
            width=artifact_button_width,
            height=28,
            corner_radius=7,
            font=FONT_HINT,
            fg_color=BTN_LIGHT_FG,
            hover_color=BTN_LIGHT_HOVER,
            text_color=BTN_LIGHT_TEXT,
            bg_color="#ffffff",
            command=lambda ext=extension: open_job_artifact(job_state, ext)
        ).grid(row=0, column=index, sticky="w", padx=(left_pad, right_pad))

    suspend_btn = ctk.CTkButton(
        filebar,
        text="暂停",
        width=68,
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
        width=68,
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
    add_job_tab_button(tab_frame, tab_title)
    root.after_idle(lambda: sync_log_notebook_width(log_widget))

    job_state["log_widget"] = log_widget
    job_state["status_var"] = status_var
    job_state["suspend_btn"] = suspend_btn
    job_state["terminate_btn"] = terminate_btn
    job_state["sta_header_label"] = sta_header_label
    job_state["tab_frame"] = tab_frame

    return log_widget


def select_job_tab(tab_frame):
    """切换右侧显示的作业日志页。"""
    try:
        log_notebook.select(tab_frame)
    except tk.TclError:
        return

    for frame, record in list(job_tab_records.items()):
        try:
            selected = frame == tab_frame
            if selected and job_selector_var is not None:
                job_selector_var.set(record["title"])
        except tk.TclError:
            job_tab_records.pop(frame, None)

    update_job_selector_values()
    update_selected_job_selector_style()


def mark_job_tab_final_status(job_state, status):
    """Mark a retained job tab by final status."""
    tab_frame = job_state.get("tab_frame")
    record = job_tab_records.get(tab_frame)
    if record is None:
        return

    record["status"] = status
    update_job_selector_values()
    try:
        selected_tab = log_notebook.select()
        if selected_tab:
            select_job_tab(root.nametowidget(selected_tab))
    except tk.TclError:
        return


def add_job_tab_button(tab_frame, title):
    """Register one job page in the selector."""
    job_tab_records[tab_frame] = {
        "title": title,
        "status": "",
    }
    update_job_selector_values()
    select_job_tab(tab_frame)


def remove_job_tab_button(tab_frame):
    """Remove one job page from the selector."""
    job_tab_records.pop(tab_frame, None)
    update_job_selector_values()

    try:
        tabs = list(log_notebook.tabs())
        if tabs:
            select_job_tab(root.nametowidget(tabs[-1]))
    except tk.TclError:
        pass


def select_job_from_dropdown(choice):
    """Switch log page from the top job selector."""
    for tab_frame, record in list(job_tab_records.items()):
        if record.get("title") == choice:
            select_job_tab(tab_frame)
            return


def update_job_selector_values():
    """Refresh job selector options and status summary."""
    if job_selector is None:
        return

    records = list(job_tab_records.values())
    values = [record["title"] for record in records] or ["无作业"]
    selected_title = job_selector_var.get() if job_selector_var is not None else ""
    if selected_title not in values and job_selector_var is not None:
        job_selector_var.set(values[0])

    try:
        job_selector.configure(values=values, state="normal" if records else "disabled")
    except tk.TclError:
        return

    running_count = 0
    done_count = 0
    failed_count = 0
    for record in records:
        status = record.get("status", "")
        if status in ("完成", "Datacheck Completed"):
            done_count += 1
        elif status:
            failed_count += 1
        else:
            running_count += 1

    if job_stats_var is not None:
        job_stats_var.set(
            f"运行中 {running_count} | 完成 {done_count} | 异常 {failed_count}"
        )

    update_selected_job_selector_style()


def make_unique_job_selector_title(base_title):
    """Return a unique display title for the job selector."""
    existing_titles = {
        record.get("title", "")
        for record in job_tab_records.values()
    }
    if base_title not in existing_titles:
        return base_title

    index = 2
    while f"{base_title} ({index})" in existing_titles:
        index += 1

    return f"{base_title} ({index})"


def update_selected_job_selector_style():
    """Tint the selected job selector by the selected job's final status."""
    if job_selector is None or job_selector_var is None:
        return

    selected_title = job_selector_var.get()
    selected_status = ""
    for record in job_tab_records.values():
        if record.get("title") == selected_title:
            selected_status = record.get("status", "")
            break

    if selected_status in ("完成", "Datacheck Completed"):
        fg_color = "#dcfce7"
        hover_color = "#bbf7d0"
        text_color = "#166534"
    elif selected_status:
        fg_color = "#fee2e2"
        hover_color = "#fecaca"
        text_color = "#991b1b"
    else:
        fg_color = BTN_LIGHT_FG
        hover_color = BTN_LIGHT_HOVER
        text_color = BTN_LIGHT_TEXT

    try:
        job_selector.configure(
            fg_color=fg_color,
            button_color=fg_color,
            button_hover_color=hover_color,
            text_color=text_color,
        )
    except tk.TclError:
        pass


def sync_log_notebook_width(log_widget):
    """Keep the notebook border and right panel as wide as the actual log text widget."""
    try:
        log_widget.update_idletasks()
        log_width = log_widget.winfo_reqwidth()
        content_frame = log_widget.master
        content_frame.grid_propagate(False)
        content_frame.configure(width=log_width)
        log_notebook.configure(width=log_width)
        right_panel.configure(width=log_width, height=PANEL_HEIGHT)
        body_frame.columnconfigure(1, minsize=log_width, weight=0)
        window_width = LEFT_PANEL_WIDTH + log_width + WINDOW_HORIZONTAL_PADDING + 16
        root.geometry(f"{window_width}x{WINDOW_HEIGHT}")
        root.minsize(window_width, WINDOW_HEIGHT)
        root.maxsize(window_width, WINDOW_HEIGHT)
    except (NameError, tk.TclError):
        pass


def ensure_right_panel_ui():
    """Create right-side job log UI only when the first job is submitted."""
    global right_panel
    global log_card
    global log_inner
    global log_notebook
    global job_selector_var
    global job_selector
    global job_stats_var

    if right_panel is None:
        right_panel = ttk.Frame(
            body_frame,
            style="Main.TFrame",
            width=RIGHT_PANEL_MIN_WIDTH,
            height=PANEL_HEIGHT
        )
        right_panel.grid_propagate(False)
        right_panel.pack_propagate(False)

    if log_notebook is not None:
        return

    log_card = ttk.Frame(right_panel, style="Card.TFrame")
    log_card.pack(fill="both", expand=True)

    log_inner = ttk.Frame(log_card, style="Card.TFrame")
    log_inner.pack(fill="both", expand=True, padx=0, pady=(0, 0))

    ttk.Label(
        log_inner,
        text="作业运行情况",
        style="Normal.TLabel"
    ).pack(anchor="w", pady=(0, 8))

    selector_row = ttk.Frame(log_inner, style="Card.TFrame")
    selector_row.pack(fill="x", anchor="w", pady=(0, 8))
    selector_row.columnconfigure(1, weight=1)

    ttk.Label(
        selector_row,
        text="Job",
        style="Normal.TLabel"
    ).grid(row=0, column=0, sticky="w", padx=(0, 8))

    job_selector_var = tk.StringVar(value="无作业")
    job_selector = ctk.CTkOptionMenu(
        selector_row,
        variable=job_selector_var,
        values=["无作业"],
        width=220,
        height=30,
        corner_radius=7,
        fg_color=BTN_LIGHT_FG,
        button_color=BTN_LIGHT_FG,
        button_hover_color=BTN_LIGHT_HOVER,
        text_color=BTN_LIGHT_TEXT,
        dropdown_fg_color="#ffffff",
        dropdown_hover_color="#e5e7eb",
        dropdown_text_color="#111827",
        font=FONT_HINT,
        dropdown_font=FONT_HINT,
        anchor="center",
        dynamic_resizing=False,
        state="disabled",
        command=select_job_from_dropdown
    )
    job_selector.grid(row=0, column=1, sticky="w")

    job_stats_var = tk.StringVar(value="运行中 0 | 完成 0 | 异常 0")
    ttk.Label(
        selector_row,
        textvariable=job_stats_var,
        style="Hint.TLabel"
    ).grid(row=0, column=2, sticky="e", padx=(14, 0))

    log_notebook = ttk.Notebook(log_inner, style="Hidden.TNotebook")
    log_notebook.pack(fill="y", expand=True, anchor="w")


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
        if log_notebook is None or right_panel is None:
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
        root.maxsize(*LEFT_ONLY_MIN_SIZE)

        # 重置页签计数，下一次提交作业时重新按第一次提交处理
        log_tab_counter = 0

        job_tab_records.clear()
        update_job_selector_values()
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
                remove_job_tab_button(tab_frame)
                tab_frame.destroy()

            root.after(0, collapse_right_panel_if_empty)

        except tk.TclError:
            pass

    root.after(delay_ms, close_tab)


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
    if job_state.get("memory_peak"):
        estimate = job_memory_estimates.setdefault(job_state["job_name"], {})
        estimate.setdefault("group", infer_model_group(job_state["job_name"]))
        estimate.setdefault("step_peaks", {})
        estimate["peak_memory"] = max(
            int(estimate.get("peak_memory") or 0),
            int(job_state.get("memory_peak") or 0)
        )
        estimate["estimated_memory"] = max(
            int(estimate.get("estimated_memory") or 0),
            int(estimate["peak_memory"] * get_memory_safety_factor())
        )
        estimate["sample_count"] = max(
            int(estimate.get("sample_count") or 0),
            len(job_state.get("memory_samples", []))
        )
        estimate["updated_at"] = time.time()
    if is_memory_related_failure(status, detail):
        increase_memory_safety_factor(detail)
    active_jobs.pop(job_state["job_key"], None)
    for extension in DIAGNOSTIC_EXTENSIONS:
        diagnostic_file_cache.pop(
            os.path.join(job_state["work_dir"], job_state["job_name"] + extension),
            None
        )
    disable_job_controls(job_state)
    set_job_status(job_state, format_final_status_for_display(status, detail))
    append_job_final_history(job_state, status, detail)
    mark_job_tab_final_status(job_state, status)

    log_widget = job_state.get("log_widget")
    if log_widget is not None:
        status_detail = f"状态：{status}{'，' + detail if detail else ''}"
        append_log(log_widget, f"{status_detail}，运行日志页已保留。\n")

    if not job_state.get("from_joblist"):
        notify_job_finished(job_state, status, detail)

    if job_state.get("from_joblist"):
        finish_joblist_job(job_state, status, detail)


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
    """Send terminate to the job; keep the button available until the job is finalized."""
    if job_state.get("finalized"):
        return

    job_state["terminating"] = True
    job_state["terminating_at"] = time.time()
    job_state["terminate_attempts"] = job_state.get("terminate_attempts", 0) + 1
    send_abaqus_job_control("terminate", job_state)

    suspend_btn = job_state.get("suspend_btn")
    if suspend_btn is not None:
        try:
            suspend_btn.configure(state="disabled")
        except tk.TclError:
            pass

    terminate_btn = job_state.get("terminate_btn")
    if terminate_btn is not None:
        try:
            terminate_btn.configure(state="normal")
        except tk.TclError:
            pass

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
    pending_lines = []
    pending_lock = threading.Lock()
    flush_scheduled = False

    def flush_pending_lines():
        nonlocal flush_scheduled

        with pending_lock:
            lines = list(pending_lines)
            pending_lines.clear()
            flush_scheduled = False

        if not lines:
            return

        if job_state is not None:
            cache_console_output(job_state, "".join(lines))

        visible_lines = []
        for line in lines:
            if job_state is not None:
                update_abaqus_stage_from_text(job_state, line)
                maybe_finalize_from_console_output(job_state, line)

            if not should_hide_console_line(line):
                visible_lines.append(line)

        if visible_lines:
            append_log(log_widget, "".join(visible_lines))

        with pending_lock:
            has_more = bool(pending_lines)

        if has_more:
            schedule_flush()

    def schedule_flush(delay_ms=OUTPUT_FLUSH_INTERVAL_MS):
        nonlocal flush_scheduled

        with pending_lock:
            if flush_scheduled:
                return
            flush_scheduled = True

        root.after(delay_ms, flush_pending_lines)

    def queue_console_line(line, flush_now=False):
        with pending_lock:
            pending_lines.append(line)

        schedule_flush(0 if flush_now else OUTPUT_FLUSH_INTERVAL_MS)

    def output_has_overwrite_prompt(output_buffer):
        prompt_text = output_buffer.upper()
        has_overwrite_context = any(
            marker in prompt_text for marker in OVERWRITE_PROMPT_MARKERS
        )
        has_choice = "Y" in prompt_text and "N" in prompt_text
        return has_overwrite_context and has_choice

    def read_output():
        if process.stdout is None:
            return

        raw_stdout = getattr(process.stdout, "buffer", process.stdout)
        read_chunk = getattr(raw_stdout, "read1", raw_stdout.read)
        uses_binary_reader = raw_stdout is not process.stdout
        decoder = codecs.getincrementaldecoder("mbcs")("replace")
        output_buffer = ""
        line_buffer = ""
        prompt_check_scheduled = False

        def schedule_prompt_check():
            nonlocal prompt_check_scheduled

            if prompt_check_scheduled:
                return

            prompt_check_scheduled = True

            def run_prompt_check():
                nonlocal prompt_check_scheduled
                prompt_check_scheduled = False
                maybe_answer_overwrite_prompt(process, job_state, output_buffer)

            root.after(0, run_prompt_check)

        while True:
            raw_chunk = read_chunk(256)
            if not raw_chunk:
                break

            if isinstance(raw_chunk, bytes):
                chunk = decoder.decode(raw_chunk)
            else:
                chunk = raw_chunk

            # 这个 buffer 仍然保留原始输出，用于识别 Abaqus 覆盖提示
            output_buffer = (output_buffer + chunk)[-1000:]

            if (
                    job_state is not None
                    and not job_state.get("overwrite_answer_sent")
                    and not job_state.get("overwrite_prompt_pending")
                    and output_has_overwrite_prompt(output_buffer)
            ):
                schedule_prompt_check()

            # 下面是显示用 buffer，按行过滤
            for char in chunk:
                line_buffer += char

                if char in ("\n", "\r"):
                    line = line_buffer
                    line_buffer = ""
                    queue_console_line(line)

        # 处理最后一段没有换行的输出
        if uses_binary_reader:
            trailing_text = decoder.decode(b"", final=True)
            if trailing_text:
                line_buffer += trailing_text

        if line_buffer:
            queue_console_line(line_buffer, flush_now=True)

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
    final_status, detail = inspect_job_files_throttled(
        monitor_state,
        interval_seconds=15
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
            final_status, detail = inspect_job_files_throttled(
                monitor_state,
                force=True
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

def refresh_runtime_status(job_state):
    """刷新单个作业的实际运行时间显示。"""
    if job_state.get("finalized"):
        return

    if job_state.get("terminating"):
        set_job_status(job_state, "Terminating")
        return

    prefix = "Suspended" if job_state.get("suspended") else "Running"

    if job_state.get("datacheck_mode"):
        prefix = job_state.get("datacheck_phase", "Datacheck | Running")

    set_job_status(job_state, format_progress_status(job_state, prefix))


def start_global_runtime_status_monitor():
    """Start one shared runtime status refresher for all active jobs."""
    if runtime_status_state.get("running"):
        return

    runtime_status_state["running"] = True
    root.after(RUNTIME_STATUS_INTERVAL_MS, run_global_runtime_status_monitor)


def run_global_runtime_status_monitor():
    """Refresh runtime labels for all active jobs with one timer."""
    active_states = [
        state for state in active_jobs.values()
        if not state.get("finalized")
    ]

    if not active_states:
        runtime_status_state["running"] = False
        return

    for job_state in active_states:
        refresh_runtime_status(job_state)

    root.after(RUNTIME_STATUS_INTERVAL_MS, run_global_runtime_status_monitor)


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
        activate_job_memory_monitor(job_state)
        current_size = os.path.getsize(sta_path)
        if current_size < monitor_state["position"]:
            monitor_state["position"] = 0
        if current_size == monitor_state["position"]:
            new_text = ""
        else:
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

    final_status, detail = inspect_job_files_throttled(
        monitor_state,
        interval_seconds=20
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
    return get_oldjob_name_from_path(oldjob_path)


def get_oldjob_name_from_path(oldjob_path):
    """从指定 ODB 路径中读取 oldjob 名称。"""
    return os.path.splitext(os.path.basename(oldjob_path))[0]


def get_queue_inp_name_for_oldjob_path(oldjob_path):
    """Return the queue INP name that can produce the missing oldjob ODB."""
    if not oldjob_path:
        return ""

    oldjob_inp_name = get_oldjob_name_from_path(oldjob_path) + ".inp"
    if oldjob_inp_name in joblist_state.get("jobs", []):
        return oldjob_inp_name

    return ""


def is_completed_queue_status(status):
    """Return whether a queue job ended successfully enough for dependents."""
    return status in ("完成", "Datacheck Completed")


def wait_for_oldjob_odb(oldjob_path, timeout_seconds=10, interval_seconds=2):
    """Wait briefly for an oldjob ODB that may be produced by a queued job."""
    deadline = time.monotonic() + timeout_seconds

    while True:
        if os.path.isfile(oldjob_path):
            return True

        if time.monotonic() >= deadline:
            return False

        try:
            root.update_idletasks()
        except tk.TclError:
            pass

        time.sleep(interval_seconds)


def inp_has_restart_keyword(inp_file):
    """检查 INP 头部是否包含 *Restart。"""
    try:
        with open(inp_file, "r", encoding=STA_FILE_ENCODING, errors="replace") as file:
            for index, line in enumerate(file):
                stripped = line.strip().lower()

                if stripped.startswith("*step"):
                    return False

                if stripped.startswith("*restart"):
                    return True

                if index >= 300:
                    return False
    except OSError:
        return False

    return False


def select_oldjob_path_for_restart(inp_name):
    """为一个重启动 INP 选择 oldjob ODB，或选择队列中的前置 INP。"""
    file_path = filedialog.askopenfilename(
        title=f"为 {inp_name} 选择 oldjob ODB 或前置 INP",
        filetypes=[
            ("Abaqus ODB/INP 文件", "*.odb *.inp"),
            ("Abaqus ODB 文件", "*.odb"),
            ("Abaqus INP 文件", "*.inp"),
            ("所有文件", "*.*")
        ]
    )

    if os.path.splitext(file_path)[1].lower() == ".inp":
        return os.path.splitext(file_path)[0] + ".odb"

    return file_path


def ask_restart_odb_strategy(restart_names):
    """询问队列重启动作业使用同一个 ODB 还是逐个选择。"""
    multiple_restart_jobs = len(restart_names) > 1

    dialog = tk.Toplevel(root)
    dialog.title("检测到重启动作业")
    dialog.transient(root)
    dialog.grab_set()
    dialog.configure(bg="#ffffff")
    dialog.resizable(False, False)

    result = {"value": "cancel"}

    main = tk.Frame(dialog, bg="#ffffff")
    main.pack(fill="both", expand=False, padx=18, pady=14)

    if multiple_restart_jobs:
        text = (
            "检测到以下 INP Heading 包含 *Restart：\n\n"
            + "\n".join(restart_names[:8])
            + ("\n..." if len(restart_names) > 8 else "")
            + "\n\n请选择 oldjob ODB 的设置方式。"
        )
    else:
        text = (
            "检测到该 INP Heading 包含 *Restart：\n\n"
            f"{restart_names[0]}\n\n"
            "请指定 oldjob ODB。"
        )

    tk.Label(
        main,
        text=text,
        bg="#ffffff",
        fg="#111827",
        justify="left",
        anchor="w",
        font=FONT_HINT
    ).pack(fill="x", anchor="w", pady=(0, 14))

    button_row = tk.Frame(main, bg="#ffffff")
    button_row.pack(fill="x", anchor="w")

    def choose(value):
        result["value"] = value
        dialog.destroy()

    if multiple_restart_jobs:
        ctk.CTkButton(
            button_row,
            text="选择同一 ODB 启动",
            width=140,
            height=32,
            corner_radius=8,
            font=FONT_BUTTON,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            text_color="white",
            command=lambda: choose("same")
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            button_row,
            text="为每个作业指定 ODB",
            width=132,
            height=32,
            corner_radius=8,
            font=FONT_BUTTON,
            fg_color=BTN_LIGHT_FG,
            hover_color=BTN_LIGHT_HOVER,
            text_color=BTN_LIGHT_TEXT,
            command=lambda: choose("each")
        ).pack(side="left", padx=(0, 10))
    else:
        ctk.CTkButton(
            button_row,
            text="指定 ODB",
            width=108,
            height=32,
            corner_radius=8,
            font=FONT_BUTTON,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            text_color="white",
            command=lambda: choose("each")
        ).pack(side="left", padx=(0, 10))

    ctk.CTkButton(
        button_row,
        text="取消",
        width=84,
        height=32,
        corner_radius=8,
        font=FONT_BUTTON,
        fg_color="#e5e7eb",
        hover_color="#d1d5db",
        text_color="#111827",
        command=lambda: choose("cancel")
    ).pack(side="left")

    dialog.protocol("WM_DELETE_WINDOW", lambda: choose("cancel"))

    dialog.update_idletasks()
    dialog_width = dialog.winfo_reqwidth()
    dialog_height = dialog.winfo_reqheight()
    root.update_idletasks()
    x = root.winfo_x() + (root.winfo_width() - dialog_width) // 2
    y = root.winfo_y() + (root.winfo_height() - dialog_height) // 2
    dialog.geometry(f"{dialog_width}x{dialog_height}+{max(x, 0)}+{max(y, 0)}")

    root.wait_window(dialog)
    return result["value"]


def collect_restart_oldjob_paths(inp_files):
    """为包含 *Restart 的 INP 收集 oldjob ODB 映射。"""
    restart_files = [
        inp_file for inp_file in inp_files
        if inp_has_restart_keyword(inp_file)
    ]

    if not restart_files:
        return {}

    restart_names = [os.path.basename(path) for path in restart_files]
    strategy = ask_restart_odb_strategy(restart_names)

    if strategy == "cancel":
        return None

    oldjob_paths = {}

    if strategy == "same":
        odb_path = select_oldjob_path_for_restart("所有重启动作业")
        if not odb_path:
            return None

        oldjob_paths = {
            os.path.basename(inp_file): odb_path
            for inp_file in restart_files
        }
    else:
        for inp_file in restart_files:
            inp_name = os.path.basename(inp_file)
            odb_path = select_oldjob_path_for_restart(inp_name)
            if not odb_path:
                return None
            oldjob_paths[inp_name] = odb_path

    return oldjob_paths


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


def get_existing_odb_action_for_submit(job_name, odb_path, queue_mode):
    """Return ODB handling action; queue mode asks once and reuses it."""
    if not queue_mode:
        return ask_existing_odb_action(job_name, odb_path)

    action = joblist_state.get("existing_odb_action", "")
    if action:
        return action

    action = ask_existing_odb_action(job_name, odb_path)
    joblist_state["existing_odb_action"] = action

    if action in ("overwrite", "backup"):
        append_history_text(
            f"队列同名 ODB 处理策略：{'覆盖' if action == 'overwrite' else '备份'}\n"
            "后续队列作业检测到同名 ODB 时将直接使用该策略。\n\n"
        )

    return action


def update_joblist_status_label(text=""):
    """更新队列状态提示。"""
    try:
        if text:
            joblist_status_var.set(text)
        elif joblist_state["jobs"]:
            waiting = sum(
                1 for name in joblist_state["jobs"]
                if joblist_state["statuses"].get(name) in ("等待", "等待前置")
            )
            running = len(joblist_state["running"])
            done = len(joblist_state["jobs"]) - waiting - running
            joblist_status_var.set(
                f"队列：{len(joblist_state['jobs'])} 个 | 运行 {running} | 等待 {waiting} | 已结束 {done}"
            )
        else:
            joblist_status_var.set("队列：未生成")
    except NameError:
        pass


def is_joblist_submitted():
    """Return True while an existing queue should be appended instead of replaced."""
    return bool(joblist_state.get("active") or joblist_state.get("running"))


def update_joblist_button_mode():
    """Switch the queue build button between create and append modes."""
    try:
        build_joblist_btn.configure(
            text="追加队列" if is_joblist_submitted() else "生成队列"
        )
        stop_joblist_btn.configure(
            state="normal" if joblist_state.get("active") else "disabled"
        )
    except (NameError, tk.TclError):
        pass


def get_joblist_user_max_parallel():
    """读取 GUI 中设置的队列最大并行作业数。"""
    try:
        value = int(joblist_max_parallel_var.get().strip())
    except (NameError, ValueError):
        return 1

    return max(1, value)


def apply_joblist_max_parallel_change(event=None):
    """Apply an edited queue max-parallel value after the user presses Enter."""
    try:
        value = int(joblist_max_parallel_var.get().strip())
    except ValueError:
        fallback = max(
            1,
            int(joblist_state.get("max_parallel") or calculate_default_joblist_parallel(cpus_var.get()))
        )
        joblist_max_parallel_var.set(str(fallback))
        messagebox.showerror("错误", "队列最大并行数必须是整数。")
        return "break"

    value = max(1, value)
    if joblist_max_parallel_var.get().strip() != str(value):
        joblist_max_parallel_var.set(str(value))

    old_value = int(joblist_state.get("max_parallel") or value)
    joblist_state["max_parallel"] = value

    if joblist_state.get("active"):
        append_history_text(f"队列最大并行数已调整：{old_value} -> {value}\n\n")
        update_joblist_status_label()
        root.after(100, dispatch_joblist)
    else:
        update_joblist_status_label()

    return "break"


def refresh_joblist_restart_jobs():
    """Rescan the queue before submission so restart detection is never stale."""
    work_dir = joblist_state.get("work_dir", "")
    restart_names = []

    for name in joblist_state.get("jobs", []):
        inp_file = os.path.join(work_dir, name)
        if os.path.isfile(inp_file) and inp_has_restart_keyword(inp_file):
            restart_names.append(name)

    joblist_state["restart_jobs"] = restart_names
    return restart_names


def save_joblist_file(work_dir, inp_names):
    """保存只包含 INP 文件名的 joblist.json。"""
    path = os.path.join(work_dir, JOBLIST_FILENAME)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(inp_names, file, ensure_ascii=False, indent=2)

    return path


def scan_inp_names_in_dir(work_dir):
    """Return sorted INP file names in a directory."""
    return sorted(
        name for name in os.listdir(work_dir)
        if name.lower().endswith(".inp")
        and os.path.isfile(os.path.join(work_dir, name))
    )


def create_joblist_from_dir():
    """选择文件夹，扫描 INP 并生成新的 joblist.json。"""
    work_dir = filedialog.askdirectory(title="选择包含 INP 文件的文件夹")
    if not work_dir:
        return

    inp_names = scan_inp_names_in_dir(work_dir)

    if not inp_names:
        messagebox.showwarning("未找到 INP", "该文件夹下没有 .inp 文件。")
        return

    try:
        joblist_path = save_joblist_file(work_dir, inp_names)
    except OSError as e:
        messagebox.showerror("保存失败", f"无法保存 joblist.json：\n{e}")
        return

    restart_names = [
        name for name in inp_names
        if inp_has_restart_keyword(os.path.join(work_dir, name))
    ]

    joblist_state.update(
        {
            "active": False,
            "work_dir": work_dir,
            "jobs": inp_names,
            "statuses": {name: "等待" for name in inp_names},
            "running": set(),
            "joblist_path": joblist_path,
            "max_parallel": 1,
            "restart_jobs": restart_names,
            "oldjob_paths": {},
            "dependencies": {},
            "existing_odb_action": "",
        }
    )

    append_history_text(
        f"已生成队列文件：{joblist_path}\n"
        f"队列 INP 数量：{len(inp_names)}\n"
        f"Restart INP 数量：{len(restart_names)}\n"
        f"{', '.join(inp_names[:8])}{' ...' if len(inp_names) > 8 else ''}\n\n"
    )
    update_joblist_status_label()
    update_joblist_button_mode()


def append_joblist_from_dir():
    """Append new INP jobs to an already submitted queue without resetting running jobs."""
    base_dir = joblist_state.get("work_dir", "")
    if not base_dir:
        create_joblist_from_dir()
        return

    work_dir = filedialog.askdirectory(
        title="选择要追加 INP 的文件夹",
        initialdir=base_dir
    )
    if not work_dir:
        return

    if os.path.normcase(os.path.abspath(work_dir)) != os.path.normcase(os.path.abspath(base_dir)):
        messagebox.showerror(
            "无法追加",
            "当前队列只支持追加同一工作目录下的 INP。\n\n"
            f"当前队列目录：{base_dir}\n"
            f"选择目录：{work_dir}"
        )
        return

    inp_names = scan_inp_names_in_dir(work_dir)
    existing_names = set(joblist_state.get("jobs", []))
    new_names = [name for name in inp_names if name not in existing_names]

    if not new_names:
        messagebox.showinfo("没有新作业", "该目录下没有可追加的新 INP 文件。")
        return

    restart_names = [
        name for name in new_names
        if inp_has_restart_keyword(os.path.join(work_dir, name))
    ]
    restart_mapping = {}
    if restart_names:
        restart_files = [os.path.join(work_dir, name) for name in restart_names]
        restart_mapping = collect_restart_oldjob_paths(restart_files)
        if restart_mapping is None:
            messagebox.showinfo("已取消", "已取消追加队列，未加入新作业。")
            return

    previous_jobs = list(joblist_state.get("jobs", []))
    previous_statuses = dict(joblist_state.get("statuses", {}))
    previous_restart_jobs = list(joblist_state.get("restart_jobs", []))
    previous_oldjob_paths = dict(joblist_state.get("oldjob_paths", {}))
    previous_dependencies = dict(joblist_state.get("dependencies", {}))

    joblist_state["jobs"].extend(new_names)
    for name in new_names:
        joblist_state["statuses"][name] = "等待"
    joblist_state["restart_jobs"] = previous_restart_jobs + restart_names
    joblist_state["oldjob_paths"].update(restart_mapping)

    if not ensure_joblist_restart_oldjobs(force_prompt=False, confirm=False):
        joblist_state["jobs"] = previous_jobs
        joblist_state["statuses"] = previous_statuses
        joblist_state["restart_jobs"] = previous_restart_jobs
        joblist_state["oldjob_paths"] = previous_oldjob_paths
        joblist_state["dependencies"] = previous_dependencies
        messagebox.showinfo("已取消", "追加队列未生效，当前运行队列保持不变。")
        return

    try:
        joblist_state["joblist_path"] = save_joblist_file(base_dir, joblist_state["jobs"])
    except OSError as e:
        joblist_state["jobs"] = previous_jobs
        joblist_state["statuses"] = previous_statuses
        joblist_state["restart_jobs"] = previous_restart_jobs
        joblist_state["oldjob_paths"] = previous_oldjob_paths
        joblist_state["dependencies"] = previous_dependencies
        messagebox.showerror("保存失败", f"无法更新 joblist.json：\n{e}")
        return

    joblist_state["active"] = True
    append_history_text(
        f"已追加队列作业：{len(new_names)} 个\n"
        f"Restart INP 数量：{len(restart_names)}\n"
        f"{', '.join(new_names[:8])}{' ...' if len(new_names) > 8 else ''}\n\n"
    )
    update_joblist_status_label()
    update_joblist_button_mode()
    root.after(100, dispatch_joblist)


def select_joblist_dir():
    """Create a new queue before submission, or append to an already submitted queue."""
    if is_joblist_submitted():
        append_joblist_from_dir()
    else:
        create_joblist_from_dir()


def get_joblist_submit_settings():
    """读取队列提交使用的公共参数。"""
    try:
        cpus = int(cpus_var.get().strip())
    except ValueError:
        messagebox.showerror("错误", "核心数必须是整数。")
        return None

    if cpus < 0 or cpus > MAX_CPUS:
        messagebox.showerror("错误", f"核心数范围应为 0–{MAX_CPUS}。")
        return None

    memory_argument = get_memory_argument()
    if not validate_memory_argument(memory_argument):
        return None

    for_file_path = for_file_var.get().strip()
    if for_file_path:
        if not os.path.isfile(for_file_path):
            messagebox.showerror("错误", f"FOR 子程序文件不存在：\n{for_file_path}")
            return None

        if os.path.splitext(for_file_path)[1].lower() not in (".for", ".f", ".f90"):
            messagebox.showerror("错误", "请选择 .for、.f 或 .f90 后缀的子程序文件。")
            return None

    return {
        "cpus": cpus,
        "memory_argument": memory_argument,
    }


def ensure_joblist_restart_oldjobs(force_prompt=False, confirm=True):
    """队列启动前为 Restart INP 准备 oldjob ODB 映射。"""
    restart_names = refresh_joblist_restart_jobs()
    if not restart_names:
        joblist_state["oldjob_paths"] = {}
        joblist_state["dependencies"] = {}
        return True

    existing_mapping = joblist_state.get("oldjob_paths", {})
    if not force_prompt and all(existing_mapping.get(name) for name in restart_names):
        oldjob_paths = existing_mapping
    else:
        joblist_state["oldjob_paths"] = {}
        joblist_state["dependencies"] = {}
        for name in restart_names:
            if joblist_state["statuses"].get(name) == "等待前置":
                joblist_state["statuses"][name] = "等待"

        restart_files = [
            os.path.join(joblist_state["work_dir"], name)
            for name in restart_names
        ]
        oldjob_paths = collect_restart_oldjob_paths(restart_files)
        if oldjob_paths is None:
            messagebox.showinfo("已取消", "已取消队列提交。")
            return False

    joblist_state["oldjob_paths"] = oldjob_paths

    dependencies = {}
    missing_external_oldjobs = []
    invalid_oldjobs = []
    for inp_name, oldjob_path in oldjob_paths.items():
        oldjob_name = get_oldjob_name_from_path(oldjob_path)
        current_job_name = os.path.splitext(inp_name)[0]

        if os.path.splitext(oldjob_path)[1].lower() != ".odb":
            invalid_oldjobs.append(f"{inp_name} -> 不是 ODB 文件：{oldjob_path}")
            continue

        if not JOB_NAME_PATTERN.fullmatch(oldjob_name):
            invalid_oldjobs.append(f"{inp_name} -> oldjob 名称不合法：{oldjob_name}")
            continue

        if oldjob_name == current_job_name:
            invalid_oldjobs.append(f"{inp_name} -> oldjob 不能与当前作业同名：{oldjob_name}")
            continue

        dependency_inp_name = get_queue_inp_name_for_oldjob_path(oldjob_path)
        if dependency_inp_name:
            dependencies[inp_name] = dependency_inp_name
            joblist_state["statuses"][inp_name] = "等待前置"
        elif os.path.isfile(oldjob_path):
            continue
        else:
            missing_external_oldjobs.append((inp_name, oldjob_path))

    if invalid_oldjobs:
        messagebox.showerror(
            "Restart oldjob 设置错误",
            "以下 Restart 作业的 oldjob 设置无效，队列尚未开始提交：\n\n"
            + "\n".join(invalid_oldjobs[:8])
            + ("\n..." if len(invalid_oldjobs) > 8 else "")
        )
        return False

    if missing_external_oldjobs:
        messagebox.showerror(
            "ODB 文件不存在",
            "以下 Restart 作业指定的 oldjob ODB 不存在，且未在本次队列中找到对应 INP：\n\n"
            + "\n".join(
                f"{inp_name} -> {oldjob_path}"
                for inp_name, oldjob_path in missing_external_oldjobs[:6]
            )
            + ("\n..." if len(missing_external_oldjobs) > 6 else "")
        )
        return False

    confirm_lines = [
        f"{name} -> {os.path.basename(path)}"
        for name, path in oldjob_paths.items()
    ]
    if confirm and confirm_lines and not messagebox.askyesno(
        "确认 Restart oldjob",
        "请确认以下 Restart oldjob 设置。\n\n"
        "如果选错，请点击“否”，队列不会提交任何作业。\n\n"
        + "\n".join(confirm_lines[:10])
        + ("\n..." if len(confirm_lines) > 10 else "")
    ):
        joblist_state["oldjob_paths"] = {}
        joblist_state["dependencies"] = {}
        messagebox.showinfo("已取消", "已取消队列提交，未提交任何新作业。")
        return False

    joblist_state["dependencies"] = dependencies
    if dependencies:
        messagebox.showinfo(
            "Restart 作业等待前置计算",
            "检测到部分 Restart 作业的 oldjob ODB 尚未生成，"
            "但对应 INP 在本次队列中。\n\n"
            + "\n".join(
                f"{inp_name} 将等待 {dep_name} 完成后提交"
                for inp_name, dep_name in dependencies.items()
            )
        )

    append_history_text(
        "Restart oldjob 设置：\n"
        + "\n".join(
            f"{name} -> {os.path.basename(path)}"
            for name, path in oldjob_paths.items()
        )
        + (
            "\nRestart 队列依赖：\n"
            + "\n".join(
                f"{name} 等待 {dep_name}"
                for name, dep_name in dependencies.items()
            )
            if dependencies else ""
        )
        + "\n\n"
    )
    return True


def start_joblist():
    """按资源估算并行提交 joblist 中的作业。"""
    if not joblist_state["jobs"]:
        select_joblist_dir()
        if not joblist_state["jobs"]:
            return

    if not ensure_joblist_restart_oldjobs(force_prompt=True):
        return

    settings = get_joblist_submit_settings()
    if settings is None:
        return

    slots, details = estimate_available_job_slots()
    user_max_parallel = get_joblist_user_max_parallel()
    planned_slots = min(slots, user_max_parallel)

    if planned_slots <= 0:
        messagebox.showwarning(
            "资源不足",
            "当前内存估算不足以提交新的队列作业。\n\n"
            f"可用内存：{format_memory_size(details['available_memory'])}\n"
            f"保留 15% 后可用：{format_memory_size(details['usable_memory'])}\n"
            f"单作业内存：{format_memory_size(details['per_job_memory'])}"
        )
        return

    joblist_state["active"] = True
    joblist_state["max_parallel"] = planned_slots
    memory_table_widths = (12, 16, 12, 16)
    memory_table_header = format_history_table_row(
        ("可用内存", "保留后可用内存", "内存估算", "估算可提交 Job"),
        memory_table_widths
    )
    memory_table_values = format_history_table_row(
        (
            format_memory_size(details["available_memory"]),
            format_memory_size(details["usable_memory"]),
            format_memory_size(details["per_job_memory"]),
            format_job_slot_count(details["memory_slots"]),
        ),
        memory_table_widths
    )

    append_history_text(
        f"开始队列提交：{joblist_state['joblist_path']}\n"
        f"队列并行：用户上限 {user_max_parallel} | 实际并行 {planned_slots}\n"
        f"{memory_table_header}\n"
        f"{memory_table_values}\n"
        f"当前 Abaqus 总内存：{format_memory_size(details['current_abaqus_memory'])} | "
        f"可用于新 Job：{format_memory_size(details['memory_available_for_new_jobs'])}\n\n"
    )
    update_joblist_status_label()
    update_joblist_button_mode()
    dispatch_joblist()


def get_next_ready_joblist_name():
    """Return the next queue job whose dependencies are satisfied."""
    dependencies = joblist_state.get("dependencies", {})
    restart_jobs = set(joblist_state.get("restart_jobs", []))

    for include_restart in (False, True):
        for name in joblist_state["jobs"]:
            if not include_restart and name in restart_jobs:
                continue

            status = joblist_state["statuses"].get(name)
            if status not in ("等待", "等待前置"):
                continue

            dependency_name = dependencies.get(name, "")
            if not dependency_name:
                return name

            dependency_status = joblist_state["statuses"].get(dependency_name)
            if is_completed_queue_status(dependency_status):
                joblist_state["statuses"][name] = "等待"
                return name

            if dependency_status in ("失败", "终止", "取消", "状态未知", "Datacheck Failed", "提交失败", "已停止", "跳过"):
                joblist_state["statuses"][name] = "跳过"
                append_history_text(
                    f"跳过 Restart 作业：{name}\n"
                    f"原因：前置作业 {dependency_name} 未正常完成（{dependency_status}）。\n\n"
                )
                continue

            joblist_state["statuses"][name] = "等待前置"

    return ""


def dispatch_joblist():
    """按当前资源状态补位提交队列作业。"""
    if not joblist_state["active"]:
        return

    settings = get_joblist_submit_settings()
    if settings is None:
        joblist_state["active"] = False
        update_joblist_status_label("队列：已停止，参数无效")
        update_joblist_button_mode()
        return

    slots, _ = estimate_available_job_slots()
    queue_room = max(0, joblist_state["max_parallel"] - len(joblist_state["running"]))
    total_job_room = max(0, joblist_state["max_parallel"] - get_active_job_count())
    submit_slots = max(0, min(slots, queue_room, total_job_room))

    while submit_slots > 0:
        next_name = get_next_ready_joblist_name()

        if not next_name:
            break

        inp_file = os.path.join(joblist_state["work_dir"], next_name)
        joblist_state["statuses"][next_name] = "提交中"
        update_joblist_status_label()

        submitted_job = submit_job(
            inp_file_override=inp_file,
            queue_mode=True,
            oldjob_path_override=joblist_state["oldjob_paths"].get(next_name, "")
        )
        if submitted_job:
            joblist_state["running"].add(next_name)
            joblist_state["statuses"][next_name] = "运行中"
        else:
            joblist_state["statuses"][next_name] = "提交失败"

        submit_slots -= 1

    waiting = any(
        joblist_state["statuses"].get(name) in ("等待", "等待前置")
        for name in joblist_state["jobs"]
    )

    if not waiting and not joblist_state["running"]:
        joblist_state["active"] = False
        update_joblist_status_label("队列：全部完成")
        update_joblist_button_mode()
        append_history_text("队列提交结束。\n\n")
    else:
        update_joblist_status_label()
        if waiting and (not joblist_state["running"] or submit_slots <= 0):
            root.after(5000, dispatch_joblist)


def finish_joblist_job(job_state, status, detail=""):
    """队列作业结束后更新状态并继续补位。"""
    inp_name = job_state.get("joblist_inp_name")
    if not inp_name:
        return

    joblist_state["running"].discard(inp_name)
    joblist_state["statuses"][inp_name] = status

    for dependent_name, dependency_name in joblist_state.get("dependencies", {}).items():
        if dependency_name != inp_name:
            continue

        if is_completed_queue_status(status):
            if joblist_state["statuses"].get(dependent_name) == "等待前置":
                joblist_state["statuses"][dependent_name] = "等待"
                oldjob_path = joblist_state["oldjob_paths"].get(dependent_name, "")
                append_history_text(
                    f"释放 Restart 作业：{dependent_name}\n"
                    f"前置作业 {inp_name} 已完成，oldjob ODB：{oldjob_path}\n\n"
                )
        else:
            joblist_state["statuses"][dependent_name] = "跳过"
            append_history_text(
                f"跳过 Restart 作业：{dependent_name}\n"
                f"原因：前置作业 {inp_name} 未正常完成（{status}）。\n\n"
            )

    update_joblist_status_label()
    update_joblist_button_mode()
    root.after(500, dispatch_joblist)


def stop_joblist_queue(source_job_state=None):
    """停止队列继续提交后续等待作业，不影响已经运行的作业。"""
    if not joblist_state["active"]:
        return

    joblist_state["active"] = False
    skipped = 0
    for name in joblist_state["jobs"]:
        if joblist_state["statuses"].get(name) in ("等待", "等待前置"):
            joblist_state["statuses"][name] = "已停止"
            skipped += 1

    update_joblist_status_label("队列：已终止，不再提交等待作业")
    update_joblist_button_mode()
    append_history_text(f"队列已终止，停止提交等待作业 {skipped} 个。\n\n")

    if source_job_state is not None:
        log_widget = source_job_state.get("log_widget")
        if log_widget is not None:
            append_log(log_widget, f"状态：队列已终止，停止提交等待作业 {skipped} 个。\n")

    update_joblist_button_mode()


def submit_job(inp_file_override="", queue_mode=False, oldjob_path_override=""):
    """提交 Abaqus 作业"""
    inp_file = inp_file_override or inp_file_var.get().strip()
    cpus_text = cpus_var.get().strip()
    oldjob_path = oldjob_path_override if oldjob_path_override else ("" if queue_mode else oldjob_var.get().strip())
    oldjob_name = get_oldjob_name_from_path(oldjob_path) if oldjob_path else ""
    for_file_path = for_file_var.get().strip()
    interactive_mode = interactive_var.get()
    memory_argument = get_memory_argument()
    datacheck_mode = datacheck_var.get()

    if not inp_file:
        messagebox.showerror("错误", "请选择 Abaqus INP 文件。")
        return ""

    if not os.path.isfile(inp_file):
        messagebox.showerror("错误", f"INP 文件不存在：\n{inp_file}")
        return ""

    if os.path.splitext(inp_file)[1].lower() != ".inp":
        messagebox.showerror("错误", "请选择 .inp 后缀的文件。")
        return ""

    work_dir = os.path.dirname(inp_file)
    job_name = os.path.splitext(os.path.basename(inp_file))[0]
    job_name_var.set(job_name)

    if not validate_abaqus_job_name(job_name):
        return ""

    if not oldjob_path and inp_has_restart_keyword(inp_file):
        messagebox.showinfo(
            "检测到重启动作业",
            f"INP 文件“{os.path.basename(inp_file)}”头部包含 *Restart。\n\n"
            "请选择对应的 oldjob ODB。"
        )
        oldjob_path = select_oldjob_path_for_restart(os.path.basename(inp_file))
        if not oldjob_path:
            return ""
        oldjob_name = get_oldjob_name_from_path(oldjob_path)

    if oldjob_path:
        if not os.path.isfile(oldjob_path):
            if queue_mode and oldjob_path_override:
                append_history_text(
                    f"等待 oldjob ODB 生成：{os.path.basename(inp_file)} -> {oldjob_path}\n"
                    "将在 10 秒内每 2 秒检测一次。\n\n"
                )
                if wait_for_oldjob_odb(oldjob_path):
                    pass
                else:
                    messagebox.showerror("错误", f"ODB 文件不存在：\n{oldjob_path}")
                    return ""
            else:
                messagebox.showerror("错误", f"ODB 文件不存在：\n{oldjob_path}")
                return ""

        if not os.path.isfile(oldjob_path):
            messagebox.showerror("错误", f"ODB 文件不存在：\n{oldjob_path}")
            return ""

        if os.path.splitext(oldjob_path)[1].lower() != ".odb":
            messagebox.showerror("错误", "请选择 .odb 后缀的文件。")
            return ""

        if not validate_abaqus_job_name(oldjob_name, "重启动作业名称"):
            return ""

        if oldjob_name and oldjob_name == job_name:
            messagebox.showerror(
                "重启动作业名称错误",
                f"当前作业名称和重启动 oldjob 名称不能相同：\n\n"
                f"job = {job_name}\n"
                f"oldjob = {oldjob_name}\n\n"
                "请将当前 INP 文件改成新的作业名，例如：\n"
                f"{job_name}_restart.inp"
            )
            return ""

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
        odb_action = get_existing_odb_action_for_submit(
            job_name,
            existing_odb_file,
            queue_mode
        )


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
        "from_joblist": queue_mode,
        "joblist_inp_name": os.path.basename(inp_file) if queue_mode else "",
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
        "memory_monitor_active": False,
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
        start_global_runtime_status_monitor()
        start_job_memory_monitor(job_state)

        append_log(log_widget, "状态：提交命令已发送，正在等待 Abaqus 响应。\n")
        start_process_output_monitor(process, log_widget, job_state)
        append_submit_history(job_state)

        if datacheck_mode:
            set_job_status(job_state, "Datacheck | Running")
            start_datacheck_monitor(process, submitted_at, job_state)
        else:
            set_job_status(job_state, "Running")
            start_sta_monitor(sta_file, process, submitted_at, job_state)

        if not queue_mode:
            messagebox.showinfo("已提交", "Abaqus 作业已后台提交。")

        return job_name

    except Exception as e:
        messagebox.showerror("提交失败", str(e))
        append_log(log_widget, f"提交失败：{e}\n")
        finalize_job(job_state, "失败", "提交命令执行失败")
        return ""


# ================= 主窗口 =================

root = tk.Tk()
root.title("Abaqus 并行队列提交工具")
root.geometry(LEFT_ONLY_GEOMETRY)
root.minsize(*LEFT_ONLY_MIN_SIZE)
root.maxsize(*LEFT_ONLY_MIN_SIZE)
root.resizable(False, False)
root.configure(bg=APP_BG)
root.option_add("*Font", FONT_ENTRY)

# ================= 变量 =================

inp_file_var = tk.StringVar()
job_name_var = tk.StringVar()
cpus_var = tk.StringVar(value=str(DEFAULT_CPUS))
command_var = tk.StringVar()
oldjob_var = tk.StringVar()
for_file_var = tk.StringVar()
memory_mode_var = tk.StringVar(value="默认")
custom_memory_var = tk.StringVar(value="")
interactive_var = tk.BooleanVar(value=False)
datacheck_var = tk.BooleanVar(value=False)
complete_notify_var = tk.BooleanVar(value=True)
abaqus_status_var = tk.StringVar(value="Abaqus 状态：待检测")
joblist_status_var = tk.StringVar(value="队列：未生成")
joblist_max_parallel_var = tk.StringVar(value=str(calculate_default_joblist_parallel(DEFAULT_CPUS)))
joblist_max_parallel_var.trace_add("write", lambda *_: update_joblist_status_label())

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
main_frame.pack(padx=12, pady=(6, 12), anchor="nw")

body_frame = ttk.Frame(main_frame, style="Main.TFrame")
body_frame.pack(anchor="nw")
body_frame.columnconfigure(0, minsize=LEFT_PANEL_WIDTH, weight=0)
body_frame.columnconfigure(1, minsize=0, weight=0)
body_frame.rowconfigure(0, weight=1)

left_panel = ttk.Frame(
    body_frame,
    style="Main.TFrame",
    width=LEFT_PANEL_WIDTH,
    height=PANEL_HEIGHT
)
left_panel.grid(row=0, column=0, sticky="nsw", padx=(0, 0))
left_panel.grid_propagate(False)
left_panel.pack_propagate(False)

right_panel = None
log_card = None
log_inner = None
log_notebook = None

# ================= 左侧提交表单 =================

card = ttk.Frame(left_panel, style="Card.TFrame")
card.pack(fill="x", pady=(0, 6))

ttk.Label(
    card,
    textvariable=abaqus_status_var,
    style="Hint.TLabel"
).pack(anchor="w", padx=16, pady=(0, 0))

inner = ttk.Frame(card, style="Card.TFrame")
inner.pack(fill="x", padx=16, pady=(8, 10))
inner.columnconfigure(0, minsize=34, weight=0)
inner.columnconfigure(1, minsize=340, weight=0)

ttk.Label(
    inner,
    text="INP",
    width=4,
    anchor="w",
    style="Normal.TLabel"
).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 12))

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
inp_file_entry.grid(row=0, column=1, sticky="ew", ipady=4, pady=(0, 12))
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
).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(0, 12))

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
oldjob_entry.grid(row=1, column=1, sticky="ew", ipady=4, pady=(0, 12))
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
).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(0, 12))

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
for_file_entry.grid(row=2, column=1, sticky="ew", ipady=4, pady=(0, 12))
set_optional_file_entry(
    for_file_entry,
    "",
    FOR_FILE_PLACEHOLDER,
    prefix="FOR"
)
for_file_entry.bind("<Button-1>", select_for_file_from_entry)

settings_row = ttk.Frame(inner, style="Card.TFrame")
settings_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 10))
settings_row.columnconfigure(0, minsize=34, weight=0)
settings_row.columnconfigure(1, weight=0)
settings_row.columnconfigure(3, weight=1)

ttk.Label(
    settings_row,
    text="Core",
    width=4,
    anchor="w",
    style="Normal.TLabel"
).grid(row=0, column=0, sticky="w")

cpus_entry = ctk.CTkEntry(
    settings_row,
    textvariable=cpus_var,
    width=52,
    height=30,
    corner_radius=0,
    border_width=1,
    border_color="#000000",
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
    text="Mem",
    style="Normal.TLabel"
).grid(row=0, column=4, sticky="e", padx=(16, 6))

memory_group = ttk.Frame(settings_row, style="Card.TFrame")
memory_group.grid(row=0, column=5, sticky="e")

custom_memory_entry = ctk.CTkEntry(
    memory_group,
    width=52,
    height=30,
    corner_radius=0,
    border_width=1,
    border_color="#000000",
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
    height=30,
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
options_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 10))

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
button_row.grid(row=5, column=0, columnspan=2, sticky="ew")
for button_column in range(4):
    button_row.columnconfigure(button_column, weight=1, uniform="action_buttons")

generate_cmd_btn = ctk.CTkButton(
    button_row,
    text="预览命令",
    width=86,
    height=32,
    corner_radius=8,
    font=FONT_BUTTON_BOLD,
    fg_color=BTN_LIGHT_FG,
    hover_color=BTN_LIGHT_HOVER,
    text_color=BTN_LIGHT_TEXT,
    bg_color="#ffffff",
    command=preview_command
)
generate_cmd_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

submit_btn = ctk.CTkButton(
    button_row,
    text="提交作业",
    width=86,
    height=32,
    corner_radius=8,
    font=FONT_BUTTON_BOLD,
    fg_color="#2563eb",
    hover_color="#1d4ed8",
    text_color="white",
    bg_color="#ffffff",
    command=submit_job
)
submit_btn.grid(row=0, column=1, sticky="ew", padx=(0, 6))

build_joblist_btn = ctk.CTkButton(
    button_row,
    text="生成队列",
    width=86,
    height=32,
    corner_radius=8,
    font=FONT_BUTTON_BOLD,
    fg_color=BTN_LIGHT_FG,
    hover_color=BTN_LIGHT_HOVER,
    text_color=BTN_LIGHT_TEXT,
    bg_color="#ffffff",
    command=select_joblist_dir
)
build_joblist_btn.grid(row=0, column=2, sticky="ew", padx=(0, 6))

start_joblist_btn = ctk.CTkButton(
    button_row,
    text="开始队列",
    width=86,
    height=32,
    corner_radius=8,
    font=FONT_BUTTON_BOLD,
    fg_color="#2563eb",
    hover_color="#1d4ed8",
    text_color="white",
    bg_color="#ffffff",
    command=start_joblist
)
start_joblist_btn.grid(row=0, column=3, sticky="ew")

joblist_limit_controls = ttk.Frame(button_row, style="Card.TFrame")
joblist_limit_controls.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

ttk.Label(
    joblist_limit_controls,
    text="队列最大并行",
    style="Normal.TLabel"
).grid(row=0, column=0, sticky="w", padx=(0, 8))

joblist_max_parallel_entry = ctk.CTkEntry(
    joblist_limit_controls,
    textvariable=joblist_max_parallel_var,
    width=52,
    height=30,
    corner_radius=0,
    border_width=1,
    border_color="#000000",
    fg_color="#ffffff",
    text_color="#111827",
    justify="center",
    font=FONT_NUMERIC_ENTRY
)
joblist_max_parallel_entry.grid(row=0, column=1, sticky="w", padx=(0, 8))
joblist_max_parallel_entry.bind("<Return>", apply_joblist_max_parallel_change)
joblist_max_parallel_entry.bind("<KP_Enter>", apply_joblist_max_parallel_change)

ttk.Label(
    joblist_limit_controls,
    text="达到上限后暂停补位",
    style="Hint.TLabel"
).grid(row=0, column=2, sticky="w")

stop_joblist_btn = ctk.CTkButton(
    button_row,
    text="终止队列",
    width=86,
    height=32,
    corner_radius=8,
    font=FONT_BUTTON_BOLD,
    fg_color="#7f1d1d",
    hover_color="#991b1b",
    text_color="white",
    bg_color="#ffffff",
    state="disabled",
    command=stop_joblist_queue
)
stop_joblist_btn.grid(row=1, column=3, sticky="ew", pady=(8, 0))

ttk.Label(
    inner,
    textvariable=joblist_status_var,
    style="Hint.TLabel"
).grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

# ================= 左侧提交记录 =================

history_card = ttk.Frame(left_panel, style="Card.TFrame")
history_card.pack(fill="both", expand=True)

history_inner = ttk.Frame(history_card, style="Card.TFrame")
history_inner.pack(fill="both", expand=True, padx=18, pady=(4, 0))
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

# ================= 启动程序 =================

root.after_idle(lambda: root.after(1000, detect_abaqus_command))
root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()
