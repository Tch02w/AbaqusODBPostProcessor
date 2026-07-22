"""Final worker entry with XZ soil viewed from the cut-face normal (+Y)."""

from __future__ import print_function
import os, sys

candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_final_v16.py")]
if not candidates: raise RuntimeError("Cannot locate extract_job_final_v16.py in argv")
script_path = os.path.abspath(candidates[-1]); directory = os.path.dirname(script_path)
base_path = os.path.join(directory, "extract_job_final_v14.py")
with open(base_path, "r", encoding="utf-8") as stream: source = stream.read()
source = source.replace("extract_job_final_v14.py", "extract_job_final_v16.py")
final_extension = r'''
source += '\n# compatibility marker: damage_spectrum_name = "DAMAGE_FIXED_10"\n'
source = source.replace(
    'viewport.view.setViewpoint(viewVector=(0.0, -1.0, 0.0), cameraUpVector=(0.0, 0.0, 1.0))',
    'viewport.view.setViewpoint(viewVector=(0.0, 1.0, 0.0), cameraUpVector=(0.0, 0.0, 1.0))',
)
if 'viewVector=(0.0, 1.0, 0.0)' not in source:
    raise RuntimeError("XZ cut-face viewing direction patch failed")
'''
source = source.replace("for component_marker in (\n", final_extension + "for component_marker in (\n")
if final_extension not in source: raise RuntimeError("Cannot inject final XZ direction patch")
exec(compile(source, base_path, "exec"), globals(), globals())
