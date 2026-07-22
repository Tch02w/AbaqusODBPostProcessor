"""Abaqus 2025 compatibility entry for the configurable extraction worker."""

from __future__ import print_function

import os
import sys


script_candidates = [
    value
    for value in sys.argv
    if value.lower().replace("/", "\\").endswith("extract_job_compat_v4.py")
]
config_candidates = [value for value in sys.argv if value.lower().endswith(".json")]
if not script_candidates or not config_candidates:
    raise RuntimeError("Cannot locate noGUI script and job JSON in argv: {0}".format(repr(sys.argv)))

script_argument = os.path.abspath(script_candidates[-1])
config_argument = os.path.abspath(config_candidates[-1])
source_path = os.path.join(os.path.dirname(script_argument), "extract_job.py")
if not os.path.isfile(source_path):
    raise RuntimeError("Cannot locate extract_job.py beside: {0}".format(script_argument))

with open(source_path, "r", encoding="utf-8") as stream:
    source = stream.read()

source = source.replace(
    "def primary_variable(variable, refinement):\n",
    "def primary_variable(variable, refinement, output_position):\n",
)
source = source.replace(
    "viewport.odbDisplay.setPrimaryVariable(variableLabel=variable, outputPosition=INTEGRATION_POINT)",
    "viewport.odbDisplay.setPrimaryVariable(variableLabel=variable, outputPosition=output_position)",
)
source = source.replace(
    "outputPosition=INTEGRATION_POINT,\n            refinement=refinement,",
    "outputPosition=output_position,\n            refinement=refinement,",
)
source = source.replace(
    'primary_variable(spec["variable"], spec.get("refinement"))',
    'primary_variable(spec["variable"], spec.get("refinement"), spec.get("position", INTEGRATION_POINT))',
)
source = source.replace(
    '{"name": "PILE_U_MAG", "set": pile_display_set_name, "variable": "U", "refinement": (INVARIANT, "Magnitude")}',
    '{"name": "PILE_U_MAG", "set": pile_display_set_name, "variable": "U", "refinement": (INVARIANT, "Magnitude"), "position": NODAL}',
)
source = source.replace(
    'def render(spec):\n    folder = os.path.join(frame_root, spec["name"])',
    'def render(spec):\n'
    '    viewport.odbDisplay.setValues(viewCut=OFF)\n'
    '    folder = os.path.join(frame_root, spec["name"])',
)
source = source.replace(
    'viewport.odbDisplay.displayGroup.replace(\n    leaf=dgo.LeafFromElementSets(elementSets=(concrete_set_name,))\n)\ncut_name = "PILE_CON_XY_{0}".format(cut_count)',
    'freebody_set_names = (concrete_set_name,)\n'
    'if config.get("pile_type") == "CFST" and config.get("pile_steel_set"):\n'
    '    freebody_set_names = (concrete_set_name, config["pile_steel_set"])\n'
    'viewport.odbDisplay.displayGroup.replace(\n'
    '    leaf=dgo.LeafFromElementSets(elementSets=freebody_set_names)\n'
    ')\n'
    'cut_name = "PILE_CON_XY_{0}".format(cut_count)',
)

required = [
    "def primary_variable(variable, refinement, output_position):",
    "freebody_set_names = (concrete_set_name,)",
    "viewport.odbDisplay.setValues(viewCut=OFF)\n    folder =",
]
for marker in required:
    if marker not in source:
        raise RuntimeError("Compatibility patch was not applied: {0}".format(marker))

sys.argv = [script_argument, config_argument]
exec(compile(source, source_path, "exec"), globals(), globals())

