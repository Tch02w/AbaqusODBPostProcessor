from __future__ import print_function

from abaqus import session
from abaqusConstants import *
import visualization
import displayGroupOdbToolset as dgo

odb = session.openOdb(name=r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb", readOnly=True)
vp = session.Viewport(name="Legend Probe"); vp.setValues(displayedObject=odb)
vp.odbDisplay.displayGroup.replace(leaf=dgo.LeafFromElementSets(elementSets=("SET-PILE",)))
vp.odbDisplay.setPrimaryVariable(variableLabel="S", outputPosition=INTEGRATION_POINT,
                                 refinement=(INVARIANT, "Mises"))
vp.odbDisplay.display.setValues(plotState=(CONTOURS_ON_DEF,))
vp.odbDisplay.setFrame(step=5, frame=10)
vp.odbDisplay.contourOptions.setValues(minAutoCompute=ON, maxAutoCompute=ON)
session.printToFile(fileName="legend_probe", format=PNG, canvasObjects=(vp,))
options = vp.odbDisplay.contourOptions
print("members", [name for name in dir(options) if "min" in name.lower() or "max" in name.lower()])
for name in ("autoMinValue", "autoMaxValue", "minValue", "maxValue", "minAutoCompute", "maxAutoCompute"):
    try: print(name, getattr(options, name))
    except Exception as exc: print(name, "ERROR", repr(exc))
odb.close()
