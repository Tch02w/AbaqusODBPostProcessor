from abaqus import session
from abaqusConstants import *
vp = session.Viewport(name="Projection Probe")
for name in ("setProjection", "setValues"):
    obj = getattr(vp.view, name, None)
    print(name, repr(getattr(obj, "__doc__", None)))
print("view members", [name for name in dir(vp.view) if "project" in name.lower()])
