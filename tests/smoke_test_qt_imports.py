import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from abaqus_submitter_qt.command import SubmitOptions
from abaqus_submitter_qt.main import MainWindow
from abaqus_submitter_qt.memory_monitor import MemoryMonitorService
from abaqus_submitter_qt.queue_manager import QueueManagerDialog
from abaqus_submitter_qt.qt_compat import QtWidgets

app = QtWidgets.QApplication.instance()
if app is None:
    app = QtWidgets.QApplication([])

window = MainWindow()
window.close()

print("Qt imports OK")
