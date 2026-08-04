"""Windows SSH connection adapter and asynchronous Qt service."""

from __future__ import annotations

import base64
import gzip
import json
import re
import threading
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import paramiko

from .qt_compat import QtCore, Signal, Slot
from .remote_frontend import ServerProfileDraft


class RemoteConnectionError(RuntimeError):
    """A user-facing remote connection failure."""


class HostKeyConfirmationRequired(RemoteConnectionError):
    """Raised when a host key has not been trusted by the user yet."""

    def __init__(self, fingerprint: str) -> None:
        super().__init__(f"需要确认服务器主机指纹：{fingerprint}")
        self.fingerprint = fingerprint


@dataclass(frozen=True)
class ServerCredentials:
    """Ephemeral authentication material; never serialize this object."""

    password: str = ""
    private_key_passphrase: str = ""


@dataclass(frozen=True)
class ServerConnectionRequest:
    """One connection attempt, including ephemeral credentials."""

    profile: ServerProfileDraft
    credentials: ServerCredentials = ServerCredentials()

    def with_fingerprint(self, fingerprint: str) -> "ServerConnectionRequest":
        return replace(
            self,
            profile=replace(self.profile, host_fingerprint=fingerprint),
        )


_CLIXML_CODEPOINT_PATTERN = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _decode_clixml_codepoints(value: str) -> str:
    return _CLIXML_CODEPOINT_PATTERN.sub(
        lambda match: chr(int(match.group(1), 16)),
        value,
    )


def clean_remote_error_output(output: str) -> str:
    """Convert PowerShell CLIXML stderr into readable plain text."""
    normalized = str(output or "").strip()
    if not normalized:
        return ""
    if not normalized.startswith("#< CLIXML"):
        return _decode_clixml_codepoints(normalized)

    xml_text = normalized.partition("\n")[2].strip()
    messages: list[str] = []
    try:
        root = ElementTree.fromstring(xml_text)
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "S":
                continue
            if element.attrib.get("S") != "Error" or not element.text:
                continue
            messages.append(_decode_clixml_codepoints(element.text).strip())
    except ElementTree.ParseError:
        messages.append(_decode_clixml_codepoints(xml_text))

    readable_lines: list[str] = []
    for message in messages:
        for line in message.replace("\r\n", "\n").replace("\r", "\n").splitlines():
            stripped = line.strip()
            if stripped and (not readable_lines or stripped != readable_lines[-1]):
                readable_lines.append(stripped)
    return "\n".join(readable_lines) or "PowerShell 返回了无法解析的错误。"


def decode_remote_stream(payload: bytes) -> str:
    """Decode Windows SSH output without assuming the server code page."""
    if not payload:
        return ""
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    if len(payload) >= 4 and payload[1::2].count(0) >= len(payload) // 4:
        try:
            return payload.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def normalize_fingerprint(value: str) -> str:
    """Normalize OpenSSH SHA256 fingerprints for exact comparison."""
    normalized = value.strip()
    if not normalized:
        return ""
    if normalized.lower().startswith("sha256:"):
        return f"SHA256:{normalized.split(':', 1)[1].rstrip('=')}"
    return normalized


def key_fingerprint(key: paramiko.PKey) -> str:
    """Return the modern OpenSSH-compatible SHA256 fingerprint."""
    fingerprint = getattr(key, "fingerprint", "")
    if fingerprint:
        return normalize_fingerprint(str(fingerprint))
    import hashlib

    digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii")
    return f"SHA256:{digest.rstrip('=')}"


class _PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected_fingerprint: str) -> None:
        self.expected_fingerprint = normalize_fingerprint(expected_fingerprint)

    def missing_host_key(self, client, hostname: str, key: paramiko.PKey) -> None:
        del client, hostname
        actual = key_fingerprint(key)
        if not self.expected_fingerprint:
            raise HostKeyConfirmationRequired(actual)
        if actual != self.expected_fingerprint:
            raise RemoteConnectionError(
                "服务器主机指纹已变化，已拒绝连接。"
                f"\n已保存：{self.expected_fingerprint}"
                f"\n本次获取：{actual}"
            )


