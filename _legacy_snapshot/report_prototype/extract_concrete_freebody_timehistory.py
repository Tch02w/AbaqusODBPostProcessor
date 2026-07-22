from abaqus import session
from abaqusConstants import *
import csv
import json
import os
import visualization
import displayGroupOdbToolset as dgo


ODB_PATH = r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb"
START_STEP = "U10D"
END_STEP = "V20D"
PILE_CONCRETE_SET = "SET-PILE_CON"
GROUND_Z_MM = 23250.0
PILE_Z_MIN_MM = 7750.0
PILE_Z_MAX_MM = 23750.0
CUT_COUNT = 100

BASE_DIR = os.path.abspath(os.path.join(os.getcwd(), "abaqus_odb_prototype"))
FREEBODY_DIR = os.path.join(BASE_DIR, "output_GJA-32_U20D_V20D", "freebody")
RAW_DIR = os.path.join(FREEBODY_DIR, "time_history_raw")
os.makedirs(RAW_DIR, exist_ok=True)
OUTPUT_PATH = os.path.join(FREEBODY_DIR, "concrete_axial_force_timehistory.csv")
METADATA_PATH = os.path.join(FREEBODY_DIR, "concrete_axial_force_timehistory_metadata.json")
LOG_PATH = os.path.join(FREEBODY_DIR, "concrete_axial_force_timehistory.log")


def log(message):
    text = str(message)
    print(text)
    with open(LOG_PATH, "a", encoding="utf-8") as stream:
        stream.write(text + "\n")


with open(LOG_PATH, "w", encoding="utf-8") as stream:
    stream.write("")

odb = session.openOdb(name=ODB_PATH, readOnly=True)
step_names = list(odb.steps.keys())
start_index = step_names.index(START_STEP)
end_index = step_names.index(END_STEP)

time_offsets = {}
running_total = 0.0
for step_name in step_names:
    time_offsets[step_name] = running_total
    frames = odb.steps[step_name].frames
    if frames:
        running_total += float(frames[-1].frameValue)

timeline = []
sequence_index = 0
for step_index in range(start_index, end_index + 1):
    step_name = step_names[step_index]
    step = odb.steps[step_name]
    for frame_index, frame in enumerate(step.frames):
        if step_index > start_index and frame_index == 0:
            continue
        timeline.append(
            {
                "SequenceIndex": sequence_index,
                "StepIndex": step_index,
                "StepName": step_name,
                "FrameIndex": frame_index,
                "IncrementNumber": int(frame.incrementNumber),
                "StepTime": float(frame.frameValue),
                "TotalTime": time_offsets[step_name] + float(frame.frameValue),
            }
        )
        sequence_index += 1

viewport = session.Viewport(
    name="Concrete FreeBody Time History", origin=(0, 0), width=160, height=100
)
viewport.makeCurrent()
viewport.setValues(displayedObject=odb)
first = timeline[0]
viewport.odbDisplay.setFrame(step=first["StepIndex"], frame=first["FrameIndex"])
viewport.odbDisplay.display.setValues(plotState=(UNDEFORMED,))
leaf = dgo.LeafFromElementSets(elementSets=(PILE_CONCRETE_SET,))
viewport.odbDisplay.displayGroup.replace(leaf=leaf)

