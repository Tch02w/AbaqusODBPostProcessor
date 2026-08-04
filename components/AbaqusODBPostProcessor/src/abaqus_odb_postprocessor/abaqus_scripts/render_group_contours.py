"""Compatibility entry for full-history and selected-frame contour rendering."""

from __future__ import print_function

import os
import sys


script_candidates = [
    value
    for value in sys.argv
    if value.lower().replace("/", "\\").endswith("render_group_contours.py")
]
if not script_candidates:
    raise RuntimeError("Cannot locate render_group_contours.py in argv")
script_path = os.path.abspath(script_candidates[-1])
base_path = os.path.join(os.path.dirname(script_path), "render_group_contours_core.py")
with open(base_path, "r", encoding="utf-8") as stream:
    source = stream.read()

source = source.replace(
    'frame = list(odb.steps.values())[step_index].frames[frame_index]',
    'frame = odb.steps[list(odb.steps.keys())[step_index]].frames[frame_index]',
)
for marker in (
    'full_timeline = read_rows(',
    'selected_timeline_path = os.path.join(',
    'render_timeline = full_timeline',
    'odb.steps[list(odb.steps.keys())[step_index]].frames[frame_index]',
):
    if marker not in source:
        raise RuntimeError("Group renderer v2 patch failed: {0}".format(marker))
exec(compile(source, base_path, "exec"), globals(), globals())
