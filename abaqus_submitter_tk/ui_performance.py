import threading
import time
from contextlib import contextmanager
from datetime import datetime

try:
    import tkinter as tk
except ImportError:
    tk = None

TCL_ERRORS = (tk.TclError,) if tk is not None else (Exception,)


ENABLE_UI_PERFORMANCE_LOG = False

UI_PERFORMANCE_LOG_PATH = "ui_performance.log"

UI_LAG_HEARTBEAT_INTERVAL_MS = 100
UI_LAG_WARNING_THRESHOLD_MS = 180

UI_CALLBACK_WARNING_THRESHOLD_MS = 30
UI_EVENT_QUEUE_WARNING_SIZE = 20
UI_EVENT_QUEUE_MAX_EVENTS_PER_TICK = 50

_ui_performance_root = None

_ui_lag_watchdog_state = {
    "running": False,
    "after_id": None,
    "expected_at": 0.0,
}

_ui_performance_lock = threading.Lock()


def configure_ui_performance(root):
    """
    Register Tk root for optional UI performance diagnostics.
    """
    global _ui_performance_root
    _ui_performance_root = root


def write_ui_performance_log(category, message):
    """
    Write one UI performance log line.
    Silently ignore failures.
    """
    if not ENABLE_UI_PERFORMANCE_LOG:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    thread_name = threading.current_thread().name
    line = f"[{timestamp}] [{thread_name}] [{category}] {message}\n"

    try:
        with _ui_performance_lock:
            with open(UI_PERFORMANCE_LOG_PATH, "a", encoding="utf-8") as file:
                file.write(line)
    except (OSError, PermissionError):
        pass


@contextmanager
def measure_ui_callback(label):
    """
    Measure one UI callback and log only slow calls.
    """
    if not ENABLE_UI_PERFORMANCE_LOG:
        yield
        return

    started_at = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        if elapsed_ms >= UI_CALLBACK_WARNING_THRESHOLD_MS:
            write_ui_performance_log(
                "UI_CALLBACK",
                f"{label}: {elapsed_ms:.1f} ms"
            )


def start_ui_lag_watchdog():
    """Start the optional Tk event-loop lag watchdog."""
    if not ENABLE_UI_PERFORMANCE_LOG:
        return

    if _ui_performance_root is None:
        return

    if _ui_lag_watchdog_state.get("running"):
        return

    _ui_lag_watchdog_state["running"] = True
    _ui_lag_watchdog_state["expected_at"] = (
        time.monotonic() + UI_LAG_HEARTBEAT_INTERVAL_MS / 1000
    )
    try:
        _ui_lag_watchdog_state["after_id"] = _ui_performance_root.after(
            UI_LAG_HEARTBEAT_INTERVAL_MS,
            run_ui_lag_watchdog
        )
    except TCL_ERRORS:
        _ui_lag_watchdog_state["running"] = False
        _ui_lag_watchdog_state["after_id"] = None


def stop_ui_lag_watchdog():
    """Stop the optional Tk event-loop lag watchdog."""
    after_id = _ui_lag_watchdog_state.get("after_id")
    root = _ui_performance_root
    if after_id and root is not None:
        try:
            root.after_cancel(after_id)
        except TCL_ERRORS:
            pass

    _ui_lag_watchdog_state["running"] = False
    _ui_lag_watchdog_state["after_id"] = None


def run_ui_lag_watchdog():
    """Measure Tk event-loop delay and reschedule itself."""
    if not ENABLE_UI_PERFORMANCE_LOG:
        _ui_lag_watchdog_state["running"] = False
        _ui_lag_watchdog_state["after_id"] = None
        return

    root = _ui_performance_root
    if root is None or not _ui_lag_watchdog_state.get("running"):
        return

    expected_at = float(_ui_lag_watchdog_state.get("expected_at") or time.monotonic())
    lag_ms = max(0.0, (time.monotonic() - expected_at) * 1000)
    if lag_ms >= UI_LAG_WARNING_THRESHOLD_MS:
        write_ui_performance_log(
            "UI_LAG",
            f"event loop delayed: {lag_ms:.1f} ms"
        )

    _ui_lag_watchdog_state["expected_at"] = (
        time.monotonic() + UI_LAG_HEARTBEAT_INTERVAL_MS / 1000
    )
    try:
        _ui_lag_watchdog_state["after_id"] = root.after(
            UI_LAG_HEARTBEAT_INTERVAL_MS,
            run_ui_lag_watchdog
        )
    except TCL_ERRORS:
        _ui_lag_watchdog_state["running"] = False
        _ui_lag_watchdog_state["after_id"] = None


def log_ui_queue_status(
    queue_size_before,
    processed_count,
    remaining_count,
    elapsed_ms,
):
    """
    Log UI queue backlog or slow processing.
    """
    if not ENABLE_UI_PERFORMANCE_LOG:
        return

    if (
            queue_size_before < UI_EVENT_QUEUE_WARNING_SIZE
            and elapsed_ms < UI_CALLBACK_WARNING_THRESHOLD_MS
    ):
        return

    write_ui_performance_log(
        "UI_QUEUE",
        (
            f"before={queue_size_before} processed={processed_count} "
            f"remaining={remaining_count} elapsed={elapsed_ms:.1f} ms"
        )
    )


def log_worker_performance(label, elapsed_ms, **details):
    """
    Log worker duration when diagnostics are enabled.
    """
    if not ENABLE_UI_PERFORMANCE_LOG:
        return

    detail_text = " ".join(
        f"{key}={value}"
        for key, value in details.items()
    )
    suffix = f" {detail_text}" if detail_text else ""
    write_ui_performance_log(
        "WORKER",
        f"{label} elapsed={elapsed_ms:.1f} ms{suffix}"
    )
