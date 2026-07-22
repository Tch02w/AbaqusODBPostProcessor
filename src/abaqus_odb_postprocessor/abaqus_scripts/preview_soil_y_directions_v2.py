"""Abaqus 2025 loader for the two-direction soil section preview."""

from __future__ import print_function

import os
import visualization


directory = os.path.dirname(os.path.abspath(__file__))
base_path = os.path.join(directory, "preview_soil_y_directions.py")
with open(base_path, "r", encoding="utf-8") as stream:
    source = stream.read()
exec(compile(source, base_path, "exec"), globals(), globals())
