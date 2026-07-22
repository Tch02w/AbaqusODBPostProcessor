from __future__ import annotations

import csv, json, math
from collections import defaultdict
from pathlib import Path

from .postprocess_base_v2 import *  # noqa: F401,F403
from .postprocess_base_v2 import (
    build_gifs, build_xlsx, normalize_white_backgrounds, plot_pile_axial,
    plot_pile_bending, read_csv, write_csv, interpolate_components,
)


def build_pile_force_moment(output_dir: Path) -> tuple[Path | None, Path | None]:
    """Combine FreeBody and rebar using one consistent tension-positive cut sign.

    The Abaqus FreeBody Fz/Mx/My values are kept in their raw cut convention.
    T3D2 S11*A is tension positive.  Compression-positive axial columns are the
    exact negatives of the resulting tension-positive quantities.
    """
    axial_path = output_dir/"freebody"/"pile_total_axial_force_time_aligned.csv"
    rebar_path = output_dir/"rebar"/"rebar_element_stress_force_timehistory.csv"
    metadata_path = output_dir/"rebar"/"rebar_metadata.json"
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
            tension_force = float(row["ElementAxialForce_TensionPositive_N"])
            current = by_level[sequence].setdefault(z, [0.0, 0.0, 0.0])
            current[0] += tension_force
            current[1] += y*tension_force
            current[2] += -x*tension_force
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")); z_min, z_max = map(float, metadata["rebar_z_extent_mm"])
    points_by_sequence = {sequence: [(z, values[0], values[1], values[2]) for z, values in sorted(levels.items())]
                          for sequence, levels in by_level.items()}

    corrected_axial, combined = [], []
    for source in axial_rows:
        sequence = int(float(source["SequenceIndex"])); elevation = float(source["Elevation_mm"])
        rebar_tension, rebar_mx, rebar_my, rule = interpolate_components(
            points_by_sequence[sequence], elevation, z_min, z_max
        )
        concrete_tension = float(source["Fz_N"])
        total_tension = concrete_tension + rebar_tension
        axial = dict(source)
        axial["ConcreteAxial_CompressionPositive_N"] = -concrete_tension
        axial["RebarAxial_Interpolated_CompressionPositive_N"] = -rebar_tension
        axial["PileTotalAxial_CompressionPositive_N"] = -total_tension
        axial.update({"ConcreteAxial_TensionPositive_N": concrete_tension,
            "RebarAxial_Interpolated_TensionPositive_N": rebar_tension,
            "PileTotalAxial_TensionPositive_N": total_tension})
        corrected_axial.append(axial)

        concrete_mx, concrete_my = float(source["Mx_Nmm"]), float(source["My_Nmm"])
        total_mx, total_my = concrete_mx+rebar_mx, concrete_my+rebar_my
        row = dict(axial)
        row.update({"ConcreteFreeBody_Fz_TensionPositive_N": concrete_tension,
            "RebarAxial_Check_TensionPositive_N": rebar_tension,
            "ConcreteBendingMoment_Mx_Nmm": concrete_mx, "ConcreteBendingMoment_My_Nmm": concrete_my,
            "RebarBendingMoment_Interpolated_Mx_Nmm": rebar_mx,
            "RebarBendingMoment_Interpolated_My_Nmm": rebar_my,
            "PileTotalBendingMoment_Mx_Nmm": total_mx, "PileTotalBendingMoment_My_Nmm": total_my,
            "PileTotalBendingMoment_Resultant_Nmm": math.hypot(total_mx, total_my),
            "BendingMomentInterpolationRule": rule})
        combined.append(row)
    write_csv(axial_path, corrected_axial)
    target = output_dir/"freebody"/"pile_total_force_moment_time_aligned.csv"; write_csv(target, combined)

    maxima = []; groups = defaultdict(list)
    for row in combined: groups[int(float(row["SequenceIndex"]))].append(row)
    for sequence, rows in sorted(groups.items()):
        my_row = max(rows, key=lambda item: abs(float(item["PileTotalBendingMoment_My_Nmm"])))
        resultant_row = max(rows, key=lambda item: float(item["PileTotalBendingMoment_Resultant_Nmm"]))
        keys = ("SequenceIndex", "StepIndex", "StepName", "FrameIndex", "IncrementNumber", "StepTime", "TotalTime", "Status")
        summary = {key: my_row[key] for key in keys if key in my_row}
        summary.update({"MaxAbsPileTotalMy_Nmm": abs(float(my_row["PileTotalBendingMoment_My_Nmm"])),
            "SignedPileTotalMyAtMaximum_Nmm": float(my_row["PileTotalBendingMoment_My_Nmm"]),
            "DepthAtMaxAbsMy_mm": float(my_row["DepthFromGround_mm"]),
            "MaxPileBendingResultant_Nmm": float(resultant_row["PileTotalBendingMoment_Resultant_Nmm"]),
            "DepthAtMaxResultant_mm": float(resultant_row["DepthFromGround_mm"])})
        maxima.append(summary)
    maxima_path = output_dir/"freebody"/"pile_bending_moment_maxima.csv"; write_csv(maxima_path, maxima)
    return target, maxima_path


def finalize_output(output_dir: Path, fps: int = 5) -> dict:
    moment_csv, maxima_csv = build_pile_force_moment(output_dir)
    manifest = {"normalized_png_count": normalize_white_backgrounds(output_dir),
        "animations": build_gifs(output_dir, fps), "pile_axial_plot": str(plot_pile_axial(output_dir) or ""),
        "pile_bending_plots": [str(path) for path in plot_pile_bending(output_dir)],
        "pile_force_moment_csv": str(moment_csv or ""), "pile_bending_maxima_csv": str(maxima_csv or ""),
        "xlsx": str(build_xlsx(output_dir))}
    (output_dir/"host_postprocess_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
