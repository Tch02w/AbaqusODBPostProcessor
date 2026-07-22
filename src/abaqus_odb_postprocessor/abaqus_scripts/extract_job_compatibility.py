"""Abaqus 2025 compatibility entry for the configurable extraction worker.

The stable worker is kept in ``extract_job.py``.  This entry applies narrowly
scoped source compatibility patches before executing it inside Abaqus/CAE.
"""

from __future__ import print_function

import os
import sys


script_candidates = [
    value
    for value in sys.argv
    if value.lower().replace("/", "\\").endswith("extract_job_compatibility.py")
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

# U is nodal; stresses and state variables remain integration-point output.
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

# Select longitudinal reinforcement by material first, then by element type and
# orientation.  The material is intentionally mandatory: silently including
# stirrups would corrupt both the cloud plot and the real axial force.
source = source.replace(
    'rebar_set_name = config["rebar_set"]\n',
    'rebar_set_name = config["rebar_set"]\n'
    'longitudinal_material_name = str(config.get("longitudinal_material", "HRB400")).strip()\n'
    'if not longitudinal_material_name:\n'
    '    raise RuntimeError("longitudinal_material must not be blank")\n',
)
source = source.replace(
    '    if not instance_name or not element.type.upper().startswith("T3D2"):\n'
    '        continue\n'
    '    if instance_name not in node_maps:',
    '    if not instance_name or not element.type.upper().startswith("T3D2"):\n'
    '        continue\n'
    '    section_category_name = str(getattr(getattr(element, "sectionCategory", None), "name", ""))\n'
    '    if longitudinal_material_name.upper() not in section_category_name.upper():\n'
    '        continue\n'
    '    if instance_name not in node_maps:',
)
source = source.replace(
    'components = {}\nfor geometry in element_geometry.values():',
    'if not element_geometry:\n'
    '    raise RuntimeError("No longitudinal T3D2 elements with material {0} were found in {1}".format(longitudinal_material_name, rebar_set_name))\n\n'
    'components = {}\nfor geometry in element_geometry.values():',
)
source = source.replace(
    '            "method": "T3D2 per element, no FreeBody slicing",\n',
    '            "method": "material + T3D2 orientation; per element; no FreeBody slicing",\n'
    '            "longitudinal_material": longitudinal_material_name,\n',
)
source = source.replace(
    '    "rebar_set": rebar_set_name,\n',
    '    "rebar_set": rebar_set_name,\n'
    '    "longitudinal_material": longitudinal_material_name,\n'
    '    "comparison_group": config.get("comparison_group", ""),\n'
    '    "legend_ranges": config.get("legend_ranges", {}),\n',
)

# A view cut used for FreeBody must be disabled before changing to a different
# display group; otherwise embedded truss elements can trigger an Abaqus error.
source = source.replace(
    'def render(spec):\n    folder = os.path.join(frame_root, spec["name"])',
    'def render(spec):\n'
    '    viewport.odbDisplay.setValues(viewCut=OFF)\n'
    '    folder = os.path.join(frame_root, spec["name"])',
)

# LeafFromElementLabels in Abaqus 2025 takes two arguments: one instance name
# and its label sequence.  Add one leaf per instance so the plot uses exactly
# the same filtered HRB400 longitudinal elements as the force calculation.
old_longitudinal = (
    '    if spec.get("longitudinal"):\n'
    '        grouped = {}\n'
    '        for key in element_geometry:\n'
    '            grouped.setdefault(key[0], []).append(str(key[1]))\n'
    '        labels = tuple((name, tuple(values)) for name, values in sorted(grouped.items()))\n'
    '        viewport.odbDisplay.displayGroup.replace(leaf=dgo.LeafFromElementLabels(elementLabels=labels))\n'
    '    else:\n'
)
new_longitudinal = (
    '    if spec.get("longitudinal"):\n'
    '        grouped = {}\n'
    '        for key in element_geometry:\n'
    '            grouped.setdefault(key[0], []).append(int(key[1]))\n'
    '        first_leaf = True\n'
    '        for instance_name, values in sorted(grouped.items()):\n'
    '            leaf = dgo.LeafFromElementLabels(\n'
    '                partInstanceName=instance_name,\n'
    '                elementLabels=tuple(sorted(values)),\n'
    '            )\n'
    '            if first_leaf:\n'
    '                viewport.odbDisplay.displayGroup.replace(leaf=leaf)\n'
    '                first_leaf = False\n'
    '            else:\n'
    '                viewport.odbDisplay.displayGroup.add(leaf=leaf)\n'
    '    else:\n'
)
source = source.replace(old_longitudinal, new_longitudinal)

# CFST FreeBody uses the union of concrete and steel sets.
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

# White viewport background is printed into PNG (not transparent), which also
# keeps the GIF background white after Pillow conversion.
source = source.replace(
    'session.pngOptions.setValues(imageSize=(800, 600))\n',
    'session.graphicsOptions.setValues(\n'
    '    backgroundStyle=SOLID,\n'
    '    backgroundColor="#FFFFFF",\n'
    '    backgroundBottomColor="#FFFFFF",\n'
    ')\n'
    'session.printOptions.setValues(rendition=COLOR, vpDecorations=OFF, vpBackground=ON, compass=OFF)\n'
    'session.pngOptions.setValues(imageSize=(800, 600))\n',
)

# User-specified ten-band damage palette.  Colors are ordered from minimum to
# maximum, and values outside the fixed 0..0.886 range retain the endpoint
# colors.  Other fields take comparison-group limits from job_config.json.
source = source.replace(
    'viewport.viewportAnnotationOptions.setValues(\n    triad=ON, legend=ON, title=OFF, state=ON, annotations=OFF, compass=OFF\n)\n',
    'viewport.viewportAnnotationOptions.setValues(\n'
    '    triad=ON, legend=ON, title=OFF, state=ON, annotations=OFF, compass=OFF\n'
    ')\n'
    'damage_spectrum_name = "DAMAGE_FIXED_10"\n'
    'damage_colors = (\n'
    '    "#F2F2F2", "#D9E8F5", "#B7D4EA", "#7BC8B8", "#2FBF71",\n'
    '    "#B7DD3B", "#F2D13D", "#E85B2A", "#CC1F2F", "#FF0000",\n'
    ')\n'
    'if damage_spectrum_name not in session.spectrums:\n'
    '    session.Spectrum(name=damage_spectrum_name, colors=damage_colors)\n'
    'legend_ranges = config.get("legend_ranges", {})\n',
)
source = source.replace(
    '    viewport.odbDisplay.contourOptions.setValues(minAutoCompute=ON, maxAutoCompute=ON)\n',
    '    if spec["variable"] in ("DAMAGET", "DAMAGEC"):\n'
    '        viewport.odbDisplay.contourOptions.setValues(\n'
    '            contourType=BANDED, contourStyle=DISCRETE, numIntervals=10,\n'
    '            intervalType=UNIFORM, spectrum=damage_spectrum_name,\n'
    '            minAutoCompute=OFF, minValue=0.0,\n'
    '            maxAutoCompute=OFF, maxValue=0.886,\n'
    '            outsideLimitsMode=SPECIFY,\n'
    '            outsideLimitsBelowColor="#F2F2F2",\n'
    '            outsideLimitsAboveColor="#FF0000",\n'
    '        )\n'
    '    elif spec["name"] in legend_ranges:\n'
    '        limits = legend_ranges[spec["name"]]\n'
    '        viewport.odbDisplay.contourOptions.setValues(\n'
    '            minAutoCompute=OFF, minValue=float(limits["min"]),\n'
    '            maxAutoCompute=OFF, maxValue=float(limits["max"]),\n'
    '            outsideLimitsMode=SPECTRUM,\n'
    '        )\n'
    '    else:\n'
    '        viewport.odbDisplay.contourOptions.setValues(minAutoCompute=ON, maxAutoCompute=ON)\n',
)

required = [
    "def primary_variable(variable, refinement, output_position):",
    "longitudinal_material_name = str(config.get",
    "LeafFromElementLabels(\n                partInstanceName=instance_name",
    "backgroundColor=\"#FFFFFF\"",
    "damage_spectrum_name = \"DAMAGE_FIXED_10\"",
    "spec[\"name\"] in legend_ranges",
    "freebody_set_names = (concrete_set_name,)",
]
for marker in required:
    if marker not in source:
        raise RuntimeError("Compatibility patch was not applied: {0}".format(marker))

sys.argv = [script_argument, config_argument]
exec(compile(source, source_path, "exec"), globals(), globals())
