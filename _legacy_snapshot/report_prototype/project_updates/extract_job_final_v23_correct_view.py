"""Formal worker entry with the verified XZ soil observation direction."""

from __future__ import print_function

import os
import sys


candidates = [
    value
    for value in sys.argv
    if value.lower().replace("/", "\\").endswith("extract_job_final_v23.py")
]
if not candidates:
    raise RuntimeError("Cannot locate extract_job_final_v23.py in argv")

script_path = os.path.abspath(candidates[-1])
directory = os.path.dirname(script_path)
base_path = os.path.join(directory, "extract_job_final_v23_base.py")
with open(base_path, "r", encoding="utf-8") as stream:
    source = stream.read()

source = source.replace(
    'viewport.view.setValues(session.views["Bottom"])',
    "viewport.view.setViewpoint("
    "viewVector=(0.0, 1.0, 0.0), "
    "cameraUpVector=(0.0, 0.0, 1.0))",
)

required = (
    "viewVector=(0.0, 1.0, 0.0)",
    "cameraUpVector=(0.0, 0.0, 1.0)",
    "showModelAboveCut=OFF, showModelBelowCut=ON, showModelOnCut=ON",
    "displaySlicing=OFF",
)
for marker in required:
    if marker not in source:
        raise RuntimeError("Formal soil camera patch failed: {0}".format(marker))

exec(compile(source, base_path, "exec"), globals(), globals())
