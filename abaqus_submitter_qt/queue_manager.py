"""Qt queue manager dialog for the new frontend."""

from __future__ import annotations

import os
import json
from dataclasses import asdict, fields
from pathlib import Path

from .constants import (
    ACTIVE_STATUSES,
    JOBLIST_FILENAME,
    STATUS_CANCELED,
    STATUS_COMPLETED,
    STATUS_CONFIRMING,
    STATUS_DATACHECK_COMPLETED,
    STATUS_DATACHECK_FAILED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING_CONFIRM,
    STATUS_PENDING_RUN,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_TERMINATED,
    STATUS_TERMINATING,
    STATUS_UNKNOWN,
    STATUS_WAITING_DEPENDENCY,
    TERMINAL_STATUSES,
)
from .models import QueueItem

from .command import derive_job_name, derive_oldjob_name, inp_has_restart_keyword, validate_job_name
from .qt_compat import QtCore, QtWidgets, Signal, Slot, hang_probe_function
from .queue_scheduler import (
    effective_queue_item_work_dir,
    queue_item_conflict_key,
    queue_status_counts,
)

RESULT_EXTENSIONS = (".odb", ".sta", ".msg", ".dat", ".log")
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
JOBLIST_PATH = Path(__file__).resolve().parents[1] / JOBLIST_FILENAME
ADD_STATS_DETAIL_LIMIT = 10
TABLE_ROW_KEY_ROLE = QtCore.Qt.ItemDataRole.UserRole
CANDIDATE_COLUMNS = (
    "勾选",
    "序号",
    "作业名称",
    "INP 文件路径",
    "加入方式",
    "作业类型",
    "重启动依赖",
    "FOR 文件",
    "检查结果",
)
CANDIDATE_CHECK_COLUMN = 0
CANDIDATE_RESTART_COLUMN = 6
CANDIDATE_FORTRAN_COLUMN = 7
FORMAL_COLUMNS = (
    "序号",
    "作业名称",
    "INP 文件路径",
    "作业类型",
    "重启动依赖",
    "FOR 文件",
    "Core",
    "内存",
    "状态",
    "备注",
)


FORMAL_RESTART_COLUMN = 4
FORMAL_FORTRAN_COLUMN = 5
FORMAL_CORE_COLUMN = 6
FORMAL_MEMORY_COLUMN = 7


def atomic_write_json(path, payload):
    """Atomically write JSON so joblist.json is never half-written."""
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, path)


QUEUE_ITEM_FIELD_NAMES = {field.name for field in fields(QueueItem)}
_last_saved_joblist_payload: dict | None = None


def queue_item_to_json(item: QueueItem) -> dict:
    return asdict(item)


def queue_item_from_json(payload: object, *, restore_active_state: bool = True) -> QueueItem | None:
    if not isinstance(payload, dict):
        return None
    values = {key: value for key, value in payload.items() if key in QUEUE_ITEM_FIELD_NAMES}
    try:
        item = QueueItem(**values)
    except TypeError:
        return None
    if restore_active_state and item.status in ACTIVE_STATUSES:
        item.status = STATUS_UNKNOWN
        item.message = "程序重启后状态待确认"
        item.active_job_key = ""
        item.pids = []
        item.pid_create_times = {}
        item.rss_bytes = 0
    return item


def load_joblist_state() -> tuple[list[QueueItem], list[QueueItem], str]:
    try:
        with JOBLIST_PATH.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except FileNotFoundError:
        return [], [], ""
    except (OSError, json.JSONDecodeError) as exc:
        return [], [], str(exc)
    if not isinstance(payload, dict):
        return [], [], "joblist.json 格式不是对象"

    def read_items(key: str) -> list[QueueItem]:
        items = []
        for raw_item in payload.get(key, []) or []:
            item = queue_item_from_json(raw_item, restore_active_state=key == "queue_items")
            if item is not None:
                items.append(item)
        return items

    return read_items("candidates"), read_items("queue_items"), ""


def save_joblist_state(candidates: list[QueueItem], queue_items: list[QueueItem]) -> None:
    global _last_saved_joblist_payload
    payload = {
        "candidates": [queue_item_to_json(item) for item in candidates],
        "queue_items": [queue_item_to_json(item) for item in queue_items],
    }
    if payload == _last_saved_joblist_payload:
        return
    atomic_write_json(str(JOBLIST_PATH), payload)
    _last_saved_joblist_payload = payload


class FolderScanWorker(QtCore.QObject):
    """Discover INP files outside the UI thread."""

    finished = Signal(list)
    failed = Signal(str)
    done = Signal()

    def __init__(self, folder: str, recursive: bool):
        super().__init__()
        self.folder = folder
        self.recursive = recursive

    @Slot()
    def run(self) -> None:
        try:
            root = Path(self.folder)
            if not root.exists():
                raise FileNotFoundError(self.folder)
            if not root.is_dir():
                raise NotADirectoryError(self.folder)
            pattern = "**/*.inp" if self.recursive else "*.inp"
            paths = [str(path) for path in sorted(root.glob(pattern)) if path.is_file()]
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(paths)
        finally:
            self.done.emit()


class NoHighlightCheckBoxDelegate(QtWidgets.QStyledItemDelegate):
    def paint(self, painter, option, index):  # noqa: ANN001
        view_option = QtWidgets.QStyleOptionViewItem(option)
        view_option.state &= ~QtWidgets.QStyle.StateFlag.State_Selected
        view_option.state &= ~QtWidgets.QStyle.StateFlag.State_HasFocus
        view_option.state &= ~QtWidgets.QStyle.StateFlag.State_MouseOver
        super().paint(painter, view_option, index)


