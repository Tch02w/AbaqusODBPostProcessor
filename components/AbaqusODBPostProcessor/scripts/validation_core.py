from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image


DAMAGE_COLORS = (
    "F2F2F2", "D9E8F5", "B7D4EA", "7BC8B8", "2FBF71",
    "B7DD3B", "F2D13D", "E85B2A", "CC1F2F", "FF0000",
)


def csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return sum(1 for _ in csv.DictReader(stream))


def dark_metrics(path: Path) -> dict:
    array = np.asarray(Image.open(path).convert("RGB"))
    dark = np.all(array < 70, axis=2)
    height, width = dark.shape
    columns = dark[int(height * 0.12):int(height * 0.88), int(width * 0.08):int(width * 0.78)].sum(axis=0)
    return {
        "dark_pixels": int(dark.sum()),
        "strong_vertical_columns": int((columns > int(height * 0.35)).sum()),
        "bottom_left_dark_pixels": int(dark[int(height * 0.72):, :int(width * 0.72)].sum()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    config = json.loads((root / "job_config.json").read_text(encoding="utf-8"))

    frame_counts: dict[str, int] = {}
    image_sizes: dict[str, list[int]] = {}
    white_corners = True
    for folder in sorted((root / "frames").iterdir()):
        if not folder.is_dir():
            continue
        images = sorted(folder.glob("*.png"))
        frame_counts[folder.name] = len(images)
        if images:
            with Image.open(images[-1]).convert("RGB") as image:
                image_sizes[folder.name] = list(image.size)
                corners = (image.getpixel((0, 0)), image.getpixel((image.width - 1, 0)),
                           image.getpixel((0, image.height - 1)), image.getpixel((image.width - 1, image.height - 1)))
                white_corners = white_corners and all(value == (255, 255, 255) for value in corners)

    gif_counts = {}
    for path in sorted((root / "animations").glob("*.gif")):
        with Image.open(path) as image:
            gif_counts[path.stem] = int(getattr(image, "n_frames", 1))

    palette_counts = {}
    for name in ("PILE_CON_DAMAGET", "PILE_CON_DAMAGEC"):
        path = sorted((root / "frames" / name).glob("*.png"))[-1]
        colors = Image.open(path).convert("RGB").getcolors(maxcolors=2_000_000) or []
        lookup = {color: count for count, color in colors}
        palette_counts[name] = {hex_value: int(lookup.get(tuple(bytes.fromhex(hex_value)), 0))
                                for hex_value in DAMAGE_COLORS}

    soil_metrics = {}
    for name in ("SOIL_PEEQ_XZ", "SOIL_PEMAG_XZ", "SOIL_S33_XZ", "SOIL_S_MISES_XZ"):
        images = sorted((root / "frames" / name).glob("*.png"))
        soil_metrics[name] = {"first": dark_metrics(images[0]), "last": dark_metrics(images[-1])}

    maxima_path = root / "freebody" / "pile_bending_moment_maxima.csv"
    maxima = []
    if maxima_path.exists():
        with maxima_path.open("r", encoding="utf-8-sig", newline="") as stream:
            maxima = list(csv.DictReader(stream))

    report = {
        "status": "PASS",
        "odb_path": metadata["odb_path"],
        "timeline_points": metadata["timeline_points"],
        "frame_count_by_sequence": frame_counts,
        "image_size_by_sequence": image_sizes,
        "gif_frame_count_by_sequence": gif_counts,
        "white_corners_all_sequences": white_corners,
        "view": {
            "projection": "PARALLEL",
            "soil_preset": "Bottom",
            "soil_display_set": metadata["soil_set"],
            "soil_view_cut": "Y-Plane",
            "view_cut_manager": {"above": False, "on": True, "below": True, "free_body": False},
            "display_slicing": False,
            "visible_edges": "FREE",
            "triad": False,
            "state": False,
            "title": False,
            "annotations": False,
            "legend": True,
        },
        "legend_ranges": config["legend_ranges"],
        "damage_exact_palette_pixel_counts_last_frame": palette_counts,
        "soil_edge_metrics": soil_metrics,
        "odb_detected_bar_count": metadata["odb_detected_bar_count"],
        "rebar_element_count": metadata["rebar_element_count"],
        "csv_rows": {
            "timeline": csv_rows(root / "timeline.csv"),
            "load_raw": csv_rows(root / "load_point" / "load_displacement_raw_time_aligned.csv"),
            "rebar_element_timehistory": csv_rows(root / "rebar" / "rebar_element_stress_force_timehistory.csv"),
            "freebody_axial": csv_rows(root / "freebody" / "pile_total_axial_force_time_aligned.csv"),
            "pile_force_moment": csv_rows(root / "freebody" / "pile_total_force_moment_time_aligned.csv"),
        },
        "freebody_append": metadata["freebody_append"],
        "bending_moment_maxima": maxima,
        "summary_xlsx_exists": (root / "summary.xlsx").exists(),
    }

    expected_frames = int(metadata["timeline_points"])
    checks = [
        len(frame_counts) == 11,
        all(value == expected_frames for value in frame_counts.values()),
        all(value == [1600, 1200] for value in image_sizes.values()),
        len(gif_counts) == 11 and all(value == expected_frames for value in gif_counts.values()),
        white_corners,
        all(all(value > 0 for value in item.values()) for item in palette_counts.values()),
        all(item["last"]["strong_vertical_columns"] <= 2 for item in soil_metrics.values()),
        metadata["soil_set"] == "SET-SOIL_CUT",
        metadata["odb_detected_bar_count"] == 32,
        metadata["rebar_element_count"] == 10176,
        report["csv_rows"]["timeline"] == expected_frames,
        report["csv_rows"]["load_raw"] == expected_frames,
        report["csv_rows"]["rebar_element_timehistory"] == expected_frames * metadata["rebar_element_count"],
        report["csv_rows"]["freebody_axial"] == 100,
        report["csv_rows"]["pile_force_moment"] == 100,
        metadata["freebody_append"] is False,
        report["summary_xlsx_exists"],
    ]
    report["checks"] = checks
    report["status"] = "PASS" if all(checks) else "FAIL"
    target = root / "validation_report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(target), "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
