"""Qt presentation components for the cluster-console mainline."""

from __future__ import annotations

import os
from pathlib import PureWindowsPath

import psutil

from .command import MEMORY_OPTIONS
from .constants import DEFAULT_CPUS, MAX_CPUS, calculate_default_joblist_parallel
from .qt_compat import QtCore, QtWidgets, Signal
from .remote_frontend import (
    ExecutionLocation,
    OdbMergeDraft,
    RemoteFrontendBridge,
    RemoteJobDraft,
    ServerProfileDraft,
)
from .ui_components import (
    FilePickerRow,
    SegmentedSpinBox,
    WorkbenchComboBox,
    configure_path_picker_button,
)


def _card(object_name: str = "dashboardCard") -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
    frame = QtWidgets.QFrame()
    frame.setObjectName(object_name)
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(8)
    return frame, layout


def _section_title(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("sectionTitle")
    return label


class ClusterTopologyWidget(QtWidgets.QWidget):
    """Dense resource view backed only by real local or remote snapshots."""

    nodeSelected = Signal(str)
    refreshRequested = Signal()

    COLUMNS = (
        "资源",
        "连接状态",
        "CPU",
        "内存",
        "运行 / 等待",
        "计算目录",
        "更新时间",
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue_count = 0
        self._resources: dict[str, dict] = {}
        self._selected_resource_id = "local"

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(_section_title("计算资源"))
        self.queue_summary_label = QtWidgets.QLabel("队列作业：0")
        self.queue_summary_label.setObjectName("hint")
        header.addWidget(self.queue_summary_label)
        header.addStretch(1)
        self.refresh_btn = QtWidgets.QPushButton("刷新")
        self.refresh_btn.setObjectName("light")
        self.server_filter = WorkbenchComboBox()
        self.server_filter.addItems(["全部资源", "在线", "未连接"])
        header.addWidget(self.refresh_btn)
        header.addWidget(self.server_filter)
        layout.addLayout(header)

        self.resource_table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.resource_table.setObjectName("resourceTable")
        self.resource_table.setHorizontalHeaderLabels(self.COLUMNS)
        self.resource_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.resource_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.resource_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.resource_table.setAlternatingRowColors(True)
        self.resource_table.setShowGrid(False)
        self.resource_table.verticalHeader().setVisible(False)
        self.resource_table.verticalHeader().setDefaultSectionSize(32)
        resource_header = self.resource_table.horizontalHeader()
        resource_header.setStretchLastSection(False)
        resource_header.setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        resource_header.setSectionResizeMode(
            5,
            QtWidgets.QHeaderView.ResizeMode.Stretch,
        )
        layout.addWidget(self.resource_table, 1)

        details = QtWidgets.QGroupBox("所选资源详情")
        detail_layout = QtWidgets.QGridLayout(details)
        detail_layout.setColumnStretch(1, 1)
        self.detail_name = QtWidgets.QLabel()
        self.detail_status = QtWidgets.QLabel()
        self.detail_cpu = QtWidgets.QLabel()
        self.detail_memory = QtWidgets.QLabel()
        self.detail_queue = QtWidgets.QLabel()
        self.detail_path = QtWidgets.QLabel()
        self.detail_path.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.detail_jobs = QtWidgets.QLabel()
        self.detail_jobs.setWordWrap(True)
        for row, (label, value) in enumerate(
            (
                ("资源", self.detail_name),
                ("连接状态", self.detail_status),
                ("CPU", self.detail_cpu),
                ("内存", self.detail_memory),
                ("运行 / 等待", self.detail_queue),
                ("计算目录", self.detail_path),
                ("活动作业", self.detail_jobs),
            )
        ):
            detail_layout.addWidget(QtWidgets.QLabel(label), row, 0)
            detail_layout.addWidget(value, row, 1)
        layout.addWidget(details)

        self.refresh_btn.clicked.connect(self._refresh_now)
        self.server_filter.currentTextChanged.connect(self._apply_filter)
        self.resource_table.cellClicked.connect(self._on_resource_clicked)
        self.refresh_local_resource()

    def set_queue_count(self, count: int) -> None:
        self._queue_count = max(0, count)
        self.queue_summary_label.setText(f"队列作业：{self._queue_count}")
        local = self._resources.get("local")
        if local is not None:
            local["waiting_jobs"] = max(
                0,
                self._queue_count - len(local["active_jobs"]),
            )
            self._rebuild_table()

    def _refresh_now(self) -> None:
        self.refresh_local_resource()
        self.refreshRequested.emit()

    def refresh_local_resource(
        self,
        *,
        work_dir: str = "",
        active_job_text: str = "",
        logical_cpus: int | None = None,
        cpu_percent: float | None = None,
        memory_used_bytes: int | None = None,
        memory_total_bytes: int | None = None,
        memory_percent: float | None = None,
    ) -> None:
        if logical_cpus is None:
            logical_cpus = psutil.cpu_count(logical=True) or 1
        if cpu_percent is None:
            cpu_percent = psutil.cpu_percent(interval=None)
        cpu_percent = max(0, min(100, round(cpu_percent)))
        busy_cpus = min(logical_cpus, round(logical_cpus * cpu_percent / 100))
        if (
            memory_used_bytes is None
            or memory_total_bytes is None
            or memory_percent is None
        ):
            memory = psutil.virtual_memory()
            memory_used_bytes = int(memory.used)
            memory_total_bytes = int(memory.total)
            memory_percent = float(memory.percent)
        active_jobs = tuple(
            line.strip()
            for line in active_job_text.splitlines()
            if line.strip()
        )
        self._resources["local"] = {
            "resource_id": "local",
            "name": "本机",
            "status": "在线",
            "cpu": f"{busy_cpus} / {logical_cpus} 线程（{cpu_percent}%）",
            "memory": (
                f"{memory_used_bytes / (1024**3):.1f} / "
                f"{memory_total_bytes / (1024**3):.1f} GB"
                f"（{round(memory_percent)}%）"
            ),
            "running_jobs": len(active_jobs),
            "waiting_jobs": max(0, self._queue_count - len(active_jobs)),
            "compute_root": work_dir or os.getcwd(),
            "active_jobs": active_jobs,
            "updated_at": QtCore.QDateTime.currentDateTime().toString(
                "yyyy-MM-dd HH:mm:ss"
            ),
        }
        self._rebuild_table()

    def apply_remote_resource_snapshot(self, snapshot: dict) -> None:
        profile_name = str(snapshot.get("profile_name") or "").strip()
        if not profile_name:
            return
        connected = bool(snapshot.get("connected", False))
        cpu_used = snapshot.get("cpu_used")
        cpu_total = snapshot.get("cpu_total")
        memory_used = snapshot.get("memory_used_gb")
        memory_total = snapshot.get("memory_total_gb")
        cpu_text = (
            f"{cpu_used} / {cpu_total} 线程"
            if cpu_used is not None and cpu_total is not None
            else "未获取"
        )
        memory_text = (
            f"{memory_used} / {memory_total} GB"
            if memory_used is not None and memory_total is not None
            else "未获取"
        )
        active_jobs = tuple(str(job) for job in snapshot.get("active_jobs") or ())
        self._resources[profile_name] = {
            "resource_id": profile_name,
            "name": profile_name,
            "status": "在线" if connected else "未连接",
            "cpu": cpu_text,
            "memory": memory_text,
            "running_jobs": len(active_jobs),
            "waiting_jobs": int(snapshot.get("waiting_jobs", 0) or 0),
            "compute_root": str(snapshot.get("compute_root") or "未获取"),
            "active_jobs": active_jobs,
            "updated_at": str(
                snapshot.get("updated_at")
                or QtCore.QDateTime.currentDateTime().toString(
                    "yyyy-MM-dd HH:mm:ss"
                )
            ),
        }
        self._rebuild_table()

    def _resource_rows(self) -> list[dict]:
        return sorted(
            self._resources.values(),
            key=lambda resource: (
                resource["resource_id"] != "local",
                resource["name"].lower(),
            ),
        )

    def _rebuild_table(self) -> None:
        rows = self._resource_rows()
        self.resource_table.blockSignals(True)
        self.resource_table.setRowCount(len(rows))
        selected_row = 0
        for row, resource in enumerate(rows):
            values = (
                resource["name"],
                resource["status"],
                resource["cpu"],
                resource["memory"],
                f"{resource['running_jobs']} / {resource['waiting_jobs']}",
                resource["compute_root"],
                resource["updated_at"],
            )
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(
                        QtCore.Qt.ItemDataRole.UserRole,
                        resource["resource_id"],
                    )
                item.setToolTip(str(value))
                self.resource_table.setItem(row, column, item)
            if resource["resource_id"] == self._selected_resource_id:
                selected_row = row
        if rows:
            self.resource_table.selectRow(selected_row)
            self._show_resource_details(rows[selected_row])
        self.resource_table.blockSignals(False)
        self._apply_filter()

    def _apply_filter(self) -> None:
        selected_filter = self.server_filter.currentText()
        for row in range(self.resource_table.rowCount()):
            status_item = self.resource_table.item(row, 1)
            status = status_item.text() if status_item is not None else ""
            hidden = (
                selected_filter == "在线"
                and status != "在线"
                or selected_filter == "未连接"
                and status != "未连接"
            )
            self.resource_table.setRowHidden(row, hidden)

    def _on_resource_clicked(self, row: int, _column: int) -> None:
        item = self.resource_table.item(row, 0)
        if item is None:
            return
        resource_id = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        resource = self._resources.get(resource_id)
        if resource is None:
            return
        self._selected_resource_id = resource_id
        self._show_resource_details(resource)
        self.nodeSelected.emit(resource_id)

    def _show_resource_details(self, resource: dict) -> None:
        self.detail_name.setText(str(resource["name"]))
        self.detail_status.setText(str(resource["status"]))
        self.detail_cpu.setText(str(resource["cpu"]))
        self.detail_memory.setText(str(resource["memory"]))
        self.detail_queue.setText(
            f"{resource['running_jobs']} / {resource['waiting_jobs']}"
        )
        self.detail_path.setText(str(resource["compute_root"]))
        self.detail_path.setToolTip(str(resource["compute_root"]))
        active_jobs = resource["active_jobs"]
        self.detail_jobs.setText(
            "、".join(active_jobs) if active_jobs else "无"
        )


class RemoteMergeInspector(QtWidgets.QWidget):
    """Persistent SSH and ODB merge inspector."""

    def __init__(self, bridge: RemoteFrontendBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        connection_card, connection_layout = _card()
        connection_layout.addWidget(_section_title("SSH 连接"))
        connection_form = QtWidgets.QFormLayout()
        connection_form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.server_combo = WorkbenchComboBox()
        self.server_combo.addItem("尚未连接服务器")
        self.auth_combo = WorkbenchComboBox()
        self.auth_combo.addItems(["SSH 私钥", "SSH Agent", "用户名/密码"])
        self.fingerprint_label = QtWidgets.QLabel("未获取")
        self.fingerprint_label.setObjectName("warningText")
        self.abaqus_command_edit = QtWidgets.QLineEdit("abaqus.bat")
        connection_form.addRow("服务器", self.server_combo)
        connection_form.addRow("认证方式", self.auth_combo)
        connection_form.addRow("主机指纹", self.fingerprint_label)
        connection_form.addRow("Abaqus 命令", self.abaqus_command_edit)
        connection_layout.addLayout(connection_form)
        connection_actions = QtWidgets.QHBoxLayout()
        self.test_connection_btn = QtWidgets.QPushButton("测试连接")
        self.reconnect_btn = QtWidgets.QPushButton("重新连接")
        self.test_connection_btn.setObjectName("light")
        self.reconnect_btn.setObjectName("primary")
        connection_actions.addWidget(self.test_connection_btn)
        connection_actions.addWidget(self.reconnect_btn)
        connection_layout.addLayout(connection_actions)
        layout.addWidget(connection_card)

        merge_card, merge_layout = _card()
        merge_layout.addWidget(_section_title("重启动作业合并"))
        flow = QtWidgets.QLabel(
            "xxx1_original.odb + xxx2_original.odb\n→ xxx2_joined.odb"
        )
        flow.setObjectName("mergeFlow")
        flow.setWordWrap(True)
        merge_layout.addWidget(flow)
        self.auto_merge_check = QtWidgets.QCheckBox("计算完成后自动合并")
        self.history_check = QtWidgets.QCheckBox("包含历史输出")
        self.compress_check = QtWidgets.QCheckBox("压缩合并结果")
        self.server_merge_check = QtWidgets.QCheckBox("服务器端合并")
        self.retain_originals_check = QtWidgets.QCheckBox("保留原始 ODB")
        self.history_check.setChecked(True)
        self.server_merge_check.setChecked(True)
        self.retain_originals_check.setChecked(True)
        for checkbox in (
            self.auto_merge_check,
            self.history_check,
            self.compress_check,
            self.server_merge_check,
            self.retain_originals_check,
        ):
            merge_layout.addWidget(checkbox)

        result_row = QtWidgets.QHBoxLayout()
        result_row.addWidget(QtWidgets.QLabel("结果名称"))
        self.result_source_combo = WorkbenchComboBox()
        self.result_source_combo.addItems(["当前作业", "原始作业", "自定义"])
        result_row.addWidget(self.result_source_combo, 1)
        merge_layout.addLayout(result_row)
        conflict_label = QtWidgets.QLabel("冲突时自动编号 _002")
        conflict_label.setObjectName("hint")
        merge_layout.addWidget(conflict_label)
        safety_label = QtWidgets.QLabel(
            "原始源文件保持不变；复制后合并；\n只读验证成功后再发布最终 ODB。"
        )
        safety_label.setObjectName("infoBanner")
        safety_label.setWordWrap(True)
        merge_layout.addWidget(safety_label)
        layout.addWidget(merge_card)
        layout.addStretch(1)

        self.test_connection_btn.clicked.connect(self._request_connection_test)
        self.reconnect_btn.clicked.connect(
            lambda: self.bridge.reconnectRequested.emit(self.server_combo.currentText().split(" ·", 1)[0])
        )

    def _request_connection_test(self) -> None:
        self.bridge.testConnectionRequested.emit(
            {
                "profile_name": self.server_combo.currentText().split(" ·", 1)[0],
                "authentication": self.auth_combo.currentText(),
                "abaqus_command": self.abaqus_command_edit.text().strip(),
            }
        )


class SubmissionWizardDialog(QtWidgets.QDialog):
    """B-style five-step job submission workflow."""

    localSubmitRequested = Signal()
    previewRequested = Signal()

    STEP_NAMES = (
        "选择作业",
        "执行位置",
        "资源配置",
        "依赖与合并",
        "检查并提交",
    )

    def __init__(self, bridge: RemoteFrontendBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.setObjectName("submissionWizard")
        self.setWindowTitle("Abaqus 作业向导")
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self.setModal(False)
        self.resize(1180, 760)
        self.setMinimumSize(980, 680)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(14, 14, 14, 14)
        body_layout.setSpacing(14)
        body_layout.addWidget(self._build_step_navigation())
        body_layout.addWidget(self._build_pages(), 1)
        body_layout.addWidget(self._build_summary(), 0)
        root.addWidget(body, 1)
        root.addWidget(self._build_footer())

        self.current_step = 0
        self.set_step(0)
        self._connect_review_updates()

    def _build_header(self) -> QtWidgets.QWidget:
        header = QtWidgets.QFrame()
        header.setObjectName("wizardHeader")
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(18, 10, 18, 10)
        title = QtWidgets.QLabel("Abaqus 作业向导")
        title.setObjectName("wizardTitle")
        layout.addWidget(title)
        layout.addStretch(1)
        self.draft_label = QtWidgets.QLabel("新作业 · 未提交")
        self.draft_label.setObjectName("warningText")
        layout.addWidget(self.draft_label)
        return header

    def _build_step_navigation(self) -> QtWidgets.QWidget:
        navigation = QtWidgets.QFrame()
        navigation.setObjectName("wizardSteps")
        navigation.setFixedWidth(210)
        layout = QtWidgets.QVBoxLayout(navigation)
        layout.setContentsMargins(10, 12, 10, 12)
        self.step_list = QtWidgets.QListWidget()
        self.step_list.setObjectName("stepList")
        self.step_list.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        for index, name in enumerate(self.STEP_NAMES, start=1):
            self.step_list.addItem(f"{index}　{name}")
        self.step_list.currentRowChanged.connect(self.set_step)
        layout.addWidget(self.step_list, 1)
        security = QtWidgets.QLabel("密码与私钥口令不持久化，\n仅用于本次会话。")
        security.setObjectName("infoBanner")
        security.setWordWrap(True)
        layout.addWidget(security)
        return navigation

    def _build_pages(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        self.pages = QtWidgets.QStackedWidget()
        self.pages.addWidget(self._build_job_page())
        self.pages.addWidget(self._build_location_page())
        self.pages.addWidget(self._build_resource_page())
        self.pages.addWidget(self._build_merge_page())
        self.pages.addWidget(self._build_review_page())
        layout.addWidget(self.pages)
        return container

    def _build_job_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(12)
        layout.addWidget(_section_title("选择作业"))
        intro = QtWidgets.QLabel("选择 INP，以及可选的重启动 ODB 和 Fortran 子程序。")
        intro.setObjectName("hint")
        layout.addWidget(intro)

        card, card_layout = _card()
        self.inp_row = FilePickerRow("INP", "点击选择 INP 文件")
        self.oldjob_row = FilePickerRow("ODB", "点击选择重启动 ODB（可选）")
        self.for_row = FilePickerRow("FOR", "点击选择 Fortran 子程序（可选）")
        card_layout.addWidget(self.inp_row)
        card_layout.addWidget(self.oldjob_row)
        card_layout.addWidget(self.for_row)
        layout.addWidget(card)
        tip = QtWidgets.QLabel(
            "若 INP 已位于服务器，可在下一步选择“服务器现有文件”，"
            "无需先下载到本机。"
        )
        tip.setObjectName("infoBanner")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch(1)
        return page

    def _build_location_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(10)
        layout.addWidget(_section_title("选择执行位置"))
        layout.addWidget(QtWidgets.QLabel("可使用本机、上传到服务器，或直接采用服务器上的现有 INP。"))

        choices = QtWidgets.QHBoxLayout()
        self.location_group = QtWidgets.QButtonGroup(self)
        self.location_local = QtWidgets.QRadioButton("本机计算\n使用当前 Windows 工作站")
        self.location_upload = QtWidgets.QRadioButton("上传至服务器\nSFTP 上传到 SSD 临时目录")
        self.location_existing = QtWidgets.QRadioButton("服务器现有文件\n按路径规则自动判定")
        for button_id, button in enumerate(
            (self.location_local, self.location_upload, self.location_existing)
        ):
            button.setObjectName("locationChoice")
            button.setMinimumHeight(72)
            self.location_group.addButton(button, button_id)
            choices.addWidget(button, 1)
        self.location_local.setChecked(True)
        layout.addLayout(choices)

        server_card, server_layout = _card()
        server_layout.addWidget(_section_title("服务器与认证"))
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.server_combo = WorkbenchComboBox()
        self.server_combo.addItem("尚未连接服务器")
        self.host_edit = QtWidgets.QLineEdit()
        self.host_edit.setPlaceholderText("服务器主机名或 IP")
        self.username_edit = QtWidgets.QLineEdit()
        self.username_edit.setPlaceholderText("Windows 用户名")
        self.auth_combo = WorkbenchComboBox()
        self.auth_combo.addItems(["SSH 私钥", "SSH Agent", "用户名/密码"])
        self.fingerprint_edit = QtWidgets.QLineEdit()
        self.fingerprint_edit.setPlaceholderText("首次连接后确认")
        self.abaqus_command_edit = QtWidgets.QLineEdit("abaqus.bat")
        self.compute_root_edit = QtWidgets.QLineEdit()
        self.compute_root_edit.setPlaceholderText("连接后配置 SSD 计算根目录")
        self.allowed_roots_edit = QtWidgets.QLineEdit()
        self.allowed_roots_edit.setPlaceholderText("多个根目录用分号分隔")
        self.remote_path_edit = QtWidgets.QLineEdit()
        self.remote_path_edit.setPlaceholderText("选择服务器现有 INP 时填写")
        remote_path_row = QtWidgets.QWidget()
        remote_path_row.setObjectName("formFieldRow")
        remote_path_layout = QtWidgets.QHBoxLayout(remote_path_row)
        remote_path_layout.setContentsMargins(0, 0, 0, 0)
        remote_path_layout.setSpacing(5)
        remote_path_layout.addWidget(self.remote_path_edit, 1)
        self.browse_remote_btn = configure_path_picker_button(
            QtWidgets.QPushButton(),
            "浏览服务器允许目录",
        )
        remote_path_layout.addWidget(self.browse_remote_btn)
        form.addRow("服务器", self.server_combo)
        form.addRow("主机", self.host_edit)
        form.addRow("用户名", self.username_edit)
        form.addRow("认证方式", self.auth_combo)
        form.addRow("主机指纹", self.fingerprint_edit)
        form.addRow("Abaqus 命令", self.abaqus_command_edit)
        form.addRow("SSD 计算根目录", self.compute_root_edit)
        form.addRow("允许根目录", self.allowed_roots_edit)
        form.addRow("服务器 INP", remote_path_row)
        server_layout.addLayout(form)
        actions = QtWidgets.QHBoxLayout()
        self.test_connection_btn = QtWidgets.QPushButton("测试连接")
        self.connection_status_label = QtWidgets.QLabel("尚未连接服务器")
        self.connection_status_label.setObjectName("hint")
        actions.addWidget(self.test_connection_btn)
        actions.addWidget(self.connection_status_label, 1)
        server_layout.addLayout(actions)
        layout.addWidget(server_card, 1)

        path_rule = QtWidgets.QLabel(
            "目录规则：使用服务器配置中经过验证的 SSD 计算根目录。"
            "源 INP 位于 SSD 时原位计算；否则复制到临时目录，完成后归档到源目录下 "
            r"AbaqusResults\<job>\<attempt_id>\。"
        )
        path_rule.setObjectName("infoBanner")
        path_rule.setWordWrap(True)
        layout.addWidget(path_rule)

        self.test_connection_btn.clicked.connect(self.request_connection_test)
        self.browse_remote_btn.clicked.connect(self.request_remote_browse)
        return page

    def _build_resource_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(12)
        layout.addWidget(_section_title("资源配置"))
        layout.addWidget(QtWidgets.QLabel("配置单作业资源请求与本地队列并行上限。"))

        card, card_layout = _card()
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.cpus_spin = SegmentedSpinBox()
        self.cpus_spin.setObjectName("plainSpin")
        self.cpus_spin.setRange(0, MAX_CPUS)
        self.cpus_spin.setValue(DEFAULT_CPUS)
        self.cpus_spin.setSpecialValueText("全部")
        self.memory_value = QtWidgets.QLineEdit("90")
        self.memory_value.setObjectName("submitParamEdit")
        self.memory_unit = WorkbenchComboBox()
        self.memory_unit.setObjectName("submitParamCombo")
        self.memory_unit.addItems(MEMORY_OPTIONS)
        self.memory_unit.setCurrentText("%")
        memory_row = QtWidgets.QHBoxLayout()
        memory_row.addWidget(self.memory_value)
        memory_row.addWidget(self.memory_unit)
        memory_widget = QtWidgets.QWidget()
        memory_widget.setObjectName("formFieldRow")
        memory_widget.setLayout(memory_row)
        self.max_parallel_spin = SegmentedSpinBox()
        self.max_parallel_spin.setObjectName("queueMaxParallelSpin")
        self.max_parallel_spin.setRange(1, 999)
        self.max_parallel_spin.setValue(calculate_default_joblist_parallel(DEFAULT_CPUS))
        form.addRow("CPU 核心", self.cpus_spin)
        form.addRow("内存", memory_widget)
        form.addRow("本地队列并行上限", self.max_parallel_spin)
        card_layout.addLayout(form)

        self.interactive_check = QtWidgets.QCheckBox("交互输出")
        self.datacheck_check = QtWidgets.QCheckBox("仅数据检查")
        self.notify_check = QtWidgets.QCheckBox("结束提醒")
        self.notify_check.setChecked(True)
        options = QtWidgets.QHBoxLayout()
        options.addWidget(self.interactive_check)
        options.addWidget(self.datacheck_check)
        options.addWidget(self.notify_check)
        options.addStretch(1)
        card_layout.addLayout(options)
        layout.addWidget(card)

        quota, quota_layout = _card()
        quota_layout.addWidget(_section_title("远程服务器配额"))
        quota_grid = QtWidgets.QGridLayout()
        quota_grid.addWidget(QtWidgets.QLabel("CPU"), 0, 0)
        quota_grid.addWidget(QtWidgets.QLabel("未连接，未获取"), 0, 1)
        quota_grid.addWidget(QtWidgets.QLabel("内存"), 1, 0)
        quota_grid.addWidget(QtWidgets.QLabel("未连接，未获取"), 1, 1)
        quota_grid.addWidget(QtWidgets.QLabel("作业"), 2, 0)
        quota_grid.addWidget(QtWidgets.QLabel("未连接，未获取"), 2, 1)
        quota_layout.addLayout(quota_grid)
        layout.addWidget(quota)
        layout.addStretch(1)
        return page

    def _build_merge_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(10)
        layout.addWidget(_section_title("依赖与合并"))
        layout.addWidget(QtWidgets.QLabel("设置重启动作业关系、安全副本、结果命名与验证策略。"))

        card, card_layout = _card()
        names_form = QtWidgets.QFormLayout()
        names_form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.original_job_edit = QtWidgets.QLineEdit()
        self.original_job_edit.setPlaceholderText("尚未选择原始作业")
        self.current_job_edit = QtWidgets.QLineEdit()
        self.current_job_edit.setPlaceholderText("选择 INP 后自动生成")
        names_form.addRow("原始作业", self.original_job_edit)
        names_form.addRow("当前重启动作业", self.current_job_edit)
        card_layout.addLayout(names_form)
        self.merge_preview_label = QtWidgets.QLabel()
        self.merge_preview_label.setObjectName("mergeFlowLarge")
        self.merge_preview_label.setWordWrap(True)
        card_layout.addWidget(self.merge_preview_label)

        checks = QtWidgets.QGridLayout()
        self.auto_merge_check = QtWidgets.QCheckBox("计算完成后自动合并")
        self.history_check = QtWidgets.QCheckBox("包含历史输出")
        self.compress_check = QtWidgets.QCheckBox("压缩合并结果")
        self.server_merge_check = QtWidgets.QCheckBox("服务器端合并")
        self.retain_originals_check = QtWidgets.QCheckBox("保留原始 ODB")
        self.history_check.setChecked(True)
        self.server_merge_check.setChecked(True)
        self.retain_originals_check.setChecked(True)
        checks.addWidget(self.auto_merge_check, 0, 0)
        checks.addWidget(self.history_check, 0, 1)
        checks.addWidget(self.compress_check, 1, 0)
        checks.addWidget(self.server_merge_check, 1, 1)
        checks.addWidget(self.retain_originals_check, 2, 0)
        card_layout.addLayout(checks)

        naming = QtWidgets.QFormLayout()
        naming.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.result_name_source_combo = WorkbenchComboBox()
        self.result_name_source_combo.addItem("当前作业", "current")
        self.result_name_source_combo.addItem("原始作业", "original")
        self.result_name_source_combo.addItem("自定义", "custom")
        self.custom_result_name_edit = QtWidgets.QLineEdit()
        self.custom_result_name_edit.setPlaceholderText("例如 Gearbox_phase2")
        self.custom_result_name_edit.setEnabled(False)
        self.conflict_strategy_combo = WorkbenchComboBox()
        self.conflict_strategy_combo.addItem("自动编号 _002", "auto_number")
        self.conflict_strategy_combo.addItem("提交前确认", "confirm")
        naming.addRow("结果名称来源", self.result_name_source_combo)
        naming.addRow("自定义名称", self.custom_result_name_edit)
        naming.addRow("名称冲突", self.conflict_strategy_combo)
        card_layout.addLayout(naming)
        layout.addWidget(card)

        safety = QtWidgets.QLabel(
            "保护规则：xxx1.odb 保持不变；先复制为 xxx1_original.odb；"
            "原始重启动结果重命名为 xxx2_original.odb；在副本上执行不含 "
            "copyoriginal 的合并；只读验证成功后发布 joined。"
        )
        safety.setObjectName("infoBanner")
        safety.setWordWrap(True)
        layout.addWidget(safety)

        multilevel = QtWidgets.QLabel(
            "多级合并：先合并前两个，验证后的 joined 自动成为下一前置作业；"
            "下一次合并成功后删除中间 joined，保留所有 _original 和最终 joined。"
        )
        multilevel.setObjectName("infoBanner")
        multilevel.setWordWrap(True)
        layout.addWidget(multilevel)
        layout.addStretch(1)

        self.result_name_source_combo.currentIndexChanged.connect(
            lambda _index: self.custom_result_name_edit.setEnabled(
                self.result_name_source_combo.currentData() == "custom"
            )
        )
        return page

    def _build_review_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(10)
        layout.addWidget(_section_title("检查并提交"))
        layout.addWidget(QtWidgets.QLabel("确认执行位置、资源请求、目录规则与合并计划。"))
        card, card_layout = _card()
        self.review_text = QtWidgets.QLabel()
        self.review_text.setObjectName("reviewText")
        self.review_text.setWordWrap(True)
        self.review_text.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(self.review_text)
        layout.addWidget(card, 1)
        validation = QtWidgets.QLabel(
            "合并后验证：文件非零 · readOnly 打开 ODB · 至少一个 Step/Frame · "
            "restartjoin 日志无错误"
        )
        validation.setObjectName("successBanner")
        validation.setWordWrap(True)
        layout.addWidget(validation)
        return page

    def _build_summary(self) -> QtWidgets.QWidget:
        summary = QtWidgets.QFrame()
        summary.setObjectName("wizardSummary")
        summary.setFixedWidth(284)
        layout = QtWidgets.QVBoxLayout(summary)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(_section_title("提交摘要"))
        self.summary_text = QtWidgets.QLabel()
        self.summary_text.setWordWrap(True)
        self.summary_text.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary_text)
        layout.addStretch(1)
        self.summary_status = QtWidgets.QLabel("配置尚未完成")
        self.summary_status.setObjectName("warningText")
        layout.addWidget(self.summary_status)
        return summary

    def _build_footer(self) -> QtWidgets.QWidget:
        footer = QtWidgets.QFrame()
        footer.setObjectName("wizardFooter")
        layout = QtWidgets.QHBoxLayout(footer)
        layout.setContentsMargins(18, 10, 18, 10)
        self.abaqus_status_label = QtWidgets.QLabel("Abaqus 状态：待检测")
        self.abaqus_status_label.setObjectName("hint")
        layout.addWidget(self.abaqus_status_label)
        layout.addStretch(1)
        self.back_btn = QtWidgets.QPushButton("上一步")
        self.preview_btn = QtWidgets.QPushButton("预览命令")
        self.next_btn = QtWidgets.QPushButton("下一步")
        self.submit_btn = QtWidgets.QPushButton("提交作业")
        self.preview_btn.setObjectName("light")
        self.next_btn.setObjectName("primary")
        self.submit_btn.setObjectName("primary")
        layout.addWidget(self.back_btn)
        layout.addWidget(self.preview_btn)
        layout.addWidget(self.next_btn)
        layout.addWidget(self.submit_btn)
        self.back_btn.clicked.connect(lambda: self.set_step(self.current_step - 1))
        self.next_btn.clicked.connect(lambda: self.set_step(self.current_step + 1))
        self.preview_btn.clicked.connect(self.previewRequested)
        self.submit_btn.clicked.connect(self.submit_current)
        return footer

    def _connect_review_updates(self) -> None:
        self.inp_row.pathChanged.connect(self.sync_review)
        self.remote_path_edit.textChanged.connect(self.sync_review)
        self.location_group.idClicked.connect(lambda _button_id: self.sync_review())
        self.server_combo.currentTextChanged.connect(self.sync_review)
        self.cpus_spin.valueChanged.connect(self.sync_review)
        self.memory_value.textChanged.connect(self.sync_review)
        self.memory_unit.currentTextChanged.connect(self.sync_review)
        self.original_job_edit.textChanged.connect(self.sync_review)
        self.current_job_edit.textChanged.connect(self.sync_review)
        self.result_name_source_combo.currentIndexChanged.connect(self.sync_review)
        self.custom_result_name_edit.textChanged.connect(self.sync_review)
        for checkbox in (
            self.auto_merge_check,
            self.history_check,
            self.compress_check,
            self.server_merge_check,
            self.retain_originals_check,
        ):
            checkbox.toggled.connect(self.sync_review)
        self.sync_review()

    def execution_location(self) -> ExecutionLocation:
        if self.location_upload.isChecked():
            return ExecutionLocation.SERVER_UPLOAD
        if self.location_existing.isChecked():
            return ExecutionLocation.SERVER_EXISTING
        return ExecutionLocation.LOCAL

    def current_input_path(self) -> str:
        if self.execution_location() == ExecutionLocation.SERVER_EXISTING:
            return self.remote_path_edit.text().strip()
        return self.inp_row.text().strip()

    def inferred_job_name(self) -> str:
        path = self.current_input_path()
        if not path:
            return self.current_job_edit.text().strip() or "未命名作业"
        return PureWindowsPath(path.replace("/", "\\")).stem or "未命名作业"

    def build_server_profile(self) -> ServerProfileDraft:
        allowed_roots = tuple(
            part.strip()
            for part in self.allowed_roots_edit.text().split(";")
            if part.strip()
        )
        return ServerProfileDraft(
            profile_name=self.server_combo.currentText().split(" ·", 1)[0],
            host=self.host_edit.text().strip(),
            username=self.username_edit.text().strip(),
            authentication=self.auth_combo.currentText(),
            host_fingerprint=self.fingerprint_edit.text().strip(),
            abaqus_command=self.abaqus_command_edit.text().strip(),
            compute_root=self.compute_root_edit.text().strip(),
            allowed_roots=allowed_roots,
        )

    def build_merge_draft(self) -> OdbMergeDraft:
        return OdbMergeDraft(
            original_job=self.original_job_edit.text().strip() or "xxx1",
            current_job=self.current_job_edit.text().strip() or self.inferred_job_name(),
            auto_merge=self.auto_merge_check.isChecked(),
            include_history=self.history_check.isChecked(),
            compress_result=self.compress_check.isChecked(),
            server_side=self.server_merge_check.isChecked(),
            retain_originals=self.retain_originals_check.isChecked(),
            result_name_source=str(self.result_name_source_combo.currentData() or "current"),
            custom_result_name=self.custom_result_name_edit.text().strip(),
            conflict_strategy=str(self.conflict_strategy_combo.currentData() or "auto_number"),
        )

    def build_remote_draft(self) -> RemoteJobDraft:
        memory_unit = self.memory_unit.currentText()
        memory_value = self.memory_value.text().strip()
        memory = f"{memory_value}{'%' if memory_unit == '%' else memory_unit.lower()}" if memory_value else ""
        return RemoteJobDraft(
            job_name=self.inferred_job_name(),
            inp_path=self.current_input_path(),
            location=self.execution_location(),
            server=self.build_server_profile(),
            remote_path=self.remote_path_edit.text().strip(),
            cpus=self.cpus_spin.value(),
            memory=memory,
            merge=self.build_merge_draft(),
        )

    def set_step(self, step: int) -> None:
        step = max(0, min(len(self.STEP_NAMES) - 1, step))
        self.current_step = step
        self.pages.setCurrentIndex(step)
        self.step_list.blockSignals(True)
        self.step_list.setCurrentRow(step)
        self.step_list.blockSignals(False)
        self.back_btn.setEnabled(step > 0)
        self.next_btn.setVisible(step < len(self.STEP_NAMES) - 1)
        self.preview_btn.setVisible(step == len(self.STEP_NAMES) - 1)
        self.submit_btn.setVisible(step == len(self.STEP_NAMES) - 1)
        self.sync_review()

    def sync_review(self) -> None:
        inferred = self.inferred_job_name()
        if self.current_input_path() and not self.current_job_edit.text().strip():
            self.current_job_edit.blockSignals(True)
            self.current_job_edit.setText(inferred)
            self.current_job_edit.blockSignals(False)
        merge = self.build_merge_draft()
        self.merge_preview_label.setText(merge.preview())
        location_labels = {
            ExecutionLocation.LOCAL: "本机计算",
            ExecutionLocation.SERVER_UPLOAD: "上传至服务器",
            ExecutionLocation.SERVER_EXISTING: "服务器现有文件",
        }
        location_text = location_labels[self.execution_location()]
        input_path = self.current_input_path() or "尚未选择"
        server_text = "—" if self.execution_location() == ExecutionLocation.LOCAL else self.server_combo.currentText()
        summary = (
            f"作业名称\n{self.inferred_job_name()}\n\n"
            f"输入文件\n{input_path}\n\n"
            f"执行位置\n{location_text}\n\n"
            f"服务器\n{server_text}\n\n"
            f"资源\nCPU {self.cpus_spin.value()} · 内存 {self.memory_value.text()}{self.memory_unit.currentText()}\n\n"
            f"合并计划\n{merge.preview()}\n"
            f"自动合并：{'开启' if merge.auto_merge else '关闭'}"
        )
        self.summary_text.setText(summary)
        self.review_text.setText(
            summary
            + "\n\n"
            + (
                r"执行目录：本机 INP 所在目录"
                if self.execution_location() == ExecutionLocation.LOCAL
                else (
                    "执行目录：连接服务器并获取真实 SSD 根目录后自动判定；"
                    "SSD 外文件复制到服务器临时目录，完成后归档回源目录。"
                )
            )
            + "\n\n"
            + "源 ODB 不覆盖；保留 xxx1_original.odb 与 xxx2_original.odb；"
            "最终结果只在只读验证通过后发布。"
        )
        complete = bool(self.current_input_path())
        self.summary_status.setText("配置可提交" if complete else "尚未选择 INP")
        self.summary_status.setObjectName("successText" if complete else "warningText")
        self.summary_status.style().unpolish(self.summary_status)
        self.summary_status.style().polish(self.summary_status)
        self.draft_label.setText(f"{self.inferred_job_name()} · 未提交")

    def request_connection_test(self) -> None:
        self.connection_status_label.setText("请在服务器连接窗口中完成认证")
        self.bridge.testConnectionRequested.emit(self.build_server_profile())

    def request_remote_browse(self) -> None:
        self.connection_status_label.setText("已发送目录浏览请求")
        self.bridge.browseRemoteDirectoryRequested.emit(
            {
                "server": self.build_server_profile(),
                "initial_path": self.remote_path_edit.text().strip(),
            }
        )

    def submit_current(self) -> None:
        if self.execution_location() == ExecutionLocation.LOCAL:
            self.localSubmitRequested.emit()
            return
        if not self.current_input_path():
            QtWidgets.QMessageBox.warning(self, "提交作业", "请先选择或填写 INP 文件。")
            return
        self.bridge.submitRemoteJobRequested.emit(self.build_remote_draft().as_payload())


__all__ = [
    "ClusterTopologyWidget",
    "RemoteMergeInspector",
    "SubmissionWizardDialog",
]
