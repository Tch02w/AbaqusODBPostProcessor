"""Qt dialog for persistent server profiles and ephemeral SSH credentials."""

from __future__ import annotations

from dataclasses import fields

from .qt_compat import QtCore, QtGui, QtWidgets, Signal
from .remote_connection import ServerConnectionRequest, ServerCredentials
from .remote_frontend import ServerProfileDraft
from .ui_components import (
    WorkbenchComboBox,
    configure_path_picker_button,
)


class ServerConnectionDialog(QtWidgets.QDialog):
    """Collect one Windows SSH profile and initiate real connection actions."""

    connectRequested = Signal(object)
    saveRequested = Signal(object)
    refreshRequested = Signal()
    disconnectRequested = Signal()

    AUTH_PRIVATE_KEY = "SSH 私钥"
    AUTH_AGENT = "SSH Agent"
    AUTH_PASSWORD = "用户名/密码"

    def __init__(self, settings: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("serverConnectionDialog")
        self.setWindowTitle("连接服务器")
        self.setModal(False)
        self.setMinimumWidth(610)
        self.resize(680, 570)
        self._connected = False

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        heading = QtWidgets.QLabel("Windows SSH 服务器")
        heading.setObjectName("sectionTitle")
        root.addWidget(heading)
        hint = QtWidgets.QLabel(
            "密码与私钥口令只用于本次连接，不会写入配置文件。"
            "首次连接必须核对并确认 SHA256 主机指纹。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )

        self.profile_name_edit = QtWidgets.QLineEdit()
        self.profile_name_edit.setPlaceholderText("例如：compute-01")
        self.host_edit = QtWidgets.QLineEdit()
        self.host_edit.setPlaceholderText("服务器主机名或 IP")
        self.port_edit = QtWidgets.QLineEdit("22")
        self.port_edit.setObjectName("serverPortEdit")
        self.port_edit.setValidator(
            QtGui.QIntValidator(1, 65535, self.port_edit)
        )
        self.port_edit.setFixedWidth(110)
        self.port_edit.setPlaceholderText("1–65535")
        self.host_port_row = QtWidgets.QWidget()
        host_port_layout = QtWidgets.QHBoxLayout(self.host_port_row)
        host_port_layout.setContentsMargins(0, 0, 0, 0)
        host_port_layout.setSpacing(8)
        self.port_label = QtWidgets.QLabel("端口")
        self.port_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        host_port_layout.addWidget(self.host_edit, 1)
        host_port_layout.addWidget(self.port_label)
        host_port_layout.addWidget(self.port_edit)
        self.username_edit = QtWidgets.QLineEdit()
        self.username_edit.setPlaceholderText("Windows 用户名")
        self.authentication_combo = WorkbenchComboBox()
        self.authentication_combo.addItems(
            [self.AUTH_PRIVATE_KEY, self.AUTH_AGENT, self.AUTH_PASSWORD]
        )

        self.private_key_row = QtWidgets.QWidget()
        key_layout = QtWidgets.QHBoxLayout(self.private_key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(5)
        self.private_key_edit = QtWidgets.QLineEdit()
        self.private_key_edit.setPlaceholderText("OpenSSH 私钥文件")
        self.private_key_button = configure_path_picker_button(
            QtWidgets.QPushButton(),
            "选择 SSH 私钥",
        )
        key_layout.addWidget(self.private_key_edit, 1)
        key_layout.addWidget(self.private_key_button)

        self.secret_edit = QtWidgets.QLineEdit()
        self.secret_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.secret_edit.setPlaceholderText("私钥口令（未加密私钥可留空）")
        self.secret_label = QtWidgets.QLabel("私钥口令")
        self.secret_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )

        self.fingerprint_edit = QtWidgets.QLineEdit()
        self.fingerprint_edit.setReadOnly(True)
        self.fingerprint_edit.setPlaceholderText("首次连接后确认并保存")
        self.abaqus_command_edit = QtWidgets.QLineEdit("abaqus.bat")
        self.compute_root_edit = QtWidgets.QLineEdit()
        self.compute_root_edit.setPlaceholderText("服务器 SSD 计算根目录")
        self.allowed_roots_edit = QtWidgets.QLineEdit()
        self.allowed_roots_edit.setPlaceholderText("多个允许目录用分号分隔")

        form.addRow("配置名称", self.profile_name_edit)
        form.addRow("主机", self.host_port_row)
        form.addRow("用户名", self.username_edit)
        form.addRow("认证方式", self.authentication_combo)
        self.private_key_label = QtWidgets.QLabel("私钥文件")
        self.private_key_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        form.addRow(self.private_key_label, self.private_key_row)
        form.addRow(self.secret_label, self.secret_edit)
        form.addRow("主机指纹", self.fingerprint_edit)
        form.addRow("Abaqus 命令", self.abaqus_command_edit)
        form.addRow("SSD 计算根目录", self.compute_root_edit)
        form.addRow("允许根目录", self.allowed_roots_edit)
        root.addLayout(form)

        self.status_label = QtWidgets.QLabel("状态：未连接")
        self.status_label.setObjectName("hint")
        self.status_label.setWordWrap(False)
        self.status_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        status_height = self.status_label.fontMetrics().height() + 8
        self.status_label.setFixedHeight(status_height)
        root.addWidget(self.status_label)

        actions = QtWidgets.QHBoxLayout()
        self.save_btn = QtWidgets.QPushButton("保存配置")
        self.save_btn.setObjectName("light")
        self.connect_btn = QtWidgets.QPushButton("连接并读取资源")
        self.connect_btn.setObjectName("primary")
        self.refresh_btn = QtWidgets.QPushButton("刷新资源")
        self.disconnect_btn = QtWidgets.QPushButton("断开")
        self.close_btn = QtWidgets.QPushButton("关闭")
        actions.addWidget(self.save_btn)
        actions.addStretch(1)
        actions.addWidget(self.connect_btn)
        actions.addWidget(self.refresh_btn)
        actions.addWidget(self.disconnect_btn)
        actions.addWidget(self.close_btn)
        root.addLayout(actions)

        self.authentication_combo.currentTextChanged.connect(
            self._update_authentication_fields
        )
        self.private_key_button.clicked.connect(self._choose_private_key)
        self.save_btn.clicked.connect(self._emit_save_request)
        self.connect_btn.clicked.connect(self._emit_connect_request)
        self.refresh_btn.clicked.connect(self.refreshRequested)
        self.disconnect_btn.clicked.connect(self.disconnectRequested)
        self.close_btn.clicked.connect(self.close)
        self.refresh_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(False)
        self.apply_settings(settings or {})
        self._update_authentication_fields()

    def apply_settings(self, settings: dict) -> None:
        field_names = {field.name for field in fields(ServerProfileDraft)}
        profile_values = {
            key: value
            for key, value in settings.items()
            if key in field_names
        }
        if "allowed_roots" in profile_values:
            profile_values["allowed_roots"] = tuple(
                profile_values["allowed_roots"] or ()
            )
        defaults = {
            "profile_name": "",
            "host": "",
            "username": "",
            "authentication": self.AUTH_PRIVATE_KEY,
            "host_fingerprint": "",
            "abaqus_command": "abaqus.bat",
            "compute_root": "",
            "allowed_roots": (),
            "port": 22,
            "private_key_path": "",
        }
        defaults.update(profile_values)
        self.apply_profile(ServerProfileDraft(**defaults))

    def apply_profile(self, profile: ServerProfileDraft) -> None:
        self.profile_name_edit.setText(profile.profile_name)
        self.host_edit.setText(profile.host)
        self.port_edit.setText(str(profile.port))
        self.username_edit.setText(profile.username)
        index = self.authentication_combo.findText(profile.authentication)
        self.authentication_combo.setCurrentIndex(max(0, index))
        self.private_key_edit.setText(profile.private_key_path)
        self.fingerprint_edit.setText(profile.host_fingerprint)
        self.abaqus_command_edit.setText(profile.abaqus_command or "abaqus.bat")
        self.compute_root_edit.setText(profile.compute_root)
        self.allowed_roots_edit.setText("; ".join(profile.allowed_roots))

    def profile(self) -> ServerProfileDraft:
        allowed_roots = tuple(
            part.strip()
            for part in self.allowed_roots_edit.text().split(";")
            if part.strip()
        )
        return ServerProfileDraft(
            profile_name=(
                self.profile_name_edit.text().strip()
                or self.host_edit.text().strip()
            ),
            host=self.host_edit.text().strip(),
            username=self.username_edit.text().strip(),
            authentication=self.authentication_combo.currentText(),
            host_fingerprint=self.fingerprint_edit.text().strip(),
            abaqus_command=self.abaqus_command_edit.text().strip() or "abaqus.bat",
            compute_root=self.compute_root_edit.text().strip(),
            allowed_roots=allowed_roots,
            port=self._port_value(),
            private_key_path=self.private_key_edit.text().strip(),
        )

    def connection_request(self) -> ServerConnectionRequest:
        authentication = self.authentication_combo.currentText()
        credentials = ServerCredentials(
            password=(
                self.secret_edit.text()
                if authentication == self.AUTH_PASSWORD
                else ""
            ),
            private_key_passphrase=(
                self.secret_edit.text()
                if authentication == self.AUTH_PRIVATE_KEY
                else ""
            ),
        )
        return ServerConnectionRequest(self.profile(), credentials)

    def set_busy(self, busy: bool) -> None:
        self.connect_btn.setEnabled(not busy)
        self.save_btn.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy and self._connected)
        self.disconnect_btn.setEnabled(not busy and self._connected)
        if busy:
            self.status_label.setText("状态：正在连接或读取服务器资源…")
            self.status_label.setToolTip("")

    def set_connected(self, snapshot: dict) -> None:
        self._connected = True
        fingerprint = str(snapshot.get("host_fingerprint") or "")
        if fingerprint:
            self.fingerprint_edit.setText(fingerprint)
        profile_name = str(snapshot.get("profile_name") or self.profile().profile_name)
        self.status_label.setText(
            f"状态：{profile_name} 已连接，资源快照更新时间 "
            f"{snapshot.get('updated_at') or '刚刚'}"
        )
        self.status_label.setToolTip("")
        self.status_label.setObjectName("successText")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.refresh_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(True)
        self.secret_edit.clear()

    def set_disconnected(self) -> None:
        self._connected = False
        self.status_label.setText("状态：未连接")
        self.status_label.setToolTip("")
        self.status_label.setObjectName("hint")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.refresh_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(False)
        self.secret_edit.clear()

    def set_error(self, message: str) -> None:
        summary = " ".join(str(message).split()).strip()
        if len(summary) > 100:
            summary = f"{summary[:97]}..."
        self.status_label.setText(f"状态：{summary}")
        self.status_label.setToolTip(str(message))
        self.status_label.setObjectName("warningText")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def set_remote_operation_error(self, _details: str) -> None:
        self.set_error("连接或读取资源失败，详情请查看主界面运行日志。")
        self.status_label.setToolTip("")

    def _update_authentication_fields(self) -> None:
        authentication = self.authentication_combo.currentText()
        key_visible = authentication == self.AUTH_PRIVATE_KEY
        secret_visible = authentication != self.AUTH_AGENT
        self.private_key_label.setVisible(key_visible)
        self.private_key_row.setVisible(key_visible)
        self.secret_label.setVisible(secret_visible)
        self.secret_edit.setVisible(secret_visible)
        if authentication == self.AUTH_PASSWORD:
            self.secret_label.setText("密码")
            self.secret_edit.setPlaceholderText("服务器登录密码")
        else:
            self.secret_label.setText("私钥口令")
            self.secret_edit.setPlaceholderText("私钥口令（未加密私钥可留空）")
        self.secret_edit.clear()

    def _choose_private_key(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择 SSH 私钥",
            "",
            "SSH 私钥 (*);;所有文件 (*.*)",
        )
        if path:
            self.private_key_edit.setText(path)

    def _emit_connect_request(self) -> None:
        try:
            request = self.connection_request()
        except ValueError as exc:
            self.set_error(str(exc))
            self.port_edit.setFocus()
            return
        self.connectRequested.emit(request)

    def _emit_save_request(self) -> None:
        try:
            payload = self.profile().persistent_payload()
        except ValueError as exc:
            self.set_error(str(exc))
            self.port_edit.setFocus()
            return
        self.saveRequested.emit(payload)

    def _port_value(self) -> int:
        if not self.port_edit.hasAcceptableInput():
            raise ValueError("端口必须是 1–65535 之间的整数。")
        return int(self.port_edit.text())

    def closeEvent(self, event) -> None:
        self.secret_edit.clear()
        super().closeEvent(event)


__all__ = ["ServerConnectionDialog"]
