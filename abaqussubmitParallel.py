import ctypes
import codecs
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk

import customtkinter as ctk

try:
    import psutil
except ImportError:
    psutil = None


from abaqus_submitter.constants import *
from abaqus_submitter.models import MemoryStatusEx, QueueItem
from abaqus_submitter.persistence import atomic_write_json
from abaqus_submitter.process_scanner import *
from abaqus_submitter.ui_performance import (
    ENABLE_UI_PERFORMANCE_LOG,
    UI_EVENT_QUEUE_MAX_EVENTS_PER_TICK,
    configure_ui_performance,
    log_ui_queue_status,
    log_worker_performance,
    measure_ui_callback,
    start_ui_lag_watchdog,
    stop_ui_lag_watchdog,
)
from abaqus_submitter.abaqus_diagnostics import (
    abaqus_stage_started,
    build_sta_table_header,
    classify_job_text,
    clear_diagnostic_file_cache,
    format_sta_output_for_log,
    get_existing_job_backup_time_tag,
    inspect_job_files,
    inspect_job_files_throttled,
    parse_sta_progress,
    read_file_tail,
    update_abaqus_stage_from_text,
)


UI_EVENT_POLL_INTERVAL_MS = 300

log_tab_counter = 0
right_panel_visible = False
application_closing = False
ui_event_queue = Queue()
ui_event_poll_after_id = None
active_jobs = {}
inp_restart_keyword_cache = {}
queue_lock = threading.Lock()
queue_candidates = []
queue_items = []
queue_manager_window = None
queue_candidate_tree = None
queue_formal_tree = None
queue_candidate_summary_var = None
queue_formal_summary_var = None
queue_scan_subdirs_var = None
queue_skip_restart_var = None
queue_skip_existing_var = None
queue_work_dir_var = None
queue_work_dir_combo = None
queue_scan_external_btn = None
queue_work_dir_history = []
queue_manager_view_signature = {
    "candidate": None,
    "formal": None,
}
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
    "dispatch_after_id": None,
    "dispatch_due_time": 0.0,
    "external_slot_notice_signature": "",
}
job_tab_records = {}
job_selector_var = None
job_selector = None
job_selector_name_label = None
job_selector_arrow_label = None
job_selector_popup = None
job_stats_var = None
memory_monitor_state = {
    "running": False,
    "after_id": None,
    "scanning": False,
    "generation": 0,
}
external_job_monitor_state = {
    "running": False,
    "after_id": None,
    "scanning": False,
    "generation": 0,
}
formal_queue_save_state = {
    "after_id": None,
}
runtime_status_state = {
    "running": False,
}
job_memory_estimates = {}
memory_safety_factor_state = {
    "value": JOB_MEMORY_BASE_SAFETY_FACTOR,
}


def refresh_sta_header_index_after_trim(log_widget, job_state):
    """Re-find the STA header after old log lines are trimmed."""
    if job_state is None or not job_state.get("sta_fixed_header_ready"):
        return

    try:
        header_text = build_sta_table_header()
        header_index = log_widget.search(header_text, "1.0", tk.END)
        if header_index:
            job_state["sta_header_index"] = log_widget.index(f"{header_index} linestart")
        else:
            job_state["sta_header_index"] = ""
            job_state["sta_fixed_header_ready"] = False
            header_label = job_state.get("sta_header_label")
            if header_label is not None and header_label.winfo_exists():
                header_label.grid_remove()
    except tk.TclError:
        pass


def trim_text_widget(widget, max_lines):
    """Keep only recent lines in a Tk text widget."""
    try:
        line_count = int(widget.index("end-1c").split(".", 1)[0])
    except (tk.TclError, ValueError):
        return

    if line_count <= max_lines:
        return

    delete_to_line = line_count - max_lines + 1
    try:
        widget.delete("1.0", f"{delete_to_line}.0")
        job_state = getattr(widget, "job_state", None)
        if job_state is not None:
            refresh_sta_header_index_after_trim(widget, job_state)
            update_sta_fixed_header_visibility(job_state)
    except tk.TclError:
        pass


def trim_log_trailing_blank_lines(log_widget, max_blank_lines=100):
    """Remove accidental blank lines before appending formatted STA output."""
    try:
        for _ in range(max_blank_lines):
            end_index = log_widget.index("end-1c")
            end_line, end_col = (int(part) for part in end_index.split(".", 1))
            if end_line <= 1:
                return

            candidate_line = end_line if end_col else end_line - 1
            line_text = log_widget.get(
                f"{candidate_line}.0",
                f"{candidate_line}.end"
            )
            if line_text.strip():
                return

            log_widget.delete(
                f"{candidate_line}.0",
                f"{candidate_line + 1}.0"
            )
    except (tk.TclError, ValueError):
        pass


def append_log(log_widget, text):
    """向指定日志页追加文本并滚动到底部。"""
    with measure_ui_callback("append_log"):
        try:
            if log_widget.winfo_exists():
                insert_start = log_widget.index("end-1c")
                log_widget.insert(tk.END, text)
                log_widget.see(tk.END)
                job_state = getattr(log_widget, "job_state", None)
                if job_state is not None:
                    remember_sta_header_index(log_widget, job_state, insert_start, text)
                    job_state["log_append_count"] = job_state.get("log_append_count", 0) + 1
                    if job_state["log_append_count"] % LOG_TRIM_CHECK_INTERVAL == 0:
                        trim_text_widget(log_widget, MAX_JOB_LOG_LINES)
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
    with measure_ui_callback("append_history_text"):
        if getattr(history_text, "placeholder_visible", False):
            history_text.delete("1.0", tk.END)
            history_text.placeholder_visible = False
    
        if tag:
            history_text.insert(tk.END, text, tag)
        else:
            history_text.insert(tk.END, text)
    
        append_count = getattr(history_text, "append_count", 0) + 1
        history_text.append_count = append_count
        if append_count % LOG_TRIM_CHECK_INTERVAL == 0:
            trim_text_widget(history_text, MAX_HISTORY_LOG_LINES)
    
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










































def estimate_per_job_memory_from_running_jobs():
    """Estimate one Abaqus job memory need from saved peaks and running samples."""
    usage_by_job = get_cached_abaqus_job_memory_usage()
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
            or not usage
    ):
        return

    job_name = job_state["job_name"]
    memory = int(usage.get("private_memory") or usage.get("working_set") or 0)
    if memory <= 0:
        return

    samples = job_state.setdefault("memory_samples", [])
    mode = job_state.get("memory_monitor_mode", "learning")
    peak_before = int(job_state.get("memory_peak", 0))
    peak_after = max(peak_before, memory)
    new_peak_ratio = (
        memory / peak_before
        if peak_before > 0 else 999
    )
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

    if mode == "patrol" and peak_before > 0 and new_peak_ratio > 1 + JOB_MEMORY_STABLE_RELATIVE_DELTA:
        job_state["memory_monitor_mode"] = "learning"
        job_state["memory_stable_polls"] = 0
        job_memory_estimates[job_name]["stable"] = False
        job_state["memory_stable_logged"] = False
    elif peak_after > peak_before * (1 + JOB_MEMORY_STABLE_RELATIVE_DELTA):
        job_state["memory_stable_polls"] = 0
    elif mode == "learning" and len(samples) >= JOB_MEMORY_MIN_SAMPLES:
        job_state["memory_stable_polls"] = job_state.get("memory_stable_polls", 0) + 1

    if (
            mode == "learning"
            and (
            job_state.get("memory_stable_polls", 0) >= JOB_MEMORY_STABLE_POLLS
            or len(samples) >= JOB_MEMORY_MAX_SAMPLES
            )
    ):
        job_memory_estimates[job_name]["stable"] = True
        job_state["memory_monitor_mode"] = "patrol"
        if not job_state.get("memory_stable_logged"):
            job_state["memory_stable_logged"] = True
            monitor_time = time.strftime("%Y-%m-%d %H:%M:%S")
            append_history_text(f"[{monitor_time}]\n", "history_time")
            append_history_text(
                f"{job_name}：状态：内存监测已稳定，"
                f"峰值 {format_memory_size(peak_after)}，"
                f"估算 {format_memory_size(job_memory_estimates[job_name]['estimated_memory'])}，"
                "转入低频巡检。\n\n"
            )

    interval_ms = (
        JOB_MEMORY_PATROL_INTERVAL_MS
        if job_state.get("memory_monitor_mode") == "patrol"
        else JOB_MEMORY_LEARNING_INTERVAL_MS
    )
    job_state["next_memory_sample_at"] = time.monotonic() + interval_ms / 1000


def start_job_memory_monitor(job_state):
    """Initialize memory monitor fields; sampling starts after .sta appears."""
    job_state["memory_samples"] = []
    job_state["memory_peak"] = 0
    job_state["memory_stable_polls"] = 0
    job_state["memory_monitor_stopped"] = False
    job_state["memory_monitor_mode"] = "learning"
    job_state["memory_stable_logged"] = False
    job_state["next_memory_sample_at"] = 0.0


def update_external_job_memory_estimate(item):
    """Use imported external job RSS samples as low-frequency memory references."""
    memory = int(item.rss_bytes or 0)
    if memory <= 0 or not item.job_name:
        return

    estimate = job_memory_estimates.setdefault(
        item.job_name,
        {
            "group": infer_model_group(item.job_name),
            "step_peaks": {},
        }
    )
    peak_before = int(estimate.get("peak_memory") or 0)
    peak_after = max(peak_before, memory)
    estimate.update(
        {
            "estimated_memory": max(
                int(estimate.get("estimated_memory") or 0),
                int(peak_after * get_memory_safety_factor())
            ),
            "peak_memory": peak_after,
            "sample_count": int(estimate.get("sample_count") or 0) + 1,
            "process_count": len(item.pids or []),
            "process_names": "external",
            "updated_at": time.time(),
            "stable": estimate.get("stable", False),
        }
    )


def queue_ui_event(event_type, payload):
    """Send a background worker result to the Tk main thread."""
    if application_closing:
        return
    ui_event_queue.put((event_type, payload))


def process_ui_event_queue():
    """Apply queued worker results on the Tk main thread."""
    global ui_event_poll_after_id

    if application_closing:
        ui_event_poll_after_id = None
        return

    queue_size_before = ui_event_queue.qsize()
    started_at = time.perf_counter()
    processed = 0
    with measure_ui_callback("process_ui_event_queue"):
        try:
            while processed < UI_EVENT_QUEUE_MAX_EVENTS_PER_TICK:
                try:
                    event_type, payload = ui_event_queue.get_nowait()
                except Empty:
                    break

                if event_type == "global_memory_scan_finished":
                    apply_global_memory_scan_result(payload)
                elif event_type == "global_memory_scan_failed":
                    apply_global_memory_scan_failure(payload)
                elif event_type == "external_job_scan_finished":
                    apply_external_job_scan_result(payload)
                elif event_type == "external_job_scan_failed":
                    apply_external_job_scan_failure(payload)

                processed += 1
        except tk.TclError:
            ui_event_poll_after_id = None
            return

    elapsed_ms = (time.perf_counter() - started_at) * 1000
    log_ui_queue_status(
        queue_size_before=queue_size_before,
        processed_count=processed,
        remaining_count=ui_event_queue.qsize(),
        elapsed_ms=elapsed_ms,
    )

    if not application_closing:
        ui_event_poll_after_id = root.after(
            UI_EVENT_POLL_INTERVAL_MS,
            process_ui_event_queue
        )


def start_ui_event_queue_polling():
    """Start the single main-thread event queue poller."""
    global ui_event_poll_after_id

    if application_closing or ui_event_poll_after_id is not None:
        return

    ui_event_poll_after_id = root.after(
        UI_EVENT_POLL_INTERVAL_MS,
        process_ui_event_queue
    )


