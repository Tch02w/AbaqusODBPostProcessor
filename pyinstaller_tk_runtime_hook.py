import os
import sys


base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)
os.environ["TCL_LIBRARY"] = os.path.join(base_dir, "tcl", "tcl8.6")
os.environ["TK_LIBRARY"] = os.path.join(base_dir, "tcl", "tk8.6")
