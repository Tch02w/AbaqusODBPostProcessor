"""PySide6 application bootstrap separated from the Submitter main window."""

from __future__ import annotations

import sys

from abaqus_workbench_core.qt import ensure_application
from abaqus_workbench_core.theme import apply_workbench_theme

from .diagnostics import StartupTimeline
from .main import MainWindow
from .qt_compat import QtCore
from .ui_styles import APP_TITLE


def main(
    argv: list[str] | None = None,
    *,
    startup_timeline_start: float | None = None,
    startup_timeline_last: float | None = None,
    startup_timeline_enabled: bool = False,
) -> int:
    startup_timeline = StartupTimeline(
        "App",
        enabled=startup_timeline_enabled,
        start=startup_timeline_start,
        last=startup_timeline_last,
    )
    startup_timeline.mark("main-function-start")
    arguments = list(sys.argv if argv is None else argv)
    startup_timeline.mark("argv-ready")
    handle = ensure_application(arguments, application_name=APP_TITLE)
    application = handle.application
    startup_timeline.mark("qapplication-created")
    apply_workbench_theme(application)
    startup_timeline.mark("qt-style-ready")
    window = MainWindow()
    startup_timeline.mark("mainwindow-created")
    window.show()
    startup_timeline.mark("mainwindow-shown")
    QtCore.QTimer.singleShot(
        0,
        lambda: startup_timeline.mark("event-loop-first-tick"),
    )
    QtCore.QTimer.singleShot(
        0,
        window.request_restored_status_scan_after_main_window_render,
    )
    return application.exec() if handle.owns_event_loop else 0
