import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from abaqus_submitter.qt_compat import QtCore, QtWidgets
from abaqus_submitter.window_chrome_prototype import (
    FramelessResizeController,
    install_frameless_window_chrome,
)
from abaqus_submitter.ui_styles import build_main_stylesheet


class FramelessWindowChromePrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_installed_chrome_replaces_native_frame_and_controls_window(self) -> None:
        window = QtWidgets.QMainWindow()
        window.resize(800, 600)
        title_bar, controller = install_frameless_window_chrome(
            window,
            "Abaqus Submitter",
        )
        window.show()
        title_bar.layout().activate()

        self.assertTrue(
            window.windowFlags() & QtCore.Qt.WindowType.FramelessWindowHint
        )
        self.assertIs(window.menuWidget(), title_bar)
        self.assertIs(controller.parent(), window)
        self.assertEqual(title_bar.minimize_button.toolTip(), "最小化")
        self.assertEqual(title_bar.maximize_button.toolTip(), "最大化")
        self.assertEqual(title_bar.close_button.toolTip(), "关闭")
        self.assertEqual(title_bar.minimize_button.text(), "")
        self.assertEqual(title_bar.maximize_button.text(), "")
        self.assertEqual(title_bar.close_button.text(), "")
        for button in (
            title_bar.minimize_button,
            title_bar.maximize_button,
            title_bar.close_button,
        ):
            self.assertEqual(button.size(), QtCore.QSize(16, 16))
        self.assertEqual(
            title_bar.maximize_button.geometry().left()
            - title_bar.minimize_button.geometry().right()
            - 1,
            11,
        )
        self.assertEqual(
            title_bar.close_button.geometry().left()
            - title_bar.maximize_button.geometry().right()
            - 1,
            11,
        )
        self.assertLess(
            title_bar.minimize_button.x(),
            title_bar.maximize_button.x(),
        )
        self.assertLess(
            title_bar.maximize_button.x(),
            title_bar.close_button.x(),
        )

        title_bar.maximize_button.click()
        self.assertTrue(window.isMaximized())
        self.assertEqual(title_bar.maximize_button.toolTip(), "还原")
        self.assertEqual(title_bar.maximize_button.text(), "")

        title_bar.maximize_button.click()
        self.assertFalse(window.isMaximized())
        title_bar.minimize_button.click()
        self.assertTrue(window.isMinimized())
        window.close()

    def test_green_window_button_has_a_visible_hover_color(self) -> None:
        window = QtWidgets.QMainWindow()
        window.setStyleSheet(build_main_stylesheet())
        title_bar, _controller = install_frameless_window_chrome(
            window,
            "Abaqus Submitter",
        )
        window.show()
        self.app.processEvents()
        button = title_bar.maximize_button
        center = button.rect().center()
        normal_color = button.grab().toImage().pixelColor(center)

        button.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_UnderMouse,
            True,
        )
        QtWidgets.QApplication.sendEvent(
            button,
            QtCore.QEvent(QtCore.QEvent.Type.Enter),
        )
        button.update()
        self.app.processEvents()
        hover_color = button.grab().toImage().pixelColor(center)

        self.assertNotEqual(normal_color, hover_color)
        QtWidgets.QApplication.sendEvent(
            button,
            QtCore.QEvent(QtCore.QEvent.Type.Leave),
        )
        button.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_UnderMouse,
            False,
        )
        window.close()
        self.app.processEvents()

    def test_window_chrome_event_filters_ignore_late_shutdown_events(self) -> None:
        window = QtWidgets.QMainWindow()
        title_bar, controller = install_frameless_window_chrome(
            window,
            "Abaqus Submitter",
        )
        event = QtCore.QEvent(QtCore.QEvent.Type.Paint)
        del title_bar._window
        del controller._window

        self.assertFalse(title_bar.eventFilter(window, event))
        self.assertFalse(controller.eventFilter(window, event))
        window.close()

    def test_application_title_uses_a_rounded_gray_badge(self) -> None:
        window = QtWidgets.QMainWindow()
        window.setStyleSheet(build_main_stylesheet())
        title_bar, _controller = install_frameless_window_chrome(
            window,
            "Abaqus Submitter",
        )
        window.show()
        self.app.processEvents()

        image = title_bar.title_label.grab().toImage()
        corner = image.pixelColor(0, 0)
        interior = image.pixelColor(5, image.height() // 2)
        left_border = image.pixelColor(0, image.height() // 2)

        self.assertEqual(interior.name(), "#e2e8f0")
        self.assertEqual(left_border.name(), "#cbd5e1")
        self.assertNotEqual(corner, left_border)
        self.assertEqual(title_bar.title_label.height(), 24)
        self.assertTrue(
            title_bar.title_label.testAttribute(
                QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents
            )
        )
        window.close()

    def test_application_menu_sits_between_title_and_drag_space(self) -> None:
        window = QtWidgets.QMainWindow()
        window.resize(800, 600)
        title_bar, _controller = install_frameless_window_chrome(
            window,
            "Abaqus Submitter",
        )
        menu_bar = QtWidgets.QMenuBar()
        menu_bar.addMenu("文件")
        menu_bar.addMenu("作业")
        menu_bar.addMenu("服务器")

        title_bar.set_menu_bar(menu_bar)
        window.show()
        self.app.processEvents()

        self.assertIs(menu_bar.parentWidget(), title_bar)
        self.assertGreaterEqual(
            menu_bar.x(),
            title_bar.title_label.geometry().right(),
        )
        self.assertLess(
            menu_bar.geometry().right(),
            title_bar.minimize_button.x(),
        )
        self.assertEqual(menu_bar.height(), title_bar.HEIGHT - 4)
        window.close()

    def test_resize_hit_zones_cover_edges_corners_and_center(self) -> None:
        size = QtCore.QSize(800, 600)
        edge = QtCore.Qt.Edge
        cases = (
            (QtCore.QPoint(0, 0), edge.LeftEdge | edge.TopEdge),
            (QtCore.QPoint(799, 0), edge.RightEdge | edge.TopEdge),
            (QtCore.QPoint(0, 599), edge.LeftEdge | edge.BottomEdge),
            (QtCore.QPoint(799, 599), edge.RightEdge | edge.BottomEdge),
            (QtCore.QPoint(0, 300), edge.LeftEdge),
            (QtCore.QPoint(799, 300), edge.RightEdge),
            (QtCore.QPoint(400, 0), edge.TopEdge),
            (QtCore.QPoint(400, 599), edge.BottomEdge),
            (QtCore.QPoint(400, 300), edge(0)),
        )

        for position, expected in cases:
            with self.subTest(position=position):
                self.assertEqual(
                    FramelessResizeController.resize_edges_for_position(
                        position,
                        size,
                    ),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
