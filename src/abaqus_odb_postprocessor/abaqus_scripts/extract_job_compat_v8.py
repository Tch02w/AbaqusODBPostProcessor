"""Render every new ODB viewport with only free mesh edges visible."""

from __future__ import print_function
import os, sys
from abaqus import session
from abaqusConstants import FREE
import visualization

session.defaultOdbDisplay.commonOptions.setValues(visibleEdges=FREE)
candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_compat_v8.py")]
if not candidates: raise RuntimeError("Cannot locate extract_job_compat_v8.py in argv")
script_path = os.path.abspath(candidates[-1]); v7_path = os.path.join(os.path.dirname(script_path), "extract_job_compat_v7.py")
with open(v7_path, "r", encoding="utf-8") as stream: loader = stream.read()
loader = loader.replace("extract_job_compat_v7.py", "extract_job_compat_v8.py")
exec(compile(loader, v7_path, "exec"), globals(), globals())
