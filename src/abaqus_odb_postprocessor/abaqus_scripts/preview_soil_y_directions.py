from __future__ import print_function

from abaqus import session
from abaqusConstants import *
import displayGroupOdbToolset as dgo
import json
import os
import sys


arguments = sys.argv[1:]
if "--" in arguments:
    arguments = arguments[arguments.index("--") + 1:]
if len(arguments) != 2:
    raise RuntimeError(
        "Usage: abaqus cae noGUI=preview_soil_y_directions.py -- job_config.json output_dir"
    )

config_path = os.path.abspath(arguments[0])
output_dir = os.path.abspath(arguments[1])
with open(config_path, "r", encoding="utf-8") as stream:
    config = json.load(stream)
os.makedirs(output_dir, exist_ok=True)

odb = session.openOdb(name=os.path.abspath(config["odb_path"]), readOnly=True)
viewport = session.Viewport(name="Soil orientation preview", origin=(0, 0), width=180, height=120)
viewport.makeCurrent()
viewport.setValues(displayedObject=odb)

step_names = list(odb.steps.keys())
step_name = config["end_step"]
step_index = step_names.index(step_name)
frame_index = len(odb.steps[step_name].frames) - 1
viewport.odbDisplay.setFrame(step=step_index, frame=frame_index)

soil_set = config["soil_set"]
viewport.odbDisplay.displayGroup.replace(
    leaf=dgo.LeafFromElementSets(elementSets=(soil_set,))
)
viewport.odbDisplay.setPrimaryVariable(
    variableLabel="PEMAG", outputPosition=INTEGRATION_POINT
)
viewport.odbDisplay.display.setValues(plotState=(CONTOURS_ON_DEF,))
viewport.odbDisplay.commonOptions.setValues(renderStyle=SHADED, visibleEdges=FREE)
limits = config.get("legend_ranges", {}).get("SOIL_PEMAG_XZ", {})
if limits:
    viewport.odbDisplay.contourOptions.setValues(
        contourType=BANDED,
        contourStyle=CONTINUOUS,
        numIntervals=12,
        intervalType=UNIFORM,
        spectrum="Rainbow",
        contourEdges=OFF,
        minAutoCompute=OFF,
        minValue=float(limits["min"]),
        maxAutoCompute=OFF,
        maxValue=float(limits["max"]),
        outsideLimitsMode=SPECTRUM,
    )

view_cut_name = "Y-Plane"
if view_cut_name in viewport.odbDisplay.viewCuts:
    soil_cut = viewport.odbDisplay.viewCuts[view_cut_name]
else:
    soil_cut = viewport.odbDisplay.ViewCut(
        name=view_cut_name,
        shape=PLANE,
        origin=(0.0, 0.0, 0.0),
        normal=(0.0, 1.0, 0.0),
        axis2=(0.0, 0.0, 1.0),
        followDeformation=OFF,
    )
soil_cut.setValues(
    origin=(0.0, float(config["settings"]["soil_section_coordinate"]), 0.0),
    normal=(0.0, 1.0, 0.0),
    axis2=(0.0, 0.0, 1.0),
    followDeformation=OFF,
    showModelAboveCut=OFF,
    showModelBelowCut=ON,
    showModelOnCut=ON,
    showFreeBodyCut=OFF,
)
viewport.odbDisplay.setValues(viewCut=ON, viewCutNames=(view_cut_name,))
viewport.odbDisplay.viewCutOptions.setValues(
    displaySlicing=OFF,
    useBelowOptions=OFF,
    useOnOptions=OFF,
    useAboveOptions=OFF,
)

session.graphicsOptions.setValues(
    backgroundStyle=SOLID,
    backgroundColor="#FFFFFF",
    backgroundBottomColor="#FFFFFF",
)
session.printOptions.setValues(
    rendition=COLOR, vpDecorations=OFF, vpBackground=ON, compass=OFF
)
session.pngOptions.setValues(imageSize=(1600, 1200))
viewport.viewportAnnotationOptions.setValues(
    triad=OFF,
    legend=ON,
    title=OFF,
    state=OFF,
    annotations=OFF,
    compass=OFF,
)
viewport.view.setProjection(projection=PARALLEL)

directions = (
    ("FROM_POSITIVE_Y", (0.0, -1.0, 0.0)),
    ("FROM_NEGATIVE_Y", (0.0, 1.0, 0.0)),
)
for label, view_vector in directions:
    viewport.view.setViewpoint(
        viewVector=view_vector,
        cameraUpVector=(0.0, 0.0, 1.0),
    )
    viewport.view.fitView()
    target = os.path.join(output_dir, "SOIL_PEMAG_{0}".format(label))
    session.printToFile(fileName=target, format=PNG, canvasObjects=(viewport,))
    print("WROTE {0}.png viewVector={1}".format(target, view_vector))

odb.close()

