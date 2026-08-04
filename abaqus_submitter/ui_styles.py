"""Qt UI constants and stylesheet for the main window."""

APP_TITLE = "Abaqus Submitter"

# The cluster-console navigation stays fixed while topology and inspectors stretch.
LEFT_PANEL_MIN_WIDTH = 208

# The topology, inspector, and event timeline need a desktop-sized viewport.
WINDOW_OUTER_HORIZONTAL_MARGIN = 24
PANEL_HORIZONTAL_SPACING = 12
COMPACT_WINDOW_MIN_WIDTH = 1280

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
BUTTON_HEIGHT = 28
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
                padding: 0 10px;
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
                padding-top: 1px;
            }}
            QPushButton#primary:pressed {{
                background: #1e40af;
            }}
            QPushButton#danger:pressed {{
                background: #991b1b;
            }}
            QPushButton:disabled {{
                background: #e5e7eb;
                color: #94a3b8;
                border-color: transparent;
            }}
            """


def _path_picker_styles() -> str:
    return f"""
            QPushButton#pathPicker {{
                background: #f8fafc;
                color: #475569;
                border: 1px solid {BORDER};
                border-radius: 5px;
                min-width: 30px;
                max-width: 30px;
                min-height: {BUTTON_HEIGHT}px;
                max-height: {BUTTON_HEIGHT}px;
                padding: 0;
                font-weight: 600;
            }}
            QPushButton#pathPicker:hover {{
                background: #ffffff;
                border-color: #94a3b8;
            }}
            QPushButton#pathPicker:disabled {{
                background: #f1f5f9;
                color: #94a3b8;
                border-color: {BORDER_SOFT};
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
            QLineEdit:disabled {{
                background: #f1f5f9;
                color: #94a3b8;
                border-color: {BORDER_SOFT};
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
                border-left: 1px solid {BORDER_SOFT};
                margin: 1px 1px 1px 0;
            }}
            QComboBox::down-arrow {{
                image: none;
                width: 0;
                height: 0;
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


def _segmented_spinbox_styles() -> str:
    return f"""
            QSpinBox[segmentedSpin="true"] {{
                background: {FIELD_BG};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 6px;
                min-width: 112px;
                min-height: 30px;
                max-height: 30px;
                padding: 0;
                selection-background-color: #bfdbfe;
            }}
            QSpinBox[segmentedSpin="true"]:focus {{
                border-color: {FOCUS};
            }}
            QSpinBox#resourceCpuSpin {{
                min-width: 178px;
                max-width: 178px;
            }}
            QSpinBox[segmentedSpin="true"]::up-button,
            QSpinBox[segmentedSpin="true"]::down-button {{
                width: 0;
                height: 0;
                border: 0;
            }}
            QSpinBox[segmentedSpin="true"] QLineEdit {{
                background: transparent;
                border: 0;
                border-radius: 0;
                min-height: 0;
                max-height: 16777215px;
                padding: 0;
            }}
            QSpinBox[segmentedSpin="true"] QToolButton#spinStepDown,
            QSpinBox[segmentedSpin="true"] QToolButton#spinStepUp {{
                background: #f8fafc;
                color: #334155;
                border: 0;
                border-radius: 0;
                min-width: 0;
                max-width: 16777215px;
                min-height: 0;
                max-height: 16777215px;
                padding: 0;
                font-size: 13px;
                font-weight: 600;
            }}
            QSpinBox[segmentedSpin="true"] QToolButton#spinStepDown {{
                border-right: 1px solid {BORDER_SOFT};
                border-top-left-radius: 5px;
                border-bottom-left-radius: 5px;
            }}
            QSpinBox[segmentedSpin="true"] QToolButton#spinStepUp {{
                border-left: 1px solid {BORDER_SOFT};
                border-top-right-radius: 5px;
                border-bottom-right-radius: 5px;
            }}
            QSpinBox[segmentedSpin="true"] QToolButton:hover {{
                background: #e8f0fc;
                color: #1d4ed8;
            }}
            QSpinBox[segmentedSpin="true"] QToolButton:pressed {{
                background: #d8e6fa;
                color: #1d4ed8;
            }}
            QSpinBox[segmentedSpin="true"] QToolButton:disabled {{
                background: #f8fafc;
                color: #b8c2d0;
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


def _combo_popup_styles() -> str:
    return f"""
            QAbstractItemView#workbenchComboPopup {{
                background: transparent;
                color: {TEXT};
                border: 0;
                border-radius: 0;
                padding: 3px;
                outline: 0;
                selection-background-color: {SELECTION_BG};
                selection-color: {SELECTION_TEXT};
            }}
            QAbstractItemView#workbenchComboPopup::item {{
                min-height: 26px;
                padding: 0 8px;
                border: 0;
                border-radius: 3px;
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
                border-radius: 6px;
                padding: 3px;
                outline: 0;
                selection-background-color: transparent;
                selection-color: {SELECTION_TEXT};
            }}
            """


def build_queue_manager_stylesheet(*, compact: bool = False) -> str:
    compact_styles = (
        """
            QDialog#embeddedQueueManager QGroupBox {
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 10px;
            }
            QDialog#embeddedQueueManager QTableWidget {
                border-radius: 0;
            }
            QDialog#embeddedQueueManager QPushButton {
                min-height: 28px;
                max-height: 28px;
                padding: 0 8px;
                border-radius: 4px;
            }
            QDialog#embeddedQueueManager QPushButton:pressed {
                padding-top: 1px;
            }
        """
        if compact
        else ""
    )
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
            {_path_picker_styles()}
            {compact_styles}
            {_field_styles()}
            {_segmented_spinbox_styles()}
            {_table_styles()}
            {_combo_popup_styles()}
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
            QMainWindow#mainWindow {{
                border: 1px solid {BORDER};
            }}
            QFrame#framelessTitleBar {{
                background: {CARD_BG};
                border: 0;
                border-bottom: 1px solid {BORDER_SOFT};
            }}
            QFrame#framelessTitleBar[maximized="true"] {{
                border-bottom-color: {BORDER};
            }}
            QLabel#framelessWindowTitle {{
                color: #334155;
                background: #e2e8f0;
                border: 1px solid #cbd5e1;
                border-radius: 5px;
                min-height: 22px;
                max-height: 22px;
                padding: 0 8px;
                font-size: 12px;
                font-weight: 600;
            }}
            QToolButton#windowMinimizeButton,
            QToolButton#windowMaximizeButton,
            QToolButton#windowCloseButton {{
                min-width: 16px;
                max-width: 16px;
                min-height: 16px;
                max-height: 16px;
                padding: 0;
                border: 1px solid transparent;
                border-radius: 8px;
            }}
            QToolButton#windowMinimizeButton {{
                background: #fbbf24;
                border-color: #e5a812;
            }}
            QToolButton#windowMaximizeButton {{
                background: #34c759;
                border-color: #25a244;
            }}
            QToolButton#windowCloseButton {{
                background: #ff5f57;
                border-color: #e54841;
            }}
            QToolButton#windowMinimizeButton:hover {{
                background: #f59e0b;
                color: #78350f;
            }}
            QToolButton#windowMaximizeButton:hover {{
                background: #15803d;
                border-color: #166534;
            }}
            QToolButton#windowCloseButton:hover {{
                background: #ef4444;
                color: #7f1d1d;
            }}
            QToolButton#windowMinimizeButton:pressed,
            QToolButton#windowMaximizeButton:pressed,
            QToolButton#windowCloseButton:pressed {{
                margin-top: 1px;
            }}
            QLabel,
            QCheckBox {{
                background: transparent;
            }}
            QWidget#formFieldRow {{
                background: transparent;
            }}
            QWidget#resourceInlineGroup {{
                background: transparent;
            }}
            QWidget#clusterShell,
            QWidget#topologyContent,
            QScrollArea#topologyScroll,
            QScrollArea#topologyScroll > QWidget > QWidget {{
                background: {APP_BG};
            }}
            QWidget#workbenchShell {{
                background: #f5f7fa;
            }}
            QMenuBar#workbenchMenuBar {{
                background: {CARD_BG};
                color: {TEXT};
                border: 0;
                padding: 2px 6px;
            }}
            QMenuBar#workbenchMenuBar::item {{
                background: transparent;
                padding: 5px 9px;
            }}
            QMenuBar#workbenchMenuBar::item:selected {{
                background: #e2e8f0;
                border-radius: 4px;
            }}
            QMenu#workbenchPopupMenu {{
                background: transparent;
                color: {TEXT};
                border: 0;
                padding: 3px;
            }}
            QFrame#workbenchComboContainer {{
                background: transparent;
                border: 0;
            }}
            QMenu#workbenchPopupMenu::item {{
                background: transparent;
                border: 0;
                border-radius: 3px;
                padding: 6px 26px 6px 10px;
                margin: 0;
            }}
            QMenu#workbenchPopupMenu::item:selected {{
                background: {SELECTION_BG};
                color: {SELECTION_TEXT};
            }}
            QMenu#workbenchPopupMenu::item:disabled {{
                color: #94a3b8;
            }}
            QMenu#workbenchPopupMenu::separator {{
                height: 1px;
                background: {BORDER_SOFT};
                margin: 3px 6px;
            }}
            QPushButton#success:disabled,
            QPushButton#outlineDanger:disabled {{
                background: #e5e7eb;
                color: #94a3b8;
                border-color: #d1d5db;
            }}
            QComboBox#connectionState {{
                min-width: 190px;
            }}
            QFrame#projectExplorer,
            QFrame#propertiesPanel {{
                background: {CARD_BG};
                border: 1px solid {BORDER};
                border-radius: 0;
            }}
            QFrame#dockHeader,
            QFrame#explorerToolbar {{
                background: #f8fafc;
                border: 0;
                border-bottom: 1px solid {BORDER_SOFT};
            }}
            QLabel#dockTitle {{
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#toolIcon {{
                background: transparent;
                color: #475569;
                border: 0;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                padding: 0;
            }}
            QPushButton#toolIcon:hover {{
                background: #e2e8f0;
            }}
            QPushButton#compactPicker {{
                background: #f8fafc;
                color: #475569;
                border: 1px solid {BORDER};
                border-radius: 5px;
                min-width: 30px;
                max-width: 30px;
                min-height: 28px;
                max-height: 28px;
                padding: 0;
            }}
            QTreeWidget#projectTree {{
                background: {CARD_BG};
                color: {TEXT};
                border: 0;
                outline: 0;
                show-decoration-selected: 0;
            }}
            QTreeWidget#projectTree::item {{
                min-height: 25px;
                padding: 1px 3px;
            }}
            QTreeWidget#projectTree::item:hover {{
                background: #f1f5f9;
                color: {TEXT};
            }}
            QFrame#resourceSummary {{
                background: #f8fafc;
                border: 1px solid {BORDER_SOFT};
                border-radius: 7px;
                margin: 6px;
            }}
            QWidget#resourceChoices {{
                background: transparent;
                border: 0;
            }}
            QFrame#resourceNodeSummary {{
                min-height: 108px;
                max-height: 108px;
                background: transparent;
                border: 0;
                border-left: 2px solid transparent;
                border-bottom: 1px solid {BORDER_SOFT};
                border-radius: 0;
            }}
            QFrame#resourceNodeSummary:hover {{
                background: #f1f5f9;
            }}
            QFrame#resourceNodeSummary[selected="true"] {{
                background: #f8fbff;
                border-left: 2px solid {PRIMARY};
            }}
            QFrame#resourceNodeSummary QLabel {{
                background: transparent;
                border: 0;
                color: {TEXT};
                font-weight: 400;
            }}
            QLabel#resourceNodeName {{
                font-weight: 600;
            }}
            QLabel#resourceNodeMetricName {{
                font-size: 11px;
            }}
            QLabel#resourceNodeMetricValue {{
                color: #475569;
                font-size: 11px;
            }}
            QComboBox#resourceSelector {{
                min-height: 22px;
                max-height: 22px;
                padding: 0 22px 0 7px;
            }}
            QComboBox#resourceSelector::drop-down {{
                width: 18px;
            }}
            QLabel#explorerFooter {{
                color: #64748b;
                background: #f8fafc;
                border-top: 1px solid {BORDER_SOFT};
                padding: 7px 9px;
            }}
            QTabWidget#workbenchTabs::pane,
            QTabWidget#workbenchLogDock::pane {{
                background: {CARD_BG};
                border: 1px solid {BORDER};
                top: -1px;
            }}
            QTabWidget#workbenchTabs QTabBar::tab,
            QTabWidget#workbenchLogDock QTabBar::tab {{
                background: #eef2f7;
                color: #475569;
                border: 1px solid {BORDER_SOFT};
                padding: 7px 15px;
                min-width: 104px;
            }}
            QTabWidget#workbenchTabs QTabBar::tab:selected,
            QTabWidget#workbenchLogDock QTabBar::tab:selected {{
                background: {CARD_BG};
                color: {PRIMARY};
                border-top: 2px solid {PRIMARY};
                border-bottom-color: {CARD_BG};
                font-weight: 700;
            }}
            QWidget#jobConfiguration {{
                background: {CARD_BG};
            }}
            QWidget#jobConfiguration QLabel,
            QWidget#jobConfiguration QCheckBox {{
                background: transparent;
            }}
            QLabel#workbenchPageTitle {{
                font-size: 15px;
                font-weight: 700;
            }}
            QGroupBox {{
                background: {CARD_BG};
                border: 1px solid {BORDER};
                border-radius: 6px;
                margin-top: 10px;
                font-weight: 700;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 9px;
                padding: 0 5px;
                background: {CARD_BG};
            }}
            QWidget#odbFlow {{
                background: #fbfdff;
                border: 0;
            }}
            QLabel#flowNode {{
                color: #1d4ed8;
                background: #ffffff;
                border: 1px solid #3b82f6;
                border-radius: 6px;
                padding: 7px 6px;
            }}
            QLabel#flowArrow {{
                color: #2563eb;
                font-size: 18px;
                font-weight: 700;
            }}
            QLabel#flowResult {{
                color: #6d28d9;
                background: #faf5ff;
                border: 1px solid #8b5cf6;
                border-radius: 6px;
                padding: 7px 6px;
                font-weight: 700;
            }}
            QLabel#emptyState {{
                color: #64748b;
                background: {CARD_BG};
                font-size: 13px;
            }}
            QPushButton#success {{
                background: #16a34a;
                color: #ffffff;
            }}
            QPushButton#success:hover {{
                background: #15803d;
            }}
            QPushButton#outlineDanger {{
                background: #ffffff;
                color: #dc2626;
                border: 1px solid #ef4444;
            }}
            QPlainTextEdit#workbenchHistory,
            QTableWidget#dockTable {{
                background: #ffffff;
                alternate-background-color: #f8fafc;
                color: {TEXT};
                border: 0;
                gridline-color: #e5e7eb;
            }}
            QHeaderView#dockTableHeader {{
                background: {TABLE_HEADER_BG};
                border: 0;
                border-bottom: 1px solid {TABLE_GRID};
            }}
            QHeaderView#dockTableHeader::section {{
                background: {TABLE_HEADER_BG};
                color: {TEXT};
                border: 0;
                border-right: 1px solid {TABLE_GRID};
                padding: 0 8px;
                font-weight: 600;
            }}
            QSplitter#workbenchOuterSplitter::handle,
            QSplitter#workbenchUpperSplitter::handle,
            QSplitter#workbenchMainSplitter::handle {{
                background: #dbe4ef;
                width: 4px;
                height: 4px;
            }}
            QFrame#clusterTopBar {{
                background: {CARD_BG};
                border: 1px solid {BORDER_SOFT};
                border-radius: {RADIUS_CARD}px;
            }}
            QLabel#appTitle {{
                color: {TEXT};
                font-size: 18px;
                font-weight: 700;
                padding-right: 8px;
            }}
            QLineEdit#globalSearch {{
                background: #f8fafc;
                border-color: {BORDER_SOFT};
            }}
            QFrame#clusterNavigation {{
                background: {CARD_BG};
                border: 1px solid {BORDER_SOFT};
                border-radius: {RADIUS_CARD}px;
            }}
            QPushButton#navButton,
            QPushButton#navSelected {{
                background: transparent;
                color: {TEXT};
                border: 0;
                border-radius: 7px;
                min-height: 38px;
                max-height: 38px;
                padding: 0 12px;
                text-align: left;
                font-weight: 500;
            }}
            QPushButton#navButton:hover {{
                background: #f1f5f9;
            }}
            QPushButton#navSelected {{
                background: #dbeafe;
                color: #1d4ed8;
                border-left: 3px solid {PRIMARY};
                font-weight: 700;
            }}
            QFrame#navigationStatusCard {{
                background: #f8fafc;
                border: 1px solid {BORDER_SOFT};
                border-radius: 8px;
            }}
            QFrame#dashboardCard {{
                background: {CARD_BG};
                border: 1px solid {BORDER_SOFT};
                border-radius: {RADIUS_CARD}px;
            }}
            QFrame#runtimeInspector {{
                background: {CARD_BG};
                border: 0;
                border-radius: 0;
            }}
            QFrame#dashboardCard QLabel,
            QFrame#runtimeInspector QLabel,
            QFrame#runtimeInspector QCheckBox {{
                background: transparent;
            }}
            QProgressBar {{
                background: #e2e8f0;
                color: {TEXT};
                border: 0;
                border-radius: 4px;
                min-height: 8px;
                max-height: 8px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background: {PRIMARY};
                border-radius: 4px;
            }}
            QLabel#mergeFlow,
            QLabel#mergeFlowLarge {{
                color: #6d28d9;
                background: #faf5ff;
                border: 1px solid #d8b4fe;
                border-radius: 7px;
                padding: 7px 9px;
                font-weight: 700;
            }}
            QLabel#mergeFlowLarge {{
                font-size: 13px;
                padding: 10px 12px;
            }}
            QLabel#infoBanner {{
                color: #1e40af;
                background: #eff6ff;
                border: 1px solid #bfdbfe;
                border-radius: 7px;
                padding: 7px 9px;
            }}
            QLabel#successBanner {{
                color: #166534;
                background: #f0fdf4;
                border: 1px solid #bbf7d0;
                border-radius: 7px;
                padding: 7px 9px;
            }}
            QLabel#successText {{
                color: #15803d;
                font-weight: 600;
            }}
            QLabel#warningText {{
                color: #c2410c;
                font-weight: 600;
            }}
            QPushButton#segmented,
            QPushButton#segmentedSelected {{
                min-height: 28px;
                max-height: 28px;
                background: #f8fafc;
                color: #475569;
                border: 1px solid {BORDER};
                border-radius: 5px;
                padding: 0 10px;
                font-weight: 500;
            }}
            QPushButton#segmentedSelected {{
                background: {PRIMARY};
                color: #ffffff;
                border-color: {PRIMARY};
            }}
            QPlainTextEdit#eventLog {{
                background: #f8fafc;
                color: {TEXT};
                border: 1px solid {BORDER_SOFT};
                border-radius: 7px;
                font-family: "Cascadia Mono", Consolas, "Microsoft YaHei", monospace;
                font-size: 11px;
            }}
            QTabWidget#inspectorTabs::pane {{
                background: {CARD_BG};
                border: 1px solid {BORDER_SOFT};
                border-radius: 8px;
                top: -1px;
            }}
            QTabWidget#inspectorTabs QTabBar::tab {{
                background: #e9eff7;
                color: #475569;
                border: 1px solid {BORDER_SOFT};
                padding: 8px 12px;
                min-width: 104px;
            }}
            QTabWidget#inspectorTabs QTabBar::tab:selected {{
                background: {CARD_BG};
                color: {PRIMARY};
                font-weight: 700;
                border-bottom-color: {CARD_BG};
            }}
            QScrollArea#inspectorScroll,
            QScrollArea#inspectorScroll > QWidget > QWidget {{
                background: {APP_BG};
            }}
            QSplitter#clusterSplitter::handle {{
                background: transparent;
                width: 6px;
            }}
            QDialog#submissionWizard {{
                background: {APP_BG};
            }}
            QFrame#wizardHeader,
            QFrame#wizardFooter {{
                background: {CARD_BG};
                border: 0;
                border-bottom: 1px solid {BORDER_SOFT};
            }}
            QFrame#wizardFooter {{
                border-top: 1px solid {BORDER_SOFT};
                border-bottom: 0;
            }}
            QLabel#wizardTitle {{
                font-size: 17px;
                font-weight: 700;
            }}
            QFrame#wizardSteps,
            QFrame#wizardSummary {{
                background: {CARD_BG};
                border: 1px solid {BORDER_SOFT};
                border-radius: {RADIUS_CARD}px;
            }}
            QListWidget#stepList {{
                background: transparent;
                border: 0;
                outline: 0;
            }}
            QListWidget#stepList::item {{
                border-radius: 7px;
                min-height: 44px;
                padding: 0 9px;
            }}
            QListWidget#stepList::item:selected {{
                background: #dbeafe;
                color: #1d4ed8;
                font-weight: 700;
            }}
            QRadioButton#locationChoice {{
                background: {CARD_BG};
                border: 1px solid {BORDER};
                border-radius: 9px;
                padding: 10px 12px;
                font-weight: 600;
            }}
            QRadioButton#locationChoice:checked {{
                background: #eff6ff;
                border: 2px solid {PRIMARY};
                color: #1d4ed8;
            }}
            QLabel#reviewText {{
                color: {TEXT};
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
                border-radius: 4px;
            }}
            QScrollArea#runtimeMeta > QWidget > QWidget {{
                background: #f8fafc;
            }}
            QWidget#runtimeMetaContent {{
                background: #f8fafc;
            }}
            QFrame#metaSection {{
                background: #ffffff;
                border: 0;
                border-bottom: 1px solid #e2e8f0;
                border-radius: 0;
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
                border-radius: 4px;
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
            QLineEdit:disabled {{
                background: #f1f5f9;
                color: #94a3b8;
                border-color: {BORDER_SOFT};
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
            QLineEdit#submitParamEdit,
            QComboBox#submitParamCombo {{
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
                border-left: 1px solid {BORDER_SOFT};
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
                border-left: 1px solid {BORDER_SOFT};
            }}
            QComboBox::down-arrow {{
                image: none;
                width: 0;
                height: 0;
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
                border-radius: 4px;
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
            {_path_picker_styles()}
            {_segmented_spinbox_styles()}
            {_combo_popup_styles()}
            {_scrollbar_styles()}
            """
