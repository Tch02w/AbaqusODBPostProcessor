"""Final Abaqus worker: selectable frames and clean high-resolution contour output."""

from __future__ import print_function
import os, sys
from abaqus import session
from abaqusConstants import FREE
import visualization

session.defaultOdbDisplay.commonOptions.setValues(visibleEdges=FREE)
candidates = [value for value in sys.argv if value.lower().replace("/", "\\").endswith("extract_job_final_v11.py")]
if not candidates: raise RuntimeError("Cannot locate extract_job_final_v11.py in argv")
script_path = os.path.abspath(candidates[-1]); directory = os.path.dirname(script_path)
v5_path = os.path.join(directory, "extract_job_compat_v5.py")
v6_path = os.path.join(directory, "extract_job_compat_v6.py")
with open(v5_path, "r", encoding="utf-8") as stream: loader = stream.read()
with open(v6_path, "r", encoding="utf-8") as stream: v6_source = stream.read()
loader = loader.replace("extract_job_compat_v5.py", "extract_job_final_v11.py")
loader = loader.replace("elementLabels=tuple(sorted(values))",
                        "elementLabels=tuple(str(value) for value in sorted(values))")

start = v6_source.index("injection = r'''" ) + len("injection = r'''")
end = v6_source.index("'''\nloader = loader.replace", start)
frame_injection = v6_source[start:end]
display_injection = r'''
source = source.replace(
    '    viewport.odbDisplay.setValues(viewCut=OFF)\n    folder =',
    '    viewport.odbDisplay.setValues(viewCut=OFF)\n'
    '    viewport.view.setProjection(projection=PARALLEL)\n'
    '    folder =',
)
source = source.replace(
    'session.pngOptions.setValues(imageSize=(800, 600))',
    'session.pngOptions.setValues(imageSize=(1600, 1200))',
)
source = source.replace(
    'viewport.viewportAnnotationOptions.setValues(\n'
    '    triad=ON, legend=ON, title=OFF, state=ON, annotations=OFF, compass=OFF\n'
    ')',
    'viewport.viewportAnnotationOptions.setValues(\n'
    '    triad=OFF, legend=ON, title=OFF, state=OFF, annotations=OFF, compass=OFF\n'
    ')',
)
for display_marker in (
    'viewport.view.setProjection(projection=PARALLEL)',
    'session.pngOptions.setValues(imageSize=(1600, 1200))',
    'triad=OFF, legend=ON, title=OFF, state=OFF, annotations=OFF, compass=OFF',
):
    if display_marker not in source:
        raise RuntimeError("Display patch was not applied: {0}".format(display_marker))

'''
loader = loader.replace("required = [\n", frame_injection + display_injection + "required = [\n")
exec(compile(loader, v5_path, "exec"), globals(), globals())
