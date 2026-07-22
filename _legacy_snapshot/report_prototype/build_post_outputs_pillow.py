import csv
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent / "output_GJA-32_U20D_V20D"
FRAME_ROOT = ROOT / "frames"
ANIMATION_DIR = ROOT / "animations"
PLOT_DIR = ROOT / "plots"
DATA_DIR = ROOT / "data"
FREEBODY_DIR = ROOT / "freebody"

ANIMATION_DIR.mkdir(parents=True, exist_ok=True)
PLOT_DIR.mkdir(parents=True, exist_ok=True)


def font(size, bold=False):
    name = "arialbd.ttf" if bold else "arial.ttf"
    try:
        return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)
    except OSError:
        return ImageFont.load_default()


FONT_SMALL = font(16)
FONT_BODY = font(18)
FONT_TITLE = font(26, bold=True)


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def padded_range(values):
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        delta = abs(minimum) * 0.05 or 1.0
    else:
        delta = (maximum - minimum) * 0.06
    return minimum - delta, maximum + delta


def draw_plot(series, title, x_label, y_label, output_path, invert_y=False, horizontal=None):
    width, height = 1100, 760
    margin_left, margin_right = 125, 45
    margin_top, margin_bottom = 85, 105
    plot_left, plot_top = margin_left, margin_top
    plot_right, plot_bottom = width - margin_right, height - margin_bottom

    all_x = [value for item in series for value in item["x"]]
    all_y = [value for item in series for value in item["y"]]
    if horizontal is not None:
        all_y.append(horizontal)
    x_min, x_max = padded_range(all_x)
    y_min, y_max = padded_range(all_y)

    def map_x(value):
        return plot_left + (value - x_min) / (x_max - x_min) * (plot_right - plot_left)

    def map_y(value):
        ratio = (value - y_min) / (y_max - y_min)
        if invert_y:
            return plot_top + ratio * (plot_bottom - plot_top)
        return plot_bottom - ratio * (plot_bottom - plot_top)

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((plot_left, plot_top, plot_right, plot_bottom), outline="#374151", width=2)

    tick_count = 6
    for index in range(tick_count + 1):
        x_value = x_min + (x_max - x_min) * index / tick_count
        x_pixel = map_x(x_value)
        draw.line((x_pixel, plot_top, x_pixel, plot_bottom), fill="#E5E7EB", width=1)
        label = f"{x_value:.3g}"
        box = draw.textbbox((0, 0), label, font=FONT_SMALL)
        draw.text((x_pixel - (box[2] - box[0]) / 2, plot_bottom + 12), label, fill="#111827", font=FONT_SMALL)

        y_value = y_min + (y_max - y_min) * index / tick_count
        y_pixel = map_y(y_value)
        draw.line((plot_left, y_pixel, plot_right, y_pixel), fill="#E5E7EB", width=1)
        label = f"{y_value:.3g}"
        box = draw.textbbox((0, 0), label, font=FONT_SMALL)
        draw.text((plot_left - (box[2] - box[0]) - 12, y_pixel - 9), label, fill="#111827", font=FONT_SMALL)

    if horizontal is not None:
        y_pixel = map_y(horizontal)
        draw.line((plot_left, y_pixel, plot_right, y_pixel), fill="#111827", width=2)

    for item in series:
        points = [(map_x(x), map_y(y)) for x, y in zip(item["x"], item["y"])]
        if len(points) > 1:
            draw.line(points, fill=item["color"], width=4, joint="curve")
        for x_pixel, y_pixel in points:
            draw.ellipse((x_pixel - 3, y_pixel - 3, x_pixel + 3, y_pixel + 3), fill=item["color"])

    draw.text((width / 2, 25), title, fill="#111827", font=FONT_TITLE, anchor="ma")
    draw.text(((plot_left + plot_right) / 2, height - 42), x_label, fill="#111827", font=FONT_BODY, anchor="mm")

    y_label_layer = Image.new("RGBA", (height, 55), (255, 255, 255, 0))
    y_draw = ImageDraw.Draw(y_label_layer)
    y_draw.text((height / 2, 27), y_label, fill="#111827", font=FONT_BODY, anchor="mm")
    rotated = y_label_layer.rotate(90, expand=True)
    image.paste(rotated, (20, int((height - rotated.height) / 2)), rotated)

    legend_x = plot_right - 250
    legend_y = plot_top + 18
    for index, item in enumerate(series):
        y = legend_y + index * 28
        draw.line((legend_x, y + 8, legend_x + 30, y + 8), fill=item["color"], width=4)
        draw.text((legend_x + 40, y), item["label"], fill="#111827", font=FONT_SMALL)

    image.save(output_path)


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


