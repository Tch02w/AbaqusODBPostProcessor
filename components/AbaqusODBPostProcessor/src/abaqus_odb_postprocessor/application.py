"""Standalone PySide6 bootstrap for the postprocessor component."""

from __future__ import annotations

import sys

from abaqus_workbench_core.qt import ensure_application

from .app import MainWindow
from .ui_style import apply_application_style


def main(argv: list[str] | None = None) -> int:
    handle = ensure_application(
        sys.argv if argv is None else argv,
        application_name="Abaqus ODB PostProcessor",
    )
    application = handle.application
    apply_application_style(application)
    window = MainWindow()
    window.showMaximized()
    return application.exec() if handle.owns_event_loop else 0
