"""Use Abaqus display-group label strings for the exact HRB400 element leaf."""

from __future__ import print_function
import os

this_path = os.path.abspath(__file__) if "__file__" in globals() else ""
if not this_path:
    import sys
    candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_compat_v7.py")]
    if not candidates: raise RuntimeError("Cannot locate extract_job_compat_v7.py in argv")
    this_path = os.path.abspath(candidates[-1])
v6_path = os.path.join(os.path.dirname(this_path), "extract_job_compat_v6.py")
with open(v6_path, "r", encoding="utf-8") as stream: loader = stream.read()
loader = loader.replace("extract_job_compat_v6.py", "extract_job_compat_v7.py")
loader = loader.replace(
    'exec(compile(loader, v5_path, "exec"), globals(), globals())',
    'loader = loader.replace("elementLabels=tuple(sorted(values))", "elementLabels=tuple(str(value) for value in sorted(values))")\n'
    'exec(compile(loader, v5_path, "exec"), globals(), globals())',
)
exec(compile(loader, v6_path, "exec"), globals(), globals())
