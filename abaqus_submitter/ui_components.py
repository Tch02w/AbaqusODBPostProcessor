"""Small reusable Qt UI components."""

import os
import time
import unicodedata
from collections.abc import Mapping

from .qt_compat import QtCore, QtGui, QtWidgets, Signal


def format_elapsed_seconds(elapsed_seconds: float) -> str:
    """将秒数格式化为紧凑耗时。"""
    total_seconds = max(0, int(elapsed_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours} h {minutes} min {seconds} s"

    if minutes:
        return f"{minutes} min {seconds} s"

    return f"{seconds} s"


def format_run_status(run: dict) -> str:
    """生成右侧 Job 名称右边的详细状态。"""
    queue_item = run.get("queue_item")

    if run.get("finalized", False):
        final_status = queue_item.status if queue_item is not None else "已结束"
        return str(final_status)

    if run.get("terminating", False):
        return "Terminating"

    parts: list[str] = []
    completion_detected = (
        str(run.get("runtime_diagnostic_status") or "") == "完成"
        or run.get("runtime_phase") == "FINISH_CANDIDATE"
    )
    if completion_detected:
        parts.append("Confirming")
    else:
        parts.append("Suspended" if run.get("is_paused", False) else "Running")

    current_step = run.get("current_step", "")
    if current_step:
        parts.append(str(current_step))

    total_time = run.get("total_time", "")
    if total_time != "":
        parts.append(f"Time {total_time}")

    if not current_step and total_time == "":
        parts.append(format_abaqus_stage(run) or "等待 sta")

    submitted_at = float(run.get("submitted_at", 0.0) or 0.0)
    if submitted_at > 0:
        parts.append(format_elapsed_seconds(time.time() - submitted_at))

    return " | ".join(parts)


def format_abaqus_stage(run: dict) -> str:
    """Return the most specific Abaqus launcher stage known before STA progress exists."""
    if run.get("standard_started"):
        return "Standard"
    if run.get("explicit_started"):
        return "Explicit"
    if run.get("package_started"):
        return "Package"
    if run.get("pre_finished"):
        return "Pre 完成"
    if run.get("pre_started"):
        return "Pre"
    return ""


def build_job_display_label(
    job_name: str,
    work_dir: str,
    duplicated: bool,
) -> str:
    """Build a UI-only job label without changing the real Abaqus job name."""
    job_name = str(job_name or "").strip()
    if not duplicated:
        return job_name

    work_dir = str(work_dir or "").strip()
    context = work_dir or "外部作业"
    return f"{job_name}  [{context}]"


def duplicated_runtime_job_names(runs: Mapping[str, Mapping[str, object]]) -> set[str]:
    counts: dict[str, int] = {}
    for run in runs.values():
        job_name = str(run.get("job_name", "") or "").strip().lower()
        if not job_name:
            continue
        counts[job_name] = counts.get(job_name, 0) + 1
    return {job_name for job_name, count in counts.items() if count > 1}


def runtime_job_display_label(
    runs: Mapping[str, Mapping[str, object]],
    job_key: str,
    *,
    duplicate_job_names: set[str] | None = None,
) -> str:
    run = runs.get(job_key)
    if run is None:
        return job_key
    job_name = str(run.get("job_name", "") or job_key)
    work_dir = str(run.get("work_dir", "") or "")
    if duplicate_job_names is None:
        duplicate_job_names = duplicated_runtime_job_names(runs)
    return build_job_display_label(
        job_name,
        work_dir,
        job_name.lower() in duplicate_job_names,
    )


def safe_int(value, default: int = 0) -> int:
    """将任意值安全转换为 int，转换失败时返回默认值。"""
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def display_text_width(text: str) -> int:
    """
    Return the terminal-style display width of text.

    Chinese full-width characters count as 2 columns.
    ASCII characters count as 1 column.
    """
    width = 0

    for char in str(text):
        if unicodedata.east_asian_width(char) in {"W", "F", "A"}:
            width += 2
        else:
            width += 1

    return width


def pad_display_text(text: str, width: int) -> str:
    """Pad text according to its visible display width."""
    text = str(text)
    padding = max(0, width - display_text_width(text))

    return text + " " * padding


def build_memory_summary_table(
    *,
    current_memory_text: str,
    peak_memory_text: str,
    estimated_memory_text: str,
    monitor_mode_text: str,
) -> list[str]:
    """Build the two-row runtime memory summary table."""
    headers = (
        "当前内存",
        "内存峰值",
        "估算内存",
        "监测模式",
    )
    values = (
        current_memory_text,
        peak_memory_text,
        estimated_memory_text,
        monitor_mode_text,
    )
    column_widths = (
        12,
        12,
        12,
        12,
    )

    def build_row(row_values: tuple[str, ...]) -> str:
        return "| " + " | ".join(pad_display_text(text, width) for text, width in zip(row_values, column_widths)) + " |"

    header_line = build_row(headers)
    value_line = build_row(values)
    separator = "-" * max(display_text_width(header_line), display_text_width(value_line))

    return [
        separator,
        header_line,
        value_line,
        separator,
    ]


class ResourceProgressBar(QtWidgets.QProgressBar):
    """Rounded resource meter that keeps low non-zero values visibly circular."""

    TRACK_COLOR = QtGui.QColor("#e2e8f0")
    CHUNK_COLOR = QtGui.QColor("#2563eb")
    BAR_HEIGHT = 8

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setObjectName("resourceProgressBar")
        self.setTextVisible(False)
        self.setMinimumHeight(self.BAR_HEIGHT)
        self.setMaximumHeight(self.BAR_HEIGHT)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if self.maximum() <= self.minimum():
            super().paintEvent(event)
            return

        track = QtCore.QRectF(self.rect())
        if track.isEmpty():
            return

        painter = QtGui.QPainter(self)
        painter.setRenderHint(
            QtGui.QPainter.RenderHint.Antialiasing,
            True,
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        track_radius = min(track.width(), track.height()) / 2
        painter.setBrush(self.TRACK_COLOR)
        painter.drawRoundedRect(
            track,
            track_radius,
            track_radius,
        )

        value = max(self.minimum(), min(self.maximum(), self.value()))
        if value <= self.minimum():
            return
        ratio = (value - self.minimum()) / (
            self.maximum() - self.minimum()
        )
        chunk_width = min(
            track.width(),
            max(track.height(), track.width() * ratio),
        )
        chunk = QtCore.QRectF(
            (
                track.right() - chunk_width
                if self.invertedAppearance()
                else track.left()
            ),
            track.top(),
            chunk_width,
            track.height(),
        )
        chunk_radius = min(chunk.width(), chunk.height()) / 2
        painter.setBrush(self.CHUNK_COLOR)
        painter.drawRoundedRect(
            chunk,
            chunk_radius,
            chunk_radius,
        )


class SegmentedSpinBox(QtWidgets.QSpinBox):
    """Horizontal ``− | value | +`` spin box with native QSpinBox behavior."""

    SEGMENT_WIDTH = 30

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setProperty("segmentedSpin", True)
        self.setButtonSymbols(
            QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.step_down_button = QtWidgets.QToolButton(self)
        self.step_down_button.setObjectName("spinStepDown")
        self.step_down_button.setText("−")
        self.step_down_button.setToolTip("减少")
        self.step_down_button.setAccessibleName("减少数值")
        self.step_down_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self.step_up_button = QtWidgets.QToolButton(self)
        self.step_up_button.setObjectName("spinStepUp")
        self.step_up_button.setText("+")
        self.step_up_button.setToolTip("增加")
        self.step_up_button.setAccessibleName("增加数值")
        self.step_up_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        for button in (self.step_down_button, self.step_up_button):
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(350)
            button.setAutoRepeatInterval(80)

        self.step_down_button.clicked.connect(
            lambda _checked=False: self._step_and_focus(-1)
        )
        self.step_up_button.clicked.connect(
            lambda _checked=False: self._step_and_focus(1)
        )
        self.valueChanged.connect(self._refresh_step_buttons)
        self._apply_text_margins()
        self._refresh_step_buttons()

    def _step_and_focus(self, direction: int) -> None:
        self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
        if direction < 0:
            self.stepDown()
        else:
            self.stepUp()

    def _apply_text_margins(self) -> None:
        line_edit = self.lineEdit()
        if line_edit is not None:
            margin = self.SEGMENT_WIDTH + 2
            line_edit.setTextMargins(margin, 0, margin, 0)

    def _refresh_step_buttons(self, *_args) -> None:
        can_step = self.isEnabled() and not self.isReadOnly()
        wraps = self.wrapping()
        self.step_down_button.setEnabled(
            can_step and (wraps or self.value() > self.minimum())
        )
        self.step_up_button.setEnabled(
            can_step and (wraps or self.value() < self.maximum())
        )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        button_height = max(0, self.height() - 2)
        self.step_down_button.setGeometry(
            1,
            1,
            self.SEGMENT_WIDTH,
            button_height,
        )
        self.step_up_button.setGeometry(
            max(1, self.width() - self.SEGMENT_WIDTH - 1),
            1,
            self.SEGMENT_WIDTH,
            button_height,
        )
        self.step_down_button.raise_()
        self.step_up_button.raise_()
        self._apply_text_margins()

    def changeEvent(self, event: QtCore.QEvent) -> None:
        super().changeEvent(event)
        if event.type() in {
            QtCore.QEvent.Type.EnabledChange,
            QtCore.QEvent.Type.StyleChange,
        }:
            self._refresh_step_buttons()

    def setRange(self, minimum: int, maximum: int) -> None:
        super().setRange(minimum, maximum)
        if hasattr(self, "step_down_button"):
            self._refresh_step_buttons()

    def setMinimum(self, minimum: int) -> None:
        super().setMinimum(minimum)
        if hasattr(self, "step_down_button"):
            self._refresh_step_buttons()

    def setMaximum(self, maximum: int) -> None:
        super().setMaximum(maximum)
        if hasattr(self, "step_down_button"):
            self._refresh_step_buttons()

    def setReadOnly(self, read_only: bool) -> None:
        super().setReadOnly(read_only)
        if hasattr(self, "step_down_button"):
            self._refresh_step_buttons()

    def setWrapping(self, wrapping: bool) -> None:
        super().setWrapping(wrapping)
        if hasattr(self, "step_down_button"):
            self._refresh_step_buttons()

    def sizeHint(self) -> QtCore.QSize:
        hint = super().sizeHint()
        return QtCore.QSize(
            max(112, hint.width() + self.SEGMENT_WIDTH * 2),
            max(30, hint.height()),
        )

    def minimumSizeHint(self) -> QtCore.QSize:
        hint = super().minimumSizeHint()
        return QtCore.QSize(
            max(96, hint.width() + self.SEGMENT_WIDTH * 2),
            max(30, hint.height()),
        )


class WorkbenchComboBox(QtWidgets.QComboBox):
    """Shared combo box with an explicit arrow and a flat, shadowless popup."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        view = self.view()
        view.setObjectName("workbenchComboPopup")
        view.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        view.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TranslucentBackground,
            False,
        )
        view.setWindowFlag(
            QtCore.Qt.WindowType.NoDropShadowWindowHint,
            True,
        )

    def showPopup(self) -> None:
        view = self.view()
        view.setWindowFlag(
            QtCore.Qt.WindowType.NoDropShadowWindowHint,
            True,
        )
        view.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TranslucentBackground,
            False,
        )
        super().showPopup()
        QtCore.QTimer.singleShot(0, self._position_popup_below)

    def _position_popup_below(self) -> None:
        view = self.view()
        popup = view.window()
        if popup is None:
            return

        popup.setWindowFlag(
            QtCore.Qt.WindowType.NoDropShadowWindowHint,
            True,
        )
        popup.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TranslucentBackground,
            False,
        )
        popup.clearMask()

        top_left = self.mapToGlobal(QtCore.QPoint(0, self.height()))
        screen = QtGui.QGuiApplication.screenAt(top_left) or self.screen()
        available = (
            screen.availableGeometry()
            if screen is not None
            else QtCore.QRect()
        )
        row_count = max(1, self.count())
        row_height = max(view.sizeHintForRow(0), 26)
        content_height = row_count * row_height + 2
        if available.isValid():
            available_below = max(
                row_height + 2,
                available.bottom() - top_left.y() + 1,
            )
            content_height = min(content_height, available_below)
        popup_width = max(self.width(), view.sizeHintForColumn(0) + 28)
        popup.setGeometry(
            top_left.x(),
            top_left.y(),
            popup_width,
            content_height,
        )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        arrow_color = "#94a3b8" if not self.isEnabled() else "#475569"
        painter.setBrush(QtGui.QColor(arrow_color))
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


class FilePickerRow(QtWidgets.QWidget):
    """Original-style one-line file selector."""

    pathChanged = Signal(str)

    def __init__(self, label: str, placeholder: str, parent=None):
        super().__init__(parent)
        self.setObjectName("filePickerRow")
        self._path = ""
        self.placeholder = placeholder
        self.label = QtWidgets.QLabel(label)
        self.button = QtWidgets.QPushButton(placeholder)
        self.button.setObjectName("filePicker")
        self.button.setFixedHeight(30)
        self.setFixedHeight(34)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.label.setFixedWidth(34)
        layout.addWidget(self.label)
        layout.addWidget(self.button, 1)

    def text(self) -> str:
        return self._path

    def set_path(self, path: str) -> None:
        self._path = path.strip()
        if self._path:
            self.button.setText(os.path.basename(self._path))
        else:
            self.button.setText(self.placeholder)
        self.pathChanged.emit(self._path)
