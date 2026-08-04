"""Shared, readable Qt styling for the desktop application."""

from __future__ import annotations

from abaqus_workbench_core.theme import apply_workbench_theme
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QMainWindow,
)


class AccidentalWheelGuard(QObject):
    """Prevent a hovered closed selector from changing a saved setting."""

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Wheel and isinstance(
            watched, (QComboBox, QAbstractSpinBox)
        ):
            event.ignore()
            return True
        return super().eventFilter(watched, event)


def apply_application_style(application: QApplication) -> None:
    """Apply the shared workbench theme and the postprocessor wheel guard."""

    apply_workbench_theme(application)
    wheel_guard = AccidentalWheelGuard(application)
    application.installEventFilter(wheel_guard)
    application._accidental_wheel_guard = wheel_guard


def configure_main_window(window: QMainWindow) -> None:
    """Set useful size constraints while remaining friendly to smaller screens."""

    window.setMinimumSize(1120, 700)
    screen = QApplication.primaryScreen()
    if screen is None:
        window.resize(1500, 880)
        return
    available = screen.availableGeometry()
    window.resize(min(1600, available.width()), min(950, available.height()))
