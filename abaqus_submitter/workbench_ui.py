"""Engineering-workbench widgets matching the selected C layout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

import psutil

from .cluster_ui import SubmissionWizardDialog
from .command import MEMORY_OPTIONS
from .constants import ACTIVE_STATUSES, DEFAULT_CPUS, MAX_CPUS
from .job_draft import LocalJobDraft
from .models import QueueItem
from .qt_compat import QtCore, QtWidgets, Signal
from .remote_frontend import ExecutionLocation, RemoteFrontendBridge
from .ui_components import (
    ResourceProgressBar,
    SegmentedSpinBox,
    WorkbenchComboBox,
)


@dataclass(frozen=True)
class LocalResourceSnapshot:
    logical_cpus: int
    cpu_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    memory_percent: float


_CPU_PERCENT_INITIALIZED = False


def capture_local_resource_snapshot() -> LocalResourceSnapshot:
    global _CPU_PERCENT_INITIALIZED
    memory = psutil.virtual_memory()
    cpu_percent = psutil.cpu_percent(
        interval=None if _CPU_PERCENT_INITIALIZED else 0.05
    )
    _CPU_PERCENT_INITIALIZED = True
    return LocalResourceSnapshot(
        logical_cpus=psutil.cpu_count(logical=True) or 1,
        cpu_percent=max(0.0, min(100.0, cpu_percent)),
        memory_used_bytes=int(memory.used),
        memory_total_bytes=int(memory.total),
        memory_percent=float(memory.percent),
    )


def _group(title: str) -> tuple[QtWidgets.QGroupBox, QtWidgets.QVBoxLayout]:
    box = QtWidgets.QGroupBox(title)
    layout = QtWidgets.QVBoxLayout(box)
    layout.setContentsMargins(12, 14, 12, 10)
    layout.setSpacing(8)
    return box, layout


class ProjectRemoteExplorer(QtWidgets.QFrame):
    """Project files, jobs, merge plans, and allowed SSH roots."""

    itemActivated = Signal(str)
    refreshRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("projectExplorer")
        self.setMinimumWidth(250)
        self.setMaximumWidth(330)
        self._remote_snapshots: dict[str, dict] = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QtWidgets.QFrame()
        header.setObjectName("dockHeader")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(10, 7, 8, 7)
        title = QtWidgets.QLabel("项目与远程资源")
        title.setObjectName("dockTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        layout.addWidget(header)

        toolbar = QtWidgets.QFrame()
        toolbar.setObjectName("explorerToolbar")
        toolbar_layout = QtWidgets.QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 5, 8, 5)
        toolbar_layout.setSpacing(5)
        self.refresh_btn = QtWidgets.QPushButton("↻")
        self.refresh_btn.setObjectName("toolIcon")
        self.refresh_btn.setToolTip("刷新本地项目与队列")
        self.refresh_btn.clicked.connect(self.refreshRequested)
        toolbar_layout.addWidget(self.refresh_btn)
        toolbar_layout.addStretch(1)
        layout.addWidget(toolbar)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName("projectTree")
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.NoSelection
        )
        self.tree.setIndentation(17)
        self.tree.setAnimated(True)
        self.tree.itemActivated.connect(
            lambda item, _column: self.itemActivated.emit(
                item.toolTip(0) or item.text(0)
            )
        )
        layout.addWidget(self.tree, 1)

        self.resource_summary = ResourceSummaryWidget()
        layout.addWidget(self.resource_summary)
        self.refresh(())
        allowed_roots = QtWidgets.QLabel("双击 INP 或 ODB 可载入主界面")
        allowed_roots.setObjectName("explorerFooter")
        layout.addWidget(allowed_roots)

    def refresh(
        self,
        queue_items: tuple[QueueItem, ...] | list[QueueItem],
        selected_inp: str = "",
        *,
        scheduler_ready: bool = True,
        resource_snapshot: LocalResourceSnapshot | None = None,
    ) -> None:
        self.tree.clear()
        known_inputs: list[str] = []
        if selected_inp:
            known_inputs.append(selected_inp)
        for item in queue_items:
            path = str(item.source_inp_path or item.inp_path or "").strip()
            if path and path not in known_inputs:
                known_inputs.append(path)

        project_name = "当前项目"
        if known_inputs:
            project_name = Path(known_inputs[0]).parent.name or "当前项目"
        project = QtWidgets.QTreeWidgetItem([project_name])

        inputs = QtWidgets.QTreeWidgetItem(["输入文件"])
        if known_inputs:
            for path in known_inputs:
                child = QtWidgets.QTreeWidgetItem([Path(path).name])
                child.setToolTip(0, path)
                inputs.addChild(child)
        else:
            inputs.addChild(QtWidgets.QTreeWidgetItem(["尚未选择 INP"]))

        results = QtWidgets.QTreeWidgetItem(["结果文件"])
        result_paths: set[Path] = set()
        for input_path in known_inputs:
            parent = Path(input_path).parent
            if not parent.is_dir():
                continue
            try:
                result_paths.update(parent.glob("*.odb"))
            except OSError:
                continue
        if result_paths:
            for result_path in sorted(result_paths, key=lambda path: path.name.lower()):
                child = QtWidgets.QTreeWidgetItem([result_path.name])
                child.setToolTip(0, str(result_path))
                results.addChild(child)
        else:
            results.addChild(QtWidgets.QTreeWidgetItem(["尚无 ODB 结果"]))

        jobs = QtWidgets.QTreeWidgetItem(["作业"])
        if queue_items:
            for item in queue_items:
                jobs.addChild(
                    QtWidgets.QTreeWidgetItem(
                        [f"{item.job_name or '未命名作业'}　{item.status or '未提交'}"]
                    )
                )
        else:
            jobs.addChild(QtWidgets.QTreeWidgetItem(["尚无调度作业"]))

        merge_plans = QtWidgets.QTreeWidgetItem(["合并计划"])
        restart_items = [item for item in queue_items if item.oldjob_name or item.oldjob_path]
        if restart_items:
            for item in restart_items:
                merge_plans.addChild(
                    QtWidgets.QTreeWidgetItem([f"{item.oldjob_name or '前置作业'} → {item.job_name}"])
                )
        else:
            merge_plans.addChild(QtWidgets.QTreeWidgetItem(["尚无重启动合并计划"]))
        project.addChildren([inputs, results, jobs, merge_plans])

        servers = QtWidgets.QTreeWidgetItem(["SSH 服务器"])
        self.server_root_item = servers
        self.tree.addTopLevelItems([project, servers])
        servers.setHidden(True)
        self._rebuild_remote_tree()
        self.tree.expandAll()
        self.resource_summary.refresh(
            queue_items,
            scheduler_ready=scheduler_ready,
            resource_snapshot=resource_snapshot,
        )

    def apply_remote_snapshot(self, snapshot: dict) -> None:
        profile_name = str(snapshot.get("profile_name") or "未命名服务器")
        self._remote_snapshots[profile_name] = dict(snapshot)
        self._rebuild_remote_tree()

    def _rebuild_remote_tree(self) -> None:
        self.server_root_item.takeChildren()
        if not self._remote_snapshots:
            self.server_root_item.addChild(
                QtWidgets.QTreeWidgetItem(["○ 尚未连接远程服务器"])
            )
        for snapshot in self._remote_snapshots.values():
            connected = bool(snapshot.get("connected", False))
            profile_name = str(snapshot.get("profile_name") or "未命名服务器")
            status_marker = "●" if connected else "○"
            server_item = QtWidgets.QTreeWidgetItem(
                [f"{status_marker} {profile_name}　{'已连接' if connected else '未连接'}"]
            )
            for root in snapshot.get("allowed_roots") or ():
                server_item.addChild(QtWidgets.QTreeWidgetItem([str(root)]))
            self.server_root_item.addChild(server_item)
            server_item.setExpanded(True)
        self.server_root_item.setExpanded(True)


class ResourceSummaryWidget(QtWidgets.QFrame):
    """Real local resource and scheduler summary."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("resourceSummary")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(3)
        title = QtWidgets.QLabel("资源总览")
        title.setObjectName("dockTitle")
        layout.addWidget(title)
        self.cpu_label = QtWidgets.QLabel()
        self.memory_label = QtWidgets.QLabel()
        self.job_label = QtWidgets.QLabel()
        self.scheduler_label = QtWidgets.QLabel()
        layout.addWidget(self.cpu_label)
        layout.addWidget(self.memory_label)
        layout.addWidget(self.job_label)
        layout.addWidget(self.scheduler_label)
        self.refresh((), scheduler_ready=False)

    def refresh(
        self,
        queue_items: tuple[QueueItem, ...] | list[QueueItem],
        *,
        scheduler_ready: bool,
        resource_snapshot: LocalResourceSnapshot | None = None,
    ) -> None:
        snapshot = resource_snapshot or capture_local_resource_snapshot()
        logical_cpus = snapshot.logical_cpus
        cpu_percent = snapshot.cpu_percent
        busy_cpus = min(logical_cpus, round(logical_cpus * cpu_percent / 100))
        used_gb = snapshot.memory_used_bytes / (1024**3)
        total_gb = snapshot.memory_total_bytes / (1024**3)
        running = sum(1 for item in queue_items if item.status in ACTIVE_STATUSES)
        pending = sum(1 for item in queue_items if "等待" in str(item.status or ""))
        self.cpu_label.setText(
            f"CPU　{busy_cpus} / {logical_cpus} 线程（{cpu_percent:.0f}%）"
        )
        self.memory_label.setText(
            f"内存　{used_gb:.1f} / {total_gb:.1f} GB（{snapshot.memory_percent:.0f}%）"
        )
        self.job_label.setText(
            f"队列　{len(queue_items)} 个 · 运行 {running} · 等待 {pending}"
        )
        self.scheduler_label.setText(
            "● Scheduler Core 已初始化"
            if scheduler_ready
            else "○ Scheduler Core 未初始化"
        )
        self.scheduler_label.setObjectName(
            "successText" if scheduler_ready else "warningText"
        )
        self.scheduler_label.style().unpolish(self.scheduler_label)
        self.scheduler_label.style().polish(self.scheduler_label)


