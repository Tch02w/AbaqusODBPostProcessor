"""Qt main window for the Abaqus submitter."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from abaqus_submitter.abaqus_diagnostics import (
    classify_job_text,
    format_sta_output_for_log,
    inspect_job_files,
)
from abaqus_submitter.constants import DEFAULT_CPUS, MAX_CPUS, STA_FILE_ENCODING

from .command import (
    MEMORY_OPTIONS,
    SubmitOptions,
    build_abaqus_command,
    derive_job_name,
    validate_cpus,
    validate_options,
)
from .qt_compat import QT_BINDING, QtCore, QtWidgets, Signal


APP_TITLE = "Abaqus Submitter Qt"
APP_BG = "#f4f7fb"
CARD_BG = "#ffffff"
PRIMARY = "#2563eb"
PRIMARY_HOVER = "#1d4ed8"
TEXT = "#111827"
HINT = "#64748b"
LOG_BG = "#f8fafc"


def _process_state_name(process: QtCore.QProcess) -> str:
    state = process.state()
    states = QtCore.QProcess.ProcessState
    if state == states.NotRunning:
        return "已停止"
    if state == states.Starting:
        return "启动中"
    return "运行中"


class FileRow(QtWidgets.QWidget):
    """A compact path entry with a browse button."""

    pathChanged = Signal(str)

    def __init__(self, label: str, placeholder: str, parent=None):
        super().__init__(parent)
        self.label = QtWidgets.QLabel(label)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.button = QtWidgets.QPushButton("选择")

        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.addWidget(self.label, 0, 0)
        layout.addWidget(self.edit, 0, 1)
        layout.addWidget(self.button, 0, 2)
        layout.setColumnStretch(1, 1)

        self.edit.textChanged.connect(self.pathChanged)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, text: str) -> None:
        self.edit.setText(text)


class MainWindow(QtWidgets.QMainWindow):
    """First-stage Qt rewrite of the main submitter window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(980, 720)
        self.setMinimumSize(620, 520)

        self.process: QtCore.QProcess | None = None
        self.current_work_dir = ""
        self.current_job_name = ""
        self.last_sta_size = 0
        self.sta_format_state: dict[str, bool] = {}

        self.sta_timer = QtCore.QTimer(self)
        self.sta_timer.setInterval(5000)
        self.sta_timer.timeout.connect(self.poll_sta_file)

        self.build_ui()
        self.apply_styles()
        self.update_command_preview()
        self.append_history("等待提交作业...")

    # ---------- UI ----------

    def build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        left = QtWidgets.QWidget()
        left.setObjectName("leftPanel")
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        self.submit_card = QtWidgets.QFrame()
        self.submit_card.setObjectName("card")
        card_layout = QtWidgets.QVBoxLayout(self.submit_card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(10)

        title = QtWidgets.QLabel("Abaqus 提交器")
        title.setObjectName("title")
        subtitle = QtWidgets.QLabel(f"Qt 前端 · {QT_BINDING} · 最大物理核心 {MAX_CPUS}")
        subtitle.setObjectName("subtitle")
        card_layout.addWidget(title)
        card_layout.addWidget(subtitle)

        self.inp_row = FileRow("INP", "点击选择 INP 文件")
        self.oldjob_row = FileRow("ODB", "点击选择重启动 ODB（可选）")
        self.for_row = FileRow("FOR", "点击选择 Fortran 子程序（可选）")
        card_layout.addWidget(self.inp_row)
        card_layout.addWidget(self.oldjob_row)
        card_layout.addWidget(self.for_row)

        self.inp_row.button.clicked.connect(self.select_inp_file)
        self.oldjob_row.button.clicked.connect(self.select_oldjob_file)
        self.for_row.button.clicked.connect(self.select_for_file)
        self.inp_row.pathChanged.connect(self.on_inp_changed)
        self.oldjob_row.pathChanged.connect(self.update_command_preview)
        self.for_row.pathChanged.connect(self.update_command_preview)

        job_row = QtWidgets.QHBoxLayout()
        job_label = QtWidgets.QLabel("Job")
        self.job_edit = QtWidgets.QLineEdit()
        self.job_edit.setPlaceholderText("作业名称")
        job_row.addWidget(job_label)
        job_row.addWidget(self.job_edit, 1)
        card_layout.addLayout(job_row)
        self.job_edit.textChanged.connect(self.update_command_preview)

        settings = QtWidgets.QHBoxLayout()
        settings.addWidget(QtWidgets.QLabel("Core"))
        self.cpus_spin = QtWidgets.QSpinBox()
        self.cpus_spin.setRange(0, MAX_CPUS)
        self.cpus_spin.setValue(DEFAULT_CPUS)
        self.cpus_spin.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        settings.addWidget(self.cpus_spin)
        settings.addWidget(QtWidgets.QLabel(f"最大 {MAX_CPUS}"))
        settings.addStretch(1)
        settings.addWidget(QtWidgets.QLabel("Mem"))
        self.memory_value = QtWidgets.QLineEdit()
        self.memory_value.setPlaceholderText("可选")
        self.memory_value.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.memory_value.setFixedWidth(58)
        self.memory_unit = QtWidgets.QComboBox()
        self.memory_unit.addItems(MEMORY_OPTIONS)
        self.memory_unit.setFixedWidth(72)
        settings.addWidget(self.memory_value)
        settings.addWidget(self.memory_unit)
        card_layout.addLayout(settings)

        self.cpus_spin.valueChanged.connect(self.update_command_preview)
        self.memory_value.textChanged.connect(self.update_command_preview)
        self.memory_unit.currentTextChanged.connect(self.update_command_preview)

        options = QtWidgets.QHBoxLayout()
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

        buttons = QtWidgets.QGridLayout()
        buttons.setHorizontalSpacing(8)
        buttons.setVerticalSpacing(8)
        self.preview_btn = QtWidgets.QPushButton("预览命令")
        self.submit_btn = QtWidgets.QPushButton("提交作业")
        self.queue_btn = QtWidgets.QPushButton("管理队列")
        self.start_queue_btn = QtWidgets.QPushButton("开始队列")
        self.stop_btn = QtWidgets.QPushButton("终止作业")
        self.pause_btn = QtWidgets.QPushButton("暂停")
        self.resume_btn = QtWidgets.QPushButton("恢复")
        for button in (self.preview_btn, self.submit_btn, self.queue_btn, self.start_queue_btn):
            button.setMinimumHeight(32)
        buttons.addWidget(self.preview_btn, 0, 0)
        buttons.addWidget(self.submit_btn, 0, 1)
        buttons.addWidget(self.queue_btn, 0, 2)
        buttons.addWidget(self.start_queue_btn, 0, 3)
        buttons.addWidget(self.pause_btn, 1, 0)
        buttons.addWidget(self.resume_btn, 1, 1)
        buttons.addWidget(self.stop_btn, 1, 3)
        card_layout.addLayout(buttons)

        self.preview_btn.clicked.connect(self.preview_command)
        self.submit_btn.clicked.connect(self.submit_job)
        self.stop_btn.clicked.connect(self.terminate_job)
        self.pause_btn.clicked.connect(self.suspend_job)
        self.resume_btn.clicked.connect(self.resume_job)
        self.queue_btn.clicked.connect(self.show_queue_placeholder)
        self.start_queue_btn.clicked.connect(self.show_queue_placeholder)

        self.command_edit = QtWidgets.QPlainTextEdit()
        self.command_edit.setReadOnly(True)
        self.command_edit.setFixedHeight(58)
        self.command_edit.setPlaceholderText("命令预览")
        card_layout.addWidget(self.command_edit)

        left_layout.addWidget(self.submit_card)

        history_card = QtWidgets.QFrame()
        history_card.setObjectName("card")
        history_layout = QtWidgets.QVBoxLayout(history_card)
        history_layout.setContentsMargins(18, 14, 18, 14)
        history_layout.addWidget(QtWidgets.QLabel("运行监控"))
        self.history = QtWidgets.QPlainTextEdit()
        self.history.setReadOnly(True)
        history_layout.addWidget(self.history)
        left_layout.addWidget(history_card, 1)

        right = QtWidgets.QFrame()
        right.setObjectName("card")
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_title = QtWidgets.QHBoxLayout()
        right_title.addWidget(QtWidgets.QLabel("作业日志"))
        right_title.addStretch(1)
        self.status_label = QtWidgets.QLabel("未运行")
        self.status_label.setObjectName("hint")
        right_title.addWidget(self.status_label)
        right_layout.addLayout(right_title)

        self.job_log = QtWidgets.QPlainTextEdit()
        self.job_log.setReadOnly(True)
        self.job_log.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        right_layout.addWidget(self.job_log)

        root.addWidget(left, 0)
        root.addWidget(right, 1)

        self.update_process_buttons(False)

    def apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget#leftPanel {{
                background: {APP_BG};
                color: {TEXT};
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 12px;
            }}
            QFrame#card {{
                background: {CARD_BG};
                border: 1px solid #e5e7eb;
                border-radius: 14px;
            }}
            QLabel#title {{
                font-size: 22px;
                font-weight: 700;
            }}
            QLabel#subtitle, QLabel#hint {{
                color: {HINT};
            }}
            QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{
                background: #ffffff;
                border: 1px solid #dbe3ef;
                border-radius: 8px;
                padding: 6px;
            }}
            QPlainTextEdit {{
                background: {LOG_BG};
                font-family: Consolas, "Microsoft YaHei", monospace;
            }}
            QPushButton {{
                background: #e8eef9;
                color: #1e3a8a;
                border: 0;
                border-radius: 8px;
                padding: 7px 10px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: #dbeafe;
            }}
            QPushButton:disabled {{
                background: #e5e7eb;
                color: #94a3b8;
            }}
            QPushButton#primary {{
                background: {PRIMARY};
                color: #ffffff;
            }}
            QPushButton#primary:hover {{
                background: {PRIMARY_HOVER};
            }}
            """
        )
        self.submit_btn.setObjectName("primary")
        self.start_queue_btn.setObjectName("primary")

    # ---------- Data ----------

    def collect_options(self) -> SubmitOptions:
        return SubmitOptions(
            inp_file=self.inp_row.text(),
            job_name=self.job_edit.text().strip(),
            cpus=self.cpus_spin.value(),
            oldjob_path=self.oldjob_row.text(),
            for_file=self.for_row.text(),
            interactive=self.interactive_check.isChecked(),
            datacheck=self.datacheck_check.isChecked(),
            memory_value=self.memory_value.text().strip(),
            memory_unit=self.memory_unit.currentText(),
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
            self.inp_row.setText(path)

    def select_oldjob_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择重启动 ODB",
            "",
            "Abaqus ODB (*.odb);;所有文件 (*.*)",
        )
        if path:
            self.oldjob_row.setText(path)

    def select_for_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 Fortran 子程序",
            "",
            "Fortran (*.for *.f *.f90);;所有文件 (*.*)",
        )
        if path:
            self.for_row.setText(path)

    def on_inp_changed(self, path: str) -> None:
        if path and not self.job_edit.text().strip():
            self.job_edit.setText(derive_job_name(path))
        self.update_command_preview()

    def update_command_preview(self) -> None:
        options = self.collect_options()
        if not options.inp_file or not options.job_name:
            self.command_edit.setPlainText("")
            return
        ok, _cpus, message = validate_cpus(str(options.cpus))
        if not ok:
            self.command_edit.setPlainText(message)
            return
        self.command_edit.setPlainText(build_abaqus_command(options))

    def preview_command(self) -> None:
        options = self.collect_options()
        ok, message = validate_options(options)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "命令预览", message)
            return
        command = build_abaqus_command(options)
        self.command_edit.setPlainText(command)
        self.append_history(f"命令预览：\n{command}")

    def submit_job(self) -> None:
        if self.process is not None and self.process.state() != QtCore.QProcess.ProcessState.NotRunning:
            QtWidgets.QMessageBox.information(self, "提交作业", "当前已有作业正在运行。")
            return

        options = self.collect_options()
        ok, message = validate_options(options)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "提交作业", message)
            return

        inp_path = Path(options.inp_file)
        self.current_work_dir = str(inp_path.parent)
        self.current_job_name = options.job_name
        self.last_sta_size = 0
        self.sta_format_state = {}

        command = build_abaqus_command(options)
        self.command_edit.setPlainText(command)
        self.job_log.clear()
        self.append_history(f"提交作业：{options.job_name}\n{command}")

        self.process = QtCore.QProcess(self)
        self.process.setWorkingDirectory(self.current_work_dir)
        self.process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_process_output)
        self.process.finished.connect(self.on_process_finished)
        self.process.errorOccurred.connect(self.on_process_error)

        if os.name == "nt":
            self.process.start("cmd.exe", ["/c", command])
        else:
            self.process.start("/bin/sh", ["-lc", command])

        if not self.process.waitForStarted(3000):
            self.append_history("Abaqus 进程启动失败。")
            self.update_process_buttons(False)
            return

        self.status_label.setText(_process_state_name(self.process))
        self.update_process_buttons(True)
        self.sta_timer.start()

    def read_process_output(self) -> None:
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput()).decode("mbcs", errors="replace")
        if data:
            self.append_job_log(data.rstrip())

    def on_process_error(self, error) -> None:  # noqa: ANN001 - Qt enum differs by binding
        self.append_history(f"进程错误：{error}")

    def on_process_finished(self, exit_code: int, exit_status) -> None:  # noqa: ANN001
        self.sta_timer.stop()
        self.poll_sta_file()
        self.update_process_buttons(False)
        self.status_label.setText(f"已结束，退出码 {exit_code}")
        self.append_history(f"作业进程结束：exit_code={exit_code}")
        self.inspect_finished_job()

    def poll_sta_file(self) -> None:
        if not self.current_work_dir or not self.current_job_name:
            return
        sta_path = Path(self.current_work_dir) / f"{self.current_job_name}.sta"
        if not sta_path.exists():
            return

        try:
            size = sta_path.stat().st_size
            if size < self.last_sta_size:
                self.last_sta_size = 0
            with sta_path.open("r", encoding=STA_FILE_ENCODING, errors="replace") as stream:
                stream.seek(self.last_sta_size)
                text = stream.read()
                self.last_sta_size = stream.tell()
        except OSError as exc:
            self.append_history(f"读取 STA 失败：{exc}")
            return

        if not text:
            return
        formatted = format_sta_output_for_log(text, self.sta_format_state)
        if formatted:
            self.append_job_log(formatted)
        status, detail = classify_job_text(text)
        if status:
            self.status_label.setText(status)
            if detail:
                self.append_history(detail)

    def inspect_finished_job(self) -> None:
        if not self.current_work_dir or not self.current_job_name:
            return
        try:
            status, detail = inspect_job_files(self.current_work_dir, self.current_job_name)
        except Exception as exc:  # keep UI alive even if diagnostics changes
            self.append_history(f"诊断作业文件失败：{exc}")
            return
        if status or detail:
            self.append_history(f"诊断结果：{status or '未知'} {detail or ''}".strip())

    def terminate_job(self) -> None:
        if not self.current_job_name or not self.current_work_dir:
            return
        self.run_abaqus_control("terminate")

    def suspend_job(self) -> None:
        if not self.current_job_name or not self.current_work_dir:
            return
        self.run_abaqus_control("suspend")

    def resume_job(self) -> None:
        if not self.current_job_name or not self.current_work_dir:
            return
        self.run_abaqus_control("resume")

    def run_abaqus_control(self, action: str) -> None:
        command = f"abaqus {action} job={self.current_job_name}"
        self.append_history(f"执行控制命令：{command}")
        QtCore.QProcess.startDetached(
            "cmd.exe" if os.name == "nt" else "/bin/sh",
            ["/c", command] if os.name == "nt" else ["-lc", command],
            self.current_work_dir,
        )

    def show_queue_placeholder(self) -> None:
        QtWidgets.QMessageBox.information(
            self,
            "队列管理",
            "Qt 版队列管理将在下一阶段迁移；当前请继续使用 CTk 入口中的队列功能。",
        )

    # ---------- Helpers ----------

    def append_history(self, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.history.appendPlainText(f"[{timestamp}] {text}")
        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

    def append_job_log(self, text: str) -> None:
        self.job_log.appendPlainText(text)
        self.job_log.verticalScrollBar().setValue(self.job_log.verticalScrollBar().maximum())

    def update_process_buttons(self, running: bool) -> None:
        self.submit_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.pause_btn.setEnabled(running)
        self.resume_btn.setEnabled(running)


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