def activate_job_memory_monitor(job_state):
    """Begin shared memory sampling once the job has generated its .sta file."""
    if (
            job_state.get("finalized")
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
    memory_monitor_state["after_id"] = root.after(
        JOB_MEMORY_LEARNING_INTERVAL_MS,
        run_global_memory_monitor
    )


def get_memory_tracked_jobs():
    """Return active GUI jobs whose memory monitor is enabled."""
    return [
        state for state in active_jobs.values()
        if (
                not state.get("finalized")
                and state.get("memory_monitor_active")
        )
    ]


def schedule_next_global_memory_monitor(tracked_jobs=None, delay_ms=None):
    """Schedule the next global memory monitor tick on the Tk main thread."""
    if application_closing:
        memory_monitor_state["running"] = False
        memory_monitor_state["after_id"] = None
        return

    if tracked_jobs is None:
        tracked_jobs = get_memory_tracked_jobs()

    if not tracked_jobs:
        memory_monitor_state["running"] = False
        memory_monitor_state["after_id"] = None
        return

    if delay_ms is None:
        now = time.monotonic()
        active_due_times = [
            float(state.get("next_memory_sample_at") or now)
            for state in tracked_jobs
            if not state.get("finalized")
        ]
        if not active_due_times:
            memory_monitor_state["running"] = False
            memory_monitor_state["after_id"] = None
            return
        next_due = min(active_due_times)
        delay_ms = max(1000, int((next_due - time.monotonic()) * 1000))

    memory_monitor_state["after_id"] = root.after(
        delay_ms,
        run_global_memory_monitor
    )


def run_global_memory_scan_worker(generation, due_job_refs):
    """Collect Abaqus memory usage away from the Tk main thread."""
    started_at = time.perf_counter()
    try:
        usage_by_job = get_abaqus_job_memory_usage(force=True)
    except Exception as exc:
        if not application_closing:
            queue_ui_event(
                "global_memory_scan_failed",
                {
                    "generation": generation,
                    "error": str(exc),
                }
            )
    else:
        if not application_closing:
            queue_ui_event(
                "global_memory_scan_finished",
                {
                    "generation": generation,
                    "due_job_refs": due_job_refs,
                    "usage_by_job": usage_by_job,
                }
            )
    finally:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        log_worker_performance(
            "global_memory_scan_worker",
            elapsed_ms,
            due_job_count=len(due_job_refs),
            usage_job_count=len(locals().get("usage_by_job", {}) or {}),
            status="failed" if "exc" in locals() else "success",
        )


def run_global_memory_monitor():
    """Start a background memory sample when one or more jobs are due."""
    memory_monitor_state["after_id"] = None
    if application_closing:
        memory_monitor_state["running"] = False
        return

    now = time.monotonic()
    tracked_jobs = get_memory_tracked_jobs()

    if not tracked_jobs:
        memory_monitor_state["running"] = False
        return

    due_jobs = [
        state for state in tracked_jobs
        if float(state.get("next_memory_sample_at") or 0) <= now
    ]

    if not due_jobs:
        schedule_next_global_memory_monitor(tracked_jobs)
        return

    if memory_monitor_state.get("scanning"):
        schedule_next_global_memory_monitor(tracked_jobs, delay_ms=1000)
        return

    memory_monitor_state["generation"] += 1
    memory_monitor_state["scanning"] = True
    generation = memory_monitor_state["generation"]
    due_job_refs = [
        {
            "job_key": state.get("job_key", ""),
            "job_name": state.get("job_name", ""),
            "work_dir": state.get("work_dir", ""),
        }
        for state in due_jobs
    ]
    threading.Thread(
        target=run_global_memory_scan_worker,
        args=(generation, due_job_refs),
        daemon=True
    ).start()


def reschedule_due_memory_jobs(due_job_refs):
    """Move due jobs to their next sample time when a scan has no usable result."""
    for ref in due_job_refs:
        job_state = active_jobs.get(ref.get("job_key", ""))
        if job_state is None or job_state.get("finalized"):
            continue
        mode = job_state.get("memory_monitor_mode", "learning")
        interval_ms = (
            JOB_MEMORY_PATROL_INTERVAL_MS
            if mode == "patrol"
            else JOB_MEMORY_LEARNING_INTERVAL_MS
        )
        job_state["next_memory_sample_at"] = time.monotonic() + interval_ms / 1000


def apply_global_memory_scan_result(payload):
    """Apply one background memory scan result on the Tk main thread."""
    if payload.get("generation") != memory_monitor_state.get("generation"):
        return

    memory_monitor_state["scanning"] = False
    usage_by_job = payload.get("usage_by_job") or {}
    due_job_refs = payload.get("due_job_refs") or []

    for ref in due_job_refs:
        job_state = active_jobs.get(ref.get("job_key", ""))
        if job_state is None or job_state.get("finalized"):
            continue
        usage = usage_by_job.get(ref.get("job_name", ""))
        if usage:
            update_job_memory_sample(job_state, usage)
        else:
            reschedule_due_memory_jobs([ref])

    schedule_next_global_memory_monitor()


def apply_global_memory_scan_failure(payload):
    """Recover the memory monitor after a background scan failure."""
    if payload.get("generation") != memory_monitor_state.get("generation"):
        return

    memory_monitor_state["scanning"] = False
    schedule_next_global_memory_monitor(delay_ms=JOB_MEMORY_LEARNING_INTERVAL_MS)


def get_active_job_count():
    """Return the number of active jobs tracked by this GUI."""
    return sum(
        1 for state in active_jobs.values()
        if not state.get("finalized")
    )


def get_active_job_keys():
    """Return unique keys for non-finalized jobs submitted by this GUI."""
    keys = set()
    for state in active_jobs.values():
        if state.get("finalized"):
            continue
        work_dir = state.get("work_dir", "")
        job_name = state.get("job_name", "")
        if work_dir and job_name:
            keys.add(get_job_key(work_dir, job_name))

    return keys


def get_external_active_job_count(active_job_keys=None):
    """Return active imported external jobs, excluding GUI-managed duplicates."""
    if active_job_keys is None:
        active_job_keys = get_active_job_keys()

    count = 0
    for item in queue_items:
        if not item.is_external or not is_managed_active_queue_status(item.status):
            continue

        work_dir = item.external_work_dir or os.path.dirname(item.inp_path)
        item_key = get_job_key(work_dir, item.job_name) if work_dir and item.job_name else ""
        if item_key and item_key in active_job_keys:
            continue

        count += 1

    return count


def get_total_managed_active_job_count():
    """Return GUI-submitted plus imported external active jobs without duplicates."""
    active_job_keys = get_active_job_keys()
    return len(active_job_keys) + get_external_active_job_count(active_job_keys)


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


def refresh_job_selector_for_job(job_state):
    """Refresh selector colors when a running job state changes."""
    tab_frame = job_state.get("tab_frame")
    record = job_tab_records.get(tab_frame)
    if record is None:
        return

    if not record.get("status"):
        update_job_selector_values()


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
    global application_closing
    global ui_event_poll_after_id

    application_closing = True
    cancel_scheduled_formal_queue_save()
    if ui_event_poll_after_id:
        try:
            root.after_cancel(ui_event_poll_after_id)
        except tk.TclError:
            pass
        ui_event_poll_after_id = None
    for state in (memory_monitor_state, external_job_monitor_state):
        after_id = state.get("after_id")
        if after_id:
            try:
                root.after_cancel(after_id)
            except tk.TclError:
                pass
        state["after_id"] = None
        state["running"] = False
        state["scanning"] = False
    try:
        save_formal_queue_file()
    except OSError:
        pass
    stop_ui_lag_watchdog()
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
    content_frame.columnconfigure(1, minsize=LOG_SCROLLBAR_WIDTH, weight=0)
    content_frame.grid_propagate(False)

    toolbar = ttk.Frame(content_frame, style="Card.TFrame")
    toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))

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
    log_scrollbar = ttk.Scrollbar(
        content_frame,
        orient="vertical",
        command=log_widget.yview,
        style="Queue.Vertical.TScrollbar",
    )
    log_scrollbar.grid(row=3, column=1, sticky="ns")
    log_widget.configure(yscrollcommand=log_scrollbar.set)
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
    filebar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
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
    if tab_frame in job_tab_records:
        job_tab_records[tab_frame]["job_state"] = job_state

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
        "job_state": None,
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
        if job_selector_name_label is not None:
            job_selector_name_label.configure(
                text=job_selector_var.get() if job_selector_var is not None else "无作业"
            )
    except tk.TclError:
        return

    running_count = 0
    done_count = 0
    failed_count = 0
    for record in records:
        status = get_job_selector_record_status(record)
        if status in ("完成", "Datacheck Completed"):
            done_count += 1
        elif status == "Paused":
            running_count += 1
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


def format_job_selector_button_text():
    """Return the compact selector button text with a dropdown indicator."""
    if job_selector_var is None:
        return "无作业"

    return job_selector_var.get() or "无作业"


def bind_job_selector_click(widget):
    """Make one part of the custom job selector open the popup."""
    widget.bind("<Button-1>", lambda _event: show_job_selector_popup())
    widget.bind("<Enter>", lambda _event: update_selected_job_selector_style(hover=True))
    widget.bind("<Leave>", lambda _event: update_selected_job_selector_style(hover=False))


def update_selected_job_selector_style(hover=False):
    """Tint the selected job selector by the selected job's final status."""
    if job_selector is None or job_selector_var is None:
        return

    selected_title = job_selector_var.get()
    selected_status = ""
    for record in job_tab_records.values():
        if record.get("title") == selected_title:
            selected_status = get_job_selector_record_status(record)
            break

    _, fg_color, text_color, hover_color = get_job_selector_status_info(selected_status)
    display_color = hover_color if hover else fg_color

    try:
        job_selector.configure(
            fg_color=display_color,
        )
        if job_selector_name_label is not None:
            job_selector_name_label.configure(
                text=format_job_selector_button_text(),
                fg_color=display_color,
                text_color=text_color,
            )
        if job_selector_arrow_label is not None:
            job_selector_arrow_label.configure(
                fg_color=display_color,
                text_color=text_color,
            )
    except tk.TclError:
        pass


def get_job_selector_status_info(status):
    """Return display status and colors for one selector row."""
    if status in ("完成", "Datacheck Completed"):
        return "Completed", "#dcfce7", "#166534", "#bbf7d0"

    if status:
        if status == "Paused":
            return "Paused", "#fef3c7", "#92400e", "#fde68a"

        if status == "Terminated":
            return "Terminated", "#fee2e2", "#991b1b", "#fecaca"

        return "Failed", "#fee2e2", "#991b1b", "#fecaca"

    return "Running", BTN_LIGHT_FG, BTN_LIGHT_TEXT, BTN_LIGHT_HOVER


def get_job_selector_record_status(record):
    """Return final or transient status for one selector record."""
    if record.get("status"):
        if record["status"] == "终止":
            return "Terminated"
        return record["status"]

    job_state = record.get("job_state")
    if job_state is not None and job_state.get("suspended"):
        return "Paused"

    return ""


def select_job_from_popup(tab_frame):
    """Switch to one job and close the custom selector popup."""
    close_job_selector_popup()
    select_job_tab(tab_frame)


def close_job_selector_popup():
    """Close the custom selector popup if it is open."""
    global job_selector_popup

    if job_selector_popup is not None:
        try:
            job_selector_popup.destroy()
        except tk.TclError:
            pass
        job_selector_popup = None


def show_job_selector_popup():
    """Show a color-coded custom job selector popup."""
    global job_selector_popup

    if job_selector is None or not job_tab_records:
        return

    if job_selector_popup is not None:
        close_job_selector_popup()
        return

    popup = tk.Toplevel(root)
    popup.overrideredirect(True)
    popup.configure(bg="#cbd5e1")
    popup.transient(root)
    job_selector_popup = popup

    container = tk.Frame(popup, bg="#cbd5e1", padx=1, pady=1)
    container.pack(fill="both", expand=True)

    row_width = job_selector.winfo_width()
    popup_font = (FONT_FAMILY, 11)
    for tab_frame, record in list(job_tab_records.items()):
        status_text, bg_color, text_color, hover_color = get_job_selector_status_info(
            get_job_selector_record_status(record)
        )
        row = tk.Frame(container, bg=bg_color, width=row_width, height=28)
        row.pack(fill="x", pady=(0, 1))
        row.pack_propagate(False)

        name_label = tk.Label(
            row,
            text=record.get("title", ""),
            bg=bg_color,
            fg=text_color,
            font=popup_font,
            anchor="w",
            padx=8
        )
        name_label.pack(side="left", fill="both", expand=True)

        status_label = tk.Label(
            row,
            text=status_text,
            bg=bg_color,
            fg=text_color,
            font=popup_font,
            anchor="e",
            padx=8,
            width=9
        )
        status_label.pack(side="right", fill="y")

        def bind_row(widget, frame=tab_frame, row_widgets=(row, name_label, status_label),
                     normal_bg=bg_color, hover_bg=hover_color):
            widget.bind("<Button-1>", lambda _event: select_job_from_popup(frame))
            widget.bind(
                "<Enter>",
                lambda _event: [
                    item.configure(bg=hover_bg) for item in row_widgets
                ]
            )
            widget.bind(
                "<Leave>",
                lambda _event: [
                    item.configure(bg=normal_bg) for item in row_widgets
                ]
            )

        for widget in (row, name_label, status_label):
            bind_row(widget)

    root.update_idletasks()
    x = job_selector.winfo_rootx()
    y = job_selector.winfo_rooty() + job_selector.winfo_height() + 2
    popup.geometry(f"{row_width}x{max(1, len(job_tab_records)) * 29}+{x}+{y}")
    popup.bind("<Escape>", lambda _event: close_job_selector_popup())
    popup.bind("<FocusOut>", lambda _event: close_job_selector_popup())
    popup.focus_force()


def sync_log_notebook_width(log_widget):
    """Keep the notebook border and right panel as wide as the actual log text widget."""
    try:
        log_widget.update_idletasks()
        log_width = log_widget.winfo_reqwidth() + LOG_SCROLLBAR_WIDTH
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
    global job_selector_name_label
    global job_selector_arrow_label
    global job_selector_popup
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
    job_selector_popup = None
    job_selector = ctk.CTkFrame(
        selector_row,
        width=220,
        height=30,
        corner_radius=7,
        fg_color=BTN_LIGHT_FG,
    )
    job_selector.grid(row=0, column=1, sticky="w")
    job_selector.grid_propagate(False)
    job_selector.columnconfigure(0, weight=1)
    job_selector.columnconfigure(1, weight=0)

    job_selector_name_label = ctk.CTkLabel(
        job_selector,
        text=format_job_selector_button_text(),
        height=30,
        anchor="w",
        font=FONT_JOB_SELECTOR,
        fg_color=BTN_LIGHT_FG,
        text_color=BTN_LIGHT_TEXT,
    )
    job_selector_name_label.grid(row=0, column=0, sticky="nsew", padx=(10, 4))

    job_selector_arrow_label = ctk.CTkLabel(
        job_selector,
        text="▼",
        width=24,
        height=30,
        anchor="center",
        font=FONT_JOB_SELECTOR,
        fg_color=BTN_LIGHT_FG,
        text_color=BTN_LIGHT_TEXT,
    )
    job_selector_arrow_label.grid(row=0, column=1, sticky="ns", padx=(0, 6))

    for selector_widget in (job_selector, job_selector_name_label, job_selector_arrow_label):
        bind_job_selector_click(selector_widget)

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
    if status == "终止" and detail and "手动终止" in detail:
        return "Terminated"

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
    clear_diagnostic_file_cache(
        work_dir=job_state["work_dir"],
        job_name=job_state["job_name"],
    )
    disable_job_controls(job_state)
    set_job_status(job_state, format_final_status_for_display(status, detail))
    append_job_final_history(job_state, status, detail)
    mark_job_tab_final_status(job_state, status)
    update_queue_item_from_final_job_status(job_state, status, detail)

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
    except (OSError, subprocess.SubprocessError) as e:
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
        refresh_job_selector_for_job(job_state)

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
        refresh_job_selector_for_job(job_state)


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
    queue_item_id = job_state.get("queue_item_id") or job_state.get("joblist_inp_name")
    queue_item = get_queue_item(queue_item_id) if queue_item_id else None
    if queue_item is not None:
        queue_item.status = STATUS_TERMINATING
        queue_item.message = "正在终止"
        joblist_state["statuses"][queue_item.item_id] = STATUS_TERMINATING
        refresh_queue_manager_views()


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
    if not stripped:
        return True

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
    with measure_ui_callback("run_global_runtime_status_monitor"):
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
    with measure_ui_callback("monitor_sta_file"):
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
                trim_log_trailing_blank_lines(log_widget)
                append_log(log_widget, display_text)

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


def get_oldjob_lck_path(oldjob_path):
    """Return the lock-file path that belongs to an oldjob ODB."""
    if not oldjob_path:
        return ""

    root_path, _extension = os.path.splitext(oldjob_path)
    return root_path + ".lck"


def check_oldjob_result_ready(oldjob_path):
    """Check that the restart oldjob ODB exists and its lock file is gone."""
    if not oldjob_path:
        return False, "未指定重启动依赖"

    if not os.path.isfile(oldjob_path):
        return False, "oldjob ODB 不存在"

    lck_path = get_oldjob_lck_path(oldjob_path)
    if lck_path and os.path.exists(lck_path):
        return False, "oldjob LCK 未释放"

    return True, ""


def get_queue_inp_name_for_oldjob_path(oldjob_path):
    """Return the queue INP name that can produce the missing oldjob ODB."""
    if not oldjob_path:
        return ""

    oldjob_inp_name = get_oldjob_name_from_path(oldjob_path) + ".inp"
    for item in joblist_state.get("jobs", []):
        if os.path.basename(get_joblist_item_path(item)).lower() == oldjob_inp_name.lower():
            return item

    return ""


def is_completed_queue_status(status):
    """Return whether a queue job ended successfully enough for dependents."""
    return status in ("完成", "Datacheck Completed", STATUS_COMPLETED)


