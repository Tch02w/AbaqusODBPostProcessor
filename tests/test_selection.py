from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from abaqus_odb_postprocessor.app import MainWindow, OdbSelectionDialog


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
    dialog.list_widget.item(1).setCheckState(Qt.Checked)
    assert dialog.selected_paths() == [paths[1].resolve()]
    dialog.search_edit.setText("A.odb")
    assert not dialog.list_widget.item(0).isHidden()
    assert dialog.list_widget.item(1).isHidden()
    dialog.close()


def test_scan_start_log_has_timestamp(tmp_path: Path) -> None:
    application()
    window = MainWindow()
    window.state_path = tmp_path / "state.json"
    window._append_log("SCAN_START|3|9|GJA-32.odb")
    last_line = window.log.toPlainText().splitlines()[-1]
    assert re.match(
        r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] 正在读取 \[3/9\] GJA-32\.odb$",
        last_line,
    )
    assert window.scan_status.text() == "正在读取 3/9：GJA-32.odb"
    window.close()
