"""Render group-specific contour frames without repeating numeric extraction."""

from __future__ import print_function

from abaqus import session
from abaqusConstants import *
import visualization
import displayGroupOdbToolset as dgo
import csv
import json
import os
import sys
import traceback


config_candidates = [value for value in sys.argv if value.lower().endswith(".json")]
if not config_candidates:
    raise RuntimeError("Cannot locate group render job JSON in argv: {0}".format(repr(sys.argv)))
config_path = os.path.abspath(config_candidates[-1])
with open(config_path, "r", encoding="utf-8") as stream:
    config = json.load(stream)

odb_path = os.path.abspath(config["odb_path"])
output_dir = os.path.abspath(config["output_dir"])
source_output_dir = os.path.abspath(config["source_output_dir"])
frame_root = os.path.join(output_dir, "frames")
contour_dir = os.path.join(output_dir, "contours")
os.makedirs(frame_root, exist_ok=True)
os.makedirs(contour_dir, exist_ok=True)
log_path = os.path.join(output_dir, "abaqus_worker.log")


def log(message):
    text = str(message)
    print(text)
    sys.stdout.flush()
    with open(log_path, "a", encoding="utf-8") as stream:
        stream.write(text + "\n")


with open(log_path, "w", encoding="utf-8") as stream:
    stream.write("Abaqus group contour renderer started\n")


def read_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


full_timeline = read_rows(os.path.join(source_output_dir, "data", "timeline_alignment.csv"))
if not full_timeline:
    raise RuntimeError("Numeric cache has no timeline rows: {0}".format(source_output_dir))
selected_timeline_path = os.path.join(
    source_output_dir, "data", "selected_timeline_alignment.csv"
)
if os.path.isfile(selected_timeline_path):
    selected_timeline = read_rows(selected_timeline_path)
else:
    requested_sequences = set(
        int(value) for value in config.get("selected_sequence_indices", [])
    )
    selected_timeline = [
        row
        for row in full_timeline
        if not requested_sequences
        or int(row["SequenceIndex"]) in requested_sequences
    ]
if not selected_timeline:
    raise RuntimeError("Numeric cache has no selected timeline rows: {0}".format(source_output_dir))
timeline = selected_timeline

rebar_rows = read_rows(
    os.path.join(source_output_dir, "rebar", "rebar_element_stress_force_timehistory.csv")
)
longitudinal = {}
for row in rebar_rows:
    key = (row["InstanceName"], int(row["ElementLabel"]))
    longitudinal[key] = True

settings = config["settings"]
image_size_unit = str(settings.get("image_size_unit", "px")).lower()
image_width = int(settings.get("image_width", 1500))
image_height = int(settings.get("image_height", 1000))
if image_size_unit not in ("px", "mm"):
    raise RuntimeError("image_size_unit must be px or mm")
if image_size_unit == "px":
    if not 320 <= image_width <= 4096 or not 320 <= image_height <= 4096:
        raise RuntimeError("Pixel image dimensions must be between 320 and 4096")
    image_size_setting = (image_width, image_height)
else:
    if not 30 <= image_width <= 500 or not 30 <= image_height <= 500:
        raise RuntimeError("Viewport dimensions must be between 30 and 500 mm")
    image_size_setting = SIZE_ON_SCREEN


def create_large_render_viewport(name, unit, requested_width, requested_height):
    """Use a real large canvas so fixed-size annotations stay proportional."""

    if unit == "mm":
        try:
            viewport = session.Viewport(
                name=name,
                origin=(0, 0),
                width=float(requested_width),
                height=float(requested_height),
                border=OFF,
                titleBar=OFF,
            )
            return viewport, (float(requested_width), float(requested_height))
        except Exception as error:
            raise RuntimeError(
                "The requested {0}x{1} mm viewport does not fit the current "
                "screen; reduce the dimensions: {2}".format(
                    requested_width, requested_height, error
                )
            )

    aspect_ratio = float(requested_width) / float(requested_height)
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
    raise RuntimeError(
        "Unable to create a large render viewport: {0}".format(
            " | ".join(attempts)
        )
    )


