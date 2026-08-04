"""PROTOTYPE: frameless Windows chrome for the Qt main window.

This module answers one UI question: can AbaqusSubmitter replace the native
Windows title bar without losing move, resize, minimise, maximise, or close?
Delete it if the prototype is rejected; absorb the chosen behaviour into the
main Qt shell if it is accepted.
"""

from __future__ import annotations

from .qt_compat import QtCore, QtGui, QtWidgets


class FramelessTitleBar(QtWidgets.QFrame):
    """Compact draggable title bar with macOS-style window controls."""

    HEIGHT = 34
    CONTROL_DIAMETER = 16
    CONTROL_SPACING = 11
    TITLE_MENU_SPACING = 7

    def __init__(self, window: QtWidgets.QMainWindow, title: str) -> None:
        super().__init__(window)
        self._window = window
        self._fallback_drag_offset: QtCore.QPoint | None = None
        self._system_move_started = False
        self.setObjectName("framelessTitleBar")
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 10, 0)
        layout.setSpacing(0)
        self._title_layout = layout
        self._menu_bar: QtWidgets.QMenuBar | None = None

        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setObjectName("framelessWindowTitle")
        self.title_label.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        layout.addWidget(self.title_label)
        layout.addSpacing(self.TITLE_MENU_SPACING)
        layout.addStretch(1)

        self.minimize_button = self._make_control_button(
            "windowMinimizeButton",
            "最小化",
        )
        self.maximize_button = self._make_control_button(
            "windowMaximizeButton",
            "最大化",
        )
        self.close_button = self._make_control_button(
            "windowCloseButton",
            "关闭",
        )
        layout.addWidget(self.minimize_button)
        layout.addSpacing(self.CONTROL_SPACING)
        layout.addWidget(self.maximize_button)
        layout.addSpacing(self.CONTROL_SPACING)
        layout.addWidget(self.close_button)

        self.minimize_button.clicked.connect(self._window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(self._window.close)
        self._window.installEventFilter(self)
        self.sync_window_state()

    def set_menu_bar(self, menu_bar: QtWidgets.QMenuBar) -> None:
        """Place application menus directly after the window title."""
        if self._menu_bar is menu_bar:
            return
        if self._menu_bar is not None:
            self._title_layout.removeWidget(self._menu_bar)
        self._menu_bar = menu_bar
        menu_bar.setNativeMenuBar(False)
        menu_bar.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        menu_bar.setFixedHeight(self.HEIGHT - 4)
        self._title_layout.insertWidget(2, menu_bar)

    @staticmethod
    def _make_control_button(
        object_name: str,
        tooltip: str,
    ) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton()
        button.setObjectName(object_name)
        button.setText("")
        button.setIcon(QtGui.QIcon())
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFixedSize(
            FramelessTitleBar.CONTROL_DIAMETER,
            FramelessTitleBar.CONTROL_DIAMETER,
        )
        button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        button.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        return button

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.sync_window_state()

    def sync_window_state(self) -> None:
        maximized = self._window.isMaximized()
        tooltip = "还原" if maximized else "最大化"
        self.maximize_button.setToolTip(tooltip)
        self.maximize_button.setAccessibleName(tooltip)
        self.setProperty("maximized", maximized)
        self.style().unpolish(self)
        self.style().polish(self)

    def eventFilter(self, watched, event) -> bool:
        window = getattr(self, "_window", None)
        if window is None:
            return False
        try:
            if (
                watched is window
                and event.type() == QtCore.QEvent.Type.WindowStateChange
            ):
                QtCore.QTimer.singleShot(0, self.sync_window_state)
            return super().eventFilter(watched, event)
        except RuntimeError:
            return False

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._fallback_drag_offset = (
            event.globalPosition().toPoint()
            - self._window.frameGeometry().topLeft()
        )
        handle = self._window.windowHandle()
        self._system_move_started = bool(
            handle is not None and handle.startSystemMove()
        )
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if (
            event.buttons() & QtCore.Qt.MouseButton.LeftButton
            and not self._system_move_started
            and self._fallback_drag_offset is not None
            and not self._window.isMaximized()
        ):
            self._window.move(
                event.globalPosition().toPoint() - self._fallback_drag_offset
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._fallback_drag_offset = None
        self._system_move_started = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class FramelessResizeController(QtCore.QObject):
    """Start native Windows edge resizing for a frameless Qt window."""

    RESIZE_MARGIN = 6

    def __init__(self, window: QtWidgets.QMainWindow) -> None:
        super().__init__(window)
        self._window = window
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    @classmethod
    def resize_edges_for_position(
        cls,
        position: QtCore.QPoint,
        size: QtCore.QSize,
    ):
        edges = QtCore.Qt.Edge(0)
        if position.x() <= cls.RESIZE_MARGIN:
            edges |= QtCore.Qt.Edge.LeftEdge
        elif position.x() >= size.width() - cls.RESIZE_MARGIN - 1:
            edges |= QtCore.Qt.Edge.RightEdge
        if position.y() <= cls.RESIZE_MARGIN:
            edges |= QtCore.Qt.Edge.TopEdge
        elif position.y() >= size.height() - cls.RESIZE_MARGIN - 1:
            edges |= QtCore.Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for_edges(edges):
        horizontal = bool(
            edges
            & (QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.RightEdge)
        )
        vertical = bool(
            edges
            & (QtCore.Qt.Edge.TopEdge | QtCore.Qt.Edge.BottomEdge)
        )
        if horizontal and vertical:
            if edges in {
                QtCore.Qt.Edge.LeftEdge | QtCore.Qt.Edge.TopEdge,
                QtCore.Qt.Edge.RightEdge | QtCore.Qt.Edge.BottomEdge,
            }:
                return QtCore.Qt.CursorShape.SizeFDiagCursor
            return QtCore.Qt.CursorShape.SizeBDiagCursor
        if horizontal:
            return QtCore.Qt.CursorShape.SizeHorCursor
        if vertical:
            return QtCore.Qt.CursorShape.SizeVerCursor
        return QtCore.Qt.CursorShape.ArrowCursor

    def _belongs_to_window(self, watched) -> bool:
        window = getattr(self, "_window", None)
        if window is None or not isinstance(watched, QtWidgets.QWidget):
            return False
        try:
            return watched is window or watched.window() is window
        except RuntimeError:
            return False

    def _edges_at_global_position(self, global_position: QtCore.QPoint):
        local_position = self._window.mapFromGlobal(global_position)
        if not self._window.rect().contains(local_position):
            return QtCore.Qt.Edge(0)
        return self.resize_edges_for_position(
            local_position,
            self._window.size(),
        )

    def eventFilter(self, watched, event) -> bool:
        window = getattr(self, "_window", None)
        if window is None or not self._belongs_to_window(watched):
            return False
        try:
            if window.isMaximized() or window.isFullScreen():
                if (
                    event.type() == QtCore.QEvent.Type.MouseMove
                    and window.cursor().shape()
                    != QtCore.Qt.CursorShape.ArrowCursor
                ):
                    window.unsetCursor()
                return False

            if event.type() == QtCore.QEvent.Type.MouseMove:
                edges = self._edges_at_global_position(
                    event.globalPosition().toPoint()
                )
                window.setCursor(self._cursor_for_edges(edges))
            elif (
                event.type() == QtCore.QEvent.Type.MouseButtonPress
                and event.button() == QtCore.Qt.MouseButton.LeftButton
            ):
                edges = self._edges_at_global_position(
                    event.globalPosition().toPoint()
                )
                if edges:
                    handle = window.windowHandle()
                    if handle is not None and handle.startSystemResize(edges):
                        event.accept()
                        return True
            elif event.type() == QtCore.QEvent.Type.Leave:
                if (
                    window.cursor().shape()
                    != QtCore.Qt.CursorShape.ArrowCursor
                ):
                    window.unsetCursor()
        except (AttributeError, RuntimeError):
            return False
        return False


def install_frameless_window_chrome(
    window: QtWidgets.QMainWindow,
    title: str,
) -> tuple[FramelessTitleBar, FramelessResizeController]:
    """Install the prototype chrome on a not-yet-shown main window."""

    window.setObjectName("mainWindow")
    window.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
    title_bar = FramelessTitleBar(window, title)
    window.setMenuWidget(title_bar)
    resize_controller = FramelessResizeController(window)
    return title_bar, resize_controller


__all__ = [
    "FramelessResizeController",
    "FramelessTitleBar",
    "install_frameless_window_chrome",
]
