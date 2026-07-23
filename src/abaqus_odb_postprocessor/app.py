"""Two-stage ODB discovery/selection before the expensive Abaqus scan."""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import __version__
from . import batch_window as _base
from .cache import abaqus_cache_version
from .config import load_defaults
from .naming import natural_sort_key
from .paths import scan_cache_dir
from .runner import (
    MultiProcessController,
    ProcessController,
    UpgradeBatchController,
    check_odb_compatibility,
    scan_folder,
    upgrade_odb_files,
    upgrade_target_path,
)
from .ui_style import apply_application_style


STATUS_PRESENTATION = {
    "unknown": ("未检测", "#64748b"),
    "valid": ("可直接读取", "#15803d"),
    "upgrade_required": ("需要升级", "#b45309"),
    "upgraded": ("已升级，可读取", "#15803d"),
    "newer_release": ("版本高于本机", "#b91c1c"),
    "invalid": ("无效或损坏", "#b91c1c"),
    "missing": ("文件不存在", "#b91c1c"),
    "empty": ("空文件", "#b91c1c"),
    "target_exists": ("目标文件已存在", "#b91c1c"),
    "upgrade_failed": ("升级失败", "#b91c1c"),
    "cancelled": ("已取消", "#64748b"),
}


def discover_odb_paths(folder: Path) -> list[Path]:
    """Discover ODB files recursively while excluding generated result trees."""

    excluded_directories = {
        "AbaqusODBPostProcessor_Results",
        "_AbaqusODBPostProcessor_Results",
        ".git",
        ".venv",
    }
    paths = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".odb":
            continue
        stem = path.stem.casefold()
        if stem.endswith("-old") or "-old-" in stem or "-upgrading-" in stem:
            continue
        relative_parts = path.relative_to(folder).parts[:-1]
        if any(part in excluded_directories for part in relative_parts):
            continue
        paths.append(path)
    return sorted(
        paths,
        key=lambda path: natural_sort_key(str(path.relative_to(folder))),
    )


class OdbFileList(QTreeWidget):
    """Multi-column ODB list with QListWidget-compatible test helpers."""

    def item(self, index: int) -> QTreeWidgetItem:
        return self.topLevelItem(index)

    def count(self) -> int:
        return self.topLevelItemCount()


