"""Resource-manager comparison groups with persistent multi-membership."""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from . import safe_defaults as _previous
from .config import save_json
from .paths import result_root_for_odb, scan_cache_dir, state_file
from .group_ui import ComparisonTree, LegendRangeDialog
from .legends import aggregate_group_ranges, choose_sequences
from .models import OdbScan
from .naming import natural_sort_key
from .postprocess import finalize_output
from .runner import (
    ProcessController,
    render_group_contours,
    run_job,
    scan_field_ranges,
    scan_folder,
)


def safe_folder_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .")
    return cleaned or "未命名组"


class MainWindow(_previous.MainWindow):
    def __init__(self) -> None:
        self.state_path = state_file()
        self.state = self._read_state()
        self.folder_key = ""
        self.groups: dict[str, dict[str, Any]] = {}
        self.condition_categories: dict[str, list[str]] = {}
        self.odb_configs: dict[str, dict[str, Any]] = {}
        self.rows_by_path: dict[str, dict[str, Any]] = {}
        self.scans_by_path: dict[str, OdbScan] = {}
        self.scan_controller: ProcessController | None = None
        self.scan_active = False
        self.scan_started_at = 0.0
        self.scan_total = 0
        self.scan_completed = 0
        self._current_scope_kind = "browse"
        self._current_scope_path = ""
        self._current_scope_group_id = ""
        self._restoring_tab_order = False
        super().__init__()
        self.setWindowTitle("Abaqus ODB PostProcessor 0.3")
        self.state_timer = QTimer(self)
        self.state_timer.setSingleShot(True)
        self.state_timer.timeout.connect(self._save_state)
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self._update_elapsed)

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"version": 1, "folders": {}}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            payload.setdefault("version", 1)
            payload.setdefault("folders", {})
            return payload
        except Exception:
            return {"version": 1, "folders": {}}

    def _build_ui(self) -> None:
        super()._build_ui()
        layout: QVBoxLayout = self.centralWidget().layout()

        progress_widget = QWidget()
        progress_widget.setObjectName("progressPanel")
        progress_layout = QHBoxLayout(progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(8)
        self.scan_status = QLabel("尚未扫描")
        self.scan_status.setMinimumWidth(280)
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(0)
        self.scan_progress.setFormat("%v / %m")
        self.elapsed_label = QLabel("用时 00:00:00")
        self.cancel_scan_button = QPushButton("取消扫描")
        self.cancel_scan_button.setEnabled(False)
        self.cancel_scan_button.clicked.connect(self._cancel_scan)
        progress_layout.addWidget(self.scan_status, 2)
        progress_layout.addWidget(self.scan_progress, 3)
        progress_layout.addWidget(self.elapsed_label)
        progress_layout.addWidget(self.cancel_scan_button)
        layout.insertWidget(1, progress_widget)

        layout.removeWidget(self.table)
        splitter = QSplitter(Qt.Horizontal)
        self.main_splitter = splitter
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(7)
        group_panel = QWidget()
        group_panel.setMinimumWidth(290)
        group_layout = QVBoxLayout(group_panel)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(4)
        self.source_header = QWidget()
        self.source_header.setFixedHeight(38)
        source_header_layout = QHBoxLayout(self.source_header)
        source_header_layout.setContentsMargins(0, 0, 0, 0)
        source_header_layout.addWidget(
            QLabel("ODB 来源（Ctrl/Shift 多选，拖到右侧）")
        )
        source_header_layout.addStretch(1)
        group_layout.addWidget(self.source_header)
        self.group_tree = ComparisonTree()
        self.group_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.group_tree.customContextMenuRequested.connect(self._tree_context_menu)
        self.group_tree.itemSelectionChanged.connect(self._tree_selection_changed)
        self.group_tree.itemDoubleClicked.connect(self._source_tree_item_activated)
        group_layout.addWidget(self.group_tree, 1)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        self.tabs_header = QWidget()
        self.tabs_header.setFixedHeight(38)
        tabs_layout = QHBoxLayout(self.tabs_header)
        tabs_layout.setContentsMargins(0, 0, 0, 0)
        self.group_tabs = QTabBar()
        self.group_tabs.setExpanding(False)
        self.group_tabs.setUsesScrollButtons(True)
        self.group_tabs.setMovable(True)
        self.group_tabs.setElideMode(Qt.ElideRight)
        self.group_tabs.setStyleSheet("QTabBar::tab { width: 160px; }")
        self.group_tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.group_tabs.currentChanged.connect(self._group_tab_changed)
        self.group_tabs.customContextMenuRequested.connect(
            self._group_tab_context_menu
        )
        self.group_tabs.tabBarDoubleClicked.connect(self._group_tab_double_clicked)
        self.group_tabs.tabMoved.connect(self._group_tab_moved)
        tabs_layout.addWidget(self.group_tabs, 1)
        self.create_group_button = QPushButton("新建对比组")
        self.create_group_button.clicked.connect(self._create_group)
        tabs_layout.addWidget(self.create_group_button)
        right_layout.addWidget(self.tabs_header)
        self.table.odbPathsDropped.connect(self._drop_paths_into_current_group)
        right_layout.addWidget(self.table, 1)

        splitter.addWidget(group_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([350, 1250])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.insertWidget(3, splitter, 1)

        self.run_button.setText("运行当前项目")
        self.run_all_button = QPushButton("运行全部项目")
        self.run_all_button.clicked.connect(lambda: self._run_scope("all"))
        option_layout = layout.itemAt(2).layout()
        option_layout.addWidget(self.run_all_button)

    def _set_busy(self, busy: bool) -> None:
        super()._set_busy(busy)
        if hasattr(self, "run_all_button"):
            self.run_all_button.setEnabled(not busy)
        if hasattr(self, "cancel_scan_button"):
            self.cancel_scan_button.setEnabled(bool(busy and self.scan_active))
        if hasattr(self, "scan_button"):
            self.scan_button.setText("正在扫描…" if busy and self.scan_active else "扫描 ODB")

    def _scan(self) -> None:
        folder = Path(self.folder_edit.text().strip())
        if not folder.is_dir():
            QMessageBox.warning(self, "路径无效", "请选择存在的 ODB 文件夹。")
            return
        self.scan_controller = ProcessController()
        self.scan_active = True
        self.scan_started_at = time.monotonic()
        self.scan_total = 0
        self.scan_completed = 0
        self.scan_status.setText(f"正在启动 Abaqus 扫描：{folder}")
        self.scan_progress.setRange(0, 0)
        self.elapsed_timer.start(1000)
        cache = scan_cache_dir(folder)
        self._append_log(f"扫描：{folder}")
        self._start_thread(
            lambda log: scan_folder(
                self.defaults["abaqus_command"], folder, cache, log, self.scan_controller
            ),
            self._scan_finished,
        )

    def _cancel_scan(self) -> None:
        if self.scan_controller is None or not self.scan_active:
            return
        self.scan_status.setText("正在取消扫描，保留已完成的 ODB…")
        self.cancel_scan_button.setEnabled(False)
        self.scan_controller.cancel()

    def _append_log(self, text: str) -> None:
        if text.startswith("SCAN_DISCOVERED|"):
            self.scan_total = int(text.split("|", 1)[1])
            self.scan_progress.setRange(0, max(self.scan_total, 1))
            self.scan_progress.setValue(0)
            self.scan_status.setText(f"已发现 {self.scan_total} 个 ODB，准备读取")
            return
        if text.startswith("SCAN_START|"):
            _, index, total, name = text.split("|", 3)
            self.scan_status.setText(f"正在读取 {index}/{total}：{name}")
            self.scan_progress.setValue(max(int(index) - 1, 0))
            super()._append_log(f"正在读取 [{index}/{total}] {name}")
            return
        if text.startswith("SCAN_DONE|"):
            _, index, total, name = text.split("|", 3)
            self.scan_completed = int(index)
            self.scan_progress.setValue(self.scan_completed)
            self.scan_status.setText(f"已完成 {index}/{total}：{name}")
            return
        if text.startswith("SCAN_FINISHED|"):
            _, done, total = text.split("|", 2)
            self.scan_completed = int(done)
            self.scan_progress.setValue(self.scan_completed)
            self.scan_status.setText(f"扫描完成：{done}/{total}")
            return
        super()._append_log(text)

    def _update_elapsed(self) -> None:
        elapsed = max(0, int(time.monotonic() - self.scan_started_at))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.elapsed_label.setText(f"用时 {hours:02d}:{minutes:02d}:{seconds:02d}")

    def _scan_finished(self, payload: dict) -> None:
        self.elapsed_timer.stop()
        self.scan_active = False
        folder = str(payload.get("folder", self.folder_edit.text().strip()))
        self._load_folder_state(folder)
        super()._scan_finished(payload)
        completed = int(payload.get("completed_count", len(payload.get("odbs", []))))
        total = int(payload.get("odb_count", completed))
        self.scan_progress.setRange(0, max(total, 1))
        self.scan_progress.setValue(completed)
        if payload.get("cancelled"):
            self.scan_status.setText(f"扫描已取消：保留 {completed}/{total} 个 ODB")
            self._append_log(f"扫描已取消，已保留 {completed}/{total} 个结果")
        else:
            self.scan_status.setText(f"扫描完成：{completed}/{total} 个 ODB")
        self._save_state()

    def _thread_failed(self, details: str) -> None:
        if self.scan_active:
            self.elapsed_timer.stop()
            self.scan_active = False
            self.scan_status.setText("扫描失败，请查看日志")
        super()._thread_failed(details)

    def _load_folder_state(self, folder: str) -> None:
        self.folder_key = str(Path(folder).resolve())
        folder_state = self.state.setdefault("folders", {}).setdefault(
            self.folder_key, {"groups": {}, "odb_configs": {}}
        )
        self.groups = folder_state.setdefault("groups", {})
        self.odb_configs = folder_state.setdefault("odb_configs", {})
        order = [
            group_id
            for group_id in folder_state.get("group_order", [])
            if group_id in self.groups
        ]
        order.extend(group_id for group_id in self.groups if group_id not in order)
        self.groups = {group_id: self.groups[group_id] for group_id in order}
        folder_state["groups"] = self.groups
        for group in self.groups.values():
            group.setdefault("name", "未命名组")
            group.setdefault("members", [])
            group.setdefault("legend_overrides", {})

    def _populate(self, scans: list[OdbScan]) -> None:
        super()._populate(scans)
        self.rows_by_path.clear()
        self.scans_by_path = {str(scan.path.resolve()): scan for scan in scans}
        for row in self.row_widgets:
            path = str(row["scan"].path.resolve())
            self.rows_by_path[path] = row
            row["group"].setReadOnly(True)
            row["group"].setToolTip("对比组由右侧标签页和拖放管理；同一 ODB 可属于多个组。")
            self._restore_row(row, self.odb_configs.get(path, {}))
            self._connect_row_state(row)
        self._rebuild_tree()
        self._update_membership_labels()

    def _restore_row(self, row: dict[str, Any], values: dict[str, Any]) -> None:
        if not values:
            return
        row["enabled"].setChecked(bool(values.get("enabled", True)))
        combo_keys = (
            "start_step", "end_step", "frame_mode", "direction", "load_set", "pile_type",
            "pile_display", "concrete", "steel", "soil", "rebar",
        )
        for key in combo_keys:
            text = str(values.get(key, ""))
            index = row[key].findText(text)
            if index >= 0:
                row[key].setCurrentIndex(index)
        row["manual_frames"].setText(str(values.get("manual_frames", "")))
        row["material"].setText(str(values.get("material", row["material"].text())))
        if "diameter" in values:
            row["diameter"].setValue(float(values["diameter"]))
        if "prefracture" in values:
            row["prefracture"].setValue(int(values["prefracture"]))

    def _connect_row_state(self, row: dict[str, Any]) -> None:
        row["enabled"].stateChanged.connect(self._schedule_save)
        for key in (
            "start_step", "end_step", "frame_mode", "direction", "load_set", "pile_type",
            "pile_display", "concrete", "steel", "soil", "rebar",
        ):
            row[key].currentTextChanged.connect(self._schedule_save)
        row["manual_frames"].editingFinished.connect(self._schedule_save)
        row["material"].editingFinished.connect(self._schedule_save)
        row["diameter"].valueChanged.connect(self._schedule_save)
        row["prefracture"].valueChanged.connect(self._schedule_save)

    def _schedule_save(self, *_args) -> None:
        if hasattr(self, "state_timer"):
            self.state_timer.start(350)

    def _serialize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "enabled": row["enabled"].isChecked(),
            "start_step": row["start_step"].currentText(),
            "end_step": row["end_step"].currentText(),
            "frame_mode": row["frame_mode"].currentText(),
            "manual_frames": row["manual_frames"].text(),
            "direction": row["direction"].currentText(),
            "load_set": row["load_set"].currentText(),
            "pile_type": row["pile_type"].currentText(),
            "pile_display": row["pile_display"].currentText(),
            "concrete": row["concrete"].currentText(),
            "steel": row["steel"].currentText(),
            "soil": row["soil"].currentText(),
            "rebar": row["rebar"].currentText(),
            "material": row["material"].text(),
            "diameter": row["diameter"].value(),
            "prefracture": row["prefracture"].value(),
        }

    def _save_state(self) -> None:
        if not self.folder_key:
            return
        for path, row in self.rows_by_path.items():
            self.odb_configs[path] = self._serialize_row(row)
        folder_state = self.state.setdefault("folders", {}).setdefault(self.folder_key, {})
        folder_state["groups"] = self.groups
        folder_state["group_order"] = list(self.groups)
        folder_state["odb_configs"] = self.odb_configs
        save_json(self.state_path, self.state)

    def _new_tree_item(self, parent, text: str, kind: str, path: str = "", group_id: str = ""):
        from PySide6.QtWidgets import QTreeWidgetItem

        item = QTreeWidgetItem(parent, [text])
        item.setData(0, Qt.UserRole, kind)
        item.setData(0, Qt.UserRole + 1, path)
        item.setData(0, Qt.UserRole + 2, group_id)
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if kind == "odb":
            flags |= Qt.ItemIsDragEnabled
        item.setFlags(flags)
        return item

    def _rebuild_tree(self, selected_group_id: str = "") -> None:
        selected_paths = set(self.group_tree.selected_odb_paths())
        self.group_tree.blockSignals(True)
        self.group_tree.clear()
        all_root = self._new_tree_item(self.group_tree, "全部 ODB", "all_root")
        categories_root = self._new_tree_item(
            self.group_tree, "按工况分类（仅浏览）", "categories_root"
        )
        for path in sorted(
            self.scans_by_path,
            key=lambda value: natural_sort_key(Path(value).name),
        ):
            item = self._new_tree_item(all_root, Path(path).name, "odb", path)
            item.setSelected(path in selected_paths)
        for condition, members in sorted(
            self.condition_categories.items(), key=lambda item: item[0].casefold()
        ):
            category_item = self._new_tree_item(
                categories_root, f"工况-{condition}", "category", condition
            )
            for path in sorted(
                set(members),
                key=lambda value: natural_sort_key(Path(value).name),
            ):
                label = Path(path).name
                if path not in self.scans_by_path:
                    label += "（缺失）"
                self._new_tree_item(category_item, label, "odb", path)
        all_root.setExpanded(True)
        categories_root.setExpanded(True)
        if not selected_paths:
            self.group_tree.setCurrentItem(all_root)
        self.group_tree.blockSignals(False)
        self._rebuild_group_tabs(selected_group_id)

    def _rebuild_group_tabs(self, selected_group_id: str = "") -> None:
        current_group_id = (
            selected_group_id
            or self._current_scope_group_id
            or (
                str(self.group_tabs.tabData(self.group_tabs.currentIndex()) or "")
                if self.group_tabs.currentIndex() >= 0
                else ""
            )
        )
        self.group_tabs.blockSignals(True)
        while self.group_tabs.count():
            self.group_tabs.removeTab(0)
        self.group_tabs.addTab("全部配置")
        self.group_tabs.setTabData(0, "")
        selected_index = 0
        for group_id, group in self.groups.items():
            index = self.group_tabs.addTab(str(group["name"]))
            self.group_tabs.setTabData(index, group_id)
            self.group_tabs.setTabToolTip(
                index,
                f"{group['name']}\n{len(group.get('members', []))} 个 ODB",
            )
            if group_id == current_group_id:
                selected_index = index
        self.group_tabs.setCurrentIndex(selected_index)
        self.group_tabs.blockSignals(False)
        self._group_tab_changed(selected_index)

    def _group_tab_moved(self, _from_index: int, _to_index: int) -> None:
        if self._restoring_tab_order:
            return
        all_index = next(
            (
                index
                for index in range(self.group_tabs.count())
                if not str(self.group_tabs.tabData(index) or "")
            ),
            -1,
        )
        if all_index != 0:
            self._restoring_tab_order = True
            self.group_tabs.moveTab(all_index, 0)
            self._restoring_tab_order = False
        order = [
            str(self.group_tabs.tabData(index))
            for index in range(1, self.group_tabs.count())
            if str(self.group_tabs.tabData(index) or "") in self.groups
        ]
        if order:
            self.groups = {group_id: self.groups[group_id] for group_id in order}
            self._save_state()

    def _group_is_locked(self, _group_id: str) -> bool:
        return False

    def _group_names_for_path(self, path: str) -> list[str]:
        return [
            str(group["name"])
            for group in self.groups.values()
            if path in group.get("members", [])
        ]

    def _update_membership_labels(self) -> None:
        for path, row in self.rows_by_path.items():
            names = self._group_names_for_path(path)
            row["group"].setText("；".join(names) if names else "未分组")

    def _set_visible_paths(self, visible: set[str] | None) -> None:
        for index, row in enumerate(self.row_widgets):
            path = str(row["scan"].path.resolve())
            self.table.setRowHidden(index, visible is not None and path not in visible)

    def _tree_selection_changed(self) -> None:
        # Source selection is intentionally independent of the active group tab.
        # This preserves Ctrl/Shift selections while the user drags them right.
        return

    def _group_tab_changed(self, index: int) -> None:
        group_id = (
            str(self.group_tabs.tabData(index) or "")
            if index >= 0
            else ""
        )
        if group_id and group_id in self.groups:
            self._current_scope_kind = "group"
            self._current_scope_group_id = group_id
            self._current_scope_path = ""
            self._set_visible_paths(set(self.groups[group_id].get("members", [])))
        elif index >= 0:
            self._current_scope_kind = "browse"
            self._current_scope_group_id = ""
            self._current_scope_path = ""
            self._set_visible_paths(None)

    def _activate_standalone(self, path: str) -> None:
        if path not in self.rows_by_path:
            return
        self.group_tabs.blockSignals(True)
        self.group_tabs.setCurrentIndex(-1)
        self.group_tabs.blockSignals(False)
        self._current_scope_kind = "odb"
        self._current_scope_group_id = ""
        self._current_scope_path = path
        self._set_visible_paths({path})

    def _source_tree_item_activated(self, item, _column: int = 0) -> None:
        if self.group_tree.kind(item) == "odb":
            self._activate_standalone(self.group_tree.odb_path(item))

    def _selected_or_item_paths(self, item) -> list[str]:
        paths = self.group_tree.selected_odb_paths()
        item_path = (
            self.group_tree.odb_path(item)
            if self.group_tree.kind(item) == "odb"
            else ""
        )
        if item_path and item_path not in paths:
            paths = [item_path]
        return paths

    def _tree_context_menu(self, position) -> None:
        item = self.group_tree.itemAt(position)
        kind = self.group_tree.kind(item)
        menu = QMenu(self)
        standalone_action = None
        add_actions = {}
        remove_actions = {}
        paths = self._selected_or_item_paths(item)
        if kind == "odb" and paths:
            standalone_action = menu.addAction("作为单个 ODB 显示")
            if self.groups:
                add_menu = menu.addMenu("加入对比组")
                remove_menu = menu.addMenu("从对比组移除")
                for group_id, group in self.groups.items():
                    members = set(group.get("members", []))
                    if any(path not in members for path in paths):
                        add_actions[
                            add_menu.addAction(str(group["name"]))
                        ] = group_id
                    if any(path in members for path in paths):
                        remove_actions[
                            remove_menu.addAction(str(group["name"]))
                        ] = group_id
                add_menu.setEnabled(bool(add_actions))
                remove_menu.setEnabled(bool(remove_actions))
        if not menu.actions():
            return
        chosen = menu.exec(self.group_tree.viewport().mapToGlobal(position))
        if chosen is None:
            return
        if chosen is standalone_action and len(paths) == 1:
            self._activate_standalone(paths[0])
        elif chosen in add_actions:
            self._add_memberships(paths, add_actions[chosen])
        elif chosen in remove_actions:
            for path in paths:
                self._remove_membership(path, remove_actions[chosen], rebuild=False)
            self._rebuild_tree(remove_actions[chosen])
            self._update_membership_labels()
            self._save_state()

    def _group_tab_context_menu(self, position) -> None:
        index = self.group_tabs.tabAt(position)
        group_id = (
            str(self.group_tabs.tabData(index) or "") if index >= 0 else ""
        )
        menu = QMenu(self)
        create_action = menu.addAction("新建对比组")
        rename_action = legend_action = delete_action = None
        if group_id in self.groups:
            menu.addSeparator()
            rename_action = menu.addAction("重命名")
            legend_action = menu.addAction("图例范围设置…")
            delete_action = menu.addAction("删除对比组")
        chosen = menu.exec(self.group_tabs.mapToGlobal(position))
        if chosen is create_action:
            self._create_group()
        elif chosen is rename_action:
            self._rename_group(group_id)
        elif chosen is legend_action:
            self._edit_group_legend(group_id)
        elif chosen is delete_action:
            self._delete_group(group_id)

    def _group_tab_double_clicked(self, index: int) -> None:
        if index >= 0:
            group_id = str(self.group_tabs.tabData(index) or "")
            if group_id:
                self._rename_group(group_id)

    def _drop_paths_into_current_group(self, paths: list[str]) -> None:
        index = self.group_tabs.currentIndex()
        group_id = (
            str(self.group_tabs.tabData(index) or "") if index >= 0 else ""
        )
        if group_id not in self.groups:
            QMessageBox.information(
                self,
                "请选择对比组",
                "请先在右侧选择一个对比组标签，再将 ODB 拖入表格。",
            )
            return
        self._add_memberships(paths, group_id)

    def _unique_group_name(self, name: str, exclude_id: str = "") -> bool:
        target = name.strip().casefold()
        return bool(target) and all(
            group_id == exclude_id or str(group["name"]).strip().casefold() != target
            for group_id, group in self.groups.items()
        )

    def _create_group(self) -> None:
        name, accepted = QInputDialog.getText(self, "新建对比组", "组名：")
        name = name.strip()
        if not accepted:
            return
        if not self._unique_group_name(name):
            QMessageBox.warning(self, "组名无效", "组名不能为空且不能与现有组重复。")
            return
        group_id = uuid.uuid4().hex
        self.groups[group_id] = {"name": name, "members": [], "legend_overrides": {}}
        self._rebuild_tree(group_id)
        self._save_state()

    def _rename_group(self, group_id: str) -> None:
        group = self.groups.get(group_id)
        if not group:
            return
        if self._group_is_locked(group_id):
            QMessageBox.information(
                self, "组正在运行", "排队或运行中的对比组不能重命名。"
            )
            return
        name, accepted = QInputDialog.getText(
            self, "重命名对比组", "新组名：", text=str(group["name"])
        )
        name = name.strip()
        if not accepted:
            return
        if not self._unique_group_name(name, group_id):
            QMessageBox.warning(self, "组名无效", "组名不能为空且不能与现有组重复。")
            return
        group["name"] = name
        self._rebuild_tree(group_id)
        self._update_membership_labels()
        self._save_state()

    def _delete_group(self, group_id: str) -> None:
        group = self.groups.get(group_id)
        if not group:
            return
        if self._group_is_locked(group_id):
            QMessageBox.information(
                self, "组正在运行", "排队或运行中的对比组不能删除。"
            )
            return
        answer = QMessageBox.question(
            self,
            "删除对比组",
            f"删除“{group['name']}”？\n仅删除成员关系，不删除 ODB 和已有输出。",
        )
        if answer != QMessageBox.Yes:
            return
        del self.groups[group_id]
        self._rebuild_tree()
        self._update_membership_labels()
        self._save_state()

    def _edit_group_legend(self, group_id: str) -> None:
        group = self.groups.get(group_id)
        if not group:
            return
        dialog = LegendRangeDialog(
            str(group["name"]), dict(group.get("legend_overrides", {})), self
        )
        if dialog.exec() == QDialog.Accepted:
            group["legend_overrides"] = dialog.values()
            self._save_state()

    def _add_membership(self, path: str, group_id: str) -> None:
        self._add_memberships([path], group_id)

    def _add_memberships(self, paths: list[str], group_id: str) -> None:
        group = self.groups.get(group_id)
        if not group:
            return
        members = group.setdefault("members", [])
        for path in paths:
            if path in self.rows_by_path and path not in members:
                members.append(path)
        self._rebuild_tree(group_id)
        self._update_membership_labels()
        self._save_state()

    def _remove_membership(
        self, path: str, group_id: str, *, rebuild: bool = True
    ) -> None:
        group = self.groups.get(group_id)
        if not group:
            return
        group["members"] = [value for value in group.get("members", []) if value != path]
        if rebuild:
            self._rebuild_tree(group_id)
            self._update_membership_labels()
            self._save_state()

    def _run_selected(self) -> None:
        self._run_scope("current")

    def _scope_groups(self, scope: str) -> list[dict[str, Any]]:
        enabled = {
            path for path, row in self.rows_by_path.items() if row["enabled"].isChecked()
        }
        if not enabled:
            return []
        if scope == "current":
            if (
                self._current_scope_kind == "group"
                and self._current_scope_group_id in self.groups
            ):
                group_id = self._current_scope_group_id
                group = self.groups[group_id]
                members = sorted(
                    (
                        path
                        for path in group.get("members", [])
                        if path in enabled
                    ),
                    key=lambda value: natural_sort_key(Path(value).name),
                )
                return [{"id": group_id, "name": group["name"], "members": members,
                         "overrides": group.get("legend_overrides", {}), "standalone": False}]
            if (
                self._current_scope_kind == "odb"
                and self._current_scope_path in enabled
            ):
                path = self._current_scope_path
                return [{
                    "id": "standalone::" + path,
                    "name": Path(path).stem,
                    "members": [path],
                    "overrides": {},
                    "standalone": True,
                }]
            return []

        plans = []
        grouped_members = set()
        for group_id, group in self.groups.items():
            members = sorted(
                (
                    path
                    for path in group.get("members", [])
                    if path in enabled
                ),
                key=lambda value: natural_sort_key(Path(value).name),
            )
            if members:
                plans.append({"id": group_id, "name": group["name"], "members": members,
                              "overrides": group.get("legend_overrides", {}), "standalone": False})
                grouped_members.update(members)
        for path in sorted(
            enabled - grouped_members,
            key=lambda value: natural_sort_key(Path(value).name),
        ):
            plans.append({"id": "standalone::" + path, "name": Path(path).stem,
                          "members": [path], "overrides": {}, "standalone": True})
        return plans

    @staticmethod
    def _apply_overrides(plan: dict[str, dict[str, Any]], overrides: dict[str, Any], group_name: str) -> None:
        for field_name, rule in overrides.items():
            if rule.get("mode") != "manual":
                continue
            current = plan.setdefault(field_name, {})
            current.update({
                "min": float(rule["min"]),
                "max": float(rule["max"]),
                "source": "comparison_group_manual_override",
                "comparison_group": group_name,
            })

    @staticmethod
    def _copy_numeric_cache(source: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        for name in ("data", "History_Output", "rebar", "freebody"):
            source_path = source / name
            if source_path.exists():
                shutil.copytree(source_path, target / name, dirs_exist_ok=True)
        metadata_source = source / "metadata.json"
        if metadata_source.exists():
            shutil.copy2(metadata_source, target / "metadata.json")

    def _run_scope(self, scope: str) -> None:
        self._save_state()
        group_specs = self._scope_groups(scope)
        group_specs = [item for item in group_specs if item["members"]]
        if not group_specs:
            QMessageBox.information(
                self,
                "没有作业",
                "请选择单个 ODB 或用户创建的对比组；“全部 ODB”和工况分类仅用于浏览。",
            )
            return
        unique_paths = []
        for group in group_specs:
            for path in group["members"]:
                if path in self.rows_by_path and path not in unique_paths:
                    unique_paths.append(path)
        snapshots = {
            path: self._job_payload(self.rows_by_path[path], Path("."))
            for path in unique_paths
        }
        run_root = result_root_for_odb(Path(unique_paths[0])) / "未分组" / datetime.now().strftime("%Y%m%d_%H%M%S")

        def task(log: Callable[[str], None]) -> list[str]:
            prepared: dict[str, dict[str, Any]] = {}
            for path in unique_paths:
                scan_dir = run_root / "_scan" / safe_folder_name(Path(path).stem)
                payload = copy.deepcopy(snapshots[path])
                payload["output_dir"] = str(scan_dir)
                payload["comparison_group"] = "范围预扫描"
                config_path = scan_dir / "job_config.json"
                save_json(config_path, payload)
                log(f"预扫描场值与损伤：{Path(path).name}")
                range_scan = scan_field_ranges(
                    self.defaults["abaqus_command"],
                    config_path,
                    scan_dir / "frame_catalog_and_ranges.json",
                    log,
                )
                indices = choose_sequences(
                    range_scan, payload["frame_mode"], payload["manual_sequence_expression"]
                )
                override = int(payload["prefracture_sequence_index"])
                if payload["frame_mode"] == "auto" and override >= 0:
                    indices = sorted(set([indices[-1], override]))
                auto = range_scan.get("auto_detection", {})
                detected = auto.get("prefracture_sequence_index")
                if override < 0:
                    payload["prefracture_sequence_index"] = -1 if detected is None else int(detected)
                payload["selected_sequence_indices"] = indices
                prepared[path] = {
                    "payload": payload,
                    "range_scan": range_scan,
                    "selected_sequence_indices": indices,
                }
                log(f"帧选择 {Path(path).name}: {indices}; 自动断裂前帧={detected}")

            group_ranges: dict[str, dict[str, Any]] = {}
            for group in group_specs:
                jobs = []
                for path in group["members"]:
                    item = prepared[path]
                    jobs.append({
                        "comparison_group": group["name"],
                        "selected_sequence_indices": item["selected_sequence_indices"],
                        "range_scan": item["range_scan"],
                    })
                plan = aggregate_group_ranges(jobs).get(str(group["name"]), {})
                self._apply_overrides(plan, group.get("overrides", {}), str(group["name"]))
                group_ranges[group["id"]] = plan
            save_json(
                run_root / "comparison_group_legends.json",
                {group["id"]: {"name": group["name"], "ranges": group_ranges[group["id"]]}
                 for group in group_specs},
            )

            outputs = []
            for path in unique_paths:
                memberships = [group for group in group_specs if path in group["members"]]
                primary_output: Path | None = None
                for membership_index, group in enumerate(memberships):
                    if group["standalone"]:
                        output_dir = run_root / "standalone" / safe_folder_name(Path(path).stem)
                    else:
                        output_dir = (
                            run_root / "groups" / safe_folder_name(str(group["name"]))
                            / safe_folder_name(Path(path).stem)
                        )
                    payload = copy.deepcopy(prepared[path]["payload"])
                    payload["output_dir"] = str(output_dir)
                    payload["comparison_group"] = str(group["name"])
                    payload["legend_ranges"] = group_ranges[group["id"]]
                    config_path = output_dir / "job_config.json"
                    if membership_index == 0:
                        save_json(config_path, payload)
                        log(f"基础数据提取：{Path(path).name}；首组={group['name']}")
                        run_job(self.defaults["abaqus_command"], config_path, log)
                        finalize_output(output_dir, int(self.defaults["animation_fps"]))
                        primary_output = output_dir
                    else:
                        assert primary_output is not None
                        self._copy_numeric_cache(primary_output, output_dir)
                        payload["source_output_dir"] = str(primary_output)
                        save_json(config_path, payload)
                        log(f"复用基础数据并按组重渲染：{Path(path).name}；组={group['name']}")
                        render_group_contours(self.defaults["abaqus_command"], config_path, log)
                        finalize_output(output_dir, int(self.defaults["animation_fps"]))
                    outputs.append(str(output_dir))
                    log(f"完成：{output_dir}")
            return outputs

        self._start_thread(task, self._run_finished)

    def _run_finished(self, outputs: list[str]) -> None:
        root = str(Path(outputs[0]).parents[2] if outputs else Path(self.folder_edit.text().strip()) / "AbaqusODBPostProcessor_Results")
        QMessageBox.information(
            self, "处理完成", f"已生成 {len(outputs)} 个组/ODB 结果。\n结果根目录：\n{root}"
        )

    def closeEvent(self, event) -> None:
        self._save_state()
        super().closeEvent(event)


FunctionThread = _previous.FunctionThread
FRAME_MODE_LABELS = _previous.FRAME_MODE_LABELS


def main() -> int:
    application = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
