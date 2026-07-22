"""Abaqus/CAE argv and built-in compatibility entry for fast legend scanning."""

from __future__ import print_function
import os, sys

candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("scan_legend_limits_compat_v2.py")]
if not candidates: raise RuntimeError("Cannot locate fast legend scanner in argv")
script_path = os.path.abspath(candidates[-1]); base_path = os.path.join(os.path.dirname(script_path), "scan_legend_limits.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()
source = source.replace("import argparse, json, math, os, sys", "import argparse, builtins, json, math, os, sys")
source = source.replace("sum(point[axis] for point in points)", "builtins.sum(point[axis] for point in points)")
source = source.replace("parser.parse_args(arguments)", "parser.parse_known_args(arguments)[0]")
for marker in ("import argparse, builtins", "builtins.sum(point[axis]", "parse_known_args(arguments)[0]"):
    if marker not in source: raise RuntimeError("fast legend compatibility patch failed: {0}".format(marker))
exec(compile(source, base_path, "exec"), globals(), globals())