def inp_has_restart_keyword(inp_file):
    """检查 INP 头部是否包含 *Restart。"""
    try:
        normalized_path = normalize_joblist_path(inp_file)
        stat_result = os.stat(normalized_path)
    except OSError:
        inp_restart_keyword_cache.pop(inp_file, None)
        return False

    signature = (stat_result.st_mtime_ns, stat_result.st_size)
    cached = inp_restart_keyword_cache.get(normalized_path)
    if cached and cached.get("signature") == signature:
        return bool(cached.get("has_restart"))

    try:
        has_restart = False
        with open(normalized_path, "r", encoding=STA_FILE_ENCODING, errors="replace") as file:
            for index, line in enumerate(file):
                stripped = line.strip().lower()

                if stripped.startswith("*step"):
                    break

                if stripped.startswith("*restart"):
                    has_restart = True
                    break

                if index >= 300:
                    break
        inp_restart_keyword_cache[normalized_path] = {
            "signature": signature,
            "has_restart": has_restart,
        }
        return has_restart
    except OSError:
        inp_restart_keyword_cache.pop(normalized_path, None)
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
        elif queue_candidates or queue_items:
            candidate_count = len(queue_candidates)
            pending_count = sum(1 for item in queue_items if item.status == STATUS_PENDING_RUN)
            dependency_count = sum(1 for item in queue_items if item.status == STATUS_WAITING_DEPENDENCY)
            running_count = sum(1 for item in queue_items if item.status in (STATUS_STARTING, STATUS_RUNNING, STATUS_TERMINATING))
            completed_count = sum(1 for item in queue_items if item.status == STATUS_COMPLETED)
            failed_count = sum(1 for item in queue_items if item.status == STATUS_FAILED)
            canceled_count = sum(1 for item in queue_items if item.status == STATUS_CANCELED)
            terminated_count = sum(1 for item in queue_items if item.status == STATUS_TERMINATED)
            stopped_count = canceled_count + terminated_count
            joblist_status_var.set(
                f"队列：候选 {candidate_count} | 待运行 {pending_count} | 前置 {dependency_count}\n"
                f"运行 {running_count} | 完成 {completed_count} | 失败 {failed_count} | 停止 {stopped_count}"
            )
        elif joblist_state["jobs"]:
            update_joblist_status_label("队列：已有旧队列数据，请打开管理队列检查")
        else:
            joblist_status_var.set("队列：未生成")
    except NameError:
        pass


def is_joblist_submitted():
    """Return True while an existing queue should be appended instead of replaced."""
    return bool(joblist_state.get("active") or joblist_state.get("running"))


