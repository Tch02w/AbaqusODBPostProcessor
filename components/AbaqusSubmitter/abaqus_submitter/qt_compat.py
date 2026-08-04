"""Compatibility import retained for callers; the merged UI is PySide6-only."""

from abaqus_workbench_core.qt import (
    QT_BINDING,
    QtCore,
    QtGui,
    QtWidgets,
    Signal,
    Slot,
)

__all__ = [
    "QtCore",
    "QtGui",
    "QtWidgets",
    "Signal",
    "Slot",
    "QT_BINDING",
]
