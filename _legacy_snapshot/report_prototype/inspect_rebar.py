from odbAccess import openOdb
from abaqusConstants import INTEGRATION_POINT
import json
import math
import os


ODB_PATH = r"G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb"
OUTPUT_PATH = os.path.abspath(
    os.path.join(
        os.getcwd(),
        "abaqus_odb_prototype",
        "output_GJA-32_U20D_V20D",
        "data",
        "rebar_inventory.json",
    )
)


def scalar_stress(value):
    data = value.data
    try:
        return float(data)
    except (TypeError, ValueError):
        return float(data[0])


odb = openOdb(ODB_PATH, readOnly=True)
assembly = odb.rootAssembly
region = assembly.elementSets["SET-REBAR"]

records = []
longitudinal_keys = set()
all_keys = set()

for element_array in region.elements:
    if not element_array:
        continue
    instance_name = element_array[0].instanceName
    instance = assembly.instances[instance_name]
    node_map = {node.label: tuple(float(x) for x in node.coordinates) for node in instance.nodes}
    ratios = []
    z_values = []
    element_types = set()
    section_categories = set()
    longitudinal_count = 0
    hoop_count = 0

    for element in element_array:
        element_types.add(element.type)
        section_categories.add(element.sectionCategory.name)
        coordinates = [node_map[label] for label in element.connectivity]
        z_values.extend(point[2] for point in coordinates)
        start = coordinates[0]
        end = coordinates[-1]
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        dz = end[2] - start[2]
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        axial_ratio = abs(dz) / length if length else 0.0
        ratios.append(axial_ratio)
        key = (instance_name, element.label)
        all_keys.add(key)
        if axial_ratio >= 0.95:
            longitudinal_keys.add(key)
            longitudinal_count += 1
        else:
            hoop_count += 1

    histogram = {
        "0.00-0.10": sum(1 for value in ratios if value < 0.10),
        "0.10-0.50": sum(1 for value in ratios if 0.10 <= value < 0.50),
        "0.50-0.90": sum(1 for value in ratios if 0.50 <= value < 0.90),
        "0.90-0.95": sum(1 for value in ratios if 0.90 <= value < 0.95),
        "0.95-1.00": sum(1 for value in ratios if value >= 0.95),
    }
    records.append(
        {
            "instance": instance_name,
            "element_count": len(element_array),
            "element_types": sorted(element_types),
            "section_categories": sorted(section_categories),
            "z_min": min(z_values),
            "z_max": max(z_values),
            "longitudinal_element_count": longitudinal_count,
            "excluded_nonlongitudinal_element_count": hoop_count,
            "orientation_histogram": histogram,
        }
    )

last_step_name = list(odb.steps.keys())[-1]
last_step = odb.steps[last_step_name]
last_frame = last_step.frames[-1]
stress_values = last_frame.fieldOutputs["S"].getSubset(
    region=region, position=INTEGRATION_POINT
).values
longitudinal_stress = []
excluded_stress = []
for value in stress_values:
    key = (value.instance.name, value.elementLabel)
    stress = scalar_stress(value)
    if key in longitudinal_keys:
        longitudinal_stress.append(stress)
    elif key in all_keys:
        excluded_stress.append(stress)

payload = {
    "odb": ODB_PATH,
    "set": "SET-REBAR",
    "axis": "global Z",
    "longitudinal_threshold_abs_dz_over_length": 0.95,
    "instances": records,
    "total_elements": len(all_keys),
    "longitudinal_elements": len(longitudinal_keys),
    "excluded_nonlongitudinal_elements": len(all_keys) - len(longitudinal_keys),
    "last_frame": {
        "step": last_step_name,
        "frame": len(last_step.frames) - 1,
        "longitudinal_stress_min": min(longitudinal_stress) if longitudinal_stress else None,
        "longitudinal_stress_max": max(longitudinal_stress) if longitudinal_stress else None,
        "longitudinal_stress_value_count": len(longitudinal_stress),
        "excluded_stress_value_count": len(excluded_stress),
    },
}

with open(OUTPUT_PATH, "w", encoding="utf-8") as stream:
    json.dump(payload, stream, ensure_ascii=False, indent=2)

print(json.dumps(payload, ensure_ascii=False, indent=2))
odb.close()
