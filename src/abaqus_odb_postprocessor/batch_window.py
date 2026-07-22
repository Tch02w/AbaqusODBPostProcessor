"""Enhanced comparison-group UI, naming defaults, and parallel ODB batches."""

from __future__ import annotations

import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
)

from . import comparison_groups as _base
from .config import save_json
from .legends import aggregate_group_ranges, choose_sequences
from .naming import OdbNameInfo, parse_odb_name
from .postprocess import finalize_output
from .paths import batch_temp_dir, result_root_for_odb
from .runner_parallel import (
    MultiProcessController,
    ProcessCancelled,
    render_group_contours,
    run_job,
    scan_field_ranges,
)


AUTO_DIRECTION = "自动（文件名）"


class MainWindow(_base.MainWindow):
    def __init__(self) -> None:
        self.batch_controller: MultiProcessController | None = None
        self.batch_active = False
        self.batch_total = 0
        self.batch_completed = 0
        self._finalize_lock = threading.Lock()
        super().__init__()
        self.setWindowTitle("Abaqus ODB PostProcessor 0.5")

    def _build_ui(self) -> None:
        super()._build_ui()
        self.group_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.group_tree.setColumnCount(2)
        header = self.group_tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.group_tree.setColumnWidth(1, 42)

        option_layout = self.centralWidget().layout().itemAt(2).layout()
        option_layout.insertWidget(option_layout.count() - 2, QLabel("并行 ODB 进程数"))
        self.parallel_workers = QSpinBox()
        self.parallel_workers.setRange(1, 4)
        self.parallel_workers.setValue(int(self.defaults.get("parallel_odb_workers", 2)))
        self.parallel_workers.setToolTip(
            "每个工作单元启动独立的 Abaqus CAE 进程。受许可证、内存和磁盘速度限制，建议 2。"
        )
        option_layout.insertWidget(option_layout.count() - 2, self.parallel_workers)
        self.cancel_run_button = QPushButton("取消当前批次")
        self.cancel_run_button.setEnabled(False)
        self.cancel_run_button.clicked.connect(self._cancel_run)
        option_layout.insertWidget(option_layout.count() - 2, self.cancel_run_button)

    def _set_busy(self, busy: bool) -> None:
        super()._set_busy(busy)
        if hasattr(self, "parallel_workers"):
            self.parallel_workers.setEnabled(not busy)
        if hasattr(self, "cancel_run_button"):
            self.cancel_run_button.setEnabled(bool(busy and self.batch_active))

    @staticmethod
    def _name_info(row: dict[str, Any]) -> OdbNameInfo:
        return parse_odb_name(row["scan"].path.name)

    def _restore_row(self, row: dict[str, Any], values: dict[str, Any]) -> None:
        info = self._name_info(row)
        direction = row["direction"]
        if direction.findText(AUTO_DIRECTION) < 0:
            direction.insertItem(0, AUTO_DIRECTION)
        direction.setCurrentText(AUTO_DIRECTION)
        detected = info.load_direction or "未识别"
        direction.setToolTip(
            f"文件名识别：工况={info.condition or '未识别'}；Abaqus 加载方向={detected}。可手动覆盖。"
        )
        if info.rebar_diameter_mm is not None:
            row["diameter"].setValue(info.rebar_diameter_mm)
            row["diameter"].setToolTip(
                f"由样本编号 {info.sample_id} 自动匹配：{info.rebar_diameter_mm:g} mm；可手动修改。"
            )

        adjusted = dict(values)
        if not bool(adjusted.get("direction_manual", False)):
            adjusted.pop("direction", None)
        if not bool(adjusted.get("diameter_manual", False)):
            adjusted.pop("diameter", None)
        super()._restore_row(row, adjusted)

    def _serialize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = super()._serialize_row(row)
        info = self._name_info(row)
        payload.update(
            {
                "direction_manual": row["direction"].currentText() != AUTO_DIRECTION,
                "diameter_manual": (
                    info.rebar_diameter_mm is None
                    or abs(row["diameter"].value() - info.rebar_diameter_mm) > 1.0e-9
                ),
                "detected_sample_id": info.sample_id,
                "detected_condition": info.condition,
                "detected_reinforced": info.reinforced,
                "detected_parameter_tags": list(info.parameter_tags),
            }
        )
        return payload

    def _job_payload(self, row: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        payload = super()._job_payload(row, output_dir)
        info = self._name_info(row)
        automatic = row["direction"].currentText() == AUTO_DIRECTION
        payload["load_direction"] = (
            info.load_direction if automatic and info.load_direction else row["direction"].currentText()
        )
        if payload["load_direction"] == AUTO_DIRECTION:
            payload["load_direction"] = "1+3"
        payload.update(
            {
                "load_direction_source": "filename" if automatic and info.load_direction else "manual",
                "name_metadata": {
                    "sample_id": info.sample_id,
                    "family": info.family,
                    "scheme": info.scheme,
                    "reinforced": info.reinforced,
                    "condition": info.condition,
                    "parameter_tags": list(info.parameter_tags),
                    "is_old": info.is_old,
                    "up_displacement_mm": info.up_displacement_mm,
                    "lateral_displacement_mm": info.lateral_displacement_mm,
                },
            }
        )
        return payload

    def _populate(self, scans) -> None:
        super()._populate(scans)
        self._ensure_initial_condition_groups(scans)
        self._rebuild_tree()
        self._update_membership_labels()
        self._save_state()

    def _ensure_initial_condition_groups(self, scans) -> None:
        if not self.folder_key:
            return
        folder_state = self.state.setdefault("folders", {}).setdefault(self.folder_key, {})
        already_grouped = set(folder_state.setdefault("auto_condition_grouped_paths", []))
        changed = False
        for scan in scans:
            path = str(scan.path.resolve())
            if path in already_grouped:
                continue
            info = parse_odb_name(scan.path.name)
            if not info.condition:
                continue
            group_id = next(
                (
                    key
                    for key, group in self.groups.items()
                    if str(group.get("auto_condition", "")).casefold()
                    == info.condition.casefold()
                ),
                "",
            )
            if not group_id:
                group_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"abaqus-odb-condition|{self.folder_key}|{info.condition.casefold()}",
                ).hex
                self.groups[group_id] = {
                    "name": f"工况-{info.condition}",
                    "members": [],
                    "legend_overrides": {},
                    "auto_condition": info.condition,
                }
            members = self.groups[group_id].setdefault("members", [])
            if path not in members:
                members.append(path)
            already_grouped.add(path)
            changed = True
        if changed:
            folder_state["auto_condition_grouped_paths"] = sorted(already_grouped)

    def _new_tree_item(
        self,
        parent,
        text: str,
        kind: str,
        path: str = "",
        group_id: str = "",
    ):
        item = super()._new_tree_item(parent, text, kind, path, group_id)
        if kind == "odb":
            count = len(self._group_names_for_path(path))
            item.setText(1, str(count))
            item.setTextAlignment(1, Qt.AlignCenter)
            item.setBackground(1, QBrush(QColor("#E5E7EB")))
            item.setForeground(1, QBrush(QColor("#4B5563")))
            item.setToolTip(1, f"已加入 {count} 个对比组")
        return item

    def _cancel_run(self) -> None:
        if self.batch_controller is None or not self.batch_active:
            return
        self.scan_status.setText("正在取消当前后处理批次……")
        self.cancel_run_button.setEnabled(False)
        self.batch_controller.cancel()

    def _append_log(self, text: str) -> None:
        if text.startswith("BATCH_PROGRESS|"):
            _, phase, done, total, name = text.split("|", 4)
            self.batch_completed = int(done)
            self.scan_progress.setRange(0, max(int(total), 1))
            self.scan_progress.setValue(self.batch_completed)
            phase_label = "预扫描" if phase == "scan" else "正式提取"
            self.scan_status.setText(f"{phase_label} {done}/{total}：{name}")
            return
        super()._append_log(text)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _run_scope(self, scope: str) -> None:
        self._save_state()
        group_specs = [item for item in self._scope_groups(scope) if item["members"]]
        if not group_specs:
            QMessageBox.information(self, "没有作业", "当前范围没有已启用的 ODB。")
            return

        unique_paths: list[str] = []
        for group in group_specs:
            for path in group["members"]:
                if path in self.rows_by_path and path not in unique_paths:
                    unique_paths.append(path)
        snapshots = {
            path: self._job_payload(self.rows_by_path[path], Path("."))
            for path in unique_paths
        }
        batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        scratch_root = batch_temp_dir(batch_id)
        result_root = result_root_for_odb(Path(unique_paths[0]))
        workers = min(int(self.parallel_workers.value()), len(unique_paths))
        controller = MultiProcessController()
        self.batch_controller = controller
        self.batch_active = True
        self.batch_total = len(unique_paths)
        self.batch_completed = 0
        self.scan_progress.setRange(0, max(len(unique_paths), 1))
        self.scan_progress.setValue(0)
        self.scan_status.setText(f"准备并行预扫描：{len(unique_paths)} 个 ODB，{workers} 个进程")

        def task(log: Callable[[str], None]) -> list[str]:
            def check_cancelled() -> None:
                if controller.cancel_requested:
                    raise ProcessCancelled("Process cancelled by user")

            def tagged(path: str, message: str) -> None:
                log(f"[{self._timestamp()}] [{Path(path).name}] {message}")

            def prepare_one(path: str) -> tuple[str, dict[str, Any]]:
                check_cancelled()
                scan_dir = scratch_root / "prescan" / _base.safe_folder_name(Path(path).stem)
                payload = copy.deepcopy(snapshots[path])
                payload["output_dir"] = str(scan_dir)
                payload["comparison_group"] = "范围预扫描"
                config_path = scan_dir / "job_config.json"
                save_json(config_path, payload)
                tagged(path, "开始读取场值、帧目录与损伤范围")
                range_scan = scan_field_ranges(
                    self.defaults["abaqus_command"],
                    config_path,
                    scan_dir / "frame_catalog_and_ranges.json",
                    lambda line: log(f"[{Path(path).name}] {line}"),
                    controller,
                )
                indices = choose_sequences(
                    range_scan,
                    payload["frame_mode"],
                    payload["manual_sequence_expression"],
                )
                override = int(payload["prefracture_sequence_index"])
                if payload["frame_mode"] == "auto" and override >= 0:
                    indices = sorted(set([indices[-1], override]))
                detected = range_scan.get("auto_detection", {}).get("prefracture_sequence_index")
                if override < 0:
                    payload["prefracture_sequence_index"] = -1 if detected is None else int(detected)
                payload["selected_sequence_indices"] = indices
                tagged(path, f"帧选择={indices}；自动断裂前帧={detected}")
                return path, {
                    "payload": payload,
                    "range_scan": range_scan,
                    "selected_sequence_indices": indices,
                }

            prepared: dict[str, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="odb-prescan") as pool:
                futures = {pool.submit(prepare_one, path): path for path in unique_paths}
                for done, future in enumerate(as_completed(futures), 1):
                    path, item = future.result()
                    prepared[path] = item
                    log(f"BATCH_PROGRESS|scan|{done}|{len(unique_paths)}|{Path(path).name}")
            check_cancelled()

            group_ranges: dict[str, dict[str, Any]] = {}
            for group in group_specs:
                jobs = [
                    {
                        "comparison_group": group["name"],
                        "selected_sequence_indices": prepared[path]["selected_sequence_indices"],
                        "range_scan": prepared[path]["range_scan"],
                    }
                    for path in group["members"]
                ]
                plan = aggregate_group_ranges(jobs).get(str(group["name"]), {})
                self._apply_overrides(plan, group.get("overrides", {}), str(group["name"]))
                group_ranges[group["id"]] = plan
            save_json(
                result_root / "_批次记录" / batch_id / "comparison_group_legends.json",
                {
                    group["id"]: {"name": group["name"], "ranges": group_ranges[group["id"]]}
                    for group in group_specs
                },
            )

            def extract_one(path: str) -> list[str]:
                check_cancelled()
                memberships = [group for group in group_specs if path in group["members"]]
                primary_output: Path | None = None
                path_outputs: list[str] = []
                for membership_index, group in enumerate(memberships):
                    check_cancelled()
                    if group["standalone"]:
                        output_dir = result_root / "未分组" / batch_id / _base.safe_folder_name(Path(path).stem)
                    else:
                        output_dir = (
                            result_root
                            / _base.safe_folder_name(str(group["name"]))
                            / batch_id
                            / _base.safe_folder_name(Path(path).stem)
                        )
                    payload = copy.deepcopy(prepared[path]["payload"])
                    payload["output_dir"] = str(output_dir)
                    payload["comparison_group"] = str(group["name"])
                    payload["legend_ranges"] = group_ranges[group["id"]]
                    config_path = output_dir / "job_config.json"
                    if membership_index == 0:
                        save_json(config_path, payload)
                        tagged(path, f"开始正式提取；首组={group['name']}")
                        run_job(
                            self.defaults["abaqus_command"],
                            config_path,
                            lambda line: log(f"[{Path(path).name}] {line}"),
                            controller,
                        )
                        with self._finalize_lock:
                            finalize_output(output_dir, int(self.defaults["animation_fps"]))
                        primary_output = output_dir
                    else:
                        assert primary_output is not None
                        self._copy_numeric_cache(primary_output, output_dir)
                        payload["source_output_dir"] = str(primary_output)
                        save_json(config_path, payload)
                        tagged(path, f"复用数值并按组重渲染；组={group['name']}")
                        render_group_contours(
                            self.defaults["abaqus_command"],
                            config_path,
                            lambda line: log(f"[{Path(path).name}] {line}"),
                            controller,
                        )
                        with self._finalize_lock:
                            finalize_output(output_dir, int(self.defaults["animation_fps"]))
                    path_outputs.append(str(output_dir))
                tagged(path, "全部所属组处理完成")
                return path_outputs

            outputs: list[str] = []
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="odb-extract") as pool:
                futures = {pool.submit(extract_one, path): path for path in unique_paths}
                for done, future in enumerate(as_completed(futures), 1):
                    path = futures[future]
                    outputs.extend(future.result())
                    log(f"BATCH_PROGRESS|extract|{done}|{len(unique_paths)}|{Path(path).name}")
            check_cancelled()
            return outputs

        self._start_thread(task, self._run_finished)

    def _run_finished(self, outputs: list[str]) -> None:
        self.batch_active = False
        self.batch_controller = None
        self.scan_status.setText(f"后处理完成：{len(outputs)} 个组/ODB 结果")
        super()._run_finished(outputs)

    def _thread_failed(self, details: str) -> None:
        was_batch = self.batch_active
        cancelled = was_batch and "ProcessCancelled" in details
        self.batch_active = False
        self.batch_controller = None
        if cancelled:
            self._set_busy(False)
            self.scan_status.setText("当前后处理批次已取消")
            self._append_log(f"[{self._timestamp()}] 当前后处理批次已取消；已完成结果予以保留。")
            QMessageBox.information(self, "批次已取消", "当前后处理批次已停止，已完成的输出不会删除。")
            return
        super()._thread_failed(details)


FunctionThread = _base.FunctionThread
FRAME_MODE_LABELS = _base.FRAME_MODE_LABELS
ComparisonTree = _base.ComparisonTree
LegendRangeDialog = _base.LegendRangeDialog
safe_folder_name = _base.safe_folder_name


def main() -> int:
    return _base.main()


if __name__ == "__main__":
    raise SystemExit(main())

