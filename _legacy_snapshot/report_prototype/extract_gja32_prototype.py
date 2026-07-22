from abaqus import session
from abaqusConstants import *
import csv
import json
import math
import os
import shutil
import traceback
import visualization
import displayGroupOdbToolset as dgo


ODB_PATH = r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb"
MODEL_NAME = "GJA-32_U20D_V20D"
START_STEP = "U10D"
END_STEP = "V20D"
LOAD_SET = "SET-LOAD"
PILE_SET = "SET-PILE"
PILE_CONCRETE_SET = "SET-PILE_CON"
SOIL_SET = "SET-SOIL_FULL"
EXPOSED_HEAD_MM = 500.0
DAMAGE_THRESHOLD = 0.90
ANGULAR_COVERAGE_THRESHOLD = 0.90
AXIAL_CUT_COUNT = 100
IMAGE_SIZE = (800, 600)

BASE_DIR = os.path.abspath(os.path.join(os.getcwd(), "abaqus_odb_prototype"))
OUTPUT_DIR = os.path.join(BASE_DIR, "output_GJA-32_U20D_V20D")
DATA_DIR = os.path.join(OUTPUT_DIR, "data")
FRAME_ROOT = os.path.join(OUTPUT_DIR, "frames")
CONTOUR_DIR = os.path.join(OUTPUT_DIR, "contours")
FREEBODY_DIR = os.path.join(OUTPUT_DIR, "freebody")
LOG_PATH = os.path.join(OUTPUT_DIR, "prototype_run.log")

for directory in (OUTPUT_DIR, DATA_DIR, FRAME_ROOT, CONTOUR_DIR, FREEBODY_DIR):
    os.makedirs(directory, exist_ok=True)


def log(message):
    text = str(message)
    print(text)
    with open(LOG_PATH, "a", encoding="utf-8") as stream:
        stream.write(text + "\n")


def flatten_arrays(arrays):
    for array in arrays:
        for item in array:
            yield item


def as_float(value):
    data = value.data
    try:
        return float(data)
    except (TypeError, ValueError):
        return max(float(component) for component in data)


def vector_from_field(frame, field_name, region):
    if field_name not in frame.fieldOutputs:
        return (None, None, None)
    values = frame.fieldOutputs[field_name].getSubset(region=region).values
    if not values:
        return (None, None, None)
    data = values[0].data
    result = [float(data[index]) if index < len(data) else None for index in range(3)]
    return tuple(result)


def write_csv(path, headers, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, payload):
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def set_display_group(viewport, element_set_names):
    leaf = dgo.LeafFromElementSets(elementSets=tuple(element_set_names))
    viewport.odbDisplay.displayGroup.replace(leaf=leaf)


def set_primary_variable(viewport, variable, refinement=None):
    kwargs = {
        "variableLabel": variable,
        "outputPosition": NODAL if variable == "U" else INTEGRATION_POINT,
    }
    if refinement is not None:
        kwargs["refinement"] = refinement
    viewport.odbDisplay.setPrimaryVariable(**kwargs)


def build_element_centroids(odb, region):
    assembly = odb.rootAssembly
    elements_by_instance = {}
    for element in flatten_arrays(region.elements):
        elements_by_instance.setdefault(element.instanceName, []).append(element)

    centroids = {}
    for instance_name, elements in elements_by_instance.items():
        instance = assembly.instances[instance_name]
        needed_labels = set()
        for element in elements:
            needed_labels.update(element.connectivity)
        node_coordinates = {
            node.label: tuple(float(x) for x in node.coordinates)
            for node in instance.nodes
            if node.label in needed_labels
        }
        for element in elements:
            coords = [node_coordinates[label] for label in element.connectivity]
            count = float(len(coords))
            x = sum(point[0] for point in coords) / count
            y = sum(point[1] for point in coords) / count
            z = sum(point[2] for point in coords) / count
            centroids[(instance_name, element.label)] = (x, y, z, math.hypot(x, y))
    return centroids


