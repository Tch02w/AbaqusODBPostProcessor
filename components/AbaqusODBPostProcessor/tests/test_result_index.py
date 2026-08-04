from __future__ import annotations

import os
import shutil
import stat
import threading
from pathlib import Path

import pytest

from abaqus_odb_postprocessor.result_browser import (
    classify_result,
    result_section,
)
from abaqus_odb_postprocessor.result_index import (
    INDEX_FILENAME,
    INDEX_SCHEMA_VERSION,
    ResultIndexCancelled,
    ResultIndexInvalid,
    build_result_index,
    index_path,
    load_result_index,
    update_result_index_scopes,
)


def make_time_directory(root: Path, name: str = "20260724_120000") -> Path:
    time_directory = root / "对比组" / name
    first = time_directory / "GJA-1-R_U100D"
    second = time_directory / "GJA-2-R_U100D"
    (first / "data").mkdir(parents=True)
    (second / "plots").mkdir(parents=True)
    (first / "data" / "load_point_raw.csv").write_text(
        "TotalTime,U3\n0,0\n", encoding="utf-8"
    )
    (second / "plots" / "axial_force.png").write_bytes(b"png")
    return time_directory


def test_time_directory_index_is_persistent_and_excludes_itself(
    tmp_path: Path,
) -> None:
    time_directory = make_time_directory(tmp_path)

    metadata = build_result_index(
        time_directory, classify_result, result_section
    )
    loaded = load_result_index(time_directory)

    assert index_path(time_directory) == time_directory / INDEX_FILENAME
    assert index_path(time_directory).is_file()
    assert int(metadata["schema_version"]) == INDEX_SCHEMA_VERSION
    assert metadata["canonical_path"] == str(time_directory.resolve()).lower()
    assert {record["name"] for record in loaded.records} == {
        "load_point_raw.csv",
        "axial_force.png",
    }
    assert all(
        record["name"] != INDEX_FILENAME for record in loaded.records
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows file attributes only")
def test_result_index_stays_hidden_after_incremental_replacement(
    tmp_path: Path,
) -> None:
    time_directory = make_time_directory(tmp_path)
    build_result_index(time_directory, classify_result, result_section)
    database = index_path(time_directory)

    assert database.stat().st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN

    update_result_index_scopes(
        time_directory,
        [time_directory / "GJA-1-R_U100D"],
        classify_result,
        result_section,
    )

    assert database.stat().st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN


def test_copied_index_is_rejected_until_rebuilt(tmp_path: Path) -> None:
    original = make_time_directory(tmp_path / "original")
    build_result_index(original, classify_result, result_section)
    copied = tmp_path / "copied" / original.name
    shutil.copytree(original, copied)

    with pytest.raises(ResultIndexInvalid, match="不匹配"):
        load_result_index(copied)


def test_cancelled_refresh_keeps_the_previous_complete_index(
    tmp_path: Path,
) -> None:
    time_directory = make_time_directory(tmp_path)
    build_result_index(time_directory, classify_result, result_section)
    previous = load_result_index(time_directory)
    (time_directory / "GJA-1-R_U100D" / "new.txt").write_text(
        "new", encoding="utf-8"
    )
    cancellation = threading.Event()
    cancellation.set()

    with pytest.raises(ResultIndexCancelled):
        build_result_index(
            time_directory,
            classify_result,
            result_section,
            cancellation,
        )

    retained = load_result_index(time_directory)
    assert retained.records == previous.records


def test_incremental_update_replaces_only_completed_odb_scope(
    tmp_path: Path,
) -> None:
    time_directory = make_time_directory(tmp_path)
    build_result_index(time_directory, classify_result, result_section)
    first = time_directory / "GJA-1-R_U100D"
    old_file = first / "data" / "load_point_raw.csv"
    old_file.unlink()
    replacement = first / "data" / "timeline_alignment.csv"
    replacement.write_text("Step,Frame\nLoad,1\n", encoding="utf-8")

    update_result_index_scopes(
        time_directory,
        [first],
        classify_result,
        result_section,
    )
    loaded = load_result_index(time_directory)
    names = {record["name"] for record in loaded.records}

    assert "load_point_raw.csv" not in names
    assert "timeline_alignment.csv" in names
    assert "axial_force.png" in names


def test_incremental_scope_treats_underscore_as_a_literal_character(
    tmp_path: Path,
) -> None:
    time_directory = tmp_path / "组A" / "20260724_120000"
    selected = time_directory / "GJA-1-R_U100D"
    similar = time_directory / "GJA-1-RXU100D"
    selected.mkdir(parents=True)
    similar.mkdir(parents=True)
    (selected / "selected.txt").write_text("old", encoding="utf-8")
    (similar / "must-remain.txt").write_text("keep", encoding="utf-8")
    build_result_index(time_directory, classify_result, result_section)

    (selected / "selected.txt").unlink()
    (selected / "replacement.txt").write_text("new", encoding="utf-8")
    update_result_index_scopes(
        time_directory,
        [selected],
        classify_result,
        result_section,
    )

    names = {
        record["name"] for record in load_result_index(time_directory).records
    }
    assert names == {"replacement.txt", "must-remain.txt"}