viewport, render_viewport_mm = create_large_render_viewport(
    "Group contour renderer",
    image_size_unit,
    image_width,
    image_height,
)
viewport.makeCurrent()
odb = session.openOdb(name=odb_path, readOnly=True)
viewport.setValues(displayedObject=odb)
viewport.odbDisplay.setFrame(
    step=int(timeline[0]["StepIndex"]), frame=int(timeline[0]["FrameIndex"])
)

legend_ranges = config.get("legend_ranges", {})
animation_legend_ranges = config.get("animation_legend_ranges", {})
damage_spectrum_name = "DAMAGE_DYNAMIC_10"
damage_colors = (
    "#F2F2F2", "#D9E8F5", "#B7D4EA", "#7BC8B8", "#2FBF71",
    "#B7DD3B", "#F2D13D", "#E85B2A", "#CC1F2F", "#FF0000",
)
if damage_spectrum_name not in session.spectrums:
    session.Spectrum(name=damage_spectrum_name, colors=damage_colors)

session.graphicsOptions.setValues(
    backgroundStyle=SOLID,
    backgroundColor="#FFFFFF",
    backgroundBottomColor="#FFFFFF",
)
session.printOptions.setValues(
    rendition=COLOR, vpDecorations=OFF, vpBackground=ON, compass=OFF
)
session.pngOptions.setValues(imageSize=image_size_setting)
viewport.viewportAnnotationOptions.setValues(
    triad=OFF, legend=ON, title=OFF, state=OFF, annotations=OFF, compass=OFF
)
viewport.view.setProjection(projection=PARALLEL)
viewport.odbDisplay.commonOptions.setValues(renderStyle=SHADED, visibleEdges=FREE)


def replace_longitudinal_group():
    grouped = {}
    for instance_name, element_label in longitudinal:
        grouped.setdefault(instance_name, []).append(element_label)
    first = True
    for instance_name, labels in sorted(grouped.items()):
        leaf = dgo.LeafFromElementLabels(
            partInstanceName=instance_name,
            elementLabels=tuple(str(value) for value in sorted(labels)),
        )
        if first:
            viewport.odbDisplay.displayGroup.replace(leaf=leaf)
            first = False
        else:
            viewport.odbDisplay.displayGroup.add(leaf=leaf)
    if first:
        raise RuntimeError("Numeric cache contains no longitudinal rebar elements")


def primary_variable(variable, refinement):
    output_position = NODAL if variable == "U" else INTEGRATION_POINT
    if refinement is None:
        viewport.odbDisplay.setPrimaryVariable(
            variableLabel=variable, outputPosition=output_position
        )
    else:
        viewport.odbDisplay.setPrimaryVariable(
            variableLabel=variable,
            outputPosition=output_position,
            refinement=refinement,
        )


def set_static_limits(spec):
    limits = legend_ranges.get(spec["name"], {})
    if spec["variable"] in ("DAMAGET", "DAMAGEC"):
        maximum = max(float(limits.get("max", 0.0)), 1.0e-12)
        viewport.odbDisplay.contourOptions.setValues(
            contourType=BANDED,
            contourStyle=DISCRETE,
            numIntervals=10,
            intervalType=UNIFORM,
            spectrum=damage_spectrum_name,
            contourEdges=OFF,
            minAutoCompute=OFF,
            minValue=float(limits.get("min", 0.0)),
            maxAutoCompute=OFF,
            maxValue=maximum,
            outsideLimitsMode=SPECIFY,
            outsideLimitsBelowColor="#F2F2F2",
            outsideLimitsAboveColor="#FF0000",
        )
    elif limits:
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
    else:
        viewport.odbDisplay.contourOptions.setValues(
            contourType=BANDED,
            contourStyle=CONTINUOUS,
            numIntervals=12,
            intervalType=UNIFORM,
            spectrum="Rainbow",
            contourEdges=OFF,
            minAutoCompute=ON,
            maxAutoCompute=ON,
            outsideLimitsMode=SPECTRUM,
        )


