import csv
import json
import math
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output_GJA-32_U20D_V20D"
REBAR_DIR = OUTPUT_DIR / "rebar"
CATALOG_PATH = BASE_DIR / "rebar_spec_catalog.csv"
MODEL_NAME = "GJA-32_U20D_V20D"


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


catalog = read_csv(CATALOG_PATH)
matches = [
    row
    for row in catalog
    if MODEL_NAME == row["SampleID"]
    or MODEL_NAME.startswith(row["SampleID"] + "_")
    or MODEL_NAME.startswith(row["SampleID"] + "-")
]
if not matches:
    raise RuntimeError(f"No reinforcement specification matches {MODEL_NAME}")
spec = sorted(matches, key=lambda row: len(row["SampleID"]), reverse=True)[0]

diameter_mm = float(spec["MainBarDiameter_mm"])
actual_bar_count = int(spec["ActualMainBarCount"])
specified_length_mm = float(spec["MainBarLength_mm"])
single_physical_area_mm2 = math.pi * diameter_mm**2 / 4.0
actual_total_area_mm2 = single_physical_area_mm2 * actual_bar_count

with (REBAR_DIR / "rebar_metadata.json").open("r", encoding="utf-8") as stream:
    rebar_metadata = json.load(stream)
model_bar_count = int(rebar_metadata["longitudinal_bar_count"])
equivalent_area_per_model_chain_mm2 = actual_total_area_mm2 / model_bar_count
model_length_mm = max(bar["z_max"] for bar in rebar_metadata["bars"]) - min(
    bar["z_min"] for bar in rebar_metadata["bars"]
)

depth_rows = read_csv(REBAR_DIR / "rebar_stress_force_depth_summary.csv")
actual_rows = []
for row in depth_rows:
    sum_stress_mpa = float(row["SumS11_MPa"])
    actual_tension_positive_n = sum_stress_mpa * equivalent_area_per_model_chain_mm2
    actual_rows.append(
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
            "ModelSamplingBarCount": int(row["CrossingBarCount"]),
            "ActualMainBarCount": actual_bar_count,
            "MainBarDiameter_mm": diameter_mm,
            "SinglePhysicalBarArea_mm2": single_physical_area_mm2,
            "ActualTotalSteelArea_mm2": actual_total_area_mm2,
            "EquivalentAreaPerModelChain_mm2": equivalent_area_per_model_chain_mm2,
            "S11_Min_MPa": float(row["S11_Min_MPa"]),
            "S11_Mean_MPa": float(row["S11_Mean_MPa"]),
            "S11_Max_MPa": float(row["S11_Max_MPa"]),
            "SumS11_MPa": sum_stress_mpa,
            "ActualSteelForce_TensionPositive_N": actual_tension_positive_n,
            "ActualSteelForce_CompressionPositive_N": -actual_tension_positive_n,
        }
    )

headers = list(actual_rows[0].keys())
write_csv(REBAR_DIR / "rebar_actual_force_depth_all_frames.csv", headers, actual_rows)

last_step = actual_rows[-1]["StepName"]
last_frame = actual_rows[-1]["FrameIndex"]
last_rows = [
    row
    for row in actual_rows
    if row["StepName"] == last_step and row["FrameIndex"] == last_frame
]
write_csv(REBAR_DIR / "rebar_actual_force_depth_LAST.csv", headers, last_rows)

metadata = {
    "model_name": MODEL_NAME,
    "matched_sample_id": spec["SampleID"],
    "specified_main_bar_length_mm": specified_length_mm,
    "detected_model_bar_length_mm": model_length_mm,
    "length_difference_mm": model_length_mm - specified_length_mm,
    "main_bar_diameter_mm": diameter_mm,
    "actual_main_bar_count": actual_bar_count,
    "detected_model_bar_count": model_bar_count,
    "physical_single_bar_area_mm2": single_physical_area_mm2,
    "actual_total_steel_area_mm2": actual_total_area_mm2,
    "equivalent_area_per_model_chain_mm2": equivalent_area_per_model_chain_mm2,
    "count_scale_factor": actual_bar_count / float(model_bar_count),
    "calculation": "sum(S11_i * equivalent_area_per_model_chain), i=1..model_bar_count",
    "equivalent_calculation": "mean(S11_i) * actual_bar_count * physical_single_bar_area",
    "stress_unit": "MPa = N/mm^2",
    "force_unit": "N",
    "sign_conventions": {
        "S11": "positive tension, negative compression",
        "ActualSteelForce_TensionPositive_N": "positive tension",
        "ActualSteelForce_CompressionPositive_N": "positive compression",
    },
    "last_frame": {"step": last_step, "frame": last_frame, "depth_rows": len(last_rows)},
}
with (REBAR_DIR / "rebar_actual_force_metadata.json").open("w", encoding="utf-8") as stream:
    json.dump(metadata, stream, ensure_ascii=False, indent=2)

print(json.dumps(metadata, ensure_ascii=False, indent=2))
