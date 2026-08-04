"""Frame-selection extension layered on the Abaqus 2025 v5 worker entry."""

from __future__ import print_function

import os


this_path = os.path.abspath(__file__) if "__file__" in globals() else ""
if not this_path:
    import sys
    candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_frame_compatibility.py")]
    if not candidates:
        raise RuntimeError("Cannot locate extract_job_frame_compatibility.py in argv")
    this_path = os.path.abspath(candidates[-1])
v5_path = os.path.join(os.path.dirname(this_path), "extract_job_compatibility.py")
with open(v5_path, "r", encoding="utf-8") as stream:
    loader = stream.read()

loader = loader.replace("extract_job_compatibility.py", "extract_job_frame_compatibility.py")
injection = r'''
# Preserve the complete time history.  GUI frame selection only limits
# expensive section/FreeBody and longitudinal-rebar numeric processing.
# Load-point history and every contour animation retain all canonical frames.
source = source.replace(
    'timeline_headers = [\n',
    'full_timeline = list(timeline)\n'
    'frame_mode = str(config.get("frame_mode", "all"))\n'
    'requested_sequences = config.get("selected_sequence_indices")\n'
    'selected_timeline = list(full_timeline)\n'
    'if requested_sequences is not None:\n'
    '    requested_sequences = set(int(value) for value in requested_sequences)\n'
    '    selected_timeline = [item for item in full_timeline if item["SequenceIndex"] in requested_sequences]\n'
    'if not selected_timeline:\n'
    '    raise RuntimeError("Frame selection produced no timeline points")\n\n'
    'timeline_headers = [\n',
)

source = source.replace(
    'rebar_region = assembly.elementSets[rebar_set_name]\n',
    'write_csv(\n'
    '    os.path.join(data_dir, "selected_timeline_alignment.csv"),\n'
    '    timeline_headers,\n'
    '    [{name: item[name] for name in timeline_headers} for item in selected_timeline],\n'
    ')\n'
    'timeline = selected_timeline\n\n'
    'rebar_region = assembly.elementSets[rebar_set_name]\n',
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
    '    written = []\n'
    '    last_rendered_item = None\n'
    '    for animation_index, item in enumerate(timeline):\n',
    '    written = []\n'
    '    last_rendered_item = None\n'
    '    render_timeline = full_timeline\n'
    '    for animation_index, item in enumerate(render_timeline):\n',
)

source = source.replace(
    '    "timeline_points": len(timeline),\n',
    '    "timeline_points": len(timeline),\n'
    '    "canonical_timeline_points": len(full_timeline),\n'
    '    "load_timehistory_points": len(full_timeline),\n'
    '    "selected_processing_points": len(timeline),\n'
    '    "frame_mode": frame_mode,\n'
    '    "selected_sequence_indices": [item["SequenceIndex"] for item in timeline],\n',
)

for frame_marker in (
    'full_timeline = list(timeline)',
    'selected_timeline = [item for item in full_timeline',
    'selected_timeline_alignment.csv',
    'frame_mode in ("auto", "manual")',
    'render_timeline = full_timeline',
    '"load_timehistory_points": len(full_timeline)',
    '"canonical_timeline_points": len(full_timeline)',
):
    if frame_marker not in source:
        raise RuntimeError("Frame compatibility patch was not applied: {0}".format(frame_marker))

'''
loader = loader.replace("required = [\n", injection + "required = [\n")
exec(compile(loader, v5_path, "exec"), globals(), globals())
