from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .config import load_defaults, project_root, save_json
from .legends import aggregate_group_ranges, choose_sequences
from .models import OdbScan, choose_name
from .postprocess import finalize_output
from .runner import run_job, scan_field_ranges, scan_folder


FRAME_MODE_LABELS = {
    "关键帧（自动）": "auto",
    "手动选择": "manual",
    "全部帧": "all",
}


class FunctionThread(QThread):
    completed = Signal(object)
    failed = Signal(str)
    message = Signal(str)

    def __init__(self, function: Callable[[Callable[[str], None]], Any]) -> None:
        super().__init__(); self.function = function

    def run(self) -> None:
        try: self.completed.emit(self.function(self.message.emit))
        except Exception: self.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    columns = [
        "启用", "ODB", "对比组", "开始 Step", "结束 Step", "帧模式", "手动时程序号",
        "加载方向", "加载点集合", "桩型", "桩体显示集合", "混凝土集合", "钢管集合",
        "土体集合", "钢筋集合", "纵筋材料", "主筋直径/mm", "断裂前序号覆盖", "状态",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.defaults = load_defaults(); self.row_widgets: list[dict[str, Any]] = []; self.worker = None
        self.setWindowTitle("Abaqus ODB PostProcessor 0.2")
        self.resize(1900, 920); self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget(); self.setCentralWidget(central); layout = QVBoxLayout(central)
        folder_row = QHBoxLayout(); folder_row.addWidget(QLabel("ODB 文件夹"))
        self.folder_edit = QLineEdit(self.defaults["default_odb_folder"]); folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("浏览"); browse.clicked.connect(self._browse_folder); folder_row.addWidget(browse)
        self.scan_button = QPushButton("扫描 ODB"); self.scan_button.clicked.connect(self._scan); folder_row.addWidget(self.scan_button)
        layout.addLayout(folder_row)

        option_row = QHBoxLayout()
        self.full_timehistory = QCheckBox("全部帧均做 100 个 FreeBodyCut（非常慢）")
        self.full_timehistory.setChecked(bool(self.defaults["full_timehistory_freebody"])); option_row.addWidget(self.full_timehistory)
        option_row.addWidget(QLabel("自动/手动模式只对选中帧做 FreeBody；全部帧模式默认仍只做末帧与断裂前帧。"))
        option_row.addStretch(1)
        self.run_button = QPushButton("运行选中项目"); self.run_button.clicked.connect(self._run_selected); option_row.addWidget(self.run_button)
        layout.addLayout(option_row)

        self.table = QTableWidget(0, len(self.columns)); self.table.setHorizontalHeaderLabels(self.columns)
        self.table.setAlternatingRowColors(True); self.table.setSortingEnabled(False)
        widths = [50, 240, 95, 105, 105, 115, 130, 80, 120, 70, 125, 125, 115, 120, 120, 90, 105, 125, 135]
        for index, width in enumerate(widths): self.table.setColumnWidth(index, width)
        layout.addWidget(self.table, 1)
        layout.addWidget(QLabel("运行日志")); self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000); layout.addWidget(self.log, 0)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择 ODB 文件夹", self.folder_edit.text())
        if folder: self.folder_edit.setText(folder)

    def _set_busy(self, busy: bool) -> None:
        self.scan_button.setEnabled(not busy); self.run_button.setEnabled(not busy)

    def _append_log(self, text: str) -> None: self.log.appendPlainText(text)

    def _start_thread(self, function, completed) -> None:
        self._set_busy(True); self.worker = FunctionThread(function)
        self.worker.message.connect(self._append_log); self.worker.completed.connect(completed)
        self.worker.completed.connect(lambda _value: self._set_busy(False)); self.worker.failed.connect(self._thread_failed)
        self.worker.start()

    def _thread_failed(self, details: str) -> None:
        self._set_busy(False); self._append_log(details); QMessageBox.critical(self, "运行失败", details.splitlines()[-1])

    def _scan(self) -> None:
        folder = Path(self.folder_edit.text().strip())
        if not folder.is_dir(): QMessageBox.warning(self, "路径无效", "请选择存在的 ODB 文件夹。"); return
        cache = project_root() / "scan_cache"; self._append_log(f"扫描：{folder}")
        self._start_thread(lambda log: scan_folder(self.defaults["abaqus_command"], folder, cache, log), self._scan_finished)

    def _scan_finished(self, payload: dict) -> None:
        scans = [OdbScan.from_dict(item) for item in payload.get("odbs", [])]
        self._populate(scans); self._append_log(f"扫描完成：{len(scans)} 个 ODB")

    @staticmethod
    def _combo(values: list[str], selected: str = "", allow_blank: bool = False) -> QComboBox:
        combo = QComboBox()
        if allow_blank: combo.addItem("")
        combo.addItems(values); index = combo.findText(selected)
        if index >= 0: combo.setCurrentIndex(index)
        return combo

    def _populate(self, scans: list[OdbScan]) -> None:
        self.table.setRowCount(0); self.row_widgets.clear()
        for scan in scans:
            row_index = self.table.rowCount(); self.table.insertRow(row_index)
            enabled = QCheckBox(); enabled.setChecked(not bool(scan.error)); self.table.setCellWidget(row_index, 0, enabled)
            self.table.setItem(row_index, 1, QTableWidgetItem(scan.path.name))
            group = QLineEdit("对比组1")
            useful = [step for step in scan.steps if not any(token in step.upper() for token in ("GEO", "GRA"))]
            start = self._combo(scan.steps, useful[0] if useful else (scan.steps[0] if scan.steps else ""))
            end = self._combo(scan.steps, scan.steps[-1] if scan.steps else "")
            frame_mode = self._combo(list(FRAME_MODE_LABELS), "关键帧（自动）")
            manual_frames = QLineEdit(); manual_frames.setPlaceholderText("例：0,10,20-25")
            direction = self._combo(["1", "3", "1+3"], "1+3")
            load_set = self._combo(scan.assembly_node_sets, choose_name(scan.assembly_node_sets, self.defaults["default_load_set"]))
            pile_type = self._combo(["RC", "CFST"], "RC")
            pile_display = self._combo(scan.assembly_element_sets, choose_name(scan.assembly_element_sets, self.defaults["default_pile_set"]))
            concrete = self._combo(scan.assembly_element_sets, choose_name(scan.assembly_element_sets, self.defaults["default_concrete_set"]))
            steel = self._combo(scan.assembly_element_sets, choose_name(scan.assembly_element_sets, self.defaults["default_cf_steel_set"]), True)
            soil = self._combo(scan.assembly_element_sets, choose_name(scan.assembly_element_sets, self.defaults["default_soil_set"]))
            rebar = self._combo(scan.assembly_element_sets, choose_name(scan.assembly_element_sets, self.defaults["default_rebar_set"]))
            material = QLineEdit(self.defaults.get("default_longitudinal_material", "HRB400"))
            diameter = QDoubleSpinBox(); diameter.setRange(1.0, 100.0); diameter.setDecimals(2); diameter.setValue(32.0)
            prefracture = QSpinBox(); prefracture.setRange(-1, 100000); prefracture.setSpecialValueText("自动"); prefracture.setValue(-1)
            widgets = [group, start, end, frame_mode, manual_frames, direction, load_set, pile_type, pile_display,
                       concrete, steel, soil, rebar, material, diameter, prefracture]
            for column, widget in enumerate(widgets, 2): self.table.setCellWidget(row_index, column, widget)
            status = QTableWidgetItem(scan.error or "待运行"); self.table.setItem(row_index, 18, status)
            self.row_widgets.append({"scan": scan, "enabled": enabled, "group": group, "start_step": start,
                "end_step": end, "frame_mode": frame_mode, "manual_frames": manual_frames, "direction": direction,
                "load_set": load_set, "pile_type": pile_type, "pile_display": pile_display, "concrete": concrete,
                "steel": steel, "soil": soil, "rebar": rebar, "material": material, "diameter": diameter,
                "prefracture": prefracture, "status": status})

    def _job_payload(self, row: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        scan: OdbScan = row["scan"]
        return {"odb_path": str(scan.path), "output_dir": str(output_dir),
            "comparison_group": row["group"].text().strip() or "默认组",
            "start_step": row["start_step"].currentText(), "end_step": row["end_step"].currentText(),
            "frame_mode": FRAME_MODE_LABELS[row["frame_mode"].currentText()],
            "manual_sequence_expression": row["manual_frames"].text().strip(),
            "load_direction": row["direction"].currentText(), "load_set": row["load_set"].currentText(),
            "pile_type": row["pile_type"].currentText(), "pile_display_set": row["pile_display"].currentText(),
            "pile_concrete_set": row["concrete"].currentText(), "pile_steel_set": row["steel"].currentText(),
            "soil_set": row["soil"].currentText(), "rebar_set": row["rebar"].currentText(),
            "longitudinal_material": row["material"].text().strip(), "rebar_diameter_mm": row["diameter"].value(),
            "prefracture_sequence_index": row["prefracture"].value(),
            "full_timehistory_freebody": self.full_timehistory.isChecked(), "settings": self.defaults}

    def _run_selected(self) -> None:
        selected = [row for row in self.row_widgets if row["enabled"].isChecked()]
        if not selected: QMessageBox.information(self, "没有作业", "请先扫描并勾选至少一个 ODB。"); return
        run_root = project_root() / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S")

        def task(log: Callable[[str], None]) -> list[str]:
            prepared = []
            for row in selected:
                scan: OdbScan = row["scan"]; output_dir = run_root / scan.path.stem
                config_path = output_dir / "job_config.json"; payload = self._job_payload(row, output_dir)
                save_json(config_path, payload); log(f"预扫描场值与损伤：{scan.path.name}")
                range_scan = scan_field_ranges(self.defaults["abaqus_command"], config_path,
                                               output_dir / "frame_catalog_and_ranges.json", log)
                indices = choose_sequences(range_scan, payload["frame_mode"], payload["manual_sequence_expression"])
                override = int(payload["prefracture_sequence_index"])
                if payload["frame_mode"] == "auto" and override >= 0:
                    indices = sorted(set([indices[-1], override]))
                payload["selected_sequence_indices"] = indices; payload["range_scan"] = range_scan
                auto = range_scan.get("auto_detection", {}); detected = auto.get("prefracture_sequence_index")
                if override < 0: payload["prefracture_sequence_index"] = -1 if detected is None else int(detected)
                prepared.append({"row": row, "output_dir": output_dir, "config_path": config_path,
                                 "payload": payload, "range_scan": range_scan,
                                 "comparison_group": payload["comparison_group"], "selected_sequence_indices": indices})
                log(f"帧选择 {scan.path.name}: {indices}; 自动断裂前帧={detected}")

            plans = aggregate_group_ranges(prepared); save_json(run_root / "comparison_group_legends.json", plans)
            outputs = []
            for item in prepared:
                payload = item["payload"]; payload.pop("range_scan", None)
                payload["legend_ranges"] = plans[payload["comparison_group"]]
                save_json(item["config_path"], payload)
                log(f"开始正式提取：{Path(payload['odb_path']).name}；对比组={payload['comparison_group']}")
                run_job(self.defaults["abaqus_command"], item["config_path"], log)
                finalize_output(item["output_dir"], int(self.defaults["animation_fps"]))
                outputs.append(str(item["output_dir"])); log(f"完成：{item['output_dir']}")
            return outputs

        self._start_thread(task, self._run_finished)

    def _run_finished(self, outputs: list[str]) -> None:
        QMessageBox.information(self, "处理完成", "\n".join(outputs))


def main() -> int:
    application = QApplication(sys.argv); window = MainWindow(); window.show(); return application.exec()


if __name__ == "__main__": raise SystemExit(main())
