from __future__ import annotations

import csv, json, math, re, shutil
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
import xlsxwriter

from .file_attributes import hide_internal_result_json_files


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


def build_transparent_backgrounds(output_dir: Path) -> int:
    """Create transparent PNG copies while preserving every Abaqus source PNG."""

    count = 0
    for folder_name in ("frames", "contours"):
        source_root = output_dir / folder_name
        if not source_root.exists():
            continue
        target_root = output_dir / f"{folder_name}_transparent"
        for source_path in source_root.rglob("*.png"):
            target_path = target_root / source_path.relative_to(source_root)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source_path) as source:
                rgb = np.asarray(source.convert("RGB")).copy()
            alpha = np.full(rgb.shape[:2], 255, dtype=np.uint8)
            alpha[np.all(rgb >= 250, axis=2)] = 0
            rgba = np.dstack((rgb, alpha))
            Image.fromarray(rgba, "RGBA").save(target_path)
            count += 1
    return count


def remove_render_png_directories(
    output_dir: Path, folder_names: tuple[str, ...]
) -> int:
    """Remove selected generated PNG trees after dependent assets are built."""

    removed = 0
    for folder_name in folder_names:
        target = output_dir / folder_name
        if not target.is_dir():
            continue
        removed += sum(1 for path in target.rglob("*.png") if path.is_file())
        shutil.rmtree(target)
    return removed


def count_render_pngs(output_dir: Path, folder_names: tuple[str, ...]) -> int:
    return sum(
        1
        for folder_name in folder_names
        for path in (output_dir / folder_name).rglob("*.png")
        if path.is_file()
    )


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


def _load_components(load_direction: str) -> list[int]:
    components = []
    for value in re.findall(r"[123]", str(load_direction)):
        component = int(value)
        if component not in components:
            components.append(component)
    return components or [3]