class QueueManagerDialog(QtWidgets.QDialog):
    """Manage candidate INP files and the formal run queue."""

    terminateRequested = Signal(list)
    scanExternalRequested = Signal(str)

    def __init__(
        self,
        parent,
        queue_items: list[QueueItem],
        current_settings,
        current_inp="",
        *,
        initial_candidates: list[QueueItem] | None = None,
        joblist_save_callback=None,
    ):
        super().__init__(parent)
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self.setWindowTitle("作业队列管理")
        self.resize(1280, 752)
        self.queue_items = queue_items
        self.current_settings = current_settings
        self.current_inp = current_inp
        self.candidates = initial_candidates if initial_candidates is not None else []
        self.joblist_save_callback = joblist_save_callback
        self.saved_paths = self.load_saved_paths()
        self.last_oldjob_odb_dir = str(self.saved_paths.get("qt_oldjob_odb_dir", "") or "")
        self.external_scan_busy = False
        self.folder_scan_thread: QtCore.QThread | None = None
        self.folder_scan_worker: FolderScanWorker | None = None
        self.folder_scan_closing = False
        self.candidate_columns_initialized = False
        self.formal_columns_initialized = False

        self.build_ui()
        self.refresh_tables()

    def request_joblist_save(self) -> None:
        if self.joblist_save_callback is not None:
            self.joblist_save_callback()

    # ---------- UI ----------

    def build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        candidate_group = QtWidgets.QGroupBox("候选区")
        candidate_layout = QtWidgets.QVBoxLayout(candidate_group)
        candidate_layout.setContentsMargins(8, 8, 8, 8)
        candidate_layout.setSpacing(6)

        candidate_toolbar = QtWidgets.QHBoxLayout()
        candidate_toolbar.setSpacing(6)
        self.add_current_btn = self.make_button("加入当前 INP")
        self.add_files_btn = self.make_button("添加 INP 文件")
        self.scan_folder_btn = self.make_button("扫描文件夹")
        self.select_all_btn = self.make_button("全选")
        self.unselect_all_btn = self.make_button("取消全选")
        self.invert_btn = self.make_button("反选")
        self.remove_candidate_btn = self.make_button("移除选中候选项", "danger")
        self.confirm_btn = self.make_button("确认选中项加入队列", "primary")
        for button in (
            self.add_current_btn,
            self.add_files_btn,
            self.scan_folder_btn,
            self.select_all_btn,
            self.unselect_all_btn,
            self.invert_btn,
            self.remove_candidate_btn,
            self.confirm_btn,
        ):
            candidate_toolbar.addWidget(button)
        candidate_toolbar.addStretch(1)
        candidate_toolbar.addWidget(QtWidgets.QLabel("SSD"))
        self.ssd_dir_edit = QtWidgets.QLineEdit(self.saved_paths.get("qt_ssd_work_dir", ""))
        self.ssd_dir_edit.setPlaceholderText("固态工作目录")
        self.ssd_dir_edit.setFixedWidth(170)
        self.choose_ssd_btn = self.make_button("选择")
        candidate_toolbar.addWidget(self.ssd_dir_edit)
        candidate_toolbar.addWidget(self.choose_ssd_btn)
        candidate_toolbar.addWidget(QtWidgets.QLabel("ARC"))
        self.archive_dir_edit = QtWidgets.QLineEdit(self.saved_paths.get("qt_archive_dir", ""))
        self.archive_dir_edit.setPlaceholderText("结果存档目录")
        self.archive_dir_edit.setFixedWidth(170)
        self.choose_archive_btn = self.make_button("选择")
        candidate_toolbar.addWidget(self.archive_dir_edit)
        candidate_toolbar.addWidget(self.choose_archive_btn)
        candidate_layout.addLayout(candidate_toolbar)

        candidate_options = QtWidgets.QHBoxLayout()
        candidate_options.setSpacing(14)
        self.scan_subdirs_check = QtWidgets.QCheckBox("扫描子文件夹")
        self.skip_restart_check = QtWidgets.QCheckBox("跳过名称中包含 Restart 的文件")
        self.skip_existing_check = QtWidgets.QCheckBox("跳过已经存在结果文件的作业")
        self.candidate_summary_label = QtWidgets.QLabel("候选：0 | 已选 0 | 异常 0")
        self.candidate_summary_label.setObjectName("hint")
        candidate_options.addWidget(self.scan_subdirs_check)
        candidate_options.addWidget(self.skip_restart_check)
        candidate_options.addWidget(self.skip_existing_check)
        candidate_options.addStretch(1)
        candidate_options.addWidget(self.candidate_summary_label)
        candidate_layout.addLayout(candidate_options)

        self.candidate_table = QtWidgets.QTableWidget(0, len(CANDIDATE_COLUMNS))
        self.setup_table(self.candidate_table, CANDIDATE_COLUMNS)
        self.candidate_table.setItemDelegateForColumn(0, NoHighlightCheckBoxDelegate(self.candidate_table))
        self.candidate_table.cellChanged.connect(self.on_candidate_cell_changed)
        self.candidate_table.itemDoubleClicked.connect(self.on_candidate_item_double_clicked)
        candidate_layout.addWidget(self.candidate_table, 1)
        root.addWidget(candidate_group, 1)

        queue_group = QtWidgets.QGroupBox("正式队列")
        queue_layout = QtWidgets.QVBoxLayout(queue_group)
        queue_layout.setContentsMargins(8, 8, 8, 8)
        queue_layout.setSpacing(6)

        queue_toolbar = QtWidgets.QHBoxLayout()
        queue_toolbar.setSpacing(6)
        self.remove_queue_btn = self.make_button("取消选中的待运行作业")
        self.edit_queue_btn = self.make_button("编辑选中的待运行作业")
        self.terminate_queue_btn = self.make_button("终止选中的运行中作业", "danger")
        self.clear_finished_btn = self.make_button("清理已结束记录")
        queue_toolbar.addWidget(self.remove_queue_btn)
        queue_toolbar.addWidget(self.edit_queue_btn)
        queue_toolbar.addWidget(self.terminate_queue_btn)
        queue_toolbar.addWidget(self.clear_finished_btn)
        queue_toolbar.addSpacing(12)
        queue_toolbar.addWidget(QtWidgets.QLabel("工作目录："))
        self.work_dir_edit = QtWidgets.QLineEdit(self.default_work_dir())
        queue_toolbar.addWidget(self.work_dir_edit, 1)
        self.choose_work_dir_btn = self.make_button("选择")
        self.scan_external_btn = self.make_button("扫描", "primary")
        queue_toolbar.addWidget(self.choose_work_dir_btn)
        queue_toolbar.addWidget(self.scan_external_btn)
        self.summary_label = QtWidgets.QLabel("状态：队列为空")
        self.summary_label.setObjectName("hint")
        queue_toolbar.addWidget(self.summary_label)
        queue_layout.addLayout(queue_toolbar)

        self.queue_table = QtWidgets.QTableWidget(0, len(FORMAL_COLUMNS))
        self.setup_table(self.queue_table, FORMAL_COLUMNS)
        self.queue_table.itemDoubleClicked.connect(self.on_queue_item_double_clicked)
        self.queue_table.itemChanged.connect(self.on_queue_table_item_changed)
        queue_layout.addWidget(self.queue_table, 1)
        root.addWidget(queue_group, 1)

        self.add_current_btn.clicked.connect(self.add_current_inp)
        self.add_files_btn.clicked.connect(self.add_inp_files)
        self.scan_folder_btn.clicked.connect(self.scan_folder)
        self.select_all_btn.clicked.connect(lambda: self.set_candidate_selection(True))
        self.unselect_all_btn.clicked.connect(lambda: self.set_candidate_selection(False))
        self.invert_btn.clicked.connect(self.invert_candidate_selection)
        self.remove_candidate_btn.clicked.connect(self.remove_selected_candidates)
        self.confirm_btn.clicked.connect(self.confirm_candidates)
        self.choose_ssd_btn.clicked.connect(self.choose_ssd_dir)
        self.choose_archive_btn.clicked.connect(self.choose_archive_dir)
        self.remove_queue_btn.clicked.connect(self.cancel_selected_pending)
        self.edit_queue_btn.clicked.connect(self.edit_selected_pending)
        self.terminate_queue_btn.clicked.connect(self.terminate_selected_running)
        self.clear_finished_btn.clicked.connect(self.clear_finished)
        self.choose_work_dir_btn.clicked.connect(self.choose_work_dir)
        self.scan_external_btn.clicked.connect(self.request_external_scan)

        self.setStyleSheet(
            """
            QDialog {
                background: #eef3f8;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 12px;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d8e1ee;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 12px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                background: #ffffff;
            }
            QGroupBox QLabel,
            QGroupBox QCheckBox {
                background: #ffffff;
            }
            QLabel#hint {
                color: #64748b;
                font-weight: 400;
            }
            QPushButton {
                background: #dbe3ee;
                color: #111827;
                border: 0;
                border-radius: 8px;
                min-height: 30px;
                padding: 4px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #cbd5e1;
            }
            QPushButton#light {
                background: #dbe3ee;
                color: #111827;
            }
            QPushButton#light:hover {
                background: #cbd5e1;
            }
            QPushButton#primary {
                background: #2563eb;
                color: #ffffff;
            }
            QPushButton#primary:hover {
                background: #1d4ed8;
            }
            QPushButton#danger {
                background: #dc2626;
                color: #ffffff;
            }
            QPushButton#danger:hover {
                background: #b91c1c;
            }
            QLineEdit {
                background: #f8fafc;
                color: #111827;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                min-height: 30px;
                padding: 0 8px;
                selection-background-color: #bfdbfe;
            }
            QLineEdit:focus {
                border-color: #60a5fa;
                background: #ffffff;
            }
            QTableWidget {
                background: #ffffff;
                border: 1px solid #d8e1ee;
                border-radius: 6px;
                gridline-color: #e5e7eb;
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            QHeaderView::section {
                background: #f1f5f9;
                border: 0;
                border-right: 1px solid #e5e7eb;
                padding: 7px 6px;
                font-weight: 500;
            }
            QScrollBar:vertical, QScrollBar:horizontal {
                background: #eef2f7;
                border: 0;
                width: 12px;
                height: 12px;
            }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background: #9fb1c8;
                border-radius: 6px;
                min-height: 24px;
                min-width: 24px;
            }
            QScrollBar::add-line, QScrollBar::sub-line {
                width: 0;
                height: 0;
            }
            """
        )

    def make_button(self, text: str, variant: str = "light") -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setObjectName(variant)
        return button

    def setup_table(self, table: QtWidgets.QTableWidget, columns: tuple[str, ...]) -> None:
        table.setHorizontalHeaderLabels(columns)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(False)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)

    def apply_table_item_alignment(
        self,
        table_item: QtWidgets.QTableWidgetItem,
        column_name: str,
    ) -> None:
        """路径列左对齐，其他列居中显示。"""
        if column_name == "INP 文件路径":
            alignment = QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        else:
            alignment = QtCore.Qt.AlignmentFlag.AlignCenter

        table_item.setTextAlignment(alignment)

    @staticmethod
    def item_row_key(item: QueueItem) -> str:
        return item.item_id

    @staticmethod
    def table_row_key(table: QtWidgets.QTableWidget, row: int) -> str:
        table_item = table.item(row, 0)
        if table_item is None:
            return ""
        value = table_item.data(TABLE_ROW_KEY_ROLE)
        return str(value or "")

    def table_row_key_map(self, table: QtWidgets.QTableWidget) -> dict[str, int]:
        row_by_key: dict[str, int] = {}
        for row in range(table.rowCount()):
            row_key = self.table_row_key(table, row)
            if row_key:
                row_by_key[row_key] = row
        return row_by_key

    def selected_table_row_keys(self, table: QtWidgets.QTableWidget) -> set[str]:
        selected_keys = set()
        for index in table.selectedIndexes():
            row_key = self.table_row_key(table, index.row())
            if row_key:
                selected_keys.add(row_key)
        return selected_keys

    def current_table_row_key(self, table: QtWidgets.QTableWidget) -> str:
        row = table.currentRow()
        if row < 0:
            return ""
        return self.table_row_key(table, row)

    def resize_table_to_count(self, table: QtWidgets.QTableWidget, target_count: int) -> None:
        while table.rowCount() > target_count:
            table.removeRow(table.rowCount() - 1)
        while table.rowCount() < target_count:
            table.insertRow(table.rowCount())

    def update_table_cell(
        self,
        table: QtWidgets.QTableWidget,
        row: int,
        column: int,
        value: str,
        column_name: str,
        row_key: str,
        check_state: QtCore.Qt.CheckState | None = None,
    ) -> None:
        table_item = table.item(row, column)
        if table_item is None:
            table_item = QtWidgets.QTableWidgetItem()
            self.apply_table_item_alignment(table_item, column_name)
            table.setItem(row, column, table_item)

        table_item.setData(TABLE_ROW_KEY_ROLE, row_key)
        flags = table_item.flags()
        if table is self.queue_table and column in (FORMAL_CORE_COLUMN, FORMAL_MEMORY_COLUMN):
            flags |= QtCore.Qt.ItemFlag.ItemIsEditable
        else:
            flags &= ~QtCore.Qt.ItemFlag.ItemIsEditable
        table_item.setFlags(flags)

        if check_state is not None:
            flags = table_item.flags()
            flags |= QtCore.Qt.ItemFlag.ItemIsEnabled
            flags |= QtCore.Qt.ItemFlag.ItemIsUserCheckable
            if table is self.candidate_table and column == 0:
                flags &= ~QtCore.Qt.ItemFlag.ItemIsSelectable
            table_item.setFlags(flags)
            if table_item.checkState() != check_state:
                table_item.setCheckState(check_state)

        if table_item.text() != value:
            table_item.setText(value)

    def restore_table_view_state(
        self,
        table: QtWidgets.QTableWidget,
        selected_keys: set[str],
        current_key: str,
        scroll_value: int,
    ) -> None:
        table.clearSelection()
        row_by_key = self.table_row_key_map(table)

        selection_model = table.selectionModel()
        if selection_model is not None:
            for row_key in selected_keys:
                row = row_by_key.get(row_key)
                if row is None:
                    continue
                top_left = table.model().index(row, 0)
                bottom_right = table.model().index(row, table.columnCount() - 1)
                selection = QtCore.QItemSelection(top_left, bottom_right)
                selection_model.select(
                    selection,
                    QtCore.QItemSelectionModel.SelectionFlag.Select
                    | QtCore.QItemSelectionModel.SelectionFlag.Rows,
                )

        if current_key in row_by_key:
            table.setCurrentCell(row_by_key[current_key], 0)
        else:
            remaining_selected_rows = [
                row_by_key[row_key]
                for row_key in selected_keys
                if row_key in row_by_key
            ]
            if remaining_selected_rows:
                table.setCurrentCell(min(remaining_selected_rows), 0)

        scroll_bar = table.verticalScrollBar()
        scroll_bar.setValue(min(scroll_value, scroll_bar.maximum()))

    def sync_table_rows(
        self,
        table: QtWidgets.QTableWidget,
        items: list[QueueItem],
        columns: tuple[str, ...],
        row_value_builder,
        *,
        checkable_first_column: bool = False,
    ) -> None:
        selected_keys = self.selected_table_row_keys(table)
        current_key = self.current_table_row_key(table)
        scroll_value = table.verticalScrollBar().value()
        sorting_enabled = table.isSortingEnabled()

        table.blockSignals(True)
        table.setUpdatesEnabled(False)
        table.setSortingEnabled(False)
        try:
            self.resize_table_to_count(table, len(items))
            for row, item in enumerate(items):
                row_key = self.item_row_key(item)
                values = row_value_builder(item, row + 1)
                for column, value in enumerate(values):
                    check_state = None
                    if checkable_first_column and column == 0:
                        check_state = (
                            QtCore.Qt.CheckState.Checked
                            if item.selected
                            else QtCore.Qt.CheckState.Unchecked
                        )
                    self.update_table_cell(
                        table,
                        row,
                        column,
                        value,
                        columns[column],
                        row_key,
                        check_state,
                    )
        finally:
            table.setSortingEnabled(sorting_enabled)
            table.setUpdatesEnabled(True)
            table.blockSignals(False)

        self.restore_table_view_state(table, selected_keys, current_key, scroll_value)
        table.viewport().update()

    def load_saved_paths(self) -> dict:
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_saved_paths(self) -> None:
        data = self.load_saved_paths()
        data["qt_ssd_work_dir"] = self.ssd_dir_edit.text().strip()
        data["qt_archive_dir"] = self.archive_dir_edit.text().strip()
        data["qt_oldjob_odb_dir"] = self.last_oldjob_odb_dir
        try:
            atomic_write_json(str(CONFIG_PATH), data)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "保存配置失败", f"无法保存目录配置：\n{exc}")

    def choose_ssd_dir(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "选择固态工作目录",
            self.ssd_dir_edit.text().strip() or self.default_work_dir(),
        )
        if folder:
            self.ssd_dir_edit.setText(os.path.normpath(folder))
            self.save_saved_paths()
            self.revalidate_candidates()
            self.refresh_candidate_table()
            self.request_joblist_save()

    def choose_archive_dir(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "选择结果存档目录",
            self.archive_dir_edit.text().strip() or self.default_work_dir(),
        )
        if folder:
            self.archive_dir_edit.setText(os.path.normpath(folder))
            self.save_saved_paths()

    # ---------- Candidate creation ----------

    def add_candidate(
        self,
        path: str,
        source: str = "文件",
        *,
        refresh: bool = True,
        existing_paths: set[str] | None = None,
    ) -> bool:
        normalized = os.path.normpath(os.path.abspath(path))
        if not normalized.lower().endswith(".inp"):
            return False
        if self.skip_restart_check.isChecked() and "restart" in os.path.basename(normalized).lower():
            return False
        if self.skip_existing_check.isChecked() and self.has_result_files(normalized):
            return False
        if existing_paths is None:
            existing_paths = {os.path.normcase(item.inp_path) for item in self.candidates + self.queue_items}
        normalized_key = os.path.normcase(normalized)
        if normalized_key in existing_paths:
            return False

        item = QueueItem(
            inp_path=normalized,
            job_name=derive_job_name(normalized),
            source=source,
            status=STATUS_PENDING_CONFIRM,
            selected=True,
            valid=True,
            message="可加入",
            cores=0,
            memory=self.current_settings["memory"],
            fortran_path=self.current_settings["for_file"],
            oldjob_path=self.current_settings["oldjob_path"],
            interactive=self.current_settings["interactive"],
            datacheck_only=self.current_settings["datacheck"],
            complete_notify=self.current_settings["notify"],
            source_inp_path=normalized,
            calculation_root_dir=self.ssd_dir_edit.text().strip(),
            archive_dir=self.archive_dir_edit.text().strip(),
            archive_after_complete=bool(self.archive_dir_edit.text().strip()),
            cleanup_after_archive=True,
        )
        self.detect_restart(item)
        self.validate_candidate(item)
        self.candidates.append(item)
        existing_paths.add(normalized_key)
        if refresh:
            self.revalidate_candidates()
            self.refresh_candidate_table()
        return True

    @staticmethod
    def new_candidate_add_stats(scanned: int = 0) -> dict:
        return {
            "scanned": scanned,
            "added": 0,
            "duplicate": 0,
            "restart_name": 0,
            "existing_result": 0,
            "invalid": 0,
            "details": {
                "duplicate": [],
                "restart_name": [],
                "existing_result": [],
                "invalid": [],
            },
        }

    @staticmethod
    def add_skip_detail(stats: dict, reason: str, path: str) -> None:
        details = stats.get("details", {}).get(reason)
        if details is not None and len(details) < ADD_STATS_DETAIL_LIMIT:
            details.append(os.path.basename(path) or path)

    def candidate_skip_reason(self, path: str, existing_paths: set[str]) -> tuple[str, str]:
        normalized = os.path.normpath(os.path.abspath(path))
        if not normalized.lower().endswith(".inp"):
            return "invalid", normalized
        if self.skip_restart_check.isChecked() and "restart" in os.path.basename(normalized).lower():
            return "restart_name", normalized
        if self.skip_existing_check.isChecked() and self.has_result_files(normalized):
            return "existing_result", normalized
        if os.path.normcase(normalized) in existing_paths:
            return "duplicate", normalized
        return "", normalized

    def add_candidate_batch(self, paths: list[str], source: str) -> dict:
        stats = self.new_candidate_add_stats(len(paths))
        existing_paths = {os.path.normcase(item.inp_path) for item in self.candidates + self.queue_items}
        changed = False

        for path in paths:
            reason, normalized = self.candidate_skip_reason(path, existing_paths)

            if reason:
                stats[reason] += 1
                self.add_skip_detail(stats, reason, normalized)
                continue

            if self.add_candidate(
                normalized,
                source,
                refresh=False,
                existing_paths=existing_paths,
            ):
                changed = True
                stats["added"] += 1
            else:
                stats["invalid"] += 1
                self.add_skip_detail(stats, "invalid", normalized)

        if changed:
            self.revalidate_candidates()
            self.refresh_candidate_table()
            self.request_joblist_save()
        else:
            self.refresh_summaries()

        return stats

    def format_candidate_add_stats_message(self, stats: dict) -> str:
        skipped = (
            int(stats["duplicate"])
            + int(stats["restart_name"])
            + int(stats["existing_result"])
            + int(stats["invalid"])
        )

        if skipped == 0 and stats["added"] > 0:
            return f"已新增 {stats['added']} 个候选作业。"

        lines = [
            "候选作业添加完成",
            "",
            f"扫描到 .inp 文件：{stats['scanned']}",
            f"新增候选：{stats['added']}",
            f"重复跳过：{stats['duplicate']}",
            f"Restart 名称跳过：{stats['restart_name']}",
            f"已有结果跳过：{stats['existing_result']}",
            f"其他跳过：{stats['invalid']}",
        ]

        detail_labels = {
            "duplicate": "重复跳过",
            "restart_name": "Restart 名称跳过",
            "existing_result": "已有结果跳过",
            "invalid": "其他跳过",
        }
        detail_lines = []
        for reason, label in detail_labels.items():
            names = stats.get("details", {}).get(reason, [])
            if names:
                detail_lines.append(f"{label}：" + "，".join(names))
        if detail_lines:
            lines.extend(["", *detail_lines])

        return "\n".join(lines)

    def show_candidate_add_stats(self, stats: dict, *, force: bool = False) -> None:
        if not force and stats["scanned"] <= 0 and stats["added"] <= 0:
            return

        QtWidgets.QMessageBox.information(
            self,
            "候选作业添加完成",
            self.format_candidate_add_stats_message(stats),
        )

    def add_current_inp(self) -> None:
        if self.current_inp:
            stats = self.add_candidate_batch([self.current_inp], "当前 INP")
            self.show_candidate_add_stats(stats)

    def add_inp_files(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "添加 INP 文件",
            self.default_work_dir(),
            "Abaqus INP (*.inp);;所有文件 (*.*)",
        )
        if not paths:
            return
        stats = self.add_candidate_batch(list(paths), "文件")
        self.show_candidate_add_stats(stats)

    def scan_folder(self) -> None:
        if self.folder_scan_thread is not None:
            QtWidgets.QMessageBox.information(self, "扫描文件夹", "文件夹扫描正在进行，请稍候。")
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "扫描文件夹", self.default_work_dir())
        if not folder:
            return
        self.start_folder_scan(folder, self.scan_subdirs_check.isChecked())

    def start_folder_scan(self, folder: str, recursive: bool) -> None:
        if self.folder_scan_thread is not None:
            QtWidgets.QMessageBox.information(self, "扫描文件夹", "文件夹扫描正在进行，请稍候。")
            return

        self.folder_scan_closing = False
        thread = QtCore.QThread(QtWidgets.QApplication.instance())
        worker = FolderScanWorker(folder, recursive)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self.handle_folder_scan_finished)
        worker.failed.connect(self.handle_folder_scan_failed)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(self.handle_folder_scan_done)
        thread.finished.connect(thread.deleteLater)

        self.folder_scan_thread = thread
        self.folder_scan_worker = worker
        self.set_folder_scan_busy(True)
        thread.start()

    def set_folder_scan_busy(self, busy: bool) -> None:
        self.scan_folder_btn.setEnabled(not busy)
        self.scan_folder_btn.setText("扫描中..." if busy else "扫描文件夹")

    def handle_folder_scan_finished(self, paths: list[str]) -> None:
        if self.folder_scan_closing:
            return
        stats = self.add_candidate_batch(paths, "文件夹")
        self.show_candidate_add_stats(stats, force=True)

    def handle_folder_scan_failed(self, message: str) -> None:
        if self.folder_scan_closing:
            return
        QtWidgets.QMessageBox.warning(self, "扫描文件夹失败", f"扫描文件夹失败：\n{message}")

    def handle_folder_scan_done(self) -> None:
        self.folder_scan_thread = None
        self.folder_scan_worker = None
        if self.folder_scan_closing:
            return
        self.set_folder_scan_busy(False)

    def closeEvent(self, event) -> None:
        self.folder_scan_closing = True
        super().closeEvent(event)

    def has_result_files(self, inp_path: str) -> bool:
        base = Path(inp_path).with_suffix("")
        return any(base.with_suffix(ext).exists() for ext in RESULT_EXTENSIONS)

    def detect_restart(self, item: QueueItem) -> None:
        item.run_mode = "normal"
        item.job_type = "普通"
        if not os.path.isfile(item.inp_path):
            item.valid = False
            item.message = "INP 文件不可读"
            return
        if inp_has_restart_keyword(item.inp_path):
            item.run_mode = "restart"
            item.job_type = "重启动"
            if item.oldjob_path:
                item.oldjob_name = Path(item.oldjob_path).stem
                item.oldjob_dir = str(Path(item.oldjob_path).parent)

    def validate_candidate(self, item: QueueItem) -> None:
        if not os.path.isfile(item.inp_path):
            item.valid = False
            item.message = "INP 文件不存在"
            return
        ok, message = validate_job_name(item.job_name)
        if not ok:
            item.valid = False
            item.message = f"作业名不合法：{item.job_name or '空'}（{message}）"
            return
        if item.fortran_path and not os.path.isfile(item.fortran_path):
            item.valid = False
            item.message = "FOR 文件不存在"
            return
        pending_restart_dependency = False
        oldjob_name = item.oldjob_name or derive_oldjob_name(item.oldjob_path)
        if item.run_mode == "restart" and not oldjob_name:
            pending_restart_dependency = True
        if oldjob_name:
            ok, message = validate_job_name(oldjob_name)
            if not ok:
                item.valid = False
                item.message = f"Restart 依赖作业名不合法：{oldjob_name}（{message}）"
                return
            if oldjob_name == item.job_name:
                item.valid = False
                item.message = "当前作业名称不能与 Restart 依赖作业名相同。"
                return
        conflict_item = self.candidate_conflict_item(item)
        if conflict_item is not None:
            item.valid = False
            item.message = (
                f"作业冲突：同一计算目录中已存在同名作业 {item.job_name}，"
                f"冲突来源：{conflict_item.inp_path}"
            )
            return
        item.valid = True
        item.message = "确认加入前选择 Restart 依赖" if pending_restart_dependency else "可加入"

    def candidate_conflict_item(self, item: QueueItem) -> QueueItem | None:
        key = queue_item_conflict_key(item)
        if not key[0] or not key[1]:
            return None
        queue_item_ids = {entry.item_id for entry in self.queue_items}
        for other in self.candidates + self.queue_items:
            if other.item_id == item.item_id:
                continue
            if other.item_id in queue_item_ids and other.status in TERMINAL_STATUSES:
                continue
            if queue_item_conflict_key(other) == key:
                return other
        return None

    def revalidate_candidates(self) -> None:
        current_calculation_root_dir = self.ssd_dir_edit.text().strip()
        for item in self.candidates:
            item.calculation_root_dir = current_calculation_root_dir
            item.effective_work_dir = ""
            self.validate_candidate(item)

    # ---------- Candidate actions ----------

    def on_candidate_cell_changed(self, row: int, column: int) -> None:
        if column != 0 or row < 0 or row >= len(self.candidates):
            return
        table_item = self.candidate_table.item(row, 0)
        if table_item is None:
            return
        self.candidates[row].selected = table_item.checkState() == QtCore.Qt.CheckState.Checked
        self.refresh_summaries()
        self.request_joblist_save()

    def on_candidate_item_double_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        row = item.row()
        column = item.column()
        if row < 0 or row >= len(self.candidates):
            return

        if column == CANDIDATE_CHECK_COLUMN:
            self.candidates[row].selected = not self.candidates[row].selected
            self.refresh_candidate_table()
            self.request_joblist_save()
        elif column == CANDIDATE_RESTART_COLUMN:
            self.choose_candidate_restart_dependency(row)
        elif column == CANDIDATE_FORTRAN_COLUMN:
            self.choose_candidate_fortran_file(row)

    def select_oldjob_odb_file(self, initial_path: str = "") -> str:
        start_dir = ""
        if initial_path:
            initial = Path(initial_path)
            start_dir = str(initial.parent if initial.suffix else initial)
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 Restart 前置 ODB",
            start_dir or self.last_oldjob_odb_dir or self.default_work_dir(),
            "Abaqus ODB (*.odb);;所有文件 (*.*)",
        )
        if not path:
            return ""
        normalized = os.path.normpath(path)
        self.last_oldjob_odb_dir = os.path.normpath(str(Path(normalized).parent))
        self.save_saved_paths()
        return normalized

    def apply_oldjob_file_to_item(self, item: QueueItem, oldjob_path: str) -> None:
        if not oldjob_path:
            return
        path = Path(oldjob_path)
        item.oldjob_path = os.path.normpath(str(path))
        item.oldjob_name = path.stem
        item.oldjob_dir = os.path.normpath(str(path.parent))

    def select_fortran_file(self, initial_path: str = "") -> str:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 FOR 文件",
            initial_path or self.default_work_dir(),
            "Fortran (*.for *.f *.f90);;所有文件 (*.*)",
        )
        return os.path.normpath(path) if path else ""

    def choose_candidate_restart_dependency(self, row: int) -> None:
        item = self.candidates[row]
        if item.run_mode != "restart":
            return
        initial_path = item.oldjob_path or item.oldjob_dir
        oldjob_path = self.select_oldjob_odb_file(initial_path)
        if not oldjob_path:
            return
        self.apply_oldjob_file_to_item(item, oldjob_path)
        self.detect_restart(item)
        self.validate_candidate(item)
        self.refresh_candidate_table()
        self.request_joblist_save()

    def choose_candidate_fortran_file(self, row: int) -> None:
        item = self.candidates[row]
        path = self.select_fortran_file(item.fortran_path or self.default_work_dir())
        if not path:
            return
        item.fortran_path = os.path.normpath(path)
        self.validate_candidate(item)
        self.refresh_candidate_table()
        self.request_joblist_save()

    def set_candidate_selection(self, selected: bool) -> None:
        for item in self.candidates:
            if item.valid:
                item.selected = selected
        self.refresh_candidate_table()
        self.request_joblist_save()

    def invert_candidate_selection(self) -> None:
        for item in self.candidates:
            if item.valid:
                item.selected = not item.selected
        self.refresh_candidate_table()
        self.request_joblist_save()

    def remove_selected_candidates(self) -> None:
        selected_rows = {index.row() for index in self.candidate_table.selectedIndexes()}
        if selected_rows:
            self.candidates[:] = [item for index, item in enumerate(self.candidates) if index not in selected_rows]
        else:
            self.candidates[:] = [item for item in self.candidates if not item.selected]
        self.refresh_candidate_table()
        self.request_joblist_save()

    @staticmethod
    def format_count_distribution(values: list[str], empty_text: str = "默认") -> str:
        counts: dict[str, int] = {}
        for value in values:
            label = value or empty_text
            counts[label] = counts.get(label, 0) + 1

        if not counts:
            return "无"

        if len(counts) == 1:
            return next(iter(counts))

        return "，".join(f"{label} × {count}" for label, count in counts.items())

    def expected_oldjob_path_for_item(self, item: QueueItem) -> str:
        source_odb = str(Path(item.source_inp_path or item.inp_path).with_suffix(".odb"))
        if Path(source_odb).exists():
            return source_odb

        effective_work_dir = effective_queue_item_work_dir(item)
        if effective_work_dir:
            return str(Path(effective_work_dir) / f"{item.job_name}.odb")

        return source_odb

    def restart_dependency_source_items(
        self,
        current_item: QueueItem,
        preceding_candidates: list[QueueItem] | None = None,
    ) -> list[QueueItem]:
        sources: list[QueueItem] = []
        seen_ids: set[str] = set()
        for item in self.queue_items + list(preceding_candidates or []):
            if item.item_id == current_item.item_id or item.item_id in seen_ids:
                continue
            if not item.job_name:
                continue
            if item.run_mode == "restart" and item.status not in TERMINAL_STATUSES:
                continue
            sources.append(item)
            seen_ids.add(item.item_id)
        return sources

    def restart_dependency_resolved(
        self,
        item: QueueItem,
        preceding_candidates: list[QueueItem] | None = None,
    ) -> bool:
        if item.run_mode != "restart":
            return True

        oldjob_name = item.oldjob_name or derive_oldjob_name(item.oldjob_path)
        if not oldjob_name:
            return False

        for source_item in self.restart_dependency_source_items(item, preceding_candidates):
            if source_item.job_name.lower() == oldjob_name.lower():
                return True

        candidate_paths = [
            item.oldjob_path,
            str(Path(item.oldjob_dir) / f"{oldjob_name}.odb") if item.oldjob_dir else "",
        ]
        for raw_path in candidate_paths:
            if not raw_path:
                continue
            path = Path(raw_path)
            if path.suffix.lower() == ".odb" and path.stem.lower() == oldjob_name.lower() and path.is_file():
                return True
        return False

    def build_restart_dependency_options(
        self,
        current_item: QueueItem,
        preceding_candidates: list[QueueItem],
    ) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        for source_item in self.restart_dependency_source_items(current_item, preceding_candidates):
            oldjob_path = self.expected_oldjob_path_for_item(source_item)
            oldjob_dir = str(Path(oldjob_path).parent) if oldjob_path else ""
            label = f"{source_item.job_name} — {oldjob_dir or '队列前置作业'}"
            options.append(
                {
                    "label": label,
                    "oldjob_name": source_item.job_name,
                    "oldjob_path": oldjob_path,
                    "oldjob_dir": oldjob_dir,
                    "allow_missing": "1",
                }
            )
        return options

    def prompt_restart_dependency(
        self,
        item: QueueItem,
        preceding_candidates: list[QueueItem],
    ) -> bool:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("选择 Restart 前置作业")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel(f"当前 Restart 作业：\n{item.job_name}"))

        form = QtWidgets.QFormLayout()
        dependency_combo = QtWidgets.QComboBox()
        dependency_combo.addItem("手动选择 oldjob ODB 文件", None)
        for option in self.build_restart_dependency_options(item, preceding_candidates):
            dependency_combo.addItem(option["label"], option)

        oldjob_name_edit = QtWidgets.QLineEdit(item.oldjob_name or derive_oldjob_name(item.oldjob_path))
        oldjob_path_edit = QtWidgets.QLineEdit(item.oldjob_path)
        oldjob_path_edit.setReadOnly(True)
        choose_odb_btn = QtWidgets.QPushButton("选择 ODB 文件")
        file_row = QtWidgets.QHBoxLayout()
        file_row.addWidget(oldjob_path_edit, 1)
        file_row.addWidget(choose_odb_btn)

        form.addRow("请选择前置作业", dependency_combo)
        form.addRow("oldjob 名称", oldjob_name_edit)
        form.addRow("前置 ODB 文件", file_row)
        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(buttons)

        selected_result: dict[str, str] = {}

        def apply_combo_selection(index: int) -> None:
            data = dependency_combo.itemData(index)
            if not data:
                return
            oldjob_name_edit.setText(str(data.get("oldjob_name", "")))
            oldjob_path_edit.setText(str(data.get("oldjob_path", "")))

        def choose_oldjob_file() -> None:
            oldjob_path = self.select_oldjob_odb_file(oldjob_path_edit.text() or item.oldjob_dir)
            if oldjob_path:
                oldjob_path_edit.setText(oldjob_path)
                oldjob_name_edit.setText(Path(oldjob_path).stem)
                dependency_combo.setCurrentIndex(0)

        def accept_dialog() -> None:
            oldjob_path = oldjob_path_edit.text().strip()
            if oldjob_path:
                oldjob_name_edit.setText(Path(oldjob_path).stem)
            oldjob_name = oldjob_name_edit.text().strip()
            ok, message = validate_job_name(oldjob_name)
            if not ok:
                QtWidgets.QMessageBox.warning(dialog, "Restart 依赖无效", f"oldjob 名称不合法：{message}")
                return
            if oldjob_name.lower() == item.job_name.lower():
                QtWidgets.QMessageBox.warning(dialog, "Restart 依赖无效", "oldjob 不能与当前作业同名。")
                return

            if not oldjob_path:
                QtWidgets.QMessageBox.warning(dialog, "Restart 依赖无效", "请选择前置 ODB 文件。")
                return

            data = dependency_combo.itemData(dependency_combo.currentIndex())
            allow_missing = bool(data and str(data.get("allow_missing", "")) == "1")
            if Path(oldjob_path).stem.lower() != oldjob_name.lower():
                oldjob_path = str(Path(oldjob_path).with_name(f"{oldjob_name}.odb"))

            if not allow_missing and not Path(oldjob_path).is_file():
                QtWidgets.QMessageBox.warning(
                    dialog,
                    "Restart 依赖无效",
                    f"目录中未找到前置 ODB：\n{oldjob_path}",
                )
                return

            selected_result.update(
                {
                    "oldjob_name": oldjob_name,
                    "oldjob_dir": os.path.normpath(str(Path(oldjob_path).parent)),
                    "oldjob_path": os.path.normpath(oldjob_path),
                }
            )
            dialog.accept()

        dependency_combo.currentIndexChanged.connect(apply_combo_selection)
        choose_odb_btn.clicked.connect(choose_oldjob_file)
        buttons.accepted.connect(accept_dialog)
        buttons.rejected.connect(dialog.reject)

        if dependency_combo.count() > 1 and not oldjob_name_edit.text().strip():
            dependency_combo.setCurrentIndex(1)
            apply_combo_selection(1)

        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return False

        item.oldjob_name = selected_result["oldjob_name"]
        item.oldjob_dir = selected_result["oldjob_dir"]
        item.oldjob_path = selected_result["oldjob_path"]
        self.validate_candidate(item)
        return item.valid

    def ensure_restart_dependencies_for_candidates(self, selected_items: list[QueueItem]) -> bool:
        for index, item in enumerate(selected_items):
            if item.run_mode != "restart":
                continue
            preceding_candidates = [
                candidate
                for candidate in selected_items[:index]
                if candidate.run_mode != "restart"
            ]
            if self.restart_dependency_resolved(item, preceding_candidates):
                continue
            if not self.prompt_restart_dependency(item, preceding_candidates):
                QtWidgets.QMessageBox.information(
                    self,
                    "已取消",
                    "已取消加入队列，Restart 作业未选择有效前置作业。",
                )
                self.refresh_candidate_table()
                return False
        return True

    def build_confirm_candidates_summary(self, selected_items: list[QueueItem]) -> str:
        total_cores = sum(int(item.cores or 0) for item in selected_items)
        memory_summary = self.format_count_distribution([item.memory for item in selected_items])
        fortran_items = [item for item in selected_items if item.fortran_path]
        fortran_summary = self.format_count_distribution(
            [os.path.basename(item.fortran_path) for item in fortran_items],
            empty_text="无",
        )
        restart_items = [item for item in selected_items if item.run_mode == "restart"]

        lines = [
            "确认将以下候选作业加入正式队列？",
            "",
            f"作业数量：{len(selected_items)}",
            f"总核心数：{total_cores}",
            f"内存设置：{memory_summary}",
            f"FOR 文件：{len(fortran_items)} 个",
        ]

        if fortran_items:
            lines.append(f"FOR 分布：{fortran_summary}")

        lines.append(f"Restart 作业：{len(restart_items)} 个")

        if restart_items:
            lines.extend(["", "Restart 依赖："])
            for item in restart_items[:10]:
                dependency = item.oldjob_name or (Path(item.oldjob_path).stem if item.oldjob_path else "启动前选择")
                lines.append(f"- {item.job_name} 依赖 {dependency}")

            hidden_count = len(restart_items) - 10
            if hidden_count > 0:
                lines.append(f"……另有 {hidden_count} 条依赖关系")

        return "\n".join(lines)

    def confirm_selected_candidates_action(self, selected_items: list[QueueItem]) -> bool:
        result = QtWidgets.QMessageBox.question(
            self,
            "确认加入队列",
            self.build_confirm_candidates_summary(selected_items),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes,
        )
        return result == QtWidgets.QMessageBox.StandardButton.Yes

    @hang_probe_function("QueueManagerDialog.confirm_candidates")
    def confirm_candidates(self) -> None:
        self.revalidate_candidates()
        blocked_items = [item for item in self.candidates if item.selected and not item.valid]
        if blocked_items:
            lines = ["检测到冲突或无效候选，无法加入正式队列："]
            for item in blocked_items[:10]:
                lines.append(f"- {item.job_name}: {item.message}")
            hidden_count = len(blocked_items) - 10
            if hidden_count > 0:
                lines.append(f"……另有 {hidden_count} 个候选作业无法加入")
            self.refresh_candidate_table()
            QtWidgets.QMessageBox.warning(self, "无法加入队列", "\n".join(lines))
            return
        selected_items = [item for item in self.candidates if item.selected and item.valid]
        if not selected_items:
            QtWidgets.QMessageBox.warning(self, "没有可加入作业", "请先勾选有效的候选作业。")
            return
        if not self.ensure_restart_dependencies_for_candidates(selected_items):
            return
        self.revalidate_candidates()
        blocked_items = [item for item in selected_items if not item.valid]
        if blocked_items:
            lines = ["Restart 依赖设置后仍存在无效候选，无法加入正式队列："]
            for item in blocked_items[:10]:
                lines.append(f"- {item.job_name}: {item.message}")
            hidden_count = len(blocked_items) - 10
            if hidden_count > 0:
                lines.append(f"……另有 {hidden_count} 个候选作业无法加入")
            self.refresh_candidate_table()
            QtWidgets.QMessageBox.warning(self, "无法加入队列", "\n".join(lines))
            return
        if not self.confirm_selected_candidates_action(selected_items):
            return
        existing = {os.path.normcase(item.inp_path) for item in self.queue_items}
        added_ids = set()
        for item in selected_items:
            if os.path.normcase(item.inp_path) in existing:
                continue
            item.source_inp_path = item.source_inp_path or item.inp_path
            item.calculation_root_dir = self.ssd_dir_edit.text().strip()
            item.effective_work_dir = ""
            item.effective_work_dir = effective_queue_item_work_dir(item)
            item.archive_dir = self.archive_dir_edit.text().strip()
            item.archive_after_complete = bool(item.archive_dir)
            item.cleanup_after_archive = True
            item.status = STATUS_PENDING_RUN
            item.message = "待提交"
            item.source = item.source or "候选"
            self.queue_items.append(item)
            existing.add(os.path.normcase(item.inp_path))
            added_ids.add(item.item_id)
        self.candidates[:] = [item for item in self.candidates if item.item_id not in added_ids]
        self.refresh_tables()
        self.request_joblist_save()

    # ---------- Formal queue actions ----------

    def selected_queue_rows(self) -> list[int]:
        return sorted({index.row() for index in self.queue_table.selectedIndexes()})

    def queue_item_by_row_key(self, row_key: str) -> QueueItem | None:
        for item in self.queue_items:
            if item.item_id == row_key:
                return item
        return None

    def queue_item_is_editable(self, item: QueueItem) -> bool:
        return item.status in (STATUS_PENDING_RUN, STATUS_WAITING_DEPENDENCY)

    def apply_formal_item_edit_result(self, item: QueueItem) -> None:
        self.detect_restart(item)
        self.validate_candidate(item)
        if item.run_mode == "restart" and not self.restart_dependency_resolved(item):
            item.status = STATUS_WAITING_DEPENDENCY
            item.message = "未选择有效的 Restart 前置作业"
        else:
            item.status = STATUS_PENDING_RUN if item.valid else STATUS_FAILED
            item.message = "待提交" if item.valid else item.message

    def on_queue_item_double_clicked(self, table_item: QtWidgets.QTableWidgetItem) -> None:
        row_key = self.table_row_key(self.queue_table, table_item.row())
        item = self.queue_item_by_row_key(row_key)
        if item is None:
            return
        if not self.queue_item_is_editable(item):
            QtWidgets.QMessageBox.information(self, "编辑队列作业", "只能编辑待运行或等待前置的作业。")
            return

        column = table_item.column()
        if column == FORMAL_RESTART_COLUMN:
            oldjob_path = self.select_oldjob_odb_file(item.oldjob_path or item.oldjob_dir)
            if oldjob_path:
                self.apply_oldjob_file_to_item(item, oldjob_path)
                self.apply_formal_item_edit_result(item)
                self.refresh_queue_table()
                self.request_joblist_save()
            return
        if column == FORMAL_FORTRAN_COLUMN:
            path = self.select_fortran_file(item.fortran_path or self.default_work_dir())
            if path:
                item.fortran_path = path
                self.apply_formal_item_edit_result(item)
                self.refresh_queue_table()
                self.request_joblist_save()
            return
        if column in (FORMAL_CORE_COLUMN, FORMAL_MEMORY_COLUMN):
            self.queue_table.editItem(table_item)
            return

        self.edit_selected_pending()

    def on_queue_table_item_changed(self, table_item: QtWidgets.QTableWidgetItem) -> None:
        column = table_item.column()
        if column not in (FORMAL_CORE_COLUMN, FORMAL_MEMORY_COLUMN):
            return
        row_key = self.table_row_key(self.queue_table, table_item.row())
        item = self.queue_item_by_row_key(row_key)
        if item is None or not self.queue_item_is_editable(item):
            self.refresh_queue_table()
            return

        value = table_item.text().strip()
        if column == FORMAL_CORE_COLUMN:
            try:
                item.cores = max(0, min(999, int(value)))
            except ValueError:
                self.refresh_queue_table()
                return
        else:
            item.memory = value
        self.apply_formal_item_edit_result(item)
        self.refresh_queue_table()
        self.request_joblist_save()

    def cancel_selected_pending(self) -> None:
        blocked = False
        for row in self.selected_queue_rows():
            if row >= len(self.queue_items):
                continue
            item = self.queue_items[row]
            if item.status in (STATUS_PENDING_RUN, STATUS_WAITING_DEPENDENCY):
                item.status = STATUS_CANCELED
                item.message = "用户取消"
            elif item.status in (STATUS_RUNNING, STATUS_STARTING, STATUS_TERMINATING):
                blocked = True
        self.refresh_queue_table()
        self.request_joblist_save()
        if blocked:
            QtWidgets.QMessageBox.information(self, "不能取消运行中作业", "运行中作业请使用“终止选中的运行中作业”。")

    def edit_selected_pending(self) -> None:
        rows = self.selected_queue_rows()
        if len(rows) != 1:
            QtWidgets.QMessageBox.information(self, "编辑队列作业", "请选择一个待运行作业。")
            return
        item = self.queue_items[rows[0]]
        if item.status not in (STATUS_PENDING_RUN, STATUS_WAITING_DEPENDENCY):
            QtWidgets.QMessageBox.information(self, "编辑队列作业", "只能编辑待运行或等待前置的作业。")
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("编辑待运行作业")
        layout = QtWidgets.QFormLayout(dialog)
        core_spin = QtWidgets.QSpinBox()
        core_spin.setRange(0, 999)
        core_spin.setSpecialValueText("未设置")
        core_spin.setValue(item.cores)
        memory_edit = QtWidgets.QLineEdit(item.memory)
        oldjob_edit = QtWidgets.QLineEdit(item.oldjob_path)
        fortran_edit = QtWidgets.QLineEdit(item.fortran_path)
        layout.addRow("Core", core_spin)
        layout.addRow("Mem", memory_edit)
        layout.addRow("重启动 ODB", oldjob_edit)
        layout.addRow("FOR 文件", fortran_edit)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        item.cores = core_spin.value()
        item.memory = memory_edit.text().strip()
        item.oldjob_path = oldjob_edit.text().strip()
        item.fortran_path = fortran_edit.text().strip()
        self.detect_restart(item)
        self.validate_candidate(item)
        if item.run_mode == "restart" and not self.restart_dependency_resolved(item):
            item.status = STATUS_WAITING_DEPENDENCY
            item.message = "未选择有效的 Restart 前置作业"
        else:
            item.status = STATUS_PENDING_RUN if item.valid else STATUS_FAILED
            item.message = "待提交" if item.valid else item.message
        self.refresh_queue_table()
        self.request_joblist_save()

    def terminate_selected_running(self) -> None:
        item_ids = []
        for row in self.selected_queue_rows():
            if row >= len(self.queue_items):
                continue
            item = self.queue_items[row]
            if item.status in (STATUS_STARTING, STATUS_RUNNING, STATUS_TERMINATING):
                item.status = STATUS_TERMINATING
                item.message = "已发送终止请求"
                item_ids.append(item.item_id)
        if item_ids:
            self.terminateRequested.emit(item_ids)
        self.refresh_queue_table()
        self.request_joblist_save()

    def clear_finished(self) -> None:
        self.queue_items[:] = [item for item in self.queue_items if item.status not in TERMINAL_STATUSES]
        self.refresh_tables()
        self.request_joblist_save()

    def choose_work_dir(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "选择外部作业工作目录", self.default_work_dir())
        if folder:
            self.work_dir_edit.setText(os.path.normpath(folder))

    def set_external_scan_busy(
        self,
        busy: bool,
    ) -> None:
        """更新外部作业扫描状态。"""
        self.external_scan_busy = busy

        self.scan_external_btn.setEnabled(not busy)

        self.choose_work_dir_btn.setEnabled(not busy)

        self.work_dir_edit.setEnabled(not busy)

        if busy:
            self.scan_external_btn.setText("扫描中...")

            self.summary_label.setText("状态：扫描中...")

            self.summary_label.setToolTip("正在后台扫描当前工作目录中的外部 Abaqus 作业。")

            return

        self.scan_external_btn.setText("扫描")

        self.refresh_summaries()

    def request_external_scan(self) -> None:
        work_dir = self.work_dir_edit.text().strip()

        if not work_dir:
            QtWidgets.QMessageBox.warning(
                self,
                "工作目录为空",
                "请先输入或选择工作目录。",
            )
            return

        if not os.path.isdir(work_dir):
            QtWidgets.QMessageBox.warning(
                self,
                "工作目录不存在",
                f"工作目录不存在：\n{work_dir}",
            )
            return

        self.set_external_scan_busy(True)

        self.scanExternalRequested.emit(work_dir)

    # ---------- Refresh ----------

    def refresh_tables(self) -> None:
        self.refresh_candidate_table()
        self.refresh_queue_table()

    @hang_probe_function("QueueManagerDialog.refresh_candidate_table")
    def refresh_candidate_table(self) -> None:
        self.sync_table_rows(
            self.candidate_table,
            self.candidates,
            CANDIDATE_COLUMNS,
            self.candidate_row_values,
            checkable_first_column=True,
        )
        self.ensure_candidate_columns_initialized()
        self.refresh_summaries()

    @hang_probe_function("QueueManagerDialog.refresh_queue_table")
    def refresh_queue_table(self) -> None:
        self.sync_table_rows(
            self.queue_table,
            self.queue_items,
            FORMAL_COLUMNS,
            self.formal_row_values,
        )
        self.ensure_formal_columns_initialized()
        self.refresh_summaries()

    def update_queue_memory_cells(self, updated_item_ids: set[str]) -> None:
        """Update volatile queue cells without rebuilding the whole table."""
        if not updated_item_ids:
            return
        self.queue_table.blockSignals(True)
        self.queue_table.setUpdatesEnabled(False)
        try:
            row_by_key = self.table_row_key_map(self.queue_table)
            for item in self.queue_items:
                if item.item_id not in updated_item_ids:
                    continue
                row = row_by_key.get(item.item_id)
                if row is None:
                    continue
                updates = {
                    7: self.format_runtime_memory(item.rss_bytes),
                    8: item.status,
                    9: item.message,
                }
                for column, value in updates.items():
                    self.update_table_cell(
                        self.queue_table,
                        row,
                        column,
                        value,
                        FORMAL_COLUMNS[column],
                        item.item_id,
                    )
        finally:
            self.queue_table.setUpdatesEnabled(True)
            self.queue_table.blockSignals(False)
        self.queue_table.viewport().update()
        self.refresh_summaries()

    def refresh_summaries(self) -> None:
        total = len(self.candidates)

        selected = sum(1 for item in self.candidates if item.selected)

        invalid = sum(1 for item in self.candidates if not item.valid)

        self.candidate_summary_label.setText(f"候选：{total} | 已选 {selected} | 异常 {invalid}")

        # 扫描期间保持“扫描中”提示，
        # 不被表格刷新过程覆盖。
        if self.external_scan_busy:
            self.summary_label.setText("状态：扫描中...")

            return

        if not self.queue_items:
            self.summary_label.setText("状态：队列为空")

            self.summary_label.setToolTip("正式队列中暂时没有作业。")

            return

        counts = queue_status_counts(self.queue_items)

        if counts.get(STATUS_RUNNING, 0) > 0:
            short_status = "状态：运行中"

        elif counts.get(STATUS_TERMINATING, 0) > 0:
            short_status = "状态：正在终止"

        elif counts.get(STATUS_STARTING, 0) > 0:
            short_status = "状态：正在启动"

        elif counts.get(STATUS_PENDING_RUN, 0) > 0:
            short_status = "状态：存在待运行作业"

        elif counts.get(STATUS_WAITING_DEPENDENCY, 0) > 0:
            short_status = "状态：等待前置作业"

        elif counts.get(STATUS_CONFIRMING, 0) > 0:
            short_status = "状态：确认中"

        elif counts.get(STATUS_INTERRUPTED, 0) > 0:
            short_status = "状态：疑似异常中断"

        elif counts.get(STATUS_UNKNOWN, 0) > 0:
            short_status = "状态：存在未知状态"

        else:
            short_status = "状态：空闲"

        self.summary_label.setText(short_status)

        detail = " | ".join(f"{status} {count}" for status, count in counts.items())

        self.summary_label.setToolTip(f"正式队列：{detail}")

    def candidate_row_values(self, item: QueueItem, index: int) -> tuple[str, ...]:
        dependency = item.oldjob_name or (Path(item.oldjob_path).stem if item.oldjob_path else "")
        return (
            "",
            str(index),
            item.job_name,
            item.inp_path,
            item.source or "文件",
            item.job_type or ("重启动" if item.run_mode == "restart" else "普通"),
            dependency,
            os.path.basename(item.fortran_path) if item.fortran_path else "",
            item.message,
        )

    @staticmethod
    def format_runtime_memory(
        size_bytes: int,
    ) -> str:
        """将最近一次统计到的内存占用量格式化为 MB 或 GB。"""
        try:
            size_bytes = int(size_bytes or 0)
        except (TypeError, ValueError):
            return "未统计"

        if size_bytes <= 0:
            return "未统计"

        gib = size_bytes / 1024**3

        if gib >= 1:
            return f"{gib:.1f} GB"

        mib = size_bytes / 1024**2

        return f"{mib:.0f} MB"

    def formal_row_values(self, item: QueueItem, index: int) -> tuple[str, ...]:
        dependency = item.oldjob_name or (Path(item.oldjob_path).stem if item.oldjob_path else "")
        return (
            str(index),
            item.job_name,
            item.inp_path,
            item.job_type or ("重启动" if item.run_mode == "restart" else "普通"),
            dependency,
            os.path.basename(item.fortran_path) if item.fortran_path else "",
            "" if int(item.cores or 0) <= 0 else str(item.cores),
            self.format_runtime_memory(item.rss_bytes),
            item.status,
            item.message,
        )

    def ensure_candidate_columns_initialized(self) -> None:
        if self.candidate_columns_initialized:
            return
        self.resize_candidate_columns()
        self.candidate_columns_initialized = True

    def ensure_formal_columns_initialized(self) -> None:
        if self.formal_columns_initialized:
            return
        self.resize_formal_columns()
        self.formal_columns_initialized = True

    def resize_candidate_columns(self) -> None:
        widths = (40, 40, 140, 360, 100, 100, 120, 120, 180)
        for column, width in enumerate(widths):
            self.candidate_table.setColumnWidth(column, width)

    def resize_formal_columns(self) -> None:
        widths = (40, 140, 360, 90, 110, 110, 60, 60, 110, 100)
        for column, width in enumerate(widths):
            self.queue_table.setColumnWidth(column, width)

    def default_work_dir(self) -> str:
        candidates = [self.work_dir_from_queue(), os.path.dirname(self.current_inp)]
        for path in candidates:
            if path and os.path.isdir(path):
                return os.path.normpath(path)
        return os.getcwd()

    def work_dir_from_queue(self) -> str:
        for item in self.queue_items:
            path = effective_queue_item_work_dir(item)
            if path and os.path.isdir(path):
                return path
        return ""
