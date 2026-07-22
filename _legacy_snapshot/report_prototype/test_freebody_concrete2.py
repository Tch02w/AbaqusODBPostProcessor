from abaqus import session
from abaqusConstants import *
import os
import visualization
import displayGroupOdbToolset as dgo


ODB_PATH = r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb"
OUTPUT_DIR = os.path.abspath(
    os.path.join(os.getcwd(), "abaqus_odb_prototype", "output_GJA-32_U20D_V20D", "freebody")
)
os.makedirs(OUTPUT_DIR, exist_ok=True)

viewport = session.Viewport(name="Concrete Free Body Probe 2", origin=(0, 0), width=160, height=100)
viewport.makeCurrent()
odb = session.openOdb(name=ODB_PATH, readOnly=True)
viewport.setValues(displayedObject=odb)
viewport.odbDisplay.setFrame(step=5, frame=10)
viewport.odbDisplay.display.setValues(plotState=(UNDEFORMED,))

leaf = dgo.LeafFromElementSets(elementSets=("SET-PILE_CON",))
viewport.odbDisplay.displayGroup.replace(leaf=leaf)

cut = viewport.odbDisplay.ViewCut(
    name="PILE_CON_XY_100",
    shape=PLANE,
    origin=(0.0, 0.0, 15750.0),
    normal=(0.0, 0.0, 1.0),
    axis2=(1.0, 0.0, 0.0),
    followDeformation=OFF,
)
cut.setValues(
    showModelAboveCut=OFF,
    showModelBelowCut=ON,
    showModelOnCut=ON,
    showFreeBodyCut=ON,
)
viewport.odbDisplay.setValues(viewCut=ON, viewCutNames=("PILE_CON_XY_100",))
print("CUT_RANGE", cut.cutRange)

viewport.odbDisplay.viewCutOptions.setValues(
    displaySlicing=ON,
    freeBodyCutThru=CURRENT_DISPLAY_GROUP,
    freeBodyStepThru=ACTIVE_CUT_RANGE,
    numCutFreeBody=100,
    cutFreeBodyMin=-7990.0,
    cutFreeBodyMax=7990.0,
    componentResolution=CSYS,
    csysName=GLOBAL,
)

session.freeBodyReportOptions.setValues(
    numDigits=8,
    forceThreshold=1.0e-12,
    momentThreshold=1.0e-12,
    numberFormat=SCIENTIFIC,
    reportFormat=COMMA_SEPARATED_VALUES,
    csysType=GLOBAL,
)

report_path = os.path.join(OUTPUT_DIR, "freebody_concrete_last_frame.csv")
try:
    session.writeFreeBodyReport(
        fileName=report_path,
        append=OFF,
        step=5,
        frame=10,
        stepFrame=SPECIFY,
        odb=odb,
    )
    print("FREE_BODY_REPORT", "SUCCESS", report_path, os.path.getsize(report_path))
except Exception as exc:
    print("FREE_BODY_REPORT", "FAILED", repr(exc))

odb.close()
