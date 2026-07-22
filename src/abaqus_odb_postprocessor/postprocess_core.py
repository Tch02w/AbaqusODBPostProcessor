from __future__ import annotations

import csv, json, math
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
import xlsxwriter


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream: return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows: return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def normalize_white_backgrounds(output_dir: Path) -> int:
    """Make Abaqus near-white (254) pixels strict RGB white without touching #F2F2F2."""
    paths = []
    for folder_name in ("frames", "contours"):
        root = output_dir / folder_name
        if root.exists(): paths.extend(root.rglob("*.png"))
    for path in paths:
        with Image.open(path) as source: array = np.asarray(source.convert("RGB")).copy()
        array[np.all(array >= 250, axis=2)] = 255
        Image.fromarray(array, "RGB").save(path)
    return len(paths)


def build_gifs(output_dir: Path, fps: int = 5) -> dict[str, int]:
    frame_root = output_dir / "frames"; animation_dir = output_dir / "animations"
    animation_dir.mkdir(parents=True, exist_ok=True); counts = {}
    if not frame_root.exists(): return counts
    for folder in sorted(path for path in frame_root.iterdir() if path.is_dir()):
        paths = sorted(folder.glob("*.png"))
        if not paths: continue
        images = []
        for path in paths:
            with Image.open(path) as source: images.append(source.convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
        images[0].save(animation_dir / f"{folder.name}.gif", save_all=True, append_images=images[1:],
                       duration=round(1000/fps), loop=0, disposal=2, optimize=False)
        counts[folder.name] = len(images)
    return counts


def interpolate_components(points: list[tuple[float, float, float, float]], elevation: float,
                           z_min: float, z_max: float) -> tuple[float, float, float, str]:
    if elevation < z_min or elevation > z_max: return 0.0, 0.0, 0.0, "outside_rebar_extent_zero"
    if elevation <= points[0][0]: return points[0][1], points[0][2], points[0][3], "end_element_constant"
    if elevation >= points[-1][0]: return points[-1][1], points[-1][2], points[-1][3], "end_element_constant"
    for left, right in zip(points, points[1:]):
        if left[0] <= elevation <= right[0]:
            ratio = (elevation-left[0])/(right[0]-left[0])
            values = tuple(left[i] + ratio*(right[i]-left[i]) for i in range(1, 4))
            return values[0], values[1], values[2], "linear_between_element_centroids"
    raise RuntimeError("Interpolation interval not found")


def build_pile_force_moment(output_dir: Path) -> tuple[Path | None, Path | None]:
    axial_path = output_dir / "freebody" / "pile_total_axial_force_time_aligned.csv"
    rebar_path = output_dir / "rebar" / "rebar_element_stress_force_timehistory.csv"
    metadata_path = output_dir / "rebar" / "rebar_metadata.json"
    if not axial_path.exists() or not rebar_path.exists() or not metadata_path.exists(): return None, None
    axial_rows = read_csv(axial_path)
    if not axial_rows: return None, None
    target_sequences = {int(float(row["SequenceIndex"])) for row in axial_rows}
    by_level: dict[int, dict[float, list[float]]] = defaultdict(dict)
    with rebar_path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            sequence = int(float(row["SequenceIndex"]))
            if sequence not in target_sequences: continue
            z = round(float(row["CentroidZ_mm"]), 8); x = float(row["CentroidX_mm"]); y = float(row["CentroidY_mm"])
            force = float(row["ElementAxialForce_CompressionPositive_N"])
            current = by_level[sequence].setdefault(z, [0.0, 0.0, 0.0])
            current[0] += force; current[1] += y*force; current[2] += -x*force
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")); z_min, z_max = map(float, metadata["rebar_z_extent_mm"])
    points_by_sequence = {sequence: [(z, values[0], values[1], values[2]) for z, values in sorted(levels.items())]
                          for sequence, levels in by_level.items()}
    combined = []
    for source in axial_rows:
        row = dict(source); sequence = int(float(row["SequenceIndex"])); elevation = float(row["Elevation_mm"])
        force, rebar_mx, rebar_my, rule = interpolate_components(points_by_sequence[sequence], elevation, z_min, z_max)
        concrete_mx = float(row["Mx_Nmm"]); concrete_my = float(row["My_Nmm"])
        total_mx, total_my = concrete_mx+rebar_mx, concrete_my+rebar_my
        row.update({"RebarAxial_Check_CompressionPositive_N": force,
            "ConcreteBendingMoment_Mx_Nmm": concrete_mx, "ConcreteBendingMoment_My_Nmm": concrete_my,
            "RebarBendingMoment_Interpolated_Mx_Nmm": rebar_mx,
            "RebarBendingMoment_Interpolated_My_Nmm": rebar_my,
            "PileTotalBendingMoment_Mx_Nmm": total_mx, "PileTotalBendingMoment_My_Nmm": total_my,
            "PileTotalBendingMoment_Resultant_Nmm": math.hypot(total_mx, total_my),
            "BendingMomentInterpolationRule": rule})
        combined.append(row)
    target = output_dir / "freebody" / "pile_total_force_moment_time_aligned.csv"; write_csv(target, combined)

    maxima = []
    groups: dict[int, list[dict]] = defaultdict(list)
    for row in combined: groups[int(float(row["SequenceIndex"]))].append(row)
    for sequence, rows in sorted(groups.items()):
        my_row = max(rows, key=lambda item: abs(float(item["PileTotalBendingMoment_My_Nmm"])))
        resultant_row = max(rows, key=lambda item: float(item["PileTotalBendingMoment_Resultant_Nmm"]))
        base_keys = ("SequenceIndex", "StepIndex", "StepName", "FrameIndex", "IncrementNumber", "StepTime", "TotalTime", "Status")
        summary = {key: my_row[key] for key in base_keys if key in my_row}
        summary.update({"MaxAbsPileTotalMy_Nmm": abs(float(my_row["PileTotalBendingMoment_My_Nmm"])),
            "SignedPileTotalMyAtMaximum_Nmm": float(my_row["PileTotalBendingMoment_My_Nmm"]),
            "DepthAtMaxAbsMy_mm": float(my_row["DepthFromGround_mm"]),
            "MaxPileBendingResultant_Nmm": float(resultant_row["PileTotalBendingMoment_Resultant_Nmm"]),
            "DepthAtMaxResultant_mm": float(resultant_row["DepthFromGround_mm"])})
        maxima.append(summary)
    maxima_path = output_dir / "freebody" / "pile_bending_moment_maxima.csv"; write_csv(maxima_path, maxima)
    return target, maxima_path


def plot_pile_axial(output_dir: Path) -> Path | None:
    source = output_dir / "freebody" / "pile_total_axial_force_time_aligned.csv"
    if not source.exists(): return None
    rows = read_csv(source)
    if not rows: return None
    depth = [float(row["DepthFromGround_mm"])/1000 for row in rows]
    concrete = [float(row["ConcreteAxial_CompressionPositive_N"])/1000 for row in rows]
    rebar = [float(row["RebarAxial_Interpolated_CompressionPositive_N"])/1000 for row in rows]
    total = [float(row["PileTotalAxial_CompressionPositive_N"])/1000 for row in rows]
    plot_dir = output_dir/"plots"; plot_dir.mkdir(parents=True, exist_ok=True); target = plot_dir/"pile_total_axial_force_depth.png"
    fig, ax = plt.subplots(figsize=(7.6, 8.0), constrained_layout=True)
    ax.plot(concrete, depth, label="Concrete/pipe FreeBody"); ax.plot(rebar, depth, label="Rebar interpolated")
    ax.plot(total, depth, label="Pile total", linewidth=2.4); ax.axhline(0, color="black", linewidth=0.8)
    ax.invert_yaxis(); ax.grid(True, alpha=0.25); ax.set_xlabel("Axial force, compression positive (kN)")
    ax.set_ylabel("Depth from ground (m)"); ax.legend(); fig.savefig(target, dpi=180); plt.close(fig); return target


def plot_pile_bending(output_dir: Path) -> list[Path]:
    source = output_dir/"freebody"/"pile_total_force_moment_time_aligned.csv"
    if not source.exists(): return []
    groups = defaultdict(list)
    for row in read_csv(source): groups[int(float(row["SequenceIndex"]))].append(row)
    plot_dir = output_dir/"plots"; plot_dir.mkdir(parents=True, exist_ok=True); targets = []
    for sequence, rows in sorted(groups.items()):
        depth = [float(row["DepthFromGround_mm"])/1000 for row in rows]
        concrete = [float(row["ConcreteBendingMoment_My_Nmm"])/1.0e6 for row in rows]
        rebar = [float(row["RebarBendingMoment_Interpolated_My_Nmm"])/1.0e6 for row in rows]
        total = [float(row["PileTotalBendingMoment_My_Nmm"])/1.0e6 for row in rows]
        target = plot_dir/f"pile_bending_moment_My_depth_SEQ{sequence:04d}.png"
        fig, ax = plt.subplots(figsize=(7.6, 8.0), constrained_layout=True)
        ax.plot(concrete, depth, label="Concrete/pipe My"); ax.plot(rebar, depth, label="Rebar My")
        ax.plot(total, depth, label="Pile total My", linewidth=2.4); ax.axvline(0, color="black", linewidth=0.8)
        ax.axhline(0, color="black", linewidth=0.8); ax.invert_yaxis(); ax.grid(True, alpha=0.25)
        ax.set_xlabel("Bending moment My about global 2-axis (kN·m)"); ax.set_ylabel("Depth from ground (m)")
        ax.set_title(f"Sequence {sequence}: lateral load in global 1 uses My"); ax.legend()
        fig.savefig(target, dpi=180); plt.close(fig); targets.append(target)
    return targets


def build_xlsx(output_dir: Path) -> Path:
    target = output_dir/"summary.xlsx"; workbook = xlsxwriter.Workbook(target)
    title = workbook.add_format({"bold": True, "font_color": "white", "bg_color": "#134E4A", "font_size": 16})
    header = workbook.add_format({"bold": True, "font_color": "white", "bg_color": "#0F766E", "border": 1})
    cell = workbook.add_format({"border": 1}); number = workbook.add_format({"border": 1, "num_format": "0.000000"})
    summary = workbook.add_worksheet("Summary"); summary.hide_gridlines(2); summary.merge_range("A1:F1", "Abaqus ODB postprocessing summary", title)
    summary.write_row("A3", ["Item", "Value"], header)
    metadata_path = output_dir/"metadata.json"; metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    entries = [("ODB", metadata.get("odb_path", "")), ("Timeline", "Selected SequenceIndex + StepName + FrameIndex + TotalTime"),
        ("Rebar", "HRB400 + near-Z T3D2; per-element S11 × area"),
        ("Pile axial", "Concrete/pipe FreeBody + Z-interpolated rebar; compression positive"),
        ("Pile bending", "Global-1 lateral loading uses total My; rebar My = Σ(-xN)"),
        ("Legend", "Abaqus active-display extrema shared within each comparison group; damage fixed 0..0.886")]
    for index, values in enumerate(entries, 3): summary.write_row(index, 0, values, cell)
    summary.set_column("A:A", 24); summary.set_column("B:B", 90)
    sheets = [("Timeline", output_dir/"data"/"timeline_alignment.csv"),
        ("Load_Raw", output_dir/"data"/"load_point_raw.csv"),
        ("Pile_Axial", output_dir/"freebody"/"pile_total_axial_force_time_aligned.csv"),
        ("Pile_Moment", output_dir/"freebody"/"pile_total_force_moment_time_aligned.csv"),
        ("Moment_Max", output_dir/"freebody"/"pile_bending_moment_maxima.csv"),
        ("Rebar_Depth", output_dir/"rebar"/"rebar_force_by_element_level_timehistory.csv")]
    for name, path in sheets:
        if not path.exists(): continue
        rows = read_csv(path)
        if not rows: continue
        sheet = workbook.add_worksheet(name[:31]); sheet.hide_gridlines(2); sheet.freeze_panes(1, 0)
        columns = list(rows[0]); sheet.write_row(0, 0, columns, header)
        for row_index, row in enumerate(rows, 1):
            for column_index, column in enumerate(columns):
                value = row[column]
                try: sheet.write_number(row_index, column_index, float(value), number)
                except ValueError: sheet.write(row_index, column_index, value, cell)
        for index, column in enumerate(columns): sheet.set_column(index, index, min(max(len(column)+2, 12), 34))
    workbook.close(); return target


def finalize_output(output_dir: Path, fps: int = 5) -> dict:
    moment_csv, maxima_csv = build_pile_force_moment(output_dir)
    manifest = {"normalized_png_count": normalize_white_backgrounds(output_dir),
        "animations": build_gifs(output_dir, fps), "pile_axial_plot": str(plot_pile_axial(output_dir) or ""),
        "pile_bending_plots": [str(path) for path in plot_pile_bending(output_dir)],
        "pile_force_moment_csv": str(moment_csv or ""), "pile_bending_maxima_csv": str(maxima_csv or ""),
        "xlsx": str(build_xlsx(output_dir))}
    (output_dir/"host_postprocess_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
