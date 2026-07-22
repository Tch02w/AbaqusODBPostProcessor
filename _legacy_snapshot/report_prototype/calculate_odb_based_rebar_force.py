import csv
import json
import math
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output_GJA-32_U20D_V20D"
REBAR_DIR = OUTPUT_DIR / "rebar"
DIAMETER_MM = 32.0
CATALOG_COUNT_REFERENCE = 30


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


with (REBAR_DIR / "rebar_metadata.json").open("r", encoding="utf-8") as stream:
    source_metadata = json.load(stream)

odb_bar_count = int(source_metadata["longitudinal_bar_count"])
single_bar_area_mm2 = math.pi * DIAMETER_MM**2 / 4.0
odb_total_area_mm2 = single_bar_area_mm2 * odb_bar_count

source_rows = read_csv(REBAR_DIR / "rebar_stress_force_depth_summary.csv")
force_rows = []
for row in source_rows:
    sum_s11 = float(row["SumS11_MPa"])
    tension_positive_force = sum_s11 * single_bar_area_mm2
    force_rows.append(
        {
            "SequenceIndex": int(row["SequenceIndex"]),
            "StepIndex": int(row["StepIndex"]),
            "StepName": row["StepName"],
            "FrameIndex": int(row["FrameIndex"]),
            "IncrementNumber": int(row["IncrementNumber"]),
            "StepTime": float(row["StepTime"]),
            "TotalTime": float(row["TotalTime"]),
            "CentroidZ_mm": float(row["CentroidZ_mm"]),
            "DepthFromGround_mm": float(row["DepthFromGround_mm"]),
            "ODBDetectedBarCount": odb_bar_count,
            "CatalogBarCount_ReferenceOnly": CATALOG_COUNT_REFERENCE,
            "MainBarDiameter_mm": DIAMETER_MM,
            "SingleBarArea_mm2": single_bar_area_mm2,
            "ODBTotalSteelArea_mm2": odb_total_area_mm2,
            "S11_Min_MPa": float(row["S11_Min_MPa"]),
            "S11_Mean_MPa": float(row["S11_Mean_MPa"]),
            "S11_Max_MPa": float(row["S11_Max_MPa"]),
            "S_Mises_Max_MPa": max(
                abs(float(row["S11_Min_MPa"])), abs(float(row["S11_Max_MPa"]))
            ),
            "SumS11_MPa": sum_s11,
            "SteelForce_TensionPositive_N": tension_positive_force,
            "SteelForce_CompressionPositive_N": -tension_positive_force,
        }
    )

headers = list(force_rows[0].keys())
write_csv(REBAR_DIR / "rebar_actual_force_depth_all_frames.csv", headers, force_rows)

last_step = force_rows[-1]["StepName"]
last_frame = force_rows[-1]["FrameIndex"]
last_rows = [
    row
    for row in force_rows
    if row["StepName"] == last_step and row["FrameIndex"] == last_frame
]
write_csv(REBAR_DIR / "rebar_actual_force_depth_LAST.csv", headers, last_rows)

metadata = {
    "model_name": "GJA-32_U20D_V20D",
    "bar_count_basis": "ODB detected longitudinal chains",
    "odb_detected_bar_count": odb_bar_count,
    "catalog_bar_count_reference_only": CATALOG_COUNT_REFERENCE,
    "bar_count_scale_factor": 1.0,
    "main_bar_diameter_mm": DIAMETER_MM,
    "single_bar_area_mm2": single_bar_area_mm2,
    "odb_total_steel_area_mm2": odb_total_area_mm2,
    "calculation": "sum(S11_i * single_bar_area), i=1..ODB_detected_bar_count",
    "cloud_variable": "S, Mises on undeformed shape",
    "force_variable": "S11 for T3D2 axial signed stress",
    "stress_unit": "MPa = N/mm^2",
    "force_unit": "N",
    "sign_conventions": {
        "S11": "positive tension, negative compression",
        "SteelForce_TensionPositive_N": "positive tension",
        "SteelForce_CompressionPositive_N": "positive compression",
    },
    "last_frame": {"step": last_step, "frame": last_frame, "depth_rows": len(last_rows)},
}
with (REBAR_DIR / "rebar_actual_force_metadata.json").open("w", encoding="utf-8") as stream:
    json.dump(metadata, stream, ensure_ascii=False, indent=2)

print(json.dumps(metadata, ensure_ascii=False, indent=2))
