"""Safe, asynchronous Abaqus ODB restart-merge execution."""

from __future__ import annotations

import locale
import os
import shlex
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .qt_compat import QtCore, Signal


class MergeConflictPolicy(str, Enum):
    """How an existing joined result is handled."""

    AUTO_NUMBER = "auto_number"
    OVERWRITE = "overwrite"


@dataclass(frozen=True)
class OdbMergeRequest:
    """User-selected inputs for one restartjoin operation."""

    original_odb: Path
    restart_odb: Path
    output_odb: Path
    abaqus_command: str = "abaqus"
    include_history: bool = True
    compress_result: bool = False
    copy_original: bool = False
    conflict_policy: MergeConflictPolicy = MergeConflictPolicy.AUTO_NUMBER


@dataclass(frozen=True)
class OdbMergePlan:
    """Resolved file paths and commands for one protected merge."""

    request: OdbMergeRequest
    staging_dir: Path
    original_backup: Path
    restart_backup: Path
    staged_original_backup: Path
    staged_restart_backup: Path
    working_odb: Path
    restartjoin_output_odb: Path
    validation_script: Path
    restartjoin_arguments: tuple[str, ...]
    validation_arguments: tuple[str, ...]

    def command_preview(self) -> str:
        return format_command((self.request.abaqus_command, *self.restartjoin_arguments))


@dataclass(frozen=True)
class OdbMergeResult:
    """Files published after restartjoin and read-only validation succeed."""

    output_odb: Path
    original_backup: Path
    restart_backup: Path
    command: str


class OdbMergeError(RuntimeError):
    """Raised when a protected ODB merge cannot be completed."""


class _OdbMergeCancelled(OdbMergeError):
    pass


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(os.fspath(_absolute(left))) == os.path.normcase(
            os.fspath(_absolute(right))
        )


def _source_stem(path: Path) -> str:
    stem = path.stem
    return stem[: -len("_original")] if stem.lower().endswith("_original") else stem


def normalize_joined_output(path: Path) -> Path:
    """Ensure the user-selected result follows the ``*_joined.odb`` convention."""

    output = Path(path)
    stem = output.stem if output.suffix else output.name
    if not stem.lower().endswith("_joined"):
        stem = f"{stem}_joined"
    return output.with_name(f"{stem}.odb")


def _numbered_path(path: Path, occupied: set[Path]) -> Path:
    candidate = path
    index = 2
    normalized_occupied = {
        Path(os.path.normcase(os.fspath(_absolute(item)))) for item in occupied
    }
    while (
        candidate.exists()
        or Path(os.path.normcase(os.fspath(_absolute(candidate)))) in normalized_occupied
    ):
        candidate = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
        index += 1
    return candidate


def _validate_request(request: OdbMergeRequest) -> OdbMergeRequest:
    original = _absolute(request.original_odb)
    restart = _absolute(request.restart_odb)
    output = _absolute(normalize_joined_output(request.output_odb))
    for label, path in (("原始 ODB", original), ("重启动 ODB", restart)):
        if path.suffix.lower() != ".odb":
            raise OdbMergeError(f"{label} 必须是 .odb 文件：{path}")
        if not path.is_file():
            raise OdbMergeError(f"{label} 不存在：{path}")
        if path.stat().st_size <= 0:
            raise OdbMergeError(f"{label} 是空文件：{path}")
    if _same_path(original, restart):
        raise OdbMergeError("原始 ODB 与重启动 ODB 不能是同一个文件。")
    if _same_path(output, original) or _same_path(output, restart):
        raise OdbMergeError("输出 ODB 不能覆盖任一源 ODB。")
    if not output.parent.is_dir():
        raise OdbMergeError(f"输出目录不存在：{output.parent}")
    command = request.abaqus_command.strip().strip('"')
    if not command:
        raise OdbMergeError("Abaqus 命令不能为空。")
    if any(character in command for character in "\r\n"):
        raise OdbMergeError("Abaqus 命令不能包含换行符。")
    return OdbMergeRequest(
        original_odb=original,
        restart_odb=restart,
        output_odb=output,
        abaqus_command=command,
        include_history=request.include_history,
        compress_result=request.compress_result,
        copy_original=request.copy_original,
        conflict_policy=request.conflict_policy,
    )