def update_joblist_button_mode():
    """Refresh queue-management button states."""
    try:
        build_joblist_btn.configure(
            text="管理队列"
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
        schedule_dispatch_joblist(100)
    else:
        update_joblist_status_label()

    return "break"


def refresh_joblist_restart_jobs():
    """Rescan the queue before submission so restart detection is never stale."""
    restart_names = []

    for name in joblist_state.get("jobs", []):
        inp_file = get_joblist_item_path(name)
        if os.path.isfile(inp_file) and inp_has_restart_keyword(inp_file):
            restart_names.append(name)

    joblist_state["restart_jobs"] = restart_names
    return restart_names




def normalize_joblist_compare_path(path):
    """Return a case-normalized absolute path for duplicate detection."""
    return os.path.normcase(normalize_joblist_path(path))


def get_queue_item(item_id):
    """Return a formal queue item by id."""
    for queue_item in queue_items:
        if queue_item.item_id == item_id:
            return queue_item
    return None


def get_candidate_item(item_id):
    """Return a candidate queue item by id."""
    for queue_item in queue_candidates:
        if queue_item.item_id == item_id:
            return queue_item
    return None


def get_joblist_item_path(item):
    """Return the real INP path for either a queue file name or an absolute path."""
    queue_item = get_queue_item(item)
    if queue_item is not None:
        return normalize_joblist_path(queue_item.inp_path)

    if os.path.isabs(item):
        return normalize_joblist_path(item)

    work_dir = joblist_state.get("work_dir", "")
    return normalize_joblist_path(os.path.join(work_dir, item))


def get_joblist_item_display(item):
    """Return a queue item label that preserves the path when it matters."""
    if os.path.isabs(item):
        path = get_joblist_item_path(item)
        return f"{os.path.basename(path)} | {path}"

    return item


def queue_status_to_legacy(status):
    """Map formal queue status to the status dictionary used by existing helpers."""
    return status


def current_queue_parameter_snapshot():
    """Capture the main-window settings for queue items confirmed now."""
    try:
        cores = int(cpus_var.get().strip())
    except (NameError, ValueError):
        cores = DEFAULT_CPUS

    return {
        "cores": max(0, min(MAX_CPUS, cores)),
        "memory": get_memory_argument() if "memory_mode_var" in globals() else "",
        "fortran_path": for_file_var.get().strip() if "for_file_var" in globals() else "",
        "interactive": bool(interactive_var.get()) if "interactive_var" in globals() else False,
        "datacheck_only": bool(datacheck_var.get()) if "datacheck_var" in globals() else False,
        "complete_notify": bool(complete_notify_var.get()) if "complete_notify_var" in globals() else False,
    }


def get_queue_item_command_settings(queue_item):
    """Return per-job submit settings saved with a queue item."""
    return {
        "cpus_text": str(queue_item.cores),
        "memory_argument": queue_item.memory,
        "for_file_path": queue_item.fortran_path,
        "interactive_mode": queue_item.interactive,
        "datacheck_mode": queue_item.datacheck_only,
        "oldjob_path": queue_item.oldjob_path,
    }


def queue_status_from_final_status(status):
    """Map a finished Abaqus status to the formal queue status vocabulary."""
    if status in ("完成", "Datacheck Completed"):
        return STATUS_COMPLETED
    if status in ("终止", "Terminated"):
        return STATUS_TERMINATED
    return STATUS_FAILED


def final_status_from_queue_status(status):
    """Return whether a queue status means a dependency can be released."""
    return status in (STATUS_COMPLETED, "完成", "Datacheck Completed")


def is_managed_active_queue_status(status):
    """Return True for queue statuses that still occupy a running slot."""
    return status in (STATUS_STARTING, STATUS_RUNNING, STATUS_TERMINATING)


def external_job_items_to_monitor():
    """Return imported external jobs whose PID lifecycle should be monitored."""
    return [
        item for item in queue_items
        if item.is_external and is_managed_active_queue_status(item.status)
    ]


def get_external_job_lck_path(item):
    """Return the expected LCK path for an external job."""
    work_dir = item.external_work_dir or os.path.dirname(item.inp_path)
    if not work_dir or not item.job_name:
        return ""

    return os.path.join(work_dir, item.job_name + ".lck")


def classify_external_job_after_process_exit(item):
    """Classify an external job after all tracked processes disappeared."""
    work_dir = item.external_work_dir or os.path.dirname(item.inp_path)
    if not work_dir or not item.job_name:
        return STATUS_UNKNOWN, "外部作业相关进程已消失，但缺少工作目录或作业名，无法读取诊断文件。"

    final_status, detail = inspect_job_files(work_dir, item.job_name)
    if final_status == "完成":
        return STATUS_COMPLETED, detail or "检测到外部作业完成信息"
    if final_status == "终止":
        return STATUS_TERMINATED, detail or "检测到外部作业终止信息"
    if final_status == "失败":
        return STATUS_FAILED, detail or "检测到外部作业错误信息"

    lck_path = get_external_job_lck_path(item)
    if lck_path and os.path.exists(lck_path):
        return STATUS_UNKNOWN, "进程已消失，但仍存在 LCK 文件，可能为异常退出后的残留文件。"

    return STATUS_UNKNOWN, "外部作业相关进程已消失，但未从诊断文件中识别出明确结束状态。"


def classify_external_job_after_process_exit_snapshot(snapshot, lck_exists):
    """Classify an external job from immutable worker data."""
    work_dir = snapshot.get("work_dir", "")
    job_name = snapshot.get("job_name", "")
    if not work_dir or not job_name:
        return STATUS_UNKNOWN, "外部作业相关进程已消失，但缺少工作目录或作业名，无法读取诊断文件。"

    combined_text = ""
    for extension in DIAGNOSTIC_EXTENSIONS:
        path = os.path.join(work_dir, job_name + extension)
        if os.path.exists(path):
            combined_text += "\n" + read_file_tail(path)

    final_status, detail = classify_job_text(combined_text)
    if final_status == "完成":
        return STATUS_COMPLETED, detail or "检测到外部作业完成信息"
    if final_status == "终止":
        return STATUS_TERMINATED, detail or "检测到外部作业终止信息"
    if final_status == "失败":
        return STATUS_FAILED, detail or "检测到外部作业错误信息"

    if lck_exists:
        return STATUS_UNKNOWN, "进程已消失，但仍存在 LCK 文件，可能为异常退出后的残留文件。"

    return STATUS_UNKNOWN, "外部作业相关进程已消失，但未从诊断文件中识别出明确结束状态。"


def build_external_job_monitor_snapshot(items):
    """Copy external job fields needed by the background PID sampler."""
    snapshots = []
    for item in items:
        snapshots.append(
            {
                "item_id": item.item_id,
                "job_name": item.job_name,
                "work_dir": item.external_work_dir or os.path.dirname(item.inp_path),
                "status": item.status,
                "pids": list(item.pids or []),
                "pid_create_times": dict(item.pid_create_times or {}),
            }
        )

    return snapshots


def collect_external_job_process_sample(snapshot):
    """Collect one external job PID sample away from the Tk main thread."""
    if psutil is None:
        return {
            "item_id": snapshot.get("item_id", ""),
            "alive_pids": [],
            "pid_create_times": {},
            "rss_bytes": 0,
            "lck_exists": False,
            "final_status": "",
            "detail": "",
        }

    old_pids = list(snapshot.get("pids") or [])
    old_status = snapshot.get("status", "")
    pid_create_times = snapshot.get("pid_create_times") or {}
    work_dir = snapshot.get("work_dir", "")
    job_name = snapshot.get("job_name", "")

    alive_pids = []
    alive_create_times = {}
    rss_total = 0
    lck_path = os.path.join(work_dir, job_name + ".lck") if work_dir and job_name else ""
    lck_exists = bool(lck_path and os.path.exists(lck_path))
    final_status = ""
    detail = ""

    for raw_pid in old_pids:
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            continue

        if not psutil.pid_exists(pid):
            continue

        try:
            process = psutil.Process(pid)
            create_time = process.create_time()
            expected_create_time = pid_create_times.get(str(pid))
            if expected_create_time and abs(float(expected_create_time) - float(create_time)) > 0.01:
                continue
            rss_total += int(process.memory_info().rss)
            process.name()
        except (psutil.AccessDenied, OSError):
            alive_pids.append(pid)
            existing_create_time = pid_create_times.get(str(pid))
            if existing_create_time:
                alive_create_times[str(pid)] = existing_create_time
            continue
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue

        alive_pids.append(pid)
        alive_create_times[str(pid)] = create_time

    if old_pids and not alive_pids:
        if old_status == STATUS_TERMINATING:
            final_status = STATUS_TERMINATED
            detail = (
                "外部终止命令已生效，进程已退出；仍存在 LCK 残留。"
                if lck_exists
                else "外部终止命令已生效，进程已退出。"
            )
        else:
            final_status, detail = classify_external_job_after_process_exit_snapshot(
                snapshot,
                lck_exists
            )

    return {
        "item_id": snapshot.get("item_id", ""),
        "alive_pids": sorted(alive_pids),
        "pid_create_times": alive_create_times,
        "rss_bytes": rss_total,
        "lck_exists": lck_exists,
        "final_status": final_status,
        "detail": detail,
    }


def apply_external_job_process_sample(item, sample):
    """Apply a background external PID sample on the Tk main thread."""
    old_pids = list(item.pids or [])
    old_rss = int(item.rss_bytes or 0)
    old_status = item.status
    old_message = item.message

    item.pids = list(sample.get("alive_pids") or [])
    item.pid_create_times = dict(sample.get("pid_create_times") or {})
    item.rss_bytes = int(sample.get("rss_bytes") or 0)
    if item.pids:
        update_external_job_memory_estimate(item)

    final_status = sample.get("final_status", "")
    detail = sample.get("detail", "")

    if old_pids and not item.pids and final_status == STATUS_TERMINATED:
        item.status = STATUS_TERMINATED
        item.message = detail or "外部终止命令已生效，进程已退出。"
        item.valid = True
        joblist_state["statuses"][item.item_id] = item.status
    elif old_pids and not item.pids:
        item.status = final_status
        item.message = detail
        item.valid = final_status not in (STATUS_FAILED, STATUS_UNKNOWN)
        joblist_state["statuses"][item.item_id] = item.status
    elif item.pids and item.status == STATUS_TERMINATING:
        item.message = "正在终止（等待外部进程退出）"
    elif item.pids and item.status in (STATUS_STARTING, STATUS_RUNNING):
        item.status = STATUS_RUNNING
        item.message = "外部导入运行中"
        joblist_state["statuses"][item.item_id] = item.status

    return (
        old_pids != item.pids
        or old_rss != int(item.rss_bytes or 0)
        or old_status != item.status
        or old_message != item.message
    )


def start_external_job_monitor():
    """Start the shared external job lifecycle monitor."""
    if psutil is None or external_job_monitor_state.get("running"):
        return

    if not external_job_items_to_monitor():
        return

    external_job_monitor_state["running"] = True
    external_job_monitor_state["after_id"] = root.after(
        EXTERNAL_JOB_MONITOR_INTERVAL_MS,
        run_external_job_monitor
    )


def stop_external_job_monitor_if_idle():
    """Stop the external monitor when no imported external jobs are active."""
    if external_job_items_to_monitor():
        return False

    after_id = external_job_monitor_state.get("after_id")
    if after_id:
        try:
            root.after_cancel(after_id)
        except tk.TclError:
            pass

    external_job_monitor_state["running"] = False
    external_job_monitor_state["after_id"] = None
    external_job_monitor_state["scanning"] = False
    return True


def run_external_job_scan_worker(generation, snapshots):
    """Collect external job PID samples away from the Tk main thread."""
    started_at = time.perf_counter()
    try:
        samples = [
            collect_external_job_process_sample(snapshot)
            for snapshot in snapshots
        ]
    except Exception as exc:
        if not application_closing:
            queue_ui_event(
                "external_job_scan_failed",
                {
                    "generation": generation,
                    "error": str(exc),
                }
            )
    else:
        if not application_closing:
            queue_ui_event(
                "external_job_scan_finished",
                {
                    "generation": generation,
                    "samples": samples,
                }
            )
    finally:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        sampled_pid_count = sum(
            len(sample.get("alive_pids") or [])
            for sample in locals().get("samples", []) or []
        )
        log_worker_performance(
            "external_job_scan_worker",
            elapsed_ms,
            snapshot_count=len(snapshots),
            sampled_pid_count=sampled_pid_count,
            status="failed" if "exc" in locals() else "success",
        )


def run_external_job_monitor():
    """Start one background scan for imported external jobs."""
    external_job_monitor_state["after_id"] = None
    if application_closing:
        external_job_monitor_state["running"] = False
        return

    monitored_items = external_job_items_to_monitor()
    if not monitored_items or psutil is None:
        external_job_monitor_state["running"] = False
        return

    if external_job_monitor_state.get("scanning"):
        external_job_monitor_state["after_id"] = root.after(
            EXTERNAL_JOB_MONITOR_INTERVAL_MS,
            run_external_job_monitor
        )
        return

    external_job_monitor_state["generation"] += 1
    external_job_monitor_state["scanning"] = True
    generation = external_job_monitor_state["generation"]
    snapshots = build_external_job_monitor_snapshot(monitored_items)
    threading.Thread(
        target=run_external_job_scan_worker,
        args=(generation, snapshots),
        daemon=True
    ).start()


def apply_external_job_scan_result(payload):
    """Apply external job PID samples on the Tk main thread."""
    if payload.get("generation") != external_job_monitor_state.get("generation"):
        return

    external_job_monitor_state["scanning"] = False
    item_by_id = {item.item_id: item for item in queue_items}
    changed = False
    for sample in payload.get("samples") or []:
        item = item_by_id.get(sample.get("item_id", ""))
        if item is None:
            continue
        changed = apply_external_job_process_sample(item, sample) or changed

    if changed:
        sync_joblist_state_from_queue_items()
        schedule_save_formal_queue_file()
        schedule_dispatch_joblist(100)

    schedule_next_external_job_monitor()


def apply_external_job_scan_failure(payload):
    """Recover the external monitor after a background scan failure."""
    if payload.get("generation") != external_job_monitor_state.get("generation"):
        return

    external_job_monitor_state["scanning"] = False
    schedule_next_external_job_monitor()


def schedule_next_external_job_monitor():
    """Schedule or stop the next external job monitor tick."""
    if application_closing:
        external_job_monitor_state["running"] = False
        external_job_monitor_state["after_id"] = None
        return

    if external_job_items_to_monitor():
        external_job_monitor_state["after_id"] = root.after(
            EXTERNAL_JOB_MONITOR_INTERVAL_MS,
            run_external_job_monitor
        )
    else:
        stop_external_job_monitor_if_idle()


def validate_formal_queue_item_before_submit(item):
    """Validate one formal queue item immediately before it is submitted."""
    item.valid = True
    item.message = "等待提交"

    if item.status != STATUS_PENDING_RUN:
        item.valid = False
        item.message = f"状态不可提交：{item.status}"
        return False

    if not item.inp_path or not os.path.isfile(item.inp_path):
        item.status = STATUS_FAILED
        item.valid = False
        item.message = "INP 文件不存在"
        return False

    if item.fortran_path and not os.path.isfile(item.fortran_path):
        item.status = STATUS_FAILED
        item.valid = False
        item.message = "FOR 文件不存在"
        return False

    try:
        cores = int(item.cores)
    except (TypeError, ValueError):
        item.status = STATUS_FAILED
        item.valid = False
        item.message = "Core 参数无效"
        return False

    if cores < 0 or cores > MAX_CPUS:
        item.status = STATUS_FAILED
        item.valid = False
        item.message = f"Core 范围应为 0–{MAX_CPUS}"
        return False

    if item.memory and not validate_memory_argument(item.memory):
        item.status = STATUS_FAILED
        item.valid = False
        item.message = "Mem 参数无效"
        return False

    detect_queue_item_restart(item)
    if item.run_mode == "restart":
        if not item.oldjob_path:
            item.status = STATUS_FAILED
            item.valid = False
            item.message = "未指定重启动依赖"
            return False

        ready, detail = check_oldjob_result_ready(item.oldjob_path)
        if not ready:
            if item.item_id in joblist_state.get("dependencies", {}):
                item.status = STATUS_WAITING_DEPENDENCY
                item.valid = True
            else:
                item.status = STATUS_FAILED
                item.valid = False
            item.message = detail
            return False

    item.message = "准备提交"
    return True


def sync_joblist_state_from_queue_items():
    """Synchronize the legacy scheduler state from the formal queue items."""
    with queue_lock:
        job_ids = [item.item_id for item in queue_items]
        joblist_state["jobs"] = job_ids
        joblist_state["statuses"] = {
            item.item_id: queue_status_to_legacy(item.status)
            for item in queue_items
        }
        joblist_state["restart_jobs"] = [
            item.item_id for item in queue_items
            if item.run_mode == "restart"
        ]
        joblist_state["oldjob_paths"] = {
            item.item_id: item.oldjob_path
            for item in queue_items
            if item.oldjob_path
        }
        if queue_items:
            joblist_state["work_dir"] = get_common_joblist_dir(
                [item.inp_path for item in queue_items]
            )
            joblist_state["joblist_path"] = os.path.join(
                joblist_state["work_dir"],
                JOBLIST_FILENAME
            )

    update_joblist_status_label()
    refresh_queue_manager_views()


def build_formal_queue_payload():
    """Build the JSON payload for the formal queue."""
    return [
        {
            "job_name": item.job_name,
            "inp_path": item.inp_path,
            "run_mode": item.run_mode,
            "oldjob_path": item.oldjob_path,
            "fortran_path": item.fortran_path,
            "cores": item.cores,
            "memory": item.memory,
            "status": item.status,
            "message": item.message,
            "source": item.source,
            "is_external": item.is_external,
            "external_work_dir": item.external_work_dir,
            "pids": item.pids,
            "pid_create_times": item.pid_create_times,
            "rss_bytes": item.rss_bytes,
        }
        for item in queue_items
    ]




def save_formal_queue_file():
    """Persist the formal queue as joblist.json without changing any model files."""
    if not queue_items:
        return ""

    work_dir = get_common_joblist_dir([item.inp_path for item in queue_items])
    if not work_dir:
        return ""

    path = os.path.join(work_dir, JOBLIST_FILENAME)
    try:
        atomic_write_json(path, build_formal_queue_payload())
    except (OSError, PermissionError) as exc:
        append_history_text(f"保存正式队列失败：{exc}\n\n")
        raise

    joblist_state["work_dir"] = work_dir
    joblist_state["joblist_path"] = path
    return path


def cancel_scheduled_formal_queue_save():
    """Cancel a pending debounced formal queue save."""
    after_id = formal_queue_save_state.get("after_id")
    if after_id:
        try:
            root.after_cancel(after_id)
        except tk.TclError:
            pass
    formal_queue_save_state["after_id"] = None


def schedule_save_formal_queue_file():
    """Debounce frequent formal queue saves."""
    cancel_scheduled_formal_queue_save()

    def run_save():
        formal_queue_save_state["after_id"] = None
        try:
            save_formal_queue_file()
        except OSError:
            pass

    formal_queue_save_state["after_id"] = root.after(
        FORMAL_QUEUE_SAVE_DEBOUNCE_MS,
        run_save
    )


def get_queue_item_external_key(item):
    """Return the formal queue de-duplication key for a job record."""
    work_dir = item.external_work_dir or os.path.dirname(item.inp_path)
    if not work_dir:
        return ("", item.job_name.lower())

    return (normalize_work_dir(work_dir), item.job_name.lower())


def find_queue_item_by_external_key(work_dir, job_name):
    """Find an existing formal queue item by work directory and job name."""
    target_key = (normalize_work_dir(work_dir), job_name.lower())
    for item in queue_items:
        if get_queue_item_external_key(item) == target_key:
            return item

    return None


def update_queue_item_from_external_job(item, job_info):
    """Apply scanned external job fields to an existing QueueItem."""
    item.source = "external_psutil" if not item.source else item.source
    item.is_external = True
    item.external_work_dir = job_info["work_dir"]
    item.inp_path = job_info.get("inp_path") or item.inp_path
    item.job_type = job_info.get("job_type") or item.job_type or "Abaqus"
    item.oldjob_name = job_info.get("restart_dependency") or item.oldjob_name
    item.oldjob_path = job_info.get("oldjob_path") or item.oldjob_path
    item.oldjob_dir = os.path.dirname(item.oldjob_path) if item.oldjob_path else item.oldjob_dir
    item.run_mode = "restart" if item.oldjob_name else item.run_mode
    item.fortran_path = job_info.get("for_file") or item.fortran_path
    try:
        item.cores = int(job_info.get("cores") or item.cores or 0)
    except (TypeError, ValueError):
        item.cores = 0
    item.memory = job_info.get("memory_setting") or item.memory
    item.status = STATUS_RUNNING
    item.valid = True
    item.message = "外部导入运行中"
    item.pids = list(job_info.get("pids") or [])
    item.pid_create_times = dict(job_info.get("pid_create_times") or {})
    item.rss_bytes = int(job_info.get("rss_bytes") or 0)
    item.active_job_key = ""


def create_external_queue_item(job_info):
    """Create a QueueItem for a running job launched outside this GUI."""
    item = QueueItem(
        inp_path=job_info.get("inp_path") or "",
        job_name=job_info["job_name"],
        source="external_psutil",
        status=STATUS_RUNNING,
        selected=False,
        valid=True,
        message="外部导入运行中",
        run_mode="restart" if job_info.get("restart_dependency") else "normal",
        oldjob_name=job_info.get("restart_dependency") or "",
        oldjob_dir=os.path.dirname(job_info.get("oldjob_path") or "") if job_info.get("oldjob_path") else "",
        oldjob_path=job_info.get("oldjob_path") or "",
        fortran_path=job_info.get("for_file") or "",
        memory=job_info.get("memory_setting") or "",
        job_type=job_info.get("job_type") or "Abaqus",
        is_external=True,
        external_work_dir=job_info["work_dir"],
        pids=list(job_info.get("pids") or []),
        pid_create_times=dict(job_info.get("pid_create_times") or {}),
        rss_bytes=int(job_info.get("rss_bytes") or 0),
    )
    try:
        item.cores = int(job_info.get("cores") or 0)
    except (TypeError, ValueError):
        item.cores = 0
    return item


def import_or_update_external_jobs(scanned_jobs):
    """Merge scanned external jobs into the formal queue."""
    added = 0
    updated = 0
    with queue_lock:
        for job_info in scanned_jobs:
            existing = find_queue_item_by_external_key(
                job_info["work_dir"],
                job_info["job_name"]
            )
            if existing is None:
                queue_items.append(create_external_queue_item(job_info))
                added += 1
            else:
                update_queue_item_from_external_job(existing, job_info)
                updated += 1

    sync_joblist_state_from_queue_items()
    try:
        save_formal_queue_file()
    except OSError:
        pass
    if added or updated:
        start_external_job_monitor()
    return added, updated


def register_single_submit_queue_item(job_state):
    """Show a manually submitted single job in the formal queue manager."""
    if job_state.get("from_joblist"):
        return

    item = QueueItem(
        inp_path=job_state["inp_file"],
        job_name=job_state["job_name"],
        source="single",
        status=STATUS_RUNNING,
        selected=False,
        valid=True,
        message="单作业提交运行中",
        run_mode="restart" if job_state.get("oldjob_name") else "normal",
        oldjob_name=job_state.get("oldjob_name", ""),
        oldjob_dir=os.path.dirname(job_state.get("oldjob_path", "")) if job_state.get("oldjob_path") else "",
        oldjob_path=job_state.get("oldjob_path", ""),
        fortran_path=job_state.get("for_file_path", ""),
        cores=job_state.get("cpus", DEFAULT_CPUS),
        memory=job_state.get("memory_argument", ""),
        interactive=job_state.get("interactive_mode", False),
        datacheck_only=job_state.get("datacheck_mode", False),
        complete_notify=bool(complete_notify_var.get()) if "complete_notify_var" in globals() else False,
        active_job_key=job_state["job_key"],
    )
    job_state["queue_item_id"] = item.item_id
    with queue_lock:
        queue_items.append(item)
    sync_joblist_state_from_queue_items()


def update_queue_item_from_final_job_status(job_state, status, detail=""):
    """Update queue-manager row for a job that has finished."""
    item_id = job_state.get("queue_item_id") or job_state.get("joblist_inp_name")
    item = get_queue_item(item_id) if item_id else None
    if item is None:
        return

    item.status = queue_status_from_final_status(status)
    item.message = detail or format_final_status_for_display(status, detail)
    item.active_job_key = ""
    if job_state.get("start_time") and job_state.get("end_time"):
        item.elapsed = format_duration(job_state["end_time"] - job_state["start_time"])
    joblist_state["statuses"][item.item_id] = item.status
    refresh_queue_manager_views()
    schedule_save_formal_queue_file()


def get_common_joblist_dir(paths):
    """Return a directory suitable for saving joblist.json for selected INP files."""
    directories = [os.path.dirname(normalize_joblist_path(path)) for path in paths]
    if not directories:
        return ""

    try:
        return os.path.commonpath(directories)
    except ValueError:
        return directories[0]


def get_joblist_items_from_dir(work_dir, absolute=False):
    """Return queue items scanned from a directory."""
    inp_names = scan_inp_names_in_dir(work_dir)
    if absolute:
        return [normalize_joblist_path(os.path.join(work_dir, name)) for name in inp_names]

    return inp_names


def natural_sort_key(value):
    """Sort strings with embedded numbers in human order."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", str(value))
    ]


def scan_inp_paths_in_dir(work_dir, recursive=False):
    """Return naturally sorted INP paths from one directory."""
    base_path = Path(work_dir)
    pattern = "**/*.inp" if recursive else "*.inp"
    return [
        normalize_joblist_path(path)
        for path in sorted(base_path.glob(pattern), key=lambda item: natural_sort_key(str(item)))
        if path.is_file()
    ]


def get_initial_joblist_browse_dir():
    """Return the best starting directory for queue source browsing."""
    candidates = [
        joblist_state.get("work_dir", ""),
        os.path.dirname(inp_file_var.get().strip()) if "inp_file_var" in globals() else "",
        os.getcwd(),
    ]
    for path in candidates:
        if path and os.path.isdir(path):
            return normalize_joblist_path(path)

    return normalize_joblist_path(os.getcwd())


def ask_joblist_source_mode():
    """Show a native dropdown menu below the queue build button."""
    result = tk.StringVar(value="")
    action_text = "追加队列" if is_joblist_submitted() else "生成队列"

    menu = tk.Menu(
        root,
        tearoff=0,
        font=FONT_HINT,
        bg="#ffffff",
        fg="#111827",
        activebackground="#e5eefc",
        activeforeground="#111827",
        relief="solid",
        bd=1,
        activeborderwidth=0
    )

    def choose(mode):
        result.set(mode)

    menu.add_command(
        label=f"从 INP 文件{action_text}",
        command=lambda: choose("files")
    )
    menu.add_command(
        label=f"从文件夹{action_text}",
        command=lambda: choose("folder")
    )

    def cancel_if_unselected(_event=None):
        if not result.get():
            root.after_idle(lambda: result.set("__cancel__") if not result.get() else None)

    menu.bind("<Unmap>", cancel_if_unselected)

    try:
        x = build_joblist_btn.winfo_rootx()
        y = build_joblist_btn.winfo_rooty() + build_joblist_btn.winfo_height()
    except (NameError, tk.TclError):
        root.update_idletasks()
        x = root.winfo_x()
        y = root.winfo_y()

    try:
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()

    root.wait_variable(result)
    try:
        menu.unpost()
    except tk.TclError:
        pass

    mode = result.get()
    return None if mode == "__cancel__" else mode


def ask_joblist_source_paths():
    """Use native dialogs after choosing files or folder as the queue source."""
    mode = ask_joblist_source_mode()
    if not mode:
        return None

    initial_dir = get_initial_joblist_browse_dir()
    if mode == "files":
        file_paths = filedialog.askopenfilenames(
            title="选择一个或多个 INP 文件",
            initialdir=initial_dir,
            filetypes=[
                ("Abaqus INP 文件", "*.inp"),
                ("所有文件", "*.*"),
            ],
        )
        if not file_paths:
            return None

        return [
            normalize_joblist_path(path)
            for path in file_paths
            if os.path.isfile(path) and os.path.splitext(path)[1].lower() == ".inp"
        ]

    folder_path = filedialog.askdirectory(
        title="选择包含 INP 文件的文件夹",
        initialdir=initial_dir,
    )
    if folder_path:
        return [normalize_joblist_path(folder_path)]

    return None


def get_joblist_items_from_selection():
    """Build a queue from selected INP files or selected folders."""
    selected_paths = ask_joblist_source_paths()
    if not selected_paths:
        return None

    inp_names = []
    source = "files"
    for path in selected_paths:
        if os.path.isdir(path):
            source = "directory"
            inp_names.extend(get_joblist_items_from_dir(path, absolute=True))
        elif os.path.isfile(path) and os.path.splitext(path)[1].lower() == ".inp":
            inp_names.append(normalize_joblist_path(path))

    seen_paths = set()
    unique_inp_names = []
    for path in inp_names:
        compare_path = normalize_joblist_compare_path(path)
        if compare_path in seen_paths:
            continue
        seen_paths.add(compare_path)
        unique_inp_names.append(path)

    inp_names = unique_inp_names
    if not inp_names:
        messagebox.showwarning("未找到 INP", "所选内容中没有可加入队列的 .inp 文件。")
        return None

    return {
        "work_dir": get_common_joblist_dir(inp_names),
        "items": inp_names,
        "source": source,
    }


def queue_item_from_inp_path(inp_path, source="file", selected=True):
    """Create a candidate queue item from an INP path."""
    inp_path = normalize_joblist_path(inp_path)
    job_name = os.path.splitext(os.path.basename(inp_path))[0]
    item = QueueItem(
        inp_path=inp_path,
        job_name=job_name,
        source=source,
        selected=selected,
    )
    snapshot = current_queue_parameter_snapshot()
    item.cores = snapshot["cores"]
    item.memory = snapshot["memory"]
    item.fortran_path = snapshot["fortran_path"]
    item.interactive = snapshot["interactive"]
    item.datacheck_only = snapshot["datacheck_only"]
    item.complete_notify = snapshot["complete_notify"]
    validate_queue_item(item)
    if not item.valid:
        item.selected = False
    return item


def get_existing_queue_paths(include_candidates=True):
    """Return normalized INP paths already present in candidate/formal queues."""
    paths = {
        normalize_joblist_compare_path(item.inp_path)
        for item in queue_items
    }
    if include_candidates:
        paths.update(
            normalize_joblist_compare_path(item.inp_path)
            for item in queue_candidates
        )
    return paths


def get_existing_queue_job_names(exclude_item_id=""):
    """Return job names already present in the formal queue."""
    return {
        item.job_name.lower()
        for item in queue_items
        if item.item_id != exclude_item_id
    }


def detect_queue_item_restart(item):
    """Update restart metadata for a queue item."""
    item.run_mode = "restart" if os.path.isfile(item.inp_path) and inp_has_restart_keyword(item.inp_path) else "normal"
    if item.run_mode == "restart":
        oldjob_path = item.oldjob_path or (oldjob_var.get().strip() if "oldjob_var" in globals() else "")
        if oldjob_path:
            item.oldjob_path = oldjob_path
            item.oldjob_name = get_oldjob_name_from_path(oldjob_path)
            item.oldjob_dir = os.path.dirname(oldjob_path)
    else:
        item.oldjob_path = ""
        item.oldjob_name = ""
        item.oldjob_dir = ""


def validate_queue_item(item):
    """Validate a candidate/formal queue item without modifying files."""
    item.valid = True
    item.message = "可加入"

    if not item.inp_path or not os.path.isfile(item.inp_path):
        item.valid = False
        item.message = "INP 文件不存在"
        return item

    if os.path.splitext(item.inp_path)[1].lower() != ".inp":
        item.valid = False
        item.message = "扩展名错误"
        return item

    if not JOB_NAME_PATTERN.fullmatch(item.job_name):
        item.valid = False
        item.message = "作业名称不合法"
        return item

    if item.job_name.lower() in get_existing_queue_job_names(item.item_id):
        item.valid = False
        item.message = "正式队列中已存在同名作业"
        return item

    if item.fortran_path and not os.path.isfile(item.fortran_path):
        item.valid = False
        item.message = "FOR 文件不存在"
        return item

    detect_queue_item_restart(item)
    if item.run_mode == "restart":
        if item.oldjob_path:
            if not os.path.isfile(item.oldjob_path):
                dependency_item = get_queue_inp_name_for_oldjob_path(item.oldjob_path)
                if dependency_item:
                    item.message = "等待队列内前置作业"
                else:
                    item.valid = False
                    item.message = "未找到旧作业相关文件"
        else:
            item.message = "启动前选择 oldjob"

    return item


def add_candidate_items_from_paths(paths, source):
    """Add INP paths to candidate queue with duplicate filtering."""
    added = 0
    skipped = 0
    existing_paths = get_existing_queue_paths()
    existing_job_names = {
        item.job_name.lower()
        for item in queue_items + queue_candidates
    }
    with queue_lock:
        for path in paths:
            normalized_path = normalize_joblist_path(path)
            if normalize_joblist_compare_path(normalized_path) in existing_paths:
                skipped += 1
                continue
            item = queue_item_from_inp_path(normalized_path, source=source)
            if queue_skip_restart_var is not None and queue_skip_restart_var.get() and "restart" in item.job_name.lower():
                skipped += 1
                continue
            if item.job_name.lower() in existing_job_names:
                item.selected = False
                item.valid = False
                item.message = "队列中已存在同名作业"
            if queue_skip_existing_var is not None and queue_skip_existing_var.get():
                if get_existing_odb_file(os.path.dirname(item.inp_path), item.job_name):
                    item.selected = False
                    item.valid = False
                    item.message = "已有结果文件"
            queue_candidates.append(item)
            existing_paths.add(normalize_joblist_compare_path(normalized_path))
            existing_job_names.add(item.job_name.lower())
            added += 1

    refresh_queue_manager_views()
    update_joblist_status_label()
    if skipped:
        append_history_text(f"候选区新增 {added} 个，跳过 {skipped} 个重复或过滤项。\n\n")
    return added, skipped


def add_current_inp_to_candidates():
    """Add the currently selected main-window INP to candidates."""
    inp_file = inp_file_var.get().strip()
    if not inp_file:
        messagebox.showwarning("未选择 INP", "请先在主界面选择 INP 文件。")
        return
    add_candidate_items_from_paths([inp_file], "current")


def add_inp_files_to_candidates():
    """Add one or more INP files to the candidate queue."""
    file_paths = filedialog.askopenfilenames(
        title="添加 INP 文件",
        initialdir=get_initial_joblist_browse_dir(),
        filetypes=[
            ("Abaqus INP 文件", "*.inp"),
            ("所有文件", "*.*"),
        ],
    )
    if file_paths:
        add_candidate_items_from_paths(file_paths, "file")


def scan_folder_to_candidates():
    """Scan a folder into the candidate queue."""
    folder_path = filedialog.askdirectory(
        title="扫描文件夹",
        initialdir=get_initial_joblist_browse_dir(),
    )
    if not folder_path:
        return
    recursive = bool(queue_scan_subdirs_var.get()) if queue_scan_subdirs_var is not None else False
    inp_paths = scan_inp_paths_in_dir(folder_path, recursive=recursive)
    if not inp_paths:
        messagebox.showinfo("未找到 INP", "该文件夹下没有 .inp 文件。")
        return
    add_candidate_items_from_paths(inp_paths, "folder")


def set_candidate_selection(mode):
    """Select/deselect/invert all candidates."""
    with queue_lock:
        for item in queue_candidates:
            if mode == "all":
                item.selected = item.valid
            elif mode == "none":
                item.selected = False
            elif mode == "invert":
                item.selected = not item.selected if item.valid else False
    refresh_queue_manager_views()


def remove_selected_candidates():
    """Remove selected candidate rows only."""
    with queue_lock:
        queue_candidates[:] = [item for item in queue_candidates if not item.selected]
    refresh_queue_manager_views()


def candidate_tree_toggle_selection(event):
    """Toggle candidate check mark when the first column is clicked."""
    if queue_candidate_tree is None:
        return
    row_id = queue_candidate_tree.identify_row(event.y)
    column = queue_candidate_tree.identify_column(event.x)
    if not row_id or column != "#1":
        return
    item = get_candidate_item(row_id)
    if item is not None and item.valid:
        item.selected = not item.selected
        refresh_queue_manager_views()


def queue_item_row_values(item, index, candidate=False):
    """Return row values for a queue Treeview."""
    job_type_text = item.job_type or ("重启动" if item.run_mode == "restart" else "普通")
    status_text = item.status
    if item.is_external and item.status == STATUS_RUNNING:
        status_text = "运行中（外部导入）"
    memory_text = item.memory if item.memory else "默认"
    if item.is_external:
        memory_text = format_memory_size(item.rss_bytes) if item.rss_bytes else ""

    if candidate:
        return (
            "☑" if item.selected else "☐",
            index,
            item.job_name,
            item.inp_path,
            item.source,
            job_type_text,
            item.oldjob_name if item.oldjob_name else "—",
            os.path.basename(item.fortran_path) if item.fortran_path else "—",
            item.message,
        )

    return (
        index,
        item.job_name,
        item.inp_path,
        job_type_text,
        item.oldjob_name if item.oldjob_name else "—",
        os.path.basename(item.fortran_path) if item.fortran_path else "—",
        item.cores,
        memory_text,
        status_text,
        item.message,
    )


def sync_treeview_rows(tree, rows):
    """Synchronize Treeview rows without rebuilding unchanged items."""
    with measure_ui_callback("sync_treeview_rows"):
        if tree is None or not tree.winfo_exists():
            return
    
        try:
            selection = set(tree.selection())
            yview = tree.yview()
            existing_ids = set(tree.get_children())
            incoming_ids = [item_id for item_id, _values in rows]
            incoming_id_set = set(incoming_ids)
    
            for item_id in existing_ids - incoming_id_set:
                tree.delete(item_id)
    
            for position, (item_id, values) in enumerate(rows):
                if item_id in existing_ids:
                    if tuple(tree.item(item_id, "values")) != tuple(str(value) for value in values):
                        tree.item(item_id, values=values)
                else:
                    tree.insert("", "end", iid=item_id, values=values)
    
                current_index = tree.index(item_id)
                if current_index != position:
                    tree.move(item_id, "", position)
    
            kept_selection = [item_id for item_id in selection if item_id in incoming_id_set]
            if kept_selection:
                tree.selection_set(kept_selection)
            if yview:
                tree.yview_moveto(yview[0])
        except tk.TclError:
            pass


def refresh_queue_manager_views():
    """Refresh candidate/formal queue Treeviews and summaries."""
    with measure_ui_callback("refresh_queue_manager_views"):
        try:
            if queue_candidate_tree is not None and queue_candidate_tree.winfo_exists():
                candidate_rows = tuple(
                    (
                        item.item_id,
                        queue_item_row_values(item, index, candidate=True)
                    )
                    for index, item in enumerate(queue_candidates, start=1)
                )
                if (
                        candidate_rows != queue_manager_view_signature["candidate"]
                        or (candidate_rows and not queue_candidate_tree.get_children())
                ):
                    sync_treeview_rows(queue_candidate_tree, candidate_rows)
                    queue_manager_view_signature["candidate"] = candidate_rows
            if queue_formal_tree is not None and queue_formal_tree.winfo_exists():
                formal_rows = tuple(
                    (
                        item.item_id,
                        queue_item_row_values(item, index, candidate=False)
                    )
                    for index, item in enumerate(queue_items, start=1)
                )
                if (
                        formal_rows != queue_manager_view_signature["formal"]
                        or (formal_rows and not queue_formal_tree.get_children())
                ):
                    sync_treeview_rows(queue_formal_tree, formal_rows)
                    queue_manager_view_signature["formal"] = formal_rows
            if queue_candidate_summary_var is not None:
                total = len(queue_candidates)
                selected = sum(1 for item in queue_candidates if item.selected)
                invalid = sum(1 for item in queue_candidates if not item.valid)
                queue_candidate_summary_var.set(f"候选：{total} | 已选 {selected} | 异常 {invalid}")
            if queue_formal_summary_var is not None:
                counts = {}
                for item in queue_items:
                    counts[item.status] = counts.get(item.status, 0) + 1
                queue_formal_summary_var.set(
                    "正式队列：" + (" | ".join(f"{status} {count}" for status, count in counts.items()) if counts else "空")
                )
        except tk.TclError:
            pass


def confirm_selected_candidates_to_queue():
    """Move selected valid candidates into the formal queue."""
    selected_items = [item for item in queue_candidates if item.selected and item.valid]
    if not selected_items:
        messagebox.showwarning("没有可加入作业", "请先勾选有效的候选作业。")
        return

    snapshot = current_queue_parameter_snapshot()
    restart_lines = [
        f"{item.job_name} -> {item.oldjob_name or '启动前选择'}"
        for item in selected_items
        if item.run_mode == "restart"
    ]
    summary = (
        f"即将加入 {len(selected_items)} 个作业。\n\n"
        f"Core: {snapshot['cores']}\n"
        f"Mem: {snapshot['memory'] if snapshot['memory'] else '默认'}\n"
        f"FOR: {os.path.basename(snapshot['fortran_path']) if snapshot['fortran_path'] else '无'}\n"
        f"Restart: {len(restart_lines)}"
        + (
            "\n\n重启动依赖：\n"
            + "\n".join(restart_lines[:10])
            + ("\n..." if len(restart_lines) > 10 else "")
            if restart_lines else ""
        )
    )
    if not messagebox.askyesno("确认加入队列", summary):
        return

    with queue_lock:
        added_ids = set()
        for item in selected_items:
            item.status = STATUS_PENDING_RUN
            item.cores = snapshot["cores"]
            item.memory = snapshot["memory"]
            item.fortran_path = snapshot["fortran_path"]
            item.interactive = snapshot["interactive"]
            item.datacheck_only = snapshot["datacheck_only"]
            item.complete_notify = snapshot["complete_notify"]
            validate_queue_item(item)
            if item.valid:
                if item.message == "可加入":
                    item.message = "待提交"
                queue_items.append(item)
                added_ids.add(item.item_id)
        queue_candidates[:] = [item for item in queue_candidates if item.item_id not in added_ids]

    sync_joblist_state_from_queue_items()
    try:
        save_formal_queue_file()
    except OSError as e:
        messagebox.showwarning("保存失败", f"正式队列已更新，但无法保存 joblist.json：\n{e}")
    append_history_text(f"已确认加入正式队列：{len(added_ids)} 个作业\n\n")
    if joblist_state.get("active"):
        if ensure_joblist_restart_oldjobs(force_prompt=False, confirm=False):
            schedule_dispatch_joblist(100)


def cancel_selected_pending_queue_items():
    """Cancel selected formal queue jobs that have not started."""
    if queue_formal_tree is None:
        return
    changed = 0
    blocked = 0
    with queue_lock:
        for item_id in queue_formal_tree.selection():
            item = get_queue_item(item_id)
            if item and item.status in (STATUS_PENDING_RUN, STATUS_WAITING_DEPENDENCY):
                item.status = STATUS_CANCELED
                item.message = "用户手动取消"
                changed += 1
            elif item and item.status in (STATUS_STARTING, STATUS_RUNNING, STATUS_TERMINATING):
                blocked += 1
    if changed:
        sync_joblist_state_from_queue_items()
    if blocked:
        messagebox.showinfo("不能取消运行中作业", "运行中作业请使用“终止选中的运行中作业”。")


def terminate_selected_running_queue_items():
    """Terminate selected formal queue jobs that are starting or running."""
    if queue_formal_tree is None:
        return
    changed = False
    for item_id in queue_formal_tree.selection():
        item = get_queue_item(item_id)
        if not item or item.status not in (STATUS_STARTING, STATUS_RUNNING):
            continue
        if item.is_external:
            work_dir = item.external_work_dir or os.path.dirname(item.inp_path)
            if not work_dir or not os.path.isdir(work_dir):
                messagebox.showerror("无法终止", f"外部作业工作目录不存在：\n{work_dir}")
                continue
            try:
                run_command_hidden(f"abaqus terminate job={item.job_name}", work_dir)
            except OSError as exc:
                messagebox.showerror(
                    "终止命令失败",
                    f"Abaqus 终止命令执行失败：\n{exc}\n\n"
                    "未执行强制终止，以避免损坏结果文件。"
                )
                continue
            item.status = STATUS_TERMINATING
            item.message = "正在终止（外部导入）"
            joblist_state["statuses"][item.item_id] = STATUS_TERMINATING
            append_history_text(f"外部作业终止命令已发送：{item.job_name}\n\n")
            changed = True
            continue
        if item.active_job_key and item.active_job_key in active_jobs:
            item.status = STATUS_TERMINATING
            item.message = "正在终止"
            joblist_state["statuses"][item.item_id] = STATUS_TERMINATING
            changed = True
            terminate_job(active_jobs[item.active_job_key])
    if changed:
        sync_joblist_state_from_queue_items()
        try:
            save_formal_queue_file()
        except OSError:
            pass
        start_external_job_monitor()
        refresh_queue_manager_views()


def clear_finished_queue_items():
    """Remove terminal records from the formal queue table only."""
    with queue_lock:
        queue_items[:] = [item for item in queue_items if item.status not in TERMINAL_QUEUE_STATUSES]
    sync_joblist_state_from_queue_items()
    stop_external_job_monitor_if_idle()


def get_default_queue_work_dir():
    """Return the default directory for external-job scanning."""
    candidates = [
        joblist_state.get("work_dir", ""),
        os.path.dirname(inp_file_var.get().strip()) if "inp_file_var" in globals() else "",
        os.getcwd(),
    ]
    for path in candidates:
        if path and os.path.isdir(path):
            return normalize_joblist_path(path)

    return normalize_joblist_path(os.getcwd())


def remember_queue_work_dir(path):
    """Keep a per-session history of queue scan directories."""
    if not path:
        return

    normalized_path = normalize_joblist_path(path)
    existing = [
        old_path for old_path in queue_work_dir_history
        if normalize_work_dir(old_path) != normalize_work_dir(normalized_path)
    ]
    queue_work_dir_history[:] = [normalized_path] + existing[:9]
    if queue_work_dir_combo is not None:
        try:
            queue_work_dir_combo.configure(values=queue_work_dir_history)
        except tk.TclError:
            pass


def set_external_scan_button_state(state):
    """Enable or disable the queue external scan button."""
    if queue_scan_external_btn is not None:
        try:
            queue_scan_external_btn.configure(state=state)
        except tk.TclError:
            pass


def finish_external_job_scan(work_dir, scanned_jobs, skipped_pids, error=""):
    """Merge scan results on the Tk main thread and report feedback."""
    set_external_scan_button_state("normal")
    if error:
        messagebox.showerror("扫描失败", error)
        return

    if not scanned_jobs:
        for pid in skipped_pids[:10]:
            append_history_text(f"发现疑似 Abaqus 进程 PID {pid}，但无法识别 Job 名称，已跳过。\n")
        messagebox.showinfo("未发现作业", "未在该目录中识别到正在运行的 Abaqus 作业。")
        return

    added, updated = import_or_update_external_jobs(scanned_jobs)
    remember_queue_work_dir(work_dir)
    for pid in skipped_pids[:10]:
        append_history_text(f"发现疑似 Abaqus 进程 PID {pid}，但无法识别 Job 名称，已跳过。\n")
    message = f"扫描完成：识别到 {len(scanned_jobs)} 个运行中的 Abaqus 作业，新增导入 {added} 个，更新 {updated} 个。"
    append_history_text(message + "\n\n")
    messagebox.showinfo("扫描完成", message)


def scan_selected_work_directory():
    """Scan external running Abaqus jobs from the selected work directory."""
    if queue_work_dir_var is None:
        return

    work_dir = queue_work_dir_var.get().strip()
    if not work_dir:
        messagebox.showwarning("工作目录为空", "请先输入或选择工作目录。")
        return

    if not os.path.isdir(work_dir):
        messagebox.showerror("工作目录不存在", f"工作目录不存在：\n{work_dir}")
        return

    if psutil is None:
        messagebox.showerror("无法扫描", "当前 Python 环境未检测到 psutil，无法扫描外部运行作业。")
        return

    work_dir = normalize_joblist_path(work_dir)
    queue_work_dir_var.set(work_dir)
    remember_queue_work_dir(work_dir)
    set_external_scan_button_state("disabled")

    def worker():
        try:
            scanned_jobs, skipped_pids = scan_running_abaqus_jobs_by_psutil(work_dir)
            error = ""
        except (OSError, FileNotFoundError, ValueError) as exc:
            scanned_jobs = []
            skipped_pids = []
            error = str(exc)

        target_window = queue_manager_window if queue_manager_window is not None else root
        try:
            target_window.after(
                0,
                lambda: finish_external_job_scan(work_dir, scanned_jobs, skipped_pids, error)
            )
        except tk.TclError:
            pass

    threading.Thread(target=worker, daemon=True).start()


def select_queue_work_directory():
    """Select a work directory for external Abaqus job scanning."""
    if queue_work_dir_var is None:
        return

    initial_dir = queue_work_dir_var.get().strip()
    if not initial_dir or not os.path.isdir(initial_dir):
        initial_dir = get_default_queue_work_dir()

    selected_dir = filedialog.askdirectory(
        title="选择外部作业工作目录",
        initialdir=initial_dir,
    )
    if not selected_dir:
        return

    selected_dir = normalize_joblist_path(selected_dir)
    queue_work_dir_var.set(selected_dir)
    remember_queue_work_dir(selected_dir)


def show_queue_item_details(_event=None):
    """Show details for selected formal queue item."""
    if queue_formal_tree is None:
        return
    selection = queue_formal_tree.selection()
    if not selection:
        return
    item = get_queue_item(selection[0])
    if item is None:
        return
    details = (
        f"作业名称：{item.job_name}\n"
        f"INP 完整路径：{item.inp_path}\n"
        f"FOR 文件完整路径：{item.fortran_path or '—'}\n"
        f"是否属于重启动作业：{'是' if item.run_mode == 'restart' else '否'}\n"
        f"重启动旧作业名称：{item.oldjob_name or '—'}\n"
        f"重启动旧作业目录：{item.oldjob_dir or '—'}\n"
        f"Core：{item.cores}\n"
        f"Mem：{item.memory or '默认'}\n"
        f"当前状态：{item.status}\n"
        f"备注信息：{item.message}"
    )
    messagebox.showinfo("作业详情", details)


def edit_selected_pending_queue_item():
    """Edit settings for one pending formal queue item."""
    if queue_formal_tree is None:
        return
    selection = queue_formal_tree.selection()
    if not selection:
        messagebox.showinfo("未选择作业", "请先选择一个待运行作业。")
        return

    item = get_queue_item(selection[0])
    if item is None:
        return
    if item.status != STATUS_PENDING_RUN:
        messagebox.showwarning("不可编辑", "只能编辑状态为“待运行”的作业。")
        return

    dialog = tk.Toplevel(root)
    dialog.title("编辑待运行作业")
    dialog.transient(root)
    dialog.grab_set()
    dialog.configure(bg="#ffffff")
    dialog.resizable(False, False)

    core_var = tk.StringVar(value=str(item.cores))
    memory_var = tk.StringVar(value=item.memory)
    fortran_var = tk.StringVar(value=item.fortran_path)
    oldjob_path_var = tk.StringVar(value=item.oldjob_path)

    main = ttk.Frame(dialog, style="Card.TFrame")
    main.pack(fill="both", expand=True, padx=16, pady=14)
    main.columnconfigure(1, minsize=360, weight=1)

    def add_row(row, label, variable, browse_command=None):
        ttk.Label(main, text=label, style="Normal.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        entry = ttk.Entry(main, textvariable=variable, width=48)
        entry.grid(row=row, column=1, sticky="ew", pady=(0, 8))
        if browse_command is not None:
            ttk.Button(main, text="选择", command=browse_command).grid(row=row, column=2, sticky="w", padx=(8, 0), pady=(0, 8))
        return entry

    ttk.Label(main, text=f"作业：{item.job_name}", style="Normal.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
    add_row(1, "Core", core_var)
    add_row(2, "Mem", memory_var)

    def choose_fortran():
        path = filedialog.askopenfilename(
            title="选择 Fortran 子程序",
            initialdir=os.path.dirname(item.fortran_path) if item.fortran_path else get_initial_joblist_browse_dir(),
            filetypes=[("Fortran 文件", "*.for *.f *.f90"), ("所有文件", "*.*")]
        )
        if path:
            fortran_var.set(normalize_joblist_path(path))

    def choose_oldjob():
        path = filedialog.askopenfilename(
            title="选择重启动 oldjob ODB",
            initialdir=os.path.dirname(item.oldjob_path) if item.oldjob_path else get_initial_joblist_browse_dir(),
            filetypes=[("Abaqus ODB 文件", "*.odb"), ("所有文件", "*.*")]
        )
        if path:
            oldjob_path_var.set(normalize_joblist_path(path))

    add_row(3, "FOR", fortran_var, choose_fortran)
    add_row(4, "ODB", oldjob_path_var, choose_oldjob)

    button_row = ttk.Frame(main, style="Card.TFrame")
    button_row.grid(row=5, column=0, columnspan=3, sticky="e", pady=(8, 0))

    def apply_changes():
        try:
            cores = int(core_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "Core 必须是整数。")
            return
        if cores < 0 or cores > MAX_CPUS:
            messagebox.showerror("错误", f"Core 范围应为 0–{MAX_CPUS}。")
            return

        memory_value = memory_var.get().strip()
        if memory_value and not validate_memory_argument(memory_value):
            return

        fortran_path = fortran_var.get().strip()
        if fortran_path and not os.path.isfile(fortran_path):
            messagebox.showerror("错误", f"FOR 文件不存在：\n{fortran_path}")
            return

        oldjob_path = oldjob_path_var.get().strip()
        if oldjob_path and os.path.splitext(oldjob_path)[1].lower() != ".odb":
            messagebox.showerror("错误", "重启动依赖请选择 .odb 文件。")
            return

        item.cores = cores
        item.memory = memory_value
        item.fortran_path = fortran_path
        item.oldjob_path = oldjob_path
        item.oldjob_name = get_oldjob_name_from_path(oldjob_path) if oldjob_path else ""
        item.oldjob_dir = os.path.dirname(oldjob_path) if oldjob_path else ""
        validate_queue_item(item)
        if item.valid:
            item.status = STATUS_PENDING_RUN
        sync_joblist_state_from_queue_items()
        try:
            save_formal_queue_file()
        except OSError:
            pass
        dialog.destroy()

    ttk.Button(button_row, text="保存", command=apply_changes).pack(side="left", padx=(0, 8))
    ttk.Button(button_row, text="取消", command=dialog.destroy).pack(side="left")

    dialog.update_idletasks()
    width = dialog.winfo_reqwidth()
    height = dialog.winfo_reqheight()
    root.update_idletasks()
    x = root.winfo_x() + (root.winfo_width() - width) // 2
    y = root.winfo_y() + (root.winfo_height() - height) // 2
    dialog.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")


def create_queue_window_button(parent, text, command, variant="light", width=96):
    """Create a queue-manager button that matches the main window style."""
    colors = {
        "primary": ("#2563eb", "#1d4ed8", "#ffffff"),
        "danger": ("#dc2626", "#b91c1c", "#ffffff"),
        "light": (BTN_LIGHT_FG, BTN_LIGHT_HOVER, BTN_LIGHT_TEXT),
    }
    fg_color, hover_color, text_color = colors.get(variant, colors["light"])
    button = ctk.CTkButton(
        parent,
        text=text,
        width=width,
        height=36,
        corner_radius=7,
        font=FONT_QUEUE_BUTTON,
        fg_color=fg_color,
        hover_color=hover_color,
        text_color=text_color,
        bg_color="#ffffff",
        command=command,
    )
    button.pack(side="left", padx=(0, 6))
    return button


def create_queue_table(parent, columns, widths):
    """Create a Treeview with scrollbars."""
    frame = ttk.Frame(parent, style="Card.TFrame")
    frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=8, style="Queue.Treeview")
    y_scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview, style="Queue.Vertical.TScrollbar")
    x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview, style="Queue.Horizontal.TScrollbar")
    tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    y_scroll.grid(row=0, column=1, sticky="ns")
    x_scroll.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    left_aligned_columns = {
        "INP 文件路径",
    }

    for column, width in zip(columns, widths):
        anchor = "w" if column in left_aligned_columns else "center"

        tree.heading(
            column,
            text=column,
            anchor=anchor,
        )

        tree.column(
            column,
            width=width,
            minwidth=45,
            anchor=anchor,
        )

    return tree


def open_queue_manager_window():
    """Open the standalone queue management window."""
    global queue_manager_window, queue_candidate_tree, queue_formal_tree
    global queue_candidate_summary_var, queue_formal_summary_var
    global queue_scan_subdirs_var, queue_skip_restart_var, queue_skip_existing_var
    global queue_work_dir_var, queue_work_dir_combo, queue_scan_external_btn

    if queue_manager_window is not None:
        try:
            if queue_manager_window.winfo_exists():
                queue_manager_window.lift()
                return
        except tk.TclError:
            pass

    queue_manager_window = tk.Toplevel(root)
    queue_manager_window.title("作业队列管理")
    queue_manager_window.geometry("1280x720")
    queue_manager_window.minsize(1180, 680)
    queue_manager_window.configure(bg="#f8fafc")
    # queue_manager_window.transient(root)

    main = ttk.Frame(queue_manager_window, style="Main.TFrame")
    main.pack(fill="both", expand=True, padx=12, pady=12)

    queue_scan_subdirs_var = tk.BooleanVar(value=False)
    queue_skip_restart_var = tk.BooleanVar(value=False)
    queue_skip_existing_var = tk.BooleanVar(value=False)
    queue_candidate_summary_var = tk.StringVar(value="候选：0")
    queue_formal_summary_var = tk.StringVar(value="正式队列：空")
    queue_work_dir_var = tk.StringVar(value=get_default_queue_work_dir())
    remember_queue_work_dir(queue_work_dir_var.get())

    candidate_section = ttk.LabelFrame(main, text="候选区", style="Queue.TLabelframe")
    candidate_section.pack(fill="both", expand=True, pady=(0, 10))
    candidate_toolbar = ttk.Frame(candidate_section, style="Card.TFrame")
    candidate_toolbar.pack(fill="x", padx=8, pady=(8, 6))
    for text, command, variant, width in (
        ("加入当前 INP", add_current_inp_to_candidates, "light", 92),
        ("添加 INP 文件", add_inp_files_to_candidates, "light", 96),
        ("扫描文件夹", scan_folder_to_candidates, "light", 86),
        ("全选", lambda: set_candidate_selection("all"), "light", 60),
        ("取消全选", lambda: set_candidate_selection("none"), "light", 76),
        ("反选", lambda: set_candidate_selection("invert"), "light", 60),
        ("移除选中候选项", remove_selected_candidates, "danger", 116),
        ("确认选中项加入队列", confirm_selected_candidates_to_queue, "primary", 132),
    ):
        create_queue_window_button(candidate_toolbar, text, command, variant=variant, width=width)

    candidate_options = ttk.Frame(candidate_section, style="Card.TFrame")
    candidate_options.pack(fill="x", padx=8, pady=(0, 6))
    ttk.Checkbutton(candidate_options, text="扫描子文件夹", variable=queue_scan_subdirs_var, style="Queue.TCheckbutton").pack(side="left", padx=(0, 14))
    ttk.Checkbutton(candidate_options, text="跳过名称中包含 Restart 的文件", variable=queue_skip_restart_var, style="Queue.TCheckbutton").pack(side="left", padx=(0, 14))
    ttk.Checkbutton(candidate_options, text="跳过已经存在结果文件的作业", variable=queue_skip_existing_var, style="Queue.TCheckbutton").pack(side="left")
    ttk.Label(candidate_options, textvariable=queue_candidate_summary_var, style="Hint.TLabel").pack(side="right")
    queue_candidate_tree = create_queue_table(
        candidate_section,
        ("勾选", "序号", "作业名称", "INP 文件路径", "加入方式", "作业类型", "重启动依赖", "FOR 文件", "检查结果"),
        (55, 50, 130, 260, 80, 80, 120, 110, 170),
    )
    queue_candidate_tree.bind("<Button-1>", candidate_tree_toggle_selection)

    formal_section = ttk.LabelFrame(main, text="正式队列", style="Queue.TLabelframe")
    formal_section.pack(fill="both", expand=True)
    formal_toolbar = ttk.Frame(formal_section, style="Card.TFrame")
    formal_toolbar.pack(fill="x", padx=8, pady=(8, 6))
    create_queue_window_button(formal_toolbar, "取消选中的待运行作业", cancel_selected_pending_queue_items, width=148)
    create_queue_window_button(formal_toolbar, "编辑选中的待运行作业", edit_selected_pending_queue_item, width=148)
    create_queue_window_button(formal_toolbar, "终止选中的运行中作业", terminate_selected_running_queue_items, variant="danger", width=148)
    create_queue_window_button(formal_toolbar, "清理已结束记录", clear_finished_queue_items, width=112)
    ctk.CTkLabel(
        formal_toolbar,
        text="工作目录：",
        font=FONT_QUEUE_BUTTON,
        text_color="#111827",
        fg_color="#ffffff",
        height=30,
    ).pack(side="left", padx=(12, 4))
    queue_work_dir_combo = ctk.CTkComboBox(
        formal_toolbar,
        variable=queue_work_dir_var,
        values=queue_work_dir_history,
        width=250,
        height=30,
        corner_radius=7,
        border_width=1,
        border_color="#cbd5e1",
        fg_color="#ffffff",
        button_color=BTN_LIGHT_FG,
        button_hover_color=BTN_LIGHT_HOVER,
        dropdown_fg_color="#ffffff",
        dropdown_hover_color="#e5eefc",
        dropdown_text_color="#111827",
        text_color="#111827",
        font=FONT_QUEUE_BUTTON,
        dropdown_font=FONT_QUEUE_BUTTON,
    )
    queue_work_dir_combo.pack(side="left", padx=(0, 6))
    create_queue_window_button(
        formal_toolbar,
        "选择",
        select_queue_work_directory,
        width=58
    )
    queue_scan_external_btn = create_queue_window_button(
        formal_toolbar,
        "扫描",
        scan_selected_work_directory,
        variant="primary",
        width=58
    )
    ttk.Label(formal_toolbar, textvariable=queue_formal_summary_var, style="Hint.TLabel").pack(side="right")
    queue_formal_tree = create_queue_table(
        formal_section,
        ("序号", "作业名称", "INP 文件路径", "作业类型", "重启动依赖", "FOR 文件", "Core", "Mem", "状态", "备注"),
        (50, 130, 280, 80, 120, 110, 60, 80, 90, 180),
    )
    queue_formal_tree.bind("<Double-1>", show_queue_item_details)
    start_external_job_monitor()

    def on_close():
        global queue_manager_window
        queue_manager_window.destroy()
        queue_manager_window = None

    queue_manager_window.protocol("WM_DELETE_WINDOW", on_close)
    refresh_queue_manager_views()


def map_restart_oldjobs_to_queue_items(restart_items, restart_mapping):
    """Convert basename-keyed restart mapping to the queue's item keys."""
    mapped = {}
    for item in restart_items:
        basename = os.path.basename(get_joblist_item_path(item))
        oldjob_path = restart_mapping.get(basename)
        if oldjob_path:
            mapped[item] = oldjob_path

    return mapped


def save_joblist_file(work_dir, inp_names):
    """Save joblist.json with full INP paths when available."""
    path = os.path.join(work_dir, JOBLIST_FILENAME)
    has_absolute_items = any(os.path.isabs(item) for item in inp_names)
    if has_absolute_items:
        payload = [
            {
                "name": os.path.basename(get_joblist_item_path(item)),
                "path": get_joblist_item_path(item),
            }
            for item in inp_names
        ]
    else:
        payload = inp_names

    atomic_write_json(path, payload)

    return path


def scan_inp_names_in_dir(work_dir):
    """Return sorted INP file names in a directory."""
    return sorted(
        name for name in os.listdir(work_dir)
        if name.lower().endswith(".inp")
        and os.path.isfile(os.path.join(work_dir, name))
    )


def format_joblist_names_for_history(names):
    """Format all queue job names for the monitor history."""
    return "\n".join(
        f"{index}. {get_joblist_item_display(name)}"
        for index, name in enumerate(names, start=1)
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
        "本次计算 Job：\n"
        f"{format_joblist_names_for_history(inp_names)}\n\n"
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
        "本次追加 Job：\n"
        f"{format_joblist_names_for_history(new_names)}\n\n"
    )
    update_joblist_status_label()
    update_joblist_button_mode()
    schedule_dispatch_joblist(100)


def create_joblist_from_source():
    """Create a new queue from selected INP files or a scanned directory."""
    selection = get_joblist_items_from_selection()
    if not selection:
        return

    work_dir = selection["work_dir"]
    inp_names = selection["items"]

    try:
        joblist_path = save_joblist_file(work_dir, inp_names)
    except OSError as e:
        messagebox.showerror("保存失败", f"无法保存 joblist.json：\n{e}")
        return

    restart_names = [
        name for name in inp_names
        if inp_has_restart_keyword(
            name if os.path.isabs(name) else os.path.join(work_dir, name)
        )
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
        "本次计算 Job：\n"
        f"{format_joblist_names_for_history(inp_names)}\n\n"
    )
    update_joblist_status_label()
    update_joblist_button_mode()


def append_joblist_from_source():
    """Append selected INP files or scanned directory jobs to the active queue."""
    base_dir = joblist_state.get("work_dir", "")
    if not base_dir:
        create_joblist_from_source()
        return

    selection = get_joblist_items_from_selection()
    if not selection:
        return

    work_dir = selection["work_dir"]
    inp_names = selection["items"]

    existing_paths = {
        normalize_joblist_compare_path(get_joblist_item_path(name))
        for name in joblist_state.get("jobs", [])
    }
    new_names = [
        name for name in inp_names
        if normalize_joblist_compare_path(get_joblist_item_path(name)) not in existing_paths
    ]

    if not new_names:
        messagebox.showinfo("没有新作业", "没有可追加的新 INP 文件。")
        return

    restart_names = [
        name for name in new_names
        if inp_has_restart_keyword(get_joblist_item_path(name))
    ]
    restart_mapping = {}
    if restart_names:
        restart_files = [get_joblist_item_path(name) for name in restart_names]
        raw_restart_mapping = collect_restart_oldjob_paths(restart_files)
        if raw_restart_mapping is None:
            messagebox.showinfo("已取消", "已取消追加队列，未加入新作业。")
            return
        restart_mapping = map_restart_oldjobs_to_queue_items(
            restart_names,
            raw_restart_mapping
        )

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
        "本次追加 Job：\n"
        f"{format_joblist_names_for_history(new_names)}\n\n"
    )
    update_joblist_status_label()
    update_joblist_button_mode()
    schedule_dispatch_joblist(100)


