import csv
import json
import math
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
REBAR_DIR = BASE_DIR / "output_GJA-32_U20D_V20D" / "rebar"
DIAMETER_MM = 32.0
AREA_MM2 = math.pi * DIAMETER_MM**2 / 4.0


with (REBAR_DIR / "rebar_bar_summary_last_frame.csv").open(
    "r", encoding="utf-8-sig", newline=""
) as stream:
    source_rows = list(csv.DictReader(stream))

rows = []
for row in source_rows:
    s11_min = float(row["LastFrame_S11_Min_MPa"])
    s11_max = float(row["LastFrame_S11_Max_MPa"])
    max_abs = max(abs(s11_min), abs(s11_max))
    rows.append(
        {
            "BarID": int(row["BarID"]),
            "X_mm": float(row["X_mm"]),
            "Y_mm": float(row["Y_mm"]),
            "Radius_mm": float(row["Radius_mm"]),
            "Angle_rad": float(row["Angle_rad"]),
            "Z_Min_mm": float(row["Z_Min_mm"]),
            "Z_Max_mm": float(row["Z_Max_mm"]),
            "NodeCount": int(row["NodeCount"]),
            "LastFrame_S11_Min_MPa": s11_min,
            "LastFrame_S11_Max_MPa": s11_max,
            "LastFrame_S11_MaxAbs_MPa": max_abs,
            "BarArea_mm2": AREA_MM2,
            "LastFrame_Force_Min_N": s11_min * AREA_MM2,
            "LastFrame_Force_Max_N": s11_max * AREA_MM2,
            "LastFrame_MaxAbsForce_N": max_abs * AREA_MM2,
        }
    )

output = REBAR_DIR / "rebar_bar_actual_summary_last_frame.csv"
with output.open("w", encoding="utf-8-sig", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

print(json.dumps({"path": str(output), "rows": len(rows), "bar_area_mm2": AREA_MM2}))
