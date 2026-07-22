"""Two-stage ODB discovery/selection before the expensive Abaqus scan."""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from . import app_groups_v1_base as _base
from .config import project_root
from .runner import ProcessController, scan_folder


class OdbSelectionDialog(QDialog):
    def __init__(self, folder: Path, paths: list[Path], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择需要读取的 ODB")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"文件夹：{folder}"))
        layout.addWidget(QLabel(f"已发现 {len(paths)} 个 ODB。请勾选需要由 Abaqus 打开读取的文件。"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按文件名筛选…")
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)
        self.list_widget = QListWidget()
        for path in paths:
            stat = path.stat()
            size_mb = stat.st_size / (1024.0 * 1024.0)
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            item = QListWidgetItem(f"{path.name}    {size_mb:,.1f} MB    {modified}")
            item.setData(Qt.UserRole, str(path.resolve()))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.list_widget.addItem(item)
        self.list_widget.itemChanged.connect(self._update_count)
        layout.addWidget(self.list_widget, 1)

        selection_row = QHBoxLayout()
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all(Qt.Checked))
        select_none = QPushButton("全不选")
        select_none.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        invert = QPushButton("反选")
        invert.clicked.connect(self._invert)
        self.count_label = QLabel()
        selection_row.addWidget(select_all)
        selection_row.addWidget(select_none)
        selection_row.addWidget(invert)
        selection_row.addStretch(1)
        selection_row.addWidget(self.count_label)
        layout.addLayout(selection_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("开始读取")
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_count()

    def _items(self):
        for index in range(self.list_widget.count()):
            yield self.list_widget.item(index)

    def _set_all(self, state: Qt.CheckState) -> None:
        for item in self._items():
            if not item.isHidden():
                item.setCheckState(state)

    def _invert(self) -> None:
        for item in self._items():
            if not item.isHidden():
                item.setCheckState(
                    Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
                )

    def _apply_filter(self, text: str) -> None:
        token = text.strip().casefold()
        for item in self._items():
            path = str(item.data(Qt.UserRole))
            item.setHidden(bool(token and token not in Path(path).name.casefold()))

    def selected_paths(self) -> list[Path]:
        return [
            Path(str(item.data(Qt.UserRole)))
            for item in self._items()
            if item.checkState() == Qt.Checked
        ]

    def _update_count(self, *_args) -> None:
        self.count_label.setText(
            f"已选择 {len(self.selected_paths())} / {self.list_widget.count()}"
        )

    def _accept_selection(self) -> None:
        if not self.selected_paths():
            QMessageBox.warning(self, "尚未选择 ODB", "请至少勾选一个 ODB 文件。")
            return
        self.accept()


class MainWindow(_base.MainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Abaqus ODB PostProcessor 0.5")
        self.scan_button.setText("发现并选择 ODB")

    def _set_busy(self, busy: bool) -> None:
        super()._set_busy(busy)
        if not busy:
            self.scan_button.setText("发现并选择 ODB")

    def _scan(self) -> None:
        folder = Path(self.folder_edit.text().strip())
        if not folder.is_dir():
            QMessageBox.warning(self, "路径无效", "请选择存在的 ODB 文件夹。")
            return
        paths = sorted(
            (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".odb"),
            key=lambda path: path.name.casefold(),
        )
        if not paths:
            QMessageBox.information(self, "没有 ODB", "所选文件夹中没有找到 .odb 文件。")
            return
        dialog = OdbSelectionDialog(folder, paths, self)
        if dialog.exec() != QDialog.Accepted:
            return
        selected_paths = dialog.selected_paths()

        self.scan_controller = ProcessController()
        self.scan_active = True
        self.scan_started_at = time.monotonic()
        self.scan_total = len(selected_paths)
        self.scan_completed = 0
        self.scan_status.setText(
            f"已选择 {len(selected_paths)}/{len(paths)} 个 ODB，正在启动 Abaqus…"
        )
        self.scan_progress.setRange(0, max(len(selected_paths), 1))
        self.scan_progress.setValue(0)
        self.elapsed_timer.start(1000)
        cache = project_root() / "scan_cache"
        self._append_log(
            f"文件发现完成：共 {len(paths)} 个 ODB；本次选择 {len(selected_paths)} 个"
        )
        self._start_thread(
            lambda log: scan_folder(
                self.defaults["abaqus_command"],
                folder,
                cache,
                log,
                self.scan_controller,
                selected_paths,
            ),
            self._scan_finished,
        )

    def _append_log(self, text: str) -> None:
        if text.startswith("SCAN_START|"):
            _, index, total, name = text.split("|", 3)
            self.scan_status.setText(f"正在读取 {index}/{total}：{name}")
            self.scan_progress.setValue(max(int(index) - 1, 0))
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.log.appendPlainText(f"[{timestamp}] 正在读取 [{index}/{total}] {name}")
            return
        super()._append_log(text)


FunctionThread = _base.FunctionThread
FRAME_MODE_LABELS = _base.FRAME_MODE_LABELS
ComparisonTree = _base.ComparisonTree
LegendRangeDialog = _base.LegendRangeDialog
safe_folder_name = _base.safe_folder_name


def main() -> int:
    application = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
