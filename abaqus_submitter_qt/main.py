"""Qt main window for the Abaqus submitter."""

from __future__ import annotations

import os
import shutil
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from .abaqus_diagnostics import (
    build_sta_table_header,
    classify_job_text,
    inspect_job_files,
)
from .archive import (
    ArchiveCoordinator,
    backup_existing_result_files,
    delete_existing_result_files,
    get_existing_lck_file,
    get_existing_odb_file,
    prepare_calculation_workspace,
)
from .constants import (
    DEFAULT_CPUS,
    JOB_MEMORY_BASE_SAFETY_FACTOR,
    JOB_MEMORY_LEARNING_INTERVAL_MS,
    JOB_MEMORY_MAX_SAMPLES,
    JOB_MEMORY_MIN_SAMPLES,
    JOB_MEMORY_PATROL_INTERVAL_MS,
    JOB_MEMORY_STABLE_POLLS,
    JOB_MEMORY_STABLE_RELATIVE_DELTA,
    LOG_SEPARATOR_WIDTH,
    MAX_CPUS,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_TERMINATED,
    STATUS_TERMINATING,
    UNLIMITED_JOB_SLOTS,
    calculate_default_joblist_parallel,
)
from .memory_adapter import QtMemoryMonitorAdapter
from .memory_monitor import MemoryMonitorService, format_memory_size
from .models import QueueItem
from .command import (
    MEMORY_OPTIONS,
    SubmitOptions,
    build_direct_submit_queue_item,
    build_abaqus_command,
    derive_job_name,
    derive_oldjob_name,
    queue_item_to_options,
    validate_options,
)
from .external_jobs import (
    ExternalJobScanWorker,
    build_queue_item_index as external_build_queue_item_index,
    collect_known_external_jobs as external_collect_known_external_jobs,
    merge_external_scan_results,
)
from .job_runtime import JobRuntimeController
from .qt_compat import QtCore, QtGui, QtWidgets
from .queue_manager import QueueManagerDialog
from .queue_scheduler import (
    find_queue_oldjob_item as scheduler_find_queue_oldjob_item,
    get_managed_active_job_keys as scheduler_get_managed_active_job_keys,
    get_managed_active_job_names as scheduler_get_managed_active_job_names,
    managed_active_statuses as scheduler_managed_active_statuses,
    managed_job_key as scheduler_managed_job_key,
    oldjob_name_from_item as scheduler_oldjob_name_from_item,
    refresh_queue_dependencies as scheduler_refresh_queue_dependencies,
)
from .ui_components import (
    build_memory_summary_table,
    FilePickerRow,
    format_elapsed_seconds,
    format_run_status,
    safe_int,
)
from .ui_styles import (
    APP_TITLE,
    COMPACT_WINDOW_MIN_WIDTH,
    LEFT_PANEL_MIN_WIDTH,
    PANEL_HORIZONTAL_SPACING,
    PRIMARY,
    RUNTIME_BODY_HORIZONTAL_MARGIN,
    RUNTIME_LOG_WIDTH_SAMPLE,
    TEXT,
    WINDOW_OUTER_HORIZONTAL_MARGIN,
    build_main_stylesheet,
)

ENABLE_EXTERNAL_SCAN_DEBUG_LOG = False