def set_animation_limits(spec):
    """Use one observed min/max range for this ODB's complete animation."""

    limits = animation_legend_ranges.get(spec["name"], {})
    if spec["variable"] in ("DAMAGET", "DAMAGEC") and limits:
        viewport.odbDisplay.contourOptions.setValues(
            contourType=BANDED,
            contourStyle=DISCRETE,
            numIntervals=10,
            intervalType=UNIFORM,
            spectrum=damage_spectrum_name,
            contourEdges=OFF,
            minAutoCompute=OFF,
            minValue=float(limits["min"]),
            maxAutoCompute=OFF,
            maxValue=float(limits["max"]),
            outsideLimitsMode=SPECIFY,
            outsideLimitsBelowColor="#F2F2F2",
            outsideLimitsAboveColor="#FF0000",
        )
    elif limits:
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
    else:
        viewport.odbDisplay.contourOptions.setValues(
            minAutoCompute=ON,
            maxAutoCompute=ON,
        )


def set_soil_view():
    view_cut_name = "Y-Plane"
    if view_cut_name in viewport.odbDisplay.viewCuts:
        soil_cut = viewport.odbDisplay.viewCuts[view_cut_name]
    else:
        soil_cut = viewport.odbDisplay.ViewCut(
            name=view_cut_name,
            shape=PLANE,
            origin=(0.0, float(settings["soil_section_coordinate"]), 0.0),
            normal=(0.0, 1.0, 0.0),
            axis2=(0.0, 0.0, 1.0),
            followDeformation=OFF,
        )
    soil_cut.setValues(
        origin=(0.0, float(settings["soil_section_coordinate"]), 0.0),
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
    viewport.view.setViewpoint(
        viewVector=(0.0, 1.0, 0.0), cameraUpVector=(0.0, 0.0, 1.0)
    )


specs = (
    {"name": "PILE_U_MAG", "set": config["pile_display_set"], "variable": "U", "refinement": (INVARIANT, "Magnitude")},
    {"name": "PILE_CON_S_MISES", "set": config["pile_concrete_set"], "variable": "S", "refinement": (INVARIANT, "Mises")},
    {"name": "PILE_STEEL_S_MISES", "set": config.get("pile_steel_set", ""), "variable": "S", "refinement": (INVARIANT, "Mises")},
    {"name": "PILE_CON_DAMAGET", "set": config["pile_concrete_set"], "variable": "DAMAGET", "refinement": None},
    {"name": "PILE_CON_DAMAGEC", "set": config["pile_concrete_set"], "variable": "DAMAGEC", "refinement": None},
    {"name": "SOIL_PEEQ_XZ", "set": config["soil_set"], "variable": "PEEQ", "refinement": None, "soil": True},
    {"name": "SOIL_PEMAG_XZ", "set": config["soil_set"], "variable": "PEMAG", "refinement": None, "soil": True},
    {"name": "SOIL_S33_XZ", "set": config["soil_set"], "variable": "S", "refinement": (COMPONENT, "S33"), "soil": True},
    {"name": "SOIL_S_MISES_XZ", "set": config["soil_set"], "variable": "S", "refinement": (INVARIANT, "Mises"), "soil": True},
    {"name": "REBAR_LONG_S_MISES_UNDEFORMED", "variable": "S", "refinement": (INVARIANT, "Mises"), "longitudinal": True, "undeformed": True},
    {"name": "REBAR_LONG_S11_UNDEFORMED", "variable": "S", "refinement": (COMPONENT, "S11"), "longitudinal": True, "undeformed": True},
)


def render(spec):
    if not spec.get("longitudinal") and not spec.get("set"):
        return
    folder = os.path.join(frame_root, spec["name"])
    os.makedirs(folder, exist_ok=True)
    viewport.odbDisplay.setValues(viewCut=OFF)
    if spec.get("longitudinal"):
        replace_longitudinal_group()
    else:
        viewport.odbDisplay.displayGroup.replace(
            leaf=dgo.LeafFromElementSets(elementSets=(spec["set"],))
        )
    if spec.get("soil"):
        set_soil_view()
    else:
        viewport.odbDisplay.setValues(viewCut=OFF)
        viewport.view.setViewpoint(
            viewVector=tuple(float(value) for value in settings["camera_view_vector"]),
            cameraUpVector=tuple(float(value) for value in settings["camera_up_vector"]),
        )
    primary_variable(spec["variable"], spec.get("refinement"))
    state = (CONTOURS_ON_UNDEF,) if spec.get("undeformed") else (CONTOURS_ON_DEF,)
    viewport.odbDisplay.display.setValues(plotState=state)
    set_static_limits(spec)
    viewport.view.fitView()
    written = []
    last_rendered_item = None
    render_timeline = full_timeline
    for animation_index, item in enumerate(render_timeline):
        step_index = int(item["StepIndex"])
        frame_index = int(item["FrameIndex"])
        frame = list(odb.steps.values())[step_index].frames[frame_index]
        if spec["variable"] not in frame.fieldOutputs:
            continue
        viewport.odbDisplay.setFrame(step=step_index, frame=frame_index)
        set_animation_limits(spec)
        base = os.path.join(
            folder,
            "{0:04d}_{1}_F{2:04d}".format(
                animation_index, item["StepName"], frame_index
            ),
        )
        try:
            session.printToFile(fileName=base, format=PNG, canvasObjects=(viewport,))
            written.append(base + ".png")
            last_rendered_item = item
        except Exception:
            log("RENDER FAILED {0} {1}\n{2}".format(
                spec["name"], animation_index, traceback.format_exc()
            ))
    if last_rendered_item is not None:
        step_index = int(last_rendered_item["StepIndex"])
        frame_index = int(last_rendered_item["FrameIndex"])
        viewport.odbDisplay.setFrame(step=step_index, frame=frame_index)
        set_static_limits(spec)
        static_base = os.path.join(contour_dir, spec["name"] + "_LAST")
        try:
            session.printToFile(
                fileName=static_base,
                format=PNG,
                canvasObjects=(viewport,),
            )
        except Exception:
            log("STATIC RENDER FAILED {0}\n{1}".format(
                spec["name"], traceback.format_exc()
            ))
    log("RENDER {0}: {1} frames".format(spec["name"], len(written)))


for spec in specs:
    render(spec)

metadata_path = os.path.join(output_dir, "metadata.json")
if os.path.isfile(metadata_path):
    with open(metadata_path, "r", encoding="utf-8") as stream:
        metadata = json.load(stream)
else:
    metadata = {}
metadata.update(
    {
        "comparison_group": config["comparison_group"],
        "legend_ranges": legend_ranges,
        "contour_sequences": [spec["name"] for spec in specs if spec.get("set") or spec.get("longitudinal")],
        "soil_view_vector": [0.0, 1.0, 0.0],
        "soil_camera_up_vector": [0.0, 0.0, 1.0],
        "numeric_cache_source": source_output_dir,
        "render_viewport_mm": list(render_viewport_mm),
        "image_size_unit": image_size_unit,
        "requested_image_size": [image_width, image_height],
        "animation_legend_mode": "odb_full_timeline_fixed",
        "animation_legend_ranges": animation_legend_ranges,
        "static_contour_legend_mode": "comparison_group_fixed_selected_frames",
    }
)
with open(metadata_path, "w", encoding="utf-8") as stream:
    json.dump(metadata, stream, ensure_ascii=False, indent=2)
odb.close()
log("GROUP RENDER COMPLETE")
