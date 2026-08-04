"""Embeddable ODB postprocessor page without another QApplication."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .app import MainWindow as PostProcessorWindow
from .paths import APP_NAME


class PostProcessorPage(QWidget):
    """Host the existing postprocessor controller as a workbench child widget."""

    resultBrowserRequested = Signal(object)
    resultRootChanged = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("postProcessorPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.controller = PostProcessorWindow()
        self.controller.setParent(self)
        self.controller.setWindowFlags(Qt.WindowType.Widget)
        self.controller.setMinimumSize(0, 0)
        layout.addWidget(self.controller)

        try:
            self.controller.result_browser_button.clicked.disconnect(
                self.controller._open_result_browser
            )
        except (RuntimeError, TypeError):
            pass
        self.controller.result_browser_button.clicked.connect(
            self._request_result_browser
        )
        self.controller.folder_edit.textChanged.connect(self._folder_changed)

    def current_result_root(self) -> Path:
        folder = Path(self.controller.folder_edit.text().strip())
        base = folder if folder.is_dir() else Path.cwd()
        return (base / f"{APP_NAME}_Results").resolve()

    def set_source_path(self, source: str | Path) -> Path:
        """Point the page at an ODB or folder without starting a scan."""

        path = Path(source).expanduser()
        folder = path.parent if path.suffix.lower() == ".odb" else path
        self.controller.folder_edit.setText(str(folder))
        self.controller.folder_edit.setFocus()
        return folder

    def _request_result_browser(self) -> None:
        result_root = self.current_result_root()
        self.resultRootChanged.emit(result_root)
        self.resultBrowserRequested.emit(result_root)

    def _folder_changed(self, _text: str) -> None:
        self.resultRootChanged.emit(self.current_result_root())

    def shutdown(self) -> bool:
        return bool(self.controller.close())