def damage_by_element(frame, variable, region):
    if variable not in frame.fieldOutputs:
        return {}
    field = frame.fieldOutputs[variable]
    try:
        values = field.getSubset(region=region, position=CENTROID).values
    except Exception:
        values = field.getSubset(region=region).values
    result = {}
    for value in values:
        key = (value.instance.name, value.elementLabel)
        scalar = as_float(value)
        if key not in result or scalar > result[key]:
            result[key] = scalar
    return result


def scan_damage(sequence, region, centroids):
    radii = sorted(point[3] for point in centroids.values())
    radial_reference = radii[int(round(0.95 * (len(radii) - 1)))]
    outer_radius = 0.75 * radial_reference
    z_values = [point[2] for point in centroids.values()]
    z_min = min(z_values)
    z_max = max(z_values)
    z_bin_width = (z_max - z_min) / 100.0
    angle_bin_count = 36

    rows = []
    first_candidate = None
    best_record = None
    for sequence_index, item in enumerate(sequence):
        frame = item["frame_object"]
        tensile = damage_by_element(frame, "DAMAGET", region)
        compressive = damage_by_element(frame, "DAMAGEC", region)
        max_tensile = max(tensile.values()) if tensile else None
        max_compressive = max(compressive.values()) if compressive else None

        occupied = {}
        severe_count = 0
        for key, value in tensile.items():
            if value < DAMAGE_THRESHOLD or key not in centroids:
                continue
            x, y, z, radius = centroids[key]
            if radius < outer_radius:
                continue
            severe_count += 1
            z_index = min(99, max(0, int((z - z_min) / z_bin_width)))
            angle = math.atan2(y, x)
            angle_index = int(((angle + math.pi) / (2.0 * math.pi)) * angle_bin_count)
            angle_index = min(angle_bin_count - 1, max(0, angle_index))
            occupied.setdefault(z_index, set()).add(angle_index)

        if occupied:
            best_z_bin, best_bins = max(occupied.items(), key=lambda pair: len(pair[1]))
            coverage = len(best_bins) / float(angle_bin_count)
            candidate_z = z_min + (best_z_bin + 0.5) * z_bin_width
        else:
            coverage = 0.0
            candidate_z = None

        is_candidate = coverage >= ANGULAR_COVERAGE_THRESHOLD
        record = {
            "SequenceIndex": sequence_index,
            "StepIndex": item["step_index"],
            "StepName": item["step_name"],
            "FrameIndex": item["frame_index"],
            "IncrementNumber": item["increment_number"],
            "StepTime": item["step_time"],
            "TotalTime": item["total_time"],
            "MaxDAMAGET": max_tensile,
            "MaxDAMAGEC": max_compressive,
            "SevereOuterElementCount": severe_count,
            "MaxAngularCoverage": coverage,
            "CandidateElevation": candidate_z,
            "Candidate": is_candidate,
        }
        rows.append(record)
        if first_candidate is None and is_candidate:
            first_candidate = item
        if best_record is None or coverage > best_record[0]:
            best_record = (coverage, item)
        log(
            "DAMAGE_SCAN {0}/{1} {2} frame {3}: maxT={4} coverage={5:.3f}".format(
                sequence_index + 1,
                len(sequence),
                item["step_name"],
                item["frame_index"],
                max_tensile,
                coverage,
            )
        )

    return rows, first_candidate, best_record[1] if best_record else None