z_mid = 0.5 * (PILE_Z_MIN_MM + PILE_Z_MAX_MM)
cut_name = "PILE_CON_XY_100_TIME_HISTORY"
cut = viewport.odbDisplay.ViewCut(
    name=cut_name,
    shape=PLANE,
    origin=(0.0, 0.0, z_mid),
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
viewport.odbDisplay.setValues(viewCut=ON, viewCutNames=(cut_name,))
epsilon = max(1.0, (PILE_Z_MAX_MM - PILE_Z_MIN_MM) / 1600.0)
viewport.odbDisplay.viewCutOptions.setValues(
    displaySlicing=ON,
    freeBodyCutThru=CURRENT_DISPLAY_GROUP,
    freeBodyStepThru=ACTIVE_CUT_RANGE,
    numCutFreeBody=CUT_COUNT,
    cutFreeBodyMin=cut.cutRange[0] + epsilon,
    cutFreeBodyMax=cut.cutRange[1] - epsilon,
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

headers = [
    "SequenceIndex",
    "StepIndex",
    "StepName",
    "FrameIndex",
    "IncrementNumber",
    "StepTime",
    "TotalTime",
    "CutIndex",
    "CutName",
    "Elevation_mm",
    "DepthFromGround_mm",
    "ConcreteAxial_CompressionPositive_N",
    "Fx_N",
    "Fy_N",
    "Fz_N",
    "Mx_Nmm",
    "My_Nmm",
    "Mz_Nmm",
]

total_rows = 0
with open(OUTPUT_PATH, "w", newline="", encoding="utf-8-sig") as output_stream:
    writer = csv.DictWriter(output_stream, fieldnames=headers)
    writer.writeheader()
    for item in timeline:
        viewport.odbDisplay.setFrame(
            step=item["StepIndex"], frame=item["FrameIndex"]
        )
        raw_path = os.path.join(
            RAW_DIR, "freebody_seq{0:04d}_raw.csv".format(item["SequenceIndex"])
        )
        session.writeFreeBodyReport(
            fileName=raw_path,
            append=OFF,
            step=item["StepIndex"],
            frame=item["FrameIndex"],
            stepFrame=SPECIFY,
            odb=odb,
        )
        frame_rows = 0
        with open(raw_path, "r", encoding="utf-8-sig") as raw_stream:
            reader = csv.DictReader(raw_stream)
            for cut_index, raw in enumerate(reader, 1):
                z = float(raw["CutZ"])
                fz = float(raw["Fz"])
                writer.writerow(
                    {
                        "SequenceIndex": item["SequenceIndex"],
                        "StepIndex": item["StepIndex"],
                        "StepName": item["StepName"],
                        "FrameIndex": item["FrameIndex"],
                        "IncrementNumber": item["IncrementNumber"],
                        "StepTime": item["StepTime"],
                        "TotalTime": item["TotalTime"],
                        "CutIndex": cut_index,
                        "CutName": raw["CutName"].strip(),
                        "Elevation_mm": z,
                        "DepthFromGround_mm": GROUND_Z_MM - z,
                        "ConcreteAxial_CompressionPositive_N": fz,
                        "Fx_N": float(raw["Fx"]),
                        "Fy_N": float(raw["Fy"]),
                        "Fz_N": fz,
                        "Mx_Nmm": float(raw["Mx"]),
                        "My_Nmm": float(raw["My"]),
                        "Mz_Nmm": float(raw["Mz"]),
                    }
                )
                frame_rows += 1
        if frame_rows != CUT_COUNT:
            raise RuntimeError(
                "Sequence {0} returned {1} cuts, expected {2}".format(
                    item["SequenceIndex"], frame_rows, CUT_COUNT
                )
            )
        total_rows += frame_rows
        log(
            "FRAME {0:02d}/40 {1} frame={2} totalTime={3:.12g} cuts={4}".format(
                item["SequenceIndex"],
                item["StepName"],
                item["FrameIndex"],
                item["TotalTime"],
                frame_rows,
            )
        )

metadata = {
    "odb_path": ODB_PATH,
    "start_step": START_STEP,
    "end_step": END_STEP,
    "alignment": "SequenceIndex + StepName + FrameIndex + TotalTime",
    "boundary_frame_rule": "skip frame 0 after the first selected step",
    "timeline_points": len(timeline),
    "cuts_per_timeline_point": CUT_COUNT,
    "total_rows": total_rows,
    "region": PILE_CONCRETE_SET,
    "shape": "XY plane, normal global Z",
    "deformation": "undeformed",
    "ground_elevation_model_z_mm": GROUND_Z_MM,
}
with open(METADATA_PATH, "w", encoding="utf-8") as stream:
    json.dump(metadata, stream, ensure_ascii=False, indent=2)

odb.close()
log(json.dumps(metadata, ensure_ascii=False))
