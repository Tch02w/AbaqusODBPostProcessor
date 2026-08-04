"""Pure path routing between the submitter and postprocessor components."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def odb_candidate_for_job(
    run: Mapping[str, object] | None,
    *,
    fallback_work_dir: str = "",
    fallback_job_name: str = "",
) -> Path | None:
    """Return the expected ODB path (or work directory) for a Submitter job."""

    record = run or {}
    work_dir = str(record.get("work_dir") or fallback_work_dir).strip()
    if not work_dir:
        return None
    job_name = str(record.get("job_name") or fallback_job_name).strip()
    directory = Path(work_dir).expanduser()
    return directory / f"{job_name}.odb" if job_name else directory
