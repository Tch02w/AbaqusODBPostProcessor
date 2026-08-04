"""支持 ``python -m abaqus_submitter`` 和项目脚本入口。"""

from __future__ import annotations

import time

from .diagnostics import StartupTimeline, startup_timeline_enabled


def run() -> int:
    startup_start = time.monotonic()
    timeline = StartupTimeline(
        "Entry",
        enabled=startup_timeline_enabled(),
        start=startup_start,
    )
    timeline.mark("script-start")

    from .application import main

    timeline.mark("import-main-module")
    return main(
        startup_timeline_start=startup_start,
        startup_timeline_last=timeline.last,
        startup_timeline_enabled=timeline.enabled,
    )


if __name__ == "__main__":
    raise SystemExit(run())
