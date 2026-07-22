"""Compatibility entry point for the Abaqus 2025 worker.

The main worker intentionally remains readable as a journal-style Abaqus script.
This loader applies two small configuration-aware adjustments before execution:
nodal positioning for U contours and the concrete+steel union for CFST FreeBody.
"""

from __future__ import print_function

import os


source_path = os.path.join(os.path.dirname(__file__), "extract_job.py")
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
    'viewport.odbDisplay.displayGroup.replace(\n    leaf=dgo.LeafFromElementSets(elementSets=(concrete_set_name,))\n)\ncut_name = "PILE_CON_XY_{0}".format(cut_count)',
    'freebody_set_names = (concrete_set_name,)\n'
    'if config.get("pile_type") == "CFST" and config.get("pile_steel_set"):\n'
    '    freebody_set_names = (concrete_set_name, config["pile_steel_set"])\n'
    'viewport.odbDisplay.displayGroup.replace(\n'
    '    leaf=dgo.LeafFromElementSets(elementSets=freebody_set_names)\n'
    ')\n'
    'cut_name = "PILE_CON_XY_{0}".format(cut_count)',
)

if "def primary_variable(variable, refinement, output_position):" not in source:
    raise RuntimeError("Compatibility patch for U output position was not applied")
if "freebody_set_names = (concrete_set_name,)" not in source:
    raise RuntimeError("Compatibility patch for CFST FreeBody union was not applied")

exec(compile(source, source_path, "exec"), globals(), globals())

