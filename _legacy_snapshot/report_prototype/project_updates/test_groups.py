from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from abaqus_odb_postprocessor.app import MainWindow, safe_folder_name
from abaqus_odb_postprocessor.models import OdbScan


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def scan(path: Path) -> OdbScan:
    return OdbScan(
        path=path,
        steps=["Load"],
        assembly_node_sets=["SET-LOAD"],
        assembly_element_sets=[
            "SET-PILE", "SET-PILE_CON", "SET-KEY", "SET-SOIL_CUT", "SET-REBAR"
        ],
        field_outputs=["U", "S", "PEMAG"],
    )


def test_multi_group_membership_and_standalone(tmp_path: Path) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    folder = tmp_path / "odb"
    folder.mkdir()
    first = scan(folder / "A.odb")
    second = scan(folder / "B.odb")
    window._load_folder_state(str(folder))
    window._populate([first, second])
    first_path = str(first.path.resolve())
    window.groups = {
        "a": {"name": "组A", "members": [first_path], "legend_overrides": {}},
        "b": {"name": "组B", "members": [first_path], "legend_overrides": {}},
    }
    window._rebuild_tree("a")
    window._update_membership_labels()
    assert window.rows_by_path[first_path]["group"].text() == "组A；组B"
    plans = window._scope_groups("all")
    assert [item["name"] for item in plans] == ["组A", "组B", "B"]
    assert sum(first_path in item["members"] for item in plans) == 2
    window.close()


def test_manual_legend_override() -> None:
    plan = {
        "SOIL_PEMAG_XZ": {
            "min": 0.0,
            "max": 0.012,
            "source": "comparison_group_selected_frames",
        }
    }
    MainWindow._apply_overrides(
        plan,
        {"SOIL_PEMAG_XZ": {"mode": "manual", "min": 0.001, "max": 0.006}},
        "试验组",
    )
    assert plan["SOIL_PEMAG_XZ"]["min"] == 0.001
    assert plan["SOIL_PEMAG_XZ"]["max"] == 0.006
    assert plan["SOIL_PEMAG_XZ"]["source"] == "comparison_group_manual_override"
    assert plan["SOIL_PEMAG_XZ"]["comparison_group"] == "试验组"


def test_scan_progress_and_state_persistence(tmp_path: Path) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    folder = tmp_path / "odb"
    folder.mkdir()
    item = scan(folder / "A.odb")
    window._load_folder_state(str(folder))
    window._populate([item])
    window._append_log("SCAN_DISCOVERED|3")
    window._append_log("SCAN_START|2|3|A.odb")
    window._append_log("SCAN_DONE|2|3|A.odb")
    assert window.scan_total == 3
    assert window.scan_completed == 2
    assert window.scan_progress.maximum() == 3
    assert window.scan_progress.value() == 2
    window.groups = {
        "g": {"name": "持久组", "members": [str(item.path.resolve())], "legend_overrides": {}}
    }
    window._save_state()
    saved = json.loads(window.state_path.read_text(encoding="utf-8"))
    assert saved["folders"][str(folder.resolve())]["groups"]["g"]["name"] == "持久组"
    window.close()


def test_safe_folder_name() -> None:
    assert safe_folder_name('组:A/B*?') == "组_A_B_"
