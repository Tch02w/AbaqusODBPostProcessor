"""Final worker with Y-Plane manager state: Above OFF, On ON, Below ON, FreeBody OFF."""

from __future__ import print_function
import os, sys

candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_final_v19.py")]
if not candidates: raise RuntimeError("Cannot locate extract_job_final_v19.py in argv")
script_path = os.path.abspath(candidates[-1]); directory = os.path.dirname(script_path)
base_path = os.path.join(directory, "extract_job_final_v18.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()
source = source.replace("extract_job_final_v18.py", "extract_job_final_v19.py")
source = source.replace(
    'showModelAboveCut=OFF, showModelBelowCut=OFF, showModelOnCut=ON, showFreeBodyCut=OFF',
    'showModelAboveCut=OFF, showModelBelowCut=ON, showModelOnCut=ON, showFreeBodyCut=OFF',
)
if 'showModelAboveCut=OFF, showModelBelowCut=ON, showModelOnCut=ON, showFreeBodyCut=OFF' not in source:
    raise RuntimeError("Y-Plane View Cut Manager state patch failed")
exec(compile(source, base_path, "exec"), globals(), globals())
