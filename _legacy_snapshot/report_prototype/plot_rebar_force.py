from pathlib import Path

import build_post_outputs_pillow as base


root = Path(__file__).resolve().parent / "output_GJA-32_U20D_V20D"
rows = base.read_csv(root / "rebar" / "rebar_actual_force_depth_LAST.csv")

force_kn = [float(row["SteelForce_CompressionPositive_N"]) / 1000.0 for row in rows]
depth_m = [float(row["DepthFromGround_mm"]) / 1000.0 for row in rows]

base.draw_plot(
    [{"x": force_kn, "y": depth_m, "label": "32 ODB-detected bars", "color": "#7C3AED"}],
    "GJA-32 Longitudinal Rebar Axial Force-Depth, Last Frame",
    "Rebar axial force, compression positive (kN)",
    "Depth from ground (m)",
    root / "plots" / "rebar_actual_force_depth_LAST.png",
    invert_y=True,
    horizontal=0.0,
)

print(root / "plots" / "rebar_actual_force_depth_LAST.png")
