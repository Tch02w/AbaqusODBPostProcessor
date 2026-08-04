import os
import subprocess
import sys
import textwrap
import unittest


@unittest.skipUnless(
    sys.platform == "win32",
    "Native Qt startup regression is Windows-specific.",
)
class NativeWindowsStartupTests(unittest.TestCase):
    def test_main_window_constructs_with_native_qt_backend(self) -> None:
        script = textwrap.dedent(
            """
            from abaqus_submitter.main import MainWindow
            from abaqus_submitter.qt_compat import QtCore, QtWidgets

            app = QtWidgets.QApplication([])
            window = MainWindow()
            window.show()
            QtCore.QTimer.singleShot(0, window.close)
            QtCore.QTimer.singleShot(0, app.quit)
            exit_code = app.exec()
            print("native-main-window-started", flush=True)
            raise SystemExit(exit_code)
            """
        )
        environment = os.environ.copy()
        environment.pop("QT_QPA_PLATFORM", None)
        environment["PYTHONFAULTHANDLER"] = "1"

        result = subprocess.run(
            [sys.executable, "-X", "faulthandler", "-c", script],
            cwd=os.getcwd(),
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("native-main-window-started", result.stdout)
        self.assertNotIn("Exception ignored", result.stderr)
        self.assertNotIn("_RoundedPopupSurface", result.stderr)
