"""Compatibility entry for selected-frame group contour rendering."""

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
base_path = os.path.join(os.path.dirname(script_path), "render_group_contours_base.py")
with open(base_path, "r", encoding="utf-8") as stream:
    source = stream.read()

source = source.replace(
    'timeline = read_rows(os.path.join(source_output_dir, "data", "timeline_alignment.csv"))\n'
    'if not timeline:\n',
    'timeline = read_rows(os.path.join(source_output_dir, "data", "timeline_alignment.csv"))\n'
    'requested_sequences = set(int(value) for value in config.get("selected_sequence_indices", []))\n'
    'if requested_sequences:\n'
    '    timeline = [row for row in timeline if int(row["SequenceIndex"]) in requested_sequences]\n'
    'if not timeline:\n',
)
source = source.replace(
    'frame = list(odb.steps.values())[step_index].frames[frame_index]',
    'frame = odb.steps[list(odb.steps.keys())[step_index]].frames[frame_index]',
)
for marker in (
    'requested_sequences = set(int(value)',
    'odb.steps[list(odb.steps.keys())[step_index]].frames[frame_index]',
):
    if marker not in source:
        raise RuntimeError("Group renderer v2 patch failed: {0}".format(marker))
exec(compile(source, base_path, "exec"), globals(), globals())
