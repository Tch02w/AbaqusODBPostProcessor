"""GUI entry that recommends SET-KEY as the RC steel/root-key stress set."""

from __future__ import annotations

from . import app_base_v2 as _base
from .models import normalized_name


_base.MainWindow.columns[12] = "钢构件/根键集合"
_original_populate = _base.MainWindow._populate


def _populate_with_component_default(self, scans):
    _original_populate(self, scans)
    cf_name = normalized_name(self.defaults.get("default_cf_steel_set", "SETPILESTEEL"))
    key_name = normalized_name(self.defaults.get("default_steel_component_set", "SET-KEY"))
    for row in self.row_widgets:
        candidates = row["scan"].assembly_element_sets
        selected = next((name for name in candidates if normalized_name(name) == cf_name), "")
        if not selected:
            selected = next((name for name in candidates if normalized_name(name) == key_name), "")
        combo = row["steel"]
        index = combo.findText(selected)
        if index >= 0:
            combo.setCurrentIndex(index)


_base.MainWindow._populate = _populate_with_component_default
MainWindow = _base.MainWindow
FunctionThread = _base.FunctionThread
FRAME_MODE_LABELS = _base.FRAME_MODE_LABELS


def main() -> int:
    return _base.main()


if __name__ == "__main__":
    raise SystemExit(main())
