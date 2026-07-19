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
LIGHT = "#e7edf6"
LIGHT_HOVER = "#d8e2ef"
DANGER = "#dc2626"
DANGER_HOVER = "#b91c1c"
BORDER = "#c7d2e1"
BORDER_SOFT = "#dbe4ef"
FOCUS = "#60a5fa"
FIELD_BG = "#ffffff"
FIELD_MUTED_BG = "#f8fafc"
TABLE_HEADER_BG = "#edf2f8"
TABLE_GRID = "#e5e7eb"
SELECTION_BG = "#dbeafe"
SELECTION_TEXT = "#0f172a"
RUNTIME_SELECTOR_FALLBACK_BG = "#e2e8f0"

RADIUS_CARD = 10
RADIUS_CONTROL = 7
CONTROL_HEIGHT = 32
BUTTON_HEIGHT = 30
SCROLLBAR_BG = "#f8fafc"
SCROLLBAR_HANDLE = "#cbd5e1"
SCROLLBAR_HANDLE_HOVER = "#94a3b8"


def _app_font_stack() -> str:
    return '"Microsoft YaHei", "Segoe UI", sans-serif'


def _button_styles() -> str:
    return f"""
            QPushButton {{
                background: {LIGHT};
                color: {TEXT};
                border: 1px solid transparent;
                border-radius: {RADIUS_CONTROL}px;
                min-height: {BUTTON_HEIGHT}px;
                max-height: {BUTTON_HEIGHT}px;
                padding: 3px 10px;
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
                background: {DANGER_HOVER};
            }}
            QPushButton:pressed {{
                background: #cbd5e1;
                padding-top: 4px;
            }}
            QPushButton#primary:pressed {{
                background: #1e40af;
            }}
            QPushButton#danger:pressed {{
                background: #991b1b;
            }}
            QPushButton:focus {{
                border-color: {FOCUS};
            }}
            QPushButton:disabled {{
                background: #e5e7eb;
                color: #94a3b8;
                border-color: transparent;
            }}
            """


def _field_styles() -> str:
    return f"""
            QLineEdit {{
                background: {FIELD_BG};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_CONTROL}px;
                min-height: {CONTROL_HEIGHT}px;
                max-height: {CONTROL_HEIGHT}px;
                padding: 0 8px;
                selection-background-color: #bfdbfe;
            }}
            QLineEdit:focus {{
                border-color: {FOCUS};
                background: {FIELD_BG};
            }}
            QSpinBox {{
                background: {FIELD_BG};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_CONTROL}px;
                min-height: {CONTROL_HEIGHT}px;
                max-height: {CONTROL_HEIGHT}px;
                padding: 0 20px 0 8px;
                selection-background-color: #bfdbfe;
            }}
            QSpinBox:focus {{
                border-color: {FOCUS};
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
            QComboBox {{
                background: {FIELD_BG};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_CONTROL}px;
                min-height: {CONTROL_HEIGHT}px;
                max-height: {CONTROL_HEIGHT}px;
                padding: 0 24px 0 8px;
                selection-background-color: #bfdbfe;
            }}
            QComboBox:focus {{
                border-color: {FOCUS};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 22px;
                border: 0;
                margin: 1px 1px 1px 0;
            }}
            QComboBox QAbstractItemView {{
                background: {FIELD_BG};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_CONTROL}px;
                padding: 4px;
                outline: 0;
                selection-background-color: {SELECTION_BG};
                selection-color: {SELECTION_TEXT};
            }}
            """


def _table_styles() -> str:
    return f"""
            QTableWidget {{
                background: {CARD_BG};
                alternate-background-color: #f8fafc;
                color: {TEXT};
                border: 1px solid {BORDER_SOFT};
                border-radius: {RADIUS_CONTROL}px;
                gridline-color: {TABLE_GRID};
                selection-background-color: {SELECTION_BG};
                selection-color: {TEXT};
            }}
            QHeaderView::section {{
                background: {TABLE_HEADER_BG};
                color: {TEXT};
                border: 0;
                border-right: 1px solid {TABLE_GRID};
                padding: 7px 6px;
                font-weight: 600;
            }}
            """


