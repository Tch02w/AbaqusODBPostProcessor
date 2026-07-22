from odbAccess import openOdb
from abaqusConstants import INTEGRATION_POINT
import csv
import json
import math
import os


BASE_DIR = os.path.abspath(os.path.join(os.getcwd(), "abaqus_odb_prototype"))
CONFIG_PATH = os.path.join(BASE_DIR, "rebar_config.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output_GJA-32_U20D_V20D", "rebar")
os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(CONFIG_PATH, "r", encoding="utf-8") as stream:
    config = json.load(stream)

odb_path = config["odb_path"]
instance_name = config["longitudinal_instance"]
start_step_name = config["start_step"]
end_step_name = config["end_step"]
orientation_threshold = float(config["longitudinal_orientation_threshold"])
ground_z = float(config["ground_elevation_model_z_mm"])

bar_area = config.get("bar_area_mm2")
bar_diameter = config.get("bar_diameter_mm")
if bar_area is None and bar_diameter is not None:
    bar_area = math.pi * float(bar_diameter) ** 2 / 4.0
if bar_area is not None:
    bar_area = float(bar_area)


def scalar_stress(value):
    data = value.data
    try:
        return float(data)
    except (TypeError, ValueError):
        return float(data[0])


def write_csv(path, headers, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


odb = openOdb(odb_path, readOnly=True)
assembly = odb.rootAssembly
instance = assembly.instances[instance_name]
node_map = {node.label: tuple(float(x) for x in node.coordinates) for node in instance.nodes}

parent = {}


def find(node):
    parent.setdefault(node, node)
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = parent[node]
    return node


def union(first, second):
    root_first = find(first)
    root_second = find(second)
    if root_first != root_second:
        parent[root_second] = root_first


element_geometry = {}
excluded_by_orientation = []
for element in instance.elements:
    coordinates = [node_map[label] for label in element.connectivity]
    start = coordinates[0]
    end = coordinates[-1]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dz = end[2] - start[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    ratio = abs(dz) / length if length else 0.0
    if ratio < orientation_threshold:
        excluded_by_orientation.append(element.label)
        continue
    union(element.connectivity[0], element.connectivity[-1])
    centroid = tuple(
        sum(point[axis] for point in coordinates) / float(len(coordinates))
        for axis in range(3)
    )
    element_geometry[element.label] = {
        "node_labels": tuple(element.connectivity),
        "centroid": centroid,
        "length": length,
        "orientation_ratio": ratio,
    }

component_nodes = {}
for geometry in element_geometry.values():
    for node_label in geometry["node_labels"]:
        component_nodes.setdefault(find(node_label), set()).add(node_label)

component_info = []
for root, node_labels in component_nodes.items():
    coordinates = [node_map[label] for label in node_labels]
    x = sum(point[0] for point in coordinates) / float(len(coordinates))
    y = sum(point[1] for point in coordinates) / float(len(coordinates))
    z_values = [point[2] for point in coordinates]
    component_info.append(
        {
            "root": root,
            "x": x,
            "y": y,
            "angle": math.atan2(y, x),
            "radius": math.hypot(x, y),
            "z_min": min(z_values),
            "z_max": max(z_values),
            "node_count": len(node_labels),
        }
    )

component_info.sort(key=lambda item: item["angle"])
root_to_bar = {}
for bar_index, item in enumerate(component_info, 1):
    item["bar_id"] = bar_index
    root_to_bar[item["root"]] = bar_index

element_to_bar = {}
for element_label, geometry in element_geometry.items():
    root = find(geometry["node_labels"][0])
    element_to_bar[element_label] = root_to_bar[root]

step_names = list(odb.steps.keys())
start_step_index = step_names.index(start_step_name)
end_step_index = step_names.index(end_step_name)
time_offsets = {}
running_total = 0.0
for step_name in step_names:
    time_offsets[step_name] = running_total
    frames = odb.steps[step_name].frames
    if frames:
        running_total += float(frames[-1].frameValue)

detailed_path = os.path.join(OUTPUT_DIR, "rebar_longitudinal_stress_force_all_frames.csv")
detailed_headers = [
    "SequenceIndex",
    "StepIndex",
    "StepName",
    "FrameIndex",
    "IncrementNumber",
    "StepTime",
    "TotalTime",
    "BarID",
    "ElementLabel",
    "CentroidX_mm",
    "CentroidY_mm",
    "CentroidZ_mm",
    "DepthFromGround_mm",
    "ElementLength_mm",
    "AbsDzOverLength",
    "S11_MPa",
    "BarArea_mm2",
    "AxialForce_TensionPositive_N",
    "AxialForce_CompressionPositive_N",
]

depth_summary_rows = []
last_frame_bar_values = {}
sequence_index = 0
with open(detailed_path, "w", newline="", encoding="utf-8-sig") as stream:
    writer = csv.DictWriter(stream, fieldnames=detailed_headers)
    writer.writeheader()
    for step_index in range(start_step_index, end_step_index + 1):
        step_name = step_names[step_index]
        step = odb.steps[step_name]
        for frame_index, frame in enumerate(step.frames):
            if step_index > start_step_index and frame_index == 0:
                continue
            stress_values = frame.fieldOutputs["S"].getSubset(
                region=instance, position=INTEGRATION_POINT
            ).values
            by_depth = {}
            for value in stress_values:
                element_label = value.elementLabel
                if element_label not in element_geometry:
                    continue
                stress = scalar_stress(value)
                geometry = element_geometry[element_label]
                x, y, z = geometry["centroid"]
                force_tension = stress * bar_area if bar_area is not None else None
                force_compression = -force_tension if force_tension is not None else None
                writer.writerow(
                    {
                        "SequenceIndex": sequence_index,
                        "StepIndex": step_index,
                        "StepName": step_name,
                        "FrameIndex": frame_index,
                        "IncrementNumber": int(frame.incrementNumber),
                        "StepTime": float(frame.frameValue),
                        "TotalTime": time_offsets[step_name] + float(frame.frameValue),
                        "BarID": element_to_bar[element_label],
                        "ElementLabel": element_label,
                        "CentroidX_mm": x,
                        "CentroidY_mm": y,
                        "CentroidZ_mm": z,
                        "DepthFromGround_mm": ground_z - z,
                        "ElementLength_mm": geometry["length"],
                        "AbsDzOverLength": geometry["orientation_ratio"],
                        "S11_MPa": stress,
                        "BarArea_mm2": bar_area,
                        "AxialForce_TensionPositive_N": force_tension,
                        "AxialForce_CompressionPositive_N": force_compression,
                    }
                )
                z_key = round(z, 6)
                by_depth.setdefault(z_key, []).append(stress)
                if step_index == end_step_index and frame_index == len(step.frames) - 1:
                    last_frame_bar_values.setdefault(element_to_bar[element_label], []).append(stress)

            for z, stresses in sorted(by_depth.items()):
                sum_stress = sum(stresses)
                depth_summary_rows.append(
                    {
                        "SequenceIndex": sequence_index,
                        "StepIndex": step_index,
                        "StepName": step_name,
                        "FrameIndex": frame_index,
                        "IncrementNumber": int(frame.incrementNumber),
                        "StepTime": float(frame.frameValue),
                        "TotalTime": time_offsets[step_name] + float(frame.frameValue),
                        "CentroidZ_mm": z,
                        "DepthFromGround_mm": ground_z - z,
                        "CrossingBarCount": len(stresses),
                        "S11_Min_MPa": min(stresses),
                        "S11_Mean_MPa": sum_stress / float(len(stresses)),
                        "S11_Max_MPa": max(stresses),
                        "SumS11_MPa": sum_stress,
                        "BarArea_mm2": bar_area,
                        "TotalSteelForce_TensionPositive_N": sum_stress * bar_area
                        if bar_area is not None
                        else None,
                        "TotalSteelForce_CompressionPositive_N": -sum_stress * bar_area
                        if bar_area is not None
                        else None,
                    }
                )
            sequence_index += 1

depth_headers = list(depth_summary_rows[0].keys())
write_csv(
    os.path.join(OUTPUT_DIR, "rebar_stress_force_depth_summary.csv"),
    depth_headers,
    depth_summary_rows,
)

bar_summary_rows = []
for item in component_info:
    stresses = last_frame_bar_values.get(item["bar_id"], [])
    max_abs_stress = max(stresses, key=abs) if stresses else None
    bar_summary_rows.append(
        {
            "BarID": item["bar_id"],
            "X_mm": item["x"],
            "Y_mm": item["y"],
            "Radius_mm": item["radius"],
            "Angle_rad": item["angle"],
            "Z_Min_mm": item["z_min"],
            "Z_Max_mm": item["z_max"],
            "NodeCount": item["node_count"],
            "LastFrame_S11_Min_MPa": min(stresses) if stresses else None,
            "LastFrame_S11_Max_MPa": max(stresses) if stresses else None,
            "LastFrame_S11_MaxAbs_MPa": max_abs_stress,
            "BarArea_mm2": bar_area,
            "LastFrame_MaxAbsForce_N": max_abs_stress * bar_area
            if max_abs_stress is not None and bar_area is not None
            else None,
        }
    )
write_csv(
    os.path.join(OUTPUT_DIR, "rebar_bar_summary_last_frame.csv"),
    list(bar_summary_rows[0].keys()),
    bar_summary_rows,
)

metadata = {
    "odb_path": odb_path,
    "longitudinal_instance": instance_name,
    "excluded_stirrup_instance": config["excluded_stirrup_instance"],
    "longitudinal_bar_count": len(component_info),
    "longitudinal_element_count": len(element_geometry),
    "excluded_by_orientation_count": len(excluded_by_orientation),
    "bar_diameter_mm": bar_diameter,
    "bar_area_mm2": bar_area,
    "force_calculated": bar_area is not None,
    "stress_sign": "positive=tension, negative=compression",
    "force_columns": {
        "tension_positive": "S11 * bar_area",
        "compression_positive": "-(S11 * bar_area)",
    },
    "bars": component_info,
}
with open(os.path.join(OUTPUT_DIR, "rebar_metadata.json"), "w", encoding="utf-8") as stream:
    json.dump(metadata, stream, ensure_ascii=False, indent=2)

print(json.dumps(metadata, ensure_ascii=False, indent=2))
odb.close()
