"""Qt main window for the Abaqus submitter."""

from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from .abaqus_diagnostics import (
    build_sta_table_header,
    inspect_job_files,
)
from .archive import (
    ArchiveCoordinator,
    ArchiveMoveService,
    ArchiveMoveTask,
)
from .workspace_prepare import (
    WorkspacePrepareService,
    WorkspacePrepareTask,
)
from .constants import (
    ACTIVE_STATUSES,
    DEFAULT_CPUS,
    FORMAL_QUEUE_SAVE_DEBOUNCE_MS,
    JOB_MEMORY_BASE_SAFETY_FACTOR,
    JOB_MEMORY_LEARNING_INTERVAL_MS,
    JOB_MEMORY_MAX_SAMPLES,
    JOB_MEMORY_MIN_SAMPLES,
    JOB_MEMORY_PATROL_INTERVAL_MS,
    JOB_MEMORY_STABLE_POLLS,
    JOB_MEMORY_STABLE_RELATIVE_DELTA,
    LOG_SEPARATOR_WIDTH,
    MAX_CPUS,
    MAX_HISTORY_LOG_LINES,
    MAX_JOB_LOG_LINES,
    STATUS_CANCELED,
    STATUS_COMPLETED,
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
    inp_has_restart_keyword,
    validate_options,
)
from .diagnostics import StartupTimeline, external_scan_debug_enabled, hang_probe_function
from .external_jobs import (
    ExternalJobCoordinator,
    ExternalJobScanWorker,
    build_queue_item_index as external_build_queue_item_index,
    collect_known_external_jobs as external_collect_known_external_jobs,
    merge_external_scan_results,
)
from .job_controller import JobController
from .job_runtime import JobRuntimeController
from .qt_compat import QtCore, QtGui, QtWidgets, Signal
from .queue_manager import QueueManagerDialog, load_joblist_state, save_joblist_state
from .queue_scheduler import (
    find_queue_item_by_key as scheduler_find_queue_item_by_key,
    find_queue_oldjob_item as scheduler_find_queue_oldjob_item,
    get_managed_active_job_keys as scheduler_get_managed_active_job_keys,
    get_managed_active_job_names as scheduler_get_managed_active_job_names,
    managed_active_statuses as scheduler_managed_active_statuses,
    oldjob_name_from_item as scheduler_oldjob_name_from_item,
    queue_item_is_finished as scheduler_queue_item_is_finished,
    queue_status_counts as scheduler_queue_status_counts,
    refresh_queue_dependencies as scheduler_refresh_queue_dependencies,
    submit_conflict_key as scheduler_submit_conflict_key,
)
from .ui_components import (
    duplicated_runtime_job_names as ui_duplicated_runtime_job_names,
    FilePickerRow,
    format_elapsed_seconds,
    format_run_status,
    runtime_job_display_label as ui_runtime_job_display_label,
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
    build_runtime_selector_stylesheet,
)

QUEUE_DISPATCH_DEBOUNCE_MS = 50


class AbaqusPathCheckWorker(QtCore.QObject):
    finished = Signal(str)

    def run(self) -> None:
        self.finished.emit(shutil.which("abaqus") or "")


class RuntimeSelectorDelegate(QtWidgets.QStyledItemDelegate):
    """Paint runtime selector popup rows without hiding status background colors."""

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        background = index.data(QtCore.Qt.ItemDataRole.BackgroundRole)
        if isinstance(background, QtGui.QBrush):
            background_color = background.color()
        elif isinstance(background, QtGui.QColor):
            background_color = background
        else:
            background_color = QtGui.QColor("#e2e8f0")

        foreground = index.data(QtCore.Qt.ItemDataRole.ForegroundRole)
        if isinstance(foreground, QtGui.QBrush):
            foreground_color = foreground.color()
        elif isinstance(foreground, QtGui.QColor):
            foreground_color = foreground
        else:
            foreground_color = QtGui.QColor("#0f172a")

        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.StateFlag.State_MouseOver)
        row_rect = option.rect
        text_rect = row_rect.adjusted(10, 0, -10, 0)

        painter.save()
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.fillRect(row_rect, background_color)

        if hovered or selected:
            overlay = QtGui.QColor(255, 255, 255, 48 if hovered else 28)
            painter.fillRect(row_rect, overlay)
            painter.setPen(QtGui.QPen(background_color.darker(118 if hovered else 132), 1))
            painter.drawLine(row_rect.bottomLeft(), row_rect.bottomRight())

        text = str(index.data(QtCore.Qt.ItemDataRole.DisplayRole) or "")
        painter.setPen(foreground_color)
        painter.drawText(text_rect, QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtCore.QSize:
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 26))
        return size


