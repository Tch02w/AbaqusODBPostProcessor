"""Qt UI constants and stylesheet for the main window."""

APP_TITLE = "Abaqus 并行队列提交工具"

# Keep the left submit panel fixed so horizontal compression does not hide buttons.
LEFT_PANEL_MIN_WIDTH = 416

# Initial compact window shows only the left panel.
WINDOW_OUTER_HORIZONTAL_MARGIN = 24
PANEL_HORIZONTAL_SPACING = 12
COMPACT_WINDOW_MIN_WIDTH = LEFT_PANEL_MIN_WIDTH + WINDOW_OUTER_HORIZONTAL_MARGIN

# Runtime log minimum width is based on this divider line.
# The log window should be able to show this line without wrapping.
RUNTIME_LOG_WIDTH_SAMPLE = "****************************************************************"

# Horizontal padding inside runtimeBodyCard: 8 + 8.
RUNTIME_BODY_HORIZONTAL_MARGIN = 16

APP_BG = "#f3f6fa"
CARD_BG = "#ffffff"
LOG_BG = "#f8fafc"
TEXT = "#111827"
HINT = "#64748b"
PRIMARY = "#2563eb"
PRIMARY_HOVER = "#1d4ed8"
LIGHT = "#dbe3ee"
LIGHT_HOVER = "#cbd5e1"
DANGER = "#7f1d1d"


def build_main_stylesheet() -> str:
    return f"""
            QMainWindow, QWidget {{
                background: {APP_BG};
                color: {TEXT};
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 12px;
            }}
            QWidget#leftPanel {{
                background: {APP_BG};
            }}
            QFrame#card {{
                background: {CARD_BG};
                border: 0;
            }}
            QFrame#runtimeBodyCard {{
                background-color: #ffffff;
                border: 1px solid #9ca3af;
                border-radius: 0;
            }}
            QFrame#runtimeBodyCard QLabel {{
                border: 0;
            }}
            QWidget#filePickerRow,
            QFrame#card QLabel,
            QFrame#card QCheckBox {{
                background: {CARD_BG};
            }}
            QLabel#hint {{
                color: {HINT};
            }}
                        QLabel#runtimeTitle {{
                color: {TEXT};
                font-size: 13px;
                font-weight: 600;
                padding: 0 0 2px 0;
            }}

            QLabel#runtimeJobTitle {{
                color: {TEXT};
                font-size: 12px;
                font-weight: 500;
            }}

            QLabel#runtimeStatus {{
                color: #64748b;
                font-size: 12px;
            }}

            QComboBox#runtimeSelector {{
                background: #dbe3ee;
                color: {TEXT};
                border: 0;
                border-radius: 0;
                min-height: 30px;
                max-height: 30px;
                padding: 0 8px;
            }}

            QPlainTextEdit#runtimeMeta {{
                background: #f8fafc;
                color: {TEXT};
                border: 0;
                font-family: Consolas, "Microsoft YaHei", monospace;
                font-size: 12px;
            }}

            QPushButton#warning {{
                background: #facc15;
                color: #111827;
            }}

            QPushButton#warning:hover {{
                background: #eab308;
            }}
            QPushButton#resume {{
                background: #16a34a;
                color: #ffffff;
            }}
            QPushButton#resume:hover {{
                background: #15803d;
            }}
            QPushButton#filePicker {{
                background: #f8fafc;
                color: {HINT};
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                min-height: 30px;
                max-height: 30px;
                padding: 0 8px;
                text-align: center;
                font-weight: 400;
            }}
            QPushButton#filePicker:hover {{
                background: #ffffff;
                border-color: #94a3b8;
            }}
            QLineEdit {{
                background: #ffffff;
                color: {TEXT};
                border: 1px solid #9ca3af;
                border-radius: 0;
                min-height: 28px;
                max-height: 28px;
                padding: 0 6px;
            }}
            QSpinBox {{
                background: #ffffff;
                color: {TEXT};
                border: 1px solid #9ca3af;
                border-radius: 0;
                min-height: 28px;
                max-height: 28px;
                padding: 0 20px 0 6px;
            }}
            QSpinBox::up-button,
            QSpinBox::down-button {{
                subcontrol-origin: border;
                width: 16px;
            }}
            QSpinBox::up-button {{
                subcontrol-position: top right;
            }}
            QSpinBox::down-button {{
                subcontrol-position: bottom right;
            }}
            QSpinBox#plainSpin,
            QLineEdit#submitParamEdit,
            QComboBox#submitParamCombo,
            QSpinBox#queueMaxParallelSpin {{
                background: #f8fafc;
                color: {TEXT};
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                min-height: 30px;
                max-height: 30px;
                padding: 0 8px;
                font-weight: 500;
            }}
            QComboBox#submitParamCombo {{
                padding: 0 22px 0 8px;
            }}
            QComboBox#submitParamCombo::drop-down {{
                width: 18px;
                border: 0;
            }}
            QComboBox {{
                background: #ffffff;
                color: {TEXT};
                border: 1px solid #9ca3af;
                border-radius: 0;
                min-height: 28px;
                max-height: 28px;
                padding: 0 22px 0 6px;
            }}
            QComboBox::drop-down {{
                width: 12px;
                border: 0;
            }}
            QPlainTextEdit#log {{
                background: #f8fafc;
                color: #111827;
                border: 1px solid #9ca3af;
                border-radius: 0;
                font-family: Consolas, "Microsoft YaHei", monospace;
                font-size: 12px;
            }}
            QFrame#runtimeLogFrame {{
                background: #f8fafc;
                border: 1px solid #9ca3af;
                border-radius: 0;
            }}
            QLabel#staStickyHeader {{
                background: #f8fafc;
                color: #111827;
                border: 0;
                border-bottom: 1px solid #9ca3af;
                padding: 2px 3px;
                font-family: Consolas, "Microsoft YaHei", monospace;
                font-size: 12px;
            }}
            QPlainTextEdit#runtimeLog {{
                background: #f8fafc;
                color: #111827;
                border: 0;
                border-radius: 0;
                font-family: Consolas, "Microsoft YaHei", monospace;
                font-size: 12px;
            }}
            QPushButton {{
                background: {LIGHT};
                color: {TEXT};
                border: 0;
                border-radius: 7px;
                padding: 6px 10px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {LIGHT_HOVER};
            }}
            QPushButton#light {{
                background: {LIGHT};
                color: {TEXT};
            }}
            QPushButton#light:hover {{
                background: {LIGHT_HOVER};
            }}
            QPushButton#primary {{
                background: {PRIMARY};
                color: #ffffff;
            }}
            QPushButton#primary:hover {{
                background: {PRIMARY_HOVER};
            }}
            QPushButton#danger {{
                background: {DANGER};
                color: #ffffff;
            }}
            QPushButton#danger:hover {{
                background: #991b1b;
            }}
            QPushButton:disabled {{
                background: #e5e7eb;
                color: #94a3b8;
            }}
            """
