from __future__ import annotations

import subprocess
import sys


def test_workbench_window_import_is_deferred_on_python_315() -> None:
    script = """
import sys
import abaqus_workbench.app
assert 'abaqus_workbench.window' not in sys.modules
from abaqus_workbench.app import IntegratedMainWindow
assert IntegratedMainWindow.__name__ == 'IntegratedMainWindow'
assert 'abaqus_workbench.window' in sys.modules
assert 'abaqus_odb_postprocessor.postprocessor_page' not in sys.modules
assert 'abaqus_odb_postprocessor.result_browser_page' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