class OdbSelectionDialog(QDialog):
    def __init__(
        self,
        folder: Path,
        paths: list[Path],
        parent=None,
        abaqus_command: str | None = None,
        local_release: str | None = None,
        force_rescan: bool = False,
        parallel_workers: int = 1,
    ) -> None:
        super().__init__(parent)
        self.folder = folder.resolve()
        defaults = getattr(parent, "defaults", None) or load_defaults()
        self.abaqus_command = abaqus_command or str(defaults["abaqus_command"])
        self.local_release = local_release or str(
            defaults.get("local_abaqus_release", "2025")
        )
        self.force_rescan = bool(force_rescan)
        self.parallel_workers = max(1, min(int(parallel_workers), 4))
        self.compatibility_cache_version = abaqus_cache_version(
            {
                "local_abaqus_release": self.local_release,
                "abaqus_command": self.abaqus_command,
            }
        )
        self.compatibility_worker = None
        self.compatibility_controller: (
            ProcessController | UpgradeBatchController | None
        ) = None
        self.compatibility_operation = ""
        self.pending_accept = False
        self.setWindowTitle("选择需要读取的 ODB")
        self.resize(1040, 720)
        self.setMinimumSize(820, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)

        folder_label = QLabel(f"文件夹：{folder}")
        folder_label.setProperty("role", "title")
        folder_label.setWordWrap(True)
        layout.addWidget(folder_label)
        intro = QLabel(
            f"已发现 {len(paths)} 个 ODB。选择后先检查能否由 Abaqus "
            f"{self.local_release} 读取；旧版文件可在此生成升级副本。"
        )
        intro.setProperty("role", "hint")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按文件名筛选…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)

        self.list_widget = OdbFileList()
        self.list_widget.setColumnCount(5)
        self.list_widget.setHeaderLabels(
            ["ODB 文件", "大小", "修改时间", "兼容性", "说明"]
        )
        self.list_widget.setRootIsDecorated(False)
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        header = self.list_widget.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.list_widget.setColumnWidth(0, 340)
        for path in paths:
            stat = path.stat()
            size_mb = stat.st_size / (1024.0 * 1024.0)
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            try:
                display_path = str(path.resolve().relative_to(self.folder))
            except ValueError:
                display_path = path.name
            item = QTreeWidgetItem(
                [display_path, f"{size_mb:,.1f} MB", modified, "未检测", ""]
            )
            item.setData(0, Qt.UserRole, str(path.resolve()))
            item.setData(0, Qt.UserRole + 1, "unknown")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
            item.setForeground(3, QBrush(QColor("#64748b")))
            self.list_widget.addTopLevelItem(item)
        self.list_widget.itemChanged.connect(self._update_count)
        layout.addWidget(self.list_widget, 1)

        selection_row = QHBoxLayout()
        selection_row.setSpacing(8)
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

        compatibility_row = QHBoxLayout()
        compatibility_row.setSpacing(8)
        self.check_button = QPushButton("检测所选 ODB")
        self.check_button.clicked.connect(self._check_selected)
        self.upgrade_button = QPushButton(f"升级旧版 ODB 到 {self.local_release}")
        self.upgrade_button.clicked.connect(self._upgrade_selected)
        self.upgrade_button.setEnabled(False)
        self.compatibility_progress = QProgressBar()
        self.compatibility_progress.setRange(0, 1)
        self.compatibility_progress.setValue(0)
        self.compatibility_progress.setFormat("%v / %m")
        self.cancel_compatibility_button = QPushButton("取消")
        self.cancel_compatibility_button.setProperty("danger", True)
        self.cancel_compatibility_button.setEnabled(False)
        self.cancel_compatibility_button.clicked.connect(self._cancel_compatibility)
        compatibility_row.addWidget(self.check_button)
        compatibility_row.addWidget(self.upgrade_button)
        compatibility_row.addWidget(self.compatibility_progress, 1)
        compatibility_row.addWidget(self.cancel_compatibility_button)
        layout.addLayout(compatibility_row)

        self.compatibility_status = QLabel(
            "尚未检测。点击“开始读取”时也会自动检测。"
        )
        self.compatibility_status.setProperty("role", "hint")
        self.compatibility_status.setWordWrap(True)
        layout.addWidget(self.compatibility_status)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("开始读取")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        self.dialog_buttons = buttons
        layout.addWidget(buttons)
        self._update_count()

    def _items(self):
        for index in range(self.list_widget.count()):
            yield self.list_widget.item(index)

    def _set_all(self, state: Qt.CheckState) -> None:
        for item in self._items():
            if not item.isHidden():
                item.setCheckState(0, state)

    def _invert(self) -> None:
        for item in self._items():
            if not item.isHidden():
                item.setCheckState(
                    0,
                    Qt.Unchecked
                    if item.checkState(0) == Qt.Checked
                    else Qt.Checked,
                )

    def _apply_filter(self, text: str) -> None:
        token = text.strip().casefold()
        for item in self._items():
            path = str(item.data(0, Qt.UserRole))
            item.setHidden(bool(token and token not in path.casefold()))

    def selected_paths(self) -> list[Path]:
        return [
            Path(str(item.data(0, Qt.UserRole)))
            for item in self._items()
            if item.checkState(0) == Qt.Checked
        ]

    def _update_count(self, *_args) -> None:
        selected = [
            item for item in self._items() if item.checkState(0) == Qt.Checked
        ]
        self.count_label.setText(
            f"已选择 {len(selected)} / {self.list_widget.count()}"
        )
        self.upgrade_button.setEnabled(
            any(self._item_status(item) == "upgrade_required" for item in selected)
            and self.compatibility_worker is None
        )

    @staticmethod
    def _item_status(item: QTreeWidgetItem) -> str:
        return str(item.data(0, Qt.UserRole + 1) or "unknown")

    def _set_item_status(
        self,
        item: QTreeWidgetItem,
        status: str,
        message: str = "",
    ) -> None:
        label, color = STATUS_PRESENTATION.get(
            status, (status or "未知", "#64748b")
        )
        item.setData(0, Qt.UserRole + 1, status)
        item.setText(3, label)
        item.setText(4, message)
        item.setForeground(3, QBrush(QColor(color)))
        item.setToolTip(3, message)

    def _set_compatibility_busy(self, busy: bool) -> None:
        self.search_edit.setEnabled(not busy)
        self.list_widget.setEnabled(not busy)
        self.check_button.setEnabled(not busy)
        self.cancel_compatibility_button.setEnabled(busy)
        self.dialog_buttons.setEnabled(not busy)
        if busy:
            self.upgrade_button.setEnabled(False)
        else:
            self._update_count()

    def _compatibility_thread_finished(self) -> None:
        self.compatibility_worker = None
        self.compatibility_operation = ""
        self._update_count()

    def _compatibility_log(self, text: str) -> None:
        if text.startswith("COMPAT_CACHE_HIT|"):
            _, name, fingerprint = text.split("|", 2)
            self.compatibility_progress.setValue(
                min(
                    self.compatibility_progress.value() + 1,
                    self.compatibility_progress.maximum(),
                )
            )
            self.compatibility_status.setText(
                f"命中兼容性缓存：{name}（{fingerprint}）"
            )
        elif text.startswith("COMPAT_CACHE_MISS|"):
            parts = text.split("|", 2)
            self.compatibility_status.setText(
                f"等待 Abaqus 检测：{parts[1]}"
            )
        elif text.startswith("ODB_CHECK|"):
            _, index, total, status, name = text.split("|", 4)
            self.compatibility_progress.setRange(0, max(int(total), 1))
            self.compatibility_progress.setValue(int(index))
            label = STATUS_PRESENTATION.get(status, (status, ""))[0]
            self.compatibility_status.setText(
                f"正在检测 {index}/{total}：{name}（{label}）"
            )
        elif text.startswith("ODB_UPGRADE_START|"):
            _, index, total, name = text.split("|", 3)
            self.compatibility_progress.setRange(0, max(int(total), 1))
            self.compatibility_progress.setValue(max(int(index) - 1, 0))
            self.compatibility_status.setText(
                f"正在升级 {index}/{total}：{name}"
            )
        elif text.startswith("ODB_UPGRADE_DONE|"):
            _, index, total, status, name = text.split("|", 4)
            self.compatibility_progress.setValue(int(index))
            label = STATUS_PRESENTATION.get(status, (status, ""))[0]
            self.compatibility_status.setText(
                f"升级进度 {index}/{total}：{name}（{label}）"
            )

    def _check_selected(self, _checked: bool = False, accept_when_ready: bool = False) -> None:
        paths = self.selected_paths()
        if not paths:
            QMessageBox.warning(self, "尚未选择 ODB", "请至少勾选一个 ODB 文件。")
            return
        self.pending_accept = accept_when_ready
        self.compatibility_operation = "check"
        self.compatibility_controller = ProcessController()
        self.compatibility_progress.setRange(0, len(paths))
        self.compatibility_progress.setValue(0)
        self.compatibility_status.setText(
            f"正在使用 Abaqus {self.local_release} 检测 {len(paths)} 个 ODB…"
        )
        self._set_compatibility_busy(True)
        cache = scan_cache_dir(self.folder)
        self.compatibility_worker = _base.FunctionThread(
            lambda log: check_odb_compatibility(
                self.abaqus_command,
                paths,
                cache,
                log,
                self.compatibility_controller,
                force_rescan=self.force_rescan,
                abaqus_version=self.compatibility_cache_version,
            )
        )
        self.compatibility_worker.message.connect(self._compatibility_log)
        self.compatibility_worker.completed.connect(self._check_finished)
        self.compatibility_worker.failed.connect(self._compatibility_failed)
        self.compatibility_worker.finished.connect(
            self._compatibility_thread_finished
        )
        self.compatibility_worker.start()

    def _check_finished(self, payload: dict[str, Any]) -> None:
        results = {
            str(Path(item["path"]).resolve()): item
            for item in payload.get("results", [])
        }
        for item in self._items():
            path = str(Path(str(item.data(0, Qt.UserRole))).resolve())
            result = results.get(path)
            if result is not None:
                self._set_item_status(
                    item,
                    str(result.get("status", "invalid")),
                    str(result.get("message", "")),
                )
        self._set_compatibility_busy(False)
        selected = [
            item for item in self._items() if item.checkState(0) == Qt.Checked
        ]
        valid_count = sum(self._item_status(item) == "valid" for item in selected)
        upgrade_count = sum(
            self._item_status(item) == "upgrade_required" for item in selected
        )
        invalid_count = len(selected) - valid_count - upgrade_count
        cache_hits = int(payload.get("cache_hits", 0))
        self.compatibility_status.setText(
            f"检测完成：可读取 {valid_count}，需要升级 {upgrade_count}，"
            f"不可用 {invalid_count}；缓存命中 {cache_hits}/{len(selected)}。"
        )
        if self.pending_accept:
            self.pending_accept = False
            if valid_count == len(selected):
                self.accept()

    def _compatibility_failed(self, details: str) -> None:
        self.pending_accept = False
        self._set_compatibility_busy(False)
        if "Process cancelled by user" in details:
            self.compatibility_status.setText("ODB 检测或升级已取消。")
            return
        self.compatibility_status.setText("ODB 检测或升级启动失败。")
        QMessageBox.critical(
            self,
            "ODB 兼容性操作失败",
            details.splitlines()[-1] if details.splitlines() else details,
        )

    def _cancel_compatibility(self) -> None:
        if self.compatibility_controller is not None:
            if self.compatibility_operation == "upgrade":
                self.compatibility_status.setText(
                    "已取消后续升级；正在等待已启动的 ODB 安全完成…"
                )
            else:
                self.compatibility_status.setText("正在取消，请稍候…")
            self.compatibility_controller.cancel()
            self.cancel_compatibility_button.setEnabled(False)

    def _upgrade_selected(self) -> None:
        items = [
            item
            for item in self._items()
            if item.checkState(0) == Qt.Checked
            and self._item_status(item) == "upgrade_required"
        ]
        if not items:
            QMessageBox.information(
                self, "没有需要升级的 ODB", "所选文件中没有检测到旧版 ODB。"
            )
            return
        answer = QMessageBox.question(
            self,
            "升级旧版 ODB",
            f"将使用 Abaqus {self.local_release} 升级 {len(items)} 个 ODB。\n\n"
            "新版 ODB 将保持原文件名；旧版 ODB 改名为“原名-old.odb”保留。\n"
            "程序会先生成并验证新版，成功后才交换文件名。是否继续？",
        )
        if answer != QMessageBox.Yes:
            return
        tasks = []
        for item in items:
            source = Path(str(item.data(0, Qt.UserRole)))
            tasks.append(
                (source, upgrade_target_path(source, self.local_release))
            )
        self.compatibility_operation = "upgrade"
        self.compatibility_controller = UpgradeBatchController()
        self.compatibility_progress.setRange(0, len(tasks))
        self.compatibility_progress.setValue(0)
        self._set_compatibility_busy(True)
        cache = scan_cache_dir(self.folder)
        self.compatibility_worker = _base.FunctionThread(
            lambda log: upgrade_odb_files(
                self.abaqus_command,
                tasks,
                cache,
                log,
                self.compatibility_controller,
                release=self.local_release,
                abaqus_version=self.compatibility_cache_version,
                parallel_workers=self.parallel_workers,
            )
        )
        self.compatibility_worker.message.connect(self._compatibility_log)
        self.compatibility_worker.completed.connect(self._upgrade_finished)
        self.compatibility_worker.failed.connect(self._compatibility_failed)
        self.compatibility_worker.finished.connect(
            self._compatibility_thread_finished
        )
        self.compatibility_worker.start()

    def _upgrade_finished(self, payload: dict[str, Any]) -> None:
        items_by_path = {
            str(Path(str(item.data(0, Qt.UserRole))).resolve()): item
            for item in self._items()
        }
        success_count = 0
        failure_count = 0
        cancelled_count = 0
        for result in payload.get("results", []):
            source_path = str(Path(result["source_path"]).resolve())
            item = items_by_path.get(source_path)
            if item is None:
                continue
            status = str(result.get("status", "upgrade_failed"))
            if status == "upgraded":
                upgraded_path = Path(result["upgraded_path"]).resolve()
                backup_path = Path(result["backup_path"]).resolve()
                item.setData(0, Qt.UserRole + 2, str(backup_path))
                item.setData(0, Qt.UserRole, str(upgraded_path))
                try:
                    item.setText(0, str(upgraded_path.relative_to(self.folder)))
                except ValueError:
                    item.setText(0, upgraded_path.name)
                if upgraded_path.exists():
                    stat = upgraded_path.stat()
                    item.setText(1, f"{stat.st_size / (1024.0 * 1024.0):,.1f} MB")
                    item.setText(
                        2,
                        datetime.fromtimestamp(stat.st_mtime).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                    )
                self._set_item_status(
                    item,
                    "valid",
                    f"已升级到 Abaqus {self.local_release}；"
                    f"旧版保留为 {backup_path.name}"
                    + (
                        "；缓存失效失败，请强制重新扫描"
                        if result.get("cache_warning")
                        else ""
                    ),
                )
                success_count += 1
            elif status == "cancelled":
                self._set_item_status(
                    item,
                    "upgrade_required",
                    "本批升级已取消，尚未处理；可再次提交升级。",
                )
                cancelled_count += 1
            else:
                self._set_item_status(
                    item, status, str(result.get("message", "升级失败"))
                )
                failure_count += 1
        self._set_compatibility_busy(False)
        self.compatibility_status.setText(
            f"升级完成：成功 {success_count}，失败 {failure_count}，"
            f"已取消 {cancelled_count}。"
        )

    def _accept_selection(self) -> None:
        if not self.selected_paths():
            QMessageBox.warning(self, "尚未选择 ODB", "请至少勾选一个 ODB 文件。")
            return
        selected = [
            item for item in self._items() if item.checkState(0) == Qt.Checked
        ]
        if any(self._item_status(item) == "unknown" for item in selected):
            self._check_selected(accept_when_ready=True)
            return
        upgrade_items = [
            item for item in selected if self._item_status(item) == "upgrade_required"
        ]
        if upgrade_items:
            QMessageBox.information(
                self,
                "存在需要升级的 ODB",
                "请先点击“升级旧版 ODB”，升级完成后再开始读取。",
            )
            return
        unusable = [
            item
            for item in selected
            if self._item_status(item) not in ("valid", "upgraded")
        ]
        if unusable:
            QMessageBox.warning(
                self,
                "存在不可读取的 ODB",
                "请取消勾选标记为无效、损坏或版本高于本机的 ODB。",
            )
            return
        self.accept()

    def reject(self) -> None:
        if (
            self.compatibility_worker is not None
            and self.compatibility_worker.isRunning()
        ):
            if self.compatibility_operation == "upgrade":
                self.compatibility_status.setText(
                    "ODB 升级尚未结束；请等待已启动任务安全完成。"
                )
                return
            self._cancel_compatibility()
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if (
            self.compatibility_worker is not None
            and self.compatibility_worker.isRunning()
        ):
            if self.compatibility_operation == "upgrade":
                self.compatibility_status.setText(
                    "ODB 升级尚未结束；请等待已启动任务安全完成。"
                )
                event.ignore()
                return
            self._cancel_compatibility()
            event.ignore()
            return
        super().closeEvent(event)


