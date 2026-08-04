"""Enhanced comparison-group UI, naming defaults, and parallel ODB batches."""

from __future__ import annotations

import copy
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
from .cache import (
    abaqus_cache_version,
    cache_entry_dir,
    load_json_cache,
    numeric_cache_is_valid,
    numeric_config_snapshot,
    prescan_config_snapshot,
    quick_odb_fingerprint,
    save_json_cache,
    stable_config_hash,
    write_numeric_cache_metadata,
)
from .config import save_json
from .file_attributes import ensure_windows_hidden
from .legends import (
    aggregate_group_animation_ranges,
    aggregate_group_ranges,
    choose_sequences,
)
from .naming import OdbNameInfo, natural_sort_key, parse_odb_name
from .postprocess import finalize_numeric_output, finalize_render_output
from .paths import batch_temp_dir, result_root_for_odb, scan_cache_dir
from .result_browser import (
    RESULT_ROOT_NAME,
    ResultBrowserDialog,
    ResultIndexCoordinator,
)
from .result_assets import (
    copy_numeric_payload,
    numeric_asset_dir,
    numeric_asset_is_valid,
    write_group_member_manifest,
    write_numeric_asset_manifest,
)
from .runner_parallel import (
    MultiProcessController,
    ProcessCancelled,
    render_group_contours,
    run_job,
    scan_field_ranges,
)


AUTO_DIRECTION = "自动"
UNRECOGNIZED_DIRECTION = "（未识别）"
DIRECTION_LABEL_TO_ABAQUS = {
    "X方向": "1",
    "Z方向": "3",
    "XZ方向": "1+3",
}
DIRECTION_ABAQUS_TO_LABEL = {
    value: label for label, value in DIRECTION_LABEL_TO_ABAQUS.items()
}


def automatic_direction_label(info: OdbNameInfo) -> str:
    detected = DIRECTION_ABAQUS_TO_LABEL.get(str(info.load_direction or ""))
    if detected:
        return f"自动（{detected}）"
    return UNRECOGNIZED_DIRECTION


