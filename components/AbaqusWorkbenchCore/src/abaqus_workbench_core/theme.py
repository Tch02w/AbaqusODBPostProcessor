"""One process-wide PySide6 rendering policy for the merged workbench."""

from __future__ import annotations

from dataclasses import dataclass

from .qt import QtGui, QtWidgets


@dataclass(frozen=True, slots=True)
class WorkbenchTheme:
    font_family: str = "Microsoft YaHei UI"
    font_point_size: int = 11

    def apply(self, application: QtWidgets.QApplication) -> None:
        application.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
        application.setPalette(application.style().standardPalette())
        font = QtGui.QFont(self.font_family)
        font.setPointSize(self.font_point_size)
        application.setFont(font)


DEFAULT_THEME = WorkbenchTheme()


def apply_workbench_theme(application: QtWidgets.QApplication) -> None:
    DEFAULT_THEME.apply(application)
