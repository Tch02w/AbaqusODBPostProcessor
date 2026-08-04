from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QTableView
from PIL import Image

from abaqus_odb_postprocessor.result_browser import (
    AspectRatioPreviewLabel,
    ResultBrowserDialog,
    ResultIndexCoordinator,
    classify_result,
    collect_shared_asset_records,
    collect_result_records,
    fit_preview_size,
    read_csv_preview,
    result_section,
)
from abaqus_odb_postprocessor.result_assets import write_group_member_manifest
from abaqus_odb_postprocessor.result_index import INDEX_FILENAME, build_result_index


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_result_tree(root: Path) -> Path:
    odb = root / "试验对比组" / "20260723_120000" / "GJA-1-R_U100D"
    (odb / "data").mkdir(parents=True)
    (odb / "freebody").mkdir()
    (odb / "animations").mkdir()
    (odb / "frames_transparent" / "PILE_U_MAG").mkdir(parents=True)
    (odb / "summary.xlsx").write_bytes(b"xlsx")
    (odb / "data" / "load_point_raw.csv").write_text(
        "TotalTime,U3,RF3\n0,0,0\n", encoding="utf-8"
    )
    (odb / "History_Output").mkdir()
    (odb / "History_Output" / "load_resistance_processed.csv").write_text(
        "TotalTime,PileTopReaction_kN,RootKeyTotalBearing_kN\n0,0,0\n",
        encoding="utf-8",
    )
    (odb / "freebody" / "pile_bending_moment_maxima.csv").write_text(
        "SequenceIndex,MaxAbsPileTotalMy_Nmm\n1,1200\n", encoding="utf-8"
    )
    (odb / "animations" / "PILE_U_MAG.gif").write_bytes(b"gif")
    (
        odb
        / "frames_transparent"
        / "PILE_U_MAG"
        / "0001_Load_F0010.png"
    ).write_bytes(b"png")
    (odb / "metadata.json").write_text('{"odb_path": "sample.odb"}', encoding="utf-8")
    return odb


def test_group_scope_includes_referenced_shared_odb_data(tmp_path: Path) -> None:
    asset = tmp_path / "ODB数据" / "A" / "asset"
    (asset / "data").mkdir(parents=True)
    (asset / "data" / "load_point_raw.csv").write_text(
        "TotalTime,U3\n0,0\n", encoding="utf-8"
    )
    (asset / "summary.xlsx").write_bytes(b"xlsx")
    group_member = tmp_path / "组A" / "batch" / "A"
    group_member.mkdir(parents=True)
    write_group_member_manifest(
        group_member,
        asset_dir=asset,
        comparison_group="组A",
        odb_path=tmp_path / "A.odb",
        content_fingerprint="a" * 64,
        numeric_config_hash="b" * 64,
    )

    records = collect_shared_asset_records(group_member)
    assert {record.path.name for record in records} == {
        "load_point_raw.csv",
        "summary.xlsx",
    }
    assert all("ODB 公共数据" in record.description for record in records)


def test_result_files_are_translated_to_engineering_uses(tmp_path: Path) -> None:
    odb = make_result_tree(tmp_path)
    summary = classify_result(odb / "summary.xlsx")
    load = classify_result(odb / "data" / "load_point_raw.csv")
    load_sharing = classify_result(
        odb / "History_Output" / "load_resistance_processed.csv"
    )
    moment = classify_result(
        odb / "freebody" / "pile_bending_moment_maxima.csv"
    )
    animation = classify_result(odb / "animations" / "PILE_U_MAG.gif")
    contour = classify_result(
        odb
        / "frames_transparent"
        / "PILE_U_MAG"
        / "0001_Load_F0010.png"
    )

    assert summary.use_case == "综合汇总"
    assert summary.recommended is True
    assert load.use_case == "荷载与位移"
    assert load_sharing.use_case == "荷载分担"
    assert load_sharing.recommended is True
    assert moment.use_case == "桩弯矩"
    assert animation.use_case == "动画"
    assert "桩身位移大小" in animation.description
    assert contour.use_case == "云图"
    assert "透明背景" in contour.description


