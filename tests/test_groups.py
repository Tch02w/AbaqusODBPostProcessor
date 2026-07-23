from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from abaqus_odb_postprocessor.app import MainWindow, safe_folder_name
from abaqus_odb_postprocessor.comparison_groups import GroupNameTable
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


def test_group_tabs_reorder_persist_and_fifo_snapshots_freeze_on_activation(
    tmp_path: Path, monkeypatch
) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    folder = tmp_path / "odb"
    folder.mkdir()
    first = scan(folder / "GJA-2_U40D_V20D.odb")
    second = scan(folder / "GJA-10_U40D_V20D.odb")
    window._load_folder_state(str(folder))
    window._populate([first, second])
    all_root = window.group_tree.topLevelItem(0)
    assert [all_root.child(index).text(0) for index in range(2)] == [
        "GJA-2_U40D_V20D.odb",
        "GJA-10_U40D_V20D.odb",
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
    assert set(window.group_queue[0]) == {"id", "force_rescan"}

    row = window.rows_by_path[second_path]
    row["direction"].setCurrentText("X方向")
    frozen, reason = window._freeze_group_task(window.group_queue.pop(0))
    assert reason == ""
    assert frozen is not None
    assert frozen["snapshots"][second_path]["load_direction"] == "1"
    assert frozen["name"] == "组B"

    window.active_group_task = frozen
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


def test_group_name_table_grows_for_typing_and_large_paste() -> None:
    application()
    table = GroupNameTable()
    assert table.rowCount() == 10
    table.paste_names(
        "\n".join(f"组{index}\t忽略说明" for index in range(15)),
        start_row=0,
    )
    assert table.rowCount() == 16
    assert table.names() == [f"组{index}" for index in range(15)]
    table.close()


def test_batch_group_creation_ignores_duplicates_after_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    window.groups = {
        "existing": {
            "name": "已有组",
            "members": [],
            "legend_overrides": {},
        }
    }
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes
    )
    created = window._create_groups_from_names(
        ["  新组1  ", "已有组", "新组2", "新组1", ""]
    )
    assert [window.groups[group_id]["name"] for group_id in created] == [
        "新组1",
        "新组2",
    ]
    assert list(window.groups) == ["existing", *created]
    assert window.group_tabs.tabData(window.group_tabs.currentIndex()) == created[0]
    window.close()


def test_waiting_group_without_valid_members_is_skipped(
    tmp_path: Path,
) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    folder = tmp_path / "odb"
    folder.mkdir()
    item = scan(folder / "GJA-1_U100D.odb")
    window._load_folder_state(str(folder))
    window._populate([item])
    path = str(item.path.resolve())
    window.groups = {
        "g": {"name": "待跳过", "members": [path], "legend_overrides": {}}
    }
    window.group_queue = [{"id": "g", "force_rescan": False}]
    window.groups["g"]["members"] = []
    window._start_next_group()
    assert window.active_group_task is None
    assert window.group_queue == []
    assert "跳过组：待跳过" in window.log.toPlainText()
    window.close()


def test_cancel_all_queued_groups_keeps_active_group(tmp_path: Path) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    window.groups = {
        "running": {"name": "运行组", "members": [], "legend_overrides": {}},
        "a": {"name": "等待A", "members": [], "legend_overrides": {}},
        "b": {"name": "等待B", "members": [], "legend_overrides": {}},
    }
    active = {"id": "running", "name": "运行组"}
    window.active_group_task = active
    window.group_queue = [
        {"id": "a", "force_rescan": False},
        {"id": "b", "force_rescan": True},
    ]
    window._refresh_queue_ui()
    assert window.cancel_queued_button.isEnabled()
    window._cancel_queued_groups()
    assert window.group_queue == []
    assert window.active_group_task is active
    assert not window.cancel_queued_button.isEnabled()
    assert "已取消全部待运行组：等待A、等待B" in window.log.toPlainText()
    window.active_group_task = None
    window.close()


def test_unrecognized_direction_blocks_group_submission(
    tmp_path: Path, monkeypatch
) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    folder = tmp_path / "odb"
    folder.mkdir()
    item = scan(folder / "unknown-model.odb")
    window._load_folder_state(str(folder))
    window._populate([item])
    path = str(item.path.resolve())
    window.groups = {
        "g": {"name": "未识别组", "members": [path], "legend_overrides": {}}
    }
    window._rebuild_tree("g")
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message, *_args, **_kwargs: warnings.append(
            message
        ),
    )
    window._run_scope("current")
    assert window.group_queue == []
    assert warnings
    assert "unknown-model.odb" in warnings[0]
    window.close()