class MainWindow(_base.MainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.folder_edit.textChanged.connect(self._update_window_title)
        self._update_window_title(self.folder_edit.text())
        self.scan_button.setText("发现并选择 ODB")

    def _update_window_title(self, folder_text: str = "") -> None:
        title = f"Abaqus ODB PostProcessor {__version__}"
        folder = Path(str(folder_text).strip())
        if folder.is_dir():
            title += f" — {folder.resolve()}"
        self.setWindowTitle(title)

    def _set_busy(self, busy: bool) -> None:
        super()._set_busy(busy)
        if not busy:
            self.scan_button.setText("发现并选择 ODB")

    def _scan(self) -> None:
        if self._has_group_work():
            QMessageBox.information(
                self,
                "后处理正在进行",
                "请等待当前运行队列结束后再扫描 ODB。",
            )
            return
        folder = Path(self.folder_edit.text().strip())
        if not folder.is_dir():
            QMessageBox.warning(self, "路径无效", "请选择存在的 ODB 文件夹。")
            return
        paths = discover_odb_paths(folder)
        if not paths:
            QMessageBox.information(self, "没有 ODB", "所选文件夹中没有找到 .odb 文件。")
            return
        dialog = OdbSelectionDialog(
            folder,
            paths,
            self,
            abaqus_command=self.defaults["abaqus_command"],
            local_release=str(self.defaults.get("local_abaqus_release", "2025")),
            force_rescan=bool(self.force_rescan_checkbox.isChecked()),
            parallel_workers=int(self.parallel_workers.value()),
        )
        if dialog.exec() != QDialog.Accepted:
            return
        selected_paths = dialog.selected_paths()
        force_rescan = self._consume_force_rescan()

        self.scan_controller = MultiProcessController()
        self.scan_active = True
        self.scan_started_at = time.monotonic()
        self.scan_total = len(selected_paths)
        self.scan_completed = 0
        self.scan_status.setText(
            f"已选择 {len(selected_paths)}/{len(paths)} 个 ODB，"
            f"正在启动 {min(self.parallel_workers.value(), len(selected_paths))} 个并行读取进程…"
        )
        self.scan_progress.setRange(0, max(len(selected_paths), 1))
        self.scan_progress.setValue(0)
        self.elapsed_timer.start(1000)
        cache = scan_cache_dir(folder)
        self._append_log(
            f"文件发现完成：共 {len(paths)} 个 ODB；本次选择 {len(selected_paths)} 个；"
            f"强制重扫={'是' if force_rescan else '否'}"
        )
        self._start_thread(
            lambda log: scan_folder(
                self.defaults["abaqus_command"],
                folder,
                cache,
                log,
                self.scan_controller,
                selected_paths,
                parallel_workers=int(self.parallel_workers.value()),
                force_rescan=force_rescan,
                abaqus_version=abaqus_cache_version(self.defaults),
            ),
            self._scan_finished,
        )

    def _append_log(self, text: str) -> None:
        if text.startswith("SCAN_START|"):
            _, index, total, name = text.split("|", 3)
            self.scan_status.setText(f"正在并行读取任务 {index}/{total}：{name}")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.log.appendPlainText(
                f"[{timestamp}] 启动读取任务 [{index}/{total}] {name}"
            )
            return
        super()._append_log(text)


FunctionThread = _base.FunctionThread
FRAME_MODE_LABELS = _base.FRAME_MODE_LABELS
ComparisonTree = _base.ComparisonTree
LegendRangeDialog = _base.LegendRangeDialog
safe_folder_name = _base.safe_folder_name


def main() -> int:
    application = QApplication(sys.argv)
    apply_application_style(application)
    window = MainWindow()
    window.showMaximized()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
