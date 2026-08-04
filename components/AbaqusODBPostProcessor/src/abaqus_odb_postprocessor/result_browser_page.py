"""Embeddable result-browser page for the unified workbench."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .result_browser import ResultBrowserDialog


class ResultBrowserPage(QWidget):
    def __init__(self, result_root: Path, *, coordinator, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("resultBrowserPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.browser = ResultBrowserDialog(
            result_root,
            self,
            coordinator=coordinator,
            prompt_for_unindexed=True,
        )
        self.browser.setWindowFlags(Qt.WindowType.Widget)
        self.browser.setWindowModality(Qt.WindowModality.NonModal)
        self.browser.setSizeGripEnabled(False)
        self.browser.setMinimumSize(0, 0)
        layout.addWidget(self.browser)

    def set_result_root(self, result_root: Path) -> None:
        self.browser.set_result_root(Path(result_root))

    def shutdown(self) -> bool:
        return bool(self.browser.close())