def select_joblist_dir():
    """Open the standalone formal queue manager."""
    open_queue_manager_window()


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
            if joblist_state["statuses"].get(name) in (STATUS_WAITING_DEPENDENCY, "等待前置"):
                joblist_state["statuses"][name] = STATUS_PENDING_RUN

        restart_files = [
            get_joblist_item_path(name)
            for name in restart_names
        ]
        raw_oldjob_paths = collect_restart_oldjob_paths(restart_files)
        oldjob_paths = map_restart_oldjobs_to_queue_items(
            restart_names,
            raw_oldjob_paths or {}
        )
        if raw_oldjob_paths is None:
            messagebox.showinfo("已取消", "已取消队列提交。")
            return False

    joblist_state["oldjob_paths"] = oldjob_paths
    for item_id, oldjob_path in oldjob_paths.items():
        item = get_queue_item(item_id)
        if item is None:
            continue
        item.oldjob_path = oldjob_path
        item.oldjob_name = get_oldjob_name_from_path(oldjob_path)
        item.oldjob_dir = os.path.dirname(oldjob_path)
        item.run_mode = "restart"

    dependencies = {}
    missing_external_oldjobs = []
    invalid_oldjobs = []
    for inp_name, oldjob_path in oldjob_paths.items():
        oldjob_name = get_oldjob_name_from_path(oldjob_path)
        current_job_name = os.path.splitext(os.path.basename(get_joblist_item_path(inp_name)))[0]

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
            joblist_state["statuses"][inp_name] = STATUS_WAITING_DEPENDENCY
            item = get_queue_item(inp_name)
            if item is not None:
                item.status = STATUS_WAITING_DEPENDENCY
                item.message = f"等待前置作业：{dependency_inp_name}"
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
    sync_joblist_state_from_queue_items()
    return True