def render_animation_frames(viewport, sequence, spec):
    animation_dir = os.path.join(FRAME_ROOT, spec["name"])
    os.makedirs(animation_dir, exist_ok=True)
    set_display_group(viewport, spec["sets"])
    viewport.odbDisplay.setValues(viewCut=OFF)

    if spec.get("soil_cut"):
        cut_name = "SOIL_XZ_Y0"
        if cut_name not in viewport.odbDisplay.viewCuts:
            cut = viewport.odbDisplay.ViewCut(
                name=cut_name,
                shape=PLANE,
                origin=(0.0, 0.0, 15750.0),
                normal=(0.0, 1.0, 0.0),
                axis2=(0.0, 0.0, 1.0),
                followDeformation=OFF,
            )
            cut.setValues(
                showModelAboveCut=OFF,
                showModelBelowCut=OFF,
                showModelOnCut=ON,
                showFreeBodyCut=OFF,
            )
        viewport.odbDisplay.setValues(viewCut=ON, viewCutNames=(cut_name,))
        viewport.view.setViewpoint(viewVector=(0.0, -1.0, 0.0), cameraUpVector=(0.0, 0.0, 1.0))
    else:
        viewport.view.setViewpoint(viewVector=(1.0, 1.0, 0.5), cameraUpVector=(0.0, 0.0, 1.0))

    set_primary_variable(viewport, spec["variable"], spec.get("refinement"))
    viewport.odbDisplay.display.setValues(plotState=(CONTOURS_ON_DEF,))
    viewport.odbDisplay.contourOptions.setValues(minAutoCompute=ON, maxAutoCompute=ON)
    viewport.view.fitView()

    written = []
    for animation_index, item in enumerate(sequence):
        frame = item["frame_object"]
        if spec["variable"] not in frame.fieldOutputs:
            log(
                "RENDER_SKIP {0} {1} frame {2}: field missing".format(
                    spec["name"], item["step_name"], item["frame_index"]
                )
            )
            continue
        viewport.odbDisplay.setFrame(step=item["step_index"], frame=item["frame_index"])
        file_base = os.path.join(
            animation_dir,
            "{0:04d}_{1}_F{2:04d}".format(
                animation_index, item["step_name"], item["frame_index"]
            ),
        )
        try:
            session.printToFile(fileName=file_base, format=PNG, canvasObjects=(viewport,))
            written.append(file_base + ".png")
        except Exception as exc:
            log(
                "RENDER_FAILED {0} {1} frame {2}: {3}".format(
                    spec["name"], item["step_name"], item["frame_index"], repr(exc)
                )
            )
        if animation_index % 5 == 0 or animation_index == len(sequence) - 1:
            log(
                "RENDER {0}: {1}/{2}".format(
                    spec["name"], animation_index + 1, len(sequence)
                )
            )

    if written:
        final_path = os.path.join(CONTOUR_DIR, spec["name"] + "_LAST.png")
        shutil.copyfile(written[-1], final_path)
    return written