class MainWindow(_base.MainWindow):
    def __init__(self) -> None:
        self.batch_controller: MultiProcessController | None = None
        self.batch_active = False
        self.batch_total = 0
        self.batch_completed = 0
        self.group_queue: list[dict[str, Any]] = []
        self.active_group_task: dict[str, Any] | None = None
        self.group_worker = None
        self._exit_after_cancel = False
        self._pending_queue_advance = False
        self._finalize_lock = threading.Lock()
        super().__init__()
        self.result_index_coordinator = ResultIndexCoordinator(self)
        self.result_index_coordinator.logMessage.connect(
            lambda text: self._append_log(
                f"[{self._timestamp()}] 结果索引：{text}"
            )
        )
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
        self.force_rescan_checkbox = QCheckBox("本次强制重新扫描")
        self.force_rescan_checkbox.setToolTip(
            "一次性选项：扫描 ODB 时重建所选 ODB 的基础缓存；"
            "运行时重建本次新入队组的范围/损伤预扫描缓存。操作提交后自动复位。"
        )
        option_layout.insertWidget(
            option_layout.count() - 2, self.force_rescan_checkbox
        )
        self.cancel_run_button = QPushButton("取消当前组")
        self.cancel_run_button.setProperty("danger", True)
        self.cancel_run_button.setEnabled(False)
        self.cancel_run_button.clicked.connect(self._cancel_run)
        option_layout.insertWidget(option_layout.count() - 2, self.cancel_run_button)
        self.cancel_queued_button = QPushButton("取消全部排队")
        self.cancel_queued_button.setProperty("danger", True)
        self.cancel_queued_button.setEnabled(False)
        self.cancel_queued_button.setToolTip(
            "清空所有尚未开始的对比组；当前运行组继续执行。"
        )
        self.cancel_queued_button.clicked.connect(self._cancel_queued_groups)
        option_layout.insertWidget(
            option_layout.count() - 2, self.cancel_queued_button
        )
        self.result_browser_button = QPushButton("结果浏览器")
        self.result_browser_button.setObjectName("resultBrowserButton")
        self.result_browser_button.setAccessibleName("打开结果浏览器")
        self.result_browser_button.setToolTip(
            "按荷载—位移、桩轴力、桩弯矩、钢筋、云图和动画等用途查找结果"
        )
        self.result_browser_button.clicked.connect(self._open_result_browser)
        folder_layout = self.centralWidget().layout().itemAt(0).layout()
        folder_layout.addWidget(self.result_browser_button)

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
        output_layout.addWidget(QLabel("云图导出"))
        self.export_white_background_checkbox = QCheckBox("白底 PNG")
        self.export_white_background_checkbox.setObjectName(
            "exportWhiteBackgroundPng"
        )
        self.export_white_background_checkbox.setToolTip(
            "保留 Abaqus 直接输出的白色背景 PNG；GIF 始终由该原始帧生成。"
        )
        output_layout.addWidget(self.export_white_background_checkbox)
        self.export_transparent_background_checkbox = QCheckBox("透明底 PNG")
        self.export_transparent_background_checkbox.setObjectName(
            "exportTransparentBackgroundPng"
        )
        self.export_transparent_background_checkbox.setToolTip(
            "额外生成去除近白背景的透明 PNG；不会改变云图颜色和图例。"
        )
        output_layout.addWidget(self.export_transparent_background_checkbox)
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
        export_white = bool(
            self.state.get(
                "export_white_background_png",
                self.defaults.get("export_white_background_png", True),
            )
        )
        export_transparent = bool(
            self.state.get(
                "export_transparent_background_png",
                self.defaults.get("export_transparent_background_png", True),
            )
        )
        if not export_white and not export_transparent:
            export_white = True
        self.export_white_background_checkbox.setChecked(export_white)
        self.export_transparent_background_checkbox.setChecked(
            export_transparent
        )
        self.image_width_input.valueChanged.connect(self._image_size_changed)
        self.image_height_input.valueChanged.connect(self._image_size_changed)
        self.image_unit_selector.currentIndexChanged.connect(
            self._image_unit_changed
        )
        self.export_white_background_checkbox.toggled.connect(
            self._background_output_changed
        )
        self.export_transparent_background_checkbox.toggled.connect(
            self._background_output_changed
        )
        self._update_image_ratio()
        self.centralWidget().layout().insertWidget(3, output_panel)

    def _consume_force_rescan(self) -> bool:
        enabled = bool(self.force_rescan_checkbox.isChecked())
        if enabled:
            self.force_rescan_checkbox.setChecked(False)
        return enabled

    def _open_result_browser(self) -> None:
        odb_folder = Path(self.folder_edit.text().strip())
        result_root = (
            odb_folder / RESULT_ROOT_NAME
            if odb_folder.is_dir()
            else Path.cwd() / RESULT_ROOT_NAME
        )
        existing = getattr(self, "_result_browser_dialog", None)
        if existing is not None:
            existing.set_result_root(result_root)
            if existing.isMinimized():
                existing.showNormal()
            else:
                existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        dialog = ResultBrowserDialog(
            result_root,
            self,
            coordinator=self.result_index_coordinator,
        )
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: setattr(self, "_result_browser_dialog", None)
        )
        self._result_browser_dialog = dialog
        dialog.show()

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

    def _background_output_changed(self, _checked: bool) -> None:
        white = self.export_white_background_checkbox.isChecked()
        transparent = self.export_transparent_background_checkbox.isChecked()
        if not white and not transparent:
            changed = self.sender()
            fallback = (
                self.export_transparent_background_checkbox
                if changed is self.export_white_background_checkbox
                else self.export_white_background_checkbox
            )
            fallback.blockSignals(True)
            fallback.setChecked(True)
            fallback.blockSignals(False)
            white = self.export_white_background_checkbox.isChecked()
            transparent = (
                self.export_transparent_background_checkbox.isChecked()
            )
        self.state["export_white_background_png"] = bool(white)
        self.state["export_transparent_background_png"] = bool(transparent)
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
            self.export_white_background_checkbox.setEnabled(not busy)
            self.export_transparent_background_checkbox.setEnabled(not busy)
        if hasattr(self, "cancel_run_button"):
            self.cancel_run_button.setEnabled(bool(busy and self.batch_active))
        if not busy and hasattr(self, "group_tabs"):
            self._refresh_queue_ui()

    @staticmethod
    def _name_info(row: dict[str, Any]) -> OdbNameInfo:
        return parse_odb_name(row["scan"].path.name)

    def _restore_row(self, row: dict[str, Any], values: dict[str, Any]) -> None:
        info = self._name_info(row)
        automatic_label = automatic_direction_label(info)
        direction = row["direction"]
        direction.blockSignals(True)
        direction.clear()
        direction.addItems(
            ["X方向", "Z方向", "XZ方向", automatic_label]
        )
        direction.setCurrentText(automatic_label)
        direction.blockSignals(False)
        detected = (
            DIRECTION_ABAQUS_TO_LABEL.get(info.load_direction, "未识别")
            if info.load_direction
            else "未识别"
        )
        direction.setToolTip(
            f"文件名识别：工况={info.condition or '未识别'}；"
            f"加载方向={detected}。可手动覆盖。"
        )
        if info.rebar_diameter_mm is not None:
            row["diameter"].setValue(info.rebar_diameter_mm)
            row["diameter"].setToolTip(
                f"由样本编号 {info.sample_id} 自动匹配：{info.rebar_diameter_mm:g} mm；可手动修改。"
            )

        adjusted = dict(values)
        saved_direction = str(adjusted.get("direction", ""))
        if saved_direction in DIRECTION_ABAQUS_TO_LABEL:
            adjusted["direction"] = DIRECTION_ABAQUS_TO_LABEL[saved_direction]
        elif saved_direction == AUTO_DIRECTION or saved_direction.startswith("自动（"):
            adjusted["direction"] = automatic_label
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
                "direction_manual": (
                    row["direction"].currentText() in DIRECTION_LABEL_TO_ABAQUS
                ),
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
        settings["export_white_background_png"] = bool(
            self.export_white_background_checkbox.isChecked()
        )
        settings["export_transparent_background_png"] = bool(
            self.export_transparent_background_checkbox.isChecked()
        )
        payload["settings"] = settings
        info = self._name_info(row)
        selected_direction = row["direction"].currentText()
        automatic = selected_direction == automatic_direction_label(info)
        if automatic and info.load_direction:
            payload["load_direction"] = info.load_direction
        elif selected_direction in DIRECTION_LABEL_TO_ABAQUS:
            payload["load_direction"] = DIRECTION_LABEL_TO_ABAQUS[
                selected_direction
            ]
        else:
            raise ValueError(
                f"{row['scan'].path.name} 的加载方向未识别，请手动选择。"
            )
        payload.update(
            {
                "load_direction_source": "filename" if automatic else "manual",
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
        if self.batch_controller is None or self.active_group_task is None:
            return
        self.scan_status.setText(
            f"正在取消当前组：{self.active_group_task['name']}…"
        )
        self.cancel_run_button.setEnabled(False)
        self._append_log(
            f"[{self._timestamp()}] 取消当前组：{self.active_group_task['name']}"
        )
        self.batch_controller.cancel()

    def _cancel_queued_groups(self) -> None:
        if not self.group_queue:
            return
        names = [
            self._queued_task_name(item) for item in self.group_queue
        ]
        self.group_queue.clear()
        self._append_log(
            f"[{self._timestamp()}] 已取消全部待运行组："
            f"{'、'.join(names)}"
        )
        self._refresh_queue_ui()

    def _append_log(self, text: str) -> None:
        if text.startswith("INDEX_INCREMENTAL|"):
            output_dir = Path(text.split("|", 1)[1]).resolve()
            self.result_index_coordinator.enqueue_incremental(
                output_dir.parent, output_dir
            )
            super()._append_log(
                f"[{self._timestamp()}] 已提交结果增量索引：{output_dir.name}"
            )
            return
        if text.startswith("BATCH_PROGRESS|"):
            _, phase, done, total, name = text.split("|", 4)
            self.batch_completed = int(done)
            self.scan_progress.setRange(0, max(int(total), 1))
            self.scan_progress.setValue(self.batch_completed)
            phase_label = "预扫描" if phase == "scan" else "正式提取"
            running = (
                str(self.active_group_task["name"])
                if self.active_group_task is not None
                else "无"
            )
            queued = "、".join(
                self._queued_task_name(item) for item in self.group_queue
            ) or "无"
            self.scan_status.setText(
                f"正在运行：{running}（{phase_label} {done}/{total}：{name}）"
                f"｜排队：{queued}"
            )
            return
        super()._append_log(text)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _has_group_work(self) -> bool:
        return self.active_group_task is not None or bool(self.group_queue)

    def _queued_task_name(self, item: dict[str, Any]) -> str:
        if item.get("name"):
            return str(item["name"])
        group_id = str(item.get("id", ""))
        if group_id.startswith("standalone::"):
            return Path(group_id.split("::", 1)[1]).stem
        group = self.groups.get(group_id)
        return str(group["name"]) if group else group_id

    def _group_is_locked(self, group_id: str) -> bool:
        if (
            self.active_group_task is not None
            and self.active_group_task["id"] == group_id
        ):
            return True
        return any(item["id"] == group_id for item in self.group_queue)

    def _refresh_queue_ui(self) -> None:
        running = (
            str(self.active_group_task["name"])
            if self.active_group_task is not None
            else ""
        )
        positions = {
            str(item["id"]): index
            for index, item in enumerate(self.group_queue, 1)
        }
        queued_names = {
            str(item["id"]): self._queued_task_name(item)
            for item in self.group_queue
        }
        for index in range(1, self.group_tabs.count()):
            group_id = str(self.group_tabs.tabData(index) or "")
            group = self.groups.get(group_id)
            if group is None:
                continue
            name = str(group["name"])
            if self.active_group_task is not None and (
                self.active_group_task["id"] == group_id
            ):
                label = f"{self.active_group_task['name']}（运行中）"
            elif group_id in positions:
                label = (
                    f"{queued_names[group_id]}（排队 {positions[group_id]}）"
                )
            else:
                label = name
            self.group_tabs.setTabText(index, label)
            self.group_tabs.setTabToolTip(
                index, f"{name}\n{len(group.get('members', []))} 个 ODB"
            )

        queued = "、".join(
            self._queued_task_name(item) for item in self.group_queue
        )
        if running or queued:
            self.scan_status.setText(
                f"正在运行：{running or '无'}｜排队：{queued or '无'}"
            )
        self.cancel_run_button.setEnabled(self.active_group_task is not None)
        self.cancel_queued_button.setEnabled(bool(self.group_queue))
        self.scan_button.setEnabled(
            not self.scan_active and not self._has_group_work()
        )
        self.run_all_button.setEnabled(not self.scan_active)
        current_id = (
            str(self.group_tabs.tabData(self.group_tabs.currentIndex()) or "")
            if self.group_tabs.currentIndex() >= 0
            else ""
        )
        current_task_id = (
            "standalone::" + self._current_scope_path
            if self._current_scope_kind == "odb"
            else current_id
        )
        self.run_button.setEnabled(
            not self.scan_active
            and bool(current_task_id)
            and not self._group_is_locked(current_task_id)
        )

    def _group_tab_changed(self, index: int) -> None:
        super()._group_tab_changed(index)
        if hasattr(self, "group_queue"):
            self._refresh_queue_ui()

    def _activate_standalone(self, path: str) -> None:
        super()._activate_standalone(path)
        self._refresh_queue_ui()

    def _unrecognized_directions(
        self, group_specs: list[dict[str, Any]]
    ) -> list[str]:
        problems: list[str] = []
        for group in group_specs:
            for path in group["members"]:
                row = self.rows_by_path.get(path)
                if (
                    row is not None
                    and row["direction"].currentText()
                    == UNRECOGNIZED_DIRECTION
                ):
                    problems.append(f"{group['name']}：{Path(path).name}")
        return problems

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
        new_specs = [
            group
            for group in group_specs
            if not self._group_is_locked(str(group["id"]))
        ]
        if not new_specs:
            QMessageBox.information(
                self, "已在队列中", "目标组正在运行或已经排队，未重复提交。"
            )
            return
        unresolved = self._unrecognized_directions(new_specs)
        if unresolved:
            shown = "\n".join(unresolved[:12])
            if len(unresolved) > 12:
                shown += f"\n……另有 {len(unresolved) - 12} 项"
            QMessageBox.warning(
                self,
                "加载方向未识别",
                "以下 ODB 无法从文件名识别加载方向，请先手动选择"
                " X方向、Z方向或 XZ方向：\n\n"
                f"{shown}\n\n本次未加入运行队列。",
            )
            return
        force_rescan = self._consume_force_rescan()
        for group in new_specs:
            item = {
                "id": str(group["id"]),
                "force_rescan": force_rescan,
            }
            self.group_queue.append(item)
            self._append_log(
                f"[{self._timestamp()}] 已入队：{group['name']}；"
                f"ODB={len(group['members'])}；"
                f"快照=开始运行时冻结；"
                f"强制预扫描={'是' if force_rescan else '否'}"
            )
        self._refresh_queue_ui()
        self._start_next_group()

    def _freeze_group_task(
        self, queued_item: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        group_id = str(queued_item["id"])
        enabled = {
            path
            for path, row in self.rows_by_path.items()
            if row["enabled"].isChecked()
        }
        if group_id.startswith("standalone::"):
            path = group_id.split("::", 1)[1]
            members = [path] if path in enabled and path in self.rows_by_path else []
            plan = {
                "id": group_id,
                "name": Path(path).stem,
                "members": members,
                "overrides": {},
                "standalone": True,
            }
        else:
            group = self.groups.get(group_id)
            if group is None:
                return None, "对比组已不存在"
            members = sorted(
                (
                    path
                    for path in group.get("members", [])
                    if path in enabled and path in self.rows_by_path
                ),
                key=lambda value: natural_sort_key(Path(value).name),
            )
            plan = {
                "id": group_id,
                "name": str(group["name"]),
                "members": members,
                "overrides": copy.deepcopy(
                    group.get("legend_overrides", {})
                ),
                "standalone": False,
            }
        if not members:
            return None, "开始运行时没有有效且已启用的 ODB"
        unresolved = self._unrecognized_directions([plan])
        if unresolved:
            names = "、".join(Path(value.split("：", 1)[1]).name for value in unresolved)
            return None, f"加载方向未识别：{names}"
        try:
            snapshots = {
                path: copy.deepcopy(
                    self._job_payload(self.rows_by_path[path], Path("."))
                )
                for path in members
            }
        except ValueError as error:
            return None, str(error)
        item = {
            **queued_item,
            "name": str(plan["name"]),
            "plan": plan,
            "members": list(members),
            "snapshots": snapshots,
            "batch_id": datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
            "folder_root": str(Path(self.folder_key).resolve()),
        }
        return item, ""

    def _start_next_group(self) -> None:
        if (
            self.active_group_task is not None
            or (
                self.group_worker is not None
                and self.group_worker.isRunning()
            )
        ):
            self._refresh_queue_ui()
            return
        item = None
        while self.group_queue and item is None:
            queued_item = self.group_queue.pop(0)
            candidate, reason = self._freeze_group_task(queued_item)
            if candidate is None:
                name = self._queued_task_name(queued_item)
                self._append_log(
                    f"[{self._timestamp()}] 跳过组：{name}；{reason}"
                )
                self.scan_status.setText(f"已跳过：{name}；{reason}")
                continue
            item = candidate
        if item is None:
            self._refresh_queue_ui()
            return
        self.active_group_task = item
        self.batch_active = True
        self.batch_total = len(item["members"])
        self.batch_completed = 0
        self.scan_progress.setRange(0, max(self.batch_total, 1))
        self.scan_progress.setValue(0)
        workers = min(
            int(self.parallel_workers.value()), max(len(item["members"]), 1)
        )
        controller = MultiProcessController()
        self.batch_controller = controller
        self._append_log(
            f"[{self._timestamp()}] 开始运行组：{item['name']}；"
            f"已冻结最新快照；ODB={len(item['members'])}；"
            f"组内并行 worker={workers}"
        )
        self._refresh_queue_ui()
        worker = _base.FunctionThread(
            lambda log: self._execute_group_task(
                item, workers, controller, log
            )
        )
        self.group_worker = worker
        worker.message.connect(self._append_log)
        worker.completed.connect(
            lambda outputs, current=item: self._group_run_finished(
                current, outputs
            )
        )
        worker.failed.connect(
            lambda details, current=item: self._group_run_failed(
                current, details
            )
        )
        worker.finished.connect(
            lambda current_worker=worker: self._group_worker_finished(
                current_worker
            )
        )
        worker.start()

    def _execute_group_task(
        self,
        item: dict[str, Any],
        workers: int,
        controller: MultiProcessController,
        log: Callable[[str], None],
    ) -> list[str]:
        members = list(item["members"])
        plan = item["plan"]
        cache_root = scan_cache_dir(Path(item["folder_root"]))
        scratch_root = batch_temp_dir(item["batch_id"])
        result_root = result_root_for_odb(Path(members[0]))
        abaqus_version = abaqus_cache_version(self.defaults)

        def check_cancelled() -> None:
            if controller.cancel_requested:
                raise ProcessCancelled("Process cancelled by user")

        def tagged(path: str, message: str) -> None:
            log(f"[{self._timestamp()}] [{Path(path).name}] {message}")

        def prepare_one(path: str) -> tuple[str, dict[str, Any]]:
            check_cancelled()
            payload = copy.deepcopy(item["snapshots"][path])
            # Re-sample at execution time so an ODB changed after the initial
            # GUI scan can never reuse stale prescan or numeric results.
            fingerprint = quick_odb_fingerprint(Path(path))
            snapshot = prescan_config_snapshot(payload)
            config_hash = stable_config_hash(snapshot)
            range_scan = None
            if not item["force_rescan"]:
                range_scan = load_json_cache(
                    cache_root,
                    "prescan",
                    fingerprint,
                    abaqus_version,
                    config_hash,
                )
            if range_scan is not None:
                tagged(
                    path,
                    f"命中预扫描缓存：{fingerprint[:12]}/{config_hash[:12]}",
                )
            else:
                scan_dir = (
                    scratch_root
                    / "prescan"
                    / _base.safe_folder_name(Path(path).stem)
                )
                payload["output_dir"] = str(scan_dir)
                payload["comparison_group"] = "范围预扫描"
                config_path = scan_dir / "job_config.json"
                save_json(config_path, payload)
                tagged(path, "重建场值、帧目录与损伤预扫描缓存")
                range_scan = scan_field_ranges(
                    self.defaults["abaqus_command"],
                    config_path,
                    scan_dir / "frame_catalog_and_ranges.json",
                    lambda line: log(f"[{Path(path).name}] {line}"),
                    controller,
                )
                save_json_cache(
                    cache_root,
                    "prescan",
                    Path(path),
                    fingerprint,
                    abaqus_version,
                    range_scan,
                    config_hash=config_hash,
                    config_snapshot=snapshot,
                )
            indices = choose_sequences(
                range_scan,
                payload["frame_mode"],
                payload["manual_sequence_expression"],
            )
            override = int(payload["prefracture_sequence_index"])
            if payload["frame_mode"] == "auto" and override >= 0:
                indices = sorted(set([indices[-1], override]))
            detected = range_scan.get("auto_detection", {}).get(
                "prefracture_sequence_index"
            )
            if override < 0:
                payload["prefracture_sequence_index"] = (
                    -1 if detected is None else int(detected)
                )
            payload["selected_sequence_indices"] = indices
            tagged(path, f"帧选择={indices}；自动断裂前帧={detected}")
            return path, {
                "payload": payload,
                "range_scan": range_scan,
                "selected_sequence_indices": indices,
                "content_fingerprint": fingerprint,
            }

        prepared: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="odb-prescan"
        ) as pool:
            futures = {
                pool.submit(prepare_one, path): path for path in members
            }
            for done, future in enumerate(as_completed(futures), 1):
                try:
                    path, prepared_item = future.result()
                except Exception:
                    controller.cancel()
                    raise
                prepared[path] = prepared_item
                log(
                    f"BATCH_PROGRESS|scan|{done}|{len(members)}|"
                    f"{Path(path).name}"
                )
        check_cancelled()

        jobs = [
            {
                "comparison_group": item["name"],
                "selected_sequence_indices": prepared[path][
                    "selected_sequence_indices"
                ],
                "range_scan": prepared[path]["range_scan"],
            }
            for path in members
        ]
        group_ranges = aggregate_group_ranges(jobs).get(item["name"], {})
        group_animation_ranges = aggregate_group_animation_ranges(jobs).get(
            item["name"], {}
        )
        self._apply_overrides(
            group_ranges, plan.get("overrides", {}), item["name"]
        )
        self._apply_overrides(
            group_animation_ranges,
            plan.get("overrides", {}),
            item["name"],
        )
        legends_path = (
            result_root
            / "_批次记录"
            / item["batch_id"]
            / "comparison_group_legends.json"
        )
        save_json(
            legends_path,
            {
                item["id"]: {
                    "name": item["name"],
                    "ranges": group_ranges,
                    "animation_ranges": group_animation_ranges,
                }
            },
        )
        ensure_windows_hidden(legends_path)

        def extract_one(path: str) -> str:
            check_cancelled()
            if plan["standalone"]:
                output_dir = (
                    result_root
                    / "未分组"
                    / item["batch_id"]
                    / _base.safe_folder_name(Path(path).stem)
                )
            else:
                output_dir = (
                    result_root
                    / _base.safe_folder_name(item["name"])
                    / item["batch_id"]
                    / _base.safe_folder_name(Path(path).stem)
                )
            payload = copy.deepcopy(prepared[path]["payload"])
            payload["output_dir"] = str(output_dir)
            payload["comparison_group"] = item["name"]
            payload["legend_ranges"] = group_ranges
            payload["animation_legend_ranges"] = group_animation_ranges
            numeric_snapshot = numeric_config_snapshot(payload)
            numeric_hash = stable_config_hash(numeric_snapshot)
            fingerprint = prepared[path]["content_fingerprint"]
            numeric_entry = cache_entry_dir(
                cache_root,
                "numeric",
                fingerprint,
                numeric_hash,
            )
            shared_data_dir = numeric_asset_dir(
                result_root,
                path,
                fingerprint,
                numeric_hash,
            )
            config_path = output_dir / "job_config.json"
            shared_data_ready = numeric_asset_is_valid(
                shared_data_dir,
                content_fingerprint=fingerprint,
                numeric_config_hash=numeric_hash,
                abaqus_version=abaqus_version,
            )
            if not shared_data_ready:
                cache_ready = numeric_cache_is_valid(
                    numeric_entry,
                    content_fingerprint=fingerprint,
                    abaqus_version=abaqus_version,
                    config_hash=numeric_hash,
                )
                if cache_ready:
                    copy_numeric_payload(numeric_entry, shared_data_dir)
                    tagged(
                        path,
                        "从持久缓存恢复 ODB 公共数据；"
                        f"配置={numeric_hash[:12]}",
                    )
                else:
                    extract_payload = copy.deepcopy(payload)
                    extract_payload["output_dir"] = str(shared_data_dir)
                    extract_payload["comparison_group"] = "ODB公共数据"
                    extract_payload["legend_ranges"] = {}
                    extract_payload["animation_legend_ranges"] = {}
                    extract_payload["render_outputs"] = False
                    asset_config_path = shared_data_dir / "job_config.json"
                    save_json(asset_config_path, extract_payload)
                    tagged(
                        path,
                        "提取一次 ODB 公共数值数据；"
                        f"配置={numeric_hash[:12]}",
                    )
                    run_job(
                        self.defaults["abaqus_command"],
                        asset_config_path,
                        lambda line: log(f"[{Path(path).name}] {line}"),
                        controller,
                    )

                metadata_path = shared_data_dir / "metadata.json"
                if metadata_path.is_file():
                    metadata = json.loads(
                        metadata_path.read_text(encoding="utf-8")
                    )
                    metadata.update(
                        {
                            "odb_path": str(Path(path).resolve()),
                            "numeric_cache_reused": cache_ready,
                            "content_fingerprint": fingerprint,
                            "numeric_config_hash": numeric_hash,
                            "result_layout_version": 2,
                            "comparison_group_independent": True,
                        }
                    )
                    save_json(metadata_path, metadata)
                with self._finalize_lock:
                    finalize_numeric_output(shared_data_dir)
                write_numeric_asset_manifest(
                    shared_data_dir,
                    odb_path=path,
                    content_fingerprint=fingerprint,
                    numeric_config_hash=numeric_hash,
                    abaqus_version=abaqus_version,
                )
                if not cache_ready:
                    copy_numeric_payload(shared_data_dir, numeric_entry)
                    write_numeric_cache_metadata(
                        numeric_entry,
                        odb_path=Path(path),
                        content_fingerprint=fingerprint,
                        abaqus_version=abaqus_version,
                        config_hash=numeric_hash,
                        config_snapshot=numeric_snapshot,
                    )
                    tagged(path, "已写入持久数值缓存")
                log(f"INDEX_SHARED_DATA|{shared_data_dir}")
            else:
                tagged(
                    path,
                    "复用 ODB 公共数据；"
                    f"配置={numeric_hash[:12]}",
                )

            payload["source_output_dir"] = str(shared_data_dir)
            payload["render_outputs"] = True
            save_json(config_path, payload)
            tagged(
                path,
                f"按对比组重渲染云图与动画；组={item['name']}",
            )
            render_group_contours(
                self.defaults["abaqus_command"],
                config_path,
                lambda line: log(f"[{Path(path).name}] {line}"),
                controller,
            )
            with self._finalize_lock:
                finalize_render_output(
                    output_dir,
                    int(payload["settings"].get("animation_fps", 5)),
                    export_white_background_png=bool(
                        payload["settings"].get(
                            "export_white_background_png", True
                        )
                    ),
                    export_transparent_background_png=bool(
                        payload["settings"].get(
                            "export_transparent_background_png", True
                        )
                    ),
                )
            write_group_member_manifest(
                output_dir,
                asset_dir=shared_data_dir,
                comparison_group=item["name"],
                odb_path=path,
                content_fingerprint=fingerprint,
                numeric_config_hash=numeric_hash,
            )
            log(f"INDEX_INCREMENTAL|{output_dir}")
            tagged(path, f"完成组输出：{output_dir}")
            return str(output_dir)

        outputs: list[str] = []
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="odb-extract"
        ) as pool:
            futures = {
                pool.submit(extract_one, path): path for path in members
            }
            for done, future in enumerate(as_completed(futures), 1):
                path = futures[future]
                try:
                    outputs.append(future.result())
                except Exception:
                    controller.cancel()
                    raise
                log(
                    f"BATCH_PROGRESS|extract|{done}|{len(members)}|"
                    f"{Path(path).name}"
                )
        check_cancelled()
        return outputs

    def _release_active_group(self, item: dict[str, Any]) -> bool:
        if self.active_group_task is not item:
            return False
        self.active_group_task = None
        self.batch_controller = None
        self.batch_active = False
        return True

    def _group_run_finished(
        self, item: dict[str, Any], outputs: list[str]
    ) -> None:
        if not self._release_active_group(item):
            return
        self._append_log(
            f"[{self._timestamp()}] 组完成：{item['name']}；"
            f"输出={len(outputs)}"
        )
        for output in outputs:
            output_dir = Path(output).resolve()
            self.result_index_coordinator.enqueue_incremental(
                output_dir.parent, output_dir
            )
        self.scan_status.setText(
            f"组完成：{item['name']}｜排队："
            f"{'、'.join(self._queued_task_name(task) for task in self.group_queue) or '无'}"
        )
        self._refresh_queue_ui()
        self._pending_queue_advance = not self._exit_after_cancel

    def _group_run_failed(
        self, item: dict[str, Any], details: str
    ) -> None:
        if not self._release_active_group(item):
            return
        cancelled = "ProcessCancelled" in details or (
            "Process cancelled by user" in details
        )
        state = "已取消" if cancelled else "失败"
        self._append_log(
            f"[{self._timestamp()}] 组{state}：{item['name']}"
        )
        if not cancelled:
            self._append_log(details)
        self.scan_status.setText(
            f"组{state}：{item['name']}｜排队："
            f"{'、'.join(self._queued_task_name(task) for task in self.group_queue) or '无'}"
        )
        self._refresh_queue_ui()
        self._pending_queue_advance = not self._exit_after_cancel

    def _group_worker_finished(self, worker) -> None:
        if self.group_worker is worker:
            self.group_worker = None
        if self._exit_after_cancel and self.active_group_task is None:
            QTimer.singleShot(0, self.close)
            return
        if self._pending_queue_advance:
            self._pending_queue_advance = False
            QTimer.singleShot(0, self._start_next_group)

    def closeEvent(self, event) -> None:
        self._save_state()
        has_index_work = self.result_index_coordinator.has_work()
        if (
            (self._has_group_work() or has_index_work)
            and not self._exit_after_cancel
        ):
            dialog = QMessageBox(self)
            dialog.setWindowTitle("仍有后台任务")
            dialog.setText("仍有正在运行或排队的后处理组/结果索引任务。")
            dialog.setInformativeText(
                "请选择返回，或安全取消全部任务并退出。"
            )
            return_button = dialog.addButton(
                "返回", QMessageBox.RejectRole
            )
            cancel_button = dialog.addButton(
                "取消全部并退出", QMessageBox.DestructiveRole
            )
            dialog.setDefaultButton(return_button)
            dialog.exec()
            if dialog.clickedButton() is not cancel_button:
                event.ignore()
                return
            self.group_queue.clear()
            self._exit_after_cancel = True
            if has_index_work:
                self._append_log(
                    f"[{self._timestamp()}] 正在安全取消结果索引任务并退出"
                )
                if not self.result_index_coordinator.shutdown(30000):
                    self._append_log(
                        f"[{self._timestamp()}] 结果索引线程仍在退出，暂不关闭程序"
                    )
                    self._exit_after_cancel = False
                    event.ignore()
                    return
            if self.batch_controller is not None:
                self._append_log(
                    f"[{self._timestamp()}] 正在取消全部任务并退出"
                )
                self.batch_controller.cancel()
                event.ignore()
                self._refresh_queue_ui()
                return
        super().closeEvent(event)


FunctionThread = _base.FunctionThread
FRAME_MODE_LABELS = _base.FRAME_MODE_LABELS
ComparisonTree = _base.ComparisonTree
LegendRangeDialog = _base.LegendRangeDialog
safe_folder_name = _base.safe_folder_name


def main() -> int:
    return _base.main()


if __name__ == "__main__":
    raise SystemExit(main())
