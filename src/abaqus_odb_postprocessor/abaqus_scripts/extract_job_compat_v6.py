"""Frame-selection extension layered on the Abaqus 2025 v5 worker entry."""

from __future__ import print_function

import os


this_path = os.path.abspath(__file__) if "__file__" in globals() else ""
if not this_path:
    import sys
    candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_compat_v6.py")]
    if not candidates:
        raise RuntimeError("Cannot locate extract_job_compat_v6.py in argv")
    this_path = os.path.abspath(candidates[-1])
v5_path = os.path.join(os.path.dirname(this_path), "extract_job_compat_v5.py")
with open(v5_path, "r", encoding="utf-8") as stream:
    loader = stream.read()

loader = loader.replace("extract_job_compat_v5.py", "extract_job_compat_v6.py")
injection = r'''
# Preserve canonical indices, then reduce every extracted dataset and cloud to
# the increments selected in the GUI.
source = source.replace(
    'timeline_headers = [\n',
    'full_timeline = list(timeline)\n'
    'frame_mode = str(config.get("frame_mode", "all"))\n'
    'requested_sequences = config.get("selected_sequence_indices")\n'
    'if requested_sequences is not None:\n'
    '    requested_sequences = set(int(value) for value in requested_sequences)\n'
    '    timeline = [item for item in timeline if item["SequenceIndex"] in requested_sequences]\n'
    'if not timeline:\n'
    '    raise RuntimeError("Frame selection produced no timeline points")\n\n'
    'timeline_headers = [\n',
)

source = source.replace(
    'targets = []\n'
    'if full_freebody:\n'
    '    targets = [("SEQ{0:04d}".format(item["SequenceIndex"]), item) for item in timeline]\n'
    'else:\n'
    '    targets.append(("LAST", timeline[-1]))\n'
    '    if 0 <= prefracture_index < len(timeline) and prefracture_index != timeline[-1]["SequenceIndex"]:\n'
    '        targets.append(("PRE_FRACTURE", timeline[prefracture_index]))\n',
    'targets = []\n'
    'if frame_mode in ("auto", "manual") or full_freebody:\n'
    '    targets = [("SEQ{0:04d}".format(item["SequenceIndex"]), item) for item in timeline]\n'
    'else:\n'
    '    targets.append(("LAST", timeline[-1]))\n'
    '    for candidate in timeline:\n'
    '        if candidate["SequenceIndex"] == prefracture_index and prefracture_index != timeline[-1]["SequenceIndex"]:\n'
    '            targets.append(("PRE_FRACTURE", candidate))\n'
    '            break\n',
)

source = source.replace(
    '    "timeline_points": len(timeline),\n',
    '    "timeline_points": len(timeline),\n'
    '    "canonical_timeline_points": len(full_timeline),\n'
    '    "frame_mode": frame_mode,\n'
    '    "selected_sequence_indices": [item["SequenceIndex"] for item in timeline],\n',
)

for frame_marker in (
    'full_timeline = list(timeline)',
    'frame_mode in ("auto", "manual")',
    '"canonical_timeline_points": len(full_timeline)',
):
    if frame_marker not in source:
        raise RuntimeError("Frame compatibility patch was not applied: {0}".format(frame_marker))

'''
loader = loader.replace("required = [\n", injection + "required = [\n")
exec(compile(loader, v5_path, "exec"), globals(), globals())
