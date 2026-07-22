"""Handle assembly-level nodal field values without an owning instance."""

from __future__ import print_function

import os


this_path = os.path.abspath(__file__) if "__file__" in globals() else ""
if not this_path:
    import sys
    candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("scan_field_ranges_v3.py")]
    if not candidates: raise RuntimeError("Cannot locate scan_field_ranges_v3.py in argv")
    this_path = os.path.abspath(candidates[-1])
v2_path = os.path.join(os.path.dirname(this_path), "scan_field_ranges_v2.py")
with open(v2_path, "r", encoding="utf-8") as stream: loader = stream.read()
loader = loader.replace("scan_field_ranges_v2.py", "scan_field_ranges_v3.py")
loader = loader.replace("value.instance.name", 'getattr(value.instance, "name", "")')
exec(compile(loader, v2_path, "exec"), globals(), globals())