def start_joblist():
    """按资源估算并行提交 joblist 中的作业。"""
    sync_joblist_state_from_queue_items()
    if not any(item.status in (STATUS_PENDING_RUN, STATUS_WAITING_DEPENDENCY) for item in queue_items):
        open_queue_manager_window()
        messagebox.showinfo("队列为空", "请先在“管理队列”中确认需要运行的作业。")
        return

    if not ensure_joblist_restart_oldjobs(force_prompt=False):
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
    try:
        save_formal_queue_file()
    except OSError as e:
        messagebox.showwarning("保存失败", f"队列可以继续运行，但无法保存 joblist.json：\n{e}")
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
            if status not in (STATUS_PENDING_RUN, STATUS_WAITING_DEPENDENCY, "等待", "等待前置"):
                continue

            dependency_name = dependencies.get(name, "")
            if not dependency_name:
                return name

            dependency_status = joblist_state["statuses"].get(dependency_name)
            if is_completed_queue_status(dependency_status) or final_status_from_queue_status(dependency_status):
                oldjob_path = joblist_state["oldjob_paths"].get(name, "")
                ready, ready_detail = check_oldjob_result_ready(oldjob_path)
                item = get_queue_item(name)
                if ready:
                    joblist_state["statuses"][name] = STATUS_PENDING_RUN
                    if item is not None:
                        item.status = STATUS_PENDING_RUN
                        item.message = "前置作业已完成，ODB 已就绪"
                    return name

                joblist_state["statuses"][name] = STATUS_FAILED
                if item is not None:
                    item.status = STATUS_FAILED
                    item.message = f"前置作业完成但 {ready_detail}"
                append_history_text(
                    f"跳过 Restart 作业：{name}\n"
                    f"原因：前置作业 {dependency_name} 已完成，但 {ready_detail}。\n\n"
                )
                continue

            if dependency_status in (
                STATUS_FAILED,
                STATUS_CANCELED,
                STATUS_TERMINATED,
                "失败",
                "终止",
                "取消",
                "状态未知",
                "Datacheck Failed",
                "提交失败",
                "已停止",
                "跳过",
            ):
                joblist_state["statuses"][name] = STATUS_CANCELED
                item = get_queue_item(name)
                if item is not None:
                    item.status = STATUS_CANCELED
                    item.message = f"前置作业未正常完成：{dependency_name}"
                append_history_text(
                    f"跳过 Restart 作业：{name}\n"
                    f"原因：前置作业 {dependency_name} 未正常完成（{dependency_status}）。\n\n"
                )
                continue

            joblist_state["statuses"][name] = STATUS_WAITING_DEPENDENCY
            item = get_queue_item(name)
            if item is not None:
                item.status = STATUS_WAITING_DEPENDENCY
                item.message = f"等待前置作业：{dependency_name}"

    return ""