def test_result_collection_places_recommended_files_first(tmp_path: Path) -> None:
    odb = make_result_tree(tmp_path)
    records = collect_result_records(odb)
    assert len(records) == 7
    recommended = [record.recommended for record in records]
    assert recommended == sorted(recommended, reverse=True)


def test_preview_fits_without_changing_image_aspect_ratio() -> None:
    assert fit_preview_size(QSize(1500, 1000), QSize(600, 600)) == QSize(600, 400)
    assert fit_preview_size(QSize(1000, 1500), QSize(600, 600)) == QSize(400, 600)


def test_gif_preview_is_scaled_inside_the_available_area(tmp_path: Path) -> None:
    app = application()
    gif_path = tmp_path / "wide.gif"
    Image.new("RGB", (600, 300), "white").save(gif_path, format="GIF")
    preview = AspectRatioPreviewLabel()
    preview.resize(320, 320)
    preview.show()
    movie = preview.show_gif(gif_path)
    app.processEvents()
    scaled = movie.scaledSize()
    assert scaled.width() <= preview.contentsRect().width()
    assert scaled.height() <= preview.contentsRect().height()
    assert abs(scaled.width() / scaled.height() - 2.0) < 0.02
    preview.close()


def test_csv_preview_reads_all_columns_but_limits_rows(tmp_path: Path) -> None:
    path = tmp_path / "wide.csv"
    headers = [f"C{index}" for index in range(20)]
    lines = [",".join(headers)]
    lines.extend(",".join(str(value) for value in range(20)) for _ in range(40))
    path.write_text("\n".join(lines), encoding="utf-8")
    preview_headers, rows = read_csv_preview(path, max_rows=7)
    assert preview_headers == headers
    assert len(rows) == 7
    assert all(len(row) == 20 for row in rows)


def test_result_browser_shows_group_batch_odb_and_common_results(
    tmp_path: Path,
) -> None:
    application()
    odb = make_result_tree(tmp_path)
    internal = tmp_path / "_批次记录" / "20260723_120000"
    internal.mkdir(parents=True)
    (internal / "manifest.json").write_text("{}", encoding="utf-8")
    time_directory = odb.parent
    build_result_index(
        time_directory,
        classify_result,
        result_section,
    )

    dialog = ResultBrowserDialog(tmp_path)
    flags = dialog.windowFlags()
    assert flags & Qt.WindowMinimizeButtonHint
    assert flags & Qt.WindowMaximizeButtonHint
    assert flags & Qt.WindowCloseButtonHint
    assert dialog.isModal() is False
    root_item = dialog.scope_tree.topLevelItem(0)
    assert root_item.text(0) == "全部结果"
    assert root_item.childCount() == 1
    group_item = root_item.child(0)
    assert group_item.text(0) == "试验对比组"
    batch_item = group_item.child(0)
    assert batch_item.text(0) == "20260723_120000"
    odb_item = batch_item.child(0)
    assert odb_item.text(0) == odb.name

    dialog.scope_tree.setCurrentItem(odb_item)
    assert dialog.current_scope == odb
    assert [
        dialog.section_tabs.tabText(index)
        for index in range(dialog.section_tabs.count())
    ] == ["常用", "数据", "曲线图", "云图", "动画", "说明与日志", "全部"]
    assert dialog.use_case_combo.currentText() == "全部内容"
    assert isinstance(dialog.file_table, QTableView)
    assert dialog.file_table.model().rowCount() == 5
    assert dialog.content_splitter.orientation() == Qt.Vertical
    assert dialog.preview.hasScaledContents() is False

    data_index = next(
        index
        for index in range(dialog.section_tabs.count())
        if dialog.section_tabs.tabData(index) == "data"
    )
    dialog.section_tabs.setCurrentIndex(data_index)
    load_row = next(
        row
        for row in range(dialog.file_table.model().rowCount())
        if dialog.file_table.model().data(
            dialog.file_table.model().index(row, 3)
        )
        == "load_point_raw.csv"
    )
    dialog.file_table.selectRow(load_row)
    for thread in list(dialog._background_threads):
        assert thread.wait(1000)
    QApplication.processEvents()
    assert dialog.preview_stack.currentWidget() is dialog.data_preview
    assert dialog.data_preview.columnCount() == 3
    assert dialog.data_preview.rowCount() == 1

    animation_index = next(
        index
        for index in range(dialog.section_tabs.count())
        if dialog.section_tabs.tabData(index) == "animations"
    )
    dialog.section_tabs.setCurrentIndex(animation_index)
    assert dialog.file_table.model().rowCount() == 1
    assert (
        dialog.file_table.model().data(dialog.file_table.model().index(0, 1))
        == "动画"
    )

    all_index = next(
        index
        for index in range(dialog.section_tabs.count())
        if dialog.section_tabs.tabData(index) == "all"
    )
    dialog.section_tabs.setCurrentIndex(all_index)
    assert dialog.file_table.model().rowCount() == 7
    dialog.close()