def _powershell_resource_script(profile: ServerProfileDraft) -> str:
    config = {
        "compute_root": profile.compute_root,
        "allowed_roots": list(profile.allowed_roots),
        "abaqus_command": profile.abaqus_command or "abaqus.bat",
    }
    config_json = json.dumps(config, ensure_ascii=False).encode("utf-8")
    config_base64 = base64.b64encode(config_json).decode("ascii")
    script = rf"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$configJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{config_base64}'))
$config = $configJson | ConvertFrom-Json
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace AbaqusSubmitter
{{
    public static class NativeResourceMetrics
    {{
        [StructLayout(LayoutKind.Sequential)]
        public struct FileTime
        {{
            public uint Low;
            public uint High;
        }}

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
        public class MemoryStatusEx
        {{
            public uint Length = (uint)Marshal.SizeOf(typeof(MemoryStatusEx));
            public uint MemoryLoad;
            public ulong TotalPhysical;
            public ulong AvailablePhysical;
            public ulong TotalPageFile;
            public ulong AvailablePageFile;
            public ulong TotalVirtual;
            public ulong AvailableVirtual;
            public ulong AvailableExtendedVirtual;
        }}

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool GetSystemTimes(
            out FileTime idle,
            out FileTime kernel,
            out FileTime user
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint GetActiveProcessorCount(ushort groupNumber);

        [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool GlobalMemoryStatusEx(
            [In, Out] MemoryStatusEx status
        );

        public static ulong ToUInt64(FileTime value)
        {{
            return ((ulong)value.High << 32) | value.Low;
        }}

        public static int LogicalProcessorCount()
        {{
            return (int)GetActiveProcessorCount(ushort.MaxValue);
        }}
    }}
}}
"@

function Read-CpuTimes {{
    $idle = New-Object 'AbaqusSubmitter.NativeResourceMetrics+FileTime'
    $kernel = New-Object 'AbaqusSubmitter.NativeResourceMetrics+FileTime'
    $user = New-Object 'AbaqusSubmitter.NativeResourceMetrics+FileTime'
    if (-not [AbaqusSubmitter.NativeResourceMetrics]::GetSystemTimes(
        [ref]$idle,
        [ref]$kernel,
        [ref]$user
    )) {{
        throw "GetSystemTimes failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }}
    return @(
        [AbaqusSubmitter.NativeResourceMetrics]::ToUInt64($idle),
        [AbaqusSubmitter.NativeResourceMetrics]::ToUInt64($kernel),
        [AbaqusSubmitter.NativeResourceMetrics]::ToUInt64($user)
    )
}}

$cpuBefore = @(Read-CpuTimes)
Start-Sleep -Milliseconds 250
$cpuAfter = @(Read-CpuTimes)
$idleDelta = [double]($cpuAfter[0] - $cpuBefore[0])
$systemDelta = [double](
    ($cpuAfter[1] - $cpuBefore[1]) + ($cpuAfter[2] - $cpuBefore[2])
)
$cpuLoad = if ($systemDelta -gt 0) {{
    [Math]::Max(
        0,
        [Math]::Min(100, 100 * ($systemDelta - $idleDelta) / $systemDelta)
    )
}} else {{
    0
}}
$cpuTotal = [AbaqusSubmitter.NativeResourceMetrics]::LogicalProcessorCount()
$cpuUsed = [int][Math]::Round($cpuTotal * $cpuLoad / 100)

$memory = New-Object 'AbaqusSubmitter.NativeResourceMetrics+MemoryStatusEx'
if (-not [AbaqusSubmitter.NativeResourceMetrics]::GlobalMemoryStatusEx($memory)) {{
    throw "GlobalMemoryStatusEx failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
}}
$memoryTotal = [Math]::Round([double]$memory.TotalPhysical / 1GB, 2)
$memoryFree = [Math]::Round([double]$memory.AvailablePhysical / 1GB, 2)
$validRoots = @()
foreach ($root in @($config.allowed_roots)) {{
    if ($root -and (Test-Path -LiteralPath $root -PathType Container)) {{
        $validRoots += [string](Resolve-Path -LiteralPath $root).Path
    }}
}}
$computeRoot = [string]$config.compute_root
$computeRootExists = $false
if ($computeRoot) {{
    $computeRootExists = Test-Path -LiteralPath $computeRoot -PathType Container
    if ($computeRootExists) {{
        $computeRoot = [string](Resolve-Path -LiteralPath $computeRoot).Path
    }}
}}
$abaqusAvailable = [bool](Get-Command -Name ([string]$config.abaqus_command) -ErrorAction SilentlyContinue)
[ordered]@{{
    computer_name = [string]$env:COMPUTERNAME
    cpu_used = $cpuUsed
    cpu_total = $cpuTotal
    cpu_percent = [Math]::Round($cpuLoad, 1)
    memory_used_gb = [Math]::Round($memoryTotal - $memoryFree, 2)
    memory_total_gb = $memoryTotal
    compute_root = $computeRoot
    compute_root_exists = $computeRootExists
    allowed_roots = $validRoots
    abaqus_command_available = $abaqusAvailable
}} | ConvertTo-Json -Compress
"""
    return script


def _powershell_encoded_command(profile: ServerProfileDraft) -> str:
    script = _powershell_resource_script(profile)
    compressed = gzip.compress(script.encode("utf-8"), mtime=0)
    payload = base64.b64encode(compressed).decode("ascii")
    launcher = (
        f"$bytes=[Convert]::FromBase64String('{payload}');"
        "$memoryStream=[IO.MemoryStream]::new($bytes);"
        "$gzipStream=[IO.Compression.GzipStream]::new("
        "$memoryStream,[IO.Compression.CompressionMode]::Decompress);"
        "$reader=[IO.StreamReader]::new($gzipStream,[Text.Encoding]::UTF8);"
        "& ([ScriptBlock]::Create($reader.ReadToEnd()))"
    )
    encoded = base64.b64encode(launcher.encode("utf-16-le")).decode("ascii")
    return (
        "powershell.exe -NoLogo -NoProfile -NonInteractive "
        f"-ExecutionPolicy Bypass -OutputFormat Text -EncodedCommand {encoded}"
    )


def parse_resource_snapshot(
    output: str,
    *,
    profile: ServerProfileDraft,
    fingerprint: str,
) -> dict[str, Any]:
    """Validate the PowerShell JSON and map it to the existing UI contract."""
    try:
        payload = json.loads(output.strip())
    except (TypeError, json.JSONDecodeError) as exc:
        raise RemoteConnectionError("服务器资源信息不是有效 JSON。") from exc
    if not isinstance(payload, dict):
        raise RemoteConnectionError("服务器资源信息格式无效。")
    return {
        "profile_name": profile.profile_name or profile.host,
        "host": profile.host,
        "connected": True,
        "computer_name": str(payload.get("computer_name") or ""),
        "cpu_used": int(payload.get("cpu_used") or 0),
        "cpu_total": int(payload.get("cpu_total") or 0),
        "cpu_percent": float(payload.get("cpu_percent") or 0),
        "memory_used_gb": float(payload.get("memory_used_gb") or 0),
        "memory_total_gb": float(payload.get("memory_total_gb") or 0),
        "running_jobs": 0,
        "waiting_jobs": 0,
        "active_jobs": (),
        "compute_root": str(payload.get("compute_root") or profile.compute_root),
        "compute_root_exists": bool(payload.get("compute_root_exists", False)),
        "allowed_roots": tuple(str(root) for root in payload.get("allowed_roots") or ()),
        "abaqus_command": profile.abaqus_command,
        "abaqus_command_available": bool(
            payload.get("abaqus_command_available", False)
        ),
        "host_fingerprint": fingerprint,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class RemoteConnectionManager:
    """Own one verified Paramiko SSH session."""

    def __init__(self, client_factory=paramiko.SSHClient) -> None:
        self._client_factory = client_factory
        self._client: paramiko.SSHClient | None = None
        self._request: ServerConnectionRequest | None = None
        self._fingerprint = ""
        self._connecting_client: paramiko.SSHClient | None = None
        self._lock = threading.RLock()

    @property
    def connected_profile_name(self) -> str:
        request = self._request
        return request.profile.profile_name if request is not None else ""

    def connect(self, request: ServerConnectionRequest) -> dict[str, Any]:
        profile = request.profile
        if not profile.host.strip():
            raise RemoteConnectionError("请填写服务器主机名或 IP。")
        if not profile.username.strip():
            raise RemoteConnectionError("请填写服务器用户名。")

        connect_kwargs: dict[str, Any] = {
            "hostname": profile.host.strip(),
            "port": int(profile.port),
            "username": profile.username.strip(),
            "timeout": 10,
            "banner_timeout": 10,
            "auth_timeout": 15,
            "channel_timeout": 15,
            "look_for_keys": False,
            "allow_agent": False,
        }
        authentication = profile.authentication.strip()
        if authentication == "用户名/密码":
            if not request.credentials.password:
                raise RemoteConnectionError("请输入服务器密码。")
            connect_kwargs["password"] = request.credentials.password
        elif authentication == "SSH Agent":
            connect_kwargs["allow_agent"] = True
        else:
            if not profile.private_key_path.strip():
                raise RemoteConnectionError("请选择 SSH 私钥文件。")
            try:
                private_key = paramiko.PKey.from_path(
                    Path(profile.private_key_path),
                    password=request.credentials.private_key_passphrase or None,
                )
            except (OSError, paramiko.SSHException) as exc:
                raise RemoteConnectionError(f"无法读取 SSH 私钥：{exc}") from exc
            connect_kwargs["pkey"] = private_key

        client = self._client_factory()
        client.set_missing_host_key_policy(
            _PinnedHostKeyPolicy(profile.host_fingerprint)
        )
        with self._lock:
            self._connecting_client = client
        try:
            client.connect(**connect_kwargs)
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise RemoteConnectionError("SSH 连接建立后立即失效。")
            transport.set_keepalive(30)
            fingerprint = key_fingerprint(transport.get_remote_server_key())
            snapshot = self._read_snapshot(client, profile, fingerprint)
        except HostKeyConfirmationRequired:
            client.close()
            raise
        except RemoteConnectionError:
            client.close()
            raise
        except (OSError, paramiko.SSHException) as exc:
            client.close()
            raise RemoteConnectionError(f"SSH 连接失败：{exc}") from exc
        finally:
            with self._lock:
                if self._connecting_client is client:
                    self._connecting_client = None

        with self._lock:
            old_client = self._client
            self._client = client
            self._request = ServerConnectionRequest(
                request.with_fingerprint(fingerprint).profile
            )
            self._fingerprint = fingerprint
        if old_client is not None:
            old_client.close()
        return snapshot

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            client = self._client
            request = self._request
            fingerprint = self._fingerprint
        if client is None or request is None:
            raise RemoteConnectionError("尚未连接服务器。")
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise RemoteConnectionError("SSH 连接已断开，请重新连接。")
        return self._read_snapshot(client, request.profile, fingerprint)

    def disconnect(self) -> str:
        with self._lock:
            client = self._client
            connecting_client = self._connecting_client
            request = self._request
            self._client = None
            self._connecting_client = None
            self._request = None
            self._fingerprint = ""
        if connecting_client is not None and connecting_client is not client:
            connecting_client.close()
        if client is not None:
            client.close()
        return request.profile.profile_name if request is not None else ""

    @staticmethod
    def _read_snapshot(
        client: paramiko.SSHClient,
        profile: ServerProfileDraft,
        fingerprint: str,
    ) -> dict[str, Any]:
        try:
            _stdin, stdout, stderr = client.exec_command(
                _powershell_encoded_command(profile),
                timeout=20,
            )
            exit_status = stdout.channel.recv_exit_status()
            output = decode_remote_stream(stdout.read())
            error_output = clean_remote_error_output(
                decode_remote_stream(stderr.read())
            )
        except (OSError, paramiko.SSHException) as exc:
            raise RemoteConnectionError(f"读取服务器资源失败：{exc}") from exc
        if exit_status != 0:
            detail = error_output or f"PowerShell 退出码 {exit_status}"
            raise RemoteConnectionError(f"读取服务器资源失败：{detail}")
        return parse_resource_snapshot(
            output,
            profile=profile,
            fingerprint=fingerprint,
        )


@dataclass(frozen=True)
class _RemoteOperation:
    action: str
    request: ServerConnectionRequest | None = None


class _RemoteConnectionWorker(QtCore.QObject):
    succeeded = Signal(str, object)
    confirmationRequired = Signal(object, str)
    failed = Signal(str)
    done = Signal()

    def __init__(
        self,
        manager: RemoteConnectionManager,
        operation: _RemoteOperation,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            if self.operation.action == "connect":
                if self.operation.request is None:
                    raise RemoteConnectionError("连接请求缺少服务器配置。")
                result = self.manager.connect(self.operation.request)
            elif self.operation.action == "refresh":
                result = self.manager.refresh()
            else:
                result = self.manager.disconnect()
        except HostKeyConfirmationRequired as exc:
            self.confirmationRequired.emit(self.operation.request, exc.fingerprint)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(self.operation.action, result)
        finally:
            self.done.emit()


class RemoteConnectionService(QtCore.QObject):
    """Execute serialized SSH operations without blocking the Qt event loop."""

    busyChanged = Signal(bool)
    confirmationRequired = Signal(object, str)
    connected = Signal(object)
    snapshotReceived = Signal(object)
    disconnected = Signal(str)
    failed = Signal(str)
    idle = Signal()

    def __init__(
        self,
        manager: RemoteConnectionManager | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager or RemoteConnectionManager()
        self._thread: QtCore.QThread | None = None
        self._worker: _RemoteConnectionWorker | None = None

    @property
    def is_busy(self) -> bool:
        return self._thread is not None

    def connect_to_server(self, request: ServerConnectionRequest) -> bool:
        return self._start(_RemoteOperation("connect", request))

    def refresh(self) -> bool:
        return self._start(_RemoteOperation("refresh"))

    def disconnect(self) -> bool:
        if self._thread is not None:
            self.failed.emit("服务器操作正在进行，请稍候。")
            return False
        return self._start(_RemoteOperation("disconnect"))

    def _start(self, operation: _RemoteOperation) -> bool:
        if self._thread is not None:
            self.failed.emit("服务器操作正在进行，请稍候。")
            return False
        thread = QtCore.QThread(self)
        worker = _RemoteConnectionWorker(self.manager, operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_succeeded)
        worker.confirmationRequired.connect(self.confirmationRequired)
        worker.failed.connect(self.failed)
        worker.done.connect(worker.deleteLater)
        worker.done.connect(thread.quit)
        thread.finished.connect(self._clear_worker)
        self._thread = thread
        self._worker = worker
        self.busyChanged.emit(True)
        thread.start()
        return True

    @Slot(str, object)
    def _on_succeeded(self, action: str, result: object) -> None:
        if action == "connect":
            self.connected.emit(result)
            self.snapshotReceived.emit(result)
        elif action == "refresh":
            self.snapshotReceived.emit(result)
        else:
            self.disconnected.emit(str(result or ""))

    @Slot()
    def _clear_worker(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        self.busyChanged.emit(False)
        self.idle.emit()
        if thread is not None:
            thread.deleteLater()

    def shutdown(self) -> None:
        thread = self._thread
        if thread is not None:
            self.manager.disconnect()
            thread.quit()
            if thread.isRunning():
                thread.wait(15000)
        self.manager.disconnect()


__all__ = [
    "HostKeyConfirmationRequired",
    "RemoteConnectionError",
    "RemoteConnectionManager",
    "RemoteConnectionService",
    "ServerConnectionRequest",
    "ServerCredentials",
    "clean_remote_error_output",
    "decode_remote_stream",
    "key_fingerprint",
    "normalize_fingerprint",
    "parse_resource_snapshot",
]