def has_runnable_joblist_item():
    """Return True when the queue has a non-dependency pending item."""
    dependencies = joblist_state.get("dependencies", {})
    for name in joblist_state["jobs"]:
        if joblist_state["statuses"].get(name) != STATUS_PENDING_RUN:
            continue

        dependency_name = dependencies.get(name, "")
        if not dependency_name or final_status_from_queue_status(joblist_state["statuses"].get(dependency_name)):
            return True

    return False


def maybe_log_external_slot_notice(external_active_count, submit_slots, waiting):
    """Log once when external jobs consume queue parallel slots."""
    if external_active_count <= 0 or submit_slots > 0 or not waiting:
        return

    signature = (
        external_active_count,
        joblist_state.get("max_parallel"),
        len(joblist_state.get("running", set())),
    )
    if joblist_state.get("external_slot_notice_signature") == signature:
        return

    joblist_state["external_slot_notice_signature"] = signature
    append_history_text(
        f"检测到 {external_active_count} 个外部运行作业，已计入并行槽位。"
        "当前暂无可用队列槽位。\n\n"
    )


def schedule_dispatch_joblist(delay_ms=100):
    """Schedule one queue dispatch pass, coalescing duplicate timers."""
    with measure_ui_callback("schedule_dispatch_joblist"):
        if not joblist_state.get("active"):
            return
    
        due_time = time.monotonic() + max(0, delay_ms) / 1000
        existing_after_id = joblist_state.get("dispatch_after_id")
        existing_due_time = float(joblist_state.get("dispatch_due_time") or 0)
    
        if existing_after_id and existing_due_time and existing_due_time <= due_time:
            return
    
        if existing_after_id:
            try:
                root.after_cancel(existing_after_id)
            except tk.TclError:
                pass
    
        def run_scheduled_dispatch():
            joblist_state["dispatch_after_id"] = None
            joblist_state["dispatch_due_time"] = 0.0
            dispatch_joblist()
    
        joblist_state["dispatch_due_time"] = due_time
        joblist_state["dispatch_after_id"] = root.after(
            max(0, delay_ms),
            run_scheduled_dispatch
        )