def test_result_browser_can_switch_to_the_main_window_result_root(
    tmp_path: Path,
) -> None:
    application()
    first_root = tmp_path / "first" / "AbaqusODBPostProcessor_Results"
    second_root = tmp_path / "second" / "AbaqusODBPostProcessor_Results"
    first_root.mkdir(parents=True)
    second_root.mkdir(parents=True)
    make_result_tree(second_root)

    dialog = ResultBrowserDialog(first_root)
    dialog.set_result_root(second_root)

    assert dialog.result_root == second_root.resolve()
    assert dialog.root_edit.text() == str(second_root.resolve())
    assert dialog.scope_tree.topLevelItem(0).childCount() == 1
    dialog.close()


def test_result_browser_startup_reads_only_directory_structure(
    tmp_path: Path, monkeypatch
) -> None:
    application()
    time_directory = (
        tmp_path / "对比组" / "20260724_120000"
    )
    (time_directory / "GJA-1-R_U100D").mkdir(parents=True)
    (time_directory / INDEX_FILENAME).write_bytes(b"not-opened")

    def fail_if_loaded(_path):
        raise AssertionError("startup must not open an index")

    monkeypatch.setattr(
        "abaqus_odb_postprocessor.result_browser.load_result_index",
        fail_if_loaded,
    )
    dialog = ResultBrowserDialog(tmp_path)

    root = dialog.scope_tree.topLevelItem(0)
    assert root.child(0).child(0).text(0) == "20260724_120000"
    assert dialog.records == []
    assert "未读取任何结果索引" in dialog.status_label.text()
    dialog.close()


def test_manual_index_request_has_priority_over_newest_first_bulk(
    tmp_path: Path, monkeypatch
) -> None:
    application()
    older = tmp_path / "20260723_120000"
    newer = tmp_path / "20260724_120000_900000"
    older.mkdir()
    newer.mkdir()
    coordinator = ResultIndexCoordinator()
    monkeypatch.setattr(coordinator, "_start_next", lambda: None)

    assert coordinator.enqueue_bulk([older, newer]) == 2
    assert coordinator.pending_paths() == [
        str(newer.resolve()),
        str(older.resolve()),
    ]

    coordinator.enqueue_manual(older)
    assert coordinator.pending_paths()[0] == str(older.resolve())


def test_manual_refresh_replaces_a_pending_incremental_request(
    tmp_path: Path, monkeypatch
) -> None:
    application()
    time_directory = tmp_path / "20260724_120000"
    scope = time_directory / "GJA-1-R_U100D"
    scope.mkdir(parents=True)
    coordinator = ResultIndexCoordinator()
    monkeypatch.setattr(coordinator, "_start_next", lambda: None)

    assert coordinator.enqueue_incremental(time_directory, scope)
    assert coordinator.enqueue_manual(time_directory, refresh=True)

    assert len(coordinator.pending) == 1
    assert coordinator.pending[0]["operation"] == "refresh"
    assert coordinator.pending[0]["priority"] == 0
    assert coordinator.pending[0]["scopes"] == set()
