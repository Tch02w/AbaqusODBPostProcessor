"""Final worker: retained-soil XZ cut centered and zoomed on the pile/root-key zone."""

from __future__ import print_function
import os, sys

candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_final_v17.py")]
if not candidates: raise RuntimeError("Cannot locate extract_job_final_v17.py in argv")
script_path = os.path.abspath(candidates[-1]); directory = os.path.dirname(script_path)
base_path = os.path.join(directory, "extract_job_final_v16.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()
source = source.replace("extract_job_final_v16.py", "extract_job_final_v17.py")
soil_detail = r'''
source = source.replace(
    '    viewport.view.fitView()\n    written = []\n',
    '    viewport.view.fitView()\n'
    '    if spec.get("soil_section"):\n'
    '        old_target = tuple(float(value) for value in viewport.view.cameraTarget)\n'
    '        old_position = tuple(float(value) for value in viewport.view.cameraPosition)\n'
    '        detail_target = (0.0, float(settings["soil_section_coordinate"]), 0.5 * (pile_z_min + pile_z_max))\n'
    '        shift = tuple(detail_target[index] - old_target[index] for index in range(3))\n'
    '        detail_position = tuple(old_position[index] + shift[index] for index in range(3))\n'
    '        viewport.view.setValues(cameraTarget=detail_target, cameraPosition=detail_position)\n'
    '        viewport.view.zoom(zoomFactor=float(settings.get("soil_zoom_factor", 3.0)), mode=RELATIVE)\n'
    '    written = []\n',
)
for soil_detail_marker in (
    'detail_target = (0.0, float(settings["soil_section_coordinate"]), 0.5 * (pile_z_min + pile_z_max))',
    'viewport.view.zoom(zoomFactor=float(settings.get("soil_zoom_factor", 3.0)), mode=RELATIVE)',
):
    if soil_detail_marker not in source:
        raise RuntimeError("Retained-soil detail view patch failed: {0}".format(soil_detail_marker))
'''
source = source.replace("for component_marker in (\n", soil_detail + "for component_marker in (\n")
if soil_detail not in source: raise RuntimeError("Cannot inject retained-soil detail view")
exec(compile(source, base_path, "exec"), globals(), globals())
