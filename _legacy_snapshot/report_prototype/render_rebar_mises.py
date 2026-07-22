from abaqus import session
from abaqusConstants import *
import os
import shutil
import traceback
import visualization
import displayGroupOdbToolset as dgo


ODB_PATH = r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb"
START_STEP = "U10D"
END_STEP = "V20D"
INSTANCE_NAME = "MR-1"
SEQUENCE_NAME = "REBAR_LONG_S_MISES_UNDEFORMED"
BASE_DIR = os.path.abspath(os.path.join(os.getcwd(), "abaqus_odb_prototype"))
OUTPUT_DIR = os.path.join(BASE_DIR, "output_GJA-32_U20D_V20D")
FRAME_DIR = os.path.join(OUTPUT_DIR, "frames", SEQUENCE_NAME)
CONTOUR_DIR = os.path.join(OUTPUT_DIR, "contours")
LOG_PATH = os.path.join(OUTPUT_DIR, "rebar", "rebar_mises_render.log")
os.makedirs(FRAME_DIR, exist_ok=True)
os.makedirs(CONTOUR_DIR, exist_ok=True)


def log(message):
    text = str(message)
    print(text)
    with open(LOG_PATH, "a", encoding="utf-8") as stream:
        stream.write(text + "\n")


with open(LOG_PATH, "w", encoding="utf-8") as stream:
    stream.write("Longitudinal rebar Mises rendering started\n")

viewport = session.Viewport(name="Longitudinal Rebar Mises", origin=(0, 0), width=180, height=120)
viewport.makeCurrent()
odb = session.openOdb(name=ODB_PATH, readOnly=True)
viewport.setValues(displayedObject=odb)
session.pngOptions.setValues(imageSize=(800, 600))
viewport.viewportAnnotationOptions.setValues(
    triad=ON,
    legend=ON,
    title=OFF,
    state=ON,
    annotations=OFF,
    compass=OFF,
)

leaf = dgo.LeafFromPartInstance(partInstanceName=(INSTANCE_NAME,))
viewport.odbDisplay.displayGroup.replace(leaf=leaf)
viewport.odbDisplay.setPrimaryVariable(
    variableLabel="S",
    outputPosition=INTEGRATION_POINT,
    refinement=(INVARIANT, "Mises"),
)
viewport.odbDisplay.display.setValues(plotState=(CONTOURS_ON_UNDEF,))
viewport.odbDisplay.contourOptions.setValues(minAutoCompute=ON, maxAutoCompute=ON)
viewport.view.setViewpoint(
    viewVector=(1.0, 1.0, 0.5),
    cameraUpVector=(0.0, 0.0, 1.0),
)
viewport.view.fitView()

step_names = list(odb.steps.keys())
start_index = step_names.index(START_STEP)
end_index = step_names.index(END_STEP)
sequence = []
for step_index in range(start_index, end_index + 1):
    step_name = step_names[step_index]
    for frame_index, frame in enumerate(odb.steps[step_name].frames):
        if step_index > start_index and frame_index == 0:
            continue
        sequence.append((step_index, step_name, frame_index, frame))

written = []
for animation_index, (step_index, step_name, frame_index, frame) in enumerate(sequence):
    if "S" not in frame.fieldOutputs:
        log("SKIP {0} frame {1}: S missing".format(step_name, frame_index))
        continue
    viewport.odbDisplay.setFrame(step=step_index, frame=frame_index)
    file_base = os.path.join(
        FRAME_DIR,
        "{0:04d}_{1}_F{2:04d}".format(animation_index, step_name, frame_index),
    )
    try:
        session.printToFile(fileName=file_base, format=PNG, canvasObjects=(viewport,))
        written.append(file_base + ".png")
    except Exception as exc:
        log(
            "FAILED {0} frame {1}: {2}\n{3}".format(
                step_name, frame_index, repr(exc), traceback.format_exc()
            )
        )
    if animation_index % 5 == 0 or animation_index == len(sequence) - 1:
        log("RENDER {0}/{1}".format(animation_index + 1, len(sequence)))

if written:
    shutil.copyfile(
        written[-1],
        os.path.join(CONTOUR_DIR, SEQUENCE_NAME + "_LAST.png"),
    )

log("COMPLETED frames={0}".format(len(written)))
odb.close()