def plot_load(direction):
    rows = read_csv(DATA_DIR / f"load_displacement_dir{direction}.csv")
    displacement = [float(row[f"U{direction}_mm"]) for row in rows]
    load_kn = [float(row[f"RF{direction}_N"]) / 1000.0 for row in rows]
    draw_plot(
        [{"x": displacement, "y": load_kn, "label": f"RF{direction}-U{direction}", "color": "#0F766E"}],
        f"GJA-32 Load-Displacement, Direction {direction}",
        f"Displacement U{direction} (mm)",
        f"Reaction RF{direction} (kN)",
        PLOT_DIR / f"load_displacement_dir{direction}.png",
    )


def plot_axial():
    rows = read_csv(FREEBODY_DIR / "axial_force_depth_LAST.csv")
    axial_kn = [float(row["AxialForce_CompressionPositive_N"]) / 1000.0 for row in rows]
    depth_m = [float(row["DepthFromGround_mm"]) / 1000.0 for row in rows]
    draw_plot(
        [{"x": axial_kn, "y": depth_m, "label": "SET-PILE_CON", "color": "#B45309"}],
        "GJA-32 Concrete Axial Force-Depth, Last Frame",
        "Concrete axial resultant F3 (kN)",
        "Depth from ground (m)",
        PLOT_DIR / "axial_force_depth_LAST.png",
        invert_y=True,
        horizontal=0.0,
    )


def plot_damage():
    rows = read_csv(DATA_DIR / "damage_ring_scan.csv")
    index = [int(row["SequenceIndex"]) for row in rows]
    max_t = [float(row["MaxDAMAGET"]) for row in rows]
    max_c = [float(row["MaxDAMAGEC"]) for row in rows]
    coverage = [float(row["MaxAngularCoverage"]) for row in rows]
    draw_plot(
        [
            {"x": index, "y": max_t, "label": "Max DAMAGET", "color": "#DC2626"},
            {"x": index, "y": max_c, "label": "Max DAMAGEC", "color": "#2563EB"},
            {"x": index, "y": coverage, "label": "Angular coverage", "color": "#059669"},
        ],
        "GJA-32 Automatic Ring-Damage Scan",
        "Continuous loading frame index",
        "Damage / coverage",
        PLOT_DIR / "damage_ring_scan.png",
        horizontal=0.90,
    )


manifest = {"animations": {}, "plots": []}
for frame_dir in sorted(path for path in FRAME_ROOT.iterdir() if path.is_dir()):
    output_path = ANIMATION_DIR / f"{frame_dir.name}.gif"
    count = build_gif(frame_dir, output_path)
    manifest["animations"][frame_dir.name] = {
        "path": str(output_path),
        "frame_count": count,
        "fps": 5,
    }

plot_load(1)
plot_load(3)
plot_axial()
plot_damage()
manifest["plots"] = [str(path) for path in sorted(PLOT_DIR.glob("*.png"))]

with (ROOT / "postprocess_manifest.json").open("w", encoding="utf-8") as stream:
    json.dump(manifest, stream, ensure_ascii=False, indent=2)

print(json.dumps({"animations": len(manifest["animations"]), "plots": len(manifest["plots"])}))
