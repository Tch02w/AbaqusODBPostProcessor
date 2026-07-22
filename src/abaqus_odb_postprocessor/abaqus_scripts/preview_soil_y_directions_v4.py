"""Abaqus 2025 loader for the two-direction soil section preview."""

from __future__ import print_function

import os
import sys
import visualization


script_candidates = [
    value
    for value in sys.argv
    if value.lower().replace("/", "\\").endswith("preview_soil_y_directions_v4.py")
]
config_candidates = [value for value in sys.argv if value.lower().endswith(".json")]
if not script_candidates or not config_candidates:
    raise RuntimeError("Cannot locate preview script and job JSON in argv: {0}".format(repr(sys.argv)))
script_path = os.path.abspath(script_candidates[-1])
config_path = os.path.abspath(config_candidates[-1])
output_candidates = [
    value
    for value in sys.argv
    if value not in script_candidates
    and value not in config_candidates
    and os.path.isdir(os.path.abspath(value))
]
if not output_candidates:
    raise RuntimeError("Cannot locate preview output directory in argv: {0}".format(repr(sys.argv)))
output_dir = os.path.abspath(output_candidates[-1])
base_path = os.path.join(os.path.dirname(script_path), "preview_soil_y_directions.py")
with open(base_path, "r", encoding="utf-8") as stream:
    source = stream.read()
sys.argv = [base_path, config_path, output_dir]
exec(compile(source, base_path, "exec"), globals(), globals())
