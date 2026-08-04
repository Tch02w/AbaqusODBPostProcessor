from __future__ import annotations

from pathlib import Path

from abaqus_workbench.routes import odb_candidate_for_job


def test_job_route_prefers_run_metadata() -> None:
    candidate = odb_candidate_for_job(
        {"work_dir": r"G:\jobs\case-a", "job_name": "foundation"},
        fallback_work_dir=r"G:\fallback",
        fallback_job_name="fallback",
    )

    assert candidate == Path(r"G:\jobs\case-a\foundation.odb")


def test_job_route_can_open_a_work_directory_without_job_name() -> None:
    assert odb_candidate_for_job(None, fallback_work_dir=r"G:\jobs") == Path(
        r"G:\jobs"
    )


def test_job_route_rejects_missing_work_directory() -> None:
    assert odb_candidate_for_job(None) is None
