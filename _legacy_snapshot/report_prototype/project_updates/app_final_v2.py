"""GUI entry with safe component and near-field soil defaults."""

from __future__ import annotations

from . import app_base_v2 as _base
from .models import normalized_name


_base.MainWindow.columns[12] = "钢构件/根键集合"
_base.MainWindow.columns[13] = "土体剖面集合"
_original_populate = _base.MainWindow._populate


def _exact(candidates, target):
    normalized = normalized_name(target)
    return next((name for name in candidates if normalized_name(name) == normalized), "")


def _populate_with_safe_defaults(self, scans):
    _original_populate(self, scans)
    for row in self.row_widgets:
        candidates = row["scan"].assembly_element_sets
        steel = _exact(candidates, self.defaults.get("default_cf_steel_set", "SETPILESTEEL"))
        if not steel:
            steel = _exact(candidates, self.defaults.get("default_steel_component_set", "SET-KEY"))
        steel_index = row["steel"].findText(steel)
        if steel_index >= 0:
            row["steel"].setCurrentIndex(steel_index)

        soil = _exact(candidates, self.defaults.get("default_soil_set", "SET-SOIL_IN"))
        if not soil:
            soil = _exact(candidates, self.defaults.get("default_soil_fallback_set", "SET-SOIL_FULL"))
        soil_index = row["soil"].findText(soil)
        if soil_index >= 0:
            row["soil"].setCurrentIndex(soil_index)


_base.MainWindow._populate = _populate_with_safe_defaults
MainWindow = _base.MainWindow
FunctionThread = _base.FunctionThread
FRAME_MODE_LABELS = _base.FRAME_MODE_LABELS


def main() -> int:
    return _base.main()


if __name__ == "__main__":
    raise SystemExit(main())
