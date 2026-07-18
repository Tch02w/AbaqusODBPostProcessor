"""Optional diagnostic helpers kept out of runtime business modules."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from functools import wraps

STARTUP_TIMELINE_ENV = "ABASUB_STARTUP_TIMELINE"
HANG_PROBE_ENV = "ABASUB_HANG_PROBE"
PERFORMANCE_LOG_ENV = "ABASUB_PERFORMANCE_LOG"
EXTERNAL_SCAN_DEBUG_ENV = "ABASUB_EXTERNAL_SCAN_DEBUG"


def env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def startup_timeline_enabled() -> bool:
    return env_flag_enabled(STARTUP_TIMELINE_ENV)


class StartupTimeline:
    """Small elapsed-time logger for startup phases."""

    def __init__(
        self,
        scope: str,
        *,
        enabled: bool | None = None,
        start: float | None = None,
        last: float | None = None,
    ) -> None:
        self.scope = scope
        self.enabled = startup_timeline_enabled() if enabled is None else enabled
        self.start = time.monotonic() if start is None else start
        self.last = self.start if last is None else last
        self.active = self.enabled

    def mark(self, label: str, **fields) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        elapsed = now - self.last
        total = now - self.start
        self.last = now
        parts = [
            f"[STARTUP-TIMELINE] {self.scope} {label}",
            f"elapsed={elapsed:.3f}s",
            f"total={total:.3f}s",
        ]
        for key, value in fields.items():
            if value not in (None, ""):
                parts.append(f"{key}={value}")
        print(" ".join(parts))


def current_thread_name() -> str:
    py_name = threading.current_thread().name
    try:
        from .qt_compat import QtCore

        qt_thread = QtCore.QThread.currentThread()
        qt_name = qt_thread.objectName() if qt_thread is not None else ""
    except Exception:
        qt_name = ""
    return f"{py_name}/{qt_name}" if qt_name else py_name


def is_gui_thread() -> bool:
    try:
        from .qt_compat import QtCore

        app = QtCore.QCoreApplication.instance()
        return bool(app is not None and QtCore.QThread.currentThread() == app.thread())
    except Exception:
        return False


def hang_probe_enabled() -> bool:
    return env_flag_enabled(HANG_PROBE_ENV)


def performance_log(message: str) -> None:
    if env_flag_enabled(PERFORMANCE_LOG_ENV):
        print(f"[perf] {message}")


def external_scan_debug_enabled() -> bool:
    return env_flag_enabled(EXTERNAL_SCAN_DEBUG_ENV)


def hang_probe_log(label: str, elapsed: float | None = None, threshold: float = 0.2, **fields) -> None:
    if not hang_probe_enabled():
        return
    if elapsed is not None and elapsed < threshold:
        return
    parts = [f"[HANG-PROBE] {label}"]
    if elapsed is not None:
        parts.append(f"elapsed={elapsed:.3f}s")
    parts.append(f"thread={current_thread_name()}")
    parts.append(f"gui_thread={is_gui_thread()}")
    for key, value in fields.items():
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    print(" ".join(parts))


@contextmanager
def hang_probe(label: str, threshold: float = 0.2, **fields):
    if not hang_probe_enabled():
        yield
        return

    start = time.monotonic()
    try:
        yield
    finally:
        hang_probe_log(label, time.monotonic() - start, threshold, **fields)


def hang_probe_function(label: str | None = None, threshold: float = 0.2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not hang_probe_enabled():
                return func(*args, **kwargs)

            with hang_probe(label or func.__qualname__, threshold):
                return func(*args, **kwargs)

        return wrapper

    return decorator
