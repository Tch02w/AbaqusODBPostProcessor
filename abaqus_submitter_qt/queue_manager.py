"""Qt queue manager dialog for the new frontend."""

from __future__ import annotations

import os
import json
from pathlib import Path

from .models import QueueItem

from .command import derive_job_name
from .qt_compat import QtCore, QtWidgets, Signal


STATUS_PENDING = "待运行"
STATUS_WAITING = "等待前置"
STATUS_RUNNING = "运行中"
STATUS_STARTING = "启动中"
STATUS_TERMINATING = "正在终止"
STATUS_COMPLETED = "已完成"
STATUS_FAILED = "运行失败"
STATUS_CANCELED = "已取消"
STATUS_TERMINATED = "已终止"
STATUS_INTERRUPTED = "疑似异常中断"
STATUS_CONFIRMING = "状态确认中"
STATUS_UNKNOWN = "状态未知"

TERMINAL_STATUSES = {
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELED,
    STATUS_TERMINATED,
}

RESULT_EXTENSIONS = (".odb", ".sta", ".msg", ".dat", ".log")
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
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


def atomic_write_json(path, payload):
    """Atomically write JSON so joblist.json is never half-written."""
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, path)


class QueueManagerDialog(QtWidgets.QDialog):
    """Manage candidate INP files and the formal run queue."""

    terminateRequested = Signal(list)
    scanExternalRequested = Signal(str)

    def __init__(self, parent, queue_items: list[QueueItem], current_settings, current_inp=""):
        super().__init__(parent)
        self.setWindowTitle("作业队列管理")
        self.resize(1280, 752)
        self.queue_items = queue_items
        self.current_settings = current_settings
        self.current_inp = current_inp
        self.candidates: list[QueueItem] = []
        self.saved_paths = self.load_saved_paths()
        self.external_scan_busy = False
        self.candidate_columns_initialized = False
        self.formal_columns_initialized = False

        self.build_ui()
        self.refresh_tables()

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
        self.queue_table.itemDoubleClicked.connect(lambda _item: self.edit_selected_pending())
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
                background: #f3f6fa;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 12px;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 10px;
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
                color: #3f5f87;
                font-weight: 400;
            }
            QPushButton {
                background: #dbe3ee;
                color: #111827;
                border: 0;
                border-radius: 6px;
                min-height: 28px;
                padding: 3px 10px;
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
                background: #ffffff;
                border: 1px solid #cbd5e1;
                min-height: 28px;
                padding: 0 8px;
            }
            QTableWidget {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                gridline-color: #e5e7eb;
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            QHeaderView::section {
                background: #eef4fb;
                border: 0;
                border-right: 1px solid #e5e7eb;
                padding: 6px;
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
            status="候选",
            selected=True,
            valid=True,
            message="可加入",
            cores=self.current_settings["cores"],
            memory=self.current_settings["memory"],
            fortran_path=self.current_settings["for_file"],
            oldjob_path=self.current_settings["oldjob_path"],
            interactive=self.current_settings["interactive"],
            datacheck_only=self.current_settings["datacheck"],
            complete_notify=self.current_settings["notify"],
            source_inp_path=normalized,
            calculation_work_dir=self.ssd_dir_edit.text().strip(),
            archive_dir=self.archive_dir_edit.text().strip(),
            archive_after_complete=bool(self.archive_dir_edit.text().strip()),
            cleanup_after_archive=True,
        )
        self.detect_restart(item)
        self.validate_candidate(item)
        self.candidates.append(item)
        existing_paths.add(normalized_key)
        if refresh:
            self.refresh_candidate_table()
        return True

    def add_current_inp(self) -> None:
        if self.current_inp:
            self.add_candidate(self.current_inp, "当前 INP")

    def add_inp_files(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "添加 INP 文件",
            self.default_work_dir(),
            "Abaqus INP (*.inp);;所有文件 (*.*)",
        )
        existing_paths = {os.path.normcase(item.inp_path) for item in self.candidates + self.queue_items}
        changed = False
        for path in paths:
            changed = (
                self.add_candidate(
                    path,
                    "文件",
                    refresh=False,
                    existing_paths=existing_paths,
                )
                or changed
            )
        if changed:
            self.refresh_candidate_table()

    def scan_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "扫描文件夹", self.default_work_dir())
        if not folder:
            return
        pattern = "**/*.inp" if self.scan_subdirs_check.isChecked() else "*.inp"
        existing_paths = {os.path.normcase(item.inp_path) for item in self.candidates + self.queue_items}
        changed = False
        for path in sorted(Path(folder).glob(pattern)):
            changed = (
                self.add_candidate(
                    str(path),
                    "文件夹",
                    refresh=False,
                    existing_paths=existing_paths,
                )
                or changed
            )
        if changed:
            self.refresh_candidate_table()

    def has_result_files(self, inp_path: str) -> bool:
        base = Path(inp_path).with_suffix("")
        return any(base.with_suffix(ext).exists() for ext in RESULT_EXTENSIONS)

    def detect_restart(self, item: QueueItem) -> None:
        item.run_mode = "normal"
        item.job_type = "普通"
        try:
            with open(item.inp_path, "r", encoding="gbk", errors="ignore") as stream:
                head = stream.read(32768)
        except OSError:
            item.valid = False
            item.message = "INP 文件不可读"
            return
        if "*restart" in head.lower():
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
        if item.job_name.lower() in {entry.job_name.lower() for entry in self.queue_items}:
            item.valid = False
            item.message = "正式队列中已存在同名作业"
            return
        if item.fortran_path and not os.path.isfile(item.fortran_path):
            item.valid = False
            item.message = "FOR 文件不存在"
            return
        item.valid = True
        item.message = "可加入"

    # ---------- Candidate actions ----------

    def on_candidate_cell_changed(self, row: int, column: int) -> None:
        if column != 0 or row < 0 or row >= len(self.candidates):
            return
        table_item = self.candidate_table.item(row, 0)
        if table_item is None:
            return
        self.candidates[row].selected = table_item.checkState() == QtCore.Qt.CheckState.Checked
        self.refresh_summaries()

    def on_candidate_item_double_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        row = item.row()
        if 0 <= row < len(self.candidates):
            self.candidates[row].selected = not self.candidates[row].selected
            self.refresh_candidate_table()

    def set_candidate_selection(self, selected: bool) -> None:
        for item in self.candidates:
            if item.valid:
                item.selected = selected
        self.refresh_candidate_table()

    def invert_candidate_selection(self) -> None:
        for item in self.candidates:
            if item.valid:
                item.selected = not item.selected
        self.refresh_candidate_table()

    def remove_selected_candidates(self) -> None:
        selected_rows = {index.row() for index in self.candidate_table.selectedIndexes()}
        if selected_rows:
            self.candidates[:] = [item for index, item in enumerate(self.candidates) if index not in selected_rows]
        else:
            self.candidates[:] = [item for item in self.candidates if not item.selected]
        self.refresh_candidate_table()

    def confirm_candidates(self) -> None:
        selected_items = [item for item in self.candidates if item.selected and item.valid]
        if not selected_items:
            QtWidgets.QMessageBox.warning(self, "没有可加入作业", "请先勾选有效的候选作业。")
            return
        existing = {os.path.normcase(item.inp_path) for item in self.queue_items}
        added_ids = set()
        for item in selected_items:
            if os.path.normcase(item.inp_path) in existing:
                continue
            item.source_inp_path = item.source_inp_path or item.inp_path
            item.calculation_work_dir = self.ssd_dir_edit.text().strip()
            item.archive_dir = self.archive_dir_edit.text().strip()
            item.archive_after_complete = bool(item.archive_dir)
            item.cleanup_after_archive = True
            item.status = STATUS_PENDING
            item.message = "待提交"
            item.source = item.source or "候选"
            self.queue_items.append(item)
            existing.add(os.path.normcase(item.inp_path))
            added_ids.add(item.item_id)
        self.candidates[:] = [item for item in self.candidates if item.item_id not in added_ids]
        self.refresh_tables()

    # ---------- Formal queue actions ----------

    def selected_queue_rows(self) -> list[int]:
        return sorted({index.row() for index in self.queue_table.selectedIndexes()})

    def cancel_selected_pending(self) -> None:
        blocked = False
        for row in self.selected_queue_rows():
            if row >= len(self.queue_items):
                continue
            item = self.queue_items[row]
            if item.status in (STATUS_PENDING, STATUS_WAITING):
                item.status = STATUS_CANCELED
                item.message = "用户取消"
            elif item.status in (STATUS_RUNNING, STATUS_STARTING, STATUS_TERMINATING):
                blocked = True
        self.refresh_queue_table()
        if blocked:
            QtWidgets.QMessageBox.information(self, "不能取消运行中作业", "运行中作业请使用“终止选中的运行中作业”。")

    def edit_selected_pending(self) -> None:
        rows = self.selected_queue_rows()
        if len(rows) != 1:
            QtWidgets.QMessageBox.information(self, "编辑队列作业", "请选择一个待运行作业。")
            return
        item = self.queue_items[rows[0]]
        if item.status not in (STATUS_PENDING, STATUS_WAITING):
            QtWidgets.QMessageBox.information(self, "编辑队列作业", "只能编辑待运行或等待前置的作业。")
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("编辑待运行作业")
        layout = QtWidgets.QFormLayout(dialog)
        core_spin = QtWidgets.QSpinBox()
        core_spin.setRange(0, 999)
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
        item.status = STATUS_PENDING if item.valid else STATUS_FAILED
        item.message = "待提交" if item.valid else item.message
        self.refresh_queue_table()

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

    def clear_finished(self) -> None:
        self.queue_items[:] = [item for item in self.queue_items if item.status not in TERMINAL_STATUSES]
        self.refresh_tables()

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

    def refresh_candidate_table(self) -> None:
        self.candidate_table.blockSignals(True)
        try:
            self.candidate_table.setRowCount(len(self.candidates))

            for row, item in enumerate(self.candidates):
                values = self.candidate_row_values(
                    item,
                    row + 1,
                )

                for column, value in enumerate(values):
                    table_item = QtWidgets.QTableWidgetItem(value)

                    self.apply_table_item_alignment(
                        table_item,
                        CANDIDATE_COLUMNS[column],
                    )

                    if column == 0:
                        table_item.setFlags(table_item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)

                        table_item.setCheckState(
                            QtCore.Qt.CheckState.Checked if item.selected else QtCore.Qt.CheckState.Unchecked
                        )

                    self.candidate_table.setItem(
                        row,
                        column,
                        table_item,
                    )

        finally:
            self.candidate_table.blockSignals(False)
        self.ensure_candidate_columns_initialized()
        self.refresh_summaries()

    def refresh_queue_table(self) -> None:
        self.queue_table.blockSignals(True)
        try:
            self.queue_table.setRowCount(len(self.queue_items))

            for row, item in enumerate(self.queue_items):
                values = self.formal_row_values(
                    item,
                    row + 1,
                )

                for column, value in enumerate(values):
                    table_item = QtWidgets.QTableWidgetItem(value)

                    self.apply_table_item_alignment(
                        table_item,
                        FORMAL_COLUMNS[column],
                    )

                    self.queue_table.setItem(
                        row,
                        column,
                        table_item,
                    )

        finally:
            self.queue_table.blockSignals(False)
        self.ensure_formal_columns_initialized()
        self.refresh_summaries()

    def update_queue_memory_cells(self, updated_item_ids: set[str]) -> None:
        """Update volatile queue cells without rebuilding the whole table."""
        if not updated_item_ids:
            return
        self.queue_table.blockSignals(True)
        try:
            for row, item in enumerate(self.queue_items):
                if item.item_id not in updated_item_ids:
                    continue
                updates = {
                    7: self.format_runtime_memory(item.rss_bytes),
                    8: item.status,
                    9: item.message,
                }
                for column, value in updates.items():
                    table_item = self.queue_table.item(row, column)
                    if table_item is None:
                        table_item = QtWidgets.QTableWidgetItem()
                        self.apply_table_item_alignment(table_item, FORMAL_COLUMNS[column])
                        self.queue_table.setItem(row, column, table_item)
                    table_item.setText(value)
        finally:
            self.queue_table.blockSignals(False)
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

        counts: dict[str, int] = {}

        for item in self.queue_items:
            counts[item.status] = counts.get(item.status, 0) + 1

        if counts.get(STATUS_RUNNING, 0) > 0:
            short_status = "状态：运行中"

        elif counts.get(STATUS_TERMINATING, 0) > 0:
            short_status = "状态：正在终止"

        elif counts.get(STATUS_STARTING, 0) > 0:
            short_status = "状态：正在启动"

        elif counts.get(STATUS_PENDING, 0) > 0:
            short_status = "状态：存在待运行作业"

        elif counts.get(STATUS_WAITING, 0) > 0:
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
            str(item.cores),
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
            path = item.external_work_dir or os.path.dirname(item.inp_path)
            if path and os.path.isdir(path):
                return path
        return ""