class JobConfigurationWorkbench(QtWidgets.QWidget):
    """Primary job submission and protected ODB merge workspace."""

    submitRequested = Signal()
    previewRequested = Signal()
    connectionTestRequested = Signal()
    chooseInputRequested = Signal()
    chooseOriginalRequested = Signal()
    chooseFortranRequested = Signal()
    chooseCalculationRootRequested = Signal()
    chooseArchiveRootRequested = Signal()
    chooseMergeOriginalRequested = Signal()
    chooseMergeRestartRequested = Signal()
    chooseMergeOutputRequested = Signal()
    mergeExecuteRequested = Signal()
    mergeStopRequested = Signal()
    jobNameChanged = Signal(str)

    def __init__(
        self,
        wizard: SubmissionWizardDialog,
        bridge: RemoteFrontendBridge,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.wizard = wizard
        self.bridge = bridge
        self._syncing = False
        self.setObjectName("jobConfiguration")

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        root.addWidget(scroll)
        content = QtWidgets.QWidget()
        scroll.setWidget(content)
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(12, 10, 12, 12)
        content_layout.setSpacing(8)
        title = QtWidgets.QLabel("作业配置与 ODB 合并")
        title.setObjectName("workbenchPageTitle")
        content_layout.addWidget(title)

        input_group, input_layout = _group("作业输入")
        input_form = QtWidgets.QFormLayout()
        input_form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.job_name_edit = QtWidgets.QLineEdit()
        self.job_name_edit.setPlaceholderText("选择 INP 后自动生成")
        self.original_job_edit = QtWidgets.QLineEdit(self)
        self.original_job_edit.setPlaceholderText("选择依赖 ODB 后自动生成")
        self.original_job_edit.hide()
        self.oldjob_path_edit = QtWidgets.QLineEdit()
        self.oldjob_path_edit.setPlaceholderText("可选：上一阶段作业 ODB")
        self.input_path_edit = QtWidgets.QLineEdit()
        self.input_path_edit.setPlaceholderText("选择本机 INP，或输入服务器现有 INP 路径")
        self.fortran_path_edit = QtWidgets.QLineEdit()
        self.fortran_path_edit.setPlaceholderText("可选：Fortran 用户子程序")
        original_row = QtWidgets.QWidget()
        original_row.setObjectName("formFieldRow")
        original_row_layout = QtWidgets.QHBoxLayout(original_row)
        original_row_layout.setContentsMargins(0, 0, 0, 0)
        original_row_layout.setSpacing(5)
        original_row_layout.addWidget(self.oldjob_path_edit, 1)
        self.choose_original_btn = QtWidgets.QPushButton("…")
        self.choose_original_btn.setObjectName("compactPicker")
        original_row_layout.addWidget(self.choose_original_btn)
        input_row = QtWidgets.QWidget()
        input_row.setObjectName("formFieldRow")
        input_row_layout = QtWidgets.QHBoxLayout(input_row)
        input_row_layout.setContentsMargins(0, 0, 0, 0)
        input_row_layout.setSpacing(5)
        input_row_layout.addWidget(self.input_path_edit, 1)
        self.choose_input_btn = QtWidgets.QPushButton("…")
        self.choose_input_btn.setObjectName("compactPicker")
        input_row_layout.addWidget(self.choose_input_btn)
        fortran_row = QtWidgets.QWidget()
        fortran_row.setObjectName("formFieldRow")
        fortran_row_layout = QtWidgets.QHBoxLayout(fortran_row)
        fortran_row_layout.setContentsMargins(0, 0, 0, 0)
        fortran_row_layout.setSpacing(5)
        fortran_row_layout.addWidget(self.fortran_path_edit, 1)
        self.choose_fortran_btn = QtWidgets.QPushButton("…")
        self.choose_fortran_btn.setObjectName("compactPicker")
        fortran_row_layout.addWidget(self.choose_fortran_btn)
        self.execution_combo = WorkbenchComboBox()
        self.execution_combo.addItem("本机计算", ExecutionLocation.LOCAL)
        self.execution_combo.addItem(
            "上传至服务器（尚未启用）",
            ExecutionLocation.SERVER_UPLOAD,
        )
        self.execution_combo.addItem(
            "服务器现有文件（尚未启用）",
            ExecutionLocation.SERVER_EXISTING,
        )
        self.execution_combo.setCurrentIndex(0)
        for index in (1, 2):
            item = self.execution_combo.model().item(index)
            if item is not None:
                item.setEnabled(False)
                item.setToolTip("远程执行 Adapter 尚未实现")
        self.calculation_root_edit = QtWidgets.QLineEdit()
        self.calculation_root_edit.setPlaceholderText(
            "可选：将作业复制到本机 SSD 工作目录计算"
        )
        calculation_root_row = self._picker_row(
            self.calculation_root_edit,
            "选择…",
            "calculationRootPicker",
        )
        self.choose_calculation_root_btn = calculation_root_row[1]
        self.archive_root_edit = QtWidgets.QLineEdit()
        self.archive_root_edit.setPlaceholderText("可选：计算完成后归档结果")
        archive_root_row = self._picker_row(
            self.archive_root_edit,
            "选择…",
            "archiveRootPicker",
        )
        self.choose_archive_root_btn = archive_root_row[1]
        input_form.addRow("作业名", self.job_name_edit)
        input_form.addRow("输入文件", input_row)
        input_form.addRow("依赖 ODB", original_row)
        input_form.addRow("用户子程序", fortran_row)
        input_form.addRow("执行策略", self.execution_combo)
        input_form.addRow("SSD 工作目录", calculation_root_row[0])
        input_form.addRow("结果归档目录", archive_root_row[0])
        input_layout.addLayout(input_form)
        self.path_decision_label = QtWidgets.QLabel(
            "尚未选择 INP，无法判定执行目录。"
        )
        self.path_decision_label.setObjectName("hint")
        self.path_decision_label.setWordWrap(True)
        input_layout.addWidget(self.path_decision_label)
        self.archive_path_edit = QtWidgets.QLineEdit()
        self.archive_path_edit.setPlaceholderText("提交时根据实际路径生成")
        self.archive_path_edit.setReadOnly(True)
        archive_row = QtWidgets.QFormLayout()
        archive_row.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        archive_row.addRow("完成归档路径", self.archive_path_edit)
        input_layout.addLayout(archive_row)

        server_group, server_layout = _group("执行环境与资源")
        server_form = QtWidgets.QFormLayout()
        server_form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.server_combo = WorkbenchComboBox()
        self.server_combo.addItem("尚未连接服务器")
        self.host_edit = QtWidgets.QLineEdit()
        self.host_edit.setPlaceholderText("服务器主机名或 IP")
        self.username_edit = QtWidgets.QLineEdit()
        self.username_edit.setPlaceholderText("Windows 用户名")
        self.authentication_combo = WorkbenchComboBox()
        self.authentication_combo.addItems(["SSH 私钥", "SSH Agent", "用户名/密码"])
        self.fingerprint_edit = QtWidgets.QLineEdit()
        self.fingerprint_edit.setPlaceholderText("首次连接后确认")
        self.abaqus_command_edit = QtWidgets.QLineEdit("abaqus.bat")
        self.abaqus_command_edit.setPlaceholderText("本机 Abaqus 启动命令")
        self.compute_root_edit = QtWidgets.QLineEdit()
        self.compute_root_edit.setPlaceholderText("服务器 SSD 计算根目录")
        self.allowed_roots_edit = QtWidgets.QLineEdit()
        self.allowed_roots_edit.setPlaceholderText("多个允许目录用分号分隔")
        self.cpu_spin = SegmentedSpinBox()
        self.cpu_spin.setRange(0, MAX_CPUS)
        self.cpu_spin.setValue(max(1, DEFAULT_CPUS))
        self.cpu_spin.setSpecialValueText("全部")
        self.memory_value_edit = QtWidgets.QLineEdit("90")
        self.memory_value_edit.setMaximumWidth(90)
        self.memory_unit_combo = WorkbenchComboBox()
        self.memory_unit_combo.addItems(MEMORY_OPTIONS)
        self.memory_unit_combo.setCurrentText("%")
        memory_row = QtWidgets.QWidget()
        memory_row.setObjectName("formFieldRow")
        memory_layout = QtWidgets.QHBoxLayout(memory_row)
        memory_layout.setContentsMargins(0, 0, 0, 0)
        memory_layout.setSpacing(5)
        memory_layout.addWidget(self.memory_value_edit, 1)
        memory_layout.addWidget(self.memory_unit_combo)
        self.priority_combo = WorkbenchComboBox()
        self.priority_combo.addItem("普通", 0)
        self.priority_combo.addItem("高", 10)
        self.priority_combo.addItem("低", -10)
        server_form.addRow("服务器", self.server_combo)
        server_form.addRow("主机", self.host_edit)
        server_form.addRow("用户名", self.username_edit)
        server_form.addRow("认证", self.authentication_combo)
        server_form.addRow("指纹", self.fingerprint_edit)
        server_form.addRow("Abaqus 命令", self.abaqus_command_edit)
        server_form.addRow("SSD 计算根目录", self.compute_root_edit)
        server_form.addRow("允许目录", self.allowed_roots_edit)
        server_form.addRow("CPU", self.cpu_spin)
        server_form.addRow("内存", memory_row)
        server_form.addRow("优先级", self.priority_combo)
        for remote_widget in (
            self.server_combo,
            self.host_edit,
            self.username_edit,
            self.authentication_combo,
            self.fingerprint_edit,
            self.compute_root_edit,
            self.allowed_roots_edit,
        ):
            remote_widget.hide()
            label = server_form.labelForField(remote_widget)
            if label is not None:
                label.hide()
        server_layout.addLayout(server_form)
        self.test_connection_btn = QtWidgets.QPushButton("测试连接")
        self.test_connection_btn.hide()
        server_layout.addWidget(self.test_connection_btn)

        run_group, run_layout = _group("运行选项")
        run_row = QtWidgets.QHBoxLayout()
        self.interactive_check = QtWidgets.QCheckBox("交互输出")
        self.datacheck_check = QtWidgets.QCheckBox("仅数据检查")
        self.notify_check = QtWidgets.QCheckBox("结束提醒")
        self.notify_check.setChecked(True)
        self.max_parallel_spin = SegmentedSpinBox()
        self.max_parallel_spin.setRange(1, 999)
        self.max_parallel_spin.setValue(self.wizard.max_parallel_spin.value())
        run_row.addWidget(self.interactive_check)
        run_row.addWidget(self.datacheck_check)
        run_row.addWidget(self.notify_check)
        run_row.addSpacing(16)
        run_row.addWidget(QtWidgets.QLabel("本地队列并行上限"))
        run_row.addWidget(self.max_parallel_spin)
        run_row.addStretch(1)
        self.preview_submit_btn = QtWidgets.QPushButton("预览提交命令")
        self.preview_submit_btn.setObjectName("light")
        self.submit_job_btn = QtWidgets.QPushButton("提交作业")
        self.submit_job_btn.setObjectName("primary")
        run_row.addWidget(self.preview_submit_btn)
        run_row.addWidget(self.submit_job_btn)
        run_layout.addLayout(run_row)

        merge_group, merge_layout = _group("ODB 合并")
        merge_form = QtWidgets.QFormLayout()
        merge_form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.merge_form = merge_form
        self.merge_original_edit = QtWidgets.QLineEdit()
        self.merge_original_edit.setPlaceholderText("请选择第一阶段（原始）ODB")
        self.merge_original_edit.setReadOnly(True)
        self.merge_restart_edit = QtWidgets.QLineEdit()
        self.merge_restart_edit.setPlaceholderText("请选择第二阶段（重启动）ODB")
        self.merge_restart_edit.setReadOnly(True)
        self.merge_output_edit = QtWidgets.QLineEdit()
        self.merge_output_edit.setPlaceholderText("选择结果保存位置，文件名以 _joined.odb 结尾")
        original_merge_row = self._picker_row(
            self.merge_original_edit,
            "选择…",
            "mergeOriginalPicker",
        )
        self.choose_merge_original_btn = original_merge_row[1]
        restart_merge_row = self._picker_row(
            self.merge_restart_edit,
            "选择…",
            "mergeRestartPicker",
        )
        self.choose_merge_restart_btn = restart_merge_row[1]
        output_merge_row = self._picker_row(
            self.merge_output_edit,
            "保存到…",
            "mergeOutputPicker",
        )
        self.choose_merge_output_btn = output_merge_row[1]
        merge_form.addRow("原始 ODB", original_merge_row[0])
        merge_form.addRow("重启动 ODB", restart_merge_row[0])
        merge_form.addRow("输出 ODB", output_merge_row[0])
        merge_layout.addLayout(merge_form)

        merge_options = QtWidgets.QHBoxLayout()
        self.history_check = QtWidgets.QCheckBox("包含历史输出")
        self.compress_check = QtWidgets.QCheckBox("压缩合并结果")
        self.copy_original_check = QtWidgets.QCheckBox("保留原始 ODB")
        self.copy_original_check.setToolTip(
            "启用后向 restartjoin 命令添加 copyoriginal。"
            "应用仍会保留两个源 ODB 的 _original 安全副本；"
            "Abaqus 生成的 Restart_ 工作结果验证成功后发布为所选 joined 文件。"
        )
        self.history_check.setChecked(True)
        self.merge_name_source_combo = WorkbenchComboBox()
        self.merge_name_source_combo.addItem("采用重启动 ODB 名称", "restart")
        self.merge_name_source_combo.addItem("采用原始 ODB 名称", "original")
        self.merge_name_source_combo.addItem("自定义输出名称", "custom")
        self.conflict_combo = WorkbenchComboBox()
        self.conflict_combo.addItem("已有结果时自动编号", "auto_number")
        self.conflict_combo.addItem("已有结果时询问是否覆盖", "confirm")
        merge_options.addWidget(self.history_check)
        merge_options.addWidget(self.compress_check)
        merge_options.addWidget(self.copy_original_check)
        merge_options.addStretch(1)
        merge_layout.addLayout(merge_options)

        merge_actions = QtWidgets.QHBoxLayout()
        merge_actions.addWidget(QtWidgets.QLabel("结果命名"))
        merge_actions.addWidget(self.merge_name_source_combo)
        merge_actions.addSpacing(8)
        merge_actions.addWidget(QtWidgets.QLabel("结果冲突"))
        merge_actions.addWidget(self.conflict_combo)
        merge_actions.addStretch(1)
        self.merge_execute_btn = QtWidgets.QPushButton("执行合并")
        self.merge_execute_btn.setObjectName("primary")
        self.merge_stop_btn = QtWidgets.QPushButton("停止")
        self.merge_stop_btn.setEnabled(False)
        merge_actions.addWidget(self.merge_execute_btn)
        merge_actions.addWidget(self.merge_stop_btn)
        merge_layout.addLayout(merge_actions)
        self.merge_progress = QtWidgets.QProgressBar()
        self.merge_progress.setRange(0, 100)
        self.merge_progress.setValue(0)
        self.merge_progress.setTextVisible(False)
        self.merge_progress.hide()
        merge_layout.addWidget(self.merge_progress)
        self.merge_status_label = QtWidgets.QLabel(
            "请选择两个源 ODB 和输出位置。源文件不会被修改。"
        )
        self.merge_status_label.setObjectName("hint")
        self.merge_status_label.setWordWrap(True)
        merge_layout.addWidget(self.merge_status_label)
        content_layout.addWidget(merge_group)
        content_layout.addWidget(input_group)
        content_layout.addWidget(server_group)
        content_layout.addWidget(run_group)
        content_layout.addStretch(1)

        self._connect_signals()
        self.sync_to_wizard()

    @staticmethod
    def _picker_row(
        edit: QtWidgets.QLineEdit,
        button_text: str,
        button_name: str,
    ) -> tuple[QtWidgets.QWidget, QtWidgets.QPushButton]:
        container = QtWidgets.QWidget()
        container.setObjectName("formFieldRow")
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(edit, 1)
        button = QtWidgets.QPushButton(button_text)
        button.setObjectName(button_name)
        layout.addWidget(button)
        return container, button

    def set_oldjob_path(self, path: str) -> None:
        self.oldjob_path_edit.setText(path)
        if path:
            original_name = Path(path).stem
            if original_name.lower().endswith("_original"):
                original_name = original_name[: -len("_original")]
            self.original_job_edit.setText(original_name)

    def set_fortran_path(self, path: str) -> None:
        self.fortran_path_edit.setText(path)

    def set_merge_original_path(self, path: str) -> None:
        self.merge_original_edit.setText(path)
        if self.merge_name_source_combo.currentData() == "original":
            self._apply_merge_name_source()
        self._refresh_merge_controls()

    def set_merge_restart_path(self, path: str) -> None:
        self.merge_restart_edit.setText(path)
        if path and self.merge_name_source_combo.currentData() != "custom":
            self._apply_merge_name_source()
        self._refresh_merge_controls()

    def set_merge_output_path(self, path: str) -> None:
        self.merge_output_edit.setText(path)
        self._refresh_merge_controls()

    def merge_values(self) -> dict[str, object]:
        return {
            "original_odb": self.merge_original_edit.text().strip(),
            "restart_odb": self.merge_restart_edit.text().strip(),
            "output_odb": self.merge_output_edit.text().strip(),
            "abaqus_command": self.abaqus_command_edit.text().strip(),
            "include_history": self.history_check.isChecked(),
            "compress_result": self.compress_check.isChecked(),
            "copy_original": self.copy_original_check.isChecked(),
            "conflict_strategy": self.conflict_combo.currentData(),
        }

    def local_job_draft(self) -> LocalJobDraft:
        return LocalJobDraft(
            inp_file=self.input_path_edit.text().strip(),
            job_name=self.job_name_edit.text().strip(),
            cpus=self.cpu_spin.value(),
            memory_value=self.memory_value_edit.text().strip(),
            memory_unit=self.memory_unit_combo.currentText(),
            oldjob_path=self.oldjob_path_edit.text().strip(),
            fortran_path=self.fortran_path_edit.text().strip(),
            interactive=self.interactive_check.isChecked(),
            datacheck=self.datacheck_check.isChecked(),
            notify=self.notify_check.isChecked(),
            abaqus_command=self.abaqus_command_edit.text().strip(),
            priority=int(self.priority_combo.currentData() or 0),
            max_parallel=self.max_parallel_spin.value(),
            calculation_root_dir=self.calculation_root_edit.text().strip(),
            archive_dir=self.archive_root_edit.text().strip(),
        )

    def export_settings(self) -> dict[str, object]:
        draft = self.local_job_draft()
        return {
            "inp_file": draft.inp_file,
            "job_name": draft.job_name,
            "oldjob_path": draft.oldjob_path,
            "fortran_path": draft.fortran_path,
            "cpus": draft.cpus,
            "memory_value": draft.memory_value,
            "memory_unit": draft.memory_unit,
            "interactive": draft.interactive,
            "datacheck": draft.datacheck,
            "notify": draft.notify,
            "abaqus_command": draft.abaqus_command,
            "priority": draft.priority,
            "max_parallel": draft.max_parallel,
            "calculation_root_dir": draft.calculation_root_dir,
            "archive_dir": draft.archive_dir,
            "merge_include_history": self.history_check.isChecked(),
            "merge_compress_result": self.compress_check.isChecked(),
            "merge_copy_original": self.copy_original_check.isChecked(),
            "merge_name_source": self.merge_name_source_combo.currentData(),
            "merge_conflict_strategy": self.conflict_combo.currentData(),
        }

    def apply_settings(self, values: dict[str, object]) -> None:
        self._syncing = True
        try:
            for edit, key in (
                (self.input_path_edit, "inp_file"),
                (self.job_name_edit, "job_name"),
                (self.oldjob_path_edit, "oldjob_path"),
                (self.fortran_path_edit, "fortran_path"),
                (self.memory_value_edit, "memory_value"),
                (self.abaqus_command_edit, "abaqus_command"),
                (self.calculation_root_edit, "calculation_root_dir"),
                (self.archive_root_edit, "archive_dir"),
            ):
                if key in values:
                    edit.setText(str(values.get(key) or ""))
            self.cpu_spin.setValue(int(values.get("cpus", DEFAULT_CPUS)))
            self.max_parallel_spin.setValue(
                int(values.get("max_parallel", self.max_parallel_spin.value()))
            )
            memory_unit = str(values.get("memory_unit") or "%")
            if memory_unit in MEMORY_OPTIONS:
                self.memory_unit_combo.setCurrentText(memory_unit)
            priority_index = self.priority_combo.findData(
                int(values.get("priority", 0))
            )
            if priority_index >= 0:
                self.priority_combo.setCurrentIndex(priority_index)
            for checkbox, key, default in (
                (self.interactive_check, "interactive", False),
                (self.datacheck_check, "datacheck", False),
                (self.notify_check, "notify", True),
                (self.history_check, "merge_include_history", True),
                (self.compress_check, "merge_compress_result", False),
                (self.copy_original_check, "merge_copy_original", False),
            ):
                checkbox.setChecked(bool(values.get(key, default)))
            name_source_index = self.merge_name_source_combo.findData(
                values.get("merge_name_source", "restart")
            )
            if name_source_index >= 0:
                self.merge_name_source_combo.setCurrentIndex(name_source_index)
            conflict_index = self.conflict_combo.findData(
                values.get("merge_conflict_strategy", "auto_number")
            )
            if conflict_index >= 0:
                self.conflict_combo.setCurrentIndex(conflict_index)
        except (TypeError, ValueError):
            pass
        finally:
            self._syncing = False
        self.sync_to_wizard()

    @staticmethod
    def _merge_source_stem(path: Path) -> str:
        stem = path.stem
        return stem[: -len("_original")] if stem.lower().endswith("_original") else stem

    def _apply_merge_name_source(self) -> None:
        source = self.merge_name_source_combo.currentData()
        if source == "custom":
            self.merge_output_edit.setFocus(
                QtCore.Qt.FocusReason.OtherFocusReason
            )
            return
        source_text = (
            self.merge_original_edit.text().strip()
            if source == "original"
            else self.merge_restart_edit.text().strip()
        )
        if not source_text:
            return
        source_path = Path(source_text)
        output_text = self.merge_output_edit.text().strip()
        output_parent = (
            Path(output_text).parent
            if output_text
            else (
                Path(self.merge_restart_edit.text().strip()).parent
                if self.merge_restart_edit.text().strip()
                else source_path.parent
            )
        )
        self.merge_output_edit.setText(
            str(
                output_parent
                / f"{self._merge_source_stem(source_path)}_joined.odb"
            )
        )

    def _refresh_merge_controls(self) -> None:
        ready = all(
            (
                self.merge_original_edit.text().strip(),
                self.merge_restart_edit.text().strip(),
                self.merge_output_edit.text().strip(),
                self.abaqus_command_edit.text().strip(),
            )
        )
        self.merge_execute_btn.setEnabled(bool(ready))
        if ready:
            self.merge_status_label.setText(
                f"结果将保存到：{self.merge_output_edit.text().strip()}"
            )
        else:
            self.merge_status_label.setText(
                "请选择两个源 ODB 和输出位置。源文件不会被修改。"
            )

    def set_merge_busy(self, busy: bool) -> None:
        self.merge_execute_btn.setEnabled(
            not busy
            and all(
                (
                    self.merge_original_edit.text().strip(),
                    self.merge_restart_edit.text().strip(),
                    self.merge_output_edit.text().strip(),
                )
            )
        )
        self.merge_stop_btn.setEnabled(busy)
        self.merge_progress.setVisible(busy)
        for widget in (
            self.choose_merge_original_btn,
            self.choose_merge_restart_btn,
            self.choose_merge_output_btn,
            self.history_check,
            self.compress_check,
            self.copy_original_check,
            self.merge_name_source_combo,
            self.conflict_combo,
        ):
            widget.setEnabled(not busy)

    def set_merge_status(self, text: str, *, state: str = "hint") -> None:
        object_name = {
            "success": "successText",
            "error": "warningText",
        }.get(state, "hint")
        self.merge_status_label.setText(text)
        self.merge_status_label.setObjectName(object_name)
        self.merge_status_label.style().unpolish(self.merge_status_label)
        self.merge_status_label.style().polish(self.merge_status_label)

    def reset_for_new_job(self) -> None:
        for edit in (
            self.job_name_edit,
            self.original_job_edit,
            self.oldjob_path_edit,
            self.input_path_edit,
            self.fortran_path_edit,
            self.merge_original_edit,
            self.merge_restart_edit,
            self.merge_output_edit,
        ):
            edit.clear()
        self.execution_combo.setCurrentIndex(0)
        self.workbench_focus()
        self.sync_to_wizard()

    def workbench_focus(self) -> None:
        self.input_path_edit.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    def _connect_signals(self) -> None:
        for widget, signal_name in (
            (self.job_name_edit, "textChanged"),
            (self.original_job_edit, "textChanged"),
            (self.oldjob_path_edit, "textChanged"),
            (self.input_path_edit, "textChanged"),
            (self.fortran_path_edit, "textChanged"),
            (self.execution_combo, "currentIndexChanged"),
            (self.server_combo, "currentIndexChanged"),
            (self.host_edit, "textChanged"),
            (self.username_edit, "textChanged"),
            (self.authentication_combo, "currentIndexChanged"),
            (self.fingerprint_edit, "textChanged"),
            (self.abaqus_command_edit, "textChanged"),
            (self.compute_root_edit, "textChanged"),
            (self.allowed_roots_edit, "textChanged"),
            (self.calculation_root_edit, "textChanged"),
            (self.archive_root_edit, "textChanged"),
            (self.cpu_spin, "valueChanged"),
            (self.memory_value_edit, "textChanged"),
            (self.memory_unit_combo, "currentIndexChanged"),
            (self.max_parallel_spin, "valueChanged"),
            (self.priority_combo, "currentIndexChanged"),
            (self.merge_name_source_combo, "currentIndexChanged"),
            (self.conflict_combo, "currentIndexChanged"),
        ):
            getattr(widget, signal_name).connect(self.sync_to_wizard)
        for checkbox in (
            self.interactive_check,
            self.datacheck_check,
            self.notify_check,
            self.history_check,
            self.compress_check,
            self.copy_original_check,
        ):
            checkbox.toggled.connect(self.sync_to_wizard)
        for edit in (
            self.merge_original_edit,
            self.merge_restart_edit,
            self.merge_output_edit,
        ):
            edit.textChanged.connect(self._refresh_merge_controls)
        self.abaqus_command_edit.textChanged.connect(self._refresh_merge_controls)
        self.merge_name_source_combo.currentIndexChanged.connect(
            self._apply_merge_name_source
        )
        self.job_name_edit.textChanged.connect(self.jobNameChanged)
        self.input_path_edit.textChanged.connect(
            lambda _text: self.jobNameChanged.emit(self.job_name_edit.text())
        )
        self.original_job_edit.textChanged.connect(
            lambda _text: self.jobNameChanged.emit(self.job_name_edit.text())
        )
        self.test_connection_btn.clicked.connect(self._request_connection_test)
        self.choose_input_btn.clicked.connect(self.chooseInputRequested)
        self.choose_original_btn.clicked.connect(self.chooseOriginalRequested)
        self.choose_fortran_btn.clicked.connect(self.chooseFortranRequested)
        self.choose_calculation_root_btn.clicked.connect(
            self.chooseCalculationRootRequested
        )
        self.choose_archive_root_btn.clicked.connect(self.chooseArchiveRootRequested)
        self.choose_merge_original_btn.clicked.connect(
            self.chooseMergeOriginalRequested
        )
        self.choose_merge_restart_btn.clicked.connect(
            self.chooseMergeRestartRequested
        )
        self.choose_merge_output_btn.clicked.connect(self.chooseMergeOutputRequested)
        self.merge_execute_btn.clicked.connect(self.mergeExecuteRequested)
        self.merge_stop_btn.clicked.connect(self.mergeStopRequested)
        self.preview_submit_btn.clicked.connect(self.previewRequested)
        self.submit_job_btn.clicked.connect(self.submitRequested)
        self._refresh_merge_controls()

    def _request_connection_test(self) -> None:
        self.sync_to_wizard()
        if not self.wizard.host_edit.text().strip():
            self.connectionTestRequested.emit()
            return
        self.wizard.request_connection_test()

    def sync_to_wizard(self, *_args) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            location = self.execution_combo.currentData()
            input_path = self.input_path_edit.text().strip()
            if input_path and not self.job_name_edit.text().strip():
                inferred_name = PureWindowsPath(input_path.replace("/", "\\")).stem
                self.job_name_edit.setText(inferred_name)
            if location == ExecutionLocation.LOCAL:
                self.wizard.location_local.setChecked(True)
            elif location == ExecutionLocation.SERVER_UPLOAD:
                self.wizard.location_upload.setChecked(True)
            else:
                self.wizard.location_existing.setChecked(True)
            self.wizard.remote_path_edit.setText(self.input_path_edit.text().strip())
            if location != ExecutionLocation.SERVER_EXISTING:
                self.wizard.inp_row.set_path(self.input_path_edit.text().strip())
            self.wizard.oldjob_row.set_path(self.oldjob_path_edit.text().strip())
            self.wizard.for_row.set_path(self.fortran_path_edit.text().strip())
            self.wizard.current_job_edit.setText(self.job_name_edit.text().strip())
            self.wizard.original_job_edit.setText(self.original_job_edit.text().strip())
            self.wizard.server_combo.setCurrentIndex(self.server_combo.currentIndex())
            self.wizard.host_edit.setText(self.host_edit.text().strip())
            self.wizard.username_edit.setText(self.username_edit.text().strip())
            self.wizard.auth_combo.setCurrentIndex(self.authentication_combo.currentIndex())
            self.wizard.fingerprint_edit.setText(self.fingerprint_edit.text().strip())
            command = self.abaqus_command_edit.text().split(" ·", 1)[0].strip()
            self.wizard.abaqus_command_edit.setText(command)
            self.wizard.compute_root_edit.setText(self.compute_root_edit.text().strip())
            self.wizard.allowed_roots_edit.setText(
                self.allowed_roots_edit.text().strip()
            )
            self.wizard.cpus_spin.setValue(self.cpu_spin.value())
            self.wizard.memory_value.setText(self.memory_value_edit.text().strip())
            self.wizard.memory_unit.setCurrentText(
                self.memory_unit_combo.currentText()
            )
            self.wizard.max_parallel_spin.setValue(self.max_parallel_spin.value())
            self.wizard.interactive_check.setChecked(self.interactive_check.isChecked())
            self.wizard.datacheck_check.setChecked(self.datacheck_check.isChecked())
            self.wizard.notify_check.setChecked(self.notify_check.isChecked())
            self.wizard.auto_merge_check.setChecked(False)
            self.wizard.history_check.setChecked(self.history_check.isChecked())
            self.wizard.compress_check.setChecked(self.compress_check.isChecked())
            self.wizard.server_merge_check.setChecked(
                location != ExecutionLocation.LOCAL
            )
            self.wizard.retain_originals_check.setChecked(True)
            self.wizard.conflict_strategy_combo.setCurrentIndex(
                self.conflict_combo.currentIndex()
            )
            self.wizard.sync_review()
            if not input_path:
                decision = "尚未选择 INP，无法判定执行目录。"
                archive_path = ""
            elif location == ExecutionLocation.LOCAL:
                calculation_root = self.calculation_root_edit.text().strip()
                archive_root = self.archive_root_edit.text().strip()
                job_name = (
                    self.job_name_edit.text().strip()
                    or PureWindowsPath(input_path.replace("/", "\\")).stem
                )
                if calculation_root:
                    decision = (
                        f"本机 SSD 计算：工作目录将在 "
                        f"{Path(calculation_root) / job_name} 下创建"
                    )
                else:
                    decision = f"本机计算：工作目录为 {Path(input_path).parent}"
                archive_path = (
                    str(Path(archive_root) / job_name)
                    if archive_root
                    else str(Path(input_path).parent)
                )
            else:
                decision = "远程服务器未连接，尚未获取 SSD 根目录，无法执行路径判定。"
                archive_path = ""
            self.path_decision_label.setText(decision)
            self.archive_path_edit.setText(archive_path)
        finally:
            self._syncing = False


class WorkbenchPropertiesPanel(QtWidgets.QFrame):
    """Right-side properties, quotas, dependencies, and operations."""

    saveRequested = Signal()
    submitRequested = Signal()
    stopRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("propertiesPanel")
        self.setMinimumWidth(300)
        self.setMaximumWidth(380)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("属性与操作")
        title.setObjectName("dockTitle")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(QtWidgets.QLabel("×"))
        layout.addLayout(header)
        self.status_label = QtWidgets.QLabel("状态：　未选择作业")
        self.status_label.setObjectName("hint")
        layout.addWidget(self.status_label)

        info_group, info_layout = _group("作业信息")
        self.job_info = QtWidgets.QLabel("尚未选择或提交作业")
        self.job_info.setWordWrap(True)
        info_layout.addWidget(self.job_info)
        layout.addWidget(info_group)

        quota_group, quota_layout = _group("本机资源（实时）")
        self.resource_rows: dict[str, tuple[QtWidgets.QProgressBar, QtWidgets.QLabel]] = {}
        for label_text in ("CPU", "内存", "作业"):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(label_text))
            bar = ResourceProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            row.addWidget(bar, 1)
            detail_label = QtWidgets.QLabel("未获取")
            row.addWidget(detail_label)
            quota_layout.addLayout(row)
            self.resource_rows[label_text] = (bar, detail_label)
        self.external_label = QtWidgets.QLabel("远程资源：未连接，未获取")
        self.external_label.setObjectName("hint")
        quota_layout.addWidget(self.external_label)
        layout.addWidget(quota_group)

        dependencies, dependency_layout = _group("依赖关系")
        self.dependency_label = QtWidgets.QLabel("尚未选择作业")
        dependency_layout.addWidget(self.dependency_label)
        layout.addWidget(dependencies)
        layout.addStretch(1)

        operations, operations_layout = _group("危险操作")
        self.save_btn = QtWidgets.QPushButton("保存配置")
        self.save_btn.setObjectName("primary")
        self.submit_btn = QtWidgets.QPushButton("提交作业")
        self.submit_btn.setObjectName("success")
        self.stop_btn = QtWidgets.QPushButton("温和停止")
        self.stop_btn.setObjectName("outlineDanger")
        operations_layout.addWidget(self.save_btn)
        operations_layout.addWidget(self.submit_btn)
        stop_row = QtWidgets.QHBoxLayout()
        stop_row.addWidget(self.stop_btn)
        stop_row.addWidget(QtWidgets.QLabel("超时后再确认强制终止"))
        operations_layout.addLayout(stop_row)
        layout.addWidget(operations)

        self.save_btn.clicked.connect(self.saveRequested)
        self.submit_btn.clicked.connect(self.submitRequested)
        self.stop_btn.clicked.connect(self.stopRequested)
        self.submit_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.refresh(())

    def refresh(
        self,
        queue_items: tuple[QueueItem, ...] | list[QueueItem],
        selected_item: QueueItem | None = None,
        resource_snapshot: LocalResourceSnapshot | None = None,
    ) -> None:
        snapshot = resource_snapshot or capture_local_resource_snapshot()
        cpu_percent = max(0, min(100, round(snapshot.cpu_percent)))
        logical_cpus = snapshot.logical_cpus
        active_count = sum(1 for item in queue_items if item.status in ACTIVE_STATUSES)
        max_jobs = max(1, len(queue_items))
        job_percent = round(active_count / max_jobs * 100)
        cpu_bar, cpu_detail = self.resource_rows["CPU"]
        cpu_bar.setValue(cpu_percent)
        cpu_detail.setText(f"{cpu_percent}% · {logical_cpus} 线程")
        memory_bar, memory_detail = self.resource_rows["内存"]
        memory_bar.setValue(round(snapshot.memory_percent))
        memory_detail.setText(
            f"{snapshot.memory_used_bytes / (1024**3):.1f} / "
            f"{snapshot.memory_total_bytes / (1024**3):.1f} GB"
        )
        job_bar, job_detail = self.resource_rows["作业"]
        job_bar.setValue(job_percent)
        job_detail.setText(f"{active_count} / {len(queue_items)}")

        if selected_item is None:
            self.status_label.setText("状态：　未选择作业")
            self.status_label.setObjectName("hint")
            self.job_info.setText("尚未选择或提交作业")
            self.dependency_label.setText("尚未选择作业")
            self.stop_btn.setEnabled(False)
        else:
            self.status_label.setText(f"状态：　{selected_item.status or '未知'}")
            self.status_label.setObjectName(
                "successText"
                if selected_item.status in ACTIVE_STATUSES
                else "warningText"
            )
            input_path = selected_item.source_inp_path or selected_item.inp_path or "未记录"
            self.job_info.setText(
                f"作业名：　{selected_item.job_name or '未命名'}\n"
                f"原始作业：{selected_item.oldjob_name or '无'}\n"
                f"输入文件：{input_path}\n"
                f"工作目录：{selected_item.effective_work_dir or '尚未分配'}\n"
                f"消息：　　{selected_item.message or '无'}"
            )
            self.dependency_label.setText(
                f"前置作业：{selected_item.oldjob_name or '无'}\n"
                f"依赖数量：{len(selected_item.dependency_job_ids)}"
            )
            self.stop_btn.setEnabled(selected_item.status in ACTIVE_STATUSES)
            self.submit_btn.setEnabled(True)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def set_draft(
        self,
        *,
        job_name: str,
        original_job: str,
        input_path: str,
    ) -> None:
        if not any((job_name, original_job, input_path)):
            return
        self.status_label.setText("状态：　未提交")
        self.status_label.setObjectName("warningText")
        self.job_info.setText(
            f"作业名：　{job_name or '未命名'}\n"
            f"原始作业：{original_job or '无'}\n"
            f"输入文件：{input_path or '尚未选择'}\n"
            "工作目录：提交时判定\n"
            "消息：　　当前为未提交配置"
        )
        self.dependency_label.setText(f"前置作业：{original_job or '无'}")
        self.submit_btn.setEnabled(bool(input_path))

    def apply_remote_snapshot(self, snapshot: dict) -> None:
        if not snapshot.get("connected", False):
            self.external_label.setText("远程资源：未连接，未获取")
            self.external_label.setObjectName("hint")
        else:
            profile_name = str(snapshot.get("profile_name") or "远程服务器")
            cpu_used = snapshot.get("cpu_used")
            cpu_total = snapshot.get("cpu_total")
            memory_used = snapshot.get("memory_used_gb")
            memory_total = snapshot.get("memory_total_gb")
            running_jobs = snapshot.get("running_jobs")
            parts = [f"远程资源：{profile_name}"]
            if cpu_used is not None and cpu_total is not None:
                parts.append(f"CPU {cpu_used}/{cpu_total}")
            if memory_used is not None and memory_total is not None:
                parts.append(f"内存 {memory_used}/{memory_total} GB")
            if running_jobs is not None:
                parts.append(f"作业 {running_jobs}")
            self.external_label.setText(" · ".join(parts))
            self.external_label.setObjectName("successText")
        self.external_label.style().unpolish(self.external_label)
        self.external_label.style().polish(self.external_label)