def build_merge_plan(request: OdbMergeRequest) -> OdbMergePlan:
    """Validate inputs and resolve non-destructive output paths."""

    request = _validate_request(request)
    output = request.output_odb
    if request.conflict_policy == MergeConflictPolicy.AUTO_NUMBER:
        output = _numbered_path(output, set())
        request = OdbMergeRequest(
            original_odb=request.original_odb,
            restart_odb=request.restart_odb,
            output_odb=output,
            abaqus_command=request.abaqus_command,
            include_history=request.include_history,
            compress_result=request.compress_result,
            copy_original=request.copy_original,
            conflict_policy=request.conflict_policy,
        )

    occupied = {request.original_odb, request.restart_odb, request.output_odb}
    original_backup = _numbered_path(
        request.output_odb.parent
        / f"{_source_stem(request.original_odb)}_original.odb",
        occupied,
    )
    occupied.add(original_backup)
    restart_backup = _numbered_path(
        request.output_odb.parent
        / f"{_source_stem(request.restart_odb)}_original.odb",
        occupied,
    )
    staging_dir = request.output_odb.parent / f".abasub-odb-merge-{uuid.uuid4().hex}"
    staged_original = staging_dir / original_backup.name
    staged_restart = staging_dir / restart_backup.name
    working = staging_dir / "abasub_join_working.odb"
    restartjoin_output = (
        staging_dir / f"Restart_{working.name}"
        if request.copy_original
        else working
    )
    validation_script = staging_dir / "validate_joined_odb.py"
    restartjoin_arguments = [
        "restartjoin",
        f"originalodb={working.name}",
        f"restartodb={staged_restart.name}",
    ]
    if request.include_history:
        restartjoin_arguments.append("history")
    if request.compress_result:
        restartjoin_arguments.append("compressresult")
    if request.copy_original:
        restartjoin_arguments.append("copyoriginal")
    validation_arguments = (
        "python",
        validation_script.name,
        restartjoin_output.name,
    )
    return OdbMergePlan(
        request=request,
        staging_dir=staging_dir,
        original_backup=original_backup,
        restart_backup=restart_backup,
        staged_original_backup=staged_original,
        staged_restart_backup=staged_restart,
        working_odb=working,
        restartjoin_output_odb=restartjoin_output,
        validation_script=validation_script,
        restartjoin_arguments=tuple(restartjoin_arguments),
        validation_arguments=validation_arguments,
    )


def format_command(arguments: tuple[str, ...]) -> str:
    """Format an argument vector for display without executing through a shell."""

    if os.name == "nt":
        return subprocess.list2cmdline(list(arguments))
    return shlex.join(arguments)


_VALIDATION_SCRIPT = """\
from __future__ import print_function
import sys
from odbAccess import openOdb

odb = openOdb(path=sys.argv[1], readOnly=True)
try:
    if len(odb.steps) == 0:
        raise RuntimeError("ODB contains no steps")
    if not any(len(step.frames) > 0 for step in odb.steps.values()):
        raise RuntimeError("ODB contains no frames")
    print("ABASUB_ODB_VALIDATION_OK")
finally:
    odb.close()
"""


