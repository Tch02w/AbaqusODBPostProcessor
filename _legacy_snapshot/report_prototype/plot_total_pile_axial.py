from pathlib import Path

import build_post_outputs_pillow as base


root = Path(__file__).resolve().parent / "output_GJA-32_U20D_V20D"
rows = base.read_csv(root / "freebody" / "pile_total_axial_force_time_aligned.csv")
depth_m = [float(row["DepthFromGround_mm"]) / 1000.0 for row in rows]

series = [
    {
        "x": [float(row["ConcreteAxial_CompressionPositive_N"]) / 1000.0 for row in rows],
        "y": depth_m,
        "label": "Concrete FreeBody",
        "color": "#B45309",
    },
    {
        "x": [float(row["RebarAxial_Interpolated_CompressionPositive_N"]) / 1000.0 for row in rows],
        "y": depth_m,
        "label": "Rebar interpolated",
        "color": "#7C3AED",
    },
    {
        "x": [float(row["PileTotalAxial_CompressionPositive_N"]) / 1000.0 for row in rows],
        "y": depth_m,
        "label": "Pile total",
        "color": "#0F766E",
    },
]
output = root / "plots" / "pile_total_axial_force_depth_LAST.png"
base.draw_plot(
    series,
    "GJA-32 Pile Axial Force-Depth, Time-Aligned Last Frame",
    "Axial force, compression positive (kN)",
    "Depth from ground (m)",
    output,
    invert_y=True,
    horizontal=0.0,
)
print(output)
