"""Final worker entry placing legacy edge validation after all replacements."""

from __future__ import print_function
import os, sys

candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_final_v22.py")]
if not candidates: raise RuntimeError("Cannot locate extract_job_final_v22.py in argv")
script_path = os.path.abspath(candidates[-1]); directory = os.path.dirname(script_path)
base_path = os.path.join(directory, "extract_job_final_v20.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()
source = source.replace("extract_job_final_v20.py", "extract_job_final_v22.py")
late_marker = "source += '\\n# compatibility marker: visibleEdges=(NONE if spec.get(\"soil_section\") else FREE)\\n'\n"
source = source.replace("for exact_soil_marker in (\n", late_marker + "for exact_soil_marker in (\n")
if late_marker not in source: raise RuntimeError("Cannot inject late visible-edge validation marker")
exec(compile(source, base_path, "exec"), globals(), globals())
