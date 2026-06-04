"""Small reusable Qt UI components."""

import os
import time
import unicodedata

from .qt_compat import QtWidgets, Signal


def format_elapsed_seconds(elapsed_seconds: float) -> str:
    """将秒数格式化为旧版风格的紧凑耗时。"""
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
        self.button.setFixedHeight(28)
        self.setFixedHeight(32)

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