class MainWindow(QtWidgets.QMainWindow):
    """Qt version of the main submitter layout."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(
            COMPACT_WINDOW_MIN_WIDTH,
            720,
        )

        self.resize(
            COMPACT_WINDOW_MIN_WIDTH,
            720,
        )

        self.current_work_dir = ""
        self.current_job_name = ""
        self.current_log_suffix = ".sta"
        self.command_preview = ""
        self.queue_items: list[QueueItem] = []
        self.run_records: dict[str, dict] = {}
        self.active_runs: dict[str, dict] = {}
        self.queue_manager_dialog: QueueManagerDialog | None = None
        self.current_job_key = ""
        self.queue_active = False
        self.queue_stop_requested = False
        self.queue_existing_result_action = ""
        self.external_scan_thread: QtCore.QThread | None = None
        self.external_scan_worker: ExternalJobScanWorker | None = None
        self.external_scan_dialog: QueueManagerDialog | None = None
        self.external_scan_message_box: QtWidgets.QMessageBox | None = None
        self.job_notification_boxes: list[QtWidgets.QMessageBox] = []
        self.deferred_archive_runs: dict[str, dict] = {}
        self.latest_memory_usage_by_job: dict = {}
        self.latest_system_memory: dict[str, int] = {}
        self.latest_memory_slot_estimate = None
        self.memory_monitor_service = MemoryMonitorService(
            learning_interval_ms=JOB_MEMORY_LEARNING_INTERVAL_MS,
            patrol_interval_ms=JOB_MEMORY_PATROL_INTERVAL_MS,
            max_samples=JOB_MEMORY_MAX_SAMPLES,
            min_samples=JOB_MEMORY_MIN_SAMPLES,
            stable_polls=JOB_MEMORY_STABLE_POLLS,
            stable_relative_delta=JOB_MEMORY_STABLE_RELATIVE_DELTA,
            safety_factor=JOB_MEMORY_BASE_SAFETY_FACTOR,
            unlimited_job_slots=UNLIMITED_JOB_SLOTS,
        )
        self.memory_adapter = QtMemoryMonitorAdapter(
            service=self.memory_monitor_service,
            parent=self,
        )
        self.memory_adapter.scanFinished.connect(self.apply_memory_scan_result)
        self.memory_adapter.memoryScanFailed.connect(self.on_memory_scan_failed)
        self.memory_adapter.memorySlotEstimateChanged.connect(self.on_memory_slot_estimate_changed)
        self.runtime_controller = JobRuntimeController(
            memory_adapter=self.memory_adapter,
            parent=self,
        )
        self.runtime_controller.jobLogReceived.connect(lambda job_key, text: self.append_job_log(text, job_key))
        self.runtime_controller.historyEvent.connect(self.append_history)
        self.runtime_controller.jobUpdated.connect(self.on_runtime_job_updated)
        self.runtime_controller.jobFinished.connect(self.finalize_completed_run)
        self.runtime_controller.processError.connect(self.on_process_error)
        self.last_effective_slot_signature: tuple[int, int, int, int] | None = None

        self.build_ui()
        self.apply_styles()

        QtCore.QTimer.singleShot(
            0,
            self.apply_runtime_panel_width_baseline,
        )

        self.update_abaqus_status()
        self.update_command_preview()
        self.append_history("等待提交作业...")

    # ---------- UI ----------

    def build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        self.root_layout = QtWidgets.QHBoxLayout(central)
        self.root_layout.setContentsMargins(12, 12, 12, 12)
        self.root_layout.setSpacing(12)

        self.left_panel = QtWidgets.QWidget()

        self.left_panel.setObjectName("leftPanel")

        self.left_panel.setMinimumWidth(LEFT_PANEL_MIN_WIDTH)

        self.left_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Minimum,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        left_panel = self.left_panel
        left_panel.setFixedWidth(LEFT_PANEL_MIN_WIDTH)
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        self.submit_card = QtWidgets.QFrame()
        self.submit_card.setObjectName("card")
        card_layout = QtWidgets.QVBoxLayout(self.submit_card)
        card_layout.setContentsMargins(18, 14, 18, 14)
        card_layout.setSpacing(10)

        self.abaqus_status_label = QtWidgets.QLabel("Abaqus 状态：待检测")
        self.abaqus_status_label.setObjectName("hint")
        card_layout.addWidget(self.abaqus_status_label)

        self.inp_row = FilePickerRow("INP", "点击选择 INP 文件")
        self.oldjob_row = FilePickerRow("ODB", "点击选择重启动 ODB（可选）")
        self.for_row = FilePickerRow("FOR", "点击选择 Fortran 子程序（可选）")
        card_layout.addWidget(self.inp_row)
        card_layout.addWidget(self.oldjob_row)
        card_layout.addWidget(self.for_row)

        self.inp_row.button.clicked.connect(self.select_inp_file)
        self.oldjob_row.button.clicked.connect(self.select_oldjob_file)
        self.for_row.button.clicked.connect(self.select_for_file)
        self.inp_row.pathChanged.connect(self.on_input_changed)
        self.oldjob_row.pathChanged.connect(self.update_command_preview)
        self.for_row.pathChanged.connect(self.update_command_preview)

        settings = QtWidgets.QHBoxLayout()
        settings.setSpacing(8)
        core_label = QtWidgets.QLabel("Core")
        core_label.setFixedWidth(34)
        settings.addWidget(core_label)
        self.cpus_spin = QtWidgets.QSpinBox()
        self.cpus_spin.setObjectName("plainSpin")
        self.cpus_spin.setRange(0, MAX_CPUS)
        self.cpus_spin.setValue(DEFAULT_CPUS)
        self.cpus_spin.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.cpus_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.cpus_spin.setFixedSize(52, 28)
        settings.addWidget(self.cpus_spin)
        max_label = QtWidgets.QLabel(f"最大 {MAX_CPUS}")
        max_label.setObjectName("hint")
        settings.addWidget(max_label)
        settings.addStretch(1)
        settings.addWidget(QtWidgets.QLabel("Mem"))
        self.memory_value = QtWidgets.QLineEdit()
        self.memory_value.setText("90")
        self.memory_value.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.memory_value.setFixedSize(52, 28)
        self.memory_unit = QtWidgets.QComboBox()
        self.memory_unit.addItems(MEMORY_OPTIONS)
        self.memory_unit.setFixedSize(56, 28)
        settings.addWidget(self.memory_value)
        settings.addWidget(self.memory_unit)
        card_layout.addLayout(settings)

        self.cpus_spin.valueChanged.connect(self.update_command_preview)
        self.memory_value.textChanged.connect(self.update_command_preview)
        self.memory_unit.currentTextChanged.connect(self.update_command_preview)

        options = QtWidgets.QHBoxLayout()
        options.setSpacing(18)
        self.interactive_check = QtWidgets.QCheckBox("交互输出")
        self.datacheck_check = QtWidgets.QCheckBox("仅数据检查")
        self.notify_check = QtWidgets.QCheckBox("结束提醒")
        self.notify_check.setChecked(True)
        options.addWidget(self.interactive_check)
        options.addWidget(self.datacheck_check)
        options.addWidget(self.notify_check)
        card_layout.addLayout(options)
        self.interactive_check.toggled.connect(self.update_command_preview)
        self.datacheck_check.toggled.connect(self.update_command_preview)

        action_grid = QtWidgets.QGridLayout()
        action_grid.setHorizontalSpacing(8)
        action_grid.setVerticalSpacing(8)
        for column in range(4):
            action_grid.setColumnStretch(column, 1)
        self.preview_btn = QtWidgets.QPushButton("预览命令")
        self.submit_btn = QtWidgets.QPushButton("提交作业")
        self.queue_btn = QtWidgets.QPushButton("管理队列")
        self.start_queue_btn = QtWidgets.QPushButton("开始队列")
        action_button_width = 89
        for button in (self.preview_btn, self.submit_btn, self.queue_btn, self.start_queue_btn):
            button.setFixedSize(action_button_width, 32)
        self.preview_btn.setObjectName("light")
        self.submit_btn.setObjectName("primary")
        self.queue_btn.setObjectName("light")
        self.start_queue_btn.setObjectName("primary")
        action_grid.addWidget(self.preview_btn, 0, 0)
        action_grid.addWidget(self.submit_btn, 0, 1)
        action_grid.addWidget(self.queue_btn, 0, 2)
        action_grid.addWidget(self.start_queue_btn, 0, 3)
        card_layout.addLayout(action_grid)

        self.preview_btn.clicked.connect(self.preview_command)
        self.submit_btn.clicked.connect(self.submit_job)
        self.queue_btn.clicked.connect(self.open_queue_manager)
        self.start_queue_btn.clicked.connect(self.start_queue)

        queue_row = QtWidgets.QGridLayout()
        queue_row.setHorizontalSpacing(8)
        queue_row.setVerticalSpacing(0)
        for column in range(4):
            queue_row.setColumnStretch(column, 1)
        queue_controls = QtWidgets.QHBoxLayout()
        queue_controls.setSpacing(8)
        queue_controls.addWidget(QtWidgets.QLabel("队列最大并行"))
        self.max_parallel_spin = QtWidgets.QSpinBox()
        self.max_parallel_spin.setRange(1, 999)
        self.max_parallel_spin.setValue(calculate_default_joblist_parallel(DEFAULT_CPUS))
        self.max_parallel_spin.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.max_parallel_spin.setFixedSize(52, 28)
        queue_controls.addWidget(self.max_parallel_spin)
        hint = QtWidgets.QLabel("达到上限后暂停补位")
        hint.setObjectName("hint")
        queue_controls.addWidget(hint)
        queue_controls.addStretch(1)
        queue_row.addLayout(queue_controls, 0, 0, 1, 3)
        self.stop_queue_btn = QtWidgets.QPushButton("终止队列")
        self.stop_queue_btn.setObjectName("danger")
        self.stop_queue_btn.setFixedSize(action_button_width, 32)
        queue_row.addWidget(self.stop_queue_btn, 0, 3)
        card_layout.addLayout(queue_row)
        self.stop_queue_btn.clicked.connect(self.stop_queue)

        self.queue_status_label = QtWidgets.QLabel("队列：未生成")
        self.queue_status_label.setObjectName("hint")
        card_layout.addWidget(self.queue_status_label)

        left_layout.addWidget(self.submit_card)

        history_card = QtWidgets.QFrame()
        history_card.setObjectName("card")
        history_layout = QtWidgets.QVBoxLayout(history_card)
        history_layout.setContentsMargins(18, 14, 18, 14)
        history_layout.setSpacing(8)
        history_layout.addWidget(QtWidgets.QLabel("运行监控"))
        self.history = QtWidgets.QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.setObjectName("log")
        history_layout.addWidget(self.history, 1)
        left_layout.addWidget(history_card, 1)

        # ---------- 右侧：作业运行情况 ----------

        self.right_panel = QtWidgets.QFrame()
        self.right_panel.setObjectName("card")

        right_layout = QtWidgets.QVBoxLayout(self.right_panel)

        self.right_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        right_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        right_layout.setSpacing(6)

        # 标题
        runtime_title = QtWidgets.QLabel("作业运行情况")

        runtime_title.setObjectName("runtimeTitle")

        right_layout.addWidget(runtime_title)

        # ---------- 第一行：Job 选择器 + 总体统计 ----------

        selector_row = QtWidgets.QHBoxLayout()

        selector_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        selector_row.setSpacing(8)

        selector_row.addWidget(QtWidgets.QLabel("Job"))

        self.job_selector = QtWidgets.QComboBox()

        self.job_selector.setObjectName("runtimeSelector")

        self.job_selector.setMinimumWidth(220)

        self.job_selector.setMaximumWidth(340)

        self.job_selector.setFixedHeight(30)

        self.job_selector.currentIndexChanged.connect(self.on_job_selector_changed)

        selector_row.addWidget(self.job_selector)

        selector_row.addStretch(1)

        self.job_stats_label = QtWidgets.QLabel("运行中 0 | 完成 0 | 异常 0")

        self.job_stats_label.setObjectName("hint")

        selector_row.addWidget(self.job_stats_label)

        right_layout.addLayout(selector_row)

        # ---------- 第二行：当前 Job 名称 + 详细状态 ----------

        current_job_row = QtWidgets.QHBoxLayout()

        current_job_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        current_job_row.setSpacing(8)

        self.current_job_title_label = QtWidgets.QLabel("Job: 未选择")

        self.current_job_title_label.setObjectName("runtimeJobTitle")

        current_job_row.addWidget(self.current_job_title_label)

        current_job_row.addStretch(1)

        self.status_label = QtWidgets.QLabel("状态：未运行")

        self.status_label.setObjectName("runtimeStatus")

        current_job_row.addWidget(self.status_label)

        # ---------- 第三行：目录 / 文件按钮 + 暂停 / 终止 ----------

        action_row = QtWidgets.QHBoxLayout()

        action_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        action_row.setSpacing(8)

        self.open_dir_btn = QtWidgets.QPushButton("目录")

        self.open_sta_btn = QtWidgets.QPushButton("STA")

        self.open_msg_btn = QtWidgets.QPushButton("MSG")

        self.open_dat_btn = QtWidgets.QPushButton("DAT")

        for button in (
            self.open_dir_btn,
            self.open_sta_btn,
            self.open_msg_btn,
            self.open_dat_btn,
        ):
            button.setFixedSize(
                58,
                30,
            )

            action_row.addWidget(button)

        action_row.addStretch(1)

        self.pause_btn = QtWidgets.QPushButton("暂停")

        self.pause_btn.setObjectName("warning")

        self.pause_btn.setFixedSize(
            68,
            30,
        )

        self.stop_btn = QtWidgets.QPushButton("终止")

        self.stop_btn.setObjectName("danger")

        self.stop_btn.setFixedSize(
            68,
            30,
        )

        action_row.addWidget(self.pause_btn)

        action_row.addWidget(self.stop_btn)

        # ---------- 第四块：当前作业概要 ----------

        self.job_meta = QtWidgets.QPlainTextEdit()

        self.job_meta.setReadOnly(True)

        self.job_meta.setObjectName("runtimeMeta")

        self.job_meta.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)

        self.job_meta.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.job_meta.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

        self.job_meta.setMaximumHeight(170)

        self.job_meta.setPlainText("尚未提交作业。")

        # ---------- 第五块：STA / MSG / DAT 运行日志 ----------

        self.job_log = QtWidgets.QPlainTextEdit()

        self.job_log.setReadOnly(True)

        self.job_log.setObjectName("runtimeLog")

        self.job_log.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)

        self.job_log.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.job_log.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        # ---------- 当前作业详情整体外框 ----------

        self.runtime_body_frame = QtWidgets.QFrame()

        self.runtime_body_frame.setObjectName("runtimeBodyCard")

        runtime_body_layout = QtWidgets.QVBoxLayout(self.runtime_body_frame)

        runtime_body_layout.setContentsMargins(
            8,
            8,
            8,
            8,
        )

        runtime_body_layout.setSpacing(6)

        runtime_body_layout.addLayout(current_job_row)

        runtime_body_layout.addLayout(action_row)

        runtime_body_layout.addWidget(self.job_meta)

        self.job_meta.setMinimumWidth(0)

        self.sta_sticky_header_label = QtWidgets.QLabel(build_sta_table_header() + "\n" + "-" * LOG_SEPARATOR_WIDTH)
        self.sta_sticky_header_label.setObjectName("staStickyHeader")
        self.sta_sticky_header_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.sta_sticky_header_label.hide()

        self.runtime_log_frame = QtWidgets.QFrame()
        self.runtime_log_frame.setObjectName("runtimeLogFrame")
        runtime_log_layout = QtWidgets.QVBoxLayout(self.runtime_log_frame)
        runtime_log_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        runtime_log_layout.setSpacing(0)

        runtime_log_layout.addWidget(self.sta_sticky_header_label)

        runtime_log_layout.addWidget(
            self.job_log,
            1,
        )

        runtime_body_layout.addWidget(
            self.runtime_log_frame,
            1,
        )

        self.job_log.setMinimumWidth(0)

        self.job_log.verticalScrollBar().valueChanged.connect(self.update_sta_sticky_header_visibility)

        right_layout.addWidget(
            self.runtime_body_frame,
            1,
        )

        # ---------- 信号 ----------

        self.pause_btn.clicked.connect(self.toggle_pause_resume)

        self.stop_btn.clicked.connect(self.terminate_job)

        self.open_dir_btn.clicked.connect(self.open_work_dir)

        self.open_sta_btn.clicked.connect(lambda: self.select_runtime_log_file(".sta"))

        self.open_msg_btn.clicked.connect(lambda: self.select_runtime_log_file(".msg"))

        self.open_dat_btn.clicked.connect(lambda: self.select_runtime_log_file(".dat"))

        self.root_layout.addWidget(
            left_panel,
            0,
        )

        self.root_layout.addWidget(
            self.right_panel,
            1,
        )

        self.right_panel.hide()

        self.update_process_buttons(False)

    def apply_styles(self) -> None:
        self.setStyleSheet(build_main_stylesheet())

    # ---------- Data ----------

    def collect_options(self) -> SubmitOptions:
        inp_file = self.inp_row.text()
        return SubmitOptions(
            inp_file=inp_file,
            job_name=derive_job_name(inp_file),
            cpus=self.cpus_spin.value(),
            oldjob_path=self.oldjob_row.text(),
            for_file=self.for_row.text(),
            interactive=self.interactive_check.isChecked(),
            datacheck=self.datacheck_check.isChecked(),
            memory_value=self.memory_value.text().strip(),
            memory_unit=self.memory_unit.currentText(),
        )

    def current_queue_settings(self) -> dict:
        memory = ""
        memory_value = self.memory_value.text().strip()
        if memory_value:
            unit = self.memory_unit.currentText()
            memory = f"{memory_value}{'%' if unit == '%' else unit.lower()}"
        return {
            "cores": self.cpus_spin.value(),
            "memory": memory,
            "oldjob_path": self.oldjob_row.text(),
            "for_file": self.for_row.text(),
            "interactive": self.interactive_check.isChecked(),
            "datacheck": self.datacheck_check.isChecked(),
            "notify": self.notify_check.isChecked(),
        }

    def find_queue_item_by_job(
        self,
        *,
        work_dir: str,
        job_name: str,
    ) -> QueueItem | None:
        """按工作目录与 Job 名称查找正式队列记录。"""
        target_key = scheduler_managed_job_key(
            work_dir,
            job_name,
        )

        for item in self.queue_items:
            item_work_dir = item.external_work_dir or item.calculation_work_dir or os.path.dirname(item.inp_path)

            if (
                scheduler_managed_job_key(
                    item_work_dir,
                    item.job_name,
                )
                == target_key
            ):
                return item

        return None

    # ---------- Slots ----------

    def select_inp_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 INP 文件",
            "",
            "Abaqus INP (*.inp);;所有文件 (*.*)",
        )
        if path:
            self.inp_row.set_path(path)

    def select_oldjob_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择重启动 ODB",
            "",
            "Abaqus ODB (*.odb);;所有文件 (*.*)",
        )
        if path:
            self.oldjob_row.set_path(path)

    def select_for_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 Fortran 子程序",
            "",
            "Fortran (*.for *.f *.f90);;所有文件 (*.*)",
        )
        if path:
            self.for_row.set_path(path)

    def on_input_changed(self, _path: str) -> None:
        self.update_command_preview()

    def update_command_preview(self) -> None:
        options = self.collect_options()
        if not options.inp_file:
            self.command_preview = ""
            return
        self.command_preview = build_abaqus_command(options)

    def preview_command(self) -> None:
        options = self.collect_options()
        ok, message = validate_options(options)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "命令预览", message)
            return
        self.command_preview = build_abaqus_command(options)
        self.append_history(f"命令预览：\n{self.command_preview}")

    def submit_job(self) -> None:
        options = self.collect_options()

        ok, message = validate_options(options)

        if not ok:
            QtWidgets.QMessageBox.warning(
                self,
                "提交作业",
                message,
            )

            return

        work_dir = str(Path(options.inp_file).parent)

        queue_item = self.find_queue_item_by_job(
            work_dir=work_dir,
            job_name=options.job_name,
        )

        if queue_item is None:
            queue_item = build_direct_submit_queue_item(
                options,
                notify=self.notify_check.isChecked(),
            )

            self.queue_items.append(queue_item)

        started = self.start_job(
            options,
            queue_item=queue_item,
            queue_mode=False,
        )

        if not started:
            if queue_item.status in {
                "启动中",
                "运行中",
            }:
                queue_item.status = "运行失败"

            if not queue_item.message:
                queue_item.message = "直接提交失败"

        self.refresh_visible_queue_manager()
        self.update_queue_status_label()

    def open_queue_manager(self) -> None:
        if self.queue_manager_dialog is not None and self.queue_manager_dialog.isVisible():
            self.queue_manager_dialog.refresh_tables()
            self.queue_manager_dialog.raise_()
            self.queue_manager_dialog.activateWindow()
            return

        dialog = QueueManagerDialog(
            self,
            self.queue_items,
            self.current_queue_settings(),
            self.inp_row.text(),
        )
        dialog.setModal(False)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.terminateRequested.connect(self.terminate_queue_items_by_ids)
        dialog.scanExternalRequested.connect(
            lambda work_dir, queue_dialog=dialog: self.scan_external_jobs(work_dir, queue_dialog)
        )
        dialog.destroyed.connect(lambda _obj=None: setattr(self, "queue_manager_dialog", None))
        dialog.destroyed.connect(lambda _obj=None: self.process_deferred_archives())
        self.queue_manager_dialog = dialog
        self.position_queue_manager(dialog)
        dialog.show()
        self.update_queue_status_label()

    def refresh_visible_queue_manager(
        self,
    ) -> None:
        """队列管理窗口可见时刷新正式队列表格。"""
        dialog = self.queue_manager_dialog

        if dialog is None or not dialog.isVisible():
            return

        dialog.refresh_queue_table()

    def position_queue_manager(self, dialog: QueueManagerDialog) -> None:
        screen = (
            self.screen().availableGeometry()
            if self.screen()
            else QtWidgets.QApplication.primaryScreen().availableGeometry()
        )
        main_geo = self.frameGeometry()
        x = main_geo.right() + 12
        y = main_geo.top()
        if x + dialog.width() > screen.right():
            x = max(screen.left(), main_geo.left() - dialog.width() - 12)
        if y + dialog.height() > screen.bottom():
            y = max(screen.top(), screen.bottom() - dialog.height())
        dialog.move(x, y)

    def terminate_queue_items_by_ids(
        self,
        item_ids: list[str],
    ) -> None:
        """终止队列管理器中选中的运行中作业。"""
        item_id_set = set(item_ids)

        active_statuses = scheduler_managed_active_statuses()

        changed = False

        for item in self.queue_items:
            if item.item_id not in item_id_set:
                continue

            if item.status not in active_statuses:
                continue

            work_dir = item.external_work_dir or item.calculation_work_dir or os.path.dirname(item.inp_path)

            if not work_dir:
                item.message = "终止失败：未找到工作目录"

                continue

            command = f"abaqus terminate job={item.job_name}"

            started = QtCore.QProcess.startDetached(
                ("cmd.exe" if os.name == "nt" else "/bin/sh"),
                (["/c", command] if os.name == "nt" else ["-lc", command]),
                work_dir,
            )

            if not started:
                item.message = "终止失败：无法启动终止命令"

                self.append_history(f"终止队列作业失败：{command}")

                continue

            item.status = STATUS_TERMINATING

            item.message = "正在终止"

            if item.active_job_key:
                run = self.active_runs.get(item.active_job_key)

                if run is not None:
                    run["terminating"] = True

                    run["terminating_at"] = time.time()

            self.append_history(f"终止队列作业：{command}")

            changed = True

        if changed:
            self.refresh_visible_queue_manager()
            self.update_queue_status_label()

    def collect_known_external_jobs(self, work_dir: str) -> list[dict]:
        return external_collect_known_external_jobs(
            self.queue_items,
            work_dir,
        )

    def scan_external_jobs(
        self,
        work_dir: str,
        queue_dialog: QueueManagerDialog | None = None,
    ) -> None:
        if self.external_scan_thread is not None:
            self.show_non_modal_message(
                "扫描外部作业",
                "外部作业扫描正在进行，请稍候。",
            )
            return

        if self.queue_dialog_is_visible(queue_dialog):
            queue_dialog.set_external_scan_busy(True)

        self.append_history(f"开始后台扫描外部 Abaqus 作业：{work_dir}")

        self.external_scan_dialog = queue_dialog

        thread = QtCore.QThread(self)

        worker = ExternalJobScanWorker(
            work_dir,
            self.collect_known_external_jobs(work_dir),
        )

        worker.moveToThread(thread)

        thread.started.connect(worker.run)

        worker.finished.connect(
            self.finish_external_scan,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )

        worker.failed.connect(
            self.fail_external_scan,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )

        worker.finished.connect(thread.quit)

        worker.failed.connect(thread.quit)

        worker.finished.connect(worker.deleteLater)

        worker.failed.connect(worker.deleteLater)

        thread.finished.connect(thread.deleteLater)

        thread.finished.connect(self.clear_external_scan_worker)

        self.external_scan_thread = thread
        self.external_scan_worker = worker

        thread.start()

    def clear_external_scan_worker(self) -> None:
        self.external_scan_thread = None
        self.external_scan_worker = None

    @staticmethod
    def queue_dialog_is_visible(queue_dialog: QueueManagerDialog | None) -> bool:
        if queue_dialog is None:
            return False
        try:
            return queue_dialog.isVisible()
        except RuntimeError:
            return False

    def show_non_modal_message(
        self,
        title: str,
        message: str,
        warning: bool = False,
    ) -> None:
        """显示不会锁住主界面的提示窗口。"""
        icon = QtWidgets.QMessageBox.Icon.Warning if warning else QtWidgets.QMessageBox.Icon.Information

        box = QtWidgets.QMessageBox(
            icon,
            title,
            message,
            QtWidgets.QMessageBox.StandardButton.Ok,
            self,
        )

        box.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_DeleteOnClose,
            True,
        )

        self.external_scan_message_box = box

        box.destroyed.connect(
            lambda _obj=None: setattr(
                self,
                "external_scan_message_box",
                None,
            )
        )

        box.open()

    @QtCore.Slot(str, str)
    def fail_external_scan(
        self,
        work_dir: str,
        error_message: str,
    ) -> None:
        queue_dialog = self.external_scan_dialog

        if self.queue_dialog_is_visible(queue_dialog):
            queue_dialog.set_external_scan_busy(False)

            queue_dialog.refresh_queue_table()

        self.append_history(f"外部作业扫描失败：{work_dir}\n{error_message}")

        self.show_non_modal_message(
            "扫描外部作业",
            f"扫描失败：\n{error_message}",
            warning=True,
        )

        self.external_scan_dialog = None

    def build_queue_item_index(self) -> dict[tuple[str, str], QueueItem]:
        return external_build_queue_item_index(
            self.queue_items,
        )

    def append_external_scan_debug_log(
        self,
        *,
        job_name: str,
        job_work_dir: str,
        job: dict,
        runtime_status: str,
        runtime_message: str,
    ) -> None:
        if not ENABLE_EXTERNAL_SCAN_DEBUG_LOG:
            return

        self.append_history(
            "[EXTERNAL_SCAN]\n"
            f"job={job_name}\n"
            f"work_dir={job_work_dir}\n"
            f"solver_pids={job.get('solver_pids', [])}\n"
            f"related_pids={job.get('related_pids', [])}\n"
            f"lock_exists={job.get('lock_exists')}\n"
            f"lock_age_seconds={job.get('lock_age_seconds')}\n"
            f"diagnostics_status={job.get('diagnostics_status', '')}\n"
            f"runtime_status={runtime_status}\n"
            f"runtime_message={runtime_message}"
        )

    @QtCore.Slot(str, list, list)
    def finish_external_scan(
        self,
        work_dir: str,
        jobs: list,
        skipped: list,
    ) -> None:
        queue_dialog = self.external_scan_dialog
        merge_result = merge_external_scan_results(
            queue_items=self.queue_items,
            work_dir=work_dir,
            jobs=jobs,
        )
        added = int(merge_result["added"])
        updated = int(merge_result["updated"])
        status_only_updates = int(merge_result["status_only_updates"])

        for record in merge_result["memory_records"]:
            target_item = record["item"]
            job = record["job"]
            key = record["key"]
            job_work_dir = record["work_dir"]
            self.memory_monitor_service.update_external_job_estimate(
                job_name=target_item.job_name,
                rss_bytes=int(target_item.rss_bytes or 0),
                process_count=len(target_item.pids or []),
                process_names=", ".join(job.get("process_names") or []),
            )
            if target_item.status in scheduler_managed_active_statuses():
                ext_key = f"{key[0]}::{target_item.job_name.lower()}"
                self.memory_adapter.register_job(
                    job_key=ext_key,
                    job_name=target_item.job_name,
                    work_dir=target_item.external_work_dir or job_work_dir,
                )
                self.memory_adapter.activate_job(ext_key)

        for record in merge_result["debug_records"]:
            self.append_external_scan_debug_log(
                job_name=record["job_name"],
                job_work_dir=record["job_work_dir"],
                job=record["job"],
                runtime_status=record["runtime_status"],
                runtime_message=record["runtime_message"],
            )

        self.update_queue_status_label()
        if self.queue_dialog_is_visible(queue_dialog):
            queue_dialog.set_external_scan_busy(False)
            queue_dialog.refresh_queue_table()

        message = (
            f"扫描完成：发现 {len(jobs)} 个 Abaqus 作业，"
            f"新增 {added} 个，更新 {updated} 个，"
            f"仅更新状态 {status_only_updates} 个，"
            f"跳过 {len(skipped)} 个。"
        )

        self.append_history(message)

        self.show_non_modal_message(
            "扫描外部作业",
            message,
        )

        self.external_scan_dialog = None

    def start_queue(self) -> None:
        if self.queue_active:
            self.dispatch_queue()
            return

        pending = [item for item in self.queue_items if item.status == "待运行"]
        if not pending:
            QtWidgets.QMessageBox.information(self, "开始队列", "队列中没有待运行作业。")
            self.update_queue_status_label()
            return

        self.queue_active = True
        self.queue_stop_requested = False
        self.queue_existing_result_action = ""
        self.show_runtime_panel()
        self.append_history(f"开始队列：待运行 {len(pending)}，最大并行 {self.max_parallel_spin.value()}")
        self.update_queue_status_label()
        self.dispatch_queue()

    def stop_queue(
        self,
    ) -> None:
        """终止整个队列，并将已运行 Job 标记为正在终止。"""
        if not self.queue_active and not self.active_runs:
            return

        self.queue_stop_requested = True
        self.queue_active = False

        for item in self.queue_items:
            if item.status == "待运行":
                item.status = "已取消"
                item.message = "用户终止队列"

        for job_key, run in list(self.active_runs.items()):
            queue_item = run.get("queue_item")

            if queue_item is None:
                continue

            run["terminating"] = True
            run["terminating_at"] = time.time()

            queue_item.status = STATUS_TERMINATING

            queue_item.message = "正在终止"

            command = f"abaqus terminate job={run['job_name']}"

            self.append_history(f"终止队列作业：{command}")

            QtCore.QProcess.startDetached(
                ("cmd.exe" if os.name == "nt" else "/bin/sh"),
                (["/c", command] if os.name == "nt" else ["-lc", command]),
                run["work_dir"],
            )

        self.refresh_visible_queue_manager()
        self.update_queue_status_label()
        self.process_deferred_archives()

    def estimate_effective_available_slots(self) -> dict:
        manual_limit = self.max_parallel_spin.value()
        managed_active_count = len(
            scheduler_get_managed_active_job_keys(
                self.active_runs,
                self.queue_items,
            )
        )
        manual_available_slots = max(0, manual_limit - managed_active_count)
        available_memory = int(self.latest_system_memory.get("available") or 0)
        slot_estimate = self.memory_monitor_service.estimate_available_slots(
            available_memory=available_memory,
            usage_by_job=self.latest_memory_usage_by_job,
            active_job_names=scheduler_get_managed_active_job_names(
                self.active_runs,
                self.queue_items,
            ),
        )
        memory_available_slots = int(slot_estimate.slots) if available_memory > 0 else UNLIMITED_JOB_SLOTS
        effective_slots = min(manual_available_slots, memory_available_slots)
        signature = (
            manual_available_slots,
            memory_available_slots,
            effective_slots,
            managed_active_count,
        )
        if signature != self.last_effective_slot_signature:
            self.last_effective_slot_signature = signature
        return {
            "manual_limit": manual_limit,
            "managed_active_count": managed_active_count,
            "manual_available_slots": manual_available_slots,
            "memory_available_slots": memory_available_slots,
            "effective_available_slots": effective_slots,
            "slot_estimate": slot_estimate,
        }

    def dispatch_queue(self) -> None:
        self.refresh_queue_dependencies()
        if not self.queue_active or self.queue_stop_requested:
            self.update_queue_status_label()
            self.process_deferred_archives()
            return

        slot_info = self.estimate_effective_available_slots()
        available_slots = int(slot_info["effective_available_slots"])
        while available_slots > 0:
            item = next((entry for entry in self.queue_items if entry.status == "待运行"), None)
            if item is None:
                break

            options = queue_item_to_options(
                item,
                default_cpus=self.cpus_spin.value(),
            )
            ok, message = validate_options(options)
            if not ok:
                item.status = "运行失败"
                item.message = message
                self.append_history(f"队列作业校验失败：{item.job_name} | {message}")
                continue

            if self.start_job(
                options,
                queue_item=item,
                queue_mode=True,
            ):
                available_slots -= 1
            else:
                if item.status == "待运行":
                    item.status = "运行失败"
                    item.message = "启动失败"
            self.refresh_queue_dependencies()
            slot_info = self.estimate_effective_available_slots()
            available_slots = min(available_slots, int(slot_info["effective_available_slots"]))

        pending = any(item.status in {"待运行", "等待前置"} for item in self.queue_items)
        running = any(run.get("queue_item") is not None for run in self.active_runs.values())
        if not pending and not running:
            self.queue_active = False
            self.append_history("队列已结束。")
        self.update_queue_status_label()
        self.process_deferred_archives()

    def ask_existing_odb_action(self, job_name: str, odb_path: Path, queue_mode: bool) -> str:
        """Ask how to handle an existing ODB before submitting."""
        if queue_mode and self.queue_existing_result_action:
            return self.queue_existing_result_action

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("已有同名 ODB 文件")
        suffix = "\n\n队列模式下，本次选择会应用到后续同名 ODB 作业。" if queue_mode else ""
        box.setText(f"检测到作业 {job_name} 已存在同名 ODB 文件：\n\n{odb_path}\n\n请选择处理方式。{suffix}")
        overwrite_button = box.addButton("覆盖旧结果", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        backup_button = box.addButton("备份旧结果", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        cancel_button = box.addButton("取消提交", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(backup_button)
        box.exec()

        clicked = box.clickedButton()
        if clicked == overwrite_button:
            action = "overwrite"
        elif clicked == backup_button:
            action = "backup"
        else:
            action = "cancel"

        if queue_mode:
            self.queue_existing_result_action = action
            if action in ("overwrite", "backup"):
                self.append_history(f"队列同名 ODB 处理策略：{'覆盖' if action == 'overwrite' else '备份'}")
        return action

    def handle_existing_job_results(
        self,
        options: SubmitOptions,
        work_dir: str,
        queue_item: QueueItem | None,
        *,
        queue_mode: bool = False,
    ) -> tuple[bool, SubmitOptions, dict]:
        """Apply overwrite/backup handling for existing Abaqus result files."""
        job_name = options.job_name
        existing_odb = get_existing_odb_file(work_dir, job_name)
        if existing_odb is None:
            return True, options, {"action": "", "odb": "", "sta": ""}

        existing_lck = get_existing_lck_file(work_dir, job_name)
        if existing_lck is not None:
            QtWidgets.QMessageBox.warning(
                self,
                "作业可能仍在运行",
                f"检测到同名 LCK 文件，暂不提交：\n\n{existing_lck}\n\n"
                "请确认旧作业已经结束，或手动清理残留 LCK 文件后再提交。",
            )
            if queue_item is not None:
                queue_item.status = "运行失败"
                queue_item.message = "检测到同名 LCK 文件"
            return False, options, {"action": "lck", "odb": str(existing_odb), "sta": ""}

        action = self.ask_existing_odb_action(
            job_name,
            existing_odb,
            queue_mode,
        )
        if action == "cancel":
            self.append_history(f"取消提交：{job_name} 检测到同名 ODB。")
            if queue_item is not None:
                queue_item.status = "已取消"
                queue_item.message = "用户取消同名 ODB 处理"

            if queue_mode:
                self.queue_stop_requested = True
                self.queue_active = False

        try:
            if action == "backup":
                result = backup_existing_result_files(work_dir, job_name)
                if result.get("odb"):
                    self.append_history(f"已有结果处理：同名 ODB 已备份为：{result['odb']}")
                if result.get("sta"):
                    self.append_history(f"旧 STA 已备份为：{result['sta']}")
                return True, replace(options, ask_delete_off=True), {"action": action, **result}

            if action == "overwrite":
                result = delete_existing_result_files(work_dir, job_name)
                if result.get("odb"):
                    self.append_history(f"已有结果处理：同名 ODB 已删除：{result['odb']}")
                if result.get("sta"):
                    self.append_history(f"旧 STA 已删除：{result['sta']}")
                return True, replace(options, ask_delete_off=True), {"action": action, **result}
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "已有结果处理失败",
                f"无法处理旧结果文件：\n\n{exc}\n\n请确认 ODB/STA 没有被 Abaqus 或其他程序占用。",
            )
            if queue_item is not None:
                queue_item.status = "运行失败"
                queue_item.message = f"旧结果处理失败：{exc}"
            return False, options, {"action": action, "odb": str(existing_odb), "sta": ""}

        return False, options, {"action": action, "odb": str(existing_odb), "sta": ""}

    def start_job(
        self,
        options: SubmitOptions,
        queue_item: QueueItem | None = None,
        *,
        queue_mode: bool = False,
    ) -> bool:

        try:
            oldjob_source_dir = self.resolve_oldjob_source_dir(options, queue_item)
            options, workspace_info = prepare_calculation_workspace(options, queue_item, oldjob_source_dir)
        except OSError as exc:
            self.append_history(f"准备计算工作目录失败：{options.job_name}\n{exc}")
            if queue_item is not None:
                queue_item.status = "运行失败"
                queue_item.message = f"准备工作目录失败：{exc}"
            return False
        if workspace_info.get("copied_inp_path"):
            self.append_history(f"已复制 INP 到固态工作目录：{options.job_name}\n{workspace_info['copied_inp_path']}")
        if workspace_info.get("copied_oldjob_files"):
            self.append_history(
                f"已复制重启动依赖文件到固态工作目录：{options.job_name}\n"
                + "\n".join(workspace_info["copied_oldjob_files"])
            )

        job_key = f"{os.path.normcase(os.path.abspath(str(Path(options.inp_file).parent)))}::{options.job_name.lower()}"
        if job_key in self.active_runs:
            self.append_history(f"作业正在运行，跳过重复提交：{options.job_name}")
            return False

        inp_path = Path(options.inp_file)
        work_dir = str(inp_path.parent)
        handled, options, existing_result_info = self.handle_existing_job_results(
            options,
            work_dir,
            queue_item,
            queue_mode=queue_mode,
        )

        if not handled:
            self.update_queue_status_label()
            return False

        command = build_abaqus_command(options)

        run = {
            "key": job_key,
            "process": None,
            "timer": None,
            "work_dir": work_dir,
            "job_name": options.job_name,
            "command": command,
            "is_paused": False,
            "terminating": False,
            "terminating_at": 0.0,
            "sta_position": 0,
            "sta_state": {},
            "log": "",
            "queue_item": queue_item,
            "source_inp_path": workspace_info.get("source_inp_path", ""),
            "archive_dir": workspace_info.get("archive_dir", ""),
            "cleanup_after_archive": workspace_info.get("cleanup_after_archive", False),
            "existing_result_action": existing_result_info.get("action", ""),
            "backup_odb_path": existing_result_info.get("odb", "")
            if existing_result_info.get("action") == "backup"
            else "",
            "backup_sta_path": existing_result_info.get("sta", "")
            if existing_result_info.get("action") == "backup"
            else "",
            "memory_monitor_activated": False,
            "memory_stable_logged": False,
            "memory_current": 0,
            "memory_peak": 0,
            "memory_estimated": 0,
            "memory_monitor_mode": "learning",
            "memory_monitor_stable": False,
            "launcher_finished": False,
            "launcher_exit_code": None,
            "launcher_exit_status": None,
            "console_output": "",
            "console_failed": False,
            "console_failed_detail": "",
            "pre_started": False,
            "pre_finished": False,
            "standard_started": False,
            "package_started": False,
            "explicit_started": False,
            "seen_lck": False,
            "stable_no_lck_polls": 0,
            "submitted_at": time.time(),
            "finalized": False,
        }

        self.run_records[job_key] = run
        self.active_runs[job_key] = run
        self.refresh_job_selector()
        self.memory_adapter.register_job(
            job_key=job_key,
            job_name=options.job_name,
            work_dir=work_dir,
            monitor_mode="learning",
        )
        self.current_work_dir = work_dir
        self.current_job_name = options.job_name
        self.command_preview = command
        self.show_runtime_panel()
        self.select_run(job_key)
        self.append_history(f"提交作业：{options.job_name}\n{command}")

        if queue_item is not None:
            queue_item.status = "运行中"
            queue_item.message = "运行中"
            queue_item.active_job_key = job_key
            queue_item.calculation_work_dir = work_dir

            queue_item.source_inp_path = (
                workspace_info.get(
                    "source_inp_path",
                    "",
                )
                or queue_item.source_inp_path
                or options.inp_file
            )

            queue_item.archive_dir = (
                workspace_info.get(
                    "archive_dir",
                    "",
                )
                or queue_item.archive_dir
            )

            queue_item.cleanup_after_archive = bool(
                workspace_info.get(
                    "cleanup_after_archive",
                    False,
                )
            )

            self.refresh_visible_queue_manager()
            self.update_queue_status_label()

        if not self.runtime_controller.start_process(
            job_key=job_key,
            run=run,
            command=command,
        ):
            self.append_history(f"Abaqus 进程启动失败：{options.job_name}")

            self.active_runs.pop(
                job_key,
                None,
            )

            if queue_item is not None:
                queue_item.status = "运行失败"
                queue_item.message = "启动失败"
                queue_item.active_job_key = ""

            self.refresh_visible_queue_manager()
            self.update_queue_status_label()
            self.dispatch_queue()

            return False

        self.update_process_buttons(True)
        return True

    def read_process_output(
        self,
        job_key: str,
    ) -> None:
        self.runtime_controller.read_process_output(job_key)

    def on_process_error(self, job_key: str, error) -> None:  # noqa: ANN001
        run = self.active_runs.get(job_key)
        job_name = run["job_name"] if run else job_key
        self.append_history(f"{job_name} 进程错误：{error}")

    def on_runtime_job_updated(self, job_key: str) -> None:
        if job_key == self.selected_job_key():
            self.refresh_selected_run_status(job_key)
        self.refresh_visible_queue_manager()
        self.update_queue_status_label()

    def apply_memory_scan_result(self, payload: object) -> None:
        step_by_job_key = {job_key: run.get("current_step", "unknown") for job_key, run in self.active_runs.items()}
        result = self.memory_adapter.apply_scan_payload(
            payload,
            step_by_job_key=step_by_job_key,
            active_job_names=scheduler_get_managed_active_job_names(
                self.active_runs,
                self.queue_items,
            ),
        )
        if not result:
            return

        usage_by_job = result.get("usage_by_job") or {}
        self.latest_memory_usage_by_job = usage_by_job
        self.latest_system_memory = result.get("system_memory") or {}

        updated_item_ids: set[str] = set()
        for job_name, usage in usage_by_job.items():
            rss_bytes = int(usage.get("private_memory") or usage.get("working_set") or usage.get("rss_bytes") or 0)
            for run in self.active_runs.values():
                if (run.get("job_name") or "").lower() != str(job_name).lower():
                    continue
                run["memory_current"] = rss_bytes
                queue_item = run.get("queue_item")
                if queue_item is not None:
                    queue_item.rss_bytes = rss_bytes
                    updated_item_ids.add(queue_item.item_id)
            for item in self.queue_items:
                if (item.job_name or "").lower() != str(job_name).lower():
                    continue
                if item.status not in scheduler_managed_active_statuses():
                    continue
                item.rss_bytes = rss_bytes
                updated_item_ids.add(item.item_id)
        updated_jobs = cast(
            list[dict[str, Any]],
            result.get(
                "updated_jobs",
                [],
            )
            or [],
        )

        for updated_job in updated_jobs:
            job_key = str(
                updated_job.get(
                    "job_key",
                    "",
                )
            )

            run = self.run_records.get(job_key)

            if run is None:
                continue

            run["memory_current"] = safe_int(
                updated_job.get(
                    "rss_bytes",
                    0,
                )
            )

            run["memory_peak"] = safe_int(
                updated_job.get(
                    "peak_memory",
                    0,
                )
            )

            run["memory_estimated"] = safe_int(
                updated_job.get(
                    "estimated_memory",
                    0,
                )
            )

            run["memory_monitor_mode"] = str(
                updated_job.get(
                    "monitor_mode",
                    "learning",
                )
                or "learning"
            )

            run["memory_monitor_stable"] = bool(
                updated_job.get(
                    "stable",
                    False,
                )
            )

        self.refresh_selected_run_meta()

        if self.queue_manager_dialog is not None and self.queue_manager_dialog.isVisible():
            self.queue_manager_dialog.update_queue_memory_cells(updated_item_ids)

        self.update_queue_status_label()
        if self.queue_active:
            self.dispatch_queue()

    def on_memory_scan_failed(self, message: str) -> None:
        self.append_history(f"Memory scan failed: {message}")

    def on_memory_slot_estimate_changed(
        self,
        slot_estimate,
    ) -> None:  # noqa: ANN001
        """
        保存最新动态槽位估算。

        槽位估算不写入左侧运行监控，
        避免频繁刷新造成日志噪声。
        """
        self.latest_memory_slot_estimate = slot_estimate

    def on_process_finished(self, job_key: str, exit_code: int, exit_status) -> None:  # noqa: ANN001
        self.runtime_controller.on_process_finished(
            job_key,
            exit_code,
            exit_status,
        )

    def finalize_completed_run(self, job_key: str) -> None:
        run = self.active_runs.get(job_key)
        if not run or run.get("finalized"):
            return
        run["finalized"] = True
        run["end_time"] = time.time()
        timer = run.get("timer")
        if timer is not None:
            timer.stop()
        self.memory_adapter.finalize_job(job_key)
        status, detail = self.inspect_finished_job(job_key)
        status, detail = self.resolve_final_status_from_console(
            run,
            status,
            detail,
        )
        queue_item = run.get("queue_item")
        if queue_item is not None:
            if run.get(
                "terminating",
                False,
            ):
                queue_item.status = STATUS_TERMINATED

                queue_item.message = "用户手动终止"

            elif status == "完成":
                queue_item.status = STATUS_COMPLETED

                queue_item.message = detail or "计算完成"

            elif status == "终止":
                queue_item.status = STATUS_TERMINATED

                queue_item.message = detail or "检测到终止信息"

            elif status:
                queue_item.status = STATUS_FAILED

                queue_item.message = detail or status

            else:
                queue_item.status = STATUS_COMPLETED if (run.get("launcher_exit_code") == 0) else STATUS_FAILED

                queue_item.message = detail or (f"exit_code={run.get('launcher_exit_code')}")

            queue_item.active_job_key = ""
        self.archive_or_defer_finished_job(run)
        final_text = queue_item.status if queue_item is not None else (status or "finished")
        self.append_history(f"{run['job_name']} final status: {final_text}")
        self.notify_job_finished(
            run,
            final_text,
            queue_item.message if queue_item is not None else detail,
        )
        self.active_runs.pop(job_key, None)
        self.runtime_controller.unregister_run(job_key)
        self.refresh_job_selector()
        self.refresh_job_stats()
        self.refresh_selected_run_status(job_key)
        if self.current_job_key == job_key:
            self.status_label.setText(final_text)
            if self.active_runs:
                self.select_run(next(iter(self.active_runs)))
        self.update_process_buttons(bool(self.active_runs))
        self.refresh_visible_queue_manager()
        self.update_queue_status_label()
        self.process_deferred_archives()
        self.dispatch_queue()

    @staticmethod
    def resolve_final_status_from_console(
        run: dict,
        status: str,
        detail: str,
    ) -> tuple[str, str]:
        """Use cached launcher output when diagnostic files do not provide a final answer."""
        console_output = run.get("console_output", "")
        console_status = ""
        console_detail = ""

        if console_output:
            console_status, console_detail = classify_job_text(console_output)

        if run.get("console_failed") and status not in {"完成", "终止"}:
            return "失败", detail or run.get("console_failed_detail", "") or console_detail

        if not status and console_status:
            return console_status, console_detail

        return status, detail

    def notify_job_finished(
        self,
        run: dict,
        status: str,
        detail: str = "",
    ) -> None:
        """作业结束提醒。"""
        queue_item = run.get("queue_item")
        if queue_item is not None and not queue_item.complete_notify:
            return

        try:
            QtWidgets.QApplication.beep()
        except RuntimeError:
            pass

        submitted_at = float(run.get("submitted_at", 0.0) or 0.0)
        end_time = float(run.get("end_time", 0.0) or time.time())
        elapsed_text = format_elapsed_seconds(end_time - submitted_at) if submitted_at > 0 else "未知"
        detail_text = f"\n{detail}" if detail else ""

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("作业结束")
        box.setIcon(
            QtWidgets.QMessageBox.Icon.Information if status == STATUS_COMPLETED else QtWidgets.QMessageBox.Icon.Warning
        )
        box.setText(f"作业：{run.get('job_name', '')}\n状态：{status}\n耗时：{elapsed_text}{detail_text}")
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        box.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.job_notification_boxes.append(box)
        box.finished.connect(
            lambda _result, message_box=box: (
                self.job_notification_boxes.remove(message_box) if message_box in self.job_notification_boxes else None
            )
        )
        box.open()

    def poll_sta_file(self, job_key: str | None = None) -> None:
        if job_key is None:
            job_key = self.selected_job_key()
        if not job_key:
            return
        self.runtime_controller.poll_sta_file(job_key)

    def inspect_finished_job(self, job_key: str) -> tuple[str, str]:
        run = self.active_runs.get(job_key)
        if not run:
            return "", ""
        try:
            status, detail = inspect_job_files(run["work_dir"], run["job_name"])
        except Exception as exc:
            self.append_history(f"诊断作业文件失败：{exc}")
            return "", str(exc)
        if status or detail:
            self.append_history(f"诊断结果：{status or '未知'} {detail or ''}".strip())
        return status, detail

    def refresh_queue_dependencies(self) -> None:
        previous = {item.item_id: (item.status, item.message) for item in self.queue_items}
        scheduler_refresh_queue_dependencies(self.queue_items)
        for item in self.queue_items:
            old_status, old_message = previous.get(
                item.item_id,
                ("", ""),
            )
            if (
                item.status == "运行失败"
                and item.status != old_status
                and item.message != old_message
                and item.message.startswith("前置作业未完成，跳过重启动：")
            ):
                self.append_history(f"跳过重启动作业：{item.job_name}\n{item.message}")

    def resolve_oldjob_source_dir(self, options: SubmitOptions, queue_item: QueueItem | None) -> str:
        oldjob_name = derive_oldjob_name(options.oldjob_path)
        if not oldjob_name and queue_item is not None:
            oldjob_name = scheduler_oldjob_name_from_item(queue_item)
        if not oldjob_name:
            return ""

        dependency = scheduler_find_queue_oldjob_item(
            oldjob_name,
            self.queue_items,
            queue_item,
        )
        if dependency is not None and dependency.status == "已完成":
            for run in reversed(list(self.run_records.values())):
                if (run.get("job_name") or "").lower() != dependency.job_name.lower():
                    continue
                work_dir = (run.get("work_dir") or "").strip()
                if work_dir and (Path(work_dir) / f"{dependency.job_name}.odb").exists():
                    return work_dir

        candidate_paths = [options.oldjob_path]
        if queue_item is not None:
            candidate_paths.extend(
                [
                    queue_item.oldjob_path,
                    str(Path(queue_item.oldjob_dir) / f"{oldjob_name}.odb") if queue_item.oldjob_dir else "",
                ]
            )
        for raw_path in candidate_paths:
            if not raw_path:
                continue
            path = Path(raw_path)
            if path.suffix.lower() != ".odb":
                continue
            if path.stem.lower() != oldjob_name.lower():
                continue
            if path.exists():
                return str(path.parent)
        return ""

    def archive_or_defer_finished_job(self, run: dict) -> None:
        outcome = ArchiveCoordinator(
            self.queue_items,
            self.deferred_archive_runs,
        ).archive_or_defer_run(run)
        if outcome["action"] == "deferred":
            self.append_history(
                f"暂缓归档：{run['job_name']} 仍被重启动作业依赖，等待：{run['archive_deferred_reason']}"
            )
            return
        self.handle_archive_result(
            run,
            outcome["result"],
        )

    def process_deferred_archives(self) -> None:
        processed = ArchiveCoordinator(
            self.queue_items,
            self.deferred_archive_runs,
        ).process_deferred_archives()
        for item in processed:
            run = item["run"]
            self.append_history(f"依赖作业已结束，开始归档：{run['job_name']}")
            self.handle_archive_result(
                run,
                item["result"],
            )

    def archive_finished_job(self, run: dict) -> None:
        result = ArchiveCoordinator(
            self.queue_items,
            self.deferred_archive_runs,
        ).archive_run(run)
        self.handle_archive_result(
            run,
            result,
        )

    def handle_archive_result(self, run: dict, result: dict) -> None:
        if result.get("exception"):
            self.append_history(f"归档结果文件失败：{run['job_name']}\n{result['error']}")
            return
        if not result.get("status"):
            return
        if result.get("message"):
            self.append_history(result["message"])
        if result.get("error"):
            self.append_history(f"归档过程中存在错误：\n{result['error']}")

    def mark_archive_result(self, run: dict, status: str, error: str) -> None:
        ArchiveCoordinator(
            self.queue_items,
            self.deferred_archive_runs,
        ).mark_archive_result(
            run,
            status,
            error,
        )

    def toggle_pause_resume(
        self,
    ) -> None:
        """
        暂停 / 恢复当前选中作业。

        使用一个按钮切换，保持右侧按钮排版接近旧版。
        """
        run = self.active_runs.get(self.selected_job_key())

        if run is None:
            return

        is_paused = bool(
            run.get(
                "is_paused",
                False,
            )
        )

        if is_paused:
            self.resume_job()

            run["is_paused"] = False

            self.refresh_pause_button_style(False)

            self.refresh_selected_run_status(run["key"])

            return

        self.suspend_job()

        run["is_paused"] = True

        self.refresh_pause_button_style(True)

        self.refresh_selected_run_status(run["key"])

    def terminate_job(
        self,
    ) -> None:
        """
        手动终止当前 Job。

        发送 terminate 命令后，不立即判定失败；
        等待 LCK 释放，再统一标记为已终止。
        """
        job_key = self.selected_job_key()

        run = self.active_runs.get(job_key)

        if run is None:
            return

        if run.get(
            "finalized",
            False,
        ):
            return

        queue_item = run.get("queue_item")

        if queue_item is not None:
            queue_item.status = STATUS_TERMINATING

            queue_item.message = "正在终止"

        self.status_label.setText("Terminating")

        self.pause_btn.setEnabled(False)

        self.runtime_controller.terminate_job(job_key)

        self.refresh_visible_queue_manager()
        self.update_queue_status_label()

    def suspend_job(self) -> None:
        self.runtime_controller.suspend_job(self.selected_job_key())

    def resume_job(self) -> None:
        self.runtime_controller.resume_job(self.selected_job_key())

    def run_abaqus_control(self, action: str) -> None:
        self.runtime_controller.run_abaqus_control(
            self.selected_job_key(),
            action,
        )

    def open_work_dir(self) -> None:
        run = self.run_records.get(self.selected_job_key())
        work_dir = run.get("archive_destination") if run else ""
        if not work_dir:
            work_dir = run["work_dir"] if run else self.current_work_dir
        if not work_dir:
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(work_dir))

    def select_runtime_log_file(self, suffix: str) -> None:
        self.current_log_suffix = suffix
        self.update_sta_sticky_header_visibility()
        self.open_job_file(suffix)

    def open_job_file(self, suffix: str) -> None:
        run = self.run_records.get(self.selected_job_key())
        if not run:
            return
        path = Path(run["work_dir"]) / f"{run['job_name']}{suffix}"
        if not path.exists() and run.get("archive_destination"):
            path = Path(run["archive_destination"]) / f"{run['job_name']}{suffix}"
        if not path.exists():
            QtWidgets.QMessageBox.information(self, suffix.upper().lstrip("."), f"文件不存在：\n{path}")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def update_sta_sticky_header_visibility(self) -> None:
        if not hasattr(self, "sta_sticky_header_label"):
            return

        should_show = False

        if self.current_log_suffix == ".sta" and self.selected_job_key():
            scroll_bar = self.job_log.verticalScrollBar()
            if scroll_bar.maximum() > 0:
                header_text = build_sta_table_header()
                document = self.job_log.document()
                cursor = document.find(header_text)

                if not cursor.isNull():
                    header_rect = self.job_log.cursorRect(cursor)
                    should_show = header_rect.bottom() < 0

        self.sta_sticky_header_label.setVisible(should_show)

    # ---------- Helpers ----------
    def calculate_runtime_panel_min_width(
        self,
    ) -> int:
        """
        Calculate the minimum runtime panel width.

        The visible text area of job_log must fit
        RUNTIME_LOG_WIDTH_SAMPLE even when the
        vertical scrollbar is visible.
        """
        self.job_log.ensurePolished()

        font_metrics = QtGui.QFontMetrics(self.job_log.font())

        sample_text_width = font_metrics.horizontalAdvance(RUNTIME_LOG_WIDTH_SAMPLE)

        document_margin = int(self.job_log.document().documentMargin() * 2)

        log_frame_width = self.job_log.frameWidth() * 2

        vertical_scrollbar_width = self.job_log.style().pixelMetric(
            QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent,
            None,
            self.job_log,
        )
        vertical_scrollbar_width = max(
            vertical_scrollbar_width,
            self.job_log.verticalScrollBar().sizeHint().width(),
        )

        runtime_body_frame_width = self.runtime_body_frame.frameWidth() * 2

        runtime_log_frame_width = self.runtime_log_frame.frameWidth() * 2

        wrap_safety_width = font_metrics.horizontalAdvance("MM")

        log_widget_min_width = (
            sample_text_width + document_margin + log_frame_width + vertical_scrollbar_width + wrap_safety_width
        )

        return (
            log_widget_min_width + runtime_log_frame_width + RUNTIME_BODY_HORIZONTAL_MARGIN + runtime_body_frame_width
        )

    def apply_runtime_panel_width_baseline(
        self,
    ) -> None:
        """
        应用运行详情区宽度下限。

        左侧保持固定宽度；
        右侧最窄时仍可以完整显示基准分隔符。
        """
        right_panel_min_width = self.calculate_runtime_panel_min_width()

        self.right_panel.setMinimumWidth(right_panel_min_width)

        full_window_min_width = (
            LEFT_PANEL_MIN_WIDTH + right_panel_min_width + WINDOW_OUTER_HORIZONTAL_MARGIN + PANEL_HORIZONTAL_SPACING
        )

        if self.right_panel.isHidden():
            self.setMinimumWidth(COMPACT_WINDOW_MIN_WIDTH)

            return

        self.setMinimumWidth(full_window_min_width)

        if self.width() < full_window_min_width:
            self.resize(
                full_window_min_width,
                self.height(),
            )

    def show_runtime_panel(
        self,
    ) -> None:
        """
        显示右侧运行区，并根据日志框字体应用宽度下限。
        """
        if self.right_panel.isHidden():
            self.right_panel.show()

        self.apply_runtime_panel_width_baseline()

    def refresh_job_selector(
        self,
    ) -> None:
        """刷新右侧 Job 选择器，并尽量保留当前选择。"""
        current_key = self.current_job_key

        self.job_selector.blockSignals(True)

        try:
            self.job_selector.clear()

            for job_key, run in self.run_records.items():
                self.job_selector.addItem(
                    str(
                        run.get(
                            "job_name",
                            job_key,
                        )
                    ),
                    job_key,
                )

            if current_key:
                index = self.job_selector.findData(current_key)

                if index >= 0:
                    self.job_selector.setCurrentIndex(index)

        finally:
            self.job_selector.blockSignals(False)

        self.refresh_job_stats()

    def on_job_selector_changed(
        self,
        index: int,
    ) -> None:
        """切换右侧当前显示的作业。"""
        if index < 0:
            return

        job_key = self.job_selector.itemData(index)

        if job_key:
            self.select_run(str(job_key))

    def refresh_job_stats(
        self,
    ) -> None:
        """更新右侧顶部：运行中 / 完成 / 异常统计。"""
        running = 0
        completed = 0
        failed = 0

        for job_key, run in self.run_records.items():
            if job_key in self.active_runs:
                running += 1
                continue

            queue_item = run.get("queue_item")

            status = queue_item.status if queue_item is not None else ""

            if status == STATUS_COMPLETED:
                completed += 1

            elif status:
                failed += 1

        self.job_stats_label.setText(f"运行中 {running} | 完成 {completed} | 异常 {failed}")

    def refresh_selected_run_status(
        self,
        job_key: str | None = None,
    ) -> None:
        """刷新当前 Job 的标题、状态和概要信息。"""
        job_key = job_key or self.selected_job_key()

        if not job_key:
            self.current_job_title_label.setText("Job: 未选择")

            self.status_label.setText("状态：未运行")

            self.job_meta.setPlainText("尚未提交作业。")

            self.update_sta_sticky_header_visibility()
            return

        run = self.run_records.get(job_key)

        if run is None:
            return

        self.current_job_title_label.setText(f"Job: {run.get('job_name', '')}")

        self.status_label.setText(format_run_status(run))

        self.refresh_selected_run_meta(job_key)

    def refresh_selected_run_meta(
        self,
        job_key: str | None = None,
    ) -> None:
        """刷新当前 Job 的概要信息区。"""
        job_key = job_key or self.selected_job_key()

        run = self.run_records.get(job_key or "")

        if run is None:
            self.job_meta.setPlainText("尚未提交作业。")

            return

        queue_item = run.get("queue_item")

        source_inp_path = run.get(
            "source_inp_path",
            "",
        ) or (queue_item.source_inp_path if queue_item is not None else "")

        cores = queue_item.cores if queue_item is not None else ""

        memory = queue_item.memory if (queue_item is not None and queue_item.memory) else "默认"

        datacheck = "是" if (queue_item is not None and queue_item.datacheck_only) else "否"

        current_memory = safe_int(
            run.get(
                "memory_current",
                0,
            )
            or (queue_item.rss_bytes if queue_item is not None else 0)
        )

        peak_memory = safe_int(
            run.get(
                "memory_peak",
                0,
            )
        )

        estimated_memory = safe_int(
            run.get(
                "memory_estimated",
                0,
            )
        )

        monitor_mode = str(
            run.get(
                "memory_monitor_mode",
                "learning",
            )
            or "learning"
        )

        monitor_mode_text = {
            "learning": "高频学习",
            "patrol": "低频巡检",
        }.get(
            monitor_mode,
            monitor_mode,
        )

        memory_table_lines = build_memory_summary_table(
            current_memory_text=(format_memory_size(current_memory) if current_memory > 0 else "未统计"),
            peak_memory_text=(format_memory_size(peak_memory) if peak_memory > 0 else "未统计"),
            estimated_memory_text=(format_memory_size(estimated_memory) if estimated_memory > 0 else "未统计"),
            monitor_mode_text=(monitor_mode_text),
        )

        lines = [
            f"工作目录: {run.get('work_dir', '')}",
            f"INP 文件: {source_inp_path}",
            f"作业名称: {run.get('job_name', '')}",
            f"核心数: {cores}",
            f"提交内存参数: {memory}",
            *memory_table_lines,
            f"Datacheck: {datacheck}",
        ]

        backup_odb_path = run.get(
            "backup_odb_path",
            "",
        )

        if backup_odb_path:
            lines.append(f"旧 ODB: {backup_odb_path}")

        backup_sta_path = run.get(
            "backup_sta_path",
            "",
        )

        if backup_sta_path:
            lines.append(f"旧 STA: {backup_sta_path}")

        self.job_meta.setPlainText("\n".join(lines))

    def selected_job_key(self) -> str:
        if self.current_job_key:
            return self.current_job_key
        if self.active_runs:
            return next(iter(self.active_runs))
        if self.run_records:
            return next(reversed(self.run_records))
        return ""

    def select_run(
        self,
        job_key: str,
    ) -> None:
        run = self.run_records.get(job_key)

        if run is None:
            return

        self.current_job_key = job_key

        self.current_work_dir = str(
            run.get(
                "work_dir",
                "",
            )
        )

        self.current_job_name = str(
            run.get(
                "job_name",
                "",
            )
        )

        index = self.job_selector.findData(job_key)

        if index >= 0 and index != self.job_selector.currentIndex():
            self.job_selector.blockSignals(True)

            self.job_selector.setCurrentIndex(index)

            self.job_selector.blockSignals(False)

        self.refresh_selected_run_status(job_key)

        self.job_log.setPlainText(
            str(
                run.get(
                    "log",
                    "",
                )
            )
        )

        self.job_log.verticalScrollBar().setValue(self.job_log.verticalScrollBar().maximum())

        self.update_sta_sticky_header_visibility()

        self.update_process_buttons(job_key in self.active_runs)

    def update_queue_status_label(self) -> None:
        pending = sum(1 for item in self.queue_items if item.status == "待运行")
        running = sum(1 for item in self.queue_items if item.status == "运行中")
        completed = sum(1 for item in self.queue_items if item.status == "已完成")
        failed = sum(1 for item in self.queue_items if item.status == "运行失败")
        cancelled = sum(1 for item in self.queue_items if item.status in ("已取消", "已终止"))
        if not self.queue_items:
            self.queue_status_label.setText("队列：未生成")
            return
        self.queue_status_label.setText(
            f"队列：{len(self.queue_items)} 个 | 运行 {running} | 等待 {pending} | 完成 {completed} | 失败 {failed} | 取消 {cancelled}"
        )

    def update_abaqus_status(self) -> None:
        abaqus_path = shutil.which("abaqus")
        if abaqus_path:
            self.abaqus_status_label.setText("Abaqus 状态：已检测到 Abaqus")
        else:
            self.abaqus_status_label.setText("Abaqus 状态：未在 PATH 中找到")

    def append_history(self, text: str) -> None:
        """追加运行记录：时间戳为蓝色，正文使用默认深灰色。"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor = self.history.textCursor()

        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)

        if not self.history.document().isEmpty():
            cursor.insertText("\n")

        timestamp_format = QtGui.QTextCharFormat()

        timestamp_format.setForeground(QtGui.QColor(PRIMARY))

        timestamp_format.setFontWeight(QtGui.QFont.Weight.DemiBold)

        body_format = QtGui.QTextCharFormat()

        body_format.setForeground(QtGui.QColor(TEXT))

        cursor.insertText(
            f"[{timestamp}]\n",
            timestamp_format,
        )

        cursor.insertText(
            text,
            body_format,
        )

        self.history.setTextCursor(cursor)

        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

    def append_job_log(
        self,
        text: str,
        job_key: str | None = None,
    ) -> None:
        """
        按原始文本追加日志。

        不使用 appendPlainText()，
        避免每次 STA 轮询批次之间自动增加额外空行。
        """
        if not text:
            return

        normalized_text = text.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
        )

        job_key = job_key or self.selected_job_key()

        run = self.run_records.get(job_key)

        if run is not None:
            current = str(
                run.get(
                    "log",
                    "",
                )
            )

            separator = "" if (not current or current.endswith("\n") or normalized_text.startswith("\n")) else "\n"

            run["log"] = current + separator + normalized_text

        if not job_key or job_key == self.selected_job_key():
            cursor = self.job_log.textCursor()

            cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)

            existing_text = self.job_log.toPlainText()

            separator = (
                "" if (not existing_text or existing_text.endswith("\n") or normalized_text.startswith("\n")) else "\n"
            )

            cursor.insertText(separator + normalized_text)

            self.job_log.setTextCursor(cursor)

            self.job_log.verticalScrollBar().setValue(self.job_log.verticalScrollBar().maximum())

            self.update_sta_sticky_header_visibility()

    def refresh_pause_button_style(
        self,
        is_paused: bool,
    ) -> None:
        """根据运行状态切换暂停 / 恢复按钮的文字和颜色。"""
        if is_paused:
            self.pause_btn.setText("恢复")

            self.pause_btn.setObjectName("resume")

        else:
            self.pause_btn.setText("暂停")

            self.pause_btn.setObjectName("warning")

        self.pause_btn.style().unpolish(self.pause_btn)

        self.pause_btn.style().polish(self.pause_btn)

        self.pause_btn.update()

    def update_process_buttons(
        self,
        running: bool,
    ) -> None:
        """根据当前 Job 状态更新右侧操作按钮。"""
        self.submit_btn.setEnabled(not running)

        self.stop_btn.setEnabled(running)

        self.pause_btn.setEnabled(running)

        run = self.active_runs.get(self.selected_job_key())

        self.refresh_pause_button_style(
            bool(
                run is not None
                and run.get(
                    "is_paused",
                    False,
                )
            )
        )

    def closeEvent(
        self,
        event,
    ) -> None:
        """关闭窗口前停止后台内存监测。"""
        self.memory_adapter.stop()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    """Run the Qt frontend."""
    argv = list(sys.argv if argv is None else argv)
    app = QtWidgets.QApplication(argv)
    app.setApplicationName(APP_TITLE)
    QtWidgets.QApplication.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
