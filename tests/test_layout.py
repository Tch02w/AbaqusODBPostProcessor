from __future__ import annotations

import tempfile
from pathlib import Path

from abaqus_odb_postprocessor.config import abaqus_script
from abaqus_odb_postprocessor.paths import (
    batch_temp_dir,
    result_root_for_odb,
    scan_cache_dir,
)


def test_runtime_scripts_are_single_current_entries() -> None:
    for name in (
        "scan_odb.py",
        "scan_legend_worker.py",
        "extract_job_worker.py",
        "render_group_contours.py",
    ):
        assert abaqus_script(name).is_file()


def test_cache_is_in_system_temp() -> None:
    cache = scan_cache_dir().resolve()
    assert cache.is_relative_to(Path(tempfile.gettempdir()).resolve())


def test_batch_scratch_is_in_system_temp() -> None:
    scratch = batch_temp_dir("test-batch").resolve()
    assert scratch.is_relative_to(Path(tempfile.gettempdir()).resolve())


def test_results_are_next_to_odb(tmp_path: Path) -> None:
    odb = tmp_path / "case.odb"
    assert result_root_for_odb(odb) == tmp_path / "AbaqusODBPostProcessor_Results"
