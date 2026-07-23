"""Enhanced comparison-group UI, naming defaults, and parallel ODB batches."""

from __future__ import annotations

import copy
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QWidget,
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
        self.group_tree.setColumnCount(1)
        header = self.group_tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)

        option_layout = self.centralWidget().layout().itemAt(2).layout()
        self.parallel_label = QLabel("并行任务数")
        self.parallel_label.setToolTip("同时启动的独立 Abaqus ODB 读取或后处理进程数")
        option_layout.insertWidget(option_layout.count() - 2, self.parallel_label)
        self.parallel_workers = QSpinBox()
        self.parallel_workers.setObjectName("parallelWorkers")
        self.parallel_workers.setRange(1, 4)
        self.parallel_workers.setSuffix(" 个")
        self.parallel_workers.setAlignment(Qt.AlignCenter)
        self.parallel_workers.setAccelerated(True)
        self.parallel_workers.setKeyboardTracking(False)
        self.parallel_workers.setMinimumWidth(118)
        self.parallel_workers.setMaximumWidth(136)
        self.parallel_workers.setAccessibleName("并行任务数")
        self.parallel_workers.setAccessibleDescription(
            "可选择 1 到 4 个并行 Abaqus ODB 读取或后处理进程，建议使用 2 个。"
        )
        saved_workers = int(
            self.state.get(
                "parallel_odb_workers",
                self.defaults.get("parallel_odb_workers", 2),
            )
        )
        self.parallel_workers.setValue(max(1, min(saved_workers, 4)))
        self.parallel_workers.valueChanged.connect(
            self._parallel_worker_count_changed
        )
        option_layout.insertWidget(option_layout.count() - 2, self.parallel_workers)
        self.parallel_hint = QLabel("范围 1–4，建议 2")
        self.parallel_hint.setProperty("role", "hint")
        option_layout.insertWidget(option_layout.count() - 2, self.parallel_hint)
        self._update_parallel_worker_tooltip(self.parallel_workers.value())
        self.cancel_run_button = QPushButton("取消当前批次")
        self.cancel_run_button.setProperty("danger", True)
        self.cancel_run_button.setEnabled(False)
        self.cancel_run_button.clicked.connect(self._cancel_run)
        option_layout.insertWidget(option_layout.count() - 2, self.cancel_run_button)

        output_panel = QWidget()
        output_panel.setObjectName("outputImageSettings")
        output_layout = QHBoxLayout(output_panel)
        output_layout.setContentsMargins(10, 4, 10, 4)
        output_layout.setSpacing(8)
        output_title = QLabel("输出图像")
        output_title.setProperty("role", "title")
        output_layout.addWidget(output_title)
        output_layout.addWidget(QLabel("横向"))
        self.image_width_input = QSpinBox()
        self.image_width_input.setObjectName("imageWidth")
        self._configure_image_dimension_input(self.image_width_input)
        output_layout.addWidget(self.image_width_input)
        output_layout.addWidget(QLabel("纵向"))
        self.image_height_input = QSpinBox()
        self.image_height_input.setObjectName("imageHeight")
        self._configure_image_dimension_input(self.image_height_input)
        output_layout.addWidget(self.image_height_input)
        output_layout.addWidget(QLabel("单位"))
        self.image_unit_selector = QComboBox()
        self.image_unit_selector.view().setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.image_unit_selector.setMinimumWidth(92)
        self.image_unit_selector.addItem("pixel", "px")
        self.image_unit_selector.addItem("mm", "mm")
        output_layout.addWidget(self.image_unit_selector)
        self.image_ratio_label = QLabel()
        self.image_ratio_label.setProperty("role", "hint")
        output_layout.addWidget(self.image_ratio_label)
        output_hint = QLabel("按此比例创建最大 Abaqus Viewport")
        output_hint.setProperty("role", "hint")
        output_layout.addWidget(output_hint)
        output_layout.addStretch(1)
        saved_unit = str(
            self.state.get(
                "image_size_unit",
                self.defaults.get("image_size_unit", "px"),
            )
        )
        if saved_unit not in ("px", "mm"):
            saved_unit = "px"
        saved_width = int(
            self.state.get(
                "image_width",
                self.state.get(
                    "image_width_px",
                    self.defaults.get("image_width", 1500),
                ),
            )
        )
        saved_height = int(
            self.state.get(
                "image_height",
                self.state.get(
                    "image_height_px",
                    self.defaults.get("image_height", 1000),
                ),
            )
        )
        self._current_image_unit = saved_unit
        self.image_unit_selector.setCurrentIndex(
            self.image_unit_selector.findData(saved_unit)
        )
        self._apply_image_unit(saved_unit, saved_width, saved_height)
        self.image_width_input.valueChanged.connect(self._image_size_changed)
        self.image_height_input.valueChanged.connect(self._image_size_changed)
        self.image_unit_selector.currentIndexChanged.connect(
            self._image_unit_changed
        )
        self._update_image_ratio()
        self.centralWidget().layout().insertWidget(3, output_panel)

    @staticmethod
    def _configure_image_dimension_input(control: QSpinBox) -> None:
        control.setAlignment(Qt.AlignCenter)
        control.setKeyboardTracking(False)
        control.setMinimumWidth(132)
        control.setMaximumWidth(150)

    def _apply_image_unit(self, unit: str, width: int, height: int) -> None:
        for control in (self.image_width_input, self.image_height_input):
            control.blockSignals(True)
            if unit == "mm":
                control.setRange(30, 500)
                control.setSingleStep(10)
                control.setSuffix(" mm")
                control.setToolTip(
                    "Abaqus Viewport 的实际毫米尺寸，范围 30–500；"
                    "最大可用值受当前屏幕限制。"
                )
            else:
                control.setRange(320, 4096)
                control.setSingleStep(100)
                control.setSuffix(" px")
                control.setToolTip(
                    "最终 PNG 的像素尺寸，范围 320–4096；"
                    "Viewport 会自动采用相同宽高比。"
                )
        self.image_width_input.setValue(width)
        self.image_height_input.setValue(height)
        self.image_width_input.blockSignals(False)
        self.image_height_input.blockSignals(False)

    def _image_unit_changed(self, _index: int) -> None:
        new_unit = str(self.image_unit_selector.currentData())
        old_unit = self._current_image_unit
        width = int(self.image_width_input.value())
        height = int(self.image_height_input.value())
        if old_unit == "px" and new_unit == "mm":
            width = round(width * 25.4 / 96.0)
            height = round(height * 25.4 / 96.0)
        elif old_unit == "mm" and new_unit == "px":
            width = round(width * 96.0 / 25.4)
            height = round(height * 96.0 / 25.4)
        self._current_image_unit = new_unit
        self._apply_image_unit(new_unit, width, height)
        self._image_size_changed()

    def _update_image_ratio(self) -> None:
        width = int(self.image_width_input.value())
        height = int(self.image_height_input.value())
        divisor = math.gcd(width, height)
        self.image_ratio_label.setText(
            f"比例 {width // divisor}:{height // divisor}"
        )

    def _image_size_changed(self, *_args) -> None:
        self._update_image_ratio()
        self.state["image_size_unit"] = self._current_image_unit
        self.state["image_width"] = int(self.image_width_input.value())
        self.state["image_height"] = int(self.image_height_input.value())
        save_json(self.state_path, self.state)

    def _update_parallel_worker_tooltip(self, workers: int) -> None:
        descriptions = {
            1: "串行处理，资源占用最低，稳定性最好。",
            2: "推荐设置，在速度、许可证和内存占用之间较均衡。",
            3: "较高并发，请确认许可证和内存充足。",
            4: "最高并发，资源占用较高，仅建议在硬件和许可证充足时使用。",
        }
        tooltip = (
            f"当前最多同时启动 {workers} 个独立 Abaqus ODB 读取或 CAE 后处理进程。\n"
            f"{descriptions[workers]}\n"
            "如果待处理 ODB 少于该数值，程序会自动采用实际 ODB 数量。"
        )
        self.parallel_label.setToolTip(tooltip)
        self.parallel_workers.setToolTip(tooltip)
        self.parallel_hint.setToolTip(tooltip)

    def _parallel_worker_count_changed(self, workers: int) -> None:
        self._update_parallel_worker_tooltip(int(workers))
        self.state["parallel_odb_workers"] = int(workers)
        save_json(self.state_path, self.state)

    def _set_busy(self, busy: bool) -> None:
        super()._set_busy(busy)
        if hasattr(self, "parallel_workers"):
            self.parallel_workers.setEnabled(not busy)
        if hasattr(self, "image_width_input"):
            self.image_width_input.setEnabled(not busy)
            self.image_height_input.setEnabled(not busy)
            self.image_unit_selector.setEnabled(not busy)
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
        settings = dict(payload["settings"])
        settings["image_size_unit"] = self._current_image_unit
        settings["image_width"] = int(self.image_width_input.value())
        settings["image_height"] = int(self.image_height_input.value())
        payload["settings"] = settings
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
        """Build browse-only condition categories and migrate old auto-groups."""

        self.condition_categories = {}
        if self.folder_key:
            folder_state = self.state.setdefault("folders", {}).setdefault(
                self.folder_key, {}
            )
            folder_state.pop("auto_condition_grouped_paths", None)
        for group_id in [
            key for key, group in self.groups.items() if group.get("auto_condition")
        ]:
            del self.groups[group_id]
        for scan in scans:
            path = str(scan.path.resolve())
            info = parse_odb_name(scan.path.name)
            if not info.condition:
                continue
            condition_key = next(
                (
                    key
                    for key in self.condition_categories
                    if key.casefold() == info.condition.casefold()
                ),
                info.condition,
            )
            members = self.condition_categories.setdefault(condition_key, [])
            if path not in members:
                members.append(path)

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
            QMessageBox.information(
                self,
                "没有作业",
                "请选择单个 ODB 或用户创建的对比组；“全部 ODB”和工况分类仅用于浏览。",
            )
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
