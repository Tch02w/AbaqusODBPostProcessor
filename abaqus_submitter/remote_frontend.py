"""Frontend contracts for Windows SSH execution and ODB merging.

The immutable payloads in this module keep Qt forms independent from the SSH
adapter.  Credentials intentionally live in a separate, non-persistent request.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

from .qt_compat import QtCore, Signal


class ExecutionLocation(str, Enum):
    """Where a submitted Abaqus job is expected to execute."""

    LOCAL = "local"
    SERVER_UPLOAD = "server_upload"
    SERVER_EXISTING = "server_existing"


@dataclass(frozen=True)
class ServerProfileDraft:
    """Connection fields collected by the frontend."""

    profile_name: str
    host: str
    username: str
    authentication: str
    host_fingerprint: str
    abaqus_command: str
    compute_root: str
    allowed_roots: tuple[str, ...]
    port: int = 22
    private_key_path: str = ""

    def persistent_payload(self) -> dict:
        """Return the non-secret subset that may be written to settings."""
        payload = asdict(self)
        payload["allowed_roots"] = list(self.allowed_roots)
        return payload


@dataclass(frozen=True)
class OdbMergeDraft:
    """ODB merge choices collected by the frontend."""

    original_job: str
    current_job: str
    auto_merge: bool
    include_history: bool
    compress_result: bool
    server_side: bool
    retain_originals: bool
    result_name_source: str
    custom_result_name: str
    conflict_strategy: str = "auto_number"

    def result_stem(self) -> str:
        if self.result_name_source == "original":
            base_name = self.original_job
        elif self.result_name_source == "custom":
            base_name = self.custom_result_name
        else:
            base_name = self.current_job
        base_name = base_name.strip() or self.current_job.strip() or "joined"
        return f"{base_name}_joined"

    def preview(self) -> str:
        original = self.original_job.strip() or "原始作业"
        current = self.current_job.strip() or "当前作业"
        return (
            f"{original}_original.odb + {current}_original.odb"
            f" → {self.result_stem()}.odb"
        )


@dataclass(frozen=True)
class RemoteJobDraft:
    """Complete frontend request for a future remote execution Adapter."""

    job_name: str
    inp_path: str
    location: ExecutionLocation
    server: ServerProfileDraft
    remote_path: str
    cpus: int
    memory: str
    merge: OdbMergeDraft

    def as_payload(self) -> dict:
        payload = asdict(self)
        payload["location"] = self.location.value
        payload["merge"]["preview"] = self.merge.preview()
        payload["merge"]["result_stem"] = self.merge.result_stem()
        return payload


class RemoteFrontendBridge(QtCore.QObject):
    """Signals reserved for the future remote execution and merge Adapters."""

    testConnectionRequested = Signal(object)
    reconnectRequested = Signal(str)
    resourceRefreshRequested = Signal(str)
    resourceSnapshotReceived = Signal(object)
    browseRemoteDirectoryRequested = Signal(object)
    submitRemoteJobRequested = Signal(object)
    cancelRemoteJobRequested = Signal(str, bool)
    mergeOdbRequested = Signal(object)
    mergeValidationSnapshotReceived = Signal(object)
    transferEventReceived = Signal(object)
    mergeEventReceived = Signal(object)
    problemEventReceived = Signal(object)


__all__ = [
    "ExecutionLocation",
    "OdbMergeDraft",
    "RemoteFrontendBridge",
    "RemoteJobDraft",
    "ServerProfileDraft",
]