class _OdbMergeWorker(QtCore.QObject):
    phaseChanged = Signal(str)
    progressChanged = Signal(int)
    outputReceived = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, request: OdbMergeRequest) -> None:
        super().__init__()
        self.request = request
        self._cancel_event = threading.Event()
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._process_lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise _OdbMergeCancelled("ODB 合并已取消。")

    def _copy_file(self, source: Path, destination: Path) -> None:
        self._check_cancelled()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as source_file, destination.open("xb") as target_file:
            while True:
                block = source_file.read(8 * 1024 * 1024)
                if not block:
                    break
                target_file.write(block)
                self._check_cancelled()
        shutil.copystat(source, destination)

    def _run_process(self, plan: OdbMergePlan, arguments: tuple[str, ...]) -> str:
        self._check_cancelled()
        process_arguments = [plan.request.abaqus_command, *arguments]
        encoding = locale.getpreferredencoding(False) or "utf-8"
        try:
            process = subprocess.Popen(
                process_arguments,
                cwd=plan.staging_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding=encoding,
                errors="replace",
                shell=False,
            )
        except OSError as exc:
            raise OdbMergeError(
                f"无法启动 Abaqus 命令“{plan.request.abaqus_command}”：{exc}"
            ) from exc
        with self._process_lock:
            self._process = process
        captured: list[str] = []
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    text = line.rstrip("\r\n")
                    if text:
                        captured.append(text)
                        self.outputReceived.emit(text)
                    self._check_cancelled()
            return_code = process.wait()
        finally:
            with self._process_lock:
                self._process = None
        self._check_cancelled()
        if return_code != 0:
            detail = captured[-1] if captured else f"退出代码 {return_code}"
            raise OdbMergeError(f"Abaqus 命令执行失败：{detail}")
        return "\n".join(captured)

    @staticmethod
    def _publish(source: Path, destination: Path, *, overwrite: bool) -> None:
        if overwrite:
            os.replace(source, destination)
            return
        try:
            os.link(source, destination)
        except FileExistsError as exc:
            raise OdbMergeError(f"发布结果时目标文件已存在：{destination}") from exc
        except OSError as exc:
            raise OdbMergeError(f"无法发布文件到 {destination}：{exc}") from exc
        source.unlink()

    @QtCore.Slot()
    def run(self) -> None:
        plan: OdbMergePlan | None = None
        try:
            plan = build_merge_plan(self.request)
            plan.staging_dir.mkdir()
            self.phaseChanged.emit("正在复制两个源 ODB 的安全副本…")
            self.progressChanged.emit(5)
            self._copy_file(
                plan.request.original_odb,
                plan.staged_original_backup,
            )
            self.progressChanged.emit(25)
            self._copy_file(
                plan.request.restart_odb,
                plan.staged_restart_backup,
            )
            self.progressChanged.emit(45)
            self.phaseChanged.emit("正在创建合并工作副本…")
            self._copy_file(plan.staged_original_backup, plan.working_odb)
            self.progressChanged.emit(60)

            plan.validation_script.write_text(_VALIDATION_SCRIPT, encoding="utf-8")
            self.phaseChanged.emit("正在执行 Abaqus restartjoin…")
            self.outputReceived.emit(f"命令：{plan.command_preview()}")
            self._run_process(plan, plan.restartjoin_arguments)
            if (
                not plan.restartjoin_output_odb.is_file()
                or plan.restartjoin_output_odb.stat().st_size <= 0
            ):
                raise OdbMergeError("restartjoin 未生成有效的非空 ODB。")
            self.progressChanged.emit(80)

            self.phaseChanged.emit("正在使用 Abaqus 只读打开并验证结果…")
            validation_output = self._run_process(plan, plan.validation_arguments)
            if "ABASUB_ODB_VALIDATION_OK" not in validation_output:
                raise OdbMergeError("Abaqus 未返回 ODB 只读验证成功标记。")
            self.progressChanged.emit(90)

            self.phaseChanged.emit("正在发布安全副本和合并结果…")
            self._publish(
                plan.staged_original_backup,
                plan.original_backup,
                overwrite=False,
            )
            self._publish(
                plan.staged_restart_backup,
                plan.restart_backup,
                overwrite=False,
            )
            self._publish(
                plan.restartjoin_output_odb,
                plan.request.output_odb,
                overwrite=(
                    plan.request.conflict_policy == MergeConflictPolicy.OVERWRITE
                ),
            )
            self.progressChanged.emit(100)
            self.succeeded.emit(
                OdbMergeResult(
                    output_odb=plan.request.output_odb,
                    original_backup=plan.original_backup,
                    restart_backup=plan.restart_backup,
                    command=plan.command_preview(),
                )
            )
        except _OdbMergeCancelled:
            self.cancelled.emit()
        except (OSError, OdbMergeError) as exc:
            self.failed.emit(str(exc))
        finally:
            if plan is not None:
                shutil.rmtree(plan.staging_dir, ignore_errors=True)


class OdbMergeService(QtCore.QObject):
    """Run one protected merge at a time without blocking the Qt UI thread."""

    busyChanged = Signal(bool)
    phaseChanged = Signal(str)
    progressChanged = Signal(int)
    outputReceived = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thread: QtCore.QThread | None = None
        self._worker: _OdbMergeWorker | None = None

    @property
    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self, request: OdbMergeRequest) -> bool:
        if self.is_busy:
            self.failed.emit("已有 ODB 合并正在执行。")
            return False
        try:
            build_merge_plan(request)
        except OdbMergeError as exc:
            self.failed.emit(str(exc))
            return False

        thread = QtCore.QThread(self)
        worker = _OdbMergeWorker(request)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.phaseChanged.connect(self.phaseChanged)
        worker.progressChanged.connect(self.progressChanged)
        worker.outputReceived.connect(self.outputReceived)
        worker.succeeded.connect(self.succeeded)
        worker.failed.connect(self.failed)
        worker.cancelled.connect(self.cancelled)
        for signal in (worker.succeeded, worker.failed, worker.cancelled):
            signal.connect(worker.deleteLater)
            signal.connect(thread.quit)
        thread.finished.connect(self._clear_worker)
        self._thread = thread
        self._worker = worker
        self.busyChanged.emit(True)
        thread.start()
        return True

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    @QtCore.Slot()
    def _clear_worker(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        self.busyChanged.emit(False)
        if thread is not None:
            thread.deleteLater()

    def shutdown(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self.cancel()
        thread.quit()
        if thread.isRunning():
            thread.wait(15000)


__all__ = [
    "MergeConflictPolicy",
    "OdbMergeError",
    "OdbMergePlan",
    "OdbMergeRequest",
    "OdbMergeResult",
    "OdbMergeService",
    "build_merge_plan",
    "format_command",
    "normalize_joined_output",
]
