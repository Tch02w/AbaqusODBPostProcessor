"""Single QApplication entry point for the merged desktop workbench."""

from __future__ import annotations

import sys

__lazy_modules__ = ["abaqus_workbench.window"]

from abaqus_workbench_core.qt import QtCore, ensure_application
from abaqus_workbench_core.theme import apply_workbench_theme

from .window import IntegratedMainWindow


def main(argv: list[str] | None = None) -> int:
    handle = ensure_application(
        sys.argv if argv is None else argv,
        application_name="Abaqus Workbench",
    )
    application = handle.application
    apply_workbench_theme(application)
    window = IntegratedMainWindow()
    window.showMaximized()
    QtCore.QTimer.singleShot(
        0,
        window.request_restored_status_scan_after_main_window_render,
    )
    return application.exec() if handle.owns_event_loop else 0


if __name__ == "__main__":
    raise SystemExit(main())
