"""Abaqus 2025 compatibility entry for the configurable extraction worker."""

from __future__ import print_function

import os
import sys


script_argument = os.path.abspath(sys.argv[0])
script_directory = os.path.dirname(script_argument)
source_path = os.path.join(script_directory, "extract_job.py")
if not os.path.isfile(source_path):
    raise RuntimeError(
        "Cannot locate extract_job.py beside noGUI script: argv0={0}, cwd={1}".format(
            sys.argv[0], os.getcwd()
        )
    )

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

