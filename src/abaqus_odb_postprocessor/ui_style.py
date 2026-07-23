"""Shared, readable Qt styling for the desktop application."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QFont
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
    """Use Qt's built-in light controls with one application-wide font."""

    application.setStyle("Fusion")
    application.setPalette(application.style().standardPalette())
    font = QFont("Microsoft YaHei UI")
    font.setPointSize(11)
    application.setFont(font)
    wheel_guard = AccidentalWheelGuard(application)
    application.installEventFilter(wheel_guard)
    application._accidental_wheel_guard = wheel_guard
    application.setStyleSheet("")


def configure_main_window(window: QMainWindow) -> None:
    """Set useful size constraints while remaining friendly to smaller screens."""

    window.setMinimumSize(1120, 700)
    screen = QApplication.primaryScreen()
    if screen is None:
        window.resize(1500, 880)
        return
    available = screen.availableGeometry()
    window.resize(min(1600, available.width()), min(950, available.height()))
