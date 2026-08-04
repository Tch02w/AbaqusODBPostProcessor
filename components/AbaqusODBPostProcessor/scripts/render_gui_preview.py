from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from abaqus_odb_postprocessor.app import MainWindow
from abaqus_odb_postprocessor.models import OdbScan


def make_scan(path: Path, steps: list[str]) -> OdbScan:
    return OdbScan(
        path=path,
        steps=steps,
        assembly_node_sets=["SET-LOAD"],
        assembly_element_sets=[
            "SET-PILE", "SET-PILE_CON", "SET-KEY", "SET-SOIL_CUT", "SET-REBAR"
        ],
        field_outputs=["U", "S", "PEEQ", "PEMAG", "DAMAGET", "DAMAGEC"],
    )


target = Path(r"G:\PythonProject\AbaqusODBPostProcessor\runs\gui_preview_v1")
target.mkdir(parents=True, exist_ok=True)
application = QApplication([])
window = MainWindow()
window.state_path = target / "preview_state.json"
folder = Path(r"G:\Job\GJA_ODB")
scans = [
    make_scan(folder / "GJA-31_U20D.odb", ["U10D", "U20D"]),
    make_scan(folder / "GJA-32_U20D_V20D.odb", ["U10D", "U20D", "V10D", "V20D"]),
]
window._load_folder_state(str(folder))
window._populate(scans)
paths = [str(item.path.resolve()) for item in scans]
window.groups = {
    "group-uplift": {
        "name": "上拔对比组",
        "members": paths,
        "legend_overrides": {},
    },
    "group-lateral": {
        "name": "水平加载对比组",
        "members": [paths[1]],
        "legend_overrides": {
            "SOIL_PEMAG_XZ": {"mode": "manual", "min": 0.0, "max": 0.006}
        },
    },
}
window._rebuild_tree("group-uplift")
window._update_membership_labels()
window._append_log("SCAN_DISCOVERED|37")
window._append_log("SCAN_START|12|37|GJA-32_U20D_V20D.odb")
window.scan_started_at = 0.0
window.elapsed_label.setText("用时 00:01:26")
window.show()
application.processEvents()
window.grab().save(str(target / "comparison_group_gui.png"))
print(target / "comparison_group_gui.png")
window.close()