class WorkbenchLogDock(QtWidgets.QTabWidget):
    """Bottom dock with runtime, transfer, validation, and problem tabs."""

    def __init__(self, history: QtWidgets.QPlainTextEdit, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("workbenchLogDock")
        history.setObjectName("workbenchHistory")
        self.addTab(history, "运行日志")
        self.transfer_table = self._build_table("文件传输")
        self.merge_table = self._build_table("合并验证")
        self.problem_table = self._build_table("问题")
        self.addTab(self.transfer_table, "文件传输")
        self.addTab(self.merge_table, "合并验证")
        self.addTab(self.problem_table, "问题")
        self.setMinimumHeight(175)
        self.setMaximumHeight(255)

    @staticmethod
    def _build_table(kind: str) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, 4)
        table.setObjectName("dockTable")
        table.setHorizontalHeaderLabels(["时间", "来源", "级别", "消息"])
        table.setToolTip(f"{kind}记录")
        header = table.horizontalHeader()
        header.setObjectName("dockTableHeader")
        header.setFixedHeight(28)
        header.setDefaultAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setStretchLastSection(True)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(28)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        return table

    @staticmethod
    def append_event(table: QtWidgets.QTableWidget, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        row = table.rowCount()
        table.insertRow(row)
        values = (
            str(payload.get("time") or ""),
            str(payload.get("source") or ""),
            str(payload.get("level") or "信息"),
            str(payload.get("message") or ""),
        )
        for column, value in enumerate(values):
            table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        table.scrollToBottom()


__all__ = [
    "JobConfigurationWorkbench",
    "LocalResourceSnapshot",
    "ProjectRemoteExplorer",
    "WorkbenchLogDock",
    "WorkbenchPropertiesPanel",
    "capture_local_resource_snapshot",
]
