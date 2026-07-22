"""Abaqus/CAE fast scanner with component-separated stress ranges."""

from __future__ import print_function
import os, sys

candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("scan_legend_limits_compat_v3.py")]
if not candidates: raise RuntimeError("Cannot locate fast legend scanner in argv")
script_path = os.path.abspath(candidates[-1])
base_path = os.path.join(os.path.dirname(script_path), "scan_legend_limits.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()

source = source.replace("import argparse, json, math, os, sys", "import argparse, builtins, json, math, os, sys")
source = source.replace("sum(point[axis] for point in points)", "builtins.sum(point[axis] for point in points)")
source = source.replace("parser.parse_args(arguments)", "parser.parse_known_args(arguments)[0]")
source = source.replace(
    '    ("PILE_S_MISES", "S", "pile", (INVARIANT, "Mises"), INTEGRATION_POINT, False),\n',
    '    ("PILE_CON_S_MISES", "S", "concrete", (INVARIANT, "Mises"), INTEGRATION_POINT, False),\n'
    '    ("PILE_STEEL_S_MISES", "S", "steel", (INVARIANT, "Mises"), INTEGRATION_POINT, False),\n',
)
source = source.replace(
    '    region_names = {"pile": config["pile_display_set"], "concrete": concrete_name,\n'
    '                    "soil": soil_name, "rebar": "rebar"}\n',
    '    region_names = {"pile": config["pile_display_set"], "concrete": concrete_name,\n'
    '                    "steel": config.get("pile_steel_set", ""),\n'
    '                    "soil": soil_name, "rebar": "rebar"}\n',
)
source = source.replace(
    '    for spec, variable, region_key, refinement, position, soil_section in SPECS:\n'
    '        set_group(viewport, region_names[region_key], rebar_labels)\n',
    '    for spec, variable, region_key, refinement, position, soil_section in SPECS:\n'
    '        region_name = region_names[region_key]\n'
    '        if not region_name:\n'
    '            continue\n'
    '        set_group(viewport, region_name, rebar_labels)\n',
)
for marker in (
    "import argparse, builtins", "builtins.sum(point[axis]", "parse_known_args(arguments)[0]",
    '"PILE_CON_S_MISES"', '"PILE_STEEL_S_MISES"', '"steel": config.get("pile_steel_set", "")',
    "if not region_name:",
):
    if marker not in source: raise RuntimeError("fast scanner patch failed: {0}".format(marker))
exec(compile(source, base_path, "exec"), globals(), globals())
