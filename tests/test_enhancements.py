from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView

from abaqus_odb_postprocessor.app import MainWindow
from abaqus_odb_postprocessor.naming import parse_odb_name
from abaqus_odb_postprocessor.models import OdbScan


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_scan(path: Path) -> OdbScan:
    return OdbScan(
        path=path,
        steps=["Load-U", "Load-V"],
        assembly_node_sets=["SET-LOAD"],
        assembly_element_sets=[
            "SET-PILE", "SET-PILE_CON", "SET-KEY", "SET-SOIL_CUT", "SET-REBAR"
        ],
        field_outputs=["U", "S", "PEMAG"],
    )


def test_filename_metadata_and_load_direction() -> None:
    info = parse_odb_name("GJA-1-R_U100D.odb")
    assert info.sample_id == "GJA-1"
    assert info.scheme == "A方案-钢筋混凝土"
    assert info.reinforced is True
    assert info.condition == "U100D"
    assert info.load_direction == "3"
    assert info.up_displacement_mm == 100.0
    assert info.rebar_diameter_mm == 22.0

    info = parse_odb_name("GJA-20-R_U40D_V20D_miu03.odb")
    assert info.condition == "U40D_V20D"
    assert info.parameter_tags == ("miu03",)
    assert info.load_direction == "1+3"
    assert info.rebar_diameter_mm == 32.0

    info = parse_odb_name("GJA-32_V40D-old.odb")
    assert info.condition == "V40D"
    assert info.load_direction == "1"
    assert info.is_old is True


def test_d_series_diameters() -> None:
    assert parse_odb_name("D800-R_U100D.odb").rebar_diameter_mm == 22.0
    assert parse_odb_name("D1000-R_U100D.odb").rebar_diameter_mm == 28.0
    assert parse_odb_name("D1400_17-R_U40D_V20D.odb").rebar_diameter_mm == 32.0


def test_initial_condition_groups_auto_defaults_and_badges(tmp_path: Path) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    folder = tmp_path / "odb"
    folder.mkdir()
    first = make_scan(folder / "GJA-1-R_U40D_V20D.odb")
    second = make_scan(folder / "GJA-2-R_U40D_V20D_miu03.odb")
    third = make_scan(folder / "GJA-4-R_U100D.odb")
    window._load_folder_state(str(folder))
    window._populate([first, second, third])

    conditions = {group.get("auto_condition"): group for group in window.groups.values()}
    assert set(conditions) == {"U40D_V20D", "U100D"}
    assert len(conditions["U40D_V20D"]["members"]) == 2
    assert window.group_tree.selectionMode() == QAbstractItemView.ExtendedSelection
    all_root = window.group_tree.topLevelItem(0)
    counts = {all_root.child(i).text(0): all_root.child(i).text(1) for i in range(all_root.childCount())}
    assert counts[first.path.name] == "1"

    first_row = window.rows_by_path[str(first.path.resolve())]
    third_row = window.rows_by_path[str(third.path.resolve())]
    assert first_row["direction"].currentText().startswith("自动")
    assert window._job_payload(first_row, tmp_path)["load_direction"] == "1+3"
    assert first_row["diameter"].value() == 22.0
    assert window._job_payload(third_row, tmp_path)["load_direction"] == "3"
    assert third_row["diameter"].value() == 28.0
    window.close()


def test_manual_direction_and_diameter_are_persisted(tmp_path: Path) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    folder = tmp_path / "odb"
    folder.mkdir()
    item = make_scan(folder / "GJA-1-R_U100D.odb")
    window._load_folder_state(str(folder))
    window._populate([item])
    row = window.rows_by_path[str(item.path.resolve())]
    row["direction"].setCurrentText("1")
    row["diameter"].setValue(25.0)
    saved = window._serialize_row(row)
    assert saved["direction_manual"] is True
    assert saved["diameter_manual"] is True
    assert window._job_payload(row, tmp_path)["load_direction"] == "1"
    window.close()

