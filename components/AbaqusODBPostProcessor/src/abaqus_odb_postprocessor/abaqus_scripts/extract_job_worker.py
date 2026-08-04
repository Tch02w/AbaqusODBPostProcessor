"""Formal worker entry with verified +Y soil camera and explicit metadata."""

from __future__ import print_function

import json
import os
import sys


candidates = [
    value
    for value in sys.argv
    if value.lower().replace("/", "\\").endswith("extract_job_worker.py")
]
if not candidates:
    raise RuntimeError("Cannot locate extract_job_worker.py in argv")
script_path = os.path.abspath(candidates[-1])
directory = os.path.dirname(script_path)
base_path = os.path.join(directory, "extract_job_worker_base.py")
with open(base_path, "r", encoding="utf-8") as stream:
    source = stream.read()
source = source.replace(
    'viewport.view.setValues(session.views["Bottom"])',
    "viewport.view.setViewpoint("
    "viewVector=(0.0, 1.0, 0.0), "
    "cameraUpVector=(0.0, 0.0, 1.0))",
)
for marker in (
    "viewVector=(0.0, 1.0, 0.0)",
    "cameraUpVector=(0.0, 0.0, 1.0)",
    "showModelAboveCut=OFF, showModelBelowCut=ON, showModelOnCut=ON",
    "displaySlicing=OFF",
):
    if marker not in source:
        raise RuntimeError("Formal soil camera patch failed: {0}".format(marker))
exec(compile(source, base_path, "exec"), globals(), globals())

metadata_path = os.path.join(output_dir, "metadata.json")
with open(metadata_path, "r", encoding="utf-8") as stream:
    metadata = json.load(stream)
metadata.update(
    {
        "soil_view_preset": "Explicit XZ",
        "soil_view_vector": [0.0, 1.0, 0.0],
        "soil_camera_up_vector": [0.0, 0.0, 1.0],
        "soil_view_cut": "Y-Plane",
        "soil_view_cut_manager": {
            "above": False,
            "on": True,
            "below": True,
            "free_body": False,
        },
        "soil_display_slicing": False,
    }
)
with open(metadata_path, "w", encoding="utf-8") as stream:
    json.dump(metadata, stream, ensure_ascii=False, indent=2)
