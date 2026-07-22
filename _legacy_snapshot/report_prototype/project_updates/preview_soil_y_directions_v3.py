"""Abaqus 2025 loader for the two-direction soil section preview."""

from __future__ import print_function

import os
import sys
import visualization


candidates = [
    value
    for value in sys.argv
    if value.lower().replace("/", "\\").endswith("preview_soil_y_directions_v3.py")
]
if not candidates:
    raise RuntimeError("Cannot locate preview_soil_y_directions_v3.py in argv")
directory = os.path.dirname(os.path.abspath(candidates[-1]))
base_path = os.path.join(directory, "preview_soil_y_directions.py")
with open(base_path, "r", encoding="utf-8") as stream:
    source = stream.read()
exec(compile(source, base_path, "exec"), globals(), globals())
