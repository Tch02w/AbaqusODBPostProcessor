from abaqus import session
from abaqusConstants import *
import os
import time
import visualization
import displayGroupOdbToolset as dgo


odb = session.openOdb(name=r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb", readOnly=True)
session.odbData[odb.name].setValues(activeFrames=(("U10D", ("0:1:1",)),))
viewport = session.Viewport(name="FreeBody ALL Test 2", origin=(0, 0), width=160, height=100)
viewport.makeCurrent()
viewport.setValues(displayedObject=odb)
viewport.odbDisplay.setFrame(step=2, frame=0)
viewport.odbDisplay.display.setValues(plotState=(UNDEFORMED,))
viewport.odbDisplay.displayGroup.replace(
    leaf=dgo.LeafFromElementSets(elementSets=("SET-PILE_CON",))
)
cut = viewport.odbDisplay.ViewCut(
    name="TEST_ALL_XY_2",
    shape=PLANE,
    origin=(0.0, 0.0, 15750.0),
    normal=(0.0, 0.0, 1.0),
    axis2=(1.0, 0.0, 0.0),
    followDeformation=OFF,
)
cut.setValues(showModelAboveCut=OFF, showModelBelowCut=ON, showModelOnCut=ON, showFreeBodyCut=ON)
viewport.odbDisplay.setValues(viewCut=ON, viewCutNames=("TEST_ALL_XY_2",))
viewport.odbDisplay.viewCutOptions.setValues(
    displaySlicing=ON,
    freeBodyCutThru=CURRENT_DISPLAY_GROUP,
    freeBodyStepThru=ACTIVE_CUT_RANGE,
    numCutFreeBody=100,
    cutFreeBodyMin=cut.cutRange[0] + 10.0,
    cutFreeBodyMax=cut.cutRange[1] - 10.0,
    componentResolution=CSYS,
    csysName=GLOBAL,
)
session.freeBodyReportOptions.setValues(
    numDigits=10,
    forceThreshold=1.0e-12,
    momentThreshold=1.0e-12,
    numberFormat=SCIENTIFIC,
    reportFormat=COMMA_SEPARATED_VALUES,
    csysType=GLOBAL,
)
path = os.path.abspath(os.path.join(os.getcwd(), "abaqus_odb_prototype", "freebody_all_two_frames_v2.csv"))
start = time.time()
session.writeFreeBodyReport(fileName=path, append=OFF, stepFrame=ALL, odb=odb)
with open(path + ".timing.txt", "w", encoding="utf-8") as stream:
    stream.write("elapsed_seconds={0}\nbytes={1}\nactive={2}\n".format(time.time() - start, os.path.getsize(path), session.odbData[odb.name].activeFrames))
odb.close()