def _float_or_none(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _component_history_value(
    row: dict[str, str],
    prefix: str,
    components: list[int],
    magnitude_column: str = "",
) -> float | None:
    values = [
        value
        for component in components
        if (
            value := _float_or_none(row.get(f"{prefix}{component}_N"))
        ) is not None
    ]
    if len(components) == 1 and values:
        return values[0]
    if values:
        return math.sqrt(sum(value * value for value in values))
    return _float_or_none(row.get(magnitude_column)) if magnitude_column else None


def build_load_resistance_table(output_dir: Path) -> Path | None:
    """Build an engineering-ready pile/root-key load-sharing history."""

    load_path = output_dir / "data" / "load_point_raw.csv"
    history_output_dir = output_dir / "History_Output"
    contact_path = history_output_dir / "contact_history_raw.csv"
    if not load_path.exists() or not contact_path.exists():
        return None
    load_rows = read_csv(load_path)
    contact_rows = read_csv(contact_path)
    if not load_rows or not contact_rows:
        return None

    metadata_path = output_dir / "metadata.json"
    metadata = (
        json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata_path.exists()
        else {}
    )
    config_path = output_dir / "job_config.json"
    config = (
        json.loads(config_path.read_text(encoding="utf-8"))
        if config_path.exists()
        else {}
    )
    load_direction = str(
        metadata.get("load_direction")
        or config.get("load_direction")
        or "3"
    )
    components = _load_components(load_direction)
    contact_by_sequence = {
        int(float(row["SequenceIndex"])): row
        for row in contact_rows
    }
    key_ids = sorted(
        {
            match.group(1)
            for column in contact_rows[0]
            if (
                match := re.match(
                    r"(KEY(?:_\d+)+)_CFN(?:[123]|M)_N$",
                    column,
                )
            )
        },
        key=lambda value: tuple(
            int(part) for part in value.split("_")[1:]
        ),
    )
    key_groups = sorted(
        {
            "_".join(key_id.split("_")[:-1])
            for key_id in key_ids
            if len(key_id.split("_")) > 2
        },
        key=lambda value: tuple(
            int(part) for part in value.split("_")[1:]
        ),
    )
    timeline_columns = {
        "SequenceIndex",
        "StepIndex",
        "StepName",
        "FrameIndex",
        "IncrementNumber",
        "StepTime",
        "TotalTime",
    }
    recognized_history_pattern = re.compile(
        r"(?:KEY(?:_\d+)+_CFN(?:[123]|M)_N|"
        r"PILE_(?:CFN[123M]|CFS[123M])_N)$"
    )
    passthrough_history_columns = [
        column
        for column in contact_rows[0]
        if column not in timeline_columns
        and not recognized_history_pattern.fullmatch(column)
    ]
    processed_rows = []
    for load_row in load_rows:
        sequence = int(float(load_row["SequenceIndex"]))
        contact_row = contact_by_sequence.get(sequence, {})
        displacement_components = [
            _float_or_none(load_row.get(f"U{component}_mm")) or 0.0
            for component in components
        ]
        reaction_components = [
            _float_or_none(load_row.get(f"RF{component}_N")) or 0.0
            for component in components
        ]
        if len(components) == 1:
            displacement = displacement_components[0]
            reaction_signed_kn = reaction_components[0] / 1000.0
        else:
            displacement = math.sqrt(
                sum(value * value for value in displacement_components)
            )
            reaction_signed_kn = None
        reaction_kn = math.sqrt(
            sum(value * value for value in reaction_components)
        ) / 1000.0
        row = {
            key: load_row[key]
            for key in (
                "SequenceIndex",
                "StepIndex",
                "StepName",
                "FrameIndex",
                "IncrementNumber",
                "StepTime",
                "TotalTime",
            )
            if key in load_row
        }
        row.update(
            {
                "LoadDirection": load_direction,
                "PileTopDisplacement_mm": displacement,
                "PileTopDisplacementAbs_mm": abs(displacement),
                "PileTopReactionSigned_kN": (
                    reaction_signed_kn
                    if reaction_signed_kn is not None
                    else ""
                ),
                "PileTopReaction_kN": reaction_kn,
            }
        )
        key_resistances = []
        key_signed_forces = []
        key_bearing_by_id = {}
        for key_id in key_ids:
            signed_force = _component_history_value(
                contact_row,
                f"{key_id}_CFN",
                components,
                f"{key_id}_CFNM_N",
            )
            bearing_kn = (
                abs(signed_force) / 1000.0
                if signed_force is not None
                else 0.0
            )
            signed_kn = (
                signed_force / 1000.0
                if signed_force is not None and len(components) == 1
                else ""
            )
            row[f"{key_id}_HistorySigned_kN"] = signed_kn
            row[f"{key_id}_Bearing_kN"] = bearing_kn
            key_resistances.append(bearing_kn)
            key_bearing_by_id[key_id] = bearing_kn
            if signed_force is not None:
                key_signed_forces.append(signed_force / 1000.0)

        root_key_total = sum(key_resistances)
        for key_group in key_groups:
            group_values = [
                bearing
                for key_id, bearing in key_bearing_by_id.items()
                if key_id.startswith(key_group + "_")
            ]
            row[f"{key_group}_GroupCount"] = len(group_values)
            row[f"{key_group}_GroupTotalBearing_kN"] = sum(group_values)
            row[f"{key_group}_GroupAverageBearing_kN"] = (
                sum(group_values) / len(group_values)
                if group_values
                else 0.0
            )
        shaft_signed = _component_history_value(
            contact_row,
            "PILE_CFS",
            components,
            "PILE_CFSM_N",
        )
        shaft_friction = (
            abs(shaft_signed) / 1000.0
            if shaft_signed is not None
            else 0.0
        )
        unresolved = reaction_kn - root_key_total - shaft_friction
        denominator = reaction_kn if reaction_kn > 1.0e-12 else None
        row.update(
            {
                "RootKeyCount": len(key_ids),
                "RootKeyHistorySignedSum_kN": (
                    sum(key_signed_forces)
                    if len(components) == 1
                    else ""
                ),
                "RootKeyTotalBearing_kN": root_key_total,
                "RootKeyAverageBearing_kN": (
                    root_key_total / len(key_ids)
                    if key_ids
                    else 0.0
                ),
                "PileShaftFrictionSigned_kN": (
                    shaft_signed / 1000.0
                    if shaft_signed is not None and len(components) == 1
                    else ""
                ),
                "PileShaftFriction_kN": shaft_friction,
                "UnresolvedResistance_kN": unresolved,
                "RootKeyShare_percent": (
                    100.0 * root_key_total / denominator
                    if denominator
                    else 0.0
                ),
                "PileShaftShare_percent": (
                    100.0 * shaft_friction / denominator
                    if denominator
                    else 0.0
                ),
                "UnresolvedShare_percent": (
                    100.0 * unresolved / denominator
                    if denominator
                    else 0.0
                ),
                "ContactHistoryStatus": (
                    "aligned"
                    if contact_row
                    else "missing_for_sequence"
                ),
            }
        )
        for column in passthrough_history_columns:
            row[column] = contact_row.get(column, "")
        processed_rows.append(row)

    target = history_output_dir / "load_resistance_processed.csv"
    write_csv(target, processed_rows)
    notes = {
        "result_file": target.name,
        "purpose": "桩顶荷载在根键、桩侧摩阻及尚未单独解析部分之间的初步分担结果",
        "units": {
            "displacement": "mm",
            "force": "kN",
            "share": "%",
        },
        "sign_convention": {
            "HistorySigned": "保留 Abaqus History Output 原始方向；当前竖向根键与桩侧接触通常为负值",
            "Bearing_or_Friction": "用于承载分担的正值大小，取相应历史力的绝对值或多方向合力",
            "PileTopReaction": "桩顶反力大小；PileTopReactionSigned_kN 保留单方向符号",
        },
        "formulas": {
            "RootKeyTotalBearing_kN": "各 Key*Bearing_kN 之和",
            "RootKeyAverageBearing_kN": "RootKeyTotalBearing_kN / RootKeyCount",
            "UnresolvedResistance_kN": "PileTopReaction_kN - RootKeyTotalBearing_kN - PileShaftFriction_kN",
        },
        "warning": "UnresolvedResistance_kN 是力平衡余量，可能包含桩端阻力、未提取接触分量及数值误差，不能未经核查直接等同为桩端阻力。",
        "load_direction": load_direction,
        "root_key_count": len(key_ids),
        "root_key_ids": key_ids,
        "root_key_groups": key_groups,
        "unprocessed_history_columns": passthrough_history_columns,
        "unprocessed_history_rule": "保留 Abaqus 原始 History Output 名称和值；不换算单位，不参加根键合力或分担比例计算。",
    }
    (history_output_dir / "load_resistance_notes.json").write_text(
        json.dumps(notes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def plot_load_resistance(output_dir: Path) -> Path | None:
    history_output_dir = output_dir / "History_Output"
    source = history_output_dir / "load_resistance_processed.csv"
    if not source.exists():
        return None
    rows = read_csv(source)
    if not rows:
        return None
    displacement = [
        float(row["PileTopDisplacementAbs_mm"])
        for row in rows
    ]
    history_output_dir.mkdir(parents=True, exist_ok=True)
    target = history_output_dir / "load_resistance_sharing.png"
    fig, ax = plt.subplots(figsize=(8.6, 6.2), constrained_layout=True)
    for column, label in (
        ("PileTopReaction_kN", "Pile-top reaction"),
        ("RootKeyTotalBearing_kN", "Root-key total"),
        ("PileShaftFriction_kN", "Pile-shaft friction"),
        ("UnresolvedResistance_kN", "Unresolved / other"),
    ):
        ax.plot(
            displacement,
            [float(row[column]) for row in rows],
            label=label,
            linewidth=2.2 if column == "PileTopReaction_kN" else 1.7,
        )
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.grid(True, alpha=0.25)
    ax.set_xlabel("Pile-top displacement magnitude (mm)")
    ax.set_ylabel("Resistance (kN)")
    ax.legend()
    fig.savefig(target, dpi=180)
    plt.close(fig)
    return target


def build_xlsx(output_dir: Path) -> Path:
    target = output_dir/"summary.xlsx"; workbook = xlsxwriter.Workbook(target)
    title = workbook.add_format({"bold": True, "font_color": "white", "bg_color": "#134E4A", "font_size": 16})
    header = workbook.add_format({"bold": True, "font_color": "white", "bg_color": "#0F766E", "border": 1})
    cell = workbook.add_format({"border": 1}); number = workbook.add_format({"border": 1, "num_format": "0.000000"})
    summary = workbook.add_worksheet("Summary"); summary.hide_gridlines(2); summary.merge_range("A1:F1", "Abaqus ODB postprocessing summary", title)
    summary.write_row("A3", ["Item", "Value"], header)
    metadata_path = output_dir/"metadata.json"; metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    entries = [("ODB", metadata.get("odb_path", "")), ("Timeline", "Selected SequenceIndex + StepName + FrameIndex + TotalTime"),
        ("Load sharing", "Pile-top reaction, individual root keys, root-key total/average, shaft friction, and unresolved balance; kN"),
        ("Rebar", "HRB400 + near-Z T3D2; per-element S11 × area"),
        ("Pile axial", "Concrete/pipe FreeBody + Z-interpolated rebar; compression positive"),
        ("Pile bending", "Global-1 lateral loading uses total My; rebar My = Σ(-xN)"),
        ("Rendering", "This workbook is group-independent; contour and GIF legends are stored with each comparison-group render")]
    for index, values in enumerate(entries, 3): summary.write_row(index, 0, values, cell)
    summary.set_column("A:A", 24); summary.set_column("B:B", 90)
    sheets = [("Timeline", output_dir/"data"/"timeline_alignment.csv"),
        ("Load_Sharing", output_dir/"History_Output"/"load_resistance_processed.csv"),
        ("Contact_Raw", output_dir/"History_Output"/"contact_history_raw.csv"),
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


def finalize_numeric_output(output_dir: Path) -> dict:
    load_resistance_csv = build_load_resistance_table(output_dir)
    moment_csv, maxima_csv = build_pile_force_moment(output_dir)
    manifest = {"pile_axial_plot": str(plot_pile_axial(output_dir) or ""),
        "load_resistance_csv": str(load_resistance_csv or ""),
        "load_resistance_plot": str(plot_load_resistance(output_dir) or ""),
        "pile_bending_plots": [str(path) for path in plot_pile_bending(output_dir)],
        "pile_force_moment_csv": str(moment_csv or ""), "pile_bending_maxima_csv": str(maxima_csv or ""),
        "xlsx": str(build_xlsx(output_dir))}
    (output_dir/"numeric_postprocess_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    hide_internal_result_json_files(output_dir)
    return manifest


def finalize_render_output(
    output_dir: Path,
    fps: int = 5,
    *,
    export_white_background_png: bool = True,
    export_transparent_background_png: bool = True,
) -> dict:
    if not export_white_background_png and not export_transparent_background_png:
        raise ValueError("At least one PNG background output must be enabled")
    white_png_count = count_render_pngs(output_dir, ("frames", "contours"))
    transparent_png_count = (
        build_transparent_backgrounds(output_dir)
        if export_transparent_background_png
        else 0
    )
    if not export_transparent_background_png:
        remove_render_png_directories(
            output_dir, ("frames_transparent", "contours_transparent")
        )
    animations = build_gifs(output_dir, fps)
    if not export_white_background_png:
        remove_render_png_directories(output_dir, ("frames", "contours"))
    manifest = {"white_png_count": white_png_count if export_white_background_png else 0,
        "transparent_png_count": transparent_png_count,
        "original_pngs_preserved": bool(export_white_background_png),
        "export_white_background_png": bool(export_white_background_png),
        "export_transparent_background_png": bool(export_transparent_background_png),
        "animations": animations}
    (output_dir/"render_postprocess_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    hide_internal_result_json_files(output_dir)
    return manifest


def finalize_output(
    output_dir: Path,
    fps: int = 5,
    *,
    export_white_background_png: bool = True,
    export_transparent_background_png: bool = True,
) -> dict:
    manifest = {**finalize_numeric_output(output_dir), **finalize_render_output(
        output_dir,
        fps,
        export_white_background_png=export_white_background_png,
        export_transparent_background_png=export_transparent_background_png,
    )}
    (output_dir/"host_postprocess_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    hide_internal_result_json_files(output_dir)
    return manifest
