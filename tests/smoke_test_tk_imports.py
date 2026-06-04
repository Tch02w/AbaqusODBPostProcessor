import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import abaqus_submitter_tk.main
import abaqus_submitter_tk.constants
import abaqus_submitter_tk.models
import abaqus_submitter_tk.persistence
import abaqus_submitter_tk.process_scanner
import abaqus_submitter_tk.abaqus_diagnostics
import abaqus_submitter_tk.memory_monitor

print("Tk imports OK")
