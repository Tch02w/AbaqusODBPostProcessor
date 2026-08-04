"""Composition root for the single-window PySide6 workbench."""

from __future__ import annotations

from pathlib import Path

__lazy_modules__ = [
    "abaqus_odb_postprocessor.postprocessor_page",
    "abaqus_odb_postprocessor.result_browser_page",
]

from abaqus_odb_postprocessor.postprocessor_page import PostProcessorPage
from abaqus_odb_postprocessor.result_browser_page import ResultBrowserPage
from abaqus_submitter.main import MainWindow as SubmitterMainWindow
from abaqus_workbench_core.qt import QtCore, QtWidgets

from .routes import odb_candidate_for_job


class IntegratedMainWindow(SubmitterMainWindow):
    """Extend the submitter shell with postprocessing and result pages."""

    APP_TITLE = "Abaqus Workbench"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(self.APP_TITLE)
        if hasattr(self, "window_chrome"):
            self.window_chrome.title_label.setText(self.APP_TITLE)

        self.postprocessor_page = None
        self.result_browser_page = None
        self._loading_tool_page = False
        self.postprocessor_placeholder = self._loading_placeholder(
            "首次打开时加载 ODB 后处理模块…"
        )
        self.result_browser_placeholder = self._loading_placeholder(
            "首次打开时加载结果浏览模块…"
        )
        self.postprocessor_tab_index = self.workbench_tabs.addTab(
            self.postprocessor_placeholder,
            "ODB 后处理",
        )
        self.result_browser_tab_index = self.workbench_tabs.addTab(
            self.result_browser_placeholder,
            "结果浏览",
        )
        self.workbench_tabs.currentChanged.connect(self._tool_page_changed)
        self._add_postprocessing_menu()

    @staticmethod
    def _loading_placeholder(text: str) -> QtWidgets.QWidget:
        placeholder = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(placeholder)
        label = QtWidgets.QLabel(text)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("emptyState")
        layout.addWidget(label)
        return placeholder

    def _replace_tool_tab(
        self,
        index: int,
        widget: QtWidgets.QWidget,
        title: str,
    ) -> None:
        current_index = self.workbench_tabs.currentIndex()
        self.workbench_tabs.removeTab(index)
        self.workbench_tabs.insertTab(index, widget, title)
        if current_index == index:
            self.workbench_tabs.setCurrentIndex(index)

    def _ensure_postprocessor_page(self):
        if self.postprocessor_page is not None:
            return self.postprocessor_page
        page = PostProcessorPage(self)
        self.postprocessor_page = page
        page.resultBrowserRequested.connect(self.show_result_browser)
        self._replace_tool_tab(self.postprocessor_tab_index, page, "ODB 后处理")
        return page

    def _ensure_result_browser_page(self):
        if self.result_browser_page is not None:
            return self.result_browser_page
        postprocessor_page = self._ensure_postprocessor_page()
        page = ResultBrowserPage(
            postprocessor_page.current_result_root(),
            coordinator=postprocessor_page.controller.result_index_coordinator,
            parent=self,
        )
        self.result_browser_page = page
        postprocessor_page.resultRootChanged.connect(page.set_result_root)
        self._replace_tool_tab(self.result_browser_tab_index, page, "结果浏览")
        return page

    def _add_postprocessing_menu(self) -> None:
        menu = self.workbench_menu_bar.addMenu("后处理(&P)")
        self.open_current_job_postprocessor_action = menu.addAction(
            "在 ODB 后处理中打开当前作业",
            self.open_current_job_in_postprocessor,
        )
        menu.addAction("打开 ODB 后处理", self.show_postprocessor)
        menu.addAction("打开结果浏览", self.show_result_browser)

    def show_postprocessor(self, source: str | Path | None = None) -> None:
        page = self._ensure_postprocessor_page()
        if source is not None:
            page.set_source_path(source)
        self.workbench_tabs.setCurrentWidget(page)

    def open_current_job_in_postprocessor(self) -> None:
        run = self.run_records.get(self.selected_job_key())
        candidate = odb_candidate_for_job(
            run,
            fallback_work_dir=self.current_work_dir,
            fallback_job_name=self.current_job_name,
        )
        if candidate is None:
            QtWidgets.QMessageBox.information(
                self,
                "ODB 后处理",
                "当前没有可用的作业目录。请先选择或提交一个作业。",
            )
            return
        self.show_postprocessor(candidate)

    def show_result_browser(self, result_root: Path | None = None) -> None:
        page = self._ensure_result_browser_page()
        if result_root is not None:
            page.set_result_root(Path(result_root))
        self.workbench_tabs.setCurrentWidget(page)

    def _tool_page_changed(self, index: int) -> None:
        tool_page = index in {
            self.postprocessor_tab_index,
            self.result_browser_tab_index,
        }
        self.properties_panel.setVisible(not tool_page)
        self.project_explorer.setVisible(not tool_page)
        self.log_dock.setVisible(not tool_page)
        if not tool_page or self._loading_tool_page:
            return
        self._loading_tool_page = True
        try:
            if index == self.postprocessor_tab_index:
                self._ensure_postprocessor_page()
            elif index == self.result_browser_tab_index:
                self._ensure_result_browser_page()
        finally:
            self._loading_tool_page = False

    def closeEvent(self, event) -> None:
        if self.result_browser_page is not None and not self.result_browser_page.shutdown():
            event.ignore()
            return
        if self.postprocessor_page is not None and not self.postprocessor_page.shutdown():
            event.ignore()
            return
        super().closeEvent(event)
