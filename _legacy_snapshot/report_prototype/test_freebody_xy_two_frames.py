from abaqus import session
from abaqusConstants import *
import csv
import os
import time
import visualization
import displayGroupOdbToolset as dgo


odb = session.openOdb(name=r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb", readOnly=True)
session.odbData[odb.name].setValues(activeFrames=(("U10D", ("0:1:1",)),))
viewport = session.Viewport(name="FreeBody XY Test", origin=(0, 0), width=160, height=100)
viewport.makeCurrent()
viewport.setValues(displayedObject=odb)
viewport.odbDisplay.setFrame(step=2, frame=0)
viewport.odbDisplay.display.setValues(plotState=(UNDEFORMED,))
viewport.odbDisplay.displayGroup.replace(
    leaf=dgo.LeafFromElementSets(elementSets=("SET-PILE_CON",))
)
cut = viewport.odbDisplay.ViewCut(
    name="TEST_XY_XY",
    shape=PLANE,
    origin=(0.0, 0.0, 15750.0),
    normal=(0.0, 0.0, 1.0),
    axis2=(1.0, 0.0, 0.0),
    followDeformation=OFF,
)
cut.setValues(showModelAboveCut=OFF, showModelBelowCut=ON, showModelOnCut=ON, showFreeBodyCut=ON)
viewport.odbDisplay.setValues(viewCut=ON, viewCutNames=("TEST_XY_XY",))
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
start = time.time()
objects = session.XYDataFromFreeBody(
    odb=odb,
    force=ON,
    moment=OFF,
    heatFlowRate=OFF,
    resultant=OFF,
    comp1=OFF,
    comp2=OFF,
    comp3=ON,
)
elapsed = time.time() - start
path = os.path.abspath(os.path.join(os.getcwd(), "abaqus_odb_prototype", "freebody_xy_two_frames.csv"))
with open(path, "w", newline="", encoding="utf-8-sig") as stream:
    writer = csv.writer(stream)
    writer.writerow(("ObjectIndex", "Name", "PointIndex", "Time", "Value"))
    for object_index, item in enumerate(objects):
        for point_index, pair in enumerate(item.data):
            writer.writerow((object_index, item.name, point_index, pair[0], pair[1]))
with open(path + ".timing.txt", "w", encoding="utf-8") as stream:
    stream.write("elapsed_seconds={0}\nobjects={1}\nrows={2}\n".format(elapsed, len(objects), sum(len(item.data) for item in objects)))
odb.close()
