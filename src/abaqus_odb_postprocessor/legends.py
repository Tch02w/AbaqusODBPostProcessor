from __future__ import annotations

from collections.abc import Iterable
from typing import Any


DAMAGE_SPECS = {"PILE_CON_DAMAGET", "PILE_CON_DAMAGEC"}
DAMAGE_DISPLAY_MIN = 0.0


def parse_sequence_expression(text: str, available: Iterable[int]) -> list[int]:
    """Parse ``1,3,5-8`` against the canonical sequence-index catalog."""
    allowed = {int(value) for value in available}
    selected: set[int] = set()
    for raw_token in text.replace("，", ",").split(","):
        token = raw_token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start, end = int(left.strip()), int(right.strip())
            if start > end:
                start, end = end, start
            selected.update(range(start, end + 1))
        else:
            selected.add(int(token))
    missing = sorted(selected - allowed)
    if missing:
        raise ValueError(f"时程序号不存在：{missing}")
    if not selected:
        raise ValueError("手动选择帧不能为空")
    return sorted(selected)


def choose_sequences(scan: dict[str, Any], mode: str, expression: str = "") -> list[int]:
    catalog = [int(item["SequenceIndex"]) for item in scan["frame_catalog"]]
    if not catalog:
        raise ValueError("所选 Step 范围没有可用帧")
    if mode == "all":
        return catalog
    if mode == "manual":
        return parse_sequence_expression(expression, catalog)
    if mode == "auto":
        chosen = [catalog[-1]]
        prefracture = scan.get("auto_detection", {}).get("prefracture_sequence_index")
        if prefracture is not None:
            chosen.append(int(prefracture))
        return sorted(set(chosen))
    raise ValueError(f"未知帧模式：{mode}")


def aggregate_group_ranges(jobs: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Build per-comparison-group limits from only the selected frames.

    DAMAGEC and DAMAGET retain the specified ten-color spectrum, while their
    numerical range is independently scaled from zero to the observed maximum
    of the active comparison group.  Different groups are never mixed.
    """
    observed: dict[str, dict[str, list[float]]] = {}
    for job in jobs:
        group = str(job["comparison_group"]).strip() or "默认组"
        selected = {int(value) for value in job["selected_sequence_indices"]}
        group_ranges = observed.setdefault(group, {})
        for frame in job["range_scan"]["frame_catalog"]:
            if int(frame["SequenceIndex"]) not in selected:
                continue
            for spec, limits in frame.get("ranges", {}).items():
                current = group_ranges.setdefault(spec, [float("inf"), float("-inf")])
                current[0] = min(current[0], float(limits["min"]))
                current[1] = max(current[1], float(limits["max"]))

    plans: dict[str, dict[str, dict[str, Any]]] = {}
    for group, fields in observed.items():
        plan: dict[str, dict[str, Any]] = {}
        for spec, (minimum, maximum) in fields.items():
            observed_min, observed_max = minimum, maximum
            if spec in DAMAGE_SPECS:
                minimum = DAMAGE_DISPLAY_MIN
                maximum = max(observed_max, 1.0e-12)
                source = "comparison_group_damage_max_selected_frames"
            else:
                source = "comparison_group_selected_frames"
                if minimum == maximum:
                    padding = max(abs(minimum), 1.0) * 1.0e-9
                    minimum -= padding
                    maximum += padding
            plan[spec] = {
                "min": minimum,
                "max": maximum,
                "observed_min": observed_min,
                "observed_max": observed_max,
                "source": source,
                "comparison_group": group,
            }
        plans[group] = plan
    return plans


def aggregate_animation_ranges(
    range_scan: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Fix one ODB animation to its observed range across every rendered frame."""

    observed: dict[str, list[float]] = {}
    for frame in range_scan.get("frame_catalog", []):
        for spec, limits in frame.get("ranges", {}).items():
            current = observed.setdefault(
                str(spec), [float("inf"), float("-inf")]
            )
            current[0] = min(current[0], float(limits["min"]))
            current[1] = max(current[1], float(limits["max"]))

    result: dict[str, dict[str, Any]] = {}
    for spec, (minimum, maximum) in observed.items():
        observed_min = minimum
        observed_max = maximum
        if minimum == maximum:
            padding = max(abs(minimum), 1.0) * 1.0e-9
            minimum -= padding
            maximum += padding
        result[spec] = {
            "min": minimum,
            "max": maximum,
            "observed_min": observed_min,
            "observed_max": observed_max,
            "source": "odb_full_animation_timeline",
        }
    return result
