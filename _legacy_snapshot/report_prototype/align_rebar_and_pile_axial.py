import bisect
import csv
import json
import math
from collections import defaultdict
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR / "output_GJA-32_U20D_V20D"
DATA_DIR = ROOT / "data"
REBAR_DIR = ROOT / "rebar"
FREEBODY_DIR = ROOT / "freebody"

BAR_DIAMETER_MM = 32.0
BAR_AREA_MM2 = math.pi * BAR_DIAMETER_MM**2 / 4.0
REBAR_Z_MIN_MM = 7800.0
REBAR_Z_MAX_MM = 23700.0


def as_bool(value):
    return str(value).strip().lower() in {"true", "1", "yes"}


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


raw_load_rows = read_csv(DATA_DIR / "load_point_raw.csv")
timeline_rows = []
timeline_by_key = {}
for raw in raw_load_rows:
    if as_bool(raw["BoundaryDuplicate"]):
        continue
    sequence_index = len(timeline_rows)
    item = {"SequenceIndex": sequence_index, **raw}
    timeline_rows.append(item)
    timeline_by_key[(raw["StepName"], int(raw["FrameIndex"]))] = item

write_csv(DATA_DIR / "timeline_alignment.csv", list(timeline_rows[0].keys()), timeline_rows)

detailed_source = REBAR_DIR / "rebar_longitudinal_stress_force_all_frames.csv"
detailed_output = REBAR_DIR / "rebar_element_stress_force_timehistory.csv"
with detailed_source.open("r", encoding="utf-8-sig", newline="") as source_stream:
    reader = csv.DictReader(source_stream)
    source_headers = list(reader.fieldnames)
    headers = [
        name
        for name in source_headers
        if name
        not in {
            "BarArea_mm2",
            "AxialForce_TensionPositive_N",
            "AxialForce_CompressionPositive_N",
        }
    ] + [
        "S_Mises_MPa",
        "BarDiameter_mm",
        "BarArea_mm2",
        "ElementAxialForce_TensionPositive_N",
        "ElementAxialForce_CompressionPositive_N",
    ]
    detailed_count = 0
    with detailed_output.open("w", encoding="utf-8-sig", newline="") as output_stream:
        writer = csv.DictWriter(output_stream, fieldnames=headers)
        writer.writeheader()
        for row in reader:
            stress = float(row["S11_MPa"])
            output = {
                name: row[name]
                for name in source_headers
                if name
                not in {
                    "BarArea_mm2",
                    "AxialForce_TensionPositive_N",
                    "AxialForce_CompressionPositive_N",
                }
            }
            output.update(
                {
                    "S_Mises_MPa": abs(stress),
                    "BarDiameter_mm": BAR_DIAMETER_MM,
                    "BarArea_mm2": BAR_AREA_MM2,
                    "ElementAxialForce_TensionPositive_N": stress * BAR_AREA_MM2,
                    "ElementAxialForce_CompressionPositive_N": -stress * BAR_AREA_MM2,
                }
            )
            writer.writerow(output)
            detailed_count += 1

rebar_rows = read_csv(REBAR_DIR / "rebar_actual_force_depth_all_frames.csv")
rebar_by_sequence = defaultdict(list)
for row in rebar_rows:
    rebar_by_sequence[int(row["SequenceIndex"])].append(
        (
            float(row["CentroidZ_mm"]),
            float(row["SteelForce_CompressionPositive_N"]),
        )
    )
for points in rebar_by_sequence.values():
    points.sort()


def interpolate_rebar_force(sequence_index, elevation):
    if elevation < REBAR_Z_MIN_MM or elevation > REBAR_Z_MAX_MM:
        return 0.0, "outside_rebar_extent_zero"
    points = rebar_by_sequence[sequence_index]
    elevations = [point[0] for point in points]
    if elevation <= elevations[0]:
        return points[0][1], "end_element_constant"
    if elevation >= elevations[-1]:
        return points[-1][1], "end_element_constant"
    right = bisect.bisect_right(elevations, elevation)
    z0, force0 = points[right - 1]
    z1, force1 = points[right]
    ratio = (elevation - z0) / (z1 - z0)
    return force0 + ratio * (force1 - force0), "linear_between_element_centroids"


selected_concrete_files = [("LAST", FREEBODY_DIR / "axial_force_depth_LAST.csv")]
combined_rows = []
for status, path in selected_concrete_files:
    if not path.exists():
        continue
    for concrete in read_csv(path):
        step_name = concrete["StepName"]
        frame_index = int(concrete["FrameIndex"])
        timeline = timeline_by_key[(step_name, frame_index)]
        sequence_index = int(timeline["SequenceIndex"])
        elevation = float(concrete["Elevation_mm"])
        concrete_force = float(concrete["AxialForce_CompressionPositive_N"])
        rebar_force, interpolation_rule = interpolate_rebar_force(
            sequence_index, elevation
        )
        combined_rows.append(
            {
                "SequenceIndex": sequence_index,
                "StepIndex": int(timeline["StepIndex"]),
                "StepName": step_name,
                "FrameIndex": frame_index,
                "IncrementNumber": int(timeline["IncrementNumber"]),
                "StepTime": float(timeline["StepTime"]),
                "TotalTime": float(timeline["TotalTime"]),
                "Status": status,
                "CutIndex": int(concrete["CutIndex"]),
                "Elevation_mm": elevation,
                "DepthFromGround_mm": float(concrete["DepthFromGround_mm"]),
                "ConcreteAxial_CompressionPositive_N": concrete_force,
                "RebarAxial_Interpolated_CompressionPositive_N": rebar_force,
                "PileTotalAxial_CompressionPositive_N": concrete_force
                + rebar_force,
                "RebarInterpolationRule": interpolation_rule,
            }
        )

combined_path = FREEBODY_DIR / "pile_total_axial_force_time_aligned.csv"
write_csv(combined_path, list(combined_rows[0].keys()), combined_rows)

metadata = {
    "alignment_key": [
        "SequenceIndex",
        "StepName",
        "FrameIndex",
        "TotalTime",
    ],
    "timeline_points": len(timeline_rows),
    "rebar_method": "T3D2 element S11 at each field-output frame; no FreeBody slicing",
    "rebar_element_force": "S11 * pi*d^2/4 for each longitudinal element",
    "rebar_mises": "abs(S11) for uniaxial T3D2",
    "odb_detected_bar_count": 32,
    "bar_diameter_mm": BAR_DIAMETER_MM,
    "bar_area_mm2": BAR_AREA_MM2,
    "detailed_rebar_rows": detailed_count,
    "concrete_method": "Abaqus XY-plane FreeBodyCut on SET-PILE_CON",
    "concrete_target_frames": [item[0] for item in selected_concrete_files],
    "combined_rows": len(combined_rows),
    "interpolation": {
        "inside_centroid_range": "linear in global Z between adjacent element-centroid resultants",
        "between_bar_end_and_end_centroid": "constant end-element resultant",
        "outside_bar_extent": "zero",
        "rebar_z_extent_mm": [REBAR_Z_MIN_MM, REBAR_Z_MAX_MM],
    },
    "force_sign": "compression positive; total = concrete + interpolated rebar",
}
with (FREEBODY_DIR / "pile_total_axial_force_time_aligned_metadata.json").open(
    "w", encoding="utf-8"
) as stream:
    json.dump(metadata, stream, ensure_ascii=False, indent=2)

print(json.dumps(metadata, ensure_ascii=False, indent=2))
