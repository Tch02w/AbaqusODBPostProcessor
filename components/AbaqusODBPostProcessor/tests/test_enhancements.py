from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTreeWidget,
)

from abaqus_odb_postprocessor.app import MainWindow
from abaqus_odb_postprocessor.naming import natural_sort_key, parse_odb_name
from abaqus_odb_postprocessor.models import OdbScan
from abaqus_odb_postprocessor.ui_style import (
    AccidentalWheelGuard,
    apply_application_style,
)


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_main_window_has_a_result_browser_launch_button() -> None:
    application()
    window = MainWindow()
    button = window.findChild(QPushButton, "resultBrowserButton")
    assert button is window.result_browser_button
    assert button.text() == "结果浏览器"
    assert button.parentWidget() is window.centralWidget()
    window.close()


def test_completed_odb_output_is_submitted_for_incremental_indexing(
    tmp_path: Path, monkeypatch
) -> None:
    application()
    window = MainWindow()
    output = (
        tmp_path
        / "AbaqusODBPostProcessor_Results"
        / "组A"
        / "20260724_120000"
        / "GJA-1-R_U100D"
    )
    output.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(
        window.result_index_coordinator,
        "enqueue_incremental",
        lambda time_directory, scope: calls.append(
            (Path(time_directory), Path(scope))
        ),
    )

    window._append_log(f"INDEX_INCREMENTAL|{output}")

    assert calls == [(output.parent.resolve(), output.resolve())]
    window.close()


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


def test_odb_names_use_natural_numeric_order() -> None:
    names = [
        "GJA-19-R_U100D.odb",
        "GJA-2-R_U100D.odb",
        "GJA-10-R_U100D.odb",
        "GJA-1-R_U100D.odb",
    ]
    assert sorted(names, key=natural_sort_key) == [
        "GJA-1-R_U100D.odb",
        "GJA-2-R_U100D.odb",
        "GJA-10-R_U100D.odb",
        "GJA-19-R_U100D.odb",
    ]


def test_condition_categories_are_browse_only_and_real_groups_are_preserved(
    tmp_path: Path,
) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    folder = tmp_path / "odb"
    folder.mkdir()
    first = make_scan(folder / "GJA-1-R_U40D_V20D.odb")
    second = make_scan(folder / "GJA-2-R_U40D_V20D_miu03.odb")
    third = make_scan(folder / "GJA-4-R_U100D.odb")
    window._load_folder_state(str(folder))
    first_path = str(first.path.resolve())
    window.groups = {
        "legacy": {
            "name": "工况-U40D_V20D",
            "members": [first_path],
            "legend_overrides": {},
            "auto_condition": "U40D_V20D",
        },
        "manual": {
            "name": "人工对比组",
            "members": [first_path],
            "legend_overrides": {},
        },
    }
    window._populate([first, second, third])

    assert set(window.groups) == {"manual"}
    assert set(window.condition_categories) == {"U40D_V20D", "U100D"}
    assert len(window.condition_categories["U40D_V20D"]) == 2
    assert window.group_tree.selectionMode() == QAbstractItemView.ExtendedSelection
    all_root = window.group_tree.topLevelItem(0)
    categories_root = window.group_tree.topLevelItem(1)
    assert window.group_tree.topLevelItemCount() == 2
    assert all_root.text(0) == "全部 ODB"
    assert categories_root.text(0) == "按工况分类（仅浏览）"
    assert window.group_tabs.count() == 2
    assert window.group_tabs.tabText(0) == "全部配置"
    assert window.group_tabs.tabText(1) == "人工对比组"
    assert window.group_tabs.tabData(1) == "manual"
    assert categories_root.childCount() == 2
    assert window.group_tree.columnCount() == 1
    assert all_root.child(0).data(0, Qt.UserRole + 3) is None

    window.group_tree.setCurrentItem(categories_root.child(0))
    assert window._scope_groups("current") == []
    category_odb = categories_root.child(0).child(0)
    window._source_tree_item_activated(category_odb)
    category_plan = window._scope_groups("current")
    assert len(category_plan) == 1
    assert category_plan[0]["standalone"] is True
    assert category_plan[0]["members"] == [window.group_tree.odb_path(category_odb)]

    first_row = window.rows_by_path[first_path]
    third_row = window.rows_by_path[str(third.path.resolve())]
    assert first_row["direction"].currentText().startswith("自动")
    assert window._job_payload(first_row, tmp_path)["load_direction"] == "1+3"
    assert first_row["diameter"].value() == 22.0
    assert window._job_payload(third_row, tmp_path)["load_direction"] == "3"
    assert third_row["diameter"].value() == 28.0
    assert [
        first_row["direction"].itemText(index)
        for index in range(first_row["direction"].count())
    ] == ["X方向", "Z方向", "XZ方向", "自动（XZ方向）"]
    window.close()


def test_wheel_guard_blocks_closed_selectors() -> None:
    guard = AccidentalWheelGuard()
    event = QEvent(QEvent.Wheel)
    assert guard.eventFilter(QComboBox(), event) is True
    assert guard.eventFilter(QSpinBox(), QEvent(QEvent.Wheel)) is True


def test_application_uses_one_font_size_and_native_control_shapes() -> None:
    app = application()
    apply_application_style(app)
    widgets = [
        QLabel("正文"),
        QLabel("标题"),
        QLabel("提示"),
        QPushButton("按钮"),
        QLineEdit(),
        QComboBox(),
        QSpinBox(),
        QTableWidget(),
        QTreeWidget(),
        QPlainTextEdit(),
    ]
    widgets[1].setProperty("role", "title")
    widgets[2].setProperty("role", "hint")
    for widget in widgets:
        widget.ensurePolished()
    assert {widget.font().pointSize() for widget in widgets} == {11}
    assert app.styleSheet() == ""
    assert app.style().objectName().casefold() == "fusion"


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
    row["direction"].setCurrentText("X方向")
    row["diameter"].setValue(25.0)
    saved = window._serialize_row(row)
    assert saved["direction_manual"] is True
    assert saved["direction"] == "X方向"
    assert saved["diameter_manual"] is True
    assert window._job_payload(row, tmp_path)["load_direction"] == "1"
    window.close()


def test_unrecognized_direction_requires_manual_selection(tmp_path: Path) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "project_state.json"
    folder = tmp_path / "odb"
    folder.mkdir()
    item = make_scan(folder / "unknown-model.odb")
    window._load_folder_state(str(folder))
    window._populate([item])
    row = window.rows_by_path[str(item.path.resolve())]
    assert row["direction"].currentText() == "（未识别）"
    with pytest.raises(ValueError, match="加载方向未识别"):
        window._job_payload(row, tmp_path)
    row["direction"].setCurrentText("Z方向")
    assert window._job_payload(row, tmp_path)["load_direction"] == "3"
    window.close()


def test_window_title_shows_only_valid_odb_root(tmp_path: Path) -> None:
    application()
    window = MainWindow()
    window.folder_edit.setText(str(tmp_path))
    assert str(tmp_path.resolve()) in window.windowTitle()
    assert "0.8" in window.windowTitle()
    window.folder_edit.setText(str(tmp_path / "missing"))
    assert str(tmp_path.resolve()) not in window.windowTitle()
    window.close()
