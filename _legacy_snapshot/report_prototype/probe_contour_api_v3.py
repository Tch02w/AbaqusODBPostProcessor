from __future__ import print_function

from abaqus import session
from abaqusConstants import *
import visualization
import displayGroupOdbToolset as dgo


odb = session.openOdb(name=r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb", readOnly=True)
viewport = session.Viewport(name="API Probe")
viewport.setValues(displayedObject=odb)
objects = {
    "LeafFromOdbElementMaterials": dgo.LeafFromOdbElementMaterials,
    "DisplayGroup.replace": viewport.odbDisplay.displayGroup.replace,
    "DisplayGroup.add": viewport.odbDisplay.displayGroup.add,
    "DisplayGroup.intersect": viewport.odbDisplay.displayGroup.intersect,
    "ContourOptions.setValues": viewport.odbDisplay.contourOptions.setValues,
}
for name, obj in objects.items():
    print("===", name, "===")
    print(repr(getattr(obj, "__doc__", None)))
odb.close()
