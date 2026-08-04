from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_single_pyside_application_loads_postprocessor_on_demand(
    tmp_path: Path,
) -> None:
    script = """
import os
import sys
from abaqus_workbench_core.qt import QT_BINDING, ensure_application
from abaqus_workbench.window import IntegratedMainWindow

handle = ensure_application([], application_name='Abaqus Workbench Test')
window = IntegratedMainWindow()
assert QT_BINDING == 'PySide6'
assert window.postprocessor_page is None
assert 'abaqus_odb_postprocessor.postprocessor_page' not in sys.modules
window.workbench_tabs.setCurrentIndex(window.postprocessor_tab_index)
handle.application.processEvents()
assert window.postprocessor_page is not None
assert 'abaqus_odb_postprocessor.postprocessor_page' in sys.modules
source = os.path.join(os.environ['WORKBENCH_TEST_DIR'], 'case-a.odb')
window.show_postprocessor(source)
assert window.postprocessor_page.controller.folder_edit.text() == os.environ['WORKBENCH_TEST_DIR']
assert handle.application.instance() is handle.application
window.close()
handle.application.processEvents()
"""
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["ABAQUS_SUBMITTER_DATA_DIR"] = str(tmp_path / "submitter")
    environment["ABAQUS_POSTPROCESSOR_DATA_DIR"] = str(tmp_path / "postprocessor")
    environment["WORKBENCH_TEST_DIR"] = str(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
