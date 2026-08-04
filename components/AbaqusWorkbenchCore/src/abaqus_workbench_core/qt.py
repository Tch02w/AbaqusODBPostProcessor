"""The only Qt binding and QApplication policy used by the merged product."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

Signal = QtCore.Signal
Slot = QtCore.Slot
QT_BINDING = "PySide6"


@dataclass(frozen=True, slots=True)
class ApplicationHandle:
    application: QtWidgets.QApplication
    owns_event_loop: bool


def ensure_application(
    argv: Sequence[str] | None = None,
    *,
    application_name: str = "Abaqus Workbench",
) -> ApplicationHandle:
    """Return the process-wide QApplication, creating it at most once."""

    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return ApplicationHandle(existing, False)
    application = QtWidgets.QApplication(list(sys.argv if argv is None else argv))
    application.setApplicationName(application_name)
    application.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
    return ApplicationHandle(application, True)


__all__ = [
    "QT_BINDING",
    "ApplicationHandle",
    "QtCore",
    "QtGui",
    "QtWidgets",
    "Signal",
    "Slot",
    "ensure_application",
]
