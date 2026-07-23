from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView

from abaqus_odb_postprocessor.app import (
    MainWindow,
    OdbSelectionDialog,
    discover_odb_paths,
)
from abaqus_odb_postprocessor import runner as runner_module
from abaqus_odb_postprocessor.process_runner import is_process_startup_noise
from abaqus_odb_postprocessor.runner_parallel import MultiProcessController
from abaqus_odb_postprocessor.runner import upgrade_target_path
from abaqus_odb_postprocessor.runner import _load_json_report


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_odb_selection_dialog(tmp_path: Path) -> None:
    application()
    paths = [tmp_path / "A.odb", tmp_path / "B.odb"]
    for path in paths:
        path.write_bytes(b"placeholder")
    dialog = OdbSelectionDialog(tmp_path, paths)
    assert dialog.selected_paths() == [path.resolve() for path in paths]
    dialog._set_all(Qt.Unchecked)
    assert dialog.selected_paths() == []
    dialog.list_widget.item(1).setCheckState(0, Qt.Checked)
    assert dialog.selected_paths() == [paths[1].resolve()]
    dialog.search_edit.setText("A.odb")
    assert not dialog.list_widget.item(0).isHidden()
    assert dialog.list_widget.item(1).isHidden()
    dialog.close()


def test_compatibility_results_are_visible(tmp_path: Path) -> None:
    application()
    valid = tmp_path / "valid.odb"
    old = tmp_path / "old.odb"
    valid.write_bytes(b"valid")
    old.write_bytes(b"old")
    dialog = OdbSelectionDialog(tmp_path, [valid, old])
    dialog._check_finished(
        {
            "results": [
                {
                    "path": str(valid),
                    "status": "valid",
                    "message": "可读取",
                },
                {
                    "path": str(old),
                    "status": "upgrade_required",
                    "message": "需要升级",
                },
            ]
        }
    )
    assert dialog.list_widget.item(0).text(3) == "可直接读取"
    assert dialog.list_widget.item(1).text(3) == "需要升级"
    assert dialog.upgrade_button.isEnabled()
    dialog.close()


def test_upgrade_target_never_overwrites(tmp_path: Path) -> None:
    source = tmp_path / "case.odb"
    source.write_bytes(b"source")
    first = upgrade_target_path(source, "2025")
    assert first.name == "case-old.odb"
    first.write_bytes(b"existing")
    second = upgrade_target_path(source, "2025")
    assert second.name == "case-old-2.odb"


def test_abaqus_report_accepts_chinese_windows_encoding(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    expected = {
        "results": [
            {
                "path": r"G:\计算\模型.odb",
                "status": "valid",
                "message": "可由本机 Abaqus 直接读取",
            }
        ]
    }
    report.write_bytes(
        json.dumps(expected, ensure_ascii=False).encode("gb18030")
    )
    assert _load_json_report(report) == expected


def test_odb_discovery_is_recursive_and_excludes_results(tmp_path: Path) -> None:
    top = tmp_path / "top.odb"
    nested = tmp_path / "case" / "nested.ODB"
    old_backup = tmp_path / "case" / "nested-old.odb"
    upgrading = tmp_path / "case" / "nested-upgrading-2025.odb"
    generated = (
        tmp_path
        / "AbaqusODBPostProcessor_Results"
        / "batch"
        / "generated.odb"
    )
    nested.parent.mkdir()
    generated.parent.mkdir(parents=True)
    for path in (top, nested, old_backup, upgrading, generated):
        path.write_bytes(b"odb")
    assert discover_odb_paths(tmp_path) == [nested, top]


def test_scan_start_log_has_timestamp(tmp_path: Path) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "state.json"
    window._append_log("SCAN_START|3|9|GJA-32.odb")
    last_line = window.log.toPlainText().splitlines()[-1]
    assert re.match(
        r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] 启动读取任务 \[3/9\] GJA-32\.odb$",
        last_line,
    )
    assert window.scan_status.text() == "正在并行读取任务 3/9：GJA-32.odb"
    assert window.minimumWidth() >= 1100
    assert window.table.verticalHeader().defaultSectionSize() == 32
    assert window.table.selectionMode() == QAbstractItemView.NoSelection
    assert window.table.focusPolicy() == Qt.NoFocus
    assert window.log.maximumHeight() <= 180
    window.close()


