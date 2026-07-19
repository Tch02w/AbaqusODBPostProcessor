"""Small compatibility layer for PySide6 / PyQt6.

The project does not pin a Qt binding yet.  Import PySide6 first because it is
LGPL friendly for this use case, then fall back to PyQt6 if the user already
has it installed.
"""

try:  # pragma: no cover - depends on the user's environment
    from PySide6 import QtCore, QtGui, QtWidgets

    Signal = QtCore.Signal
    Slot = QtCore.Slot
    QT_BINDING = "PySide6"
except ImportError:  # pragma: no cover - depends on environment
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets

        Signal = QtCore.pyqtSignal
        Slot = QtCore.pyqtSlot
        QT_BINDING = "PyQt6"
    except ImportError as pyqt_error:
        raise ImportError(
            "未安装 Qt 绑定。请先安装 PySide6 或 PyQt6，例如：\n    pip install PySide6\n或：\n    pip install PyQt6"
        ) from pyqt_error

__all__ = [
    "QtCore",
    "QtGui",
    "QtWidgets",
    "Signal",
    "Slot",
    "QT_BINDING",
]
