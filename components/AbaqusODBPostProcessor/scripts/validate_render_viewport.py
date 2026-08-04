"""Render one real ODB frame to validate viewport/image-size behavior."""

from __future__ import print_function

from abaqus import session
from abaqusConstants import *
import os
import sys
import visualization


def create_large_viewport(name, pixel_width, pixel_height):
    aspect_ratio = float(pixel_width) / float(pixel_height)
    attempts = []
    for maximum_width, maximum_height in (
        (396.0, 264.0),
        (360.0, 240.0),
        (330.0, 220.0),
        (300.0, 200.0),
        (270.0, 180.0),
        (240.0, 160.0),
        (180.0, 120.0),
    ):
        if maximum_width / maximum_height >= aspect_ratio:
            height = maximum_height
            width = height * aspect_ratio
        else:
            width = maximum_width
            height = width / aspect_ratio
        if width < 30.0 or height < 30.0:
            continue
        try:
            viewport = session.Viewport(
                name=name,
                origin=(0, 0),
                width=width,
                height=height,
                border=OFF,
                titleBar=OFF,
            )
            return viewport, (width, height)
        except Exception as error:
            attempts.append("{0:g}x{1:g}: {2}".format(width, height, error))
    raise RuntimeError("Cannot create render viewport: " + " | ".join(attempts))


arguments = sys.argv[-4:]
if len(arguments) != 4:
    raise RuntimeError(
        "Usage: validate_render_viewport.py -- ODB OUTPUT_BASE WIDTH HEIGHT"
    )
odb_path = os.path.abspath(arguments[0])
output_base = os.path.abspath(arguments[1])
image_width = int(arguments[2])
image_height = int(arguments[3])
output_directory = os.path.dirname(output_base)
if output_directory and not os.path.isdir(output_directory):
    os.makedirs(output_directory)

viewport, viewport_size = create_large_viewport(
    "Viewport size validation",
    image_width,
    image_height,
)
viewport.makeCurrent()
odb = session.openOdb(name=odb_path, readOnly=True)
viewport.setValues(displayedObject=odb)
step_index = len(odb.steps) - 1
frame_index = len(list(odb.steps.values())[-1].frames) - 1
viewport.odbDisplay.setFrame(step=step_index, frame=frame_index)
viewport.odbDisplay.setPrimaryVariable(
    variableLabel="U",
    outputPosition=NODAL,
    refinement=(INVARIANT, "Magnitude"),
)
viewport.odbDisplay.display.setValues(plotState=(CONTOURS_ON_DEF,))
viewport.odbDisplay.commonOptions.setValues(renderStyle=SHADED, visibleEdges=FREE)
viewport.viewportAnnotationOptions.setValues(
    triad=OFF,
    legend=ON,
    title=OFF,
    state=OFF,
    annotations=OFF,
    compass=OFF,
)
viewport.view.setProjection(projection=PARALLEL)
viewport.view.fitView()
session.graphicsOptions.setValues(
    backgroundStyle=SOLID,
    backgroundColor="#FFFFFF",
    backgroundBottomColor="#FFFFFF",
)
session.printOptions.setValues(
    rendition=COLOR,
    vpDecorations=OFF,
    vpBackground=ON,
    compass=OFF,
)
session.pngOptions.setValues(imageSize=(image_width, image_height))
session.printToFile(
    fileName=output_base,
    format=PNG,
    canvasObjects=(viewport,),
)
print(
    "VALIDATION_RENDER|viewport_mm={0:.1f}x{1:.1f}|image_px={2}x{3}".format(
        viewport_size[0],
        viewport_size[1],
        image_width,
        image_height,
    )
)
odb.close()
