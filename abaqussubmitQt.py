"""Qt entry point for the Abaqus submitter main window."""

import time

from abaqus_submitter_qt.diagnostics import StartupTimeline, startup_timeline_enabled

_STARTUP_TIMELINE_START = time.monotonic()
_startup_timeline = StartupTimeline(
    "Entry",
    enabled=startup_timeline_enabled(),
    start=_STARTUP_TIMELINE_START,
)


_startup_timeline.mark("script-start")

from abaqus_submitter_qt.main import main  # noqa: E402

_startup_timeline.mark("import-main-module")


if __name__ == "__main__":
    main(
        startup_timeline_start=_STARTUP_TIMELINE_START,
        startup_timeline_last=_startup_timeline.last,
        startup_timeline_enabled=_startup_timeline.enabled,
    )