def test_initial_odb_scan_uses_configured_parallel_workers(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "odb"
    folder.mkdir()
    paths = [folder / f"case-{index}.odb" for index in range(4)]
    for path in paths:
        path.write_bytes(b"odb")

    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def fake_run_process(arguments, _cwd, log=None, controller=None):
        nonlocal active, maximum_active
        selection_path = Path(arguments[arguments.index("--selection") + 1])
        output_path = Path(arguments[arguments.index("--output") + 1])
        selected = json.loads(selection_path.read_text(encoding="utf-8"))["paths"]
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.04)
        runner_module.save_json(
            output_path,
            {
                "folder": str(folder),
                "odb_count": 1,
                "completed_count": 1,
                "odbs": [{"path": selected[0], "error": ""}],
            },
        )
        with lock:
            active -= 1

    monkeypatch.setattr(runner_module, "run_process", fake_run_process)
    messages: list[str] = []
    payload = runner_module.scan_folder(
        "abaqus",
        folder,
        tmp_path / "cache",
        messages.append,
        MultiProcessController(),
        paths,
        parallel_workers=3,
    )

    assert maximum_active == 3
    assert payload["parallel_workers"] == 3
    assert payload["completed_count"] == 4
    assert [item["path"] for item in payload["odbs"]] == [
        str(path.resolve()) for path in paths
    ]
    done = [message for message in messages if message.startswith("SCAN_DONE|")]
    assert [int(message.split("|")[1]) for message in done] == [1, 2, 3, 4]


def test_visual_studio_startup_noise_is_filtered_without_hiding_real_warnings() -> None:
    noise = [
        "**********************************************************************",
        "** Visual Studio 2026 Developer Command Prompt v18.2.1",
        "** Copyright (c) 2025 Microsoft Corporation",
        "[DEBUG:ext\\vcvars.bat] Found potential v145 version file: 'x.txt'",
        "[vcvarsall.bat] Environment initialized for: 'x64'",
        (
            "WARNING: vars.bat does not set up dependencies when invoked directly. "
            "Please perform environment setup for UMF before running DPC++ applications."
        ),
    ]
    assert all(is_process_startup_noise(line) for line in noise)
    assert not is_process_startup_noise(
        "WARNING: ODB frame 12 does not contain field output DAMAGET"
    )
    assert not is_process_startup_noise(
        'Abaqus License Manager checked out the following license: "cae"'
    )


def test_parallel_worker_control_is_readable_and_persistent(tmp_path: Path) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "state.json"
    assert window.parallel_label.text() == "并行任务数"
    assert window.parallel_workers.minimum() == 1
    assert window.parallel_workers.maximum() == 4
    assert window.parallel_workers.minimumWidth() >= 110
    assert window.parallel_workers.suffix() == " 个"
    assert "建议 2" in window.parallel_hint.text()
    window.parallel_workers.setValue(3)
    saved = json.loads(window.state_path.read_text(encoding="utf-8"))
    assert saved["parallel_odb_workers"] == 3
    window.close()


def test_image_size_controls_support_pixel_and_mm(tmp_path: Path) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "state.json"
    pixel_index = window.image_unit_selector.findData("px")
    mm_index = window.image_unit_selector.findData("mm")
    window.image_unit_selector.setCurrentIndex(pixel_index)
    window.image_width_input.setValue(1500)
    window.image_height_input.setValue(1000)
    assert window.image_width_input.suffix() == " px"
    assert window.image_ratio_label.text() == "比例 3:2"

    window.image_unit_selector.setCurrentIndex(mm_index)
    assert window.image_width_input.suffix() == " mm"
    assert window.image_width_input.value() == 397
    assert window.image_height_input.value() == 265

    saved = json.loads(window.state_path.read_text(encoding="utf-8"))
    assert saved["image_size_unit"] == "mm"
    assert saved["image_width"] == 397
    assert saved["image_height"] == 265
    window.close()
