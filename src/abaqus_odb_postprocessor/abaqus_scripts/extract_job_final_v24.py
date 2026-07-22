"""Final worker: explicit XZ soil camera from the retained-half-space side."""

from __future__ import print_function
import os
import sys


candidates = [
    value
    for value in sys.argv
    if value.lower().replace("/", "\\").endswith("extract_job_final_v24.py")
]
if not candidates:
    raise RuntimeError("Cannot locate extract_job_final_v24.py in argv")
script_path = os.path.abspath(candidates[-1])
directory = os.path.dirname(script_path)
base_path = os.path.join(directory, "extract_job_final_v23.py")
with open(base_path, "r", encoding="utf-8") as stream:
    source = stream.read()

source = source.replace("extract_job_final_v23.py", "extract_job_final_v24.py")
source = source.replace(
    'viewport.view.setValues(session.views["Bottom"])',
    "viewport.view.setViewpoint("
    "viewVector=(0.0, -1.0, 0.0), "
    "cameraUpVector=(0.0, 0.0, 1.0))",
)

required = (
    "extract_job_final_v24.py",
    "viewVector=(0.0, -1.0, 0.0)",
    "cameraUpVector=(0.0, 0.0, 1.0)",
)
for marker in required:
    if marker not in source:
        raise RuntimeError("Explicit soil camera patch failed: {0}".format(marker))

exec(compile(source, base_path, "exec"), globals(), globals())