def dispatch_joblist():
    """按当前资源状态补位提交队列作业。"""
    with measure_ui_callback("dispatch_joblist"):
        if not joblist_state["active"]:
            return
    
        sync_joblist_state_from_queue_items()
    
        slots, _ = estimate_available_job_slots()
        queue_room = max(0, joblist_state["max_parallel"] - len(joblist_state["running"]))
        external_active_count = get_external_active_job_count()
        total_job_room = max(0, joblist_state["max_parallel"] - get_total_managed_active_job_count())
        submit_slots = max(0, min(slots, queue_room, total_job_room))
        waiting = any(
            joblist_state["statuses"].get(name) in (STATUS_PENDING_RUN, STATUS_WAITING_DEPENDENCY, "等待", "等待前置")
            for name in joblist_state["jobs"]
        )
        maybe_log_external_slot_notice(external_active_count, submit_slots, waiting)
    
        while submit_slots > 0:
            next_name = get_next_ready_joblist_name()
    
            if not next_name:
                break
    
            queue_item = get_queue_item(next_name)
            if queue_item is None:
                joblist_state["statuses"][next_name] = STATUS_FAILED
                continue
    
            if not validate_formal_queue_item_before_submit(queue_item):
                joblist_state["statuses"][next_name] = queue_item.status
                refresh_queue_manager_views()
                submit_slots -= 1
                continue
    
            inp_file = get_joblist_item_path(next_name)
            queue_item.status = STATUS_STARTING
            queue_item.message = "正在启动 Abaqus"
            joblist_state["statuses"][next_name] = STATUS_STARTING
            update_joblist_status_label()
            refresh_queue_manager_views()
    
            submitted_job = submit_job(
                inp_file_override=inp_file,
                queue_mode=True,
                oldjob_path_override=joblist_state["oldjob_paths"].get(next_name, ""),
                queue_job_key_override=next_name,
                queue_item_override=queue_item
            )
            if submitted_job:
                joblist_state["running"].add(next_name)
                queue_item.status = STATUS_RUNNING
                queue_item.message = "运行中"
                joblist_state["statuses"][next_name] = STATUS_RUNNING
            else:
                queue_item.status = STATUS_FAILED
                queue_item.message = "提交失败"
                queue_item.active_job_key = ""
                joblist_state["statuses"][next_name] = STATUS_FAILED
    
            submit_slots -= 1
    
        waiting = any(
            joblist_state["statuses"].get(name) in (STATUS_PENDING_RUN, STATUS_WAITING_DEPENDENCY, "等待", "等待前置")
            for name in joblist_state["jobs"]
        )
    
        if not waiting and not joblist_state["running"]:
            joblist_state["active"] = False
            update_joblist_status_label("队列：全部完成")
            update_joblist_button_mode()
            append_history_text("队列提交结束。\n\n")
        else:
            update_joblist_status_label()
            refresh_queue_manager_views()
            if waiting and has_runnable_joblist_item() and (not joblist_state["running"] or submit_slots <= 0):
                schedule_dispatch_joblist(5000)


def finish_joblist_job(job_state, status, detail=""):
    """队列作业结束后更新状态并继续补位。"""
    inp_name = job_state.get("joblist_inp_name")
    if not inp_name:
        return

    joblist_state["running"].discard(inp_name)
    queue_status = queue_status_from_final_status(status)
    joblist_state["statuses"][inp_name] = queue_status
    item = get_queue_item(inp_name)
    if item is not None:
        item.status = queue_status
        item.message = detail or format_final_status_for_display(status, detail)
        item.elapsed = format_duration(job_state.get("end_time", time.time()) - job_state.get("start_time", time.time()))

    for dependent_name, dependency_name in joblist_state.get("dependencies", {}).items():
        if dependency_name != inp_name:
            continue

        if is_completed_queue_status(status):
            if joblist_state["statuses"].get(dependent_name) in (STATUS_WAITING_DEPENDENCY, "等待前置"):
                oldjob_path = joblist_state["oldjob_paths"].get(dependent_name, "")
                ready, ready_detail = check_oldjob_result_ready(oldjob_path)
                dependent_item = get_queue_item(dependent_name)
                if ready:
                    joblist_state["statuses"][dependent_name] = STATUS_PENDING_RUN
                    if dependent_item is not None:
                        dependent_item.status = STATUS_PENDING_RUN
                        dependent_item.message = "前置作业已完成，ODB 已就绪"
                else:
                    joblist_state["statuses"][dependent_name] = STATUS_FAILED
                    if dependent_item is not None:
                        dependent_item.status = STATUS_FAILED
                        dependent_item.message = f"前置作业完成但 {ready_detail}"
                append_history_text(
                    f"释放 Restart 作业：{dependent_name}\n"
                    f"前置作业 {inp_name} 已完成，oldjob ODB：{oldjob_path}\n"
                    f"检查结果：{'ODB 已就绪，LCK 已释放' if ready else ready_detail}\n\n"
                )
        else:
            joblist_state["statuses"][dependent_name] = STATUS_CANCELED
            dependent_item = get_queue_item(dependent_name)
            if dependent_item is not None:
                dependent_item.status = STATUS_CANCELED
                dependent_item.message = f"前置作业未正常完成：{inp_name}"
            append_history_text(
                f"跳过 Restart 作业：{dependent_name}\n"
                f"原因：前置作业 {inp_name} 未正常完成（{status}）。\n\n"
            )

    update_joblist_status_label()
    update_joblist_button_mode()
    refresh_queue_manager_views()
    schedule_save_formal_queue_file()
    schedule_dispatch_joblist(500)


def stop_joblist_queue(source_job_state=None):
    """停止队列继续提交后续等待作业，不影响已经运行的作业。"""
    if not joblist_state["active"]:
        return

    joblist_state["active"] = False
    skipped = 0
    for name in joblist_state["jobs"]:
        if joblist_state["statuses"].get(name) in (STATUS_PENDING_RUN, STATUS_WAITING_DEPENDENCY, "等待", "等待前置"):
            joblist_state["statuses"][name] = STATUS_CANCELED
            item = get_queue_item(name)
            if item is not None:
                item.status = STATUS_CANCELED
                item.message = "队列终止，未提交"
            skipped += 1

    update_joblist_status_label("队列：已终止，不再提交等待作业")
    update_joblist_button_mode()
    refresh_queue_manager_views()
    append_history_text(f"队列已终止，停止提交等待作业 {skipped} 个。\n\n")

    if source_job_state is not None:
        log_widget = source_job_state.get("log_widget")
        if log_widget is not None:
            append_log(log_widget, f"状态：队列已终止，停止提交等待作业 {skipped} 个。\n")

    update_joblist_button_mode()


def submit_job(inp_file_override="", queue_mode=False, oldjob_path_override="", queue_job_key_override="", queue_item_override=None):
    """提交 Abaqus 作业"""
    inp_file = inp_file_override or inp_file_var.get().strip()
    if queue_item_override is not None:
        queue_settings = get_queue_item_command_settings(queue_item_override)
        cpus_text = queue_settings["cpus_text"]
        oldjob_path = queue_settings["oldjob_path"] or oldjob_path_override
        for_file_path = queue_settings["for_file_path"]
        interactive_mode = queue_settings["interactive_mode"]
        memory_argument = queue_settings["memory_argument"]
        datacheck_mode = queue_settings["datacheck_mode"]
    else:
        cpus_text = cpus_var.get().strip()
        oldjob_path = oldjob_path_override if oldjob_path_override else ("" if queue_mode else oldjob_var.get().strip())
        for_file_path = for_file_var.get().strip()
        interactive_mode = interactive_var.get()
        memory_argument = get_memory_argument()
        datacheck_mode = datacheck_var.get()
    oldjob_name = get_oldjob_name_from_path(oldjob_path) if oldjob_path else ""

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
        ready, ready_detail = check_oldjob_result_ready(oldjob_path)
        if not ready:
            messagebox.showerror("错误", f"{ready_detail}：\n{oldjob_path}")
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
    if queue_item_override is not None:
        queue_item_override.active_job_key = job_key

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
        "joblist_inp_name": queue_job_key_override if queue_mode else "",
        "queue_item_id": queue_item_override.item_id if queue_item_override is not None else "",
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
        register_single_submit_queue_item(job_state)
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

    except (OSError, subprocess.SubprocessError, ValueError) as e:
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
configure_ui_performance(root)
if ENABLE_UI_PERFORMANCE_LOG:
    start_ui_lag_watchdog()

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
    "Light.TButton",
    font=FONT_BUTTON_BOLD,
    padding=(4, 4),
    background=BTN_LIGHT_FG,
    foreground=BTN_LIGHT_TEXT,
    borderwidth=0,
    focusthickness=0,
)

style.map(
    "Light.TButton",
    background=[
        ("active", BTN_LIGHT_HOVER),
        ("pressed", BTN_LIGHT_HOVER),
        ("disabled", "#e5e7eb"),
        ("!disabled", BTN_LIGHT_FG),
    ],
    foreground=[
        ("disabled", "#94a3b8"),
        ("!disabled", BTN_LIGHT_TEXT),
    ],
)

style.configure(
    "Primary.TButton",
    font=FONT_BUTTON_BOLD,
    padding=(4, 4),
    background="#2563eb",
    foreground="#ffffff",
    borderwidth=0,
    focusthickness=0,
)

style.map(
    "Primary.TButton",
    background=[
        ("active", "#1d4ed8"),
        ("pressed", "#1d4ed8"),
        ("disabled", "#93c5fd"),
        ("!disabled", "#2563eb"),
    ],
    foreground=[
        ("disabled", "#e0f2fe"),
        ("!disabled", "#ffffff"),
    ],
)

style.configure(
    "Danger.TButton",
    font=FONT_BUTTON_BOLD,
    padding=(4, 4),
    background="#7f1d1d",
    foreground="#ffffff",
    borderwidth=0,
    focusthickness=0,
)

style.map(
    "Danger.TButton",
    background=[
        ("active", "#991b1b"),
        ("pressed", "#991b1b"),
        ("disabled", "#e5e7eb"),
        ("!disabled", "#7f1d1d"),
    ],
    foreground=[
        ("disabled", "#94a3b8"),
        ("!disabled", "#ffffff"),
    ],
)

style.configure(
    "Main.TCheckbutton",
    font=FONT_HINT,
    background="#ffffff",
    foreground="#111827",
)

style.map(
    "Main.TCheckbutton",
    background=[("active", "#ffffff"), ("selected", "#ffffff")],
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

style.configure(
    "Queue.Treeview",
    background="#ffffff",
    fieldbackground="#ffffff",
    foreground="#111827",
    rowheight=26,
    borderwidth=0,
    font=FONT_QUEUE_TABLE,
)

style.configure(
    "Queue.Treeview.Heading",
    background="#eef4fb",
    foreground="#111827",
    relief="flat",
    font=FONT_QUEUE_HEADING,
)

style.map(
    "Queue.Treeview.Heading",
    background=[("active", "#dbe3ee")]
)

style.configure(
    "Queue.Vertical.TScrollbar",
    gripcount=0,
    background="#9aa8ba",
    darkcolor="#9aa8ba",
    lightcolor="#9aa8ba",
    troughcolor="#f1f5f9",
    bordercolor="#f1f5f9",
    arrowcolor="#9aa8ba",
    relief="flat",
    width=12,
)

style.map(
    "Queue.Vertical.TScrollbar",
    background=[
        ("active", "#7f8da0"),
        ("pressed", "#7f8da0"),
        ("disabled", "#cbd5e1"),
        ("!active", "#9aa8ba"),
    ],
    arrowcolor=[
        ("active", "#7f8da0"),
        ("pressed", "#7f8da0"),
        ("disabled", "#cbd5e1"),
        ("!active", "#9aa8ba"),
    ],
)

style.configure(
    "Queue.Horizontal.TScrollbar",
    gripcount=0,
    background="#9aa8ba",
    darkcolor="#9aa8ba",
    lightcolor="#9aa8ba",
    troughcolor="#f1f5f9",
    bordercolor="#f1f5f9",
    arrowcolor="#9aa8ba",
    relief="flat",
    width=12,
)

style.map(
    "Queue.Horizontal.TScrollbar",
    background=[
        ("active", "#7f8da0"),
        ("pressed", "#7f8da0"),
        ("disabled", "#cbd5e1"),
        ("!active", "#9aa8ba"),
    ],
    arrowcolor=[
        ("active", "#7f8da0"),
        ("pressed", "#7f8da0"),
        ("disabled", "#cbd5e1"),
        ("!active", "#9aa8ba"),
    ],
)

style.configure(
    "Queue.TLabelframe",
    background="#ffffff",
    borderwidth=1,
    relief="solid",
)

style.configure(
    "Queue.TLabelframe.Label",
    background="#ffffff",
    foreground="#111827",
    font=FONT_LABEL,
)

style.configure(
    "Queue.TCheckbutton",
    background="#ffffff",
    foreground="#111827",
    font=FONT_QUEUE_HEADING,
)

style.map(
    "Queue.TCheckbutton",
    background=[("active", "#ffffff"), ("selected", "#ffffff")],
)

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
    text="管理队列",
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
    style="Hint.TLabel",
    wraplength=360,
    justify="left"
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
    text="运行监控",
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
history_text.placeholder_visible = True

# ================= 启动程序 =================

start_ui_event_queue_polling()
root.after_idle(lambda: root.after(1000, detect_abaqus_command))
root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()