class ArrowComboBox(QtWidgets.QComboBox):
    """Combo box with a painted arrow that does not depend on theme assets."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.view().setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.view().setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.view().setWindowFlag(QtCore.Qt.WindowType.NoDropShadowWindowHint, True)

    def showPopup(self) -> None:
        view = self.view()
        view.setWindowFlag(QtCore.Qt.WindowType.NoDropShadowWindowHint, True)
        view.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, False)
        super().showPopup()
        QtCore.QTimer.singleShot(0, self.position_popup_below)

    def position_popup_below(self) -> None:
        view = self.view()
        popup = view.window()
        if popup is None:
            return

        popup.setWindowFlag(QtCore.Qt.WindowType.NoDropShadowWindowHint, True)
        popup.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, False)

        top_left = self.mapToGlobal(QtCore.QPoint(0, self.height()))
        screen = QtGui.QGuiApplication.screenAt(top_left) or self.screen()
        available = screen.availableGeometry() if screen is not None else QtCore.QRect()
        row_count = max(1, self.count())
        row_height = max(view.sizeHintForRow(0), 26)
        content_height = row_count * row_height + 2
        if available.isValid():
            available_below = max(row_height + 2, available.bottom() - top_left.y() + 1)
            content_height = min(content_height, available_below)
        popup_width = max(self.width(), view.sizeHintForColumn(0) + 24)
        popup.setGeometry(top_left.x(), top_left.y(), popup_width, content_height)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QtGui.QColor("#475569"))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        center_x = self.width() - 13
        center_y = self.height() // 2 + 1
        painter.drawPolygon(
            QtGui.QPolygon(
                [
                    QtCore.QPoint(center_x - 4, center_y - 2),
                    QtCore.QPoint(center_x + 4, center_y - 2),
                    QtCore.QPoint(center_x, center_y + 3),
                ]
            )
        )


class MainWindow(QtWidgets.QMainWindow):
    """Qt version of the main submitter layout."""

    def __init__(self):
        super().__init__()
        self._startup_timeline = StartupTimeline("MainWindow")
        self._startup_timeline.mark("main-init-start")
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
        self.candidate_queue_items: list[QueueItem] = []
        self.joblist_load_error = ""
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
        self.external_scan_show_summary = True
        self.external_scan_reason = "manual"
        self.external_job_coordinator = ExternalJobCoordinator(self)
        self._start_queue_after_restore_scan = False
        self._restored_status_scan_after_queue_open_scheduled = False
        self.abaqus_status_thread: QtCore.QThread | None = None
        self.abaqus_status_worker: AbaqusPathCheckWorker | None = None
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
        self.runtime_controller.processError.connect(self.on_process_error)
        self.last_effective_slot_signature: tuple[int, int, int, int] | None = None
        self._closing = False
        self._dispatch_pending = False
        self._dispatch_running = False
        self._dispatch_timer = QtCore.QTimer(self)
        self._dispatch_timer.setSingleShot(True)
        self._dispatch_timer.timeout.connect(self._run_scheduled_dispatch_queue)
        self._joblist_save_timer = QtCore.QTimer(self)
        self._joblist_save_timer.setSingleShot(True)
        self._joblist_save_timer.setInterval(FORMAL_QUEUE_SAVE_DEBOUNCE_MS)
        self._joblist_save_timer.timeout.connect(self.save_joblist_state_now)
        self._ui_heartbeat_last = time.monotonic()
        self._ui_heartbeat_timer = QtCore.QTimer(self)
        self._ui_heartbeat_timer.setInterval(1000)
        self._ui_heartbeat_timer.timeout.connect(self.on_ui_heartbeat)
        self._ui_heartbeat_timer.start()
        self.workspace_prepare_service = WorkspacePrepareService(self)
        self._workspace_prepare_contexts: dict[str, dict] = {}
        self.archive_move_service = ArchiveMoveService(self)
        self._archive_move_contexts: dict[str, dict] = {}
        self._archive_move_reserved_keys: set[tuple[str, str]] = set()
        self.job_controller = JobController(self)
        self.runtime_controller.jobFinished.connect(self.finalize_completed_run)
        self.workspace_prepare_service.succeeded.connect(self.on_workspace_prepare_succeeded)
        self.workspace_prepare_service.failed.connect(self.on_workspace_prepare_failed)
        self.archive_move_service.succeeded.connect(self.on_archive_move_succeeded)
        self.archive_move_service.blocked.connect(self.on_archive_move_blocked)
        self.archive_move_service.failed.connect(self.on_archive_move_failed)
        self._startup_timeline.mark("services-ready")

        self.restore_joblist_state()
        self._startup_timeline.mark(
            "restore-joblist-state",
            candidates=len(self.candidate_queue_items),
            queue=len(self.queue_items),
        )

        self.build_ui()
        self._startup_timeline.mark("build-ui")
        self.apply_styles()
        self._startup_timeline.mark("apply-styles")

        QtCore.QTimer.singleShot(250, self.start_abaqus_status_check)
        self.update_command_preview()
        self._startup_timeline.mark("command-preview")
        self.append_history("等待提交作业...")
        if self.joblist_load_error:
            self.append_history(f"读取 joblist.json 失败：{self.joblist_load_error}")
        elif self.candidate_queue_items or self.queue_items:
            self.append_history(
                f"已恢复队列记录：候选 {len(self.candidate_queue_items)}，正式 {len(self.queue_items)}"
            )
            self.update_queue_status_label()
        self._startup_timeline.mark("main-init-done")

    # ---------- UI ----------

    def on_ui_heartbeat(self) -> None:
        now = time.monotonic()
        self._ui_heartbeat_last = now

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
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(11)

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
        core_label.setFixedWidth(38)
        settings.addWidget(core_label)
        self.cpus_spin = QtWidgets.QSpinBox()
        self.cpus_spin.setObjectName("plainSpin")
        self.cpus_spin.setRange(0, MAX_CPUS)
        self.cpus_spin.setValue(DEFAULT_CPUS)
        self.cpus_spin.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.cpus_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.cpus_spin.setFixedSize(52, 30)
        settings.addWidget(self.cpus_spin)
        settings.addStretch(1)
        settings.addWidget(QtWidgets.QLabel("Mem"))
        self.memory_value = QtWidgets.QLineEdit()
        self.memory_value.setObjectName("submitParamEdit")
        self.memory_value.setText("90")
        self.memory_value.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.memory_value.setFixedSize(52, 30)
        self.memory_unit = ArrowComboBox()
        self.memory_unit.setObjectName("submitParamCombo")
        self.memory_unit.addItems(MEMORY_OPTIONS)
        self.memory_unit.setCurrentText("%")
        self.memory_unit.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.memory_unit.setVisible(False)
        memory_percent_label = QtWidgets.QLabel("%")
        memory_percent_label.setObjectName("unitBadge")
        memory_percent_label.setFixedSize(34, 30)
        memory_percent_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        settings.addWidget(self.memory_value)
        settings.addWidget(memory_percent_label)
        settings.addStretch(1)
        queue_parallel_label = QtWidgets.QLabel("并行上限")
        queue_parallel_label.setObjectName("hint")
        settings.addWidget(queue_parallel_label)
        self.max_parallel_spin = QtWidgets.QSpinBox()
        self.max_parallel_spin.setObjectName("queueMaxParallelSpin")
        self.max_parallel_spin.setRange(1, 999)
        self.max_parallel_spin.setValue(calculate_default_joblist_parallel(DEFAULT_CPUS))
        self.max_parallel_spin.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.max_parallel_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.max_parallel_spin.setFixedSize(64, 30)
        self.max_parallel_spin.setToolTip("队列中允许同时运行的最大作业数")
        settings.addWidget(self.max_parallel_spin)
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

        action_rows = QtWidgets.QVBoxLayout()
        action_rows.setSpacing(8)
        single_file_actions = QtWidgets.QHBoxLayout()
        single_file_actions.setSpacing(8)
        queue_actions = QtWidgets.QHBoxLayout()
        queue_actions.setSpacing(8)
        self.preview_btn = QtWidgets.QPushButton("预览命令")
        self.submit_btn = QtWidgets.QPushButton("提交作业")
        self.queue_btn = QtWidgets.QPushButton("管理队列")
        self.start_queue_btn = QtWidgets.QPushButton("开始队列")
        self.stop_queue_btn = QtWidgets.QPushButton("终止队列")
        action_button_width = 120
        action_spacing = 8
        single_file_button_width = int((action_button_width * 3 + action_spacing * 2 - action_spacing) / 2)
        for button in (self.queue_btn, self.start_queue_btn, self.stop_queue_btn):
            button.setFixedSize(action_button_width, 32)
        for button in (self.preview_btn, self.submit_btn):
            button.setFixedSize(single_file_button_width, 32)
        self.preview_btn.setObjectName("light")
        self.submit_btn.setObjectName("primary")
        self.queue_btn.setObjectName("light")
        self.start_queue_btn.setObjectName("primary")
        self.stop_queue_btn.setObjectName("danger")
        single_file_actions.addWidget(self.preview_btn)
        single_file_actions.addWidget(self.submit_btn)
        queue_actions.addWidget(self.queue_btn)
        queue_actions.addWidget(self.start_queue_btn)
        queue_actions.addWidget(self.stop_queue_btn)
        action_rows.addLayout(single_file_actions)
        action_rows.addLayout(queue_actions)
        card_layout.addLayout(action_rows)

        self.preview_btn.clicked.connect(self.preview_command)
        self.submit_btn.clicked.connect(self.submit_job)
        self.queue_btn.clicked.connect(self.open_queue_manager)
        self.start_queue_btn.clicked.connect(self.start_queue)
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
        self.history.document().setMaximumBlockCount(MAX_HISTORY_LOG_LINES)
        self.history.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.history.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
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
            8,
            8,
            8,
            8,
        )

        right_layout.setSpacing(8)

        # 标题
        runtime_title = QtWidgets.QLabel("作业运行情况")

        runtime_title.setObjectName("runtimeTitle")

        right_layout.addWidget(runtime_title)

        # ---------- 第一行：Job 选择器 + 总体统计 ----------

        selector_row = QtWidgets.QHBoxLayout()

        selector_row.setContentsMargins(
            0,
            0,
            8,
            0,
        )

        selector_row.setSpacing(8)

        selector_row.addWidget(QtWidgets.QLabel("Job"))

        self.job_selector = ArrowComboBox()

        self.job_selector.setObjectName("runtimeSelector")

        self.job_selector.setMinimumWidth(148)

        self.job_selector.setMaximumWidth(220)

        self.job_selector.setFixedHeight(30)
        self.job_selector.setToolTip("点击展开切换作业；颜色表示作业状态")
        self.job_selector.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.job_selector.setItemDelegate(RuntimeSelectorDelegate(self.job_selector))
        self.job_selector.view().setMouseTracking(True)

        self.job_selector.currentIndexChanged.connect(self.on_job_selector_changed)

        selector_row.addWidget(self.job_selector)

        selector_row.addStretch(1)

        self.job_stats_label = QtWidgets.QLabel("运行中 0 | 完成 0 | 异常 0")

        self.job_stats_label.setObjectName("hint")
        self.job_stats_label.setContentsMargins(0, 0, 8, 0)

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

        self.job_meta = QtWidgets.QScrollArea()
        self.job_meta.setObjectName("runtimeMeta")
        self.job_meta.setWidgetResizable(True)
        self.job_meta.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.job_meta.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.job_meta.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.job_meta.setViewportMargins(1, 1, 1, 1)

        self.job_meta.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )

        self.job_meta.setMinimumHeight(172)

        self.job_meta.setMaximumHeight(236)

        self.job_meta_content = QtWidgets.QWidget()
        self.job_meta_content.setObjectName("runtimeMetaContent")
        self.job_meta_layout = QtWidgets.QVBoxLayout(self.job_meta_content)
        self.job_meta_layout.setContentsMargins(8, 8, 8, 8)
        self.job_meta_layout.setSpacing(8)
        self.job_meta.setWidget(self.job_meta_content)
        self.set_job_meta_empty()

        # ---------- 第五块：STA / MSG / DAT 运行日志 ----------

        self.job_log = QtWidgets.QPlainTextEdit()

        self.job_log.setReadOnly(True)

        self.job_log.setObjectName("runtimeLog")

        self.job_log.document().setMaximumBlockCount(MAX_JOB_LOG_LINES)

        self.job_log.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)

        self.job_log.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.job_log.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.job_log.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        self.job_log.setMinimumHeight(160)

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

        runtime_body_layout.addWidget(self.job_meta, 0)

        self.job_meta.setMinimumWidth(0)

        self.sta_sticky_header_label = QtWidgets.QLabel(build_sta_table_header() + "\n" + "-" * LOG_SEPARATOR_WIDTH)
        self.sta_sticky_header_label.setObjectName("staStickyHeader")
        self.sta_sticky_header_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.sta_sticky_header_label.hide()

        self.runtime_log_frame = QtWidgets.QFrame()
        self.runtime_log_frame.setObjectName("runtimeLogFrame")
        runtime_log_layout = QtWidgets.QVBoxLayout(self.runtime_log_frame)
        runtime_log_layout.setContentsMargins(
            1,
            1,
            1,
            1,
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

    def restore_joblist_state(self) -> None:
        candidates, queue_items, error = load_joblist_state()
        self.candidate_queue_items = candidates
        self.queue_items = queue_items
        self.joblist_load_error = error

    def request_joblist_save(self) -> None:
        if self._closing:
            return
        if not hasattr(self, "_joblist_save_timer"):
            return
        self._joblist_save_timer.start(FORMAL_QUEUE_SAVE_DEBOUNCE_MS)

    def save_joblist_state_now(self) -> None:
        try:
            save_joblist_state(self.candidate_queue_items, self.queue_items)
        except OSError as exc:
            if hasattr(self, "history"):
                self.append_history(f"保存 joblist.json 失败：{exc}")

    def find_queue_item_by_job(
        self,
        *,
        work_dir: str,
        job_name: str,
    ) -> QueueItem | None:
        """按工作目录与 Job 名称查找正式队列记录。"""
        return scheduler_find_queue_item_by_key(
            work_dir=work_dir,
            job_name=job_name,
            queue_items=self.queue_items,
        )

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

    @hang_probe_function("MainWindow.submit_job")
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
                STATUS_STARTING,
                STATUS_RUNNING,
            }:
                queue_item.status = STATUS_FAILED

            if not queue_item.message:
                queue_item.message = "直接提交失败"

        self.refresh_visible_queue_manager()
        self.update_queue_status_label()

    def open_queue_manager(self) -> None:
        self._startup_timeline.mark("open-queue-manager-start")
        if self.queue_manager_dialog is not None and self.queue_manager_dialog.isVisible():
            self.queue_manager_dialog.refresh_tables()
            self.queue_manager_dialog.raise_()
            self.queue_manager_dialog.activateWindow()
            self.request_restored_status_scan_after_queue_manager_render()
            self._startup_timeline.mark("open-queue-manager-existing")
            return

        dialog = QueueManagerDialog(
            self,
            self.queue_items,
            self.current_queue_settings(),
            self.inp_row.text(),
            initial_candidates=self.candidate_queue_items,
            joblist_save_callback=self.request_joblist_save,
        )
        dialog.setWindowFlag(QtCore.Qt.WindowType.Window, True)
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
        self.request_restored_status_scan_after_queue_manager_render()
        self._startup_timeline.mark(
            "open-queue-manager-shown",
            candidates=len(self.candidate_queue_items),
            queue=len(self.queue_items),
        )

    def request_restored_status_scan_after_queue_manager_render(self) -> None:
        if self._restored_status_scan_after_queue_open_scheduled:
            return
        if not self.restored_unknown_queue_items():
            return

        self._restored_status_scan_after_queue_open_scheduled = True

        def run_restore_scan() -> None:
            self._restored_status_scan_after_queue_open_scheduled = False
            dialog = self.queue_manager_dialog
            if dialog is None or not dialog.isVisible():
                return
            self.request_restored_queue_status_scan()

        QtCore.QTimer.singleShot(350, run_restore_scan)

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
        return self.job_controller.terminate_queue_items_by_ids(item_ids)

    def collect_known_external_jobs(self, work_dir: str) -> list[dict]:
        return external_collect_known_external_jobs(
            self.queue_items,
            work_dir,
        )

    def scan_external_jobs(
        self,
        work_dir: str,
        queue_dialog: QueueManagerDialog | None = None,
        *,
        show_summary: bool = True,
        reason: str = "manual",
    ) -> None:
        if self.external_scan_thread is not None:
            self.show_non_modal_message(
                "扫描外部作业",
                "外部作业扫描正在进行，请稍候。",
            )
            return

        if self.queue_dialog_is_visible(queue_dialog):
            queue_dialog.set_external_scan_busy(True)

        scan_operation = f"external-scan:{work_dir}"
        self.append_history(
            f"开始后台扫描外部 Abaqus 作业：{work_dir}",
            operation=scan_operation,
        )

        self.external_scan_dialog = queue_dialog
        self.external_scan_show_summary = show_summary
        self.external_scan_reason = reason

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
    def queue_item_runtime_work_dir(item: QueueItem) -> str:
        return (
            item.effective_work_dir
            or item.external_work_dir
            or item.calculation_root_dir
            or os.path.dirname(item.source_inp_path or item.inp_path)
        )

    def restored_unknown_queue_items(self) -> list[QueueItem]:
        return [item for item in self.queue_items if item.status == STATUS_UNKNOWN]

    def request_restored_queue_status_scan(self, *, start_queue_after: bool = False) -> bool:
        unknown_items = self.restored_unknown_queue_items()
        if not unknown_items:
            return False

        if start_queue_after:
            self._start_queue_after_restore_scan = True

        if self.external_scan_thread is not None:
            self.append_history("恢复队列状态复核：外部作业扫描正在进行，等待当前扫描完成。")
            return True

        scan_root = ""
        for item in unknown_items:
            scan_root = self.queue_item_runtime_work_dir(item)
            if scan_root:
                break
        if not scan_root:
            for item in unknown_items:
                item.status = STATUS_PENDING_CONFIRM
                item.message = "程序重启后缺少运行目录，请人工确认"
            self.update_queue_status_label()
            self.refresh_visible_queue_manager()
            self.request_joblist_save()
            return False

        self.append_history(f"开始恢复队列状态复核：{len(unknown_items)} 个待确认作业")
        self.scan_external_jobs(
            scan_root,
            show_summary=False,
            reason="restore",
        )
        return True

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
        show_summary = self.external_scan_show_summary

        if self.queue_dialog_is_visible(queue_dialog):
            queue_dialog.set_external_scan_busy(False)

            queue_dialog.refresh_queue_table()

        self.append_history(
            f"外部作业扫描失败：{work_dir}\n{error_message}",
            operation=f"external-scan:{work_dir}",
        )

        if show_summary:
            self.show_non_modal_message(
                "扫描外部作业",
                f"扫描失败：\n{error_message}",
                warning=True,
            )

        self.external_scan_dialog = None
        self.external_scan_show_summary = True
        self.external_scan_reason = "manual"
        self._start_queue_after_restore_scan = False

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
        if not external_scan_debug_enabled():
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
        show_summary = self.external_scan_show_summary
        start_queue_after_restore_scan = self._start_queue_after_restore_scan
        self._start_queue_after_restore_scan = False
        merge_result = merge_external_scan_results(
            queue_items=self.queue_items,
            work_dir=work_dir,
            jobs=jobs,
        )
        added = int(merge_result["added"])
        updated = int(merge_result["updated"])
        status_only_updates = int(merge_result["status_only_updates"])
        terminal_external_records = merge_result.get("terminal_external_records") or []
        self.external_job_coordinator.apply_scan_merge_result(
            merge_result=merge_result,
            operation=f"external-scan:{work_dir}",
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

        self.append_history(
            message,
            operation=f"external-scan:{work_dir}",
        )

        if show_summary:
            self.show_non_modal_message(
                "扫描外部作业",
                message,
            )

        self.external_scan_dialog = None
        self.external_scan_show_summary = True
        self.external_scan_reason = "manual"

        if added or updated or status_only_updates or terminal_external_records:
            self.request_joblist_save()

        if start_queue_after_restore_scan:
            QtCore.QTimer.singleShot(0, self.start_queue)

    def start_queue(self) -> None:
        if self.queue_active:
            if self.queue_stop_requested:
                self.queue_stop_requested = False
                self.append_history("开始队列：已清除残留的停止请求。")
            self.dispatch_queue()
            return

        if self.restored_unknown_queue_items():
            if self.request_restored_queue_status_scan(start_queue_after=True):
                self.append_history("开始队列：先复核程序重启后的未知状态作业。")
                self.update_queue_status_label()
                return

        self.refresh_queue_dependencies()
        pending = [item for item in self.queue_items if item.status == STATUS_PENDING_RUN]
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
        return self.job_controller.stop_queue()

    def estimate_effective_available_slots(self) -> dict:
        manual_limit = self.max_parallel_spin.value()
        active_job_names = scheduler_get_managed_active_job_names(
            self.active_runs,
            self.queue_items,
        )
        managed_active_count = len(
            scheduler_get_managed_active_job_keys(
                self.active_runs,
                self.queue_items,
                include_external=False,
            )
        )
        manual_available_slots = max(0, manual_limit - managed_active_count)
        available_memory = int(self.latest_system_memory.get("available") or 0)
        active_job_names_lower = {name.lower() for name in active_job_names}
        active_memory_usage_by_job = {
            job_name: usage
            for job_name, usage in self.latest_memory_usage_by_job.items()
            if str(job_name).lower() in active_job_names_lower
        }
        slot_estimate = self.memory_monitor_service.estimate_available_slots(
            available_memory=available_memory,
            usage_by_job=active_memory_usage_by_job,
            active_job_names=active_job_names,
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

    def request_dispatch_queue(self) -> None:
        if self._closing:
            return
        if self._dispatch_pending:
            return
        self._dispatch_pending = True
        self._dispatch_timer.start(QUEUE_DISPATCH_DEBOUNCE_MS)

    def _run_scheduled_dispatch_queue(self) -> None:
        if self._closing:
            self._dispatch_pending = False
            return
        self._dispatch_pending = False
        self.dispatch_queue()

    @hang_probe_function("MainWindow.dispatch_queue")
    def dispatch_queue(self) -> None:
        if self._closing:
            return
        if self._dispatch_timer.isActive():
            self._dispatch_timer.stop()
        self._dispatch_pending = False
        if self._dispatch_running:
            self.request_dispatch_queue()
            return
        self._dispatch_running = True
        try:
            self._dispatch_queue_now()
        finally:
            self._dispatch_running = False

    def _dispatch_queue_now(self) -> None:
        return self.job_controller.dispatch_queue_now()

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

    @hang_probe_function("MainWindow.handle_existing_job_results")
    def handle_existing_job_results(
        self,
        options: SubmitOptions,
        work_dir: str,
        queue_item: QueueItem | None,
        *,
        queue_mode: bool = False,
    ) -> tuple[bool, SubmitOptions, dict]:
        return self.job_controller.handle_existing_job_results(
            options,
            work_dir,
            queue_item,
            queue_mode=queue_mode,
        )

    def archive_move_conflict_message(self, options: SubmitOptions, queue_item: QueueItem | None) -> str:
        conflict_key = scheduler_submit_conflict_key(
            options.inp_file,
            options.job_name,
            queue_item,
        )
        if conflict_key not in self._archive_move_reserved_keys:
            return ""
        return (
            f"无法提交作业 {options.job_name}：\n"
            "同名 SSD 计算目录正在等待归档或归档中，请稍后再提交。"
        )

    def submit_requires_restart_dependency(self, options: SubmitOptions, queue_item: QueueItem | None) -> bool:
        if queue_item is not None and queue_item.run_mode == "restart":
            return True
        return inp_has_restart_keyword(options.inp_file)

    def block_missing_restart_dependency(
        self,
        options: SubmitOptions,
        queue_item: QueueItem | None,
        *,
        queue_mode: bool,
        message: str,
    ) -> None:
        return self.job_controller.block_missing_restart_dependency(options, queue_item, queue_mode=queue_mode, message=message)

    def validate_restart_dependency_before_start(
        self,
        options: SubmitOptions,
        queue_item: QueueItem | None,
        *,
        queue_mode: bool,
    ) -> str:
        return self.job_controller.validate_restart_dependency_before_start(options, queue_item, queue_mode=queue_mode)

    def start_job(
        self,
        options: SubmitOptions,
        queue_item: QueueItem | None = None,
        *,
        queue_mode: bool = False,
    ) -> bool:

        return self.job_controller.start_job(options, queue_item, queue_mode=queue_mode)

    def enqueue_workspace_prepare(
        self,
        *,
        options: SubmitOptions,
        queue_item: QueueItem | None,
        queue_mode: bool,
        plan,
        workspace_info: dict,
    ) -> bool:
        return self.job_controller.enqueue_workspace_prepare(options=options, queue_item=queue_item, queue_mode=queue_mode, plan=plan, workspace_info=workspace_info)

    def on_workspace_prepare_succeeded(self, task: WorkspacePrepareTask, result) -> None:
        return self.job_controller.on_workspace_prepare_succeeded(task, result)

    def on_workspace_prepare_failed(self, task: WorkspacePrepareTask, message: str, copied_inp_path: str = "") -> None:
        return self.job_controller.on_workspace_prepare_failed(task, message, copied_inp_path)

    def workspace_prepare_task_is_current(self, task: WorkspacePrepareTask, queue_item: QueueItem | None) -> bool:
        return self.job_controller.workspace_prepare_task_is_current(task, queue_item)

    def log_workspace_prepare_result(
        self,
        options: SubmitOptions,
        workspace_info: dict,
    ) -> None:
        return self.job_controller.log_workspace_prepare_result(options, workspace_info)

    def continue_start_job_after_workspace_ready(
        self,
        options: SubmitOptions,
        queue_item: QueueItem | None,
        *,
        queue_mode: bool,
        workspace_info: dict,
    ) -> bool:
        return self.job_controller.continue_start_job_after_workspace_ready(options, queue_item, queue_mode=queue_mode, workspace_info=workspace_info)

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
            self.request_dispatch_queue()

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
        return self.job_controller.finalize_completed_run(job_key)

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
            status, detail = inspect_job_files(
                run["work_dir"],
                run["job_name"],
                submitted_after=float(run.get("submitted_at", 0.0) or 0.0),
                diagnostic_baseline=run.get("diagnostic_baseline") or {},
            )
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
                item.status == STATUS_FAILED
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
        if dependency is not None and dependency.status == STATUS_COMPLETED:
            for run in reversed(list(self.run_records.values())):
                if (run.get("job_name") or "").lower() != dependency.job_name.lower():
                    continue
                work_dir = (run.get("work_dir") or "").strip()
                if work_dir and (Path(work_dir) / f"{dependency.job_name}.odb").exists():
                    return work_dir
                archive_destination = (run.get("archive_destination") or "").strip()
                if archive_destination and (Path(archive_destination) / f"{dependency.job_name}.odb").exists():
                    return archive_destination

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

    def run_is_ssd_independent_archive_candidate(self, run: dict) -> bool:
        return self.job_controller.run_is_ssd_independent_archive_candidate(run)

    def run_allows_archive_move(self, run: dict) -> bool:
        return self.job_controller.run_allows_archive_move(run)

    def archive_move_reserved_key_for_run(self, run: dict) -> tuple[str, str]:
        return self.job_controller.archive_move_reserved_key_for_run(run)

    def mark_archive_move_result(self, run: dict, status: str, error: str) -> None:
        return self.job_controller.mark_archive_move_result(run, status, error)

    def enqueue_archive_move(self, run_key: str, run: dict) -> bool:
        return self.job_controller.enqueue_archive_move(run_key, run)

    def release_archive_move_context(self, task: ArchiveMoveTask) -> dict | None:
        return self.job_controller.release_archive_move_context(task)

    def archive_move_context_run(self, task: ArchiveMoveTask) -> tuple[dict | None, dict | None]:
        return self.job_controller.archive_move_context_run(task)

    def on_archive_move_succeeded(self, task: ArchiveMoveTask, result) -> None:
        return self.job_controller.on_archive_move_succeeded(task, result)

    def on_archive_move_blocked(self, task: ArchiveMoveTask, message: str) -> None:
        return self.job_controller.on_archive_move_blocked(task, message)

    def on_archive_move_failed(self, task: ArchiveMoveTask, message: str) -> None:
        return self.job_controller.on_archive_move_failed(task, message)

    def archive_or_defer_finished_job(self, run: dict) -> None:
        return self.job_controller.archive_or_defer_finished_job(run)

    def process_deferred_archives(self) -> None:
        return self.job_controller.process_deferred_archives()

    def archive_finished_job(self, run: dict) -> None:
        return self.job_controller.archive_finished_job(run)

    def handle_archive_result(self, run: dict, result: dict) -> None:
        return self.job_controller.handle_archive_result(run, result)

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
        return self.job_controller.terminate_job()

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
        duplicate_job_names = ui_duplicated_runtime_job_names(self.run_records)

        self.job_selector.blockSignals(True)

        try:
            self.job_selector.clear()

            for job_key, run in self.run_records.items():
                label = ui_runtime_job_display_label(
                    self.run_records,
                    job_key,
                    duplicate_job_names=duplicate_job_names,
                )
                self.job_selector.addItem(label, job_key)
                index = self.job_selector.count() - 1
                background_color = self.runtime_selector_color(job_key, run)
                self.job_selector.setItemData(
                    index,
                    background_color,
                    QtCore.Qt.ItemDataRole.BackgroundRole,
                )
                self.job_selector.setItemData(
                    index,
                    QtGui.QColor("#0f172a"),
                    QtCore.Qt.ItemDataRole.ForegroundRole,
                )
                self.job_selector.setItemData(
                    index,
                    f"{label} | {self.runtime_selector_status_text(job_key, run)}",
                    QtCore.Qt.ItemDataRole.ToolTipRole,
                )

            if current_key:
                index = self.job_selector.findData(current_key)

                if index >= 0:
                    self.job_selector.setCurrentIndex(index)

        finally:
            self.job_selector.blockSignals(False)

        self.apply_runtime_selector_current_color()
        self.refresh_job_stats()

    def runtime_selector_status_text(self, job_key: str, run: dict) -> str:
        if job_key in self.active_runs:
            return STATUS_RUNNING
        queue_item = run.get("queue_item")
        return str(getattr(queue_item, "status", "") or "未运行")

    def runtime_selector_color(self, job_key: str, run: dict) -> QtGui.QColor:
        status = self.runtime_selector_status_text(job_key, run)
        if status in {STATUS_RUNNING, STATUS_STARTING}:
            return QtGui.QColor("#dcfce7")
        if status in {STATUS_COMPLETED, STATUS_DATACHECK_COMPLETED}:
            return QtGui.QColor("#dbeafe")
        if status in {STATUS_FAILED, STATUS_DATACHECK_FAILED}:
            return QtGui.QColor("#fee2e2")
        if status in {STATUS_TERMINATED, STATUS_TERMINATING, STATUS_CANCELED}:
            return QtGui.QColor("#ede9fe")
        if status == STATUS_WAITING_DEPENDENCY:
            return QtGui.QColor("#fef3c7")
        return QtGui.QColor("#e2e8f0")

    def apply_runtime_selector_current_color(self) -> None:
        index = self.job_selector.currentIndex()
        background_color = (
            self.job_selector.itemData(index, QtCore.Qt.ItemDataRole.BackgroundRole) if index >= 0 else None
        )
        if not isinstance(background_color, QtGui.QColor):
            background_color = QtGui.QColor("#e2e8f0")
        background = background_color.name()
        self.job_selector.setStyleSheet(build_runtime_selector_stylesheet(background))

    def on_job_selector_changed(
        self,
        index: int,
    ) -> None:
        """切换右侧当前显示的作业。"""
        if index < 0:
            return

        job_key = self.job_selector.itemData(index)

        if job_key:
            self.apply_runtime_selector_current_color()
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

            if status in (STATUS_COMPLETED, STATUS_DATACHECK_COMPLETED):
                completed += 1

            elif status:
                failed += 1

        self.job_stats_label.setText(f"运行中 {running} | 完成 {completed} | 异常 {failed}")

    @staticmethod
    def clear_layout(layout: QtWidgets.QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                MainWindow.clear_layout(child_layout)

    @staticmethod
    def make_meta_label(text: str, object_name: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName(object_name)
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def add_meta_section(self, title: str) -> QtWidgets.QGridLayout:
        section = QtWidgets.QFrame()
        section.setObjectName("metaSection")
        layout = QtWidgets.QVBoxLayout(section)
        layout.setContentsMargins(8, 7, 8, 8)
        layout.setSpacing(6)
        title_label = self.make_meta_label(title, "metaSectionTitle")
        layout.addWidget(title_label)
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        grid.setColumnMinimumWidth(0, 86)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.job_meta_layout.addWidget(section)
        return grid

    def add_meta_row(self, grid: QtWidgets.QGridLayout, row: int, key: str, value: object) -> int:
        value_text = str(value if value not in (None, "") else "-")
        key_label = self.make_meta_label(key, "metaKey")
        key_label.setFixedWidth(86)
        value_label = self.make_meta_label(value_text, "metaValue")
        grid.addWidget(key_label, row, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        grid.addWidget(value_label, row, 1)
        return row + 1

    def add_memory_stat(
        self,
        grid: QtWidgets.QGridLayout,
        column: int,
        label_text: str,
        value_text: str,
    ) -> None:
        card = QtWidgets.QFrame()
        card.setObjectName("memoryStat")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        label = self.make_meta_label(label_text, "memoryStatLabel")
        value = self.make_meta_label(value_text, "memoryStatValue")
        layout.addWidget(label)
        layout.addWidget(value)
        grid.addWidget(card, 0, column)

    def set_job_meta_empty(self) -> None:
        self.clear_layout(self.job_meta_layout)
        label = self.make_meta_label("尚未提交作业。", "metaEmpty")
        self.job_meta_layout.addWidget(label)
        self.job_meta_layout.addStretch(1)

    def refresh_selected_run_status(
        self,
        job_key: str | None = None,
    ) -> None:
        """刷新当前 Job 的标题、状态和概要信息。"""
        job_key = job_key or self.selected_job_key()

        if not job_key:
            self.current_job_title_label.setText("Job: 未选择")

            self.status_label.setText("状态：未运行")

            self.set_job_meta_empty()

            self.update_sta_sticky_header_visibility()
            return

        run = self.run_records.get(job_key)

        if run is None:
            return

        self.current_job_title_label.setText(f"Job: {ui_runtime_job_display_label(self.run_records, job_key)}")

        self.status_label.setText(format_run_status(run))

        self.refresh_selected_run_meta(job_key)

    @staticmethod
    def restart_dependency_status_text(queue_item: QueueItem) -> str:
        message = str(queue_item.message or "")
        status = str(queue_item.status or "")
        if status == STATUS_WAITING_DEPENDENCY or "等待前置" in message:
            return "等待前置完成"
        if "缺少" in message or "不存在" in message:
            return "依赖缺失"
        if "已复制" in message or "已准备" in message:
            return "依赖文件已准备"
        if status == STATUS_PENDING_RUN or "已完成" in message or "等待提交" in message:
            return "已满足"
        if queue_item.oldjob_name or queue_item.oldjob_path:
            return "已记录"
        return "-"

    @staticmethod
    def _same_path(left: str, right: str) -> bool:
        if not left or not right:
            return False
        try:
            return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
        except (OSError, ValueError):
            return str(left).strip().lower() == str(right).strip().lower()

    def refresh_selected_run_meta(
        self,
        selected_key: str | None = None,
        run: dict | None = None,
    ) -> None:
        if selected_key is None:
            selected_key = self.selected_job_key()
        if run is None:
            run = self.run_records.get(selected_key) if selected_key else None
        if run is None:
            self.set_job_meta_empty()
            return

        self.clear_layout(self.job_meta_layout)
        queue_item = run.get("queue_item")
        source_inp_path = run.get("source_inp_path", "") or (
            queue_item.source_inp_path if queue_item is not None else ""
        )
        runtime_work_dir = str(run.get("work_dir", "") or "")
        source_work_dir = os.path.dirname(source_inp_path) if source_inp_path else ""
        display_work_dir = source_work_dir or runtime_work_dir
        temporary_work_dir = ""
        if runtime_work_dir and display_work_dir and not self._same_path(runtime_work_dir, display_work_dir):
            temporary_work_dir = runtime_work_dir

        cores = queue_item.cores if queue_item is not None else ""
        datacheck_enabled = bool(queue_item.datacheck_only) if queue_item is not None else False
        job_type = queue_item.job_type if queue_item is not None else ""
        fortran_path = queue_item.fortran_path if queue_item is not None else ""

        current_memory = safe_int(run.get("memory_current", 0) or (queue_item.rss_bytes if queue_item is not None else 0))
        peak_memory = safe_int(run.get("memory_peak", 0))
        estimated_memory = safe_int(run.get("memory_estimated", 0))
        monitor_mode = str(run.get("memory_monitor_mode", "learning") or "learning")
        monitor_mode_text = {
            "learning": "学习中",
            "patrol": "patrol",
            "external": "external",
            "stable": "稳定",
        }.get(monitor_mode, monitor_mode)
        memory_metrics = [
            ("当前内存", format_memory_size(current_memory) if current_memory > 0 else "-"),
            ("内存峰值", format_memory_size(peak_memory) if peak_memory > 0 else "未统计"),
            ("估算内存", format_memory_size(estimated_memory) if estimated_memory > 0 else "未统计"),
            ("监测模式", monitor_mode_text),
        ]
        memory_section = QtWidgets.QFrame()
        memory_section.setObjectName("memorySection")
        memory_layout = QtWidgets.QGridLayout(memory_section)
        memory_layout.setContentsMargins(0, 0, 0, 0)
        memory_layout.setHorizontalSpacing(6)
        memory_layout.setVerticalSpacing(6)
        for idx, (label, value) in enumerate(memory_metrics):
            self.add_memory_stat(memory_layout, idx, label, value)
        self.job_meta_layout.addWidget(memory_section)

        basic_grid = self.add_meta_section("作业信息")
        self.add_meta_row(basic_grid, 0, "工作目录", display_work_dir or "-")
        if temporary_work_dir:
            self.add_meta_row(basic_grid, 1, "临时计算目录", temporary_work_dir)
            next_row = 2
        else:
            next_row = 1
        self.add_meta_row(basic_grid, next_row, "INP 文件", source_inp_path or "-")
        next_row += 1
        if fortran_path:
            self.add_meta_row(basic_grid, next_row, "FOR 文件", fortran_path)
            next_row += 1
        if job_type:
            self.add_meta_row(basic_grid, next_row, "作业类型", job_type)
            next_row += 1
        self.add_meta_row(basic_grid, next_row, "核心数", str(cores or "-"))
        next_row += 1
        if datacheck_enabled:
            self.add_meta_row(basic_grid, next_row, "Datacheck", "是")

        if queue_item is not None and (queue_item.oldjob_name or queue_item.oldjob_path):
            restart_grid = self.add_meta_section("Restart 依赖")
            restart_row = 0
            self.add_meta_row(restart_grid, restart_row, "oldjob", queue_item.oldjob_name or "-")
            restart_row += 1
            dependency_dir = queue_item.oldjob_dir or (
                os.path.dirname(queue_item.oldjob_path) if queue_item.oldjob_path else ""
            )
            if dependency_dir:
                self.add_meta_row(restart_grid, restart_row, "依赖目录", dependency_dir)
                restart_row += 1
            if queue_item.oldjob_path:
                self.add_meta_row(restart_grid, restart_row, "ODB", queue_item.oldjob_path)
                restart_row += 1
            self.add_meta_row(
                restart_grid,
                restart_row,
                "状态",
                self.restart_dependency_status_text(queue_item),
            )
            restart_row += 1
            if queue_item.message:
                self.add_meta_row(restart_grid, restart_row, "备注", queue_item.message)

        backup_messages = run.get("backup_messages") or []
        if backup_messages:
            backup_grid = self.add_meta_section("旧结果处理")
            for idx, message in enumerate(backup_messages[:3]):
                self.add_meta_row(backup_grid, idx, "备份", str(message))

        self.job_meta_layout.addStretch(1)

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
            self.apply_runtime_selector_current_color()

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
        counts = scheduler_queue_status_counts(self.queue_items)
        pending = counts.get(STATUS_PENDING_RUN, 0) + counts.get(STATUS_WAITING_DEPENDENCY, 0)
        running = sum(counts.get(status, 0) for status in ACTIVE_STATUSES)
        completed = counts.get(STATUS_COMPLETED, 0) + counts.get(STATUS_DATACHECK_COMPLETED, 0)
        failed = (
            counts.get(STATUS_FAILED, 0)
            + counts.get(STATUS_DATACHECK_FAILED, 0)
            + counts.get(STATUS_INTERRUPTED, 0)
            + counts.get(STATUS_UNKNOWN, 0)
        )
        cancelled = counts.get(STATUS_CANCELED, 0) + counts.get(STATUS_TERMINATED, 0)
        if not self.queue_items:
            self.queue_status_label.setText("队列：未生成")
            self.request_joblist_save()
            return
        covered = running + pending + completed + failed + cancelled
        other = max(0, len(self.queue_items) - covered)
        other_text = f" | 其他 {other}" if other else ""
        self.queue_status_label.setText(
            f"队列：{len(self.queue_items)} 个 | 运行 {running} | 等待 {pending} | 完成 {completed} | 失败 {failed} | 取消 {cancelled}{other_text}"
        )
        self.request_joblist_save()

    def update_abaqus_status(self) -> None:
        abaqus_path = shutil.which("abaqus")
        if abaqus_path:
            self.abaqus_status_label.setText("Abaqus 状态：已检测到 Abaqus")
        else:
            self.abaqus_status_label.setText("Abaqus 状态：未在 PATH 中找到")

    def start_abaqus_status_check(self) -> None:
        if self._closing or self.abaqus_status_thread is not None:
            return
        self.abaqus_status_label.setText("Abaqus 状态：检测中...")
        thread = QtCore.QThread(self)
        worker = AbaqusPathCheckWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(
            self.finish_abaqus_status_check,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self.clear_abaqus_status_check_worker)
        self.abaqus_status_thread = thread
        self.abaqus_status_worker = worker
        thread.start()

    @QtCore.Slot(str)
    def finish_abaqus_status_check(self, abaqus_path: str) -> None:
        if self._closing:
            return
        if abaqus_path:
            self.abaqus_status_label.setText("Abaqus 状态：已检测到 Abaqus")
        else:
            self.abaqus_status_label.setText("Abaqus 状态：未在 PATH 中找到")

    @QtCore.Slot()
    def clear_abaqus_status_check_worker(self) -> None:
        self.abaqus_status_thread = None
        self.abaqus_status_worker = None

    def append_history(self, text: str, *, operation: str | None = None) -> None:
        """追加运行记录：时间戳为蓝色，正文使用默认深灰色。"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        last_operation = getattr(self, "_history_last_operation", "")
        same_operation = bool(operation) and operation == last_operation

        cursor = self.history.textCursor()

        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)

        if not self.history.document().isEmpty():
            cursor.insertText("\n")

        timestamp_format = QtGui.QTextCharFormat()

        timestamp_format.setForeground(QtGui.QColor(PRIMARY))

        timestamp_format.setFontWeight(QtGui.QFont.Weight.DemiBold)

        body_format = QtGui.QTextCharFormat()

        body_format.setForeground(QtGui.QColor(TEXT))

        if not same_operation:
            cursor.insertText(
                f"[{timestamp}]\n",
                timestamp_format,
            )

        cursor.insertText(
            text,
            body_format,
        )

        self._history_last_operation = operation or ""

        self.history.setTextCursor(cursor)

        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

    @staticmethod
    def trim_log_cache_text(text: str, max_lines: int) -> str:
        """Keep only the newest lines in the in-memory per-job log cache."""
        if max_lines <= 0:
            return ""

        line_count = text.count("\n") + (0 if not text or text.endswith("\n") else 1)

        if line_count <= max_lines:
            return text

        return "\n".join(text.rsplit("\n", max_lines)[-max_lines:])

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

            run["log"] = self.trim_log_cache_text(
                current + separator + normalized_text,
                MAX_JOB_LOG_LINES,
            )

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
        self._closing = True
        queue_dialog = self.queue_manager_dialog
        if queue_dialog is not None:
            try:
                queue_dialog.close()
            except RuntimeError:
                pass
        self._dispatch_timer.stop()
        self._joblist_save_timer.stop()
        self.save_joblist_state_now()
        self._ui_heartbeat_timer.stop()
        abaqus_status_thread = self.abaqus_status_thread
        if abaqus_status_thread is not None:
            abaqus_status_thread.quit()
            if abaqus_status_thread.isRunning():
                abaqus_status_thread.wait(1500)
        self.runtime_controller.shutdown()
        self.workspace_prepare_service.shutdown()
        self._workspace_prepare_contexts.clear()
        self.archive_move_service.shutdown()
        self._archive_move_contexts.clear()
        self._archive_move_reserved_keys.clear()
        self.memory_adapter.stop()
        super().closeEvent(event)


def main(
    argv: list[str] | None = None,
    *,
    startup_timeline_start: float | None = None,
    startup_timeline_last: float | None = None,
    startup_timeline_enabled: bool = False,
) -> int:
    """Run the Qt frontend."""
    startup_timeline = StartupTimeline(
        "App",
        enabled=startup_timeline_enabled,
        start=startup_timeline_start,
        last=startup_timeline_last,
    )

    startup_timeline.mark("main-function-start")
    argv = list(sys.argv if argv is None else argv)
    startup_timeline.mark("argv-ready")
    app = QtWidgets.QApplication(argv)
    startup_timeline.mark("qapplication-created")
    app.setApplicationName(APP_TITLE)
    QtWidgets.QApplication.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
    startup_timeline.mark("qt-style-ready")
    window = MainWindow()
    startup_timeline.mark("mainwindow-created")
    window.show()
    startup_timeline.mark("mainwindow-shown")
    QtCore.QTimer.singleShot(0, lambda: startup_timeline.mark("event-loop-first-tick"))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