def _scrollbar_styles() -> str:
    return f"""
            QScrollBar:vertical,
            QScrollBar:horizontal {{
                background: {SCROLLBAR_BG};
                border: 0;
                width: 9px;
                height: 9px;
                margin: 0;
            }}
            QScrollBar::handle:vertical,
            QScrollBar::handle:horizontal {{
                background: {SCROLLBAR_HANDLE};
                border-radius: 4px;
                min-height: 24px;
                min-width: 24px;
            }}
            QScrollBar::handle:vertical:hover,
            QScrollBar::handle:horizontal:hover {{
                background: {SCROLLBAR_HANDLE_HOVER};
            }}
            QScrollBar::add-line,
            QScrollBar::sub-line {{
                width: 0;
                height: 0;
                border: 0;
                background: transparent;
            }}
            QScrollBar::add-page,
            QScrollBar::sub-page {{
                background: transparent;
            }}
            """


def build_runtime_selector_stylesheet(background: str | None = None) -> str:
    background = background or RUNTIME_SELECTOR_FALLBACK_BG
    return f"""
            QComboBox#runtimeSelector {{
                background: {background};
                color: {SELECTION_TEXT};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_CONTROL}px;
                min-height: {CONTROL_HEIGHT}px;
                max-height: {CONTROL_HEIGHT}px;
                padding: 0 24px 0 9px;
                outline: 0;
            }}
            QComboBox#runtimeSelector:hover,
            QComboBox#runtimeSelector:focus,
            QComboBox#runtimeSelector:pressed,
            QComboBox#runtimeSelector:on {{
                background: {background};
                color: {SELECTION_TEXT};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_CONTROL}px;
                padding: 0 24px 0 9px;
                outline: 0;
            }}
            QComboBox#runtimeSelector::drop-down {{
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 22px;
                border: 0;
                margin: 1px 1px 1px 0;
            }}
            QComboBox#runtimeSelector::drop-down:on,
            QComboBox#runtimeSelector::drop-down:pressed,
            QComboBox#runtimeSelector::drop-down:hover {{
                border: 0;
                margin: 1px 1px 1px 0;
            }}
            QComboBox#runtimeSelector QAbstractItemView {{
                background: {FIELD_BG};
                border: 1px solid {BORDER};
                border-radius: 0;
                padding: 0;
                outline: 0;
                selection-background-color: transparent;
                selection-color: {SELECTION_TEXT};
            }}
            """


