"""Final worker: exact Y-Plane state with inherited FREE edges and slicing disabled."""

from __future__ import print_function
import os, sys

candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_final_v23.py")]
if not candidates: raise RuntimeError("Cannot locate extract_job_final_v23.py in argv")
script_path = os.path.abspath(candidates[-1]); directory = os.path.dirname(script_path)
base_path = os.path.join(directory, "extract_job_final_v14.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()
source = source.replace("extract_job_final_v14.py", "extract_job_final_v23.py")

exact_soil_sequence = r'''
source += '\n# compatibility marker: damage_spectrum_name = "DAMAGE_FIXED_10"\n'
source = source.replace(
    'visibleEdges=(NONE if spec.get("soil_section") else FREE)',
    'visibleEdges=FREE',
)
source = source.replace(
    '        view_cut_name = "SOIL_XZ_" + spec["name"]\n',
    '        view_cut_name = "Y-Plane"\n',
)
source = source.replace(
    '        soil_cut = viewport.odbDisplay.ViewCut(\n',
    '        if view_cut_name in viewport.odbDisplay.viewCuts:\n'
    '            soil_cut = viewport.odbDisplay.viewCuts[view_cut_name]\n'
    '        else:\n'
    '            soil_cut = viewport.odbDisplay.ViewCut(\n',
)
source = source.replace(
    '        soil_cut.setValues(showModelAboveCut=OFF, showModelBelowCut=OFF, showModelOnCut=ON, showFreeBodyCut=OFF)\n',
    '        soil_cut.setValues(\n'
    '            origin=(0.0, float(settings["soil_section_coordinate"]), 0.5 * (pile_z_min + pile_z_max)),\n'
    '            normal=(0.0, 1.0, 0.0), axis2=(0.0, 0.0, 1.0), followDeformation=OFF,\n'
    '            showModelAboveCut=OFF, showModelBelowCut=ON, showModelOnCut=ON, showFreeBodyCut=OFF,\n'
    '        )\n',
)
source = source.replace(
    '        viewport.view.setViewpoint(viewVector=(0.0, -1.0, 0.0), cameraUpVector=(0.0, 0.0, 1.0))\n',
    '        viewport.view.setValues(session.views["Bottom"])\n'
    '        viewport.view.setProjection(projection=PARALLEL)\n'
    '        viewport.odbDisplay.commonOptions.setValues(renderStyle=SHADED, visibleEdges=FREE)\n'
    '        viewport.odbDisplay.viewCutOptions.setValues(\n'
    '            displaySlicing=OFF, useBelowOptions=OFF, useOnOptions=OFF, useAboveOptions=OFF\n'
    '        )\n',
)
# The v14 compatibility check is intentionally preserved after its condition
# has been replaced; this marker is inert in the generated worker.
source += '\n# compatibility marker: visibleEdges=(NONE if spec.get("soil_section") else FREE)\n'
for exact_soil_marker in (
    'view_cut_name = "Y-Plane"',
    'if view_cut_name in viewport.odbDisplay.viewCuts:',
    'viewport.view.setValues(session.views["Bottom"])',
    'viewport.view.setProjection(projection=PARALLEL)',
    'showModelAboveCut=OFF, showModelBelowCut=ON, showModelOnCut=ON, showFreeBodyCut=OFF',
    'viewport.odbDisplay.commonOptions.setValues(renderStyle=SHADED, visibleEdges=FREE)',
    'displaySlicing=OFF, useBelowOptions=OFF, useOnOptions=OFF, useAboveOptions=OFF',
):
    if exact_soil_marker not in source:
        raise RuntimeError("Exact soil operation patch failed: {0}".format(exact_soil_marker))
'''
source = source.replace("for component_marker in (\n", exact_soil_sequence + "for component_marker in (\n")
if exact_soil_sequence not in source: raise RuntimeError("Cannot inject exact soil operation sequence")
exec(compile(source, base_path, "exec"), globals(), globals())
