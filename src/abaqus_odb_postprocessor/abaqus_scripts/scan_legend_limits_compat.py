"""Abaqus/CAE argv compatibility entry for the fast legend scanner."""

from __future__ import print_function
import os, sys

candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("scan_legend_limits_compat.py")]
if not candidates: raise RuntimeError("Cannot locate fast legend scanner in argv")
script_path = os.path.abspath(candidates[-1])
base_path = os.path.join(os.path.dirname(script_path), "scan_legend_limits.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()
source = source.replace("parser.parse_args(arguments)", "parser.parse_known_args(arguments)[0]")
if "parser.parse_known_args(arguments)[0]" not in source: raise RuntimeError("argv compatibility patch failed")
exec(compile(source, base_path, "exec"), globals(), globals())