def build_queue_manager_stylesheet() -> str:
    return f"""
            QDialog {{
                background: {APP_BG};
                color: {TEXT};
                font-family: {_app_font_stack()};
                font-size: 12px;
            }}
            QGroupBox {{
                background: {CARD_BG};
                border: 1px solid {BORDER_SOFT};
                border-radius: {RADIUS_CARD}px;
                margin-top: 12px;
                padding-top: 14px;
                font-weight: 600;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                background: {CARD_BG};
            }}
            QGroupBox QLabel,
            QGroupBox QCheckBox {{
                background: {CARD_BG};
            }}
            QLabel#hint {{
                color: {HINT};
                font-weight: 400;
            }}
            {_button_styles()}
            {_field_styles()}
            {_table_styles()}
            {_scrollbar_styles()}
            """


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
                border: 1px solid {BORDER_SOFT};
                border-radius: {RADIUS_CARD}px;
            }}
            QFrame#runtimeBodyCard {{
                background-color: #ffffff;
                border: 0;
                border-radius: {RADIUS_CARD}px;
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
            QLabel#sectionTitle {{
                color: {TEXT};
                font-size: 13px;
                font-weight: 700;
            }}
            QLabel#unitBadge {{
                background: #f8fafc;
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 6px;
                font-weight: 500;
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
                padding-right: 4px;
            }}

            QComboBox#runtimeSelector {{
                background: #dbe3ee;
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 6px;
                min-height: 30px;
                max-height: 30px;
                padding: 0 24px 0 9px;
            }}

            QScrollArea#runtimeMeta {{
                background: #f8fafc;
                border: 1px solid {BORDER};
                border-radius: {RADIUS_CARD}px;
            }}
            QScrollArea#runtimeMeta > QWidget > QWidget {{
                background: #f8fafc;
            }}
            QWidget#runtimeMetaContent {{
                background: #f8fafc;
            }}
            QFrame#metaSection {{
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 7px;
            }}
            QLabel#metaSectionTitle {{
                color: {TEXT};
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#metaKey {{
                color: #64748b;
                font-size: 12px;
                min-width: 86px;
            }}
            QLabel#metaValue {{
                color: {TEXT};
                font-size: 12px;
            }}
            QLabel#metaEmpty {{
                color: #64748b;
                font-size: 12px;
            }}
            QFrame#memoryStat {{
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
            }}
            QLabel#memoryStatLabel {{
                color: #64748b;
                font-size: 11px;
            }}
            QLabel#memoryStatValue {{
                color: {TEXT};
                font-size: 12px;
                font-weight: 700;
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
                border: 1px solid {BORDER};
                border-radius: 6px;
                min-height: {BUTTON_HEIGHT}px;
                max-height: {BUTTON_HEIGHT}px;
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
                border: 1px solid {BORDER};
                border-radius: 6px;
                min-height: 30px;
                max-height: 30px;
                padding: 0 8px;
                selection-background-color: #bfdbfe;
            }}
            QLineEdit:focus {{
                border-color: {FOCUS};
                background: #ffffff;
            }}
            QSpinBox {{
                background: #ffffff;
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 6px;
                min-height: 30px;
                max-height: 30px;
                padding: 0 20px 0 8px;
                selection-background-color: #bfdbfe;
            }}
            QSpinBox:focus {{
                border-color: {FOCUS};
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
                border: 1px solid {BORDER};
                border-radius: 6px;
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
                border: 1px solid {BORDER};
                border-radius: 6px;
                min-height: 30px;
                max-height: 30px;
                padding: 0 24px 0 8px;
                selection-background-color: #bfdbfe;
            }}
            QComboBox:focus {{
                border-color: {FOCUS};
            }}
            QComboBox::drop-down {{
                width: 20px;
                border: 0;
            }}
            QComboBox QAbstractItemView {{
                background: #ffffff;
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px;
                outline: 0;
                selection-background-color: #dbeafe;
                selection-color: #0f172a;
            }}
            QPlainTextEdit#log {{
                background: #f8fafc;
                color: #111827;
                border: 1px solid #d8e1ee;
                border-radius: 8px;
                font-family: Consolas, "Microsoft YaHei", monospace;
                font-size: 12px;
            }}
            QFrame#runtimeLogFrame {{
                background: {LOG_BG};
                border: 1px solid {BORDER};
                border-radius: {RADIUS_CARD}px;
            }}
            QLabel#staStickyHeader {{
                background: {LOG_BG};
                color: #111827;
                border: 0;
                border-bottom: 1px solid {BORDER};
                padding: 6px 8px 4px 8px;
                font-family: "Cascadia Mono", Consolas, "Microsoft YaHei", monospace;
                font-size: 12px;
            }}
            QPlainTextEdit#runtimeLog {{
                background: transparent;
                color: #111827;
                border: 0;
                border-radius: 0;
                padding: 6px 0 6px 8px;
                font-family: "Cascadia Mono", Consolas, "Microsoft YaHei", monospace;
                font-size: 12px;
                selection-background-color: #bfdbfe;
                selection-color: #0f172a;
            }}
            QPlainTextEdit#runtimeLog QWidget {{
                background: {LOG_BG};
            }}
            QToolTip {{
                background: #0f172a;
                color: #ffffff;
                border: 0;
                padding: 5px 7px;
            }}
            {_button_styles()}
            {_scrollbar_styles()}
            """