def convert_freebody_report(raw_path, clean_path, ground_z, label):
    rows = []
    with open(raw_path, "r", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        for index, raw in enumerate(reader):
            z = float(raw["CutZ"])
            fz = float(raw["Fz"])
            rows.append(
                {
                    "CutIndex": index + 1,
                    "CutName": raw["CutName"].strip(),
                    "Elevation_mm": z,
                    "DepthFromGround_mm": ground_z - z,
                    "AxialForce_CompressionPositive_N": fz,
                    "Fx_N": float(raw["Fx"]),
                    "Fy_N": float(raw["Fy"]),
                    "Fz_N": fz,
                    "Mx_Nmm": float(raw["Mx"]),
                    "My_Nmm": float(raw["My"]),
                    "Mz_Nmm": float(raw["Mz"]),
                    "StepName": raw["StepName"].strip(),
                    "FrameIndex": int(raw["FrameId"]),
                    "TotalTime": float(raw["Time"]),
                    "Region": PILE_CONCRETE_SET,
                    "Status": label,
                }
            )
    headers = list(rows[0].keys()) if rows else []
    if headers:
        write_csv(clean_path, headers, rows)
    return len(rows)


def write_freebody(viewport, odb, frame_item, pile_bbox, ground_z, label):
    viewport.makeCurrent()
    viewport.setValues(displayedObject=odb)
    viewport.odbDisplay.setFrame(
        step=frame_item["step_index"], frame=frame_item["frame_index"]
    )
    viewport.odbDisplay.display.setValues(plotState=(UNDEFORMED,))
    set_display_group(viewport, (PILE_CONCRETE_SET,))

    z_min = pile_bbox[2][0]
    z_max = pile_bbox[2][1]
    z_mid = 0.5 * (z_min + z_max)
    cut_name = "PILE_CON_XY_100_{0}".format(label)
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
    epsilon = max(1.0, (z_max - z_min) / 1600.0)
    viewport.odbDisplay.viewCutOptions.setValues(
        displaySlicing=ON,
        freeBodyCutThru=CURRENT_DISPLAY_GROUP,
        freeBodyStepThru=ACTIVE_CUT_RANGE,
        numCutFreeBody=AXIAL_CUT_COUNT,
        cutFreeBodyMin=cut.cutRange[0] + epsilon,
        cutFreeBodyMax=cut.cutRange[1] - epsilon,
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
    raw_path = os.path.join(FREEBODY_DIR, "freebody_{0}_raw.csv".format(label))
    session.writeFreeBodyReport(
        fileName=raw_path,
        append=OFF,
        step=frame_item["step_index"],
        frame=frame_item["frame_index"],
        stepFrame=SPECIFY,
        odb=odb,
    )
    clean_path = os.path.join(FREEBODY_DIR, "axial_force_depth_{0}.csv".format(label))
    row_count = convert_freebody_report(raw_path, clean_path, ground_z, label)
    log("FREEBODY {0}: {1} cuts written".format(label, row_count))
    return clean_path, row_count


def main():
    with open(LOG_PATH, "w", encoding="utf-8") as stream:
        stream.write("Prototype extraction started\n")

    viewport = session.Viewport(name="GJA32 Prototype", origin=(0, 0), width=180, height=120)
    viewport.makeCurrent()
    odb = session.openOdb(name=ODB_PATH, readOnly=True)
    viewport.setValues(displayedObject=odb)
    session.pngOptions.setValues(imageSize=IMAGE_SIZE)
    viewport.viewportAnnotationOptions.setValues(
        triad=ON,
        legend=ON,
        title=OFF,
        state=ON,
        annotations=OFF,
        compass=OFF,
    )

    assembly = odb.rootAssembly
    step_names = list(odb.steps.keys())
    start_index = step_names.index(START_STEP)
    end_index = step_names.index(END_STEP)
    selected_step_names = step_names[start_index : end_index + 1]
    load_region = assembly.nodeSets[LOAD_SET]
    pile_region = assembly.nodeSets[PILE_SET]
    pile_nodes = list(flatten_arrays(pile_region.nodes))
    pile_bbox = tuple(
        (
            min(float(node.coordinates[axis]) for node in pile_nodes),
            max(float(node.coordinates[axis]) for node in pile_nodes),
        )
        for axis in range(3)
    )
    ground_z = pile_bbox[2][1] - EXPOSED_HEAD_MM

    time_offsets = {}
    running_total = 0.0
    for step_name in step_names:
        time_offsets[step_name] = running_total
        frames = odb.steps[step_name].frames
        if frames:
            running_total += float(frames[-1].frameValue)

    raw_rows = []
    animation_sequence = []
    selected_offset = time_offsets[START_STEP]
    step_directions = {}
    for step_index in range(start_index, end_index + 1):
        step_name = step_names[step_index]
        step = odb.steps[step_name]
        first_u = vector_from_field(step.frames[0], "U", load_region)
        last_u = vector_from_field(step.frames[-1], "U", load_region)
        delta_u1 = (last_u[0] or 0.0) - (first_u[0] or 0.0)
        delta_u3 = (last_u[2] or 0.0) - (first_u[2] or 0.0)
        dominant = 1 if abs(delta_u1) > abs(delta_u3) else 3
        step_directions[step_name] = dominant

        for frame_index, frame in enumerate(step.frames):
            u = vector_from_field(frame, "U", load_region)
            ur = vector_from_field(frame, "UR", load_region)
            rf = vector_from_field(frame, "RF", load_region)
            rm = vector_from_field(frame, "RM", load_region)
            total_time = time_offsets[step_name] + float(frame.frameValue)
            is_duplicate = step_index > start_index and frame_index == 0
            item = {
                "step_index": step_index,
                "step_name": step_name,
                "frame_index": frame_index,
                "increment_number": int(frame.incrementNumber),
                "step_time": float(frame.frameValue),
                "total_time": total_time,
                "sequence_time": total_time - selected_offset,
                "frame_object": frame,
            }
            if not is_duplicate:
                animation_sequence.append(item)
            raw_rows.append(
                {
                    "StepIndex": step_index,
                    "StepName": step_name,
                    "FrameIndex": frame_index,
                    "IncrementNumber": int(frame.incrementNumber),
                    "StepTime": float(frame.frameValue),
                    "TotalTime": total_time,
                    "SequenceTime": total_time - selected_offset,
                    "BoundaryDuplicate": is_duplicate,
                    "DominantDirection": dominant,
                    "U1_mm": u[0],
                    "U2_mm": u[1],
                    "U3_mm": u[2],
                    "UR1_rad": ur[0],
                    "UR2_rad": ur[1],
                    "UR3_rad": ur[2],
                    "RF1_N": rf[0],
                    "RF2_N": rf[1],
                    "RF3_N": rf[2],
                    "RM1_Nmm": rm[0],
                    "RM2_Nmm": rm[1],
                    "RM3_Nmm": rm[2],
                }
            )

    raw_headers = list(raw_rows[0].keys())
    raw_csv = os.path.join(DATA_DIR, "load_point_raw.csv")
    write_csv(raw_csv, raw_headers, raw_rows)
    for direction in (1, 3):
        component_rows = [
            row
            for row in raw_rows
            if row["DominantDirection"] == direction and not row["BoundaryDuplicate"]
        ]
        component_csv = os.path.join(DATA_DIR, "load_displacement_dir{0}.csv".format(direction))
        write_csv(component_csv, raw_headers, component_rows)
    log("LOAD_DATA: {0} rows".format(len(raw_rows)))

    concrete_region = assembly.elementSets[PILE_CONCRETE_SET]
    log("Building concrete element centroids")
    centroids = build_element_centroids(odb, concrete_region)
    log("Concrete centroids: {0}".format(len(centroids)))
    damage_rows, first_candidate, best_candidate = scan_damage(
        animation_sequence, concrete_region, centroids
    )
    damage_csv = os.path.join(DATA_DIR, "damage_ring_scan.csv")
    write_csv(damage_csv, list(damage_rows[0].keys()), damage_rows)

    prefracture = None
    if first_candidate is not None:
        candidate_index = animation_sequence.index(first_candidate)
        if candidate_index > 0:
            prefracture = animation_sequence[candidate_index - 1]

    specs = (
        {
            "name": "PILE_U_MAG",
            "sets": (PILE_SET,),
            "variable": "U",
            "refinement": (INVARIANT, "Magnitude"),
        },
        {
            "name": "PILE_S_MISES",
            "sets": (PILE_SET,),
            "variable": "S",
            "refinement": (INVARIANT, "Mises"),
        },
        {
            "name": "PILE_CON_DAMAGET",
            "sets": (PILE_CONCRETE_SET,),
            "variable": "DAMAGET",
        },
        {
            "name": "PILE_CON_DAMAGEC",
            "sets": (PILE_CONCRETE_SET,),
            "variable": "DAMAGEC",
        },
        {
            "name": "SOIL_PEMAG_XZ",
            "sets": (SOIL_SET,),
            "variable": "PEMAG",
            "soil_cut": True,
        },
        {
            "name": "SOIL_PEEQ_XZ",
            "sets": (SOIL_SET,),
            "variable": "PEEQ",
            "soil_cut": True,
        },
        {
            "name": "SOIL_S33_XZ",
            "sets": (SOIL_SET,),
            "variable": "S",
            "refinement": (COMPONENT, "S33"),
            "soil_cut": True,
        },
        {
            "name": "SOIL_S_MISES_XZ",
            "sets": (SOIL_SET,),
            "variable": "S",
            "refinement": (INVARIANT, "Mises"),
            "soil_cut": True,
        },
    )

    render_manifest = {}
    for spec in specs:
        try:
            log("RENDER_START " + spec["name"])
            render_manifest[spec["name"]] = render_animation_frames(
                viewport, animation_sequence, spec
            )
        except Exception as exc:
            log("RENDER_SPEC_FAILED {0}: {1}".format(spec["name"], repr(exc)))
            log(traceback.format_exc())
            render_manifest[spec["name"]] = []

    freebody_results = []
    final_item = animation_sequence[-1]
    for label, item in (("LAST", final_item), ("PRE_FRACTURE_AUTO", prefracture)):
        if item is None:
            continue
        try:
            path, count = write_freebody(
                viewport, odb, item, pile_bbox, ground_z, label
            )
            freebody_results.append({"label": label, "path": path, "count": count})
        except Exception as exc:
            log("FREEBODY_FAILED {0}: {1}".format(label, repr(exc)))
            log(traceback.format_exc())

    load_nodes = list(flatten_arrays(load_region.nodes))
    metadata = {
        "odb_path": ODB_PATH,
        "model_name": MODEL_NAME,
        "step_order": step_names,
        "selected_steps": selected_step_names,
        "start_step": START_STEP,
        "end_step": END_STEP,
        "step_dominant_directions": step_directions,
        "assembly_node_sets": sorted(assembly.nodeSets.keys()),
        "assembly_element_sets": sorted(assembly.elementSets.keys()),
        "load_set": LOAD_SET,
        "load_node_count": len(load_nodes),
        "load_nodes": [
            {
                "instance": node.instanceName,
                "label": node.label,
                "coordinates": [float(x) for x in node.coordinates],
            }
            for node in load_nodes
        ],
        "pile_set": PILE_SET,
        "pile_concrete_set": PILE_CONCRETE_SET,
        "soil_set": SOIL_SET,
        "pile_bbox": pile_bbox,
        "ground_elevation_model_z_mm": ground_z,
        "reported_pile_head_depth_mm": -EXPOSED_HEAD_MM,
        "animation_frame_count": len(animation_sequence),
        "legend_range_mode": "AUTO_PER_FRAME_PROTOTYPE",
        "damage_threshold": DAMAGE_THRESHOLD,
        "angular_coverage_threshold": ANGULAR_COVERAGE_THRESHOLD,
        "first_auto_fracture_candidate": None
        if first_candidate is None
        else {
            "step": first_candidate["step_name"],
            "frame": first_candidate["frame_index"],
            "total_time": first_candidate["total_time"],
        },
        "best_damage_candidate": None
        if best_candidate is None
        else {
            "step": best_candidate["step_name"],
            "frame": best_candidate["frame_index"],
            "total_time": best_candidate["total_time"],
        },
        "prefracture_frame_used": None
        if prefracture is None
        else {
            "step": prefracture["step_name"],
            "frame": prefracture["frame_index"],
            "total_time": prefracture["total_time"],
        },
        "rendered_frames": {
            key: len(value) for key, value in render_manifest.items()
        },
        "freebody_results": freebody_results,
        "limitations": [
            "SET-PILE contains embedded truss elements; Abaqus 2025 rejects direct Free Body computation on that display group.",
            "Prototype Free Body reports therefore use SET-PILE_CON and represent concrete contribution only.",
            "Legend bounds are automatic in this prototype because manual comparison-group limits were not supplied.",
            "Automatic fracture candidate must be confirmed manually from DAMAGET/DAMAGEC animation.",
        ],
    }
    write_json(os.path.join(OUTPUT_DIR, "metadata.json"), metadata)
    log("METADATA_WRITTEN")
    odb.close()
    log("Prototype extraction completed")


try:
    main()
except Exception as exc:
    log("FATAL: " + repr(exc))
    log(traceback.format_exc())
    raise
