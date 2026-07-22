"""Node-field compatibility wrapper for ``scan_field_ranges.py``."""

from __future__ import print_function

import os


this_path = os.path.abspath(__file__) if "__file__" in globals() else ""
if not this_path:
    import sys
    candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("scan_field_ranges_v2.py")]
    if not candidates: raise RuntimeError("Cannot locate scan_field_ranges_v2.py in argv")
    this_path = os.path.abspath(candidates[-1])
base_path = os.path.join(os.path.dirname(this_path), "scan_field_ranges.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()

source = source.replace(
    '        material = str(config.get("longitudinal_material", "HRB400")).strip()\n',
    '        pile_node_keys = set()\n'
    '        for instance_name, element in region_elements(assembly, regions["pile"]):\n'
    '            for node_label in element.connectivity:\n'
    '                pile_node_keys.add((instance_name, int(node_label)))\n'
    '        material = str(config.get("longitudinal_material", "HRB400")).strip()\n',
)
source = source.replace(
    '                try: values = frame.fieldOutputs[field_name].getSubset(region=regions[region_name]).values\n'
    '                except Exception: continue\n'
    '                selected = []\n'
    '                for value in values:\n'
    '                    key = (value.instance.name, int(getattr(value, "elementLabel", -1)))\n'
    '                    if region_name == "rebar" and key not in rebar_keys: continue\n',
    '                try:\n'
    '                    if field_name == "U":\n'
    '                        values = frame.fieldOutputs[field_name].values\n'
    '                    else:\n'
    '                        values = frame.fieldOutputs[field_name].getSubset(region=regions[region_name]).values\n'
    '                except Exception: continue\n'
    '                selected = []\n'
    '                for value in values:\n'
    '                    if field_name == "U":\n'
    '                        key = (value.instance.name, int(getattr(value, "nodeLabel", -1)))\n'
    '                        if key not in pile_node_keys: continue\n'
    '                    else:\n'
    '                        key = (value.instance.name, int(getattr(value, "elementLabel", -1)))\n'
    '                    if region_name == "rebar" and key not in rebar_keys: continue\n',
)
for marker in ("pile_node_keys = set()", 'if field_name == "U":', "key not in pile_node_keys"):
    if marker not in source: raise RuntimeError("U range patch failed: {0}".format(marker))
exec(compile(source, base_path, "exec"), globals(), globals())
