"""Final worker extension: component stresses, XZ soil without cut-cell edges, default Rainbow elsewhere."""

from __future__ import print_function
import os, sys

candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_final_v14.py")]
if not candidates: raise RuntimeError("Cannot locate extract_job_final_v14.py in argv")
script_path = os.path.abspath(candidates[-1]); directory = os.path.dirname(script_path)
base_path = os.path.join(directory, "extract_job_final_v13.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()
source = source.replace("extract_job_final_v13.py", "extract_job_final_v14.py")

extension = r'''
source = source.replace(
    'concrete_set_name = config["pile_concrete_set"]\n',
    'concrete_set_name = config["pile_concrete_set"]\n'
    'pile_steel_set_name = str(config.get("pile_steel_set", "")).strip()\n',
)
source = source.replace(
    '{"name": "PILE_S_MISES", "set": pile_display_set_name, "variable": "S", "refinement": (INVARIANT, "Mises")},\n',
    '{"name": "PILE_CON_S_MISES", "set": concrete_set_name, "variable": "S", "refinement": (INVARIANT, "Mises")},\n'
    '    {"name": "PILE_STEEL_S_MISES", "set": pile_steel_set_name, "variable": "S", "refinement": (INVARIANT, "Mises")},\n',
)
source = source.replace(
    '    if spec.get("soil_section"):\n',
    '    viewport.odbDisplay.commonOptions.setValues(\n'
    '        renderStyle=SHADED, visibleEdges=(NONE if spec.get("soil_section") else FREE)\n'
    '    )\n'
    '    if spec.get("soil_section"):\n',
)
source = source.replace(
    'for spec in specs:\n    render(spec)\n',
    'for spec in specs:\n'
    '    if spec.get("longitudinal") or spec.get("set", ""):\n'
    '        render(spec)\n',
)
source = source.replace(
    '        viewport.odbDisplay.contourOptions.setValues(\n'
    '            minAutoCompute=OFF, minValue=float(limits["min"]),\n'
    '            maxAutoCompute=OFF, maxValue=float(limits["max"]),\n'
    '            outsideLimitsMode=SPECTRUM,\n'
    '        )\n'
    '    else:\n'
    '        viewport.odbDisplay.contourOptions.setValues(minAutoCompute=ON, maxAutoCompute=ON)\n',
    '        viewport.odbDisplay.contourOptions.setValues(\n'
    '            contourType=BANDED, contourStyle=CONTINUOUS, numIntervals=12,\n'
    '            intervalType=UNIFORM, spectrum="Rainbow", contourEdges=OFF,\n'
    '            minAutoCompute=OFF, minValue=float(limits["min"]),\n'
    '            maxAutoCompute=OFF, maxValue=float(limits["max"]),\n'
    '            outsideLimitsMode=SPECTRUM,\n'
    '        )\n'
    '    else:\n'
    '        viewport.odbDisplay.contourOptions.setValues(\n'
    '            contourType=BANDED, contourStyle=CONTINUOUS, numIntervals=12,\n'
    '            intervalType=UNIFORM, spectrum="Rainbow", contourEdges=OFF,\n'
    '            minAutoCompute=ON, maxAutoCompute=ON, outsideLimitsMode=SPECTRUM,\n'
    '        )\n',
)
for component_marker in (
    'pile_steel_set_name = str(config.get("pile_steel_set", "")).strip()',
    '"PILE_CON_S_MISES"', '"PILE_STEEL_S_MISES"',
    'visibleEdges=(NONE if spec.get("soil_section") else FREE)',
    'spectrum="Rainbow", contourEdges=OFF',
):
    if component_marker not in source:
        raise RuntimeError("Component/soil patch failed: {0}".format(component_marker))

'''
source = source.replace("for display_marker in (\n", extension + "for display_marker in (\n")
if extension not in source: raise RuntimeError("Cannot inject component extension")
exec(compile(source, base_path, "exec"), globals(), globals())
