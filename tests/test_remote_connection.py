import json
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import abaqus_submitter.remote_connection as remote_connection
from abaqus_submitter.qt_compat import QtCore, QtWidgets
from abaqus_submitter.remote_connection import (
    HostKeyConfirmationRequired,
    RemoteConnectionManager,
    ServerConnectionRequest,
    ServerCredentials,
    normalize_fingerprint,
    parse_resource_snapshot,
)
from abaqus_submitter.remote_frontend import ServerProfileDraft
from abaqus_submitter.server_ui import ServerConnectionDialog


class _FakeKey:
    fingerprint = "SHA256:server-key"


class _FakeTransport:
    def __init__(self) -> None:
        self.keepalive = None

    def is_active(self) -> bool:
        return True

    def set_keepalive(self, seconds: int) -> None:
        self.keepalive = seconds

    def get_remote_server_key(self) -> _FakeKey:
        return _FakeKey()


class _FakeChannel:
    def recv_exit_status(self) -> int:
        return 0


class _FakeStream:
    def __init__(self, payload: bytes = b"") -> None:
        self.payload = payload
        self.channel = _FakeChannel()

    def read(self) -> bytes:
        return self.payload


class _FakeClient:
    def __init__(self) -> None:
        self.policy = None
        self.connect_kwargs = {}
        self.transport = _FakeTransport()
        self.closed = False

    def set_missing_host_key_policy(self, policy) -> None:
        self.policy = policy

    def connect(self, **kwargs) -> None:
        self.connect_kwargs = kwargs
        self.policy.missing_host_key(self, kwargs["hostname"], _FakeKey())

    def get_transport(self) -> _FakeTransport:
        return self.transport

    def exec_command(self, command: str, timeout: int):
        self.command = command
        self.command_timeout = timeout
        snapshot = {
            "computer_name": "COMPUTE01",
            "cpu_used": 4,
            "cpu_total": 32,
            "cpu_percent": 12.5,
            "memory_used_gb": 16.5,
            "memory_total_gb": 64,
            "compute_root": r"D:\Abaqus_Cal",
            "compute_root_exists": True,
            "allowed_roots": [r"D:\Abaqus_Cal"],
            "abaqus_command_available": True,
        }
        return (
            _FakeStream(),
            _FakeStream(json.dumps(snapshot).encode("utf-8")),
            _FakeStream(),
        )

    def close(self) -> None:
        self.closed = True


def _profile(**changes) -> ServerProfileDraft:
    values = {
        "profile_name": "compute-01",
        "host": "10.0.0.8",
        "username": "abaqus_user",
        "authentication": "用户名/密码",
        "host_fingerprint": "SHA256:server-key",
        "abaqus_command": "abaqus.bat",
        "compute_root": r"D:\Abaqus_Cal",
        "allowed_roots": (r"D:\Abaqus_Cal", r"E:\Projects"),
        "port": 22,
        "private_key_path": "",
    }
    values.update(changes)
    return ServerProfileDraft(**values)


class RemoteConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_unknown_host_key_stops_before_authentication_is_accepted(self) -> None:
        client = _FakeClient()
        manager = RemoteConnectionManager(lambda: client)
        request = ServerConnectionRequest(
            _profile(host_fingerprint=""),
            ServerCredentials(password="secret"),
        )

        with self.assertRaises(HostKeyConfirmationRequired) as context:
            manager.connect(request)

        self.assertEqual(context.exception.fingerprint, "SHA256:server-key")
        self.assertTrue(client.closed)

    def test_changed_host_key_is_rejected_without_replacing_saved_fingerprint(
        self,
    ) -> None:
        client = _FakeClient()
        manager = RemoteConnectionManager(lambda: client)
        request = ServerConnectionRequest(
            _profile(host_fingerprint="SHA256:previous-key"),
            ServerCredentials(password="secret"),
        )

        with self.assertRaisesRegex(Exception, "主机指纹已变化"):
            manager.connect(request)

        self.assertTrue(client.closed)

    def test_password_auth_uses_only_explicit_password_credentials(self) -> None:
        client = _FakeClient()
        manager = RemoteConnectionManager(lambda: client)

        snapshot = manager.connect(
            ServerConnectionRequest(
                _profile(),
                ServerCredentials(password="secret"),
            )
        )

        self.assertEqual(client.connect_kwargs["password"], "secret")
        self.assertFalse(client.connect_kwargs["allow_agent"])
        self.assertFalse(client.connect_kwargs["look_for_keys"])
        self.assertEqual(client.transport.keepalive, 30)
        self.assertEqual(snapshot["cpu_total"], 32)
        self.assertEqual(snapshot["memory_total_gb"], 64.0)
        self.assertTrue(snapshot["abaqus_command_available"])
        self.assertIn("-EncodedCommand", client.command)

    def test_agent_auth_does_not_fall_back_to_local_key_search(self) -> None:
        client = _FakeClient()
        manager = RemoteConnectionManager(lambda: client)

        manager.connect(
            ServerConnectionRequest(
                _profile(authentication="SSH Agent"),
            )
        )

        self.assertTrue(client.connect_kwargs["allow_agent"])
        self.assertFalse(client.connect_kwargs["look_for_keys"])
        self.assertNotIn("password", client.connect_kwargs)

    def test_private_key_passphrase_is_not_used_as_password_auth(self) -> None:
        client = _FakeClient()
        manager = RemoteConnectionManager(lambda: client)
        fake_private_key = object()
        request = ServerConnectionRequest(
            _profile(
                authentication="SSH 私钥",
                private_key_path=r"C:\Keys\id_ed25519",
            ),
            ServerCredentials(private_key_passphrase="key-secret"),
        )

        with mock.patch(
            "abaqus_submitter.remote_connection.paramiko.PKey.from_path",
            return_value=fake_private_key,
        ) as from_path:
            manager.connect(request)

        from_path.assert_called_once()
        self.assertEqual(client.connect_kwargs["pkey"], fake_private_key)
        self.assertNotIn("password", client.connect_kwargs)

    def test_persistent_profile_never_contains_credentials(self) -> None:
        request = ServerConnectionRequest(
            _profile(),
            ServerCredentials(
                password="login-secret",
                private_key_passphrase="key-secret",
            ),
        )

        payload = request.profile.persistent_payload()

        self.assertNotIn("password", payload)
        self.assertNotIn("private_key_passphrase", payload)
        self.assertNotIn("login-secret", json.dumps(payload))
        self.assertNotIn("key-secret", json.dumps(payload))

    def test_resource_snapshot_rejects_non_json_output(self) -> None:
        with self.assertRaisesRegex(Exception, "有效 JSON"):
            parse_resource_snapshot(
                "not-json",
                profile=_profile(),
                fingerprint="SHA256:server-key",
            )

    def test_resource_script_does_not_require_cim_permissions(self) -> None:
        command = remote_connection._powershell_encoded_command(_profile())
        script = remote_connection._powershell_resource_script(_profile())

        self.assertNotIn("Get-CimInstance", script)
        self.assertIn("GetSystemTimes", script)
        self.assertIn("GetActiveProcessorCount", script)
        self.assertIn("GlobalMemoryStatusEx", script)
        self.assertIn("$ProgressPreference = 'SilentlyContinue'", script)
        self.assertIn("-OutputFormat Text", command)
        self.assertLess(len(command), 8191)

    def test_remote_stderr_uses_the_server_windows_code_page(self) -> None:
        encoded_error = "命令行太长。".encode("gb18030")

        decoded_error = remote_connection.decode_remote_stream(encoded_error)

        self.assertEqual(decoded_error, "命令行太长。")

    def test_clixml_errors_are_converted_to_readable_text(self) -> None:
        raw_error = (
            '#< CLIXML\r\n<Objs xmlns="http://schemas.microsoft.com/'
            'powershell/2004/04"><S S="Error">Get-CimInstance : '
            '拒绝访问_x000D__x000A_HRESULT 0x80041003</S></Objs>'
        )

        cleaned = remote_connection.clean_remote_error_output(raw_error)

        self.assertNotIn("#< CLIXML", cleaned)
        self.assertNotIn("_x000D_", cleaned)
        self.assertIn("Get-CimInstance : 拒绝访问", cleaned)
        self.assertIn("HRESULT 0x80041003", cleaned)

    def test_fingerprint_normalization_removes_only_base64_padding(self) -> None:
        self.assertEqual(
            normalize_fingerprint("sha256:abc==="),
            "SHA256:abc",
        )

    def test_server_dialog_hides_secrets_from_saved_profile(self) -> None:
        dialog = ServerConnectionDialog()
        dialog.authentication_combo.setCurrentText("用户名/密码")
        dialog.profile_name_edit.setText("compute-01")
        dialog.host_edit.setText("10.0.0.8")
        dialog.username_edit.setText("user")
        dialog.secret_edit.setText("secret")
        saved_payloads = []
        dialog.saveRequested.connect(saved_payloads.append)

        dialog.save_btn.click()
        request = dialog.connection_request()

        self.assertEqual(request.credentials.password, "secret")
        self.assertEqual(len(saved_payloads), 1)
        self.assertNotIn("password", saved_payloads[0])
        self.assertNotIn("secret", json.dumps(saved_payloads[0]))
        dialog.close()

    def test_server_dialog_authentication_fields_match_selected_mode(self) -> None:
        dialog = ServerConnectionDialog()
        dialog.show()
        self.app.processEvents()

        dialog.authentication_combo.setCurrentText("SSH Agent")
        self.app.processEvents()
        self.assertFalse(dialog.private_key_row.isVisibleTo(dialog))
        self.assertFalse(dialog.secret_edit.isVisibleTo(dialog))

        dialog.authentication_combo.setCurrentText("SSH 私钥")
        self.app.processEvents()
        self.assertTrue(dialog.private_key_row.isVisibleTo(dialog))
        self.assertTrue(dialog.secret_edit.isVisibleTo(dialog))
        dialog.close()

    def test_server_port_is_a_validated_direct_input(self) -> None:
        dialog = ServerConnectionDialog()
        requests = []
        dialog.connectRequested.connect(requests.append)
        dialog.show()
        self.app.processEvents()

        self.assertIsInstance(dialog.port_edit, QtWidgets.QLineEdit)
        self.assertEqual(dialog.port_edit.text(), "22")
        self.assertTrue(dialog.port_edit.hasAcceptableInput())
        self.assertFalse(hasattr(dialog, "port_spin"))
        self.assertIs(dialog.host_edit.parentWidget(), dialog.host_port_row)
        self.assertIs(dialog.port_edit.parentWidget(), dialog.host_port_row)
        self.assertLess(
            dialog.host_edit.geometry().right(),
            dialog.port_edit.geometry().left(),
        )
        self.assertEqual(dialog.port_edit.width(), 110)

        dialog.port_edit.setText("65536")
        dialog.connect_btn.click()
        self.assertFalse(requests)
        self.assertIn("1–65535", dialog.status_label.text())

        dialog.port_edit.setText("2222")
        dialog.host_edit.setText("192.168.1.50")
        dialog.username_edit.setText("abaqus")
        dialog.connect_btn.click()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].profile.port, 2222)
        dialog.close()

    def test_server_dialog_keeps_remote_error_details_out_of_status_row(
        self,
    ) -> None:
        dialog = ServerConnectionDialog()
        dialog.show()
        self.app.processEvents()
        original_host_height = dialog.host_edit.height()
        detailed_error = "#< CLIXML\n" + ("Get-CimInstance 拒绝访问 " * 80)

        dialog.set_remote_operation_error(detailed_error)
        self.app.processEvents()

        self.assertEqual(
            dialog.status_label.text(),
            "状态：连接或读取资源失败，详情请查看主界面运行日志。",
        )
        self.assertNotIn("CLIXML", dialog.status_label.text())
        self.assertFalse(dialog.status_label.wordWrap())
        self.assertLessEqual(
            dialog.status_label.maximumHeight(),
            dialog.status_label.fontMetrics().height() + 8,
        )
        self.assertEqual(dialog.host_edit.height(), original_host_height)
        dialog.close()

    def test_remote_failure_keeps_full_detail_in_main_runtime_log(self) -> None:
        from abaqus_submitter.main import MainWindow

        window = MainWindow()
        window.open_server_configuration()
        detailed_error = "读取服务器资源失败：#< CLIXML\nHRESULT 0x80041003"

        try:
            window.on_remote_connection_failed(detailed_error)

            self.assertIn(detailed_error, window.history.toPlainText())
            self.assertNotIn(
                "CLIXML",
                window._server_dialog.status_label.text(),
            )
            self.assertEqual(
                window.history.verticalScrollBarPolicy(),
                QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded,
            )
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
