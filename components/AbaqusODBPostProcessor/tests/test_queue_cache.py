from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from abaqus_odb_postprocessor import batch_window as batch_module
from abaqus_odb_postprocessor.app import MainWindow
from abaqus_odb_postprocessor.models import OdbScan
from abaqus_odb_postprocessor.runner_parallel import MultiProcessController
from abaqus_odb_postprocessor.result_assets import resolve_group_member_asset


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def scan(path: Path) -> OdbScan:
    return OdbScan(
        path=path,
        steps=["Load"],
        assembly_node_sets=["SET-LOAD"],
        assembly_element_sets=[
            "SET-PILE",
            "SET-PILE_CON",
            "SET-KEY",
            "SET-SOIL_CUT",
            "SET-REBAR",
        ],
        field_outputs=["U", "S", "PEMAG"],
    )


def test_sequential_groups_reuse_prescan_and_numeric_cache(
    tmp_path: Path, monkeypatch
) -> None:
    application()
    folder = tmp_path / "odb"
    folder.mkdir()
    odb = folder / "GJA-2-R_U100D.odb"
    odb.write_bytes(b"representative-odb-content")

    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    window._load_folder_state(str(folder))
    window._populate([scan(odb)])
    path = str(odb.resolve())
    snapshot = window._job_payload(window.rows_by_path[path], Path("."))
    calls = {"prescan": 0, "extract": 0, "render": 0}

    def fake_prescan(*_args, **_kwargs):
        calls["prescan"] += 1
        return {
            "frame_catalog": [
                {
                    "SequenceIndex": 0,
                    "ranges": {
                        "PILE_U_MAG": {"min": 0.0, "max": 1.0}
                    },
                },
                {
                    "SequenceIndex": 1,
                    "ranges": {
                        "PILE_U_MAG": {"min": -2.0, "max": 3.0}
                    },
                },
            ],
            "auto_detection": {"prefracture_sequence_index": 0},
        }

    def fake_extract(_command, config_path, _log, _controller):
        calls["extract"] += 1
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        output = Path(config["output_dir"])
        (output / "data").mkdir(parents=True)
        (output / "data" / "timeline_alignment.csv").write_text(
            "SequenceIndex,StepIndex,FrameIndex,StepName\n"
            "0,0,0,Load\n1,0,1,Load\n",
            encoding="utf-8",
        )
        (output / "rebar").mkdir()
        (output / "freebody").mkdir()
        (output / "History_Output").mkdir()
        (output / "metadata.json").write_text("{}", encoding="utf-8")

    def fake_render(_command, config_path, _log, _controller):
        calls["render"] += 1
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        assert Path(config["source_output_dir"]).is_dir()
        assert config["animation_legend_ranges"]["PILE_U_MAG"] == {
            "min": -2.0,
            "max": 3.0,
            "observed_min": -2.0,
            "observed_max": 3.0,
            "source": "comparison_group_full_animation_timeline",
            "comparison_group": config["comparison_group"],
        }

    monkeypatch.setattr(batch_module, "scan_field_ranges", fake_prescan)
    monkeypatch.setattr(batch_module, "run_job", fake_extract)
    monkeypatch.setattr(batch_module, "render_group_contours", fake_render)

    def fake_finalize_numeric(output_dir):
        (Path(output_dir) / "summary.xlsx").write_bytes(b"xlsx")

    monkeypatch.setattr(
        batch_module, "finalize_numeric_output", fake_finalize_numeric
    )
    monkeypatch.setattr(
        batch_module,
        "finalize_render_output",
        lambda *_args, **_kwargs: None,
    )

    logs: list[str] = []

    def task(group_id: str, name: str, batch_id: str) -> dict:
        return {
            "id": group_id,
            "name": name,
            "plan": {
                "id": group_id,
                "name": name,
                "members": [path],
                "overrides": {},
                "standalone": False,
            },
            "members": [path],
            "snapshots": {path: snapshot},
            "force_rescan": False,
            "batch_id": batch_id,
            "folder_root": str(folder.resolve()),
        }

    first_outputs = window._execute_group_task(
        task("a", "组A", "20260723_120000_000001"),
        1,
        MultiProcessController(),
        logs.append,
    )
    second_outputs = window._execute_group_task(
        task("b", "组B", "20260723_120001_000001"),
        1,
        MultiProcessController(),
        logs.append,
    )

    assert calls == {"prescan": 1, "extract": 1, "render": 2}
    first_output = Path(first_outputs[0])
    second_output = Path(second_outputs[0])
    assert not (first_output / "data").exists()
    assert not (second_output / "data").exists()
    first_asset = resolve_group_member_asset(first_output)
    second_asset = resolve_group_member_asset(second_output)
    assert first_asset is not None
    assert first_asset == second_asset
    assert (first_asset / "data" / "timeline_alignment.csv").is_file()
    assert (first_asset / "summary.xlsx").is_file()
    assert any("命中预扫描缓存" in message for message in logs)
    assert any("复用 ODB 公共数据" in message for message in logs)
    window.close()
