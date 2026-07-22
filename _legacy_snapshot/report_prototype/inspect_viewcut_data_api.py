from abaqus import session
from abaqusConstants import *
import os
import visualization
import displayGroupOdbToolset as dgo


odb = session.openOdb(name=r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb", readOnly=True)
viewport = session.Viewport(name="Inspect ViewCut", origin=(0, 0), width=160, height=100)
viewport.setValues(displayedObject=odb)
viewport.odbDisplay.setFrame(step=2, frame=0)
viewport.odbDisplay.display.setValues(plotState=(UNDEFORMED,))
viewport.odbDisplay.displayGroup.replace(
    leaf=dgo.LeafFromElementSets(elementSets=("SET-PILE_CON",))
)
cut = viewport.odbDisplay.ViewCut(
    name="INSPECT_CUT",
    shape=PLANE,
    origin=(0.0, 0.0, 15750.0),
    normal=(0.0, 0.0, 1.0),
    axis2=(1.0, 0.0, 0.0),
    followDeformation=OFF,
)
path = os.path.abspath(os.path.join(os.getcwd(), "abaqus_odb_prototype", "viewcut_api.txt"))
with open(path, "w", encoding="utf-8") as stream:
    stream.write("CUT_TYPE\n" + repr(type(cut)) + "\n")
    stream.write("CUT_DIR\n" + repr(dir(cut)) + "\n")
    stream.write("FREEBODIES\n" + repr(session.freeBodies.keys()) + "\n")
    stream.write("VIEWCUTS\n" + repr(viewport.odbDisplay.viewCuts.keys()) + "\n")
odb.close()
