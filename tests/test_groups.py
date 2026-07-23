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
    assert window.group_tabs.count() == 3
    assert window.group_tabs.tabText(0) == "全部配置"
    assert window.group_tabs.tabData(window.group_tabs.currentIndex()) == "a"
    assert window._scope_groups("current")[0]["standalone"] is False
    window._activate_standalone(first_path)
    current_odb_plan = window._scope_groups("current")
    assert len(current_odb_plan) == 1
    assert current_odb_plan[0]["standalone"] is True
    window.group_tabs.setCurrentIndex(0)
    assert window._scope_groups("current") == []
    plans = window._scope_groups("all")
    assert [item["name"] for item in plans] == ["组A", "组B", "B"]
    assert sum(first_path in item["members"] for item in plans) == 2

    all_root = window.group_tree.topLevelItem(0)
    first_item = all_root.child(0)
    second_item = all_root.child(1)
    window.group_tree.clearSelection()
    first_item.setSelected(True)
    second_item.setSelected(True)
    assert set(window.group_tree.selected_odb_paths()) == {
        str(first.path.resolve()),
        str(second.path.resolve()),
    }
    window.group_tabs.setCurrentIndex(1)
    window._drop_paths_into_current_group(window.group_tree.selected_odb_paths())
    assert str(second.path.resolve()) in window.groups["a"]["members"]
    assert window.source_header.minimumHeight() == window.tabs_header.minimumHeight()
    assert window.source_header.maximumHeight() == window.tabs_header.maximumHeight()
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


def test_group_tabs_reorder_persist_and_fifo_snapshots_are_frozen(
    tmp_path: Path, monkeypatch
) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    folder = tmp_path / "odb"
    folder.mkdir()
    first = scan(folder / "GJA-2.odb")
    second = scan(folder / "GJA-10.odb")
    window._load_folder_state(str(folder))
    window._populate([first, second])
    all_root = window.group_tree.topLevelItem(0)
    assert [all_root.child(index).text(0) for index in range(2)] == [
        "GJA-2.odb",
        "GJA-10.odb",
    ]
    first_path = str(first.path.resolve())
    second_path = str(second.path.resolve())
    window.groups = {
        "a": {
            "name": "组A",
            "members": [first_path],
            "legend_overrides": {},
        },
        "b": {
            "name": "组B",
            "members": [second_path],
            "legend_overrides": {},
        },
    }
    window._rebuild_tree("a")
    assert window.group_tabs.isMovable()
    assert window.group_tabs.usesScrollButtons()

    window.group_tabs.moveTab(2, 1)
    assert list(window.groups) == ["b", "a"]
    window.group_tabs.moveTab(0, 2)
    assert window.group_tabs.tabText(0) == "全部配置"
    assert list(window.groups) == ["b", "a"]

    monkeypatch.setattr(window, "_start_next_group", lambda: None)
    window.force_rescan_checkbox.setChecked(True)
    window._run_scope("all")
    assert [item["id"] for item in window.group_queue] == ["b", "a"]
    assert all(item["force_rescan"] for item in window.group_queue)
    assert not window.force_rescan_checkbox.isChecked()
    assert (
        window.group_queue[0]["snapshots"][second_path]["load_direction"]
        == "1+3"
    )

    row = window.rows_by_path[second_path]
    row["direction"].setCurrentText("X方向")
    window.groups["b"]["name"] = "后来修改"
    assert (
        window.group_queue[0]["snapshots"][second_path]["load_direction"]
        == "1+3"
    )
    assert window.group_queue[0]["name"] == "组B"

    window.active_group_task = window.group_queue.pop(0)
    window._refresh_queue_ui()
    assert window.group_tabs.tabText(1) == "组B（运行中）"
    assert window.group_tabs.tabText(2) == "组A（排队 1）"
    assert "正在运行：组B" in window.scan_status.text()
    window._append_log("BATCH_PROGRESS|scan|1|2|GJA-10.odb")
    assert "正在运行：组B（预扫描 1/2：GJA-10.odb）" in window.scan_status.text()
    assert "排队：组A" in window.scan_status.text()
    assert window._group_is_locked("b")
    assert window._group_is_locked("a")

    window.active_group_task = None
    window.group_queue.clear()
    window._save_state()
    saved = json.loads(window.state_path.read_text(encoding="utf-8"))
    assert saved["folders"][str(folder.resolve())]["group_order"] == ["b", "a"]
    window.close()
