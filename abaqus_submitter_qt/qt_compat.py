"""Small compatibility layer for PySide6 / PyQt6.

The project does not pin a Qt binding yet.  Import PySide6 first because it is
LGPL friendly for this use case, then fall back to PyQt6 if the user already
has it installed.
"""

import threading
import time
from contextlib import contextmanager
from functools import wraps

try:  # pragma: no cover - depends on the user's environment
    from PySide6 import QtCore, QtGui, QtWidgets

    Signal = QtCore.Signal
    Slot = QtCore.Slot
    QT_BINDING = "PySide6"
except ImportError as pyside_error:  # pragma: no cover - depends on environment
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets

        Signal = QtCore.pyqtSignal
        Slot = QtCore.pyqtSlot
        QT_BINDING = "PyQt6"
    except ImportError as pyqt_error:
        raise ImportError(
            "未安装 Qt 绑定。请先安装 PySide6 或 PyQt6，例如：\n    pip install PySide6\n或：\n    pip install PyQt6"
        ) from pyqt_error


ENABLE_HANG_PROBE_LOGS = False


def current_thread_name() -> str:
    qt_thread = QtCore.QThread.currentThread()
    qt_name = qt_thread.objectName() if qt_thread is not None else ""
    py_name = threading.current_thread().name
    return f"{py_name}/{qt_name}" if qt_name else py_name


def is_gui_thread() -> bool:
    app = QtCore.QCoreApplication.instance()
    return bool(app is not None and QtCore.QThread.currentThread() == app.thread())


def hang_probe_log(label: str, elapsed: float | None = None, threshold: float = 0.2, **fields) -> None:
    if not ENABLE_HANG_PROBE_LOGS:
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
    start = time.monotonic()
    try:
        yield
    finally:
        hang_probe_log(label, time.monotonic() - start, threshold, **fields)


def hang_probe_function(label: str | None = None, threshold: float = 0.2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with hang_probe(label or func.__qualname__, threshold):
                return func(*args, **kwargs)

        return wrapper

    return decorator


__all__ = [
    "QtCore",
    "QtGui",
    "QtWidgets",
    "Signal",
    "Slot",
    "QT_BINDING",
    "hang_probe",
    "hang_probe_function",
    "hang_probe_log",
    "is_gui_thread",
]
