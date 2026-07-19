# ruff: noqa: E402

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_runtime_dir = tempfile.TemporaryDirectory()
os.environ["ABAQUS_SUBMITTER_DATA_DIR"] = _runtime_dir.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from abaqus_submitter.command import SubmitOptions  # noqa: F401
from abaqus_submitter.main import MainWindow
from abaqus_submitter.memory_monitor import MemoryMonitorService  # noqa: F401
from abaqus_submitter.queue_manager import QueueManagerDialog  # noqa: F401
from abaqus_submitter.qt_compat import QtWidgets

app = QtWidgets.QApplication.instance()
if app is None:
    app = QtWidgets.QApplication([])

window = MainWindow()
window.close()
_runtime_dir.cleanup()

print("AbaqusSubmitter imports OK")
