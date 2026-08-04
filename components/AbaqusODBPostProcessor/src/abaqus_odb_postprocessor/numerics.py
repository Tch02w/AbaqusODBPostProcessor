from __future__ import annotations

import bisect
from collections.abc import Sequence


def interpolate_rebar_force(
    points: Sequence[tuple[float, float]],
    elevation: float,
    physical_z_min: float,
    physical_z_max: float,
) -> tuple[float, str]:
    """Interpolate a rebar resultant onto a concrete FreeBody elevation."""
    if not points:
        raise ValueError("At least one rebar element-level point is required")
    ordered = sorted((float(z), float(force)) for z, force in points)
    if elevation < physical_z_min or elevation > physical_z_max:
        return 0.0, "outside_rebar_extent_zero"
    elevations = [item[0] for item in ordered]
    if elevation <= elevations[0]:
        return ordered[0][1], "end_element_constant"
    if elevation >= elevations[-1]:
        return ordered[-1][1], "end_element_constant"
    right = bisect.bisect_right(elevations, elevation)
    z0, force0 = ordered[right - 1]
    z1, force1 = ordered[right]
    ratio = (elevation - z0) / (z1 - z0)
    return force0 + ratio * (force1 - force0), "linear_between_element_centroids"

