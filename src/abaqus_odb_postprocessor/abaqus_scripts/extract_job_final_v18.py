"""Final worker matching the user's Abaqus soil-section operation sequence."""

from __future__ import print_function
import os, sys

candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_final_v18.py")]
if not candidates: raise RuntimeError("Cannot locate extract_job_final_v18.py in argv")
script_path = os.path.abspath(candidates[-1]); directory = os.path.dirname(script_path)
base_path = os.path.join(directory, "extract_job_final_v14.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()
source = source.replace("extract_job_final_v14.py", "extract_job_final_v18.py")

exact_soil_sequence = r'''
source += '\n# compatibility marker: damage_spectrum_name = "DAMAGE_FIXED_10"\n'
source = source.replace(
    'visibleEdges=(NONE if spec.get("soil_section") else FREE)',
    'visibleEdges=FREE',
)
old_soil_block = (
    '    if spec.get("soil_section"):\n'
    '        view_cut_name = "SOIL_XZ_" + spec["name"]\n'
    '        soil_cut = viewport.odbDisplay.ViewCut(\n'
    '            name=view_cut_name,\n'
    '            shape=PLANE,\n'
    '            origin=(0.0, float(settings["soil_section_coordinate"]), 0.5 * (pile_z_min + pile_z_max)),\n'
    '            normal=(0.0, 1.0, 0.0),\n'
    '            axis2=(0.0, 0.0, 1.0),\n'
    '            followDeformation=OFF,\n'
    '        )\n'
    '        soil_cut.setValues(showModelAboveCut=OFF, showModelBelowCut=OFF, showModelOnCut=ON, showFreeBodyCut=OFF)\n'
    '        viewport.odbDisplay.setValues(viewCut=ON, viewCutNames=(view_cut_name,))\n'
    '        viewport.view.setViewpoint(viewVector=(0.0, -1.0, 0.0), cameraUpVector=(0.0, 0.0, 1.0))\n'
)
new_soil_block = (
    '    if spec.get("soil_section"):\n'
    '        # Apply Bottom View -> PARALLEL -> display-group Replace -> FREE -> active Y-Plane.\n'
    '        viewport.view.setValues(session.views["Bottom"])\n'
    '        viewport.view.setProjection(projection=PARALLEL)\n'
    '        viewport.odbDisplay.commonOptions.setValues(renderStyle=SHADED, visibleEdges=FREE)\n'
    '        view_cut_name = "Y-Plane"\n'
    '        if view_cut_name in viewport.odbDisplay.viewCuts:\n'
    '            soil_cut = viewport.odbDisplay.viewCuts[view_cut_name]\n'
    '            soil_cut.setValues(\n'
    '                origin=(0.0, float(settings["soil_section_coordinate"]), 0.5 * (pile_z_min + pile_z_max)),\n'
    '                normal=(0.0, 1.0, 0.0), axis2=(0.0, 0.0, 1.0), followDeformation=OFF,\n'
    '            )\n'
    '        else:\n'
    '            soil_cut = viewport.odbDisplay.ViewCut(\n'
    '                name=view_cut_name, shape=PLANE,\n'
    '                origin=(0.0, float(settings["soil_section_coordinate"]), 0.5 * (pile_z_min + pile_z_max)),\n'
    '                normal=(0.0, 1.0, 0.0), axis2=(0.0, 0.0, 1.0), followDeformation=OFF,\n'
    '            )\n'
    '        soil_cut.setValues(\n'
    '            showModelAboveCut=OFF, showModelBelowCut=OFF, showModelOnCut=ON, showFreeBodyCut=OFF\n'
    '        )\n'
    '        viewport.odbDisplay.setValues(viewCut=ON, viewCutNames=(view_cut_name,))\n'
)
if old_soil_block not in source:
    raise RuntimeError("Original soil-section block was not found")
source = source.replace(old_soil_block, new_soil_block)
for exact_soil_marker in (
    'viewport.view.setValues(session.views["Bottom"])',
    'viewport.view.setProjection(projection=PARALLEL)',
    'view_cut_name = "Y-Plane"',
    'viewport.odbDisplay.commonOptions.setValues(renderStyle=SHADED, visibleEdges=FREE)',
):
    if exact_soil_marker not in source:
        raise RuntimeError("Exact soil operation patch failed: {0}".format(exact_soil_marker))
'''
source = source.replace("for component_marker in (\n", exact_soil_sequence + "for component_marker in (\n")
if exact_soil_sequence not in source: raise RuntimeError("Cannot inject exact soil operation sequence")
exec(compile(source, base_path, "exec"), globals(), globals())
