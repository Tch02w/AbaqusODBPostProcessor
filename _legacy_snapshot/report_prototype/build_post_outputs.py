import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
from PIL import Image


ROOT = Path(__file__).resolve().parent / "output_GJA-32_U20D_V20D"
FRAME_ROOT = ROOT / "frames"
ANIMATION_DIR = ROOT / "animations"
PLOT_DIR = ROOT / "plots"
DATA_DIR = ROOT / "data"
FREEBODY_DIR = ROOT / "freebody"

ANIMATION_DIR.mkdir(parents=True, exist_ok=True)
PLOT_DIR.mkdir(parents=True, exist_ok=True)


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def float_or_none(value):
    if value is None or value == "":
        return None
    return float(value)


def build_gif(frame_dir, output_path, duration_ms=200):
    paths = sorted(frame_dir.glob("*.png"))
    if not paths:
        return 0
    images = []
    for path in paths:
        with Image.open(path) as source:
            images.append(source.convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
    images[0].save(
        output_path,
        save_all=True,
        append_images=images[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,
        optimize=False,
    )
    return len(images)


def plot_load_curve(direction):
    rows = read_csv(DATA_DIR / f"load_displacement_dir{direction}.csv")
    u_key = f"U{direction}_mm"
    rf_key = f"RF{direction}_N"
    displacement = [float(row[u_key]) for row in rows]
    load_kn = [float(row[rf_key]) / 1000.0 for row in rows]
    fig, axis = plt.subplots(figsize=(7.2, 5.2), constrained_layout=True)
    axis.plot(displacement, load_kn, color="#0F766E", linewidth=2.2)
    axis.scatter(displacement, load_kn, color="#0F766E", s=18)
    axis.set_xlabel(f"Displacement U{direction} (mm)")
    axis.set_ylabel(f"Reaction RF{direction} (kN)")
    axis.set_title(f"GJA-32 Load-Displacement, Direction {direction}")
    axis.grid(True, color="#D1D5DB", linewidth=0.7)
    fig.savefig(PLOT_DIR / f"load_displacement_dir{direction}.png", dpi=180)
    plt.close(fig)


def plot_axial_force_depth():
    path = FREEBODY_DIR / "axial_force_depth_LAST.csv"
    rows = read_csv(path)
    axial_kn = [float(row["AxialForce_CompressionPositive_N"]) / 1000.0 for row in rows]
    depth_m = [float(row["DepthFromGround_mm"]) / 1000.0 for row in rows]
    fig, axis = plt.subplots(figsize=(6.4, 7.4), constrained_layout=True)
    axis.plot(axial_kn, depth_m, color="#B45309", linewidth=2.2)
    axis.scatter(axial_kn, depth_m, color="#B45309", s=10)
    axis.axhline(0.0, color="#111827", linewidth=0.9, linestyle="--")
    axis.set_xlabel("Concrete axial resultant F3 (kN)")
    axis.set_ylabel("Depth from ground (m)")
    axis.set_title("GJA-32 Concrete Axial Force-Depth, Last Frame")
    axis.invert_yaxis()
    axis.grid(True, color="#D1D5DB", linewidth=0.7)
    fig.savefig(PLOT_DIR / "axial_force_depth_LAST.png", dpi=180)
    plt.close(fig)


def plot_damage_scan():
    rows = read_csv(DATA_DIR / "damage_ring_scan.csv")
    indices = [int(row["SequenceIndex"]) for row in rows]
    max_t = [float_or_none(row["MaxDAMAGET"]) for row in rows]
    max_c = [float_or_none(row["MaxDAMAGEC"]) for row in rows]
    coverage = [float(row["MaxAngularCoverage"]) for row in rows]
    fig, axis = plt.subplots(figsize=(8.2, 5.2), constrained_layout=True)
    axis.plot(indices, max_t, label="Max DAMAGET", color="#DC2626", linewidth=2.0)
    axis.plot(indices, max_c, label="Max DAMAGEC", color="#2563EB", linewidth=2.0)
    axis.plot(indices, coverage, label="Angular coverage", color="#059669", linewidth=2.0)
    axis.axhline(0.90, color="#111827", linewidth=0.9, linestyle="--", label="0.90 threshold")
    axis.set_xlabel("Continuous loading frame index")
    axis.set_ylabel("Damage / coverage")
    axis.set_title("GJA-32 Automatic Ring-Damage Scan")
    axis.set_ylim(-0.02, 1.02)
    axis.grid(True, color="#D1D5DB", linewidth=0.7)
    axis.legend()
    fig.savefig(PLOT_DIR / "damage_ring_scan.png", dpi=180)
    plt.close(fig)


manifest = {"animations": {}, "plots": []}
for frame_dir in sorted(path for path in FRAME_ROOT.iterdir() if path.is_dir()):
    output_path = ANIMATION_DIR / f"{frame_dir.name}.gif"
    count = build_gif(frame_dir, output_path)
    manifest["animations"][frame_dir.name] = {
        "path": str(output_path),
        "frame_count": count,
        "fps": 5,
    }

plot_load_curve(1)
plot_load_curve(3)
plot_axial_force_depth()
plot_damage_scan()
manifest["plots"] = [str(path) for path in sorted(PLOT_DIR.glob("*.png"))]

with (ROOT / "postprocess_manifest.json").open("w", encoding="utf-8") as stream:
    json.dump(manifest, stream, ensure_ascii=False, indent=2)

print(json.dumps({"animations": len(manifest["animations"]), "plots": len(manifest["plots"])}))
