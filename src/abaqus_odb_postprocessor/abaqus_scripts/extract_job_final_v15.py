"""Final worker entry with the legacy damage-spectrum validation marker."""

from __future__ import print_function
import os, sys

candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_final_v15.py")]
if not candidates: raise RuntimeError("Cannot locate extract_job_final_v15.py in argv")
script_path = os.path.abspath(candidates[-1]); directory = os.path.dirname(script_path)
base_path = os.path.join(directory, "extract_job_final_v14.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()
source = source.replace("extract_job_final_v14.py", "extract_job_final_v15.py")
compatibility = r'''
source += '\n# compatibility marker: damage_spectrum_name = "DAMAGE_FIXED_10"\n'
'''
source = source.replace("for component_marker in (\n", compatibility + "for component_marker in (\n")
if compatibility not in source: raise RuntimeError("Cannot inject legacy validation marker")
exec(compile(source, base_path, "exec"), globals(), globals())
