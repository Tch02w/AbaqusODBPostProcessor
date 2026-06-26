"""Coordinate internally submitted Abaqus jobs without owning UI widgets."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from .abaqus_diagnostics import build_diagnostic_file_baseline, classify_job_text
from .archive import (
    ArchiveCoordinator,
    ArchiveMoveTask,
    apply_workspace_prepare_result,
    backup_existing_result_files,
    build_archive_move_plan,
    build_workspace_info,
    build_workspace_prepare_plan,
    delete_existing_result_files,
    get_existing_lck_file,
    get_existing_odb_file,
    is_ssd_independent_work_dir,
    prepare_calculation_workspace,
)
from .workspace_prepare import WorkspacePrepareTask
from .command import (
    SubmitOptions,
    build_abaqus_command,
    derive_oldjob_name,
    queue_item_to_options,
    validate_options,
)
from .constants import (
    STATUS_CANCELED,
    STATUS_COMPLETED,
    STATUS_DATACHECK_COMPLETED,
    STATUS_DATACHECK_FAILED,
    STATUS_FAILED,
    STATUS_PENDING_RUN,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_TERMINATED,
    STATUS_TERMINATING,
    STATUS_UNKNOWN,
    STATUS_WAITING_DEPENDENCY,
)
from .models import QueueItem
from .diagnostics import hang_probe_function
from .qt_compat import QtCore, QtWidgets
from .queue_scheduler import (
    active_submit_conflict_message as scheduler_active_submit_conflict_message,
    effective_queue_item_work_dir as scheduler_effective_queue_item_work_dir,
    find_formal_queue_conflict as scheduler_find_formal_queue_conflict,
    managed_active_statuses as scheduler_managed_active_statuses,
    managed_job_key as scheduler_managed_job_key,
    oldjob_name_from_item as scheduler_oldjob_name_from_item,
    queue_item_conflict_key as scheduler_queue_item_conflict_key,
)
from .ui_components import runtime_job_display_label as ui_runtime_job_display_label


@dataclass(frozen=True)
class FinalizationInput:
    is_datacheck: bool
    terminating: bool
    launcher_exit_code: int | None
    console_failed: bool
    console_failed_detail: str
    diagnostic_status: str
    diagnostic_detail: str
    solver_start_timeout: bool = False
    solver_start_timeout_detail: str = ""
    runtime_completion_confirmed: bool = False
    runtime_completion_reason: str = ""


@dataclass(frozen=True)
class FinalizationDecision:
    status: str
    message: str


@dataclass(frozen=True)
class DispatchSlotInfo:
    manual_limit: int
    managed_active_count: int
    manual_available_slots: int
    memory_available_slots: int
    effective_available_slots: int
    slot_estimate: object | None = None

    @classmethod
    def from_mapping(cls, slot_info: dict) -> "DispatchSlotInfo":
        return cls(
            manual_limit=int(slot_info.get("manual_limit") or 0),
            managed_active_count=int(slot_info.get("managed_active_count") or 0),
            manual_available_slots=int(slot_info.get("manual_available_slots") or 0),
            memory_available_slots=int(slot_info.get("memory_available_slots") or 0),
            effective_available_slots=int(slot_info["effective_available_slots"]),
            slot_estimate=slot_info.get("slot_estimate"),
        )

    def dispatch_slots(self, queue_items: list[QueueItem]) -> int:
        if (
            self.effective_available_slots <= 0
            and any(item.status == STATUS_PENDING_RUN for item in queue_items)
            and self.managed_active_count == 0
            and self.manual_available_slots > 0
            and self.memory_available_slots == 0
        ):
            return 1
        return self.effective_available_slots


def run_is_datacheck(
    run_datacheck_only: bool,
    queue_item_datacheck_only: bool = False,
) -> bool:
    return bool(run_datacheck_only or queue_item_datacheck_only)


def resolve_final_status_from_console(
    *,
    console_output: str,
    console_failed: bool,
    console_failed_detail: str,
    status: str,
    detail: str,
) -> tuple[str, str]:
    """Use cached launcher output when diagnostic files do not provide a final answer."""
    console_status = ""
    console_detail = ""

    if console_output:
        console_status, console_detail = classify_job_text(console_output)

    if console_failed and status not in {"完成", "终止"}:
        return "失败", detail or console_failed_detail or console_detail

    if not status and console_status:
        return console_status, console_detail

    return status, detail


def resolve_datacheck_final_status(finalization: FinalizationInput) -> FinalizationDecision:
    """Map Datacheck exits to explicit Datacheck final states."""
    status = finalization.diagnostic_status
    detail = finalization.diagnostic_detail
    exit_code = finalization.launcher_exit_code

    if status == "终止":
        return FinalizationDecision(STATUS_TERMINATED, detail or "检测到终止信息")

    if finalization.console_failed or status == "失败":
        return FinalizationDecision(
            STATUS_DATACHECK_FAILED,
            detail or finalization.console_failed_detail or "Datacheck 检测到错误信息",
        )

    if status == "完成" or exit_code == 0:
        return FinalizationDecision(
            STATUS_DATACHECK_COMPLETED,
            detail or "Datacheck 进程结束，未检测到错误信息",
        )

    return FinalizationDecision(
        STATUS_DATACHECK_FAILED,
        detail or f"Datacheck 进程异常结束，返回码 {exit_code}",
    )


def resolve_finalization_status(finalization: FinalizationInput) -> FinalizationDecision:
    if finalization.terminating:
        return FinalizationDecision(STATUS_TERMINATED, "用户手动终止")

    if finalization.is_datacheck:
        return resolve_datacheck_final_status(finalization)

    status = finalization.diagnostic_status
    detail = finalization.diagnostic_detail

    if finalization.solver_start_timeout:
        if finalization.console_failed:
            return FinalizationDecision(
                STATUS_FAILED,
                detail or finalization.console_failed_detail or finalization.solver_start_timeout_detail,
            )
        if finalization.launcher_exit_code not in (None, 0):
            return FinalizationDecision(STATUS_FAILED, detail or f"exit_code={finalization.launcher_exit_code}")
        if status:
            return FinalizationDecision(STATUS_FAILED, detail or status)
        return FinalizationDecision(
            STATUS_UNKNOWN,
            finalization.solver_start_timeout_detail or "后台求解器未启动",
        )

    if status == "终止":
        return FinalizationDecision(STATUS_TERMINATED, detail or "检测到终止信息")
    if status and status != "完成":
        return FinalizationDecision(STATUS_FAILED, detail or status)
    if finalization.runtime_completion_confirmed:
        return FinalizationDecision(
            STATUS_COMPLETED,
            detail or finalization.runtime_completion_reason or "运行监控确认计算完成",
        )
    if finalization.launcher_exit_code not in (None, 0):
        return FinalizationDecision(STATUS_FAILED, detail or f"exit_code={finalization.launcher_exit_code}")
    return FinalizationDecision(STATUS_UNKNOWN, detail or "运行监控尚未确认普通作业完成")


class JobController:
    """Coordinate job business flow while delegating UI work to MainWindow."""

    def __init__(self, window) -> None:  # noqa: ANN001
        object.__setattr__(self, "_window", window)

    @property
    def window(self):  # noqa: ANN201
        return object.__getattribute__(self, "_window")

    def __getattr__(self, name: str):  # noqa: ANN204
        return getattr(self.window, name)

    def __setattr__(self, name: str, value) -> None:  # noqa: ANN001
        if name == "_window":
            object.__setattr__(self, name, value)
            return
        setattr(self.window, name, value)

    def _queue_items(self) -> list[QueueItem]:
        return self.window.queue_items

    def _active_runs(self) -> dict[str, dict]:
        return self.window.active_runs

    def _is_queue_active(self) -> bool:
        return bool(self.window.queue_active)

    def _is_queue_stop_requested(self) -> bool:
        return bool(self.window.queue_stop_requested)

    def _set_queue_active(self, value: bool) -> None:
        self.window.queue_active = value

    def _archive_reserved_keys(self) -> set[tuple[str, str]]:
        return self.window._archive_move_reserved_keys

    def _append_history(self, message: str) -> None:
        self.window.append_history(message)

    def _update_queue_status_label(self) -> None:
        self.window.update_queue_status_label()

    def _refresh_queue_views(self) -> None:
        self.window.refresh_visible_queue_manager()
        self.window.update_queue_status_label()

    def _default_cpus(self) -> int:
        return int(self.window.cpus_spin.value())

    def _estimate_effective_available_slots(self) -> dict:
        return self.window.estimate_effective_available_slots()

    def _dispatch_slot_info(self) -> DispatchSlotInfo:
        return DispatchSlotInfo.from_mapping(self._estimate_effective_available_slots())

    def _dispatch_available_slots(self, slot_info: DispatchSlotInfo, queue_items: list[QueueItem]) -> int:
        return slot_info.dispatch_slots(queue_items)

    def _refresh_queue_dependencies(self) -> None:
        self.window.refresh_queue_dependencies()

    def _process_deferred_archives(self) -> None:
        self.window.process_deferred_archives()

    def _request_dispatch_queue(self) -> None:
        self.window.request_dispatch_queue()

    def _max_parallel_jobs(self) -> int:
        return int(self.window.max_parallel_spin.value())

    def _queue_item_brief(self, item: QueueItem) -> str:
        work_dir = scheduler_effective_queue_item_work_dir(item)
        return f"{item.job_name}|{item.status}|{work_dir}"

    def _queue_items_brief(self, items: list[QueueItem], limit: int = 8) -> str:
        values = [self._queue_item_brief(item) for item in items[:limit]]
        if len(items) > limit:
            values.append(f"...+{len(items) - limit}")
        return "[" + "; ".join(values) + "]"

    def _active_runs_brief(self, active_runs: dict[str, dict], limit: int = 8) -> str:
        values = []
        for key, run in list(active_runs.items())[:limit]:
            values.append(f"{key}|{run.get('job_name', '')}")
        if len(active_runs) > limit:
            values.append(f"...+{len(active_runs) - limit}")
        return "[" + "; ".join(values) + "]"

    def _ask_existing_odb_action(self, job_name: str, odb_path: Path, queue_mode: bool) -> str:
        return self.window.ask_existing_odb_action(job_name, odb_path, queue_mode)

    def _show_existing_lck_warning(self, existing_lck: Path) -> None:
        QtWidgets.QMessageBox.warning(
            self.window,
            "作业可能仍在运行",
            f"检测到同名 LCK 文件，暂不提交：\n\n{existing_lck}\n\n"
            "请确认旧作业已经结束，或手动清理残留 LCK 文件后再提交。",
        )

    def _show_existing_result_error(self, exc: OSError) -> None:
        QtWidgets.QMessageBox.critical(
            self.window,
            "已有结果处理失败",
            f"无法处理旧结果文件：\n\n{exc}\n\n请确认 ODB/STA 没有被 Abaqus 或其他程序占用。",
        )

    def _set_queue_stop_requested(self, value: bool) -> None:
        self.window.queue_stop_requested = value

    @hang_probe_function("JobController.handle_existing_job_results")
    def handle_existing_job_results(
        self,
        options: SubmitOptions,
        work_dir: str,
        queue_item: QueueItem | None,
        *,
        queue_mode: bool = False,
    ) -> tuple[bool, SubmitOptions, dict]:
        """Apply overwrite/backup handling for existing Abaqus result files."""
        job_name = options.job_name
        existing_lck = get_existing_lck_file(work_dir, job_name)
        if existing_lck is not None:
            self._show_existing_lck_warning(existing_lck)
            if queue_item is not None:
                queue_item.status = STATUS_FAILED
                queue_item.message = "检测到同名 LCK 文件"
            return False, options, {"action": "lck", "odb": "", "sta": ""}

        existing_odb = get_existing_odb_file(work_dir, job_name)
        if existing_odb is None:
            return True, options, {"action": "", "odb": "", "sta": ""}

        action = self._ask_existing_odb_action(
            job_name,
            existing_odb,
            queue_mode,
        )
        if action == "cancel":
            self._append_history(f"取消提交：{job_name} 检测到同名 ODB。")
            if queue_item is not None:
                queue_item.status = STATUS_CANCELED
                queue_item.message = "用户取消同名 ODB 处理"

            if queue_mode:
                self._set_queue_stop_requested(True)
                self._set_queue_active(False)

        try:
            if action == "backup":
                result = backup_existing_result_files(work_dir, job_name)
                if result.get("odb"):
                    self._append_history(f"已有结果处理：同名 ODB 已备份为：{result['odb']}")
                if result.get("sta"):
                    self._append_history(f"旧 STA 已备份为：{result['sta']}")
                return True, replace(options, ask_delete_off=True), {"action": action, **result}

            if action == "overwrite":
                result = delete_existing_result_files(work_dir, job_name)
                if result.get("odb"):
                    self._append_history(f"已有结果处理：同名 ODB 已删除：{result['odb']}")
                if result.get("sta"):
                    self._append_history(f"旧 STA 已删除：{result['sta']}")
                return True, replace(options, ask_delete_off=True), {"action": action, **result}
        except OSError as exc:
            self._show_existing_result_error(exc)
            if queue_item is not None:
                queue_item.status = STATUS_FAILED
                queue_item.message = f"旧结果处理失败：{exc}"
            return False, options, {"action": action, "odb": str(existing_odb), "sta": ""}

        return False, options, {"action": action, "odb": str(existing_odb), "sta": ""}
    def terminate_queue_items_by_ids(
        self,
        item_ids: list[str],
    ) -> None:
        """终止队列管理器中选中的运行中作业。"""
        item_id_set = set(item_ids)

        active_statuses = scheduler_managed_active_statuses()

        changed = False

        for item in self.queue_items:
            if item.item_id not in item_id_set:
                continue

            if item.status not in active_statuses:
                continue

            work_dir = scheduler_effective_queue_item_work_dir(item)

            if not work_dir:
                item.message = "终止失败：未找到工作目录"

                continue

            command = f"abaqus terminate job={item.job_name}"

            started = QtCore.QProcess.startDetached(
                ("cmd.exe" if os.name == "nt" else "/bin/sh"),
                (["/c", command] if os.name == "nt" else ["-lc", command]),
                work_dir,
            )

            if not started:
                item.message = "终止失败：无法启动终止命令"

                self.append_history(f"终止队列作业失败：{command}")

                continue

            item.status = STATUS_TERMINATING

            item.message = "正在终止"

            if item.active_job_key:
                run = self.active_runs.get(item.active_job_key)

                if run is not None:
                    run["terminating"] = True

                    run["terminating_at"] = time.monotonic()

            self.append_history(f"终止队列作业：{command}")

            changed = True

        if changed:
            self.refresh_visible_queue_manager()
            self.update_queue_status_label()

    def stop_queue(
        self,
    ) -> None:
        """终止整个队列，并将已运行 Job 标记为正在终止。"""
        if not self.queue_active and not self.active_runs:
            return

        self.queue_stop_requested = True
        self.queue_active = False

        for item in self.queue_items:
            if item.status == STATUS_PENDING_RUN:
                item.status = STATUS_CANCELED
                item.message = "用户终止队列"

        for job_key, run in list(self.active_runs.items()):
            queue_item = run.get("queue_item")

            if queue_item is None:
                continue

            run["terminating"] = True
            run["terminating_at"] = time.monotonic()

            queue_item.status = STATUS_TERMINATING

            queue_item.message = "正在终止"

            command = f"abaqus terminate job={run['job_name']}"

            self.append_history(f"终止队列作业：{command}")

            QtCore.QProcess.startDetached(
                ("cmd.exe" if os.name == "nt" else "/bin/sh"),
                (["/c", command] if os.name == "nt" else ["-lc", command]),
                run["work_dir"],
            )

        self.refresh_visible_queue_manager()
        self.update_queue_status_label()
        self.process_deferred_archives()

    def dispatch_queue_now(self) -> None:
        self._refresh_queue_dependencies()
        queue_items = self._queue_items()
        archive_reserved_keys = self._archive_reserved_keys()
        if not self._is_queue_active() or self._is_queue_stop_requested():
            self._update_queue_status_label()
            self._process_deferred_archives()
            return

        slot_info = self._dispatch_slot_info()
        available_slots = self._dispatch_available_slots(slot_info, queue_items)
        while available_slots > 0:
            for entry in queue_items:
                if (
                    entry.status == STATUS_PENDING_RUN
                    and scheduler_queue_item_conflict_key(entry) in archive_reserved_keys
                ):
                    entry.message = "同名 SSD 计算目录正在等待归档或归档中，请稍后再提交。"
            item = next(
                (
                    entry
                    for entry in queue_items
                    if entry.status == STATUS_PENDING_RUN
                    and scheduler_queue_item_conflict_key(entry) not in archive_reserved_keys
                ),
                None,
            )
            if item is None:
                break

            conflict_item = scheduler_find_formal_queue_conflict(
                item,
                queue_items,
            )
            if conflict_item is not None:
                item.status = STATUS_FAILED
                item.message = (
                    f"作业冲突：同一计算目录中已存在同名作业 {item.job_name}，"
                    f"冲突来源：{conflict_item.inp_path}"
                )
                self._append_history(f"队列作业冲突，已跳过：{item.job_name} | {item.message}")
                continue

            if int(item.cores or 0) <= 0:
                item.message = "请先设置 Core 后再提交。"
                self._set_queue_active(False)
                self._refresh_queue_views()
                QtWidgets.QMessageBox.warning(
                    self.window,
                    "队列作业 Core 未设置",
                    f"作业 {item.job_name} 尚未设置 Core。\n\n"
                    "请在作业队列管理窗口中编辑该作业的 Core 后，再开始队列。",
                )
                self._process_deferred_archives()
                return

            options = queue_item_to_options(
                item,
                default_cpus=self._default_cpus(),
            )
            ok, message = validate_options(options)
            if not ok:
                item.status = STATUS_FAILED
                item.message = message
                self._append_history(f"队列作业校验失败：{item.job_name} | {message}")
                continue

            started = self.start_job(
                options,
                queue_item=item,
                queue_mode=True,
            )
            if started:
                available_slots -= 1
            else:
                if item.status == STATUS_PENDING_RUN:
                    item.status = STATUS_FAILED
                    item.message = "启动失败"
            self._refresh_queue_dependencies()
            slot_info = self._dispatch_slot_info()
            available_slots = min(available_slots, slot_info.effective_available_slots)
            if started:
                break

        pending = any(
            item.status in {STATUS_PENDING_RUN, STATUS_WAITING_DEPENDENCY}
            for item in queue_items
        )
        running = any(run.get("queue_item") is not None for run in self._active_runs().values()) or any(
            item.status == STATUS_STARTING for item in queue_items
        )
        if not pending and not running:
            self._set_queue_active(False)
            self._append_history("队列已结束。")
        self._update_queue_status_label()
        self._process_deferred_archives()

    def block_missing_restart_dependency(
        self,
        options: SubmitOptions,
        queue_item: QueueItem | None,
        *,
        queue_mode: bool,
        message: str,
    ) -> None:
        if queue_item is not None:
            queue_item.status = STATUS_WAITING_DEPENDENCY
            queue_item.message = message
            queue_item.active_job_key = ""
        if not queue_mode:
            QtWidgets.QMessageBox.warning(self.window, "Restart 依赖未选择", message)
        self.append_history(f"阻止 Restart 作业提交：{options.job_name} | {message}")
        self.refresh_visible_queue_manager()
        self.update_queue_status_label()

    def validate_restart_dependency_before_start(
        self,
        options: SubmitOptions,
        queue_item: QueueItem | None,
        *,
        queue_mode: bool,
    ) -> str:
        if not self.submit_requires_restart_dependency(options, queue_item):
            return ""

        oldjob_name = derive_oldjob_name(options.oldjob_path)
        if not oldjob_name and queue_item is not None:
            oldjob_name = scheduler_oldjob_name_from_item(queue_item)
        if not oldjob_name:
            message = "未选择有效的 Restart 前置作业"
            self.block_missing_restart_dependency(
                options,
                queue_item,
                queue_mode=queue_mode,
                message=message,
            )
            return ""

        oldjob_source_dir = self.resolve_oldjob_source_dir(options, queue_item)
        if not oldjob_source_dir:
            message = f"未找到 Restart 前置作业 ODB：{oldjob_name}"
            self.block_missing_restart_dependency(
                options,
                queue_item,
                queue_mode=queue_mode,
                message=message,
            )
            return ""

        return oldjob_source_dir

    @hang_probe_function("JobController.start_job")
    def start_job(
        self,
        options: SubmitOptions,
        queue_item: QueueItem | None = None,
        *,
        queue_mode: bool = False,
    ) -> bool:

        archive_conflict_message = self.archive_move_conflict_message(options, queue_item)
        if archive_conflict_message:
            if queue_item is not None:
                queue_item.message = archive_conflict_message.replace("\n", " ")
            if not queue_mode:
                QtWidgets.QMessageBox.warning(self.window, "提交作业", archive_conflict_message)
            self.append_history(archive_conflict_message)
            self.update_queue_status_label()
            return False

        conflict_message = scheduler_active_submit_conflict_message(
            inp_file=options.inp_file,
            job_name=options.job_name,
            queue_item=queue_item,
            active_runs=self.active_runs,
            queue_items=self.queue_items,
        )
        if conflict_message:
            if queue_item is not None:
                queue_item.status = STATUS_FAILED
                queue_item.message = conflict_message.replace("\n", " ")
            if not queue_mode:
                QtWidgets.QMessageBox.warning(self.window, "提交作业", conflict_message)
            self.append_history(conflict_message)
            self.update_queue_status_label()
            return False

        try:
            oldjob_source_dir = self.validate_restart_dependency_before_start(
                options,
                queue_item,
                queue_mode=queue_mode,
            )
            if self.submit_requires_restart_dependency(options, queue_item) and not oldjob_source_dir:
                return False
            plan = build_workspace_prepare_plan(
                options,
                queue_item,
                oldjob_source_dir,
            )
            if plan.enabled:
                workspace_info = build_workspace_info(options, queue_item)
                return self.enqueue_workspace_prepare(
                    options=options,
                    queue_item=queue_item,
                    queue_mode=queue_mode,
                    plan=plan,
                    workspace_info=workspace_info,
                )
            options, workspace_info = prepare_calculation_workspace(options, queue_item, oldjob_source_dir)
        except OSError as exc:
            self.append_history(f"准备计算工作目录失败：{options.job_name}\n{exc}")
            if queue_item is not None:
                queue_item.status = STATUS_FAILED
                queue_item.message = f"准备工作目录失败：{exc}"
            return False

        return self.continue_start_job_after_workspace_ready(
            options,
            queue_item,
            queue_mode=queue_mode,
            workspace_info=workspace_info,
        )

    def enqueue_workspace_prepare(
        self,
        *,
        options: SubmitOptions,
        queue_item: QueueItem | None,
        queue_mode: bool,
        plan,
        workspace_info: dict,
    ) -> bool:
        if queue_item is None:
            return False

        if queue_item.status == STATUS_STARTING:
            return True

        task_id = uuid4().hex
        task = WorkspacePrepareTask(
            task_id=task_id,
            item_id=queue_item.item_id,
            plan=plan,
        )
        self._workspace_prepare_contexts[task_id] = {
            "options": options,
            "queue_item": queue_item,
            "queue_mode": queue_mode,
            "workspace_info": workspace_info,
        }

        queue_item.status = STATUS_STARTING
        queue_item.message = "准备计算工作目录"
        self.refresh_visible_queue_manager()
        self.update_queue_status_label()

        if self.workspace_prepare_service.enqueue(task):
            return True

        self._workspace_prepare_contexts.pop(task_id, None)
        queue_item.status = STATUS_FAILED
        queue_item.message = "准备工作目录失败：窗口正在关闭"
        self.refresh_visible_queue_manager()
        self.update_queue_status_label()
        return False

    def on_workspace_prepare_succeeded(self, task: WorkspacePrepareTask, result) -> None:
        context = self._workspace_prepare_contexts.pop(task.task_id, None)
        if self._closing or context is None:
            return

        queue_item = context["queue_item"]
        if not self.workspace_prepare_task_is_current(task, queue_item):
            self.request_dispatch_queue()
            return

        options, workspace_info = apply_workspace_prepare_result(
            context["options"],
            queue_item,
            context["workspace_info"],
            result,
        )
        started = self.continue_start_job_after_workspace_ready(
            options,
            queue_item,
            queue_mode=bool(context["queue_mode"]),
            workspace_info=workspace_info,
        )
        if not started and queue_item.status == STATUS_STARTING:
            queue_item.status = STATUS_FAILED
            queue_item.message = "启动失败"
            self.refresh_visible_queue_manager()
            self.update_queue_status_label()
            self.request_dispatch_queue()

    def on_workspace_prepare_failed(self, task: WorkspacePrepareTask, message: str, copied_inp_path: str = "") -> None:
        context = self._workspace_prepare_contexts.pop(task.task_id, None)
        if self._closing or context is None:
            return

        queue_item = context["queue_item"]
        if not self.workspace_prepare_task_is_current(task, queue_item):
            self.request_dispatch_queue()
            return

        options = context["options"]
        if copied_inp_path:
            queue_item.source_inp_path = context["workspace_info"].get("source_inp_path", "")
        queue_item.status = STATUS_FAILED
        queue_item.message = f"准备工作目录失败：{message}"
        self.append_history(f"准备计算工作目录失败：{options.job_name}\n{message}")
        if not context["queue_mode"]:
            QtWidgets.QMessageBox.warning(self.window,
                "提交作业",
                f"准备计算工作目录失败：\n{message}",
            )
        self.refresh_visible_queue_manager()
        self.update_queue_status_label()
        self.request_dispatch_queue()

    def workspace_prepare_task_is_current(self, task: WorkspacePrepareTask, queue_item: QueueItem | None) -> bool:
        if queue_item is None:
            return False
        if queue_item.item_id != task.item_id:
            return False
        if queue_item.status != STATUS_STARTING:
            return False
        return any(item.item_id == task.item_id and item is queue_item for item in self.queue_items)

    def log_workspace_prepare_result(
        self,
        options: SubmitOptions,
        workspace_info: dict,
    ) -> None:
        if workspace_info.get("copied_inp_path"):
            self.append_history(f"已复制 INP 到固态工作目录：{options.job_name}\n{workspace_info['copied_inp_path']}")
        if workspace_info.get("copied_oldjob_files"):
            self.append_history(
                f"已复制重启动依赖文件到固态工作目录：{options.job_name}\n"
                + "\n".join(workspace_info["copied_oldjob_files"])
            )

    @hang_probe_function("JobController.continue_start_job_after_workspace_ready")
    def continue_start_job_after_workspace_ready(
        self,
        options: SubmitOptions,
        queue_item: QueueItem | None,
        *,
        queue_mode: bool,
        workspace_info: dict,
    ) -> bool:
        self.log_workspace_prepare_result(options, workspace_info)

        managed_key = scheduler_managed_job_key(
            str(Path(options.inp_file).parent),
            options.job_name,
        )
        job_key = f"{managed_key[0]}::{managed_key[1]}"
        if job_key in self.active_runs:
            self.append_history(f"作业正在运行，跳过重复提交：{options.job_name}")
            return False

        inp_path = Path(options.inp_file)
        work_dir = str(inp_path.parent)
        handled, options, existing_result_info = self.handle_existing_job_results(
            options,
            work_dir,
            queue_item,
            queue_mode=queue_mode,
        )

        if not handled:
            self.update_queue_status_label()
            return False

        command = build_abaqus_command(options)
        calculation_root_dir = ""
        if queue_item is not None:
            calculation_root_dir = queue_item.calculation_root_dir or ""
        diagnostic_baseline = build_diagnostic_file_baseline(
            work_dir,
            options.job_name,
        )
        submitted_at = time.time()
        runtime_started_monotonic = time.monotonic()

        run = {
            "key": job_key,
            "process": None,
            "timer": None,
            "work_dir": work_dir,
            "job_name": options.job_name,
            "command": command,
            "is_paused": False,
            "datacheck_only": options.datacheck,
            "terminating": False,
            "terminating_at": 0.0,
            "sta_position": 0,
            "sta_state": {},
            "log": "",
            "queue_item": queue_item,
            "source_inp_path": workspace_info.get("source_inp_path", ""),
            "calculation_root_dir": calculation_root_dir,
            "archive_dir": workspace_info.get("archive_dir", ""),
            "archive_destination": "",
            "archive_status": "",
            "archive_error": "",
            "cleanup_after_archive": workspace_info.get("cleanup_after_archive", False),
            "existing_result_action": existing_result_info.get("action", ""),
            "backup_odb_path": existing_result_info.get("odb", "")
            if existing_result_info.get("action") == "backup"
            else "",
            "backup_sta_path": existing_result_info.get("sta", "")
            if existing_result_info.get("action") == "backup"
            else "",
            "memory_monitor_activated": False,
            "memory_stable_logged": False,
            "memory_current": 0,
            "memory_peak": 0,
            "memory_estimated": 0,
            "memory_monitor_mode": "learning",
            "memory_monitor_stable": False,
            "launcher_finished": False,
            "launcher_exit_code": None,
            "launcher_exit_status": None,
            "launcher_finished_at": None,
            "launcher_finished_monotonic": None,
            "console_output": "",
            "console_failed": False,
            "console_failed_detail": "",
            "activity_seen": False,
            "solver_started": False,
            "solver_kind": "",
            "runtime_phase": "STARTING",
            "runtime_started_monotonic": runtime_started_monotonic,
            "last_runtime_activity_at": runtime_started_monotonic,
            "runtime_phase_text_pending": "",
            "runtime_diagnostic_status": "",
            "runtime_diagnostic_detail": "",
            "log_position": 0,
            "msg_position": 0,
            "dat_position": 0,
            "solver_start_timeout": False,
            "solver_start_timeout_detail": "",
            "runtime_completion_confirmed": False,
            "runtime_completion_reason": "",
            "pre_started": False,
            "pre_finished": False,
            "standard_started": False,
            "package_started": False,
            "explicit_started": False,
            "seen_sta": False,
            "sta_valid": False,
            "datacheck_stable_polls": 0,
            "sta_signature": None,
            "sta_stable_polls": 0,
            "finish_candidate_since": None,
            "termination_stable_polls": 0,
            "termination_candidate_since": None,
            "diagnostic_baseline": diagnostic_baseline,
            "submitted_at": submitted_at,
            "finish_emitted": False,
            "finalizing": False,
            "finalized": False,
        }

        self.run_records[job_key] = run
        self.active_runs[job_key] = run
        self.refresh_job_selector()
        self.memory_adapter.register_job(
            job_key=job_key,
            job_name=options.job_name,
            work_dir=work_dir,
            monitor_mode="learning",
        )
        self.current_work_dir = work_dir
        self.current_job_name = options.job_name
        self.command_preview = command
        self.show_runtime_panel()
        self.select_run(job_key)
        self.append_history(f"提交作业：{ui_runtime_job_display_label(self.run_records, job_key)}\n{command}")

        if queue_item is not None:
            queue_item.status = STATUS_RUNNING
            queue_item.message = "运行中"
            queue_item.active_job_key = job_key
            queue_item.effective_work_dir = work_dir

            queue_item.source_inp_path = (
                workspace_info.get(
                    "source_inp_path",
                    "",
                )
                or queue_item.source_inp_path
                or options.inp_file
            )

            queue_item.archive_dir = (
                workspace_info.get(
                    "archive_dir",
                    "",
                )
                or queue_item.archive_dir
            )

            queue_item.cleanup_after_archive = bool(
                workspace_info.get(
                    "cleanup_after_archive",
                    False,
                )
            )

            self.refresh_visible_queue_manager()
            self.update_queue_status_label()

        if not self.runtime_controller.start_process(
            job_key=job_key,
            run=run,
            command=command,
        ):
            self.append_history(f"Abaqus 进程启动失败：{options.job_name}")

            self.active_runs.pop(
                job_key,
                None,
            )

            if queue_item is not None:
                queue_item.status = STATUS_FAILED
                queue_item.message = "启动失败"
                queue_item.active_job_key = ""

            self.refresh_visible_queue_manager()
            self.update_queue_status_label()
            self.request_dispatch_queue()

            return False

        self.update_process_buttons(True)
        return True

    def finalize_completed_run(self, job_key: str) -> None:
        run = self.active_runs.get(job_key)
        if not run or run.get("finalized") or run.get("finalizing"):
            return
        run["finalizing"] = True
        run["finalized"] = True
        run["end_time"] = time.time()
        timer = run.get("timer")
        if timer is not None:
            timer.stop()
        self.memory_adapter.finalize_job(job_key)
        status, detail = self.inspect_finished_job(job_key)
        status, detail = resolve_final_status_from_console(
            console_output=str(run.get("console_output", "") or ""),
            console_failed=bool(run.get("console_failed")),
            console_failed_detail=str(run.get("console_failed_detail", "") or ""),
            status=status,
            detail=detail,
        )
        queue_item = run.get("queue_item")
        if queue_item is not None:
            decision = resolve_finalization_status(
                FinalizationInput(
                    is_datacheck=run_is_datacheck(
                        bool(run.get("datacheck_only")),
                        queue_item.datacheck_only,
                    ),
                    terminating=bool(run.get("terminating", False)),
                    launcher_exit_code=run.get("launcher_exit_code"),
                    console_failed=bool(run.get("console_failed")),
                    console_failed_detail=str(run.get("console_failed_detail", "") or ""),
                    diagnostic_status=status,
                    diagnostic_detail=detail,
                    solver_start_timeout=bool(run.get("solver_start_timeout")),
                    solver_start_timeout_detail=str(run.get("solver_start_timeout_detail", "") or ""),
                    runtime_completion_confirmed=bool(run.get("runtime_completion_confirmed")),
                    runtime_completion_reason=str(run.get("runtime_completion_reason", "") or ""),
                )
            )
            queue_item.status = decision.status
            queue_item.message = decision.message

            queue_item.active_job_key = ""
            self._refresh_queue_dependencies()
            self._refresh_queue_views()
            self._append_history(f"作业完成状态写回后已刷新队列依赖：{queue_item.job_name}")
        self.archive_or_defer_finished_job(run)
        final_text = queue_item.status if queue_item is not None else (status or "finished")
        self.append_history(f"{run['job_name']} final status: {final_text}")
        self.notify_job_finished(
            run,
            final_text,
            queue_item.message if queue_item is not None else detail,
        )
        self.active_runs.pop(job_key, None)
        self.runtime_controller.unregister_run(job_key)
        self.refresh_job_selector()
        self.refresh_job_stats()
        self.refresh_selected_run_status(job_key)
        if self.current_job_key == job_key:
            self.status_label.setText(final_text)
            if self.active_runs:
                self.select_run(next(iter(self.active_runs)))
        self.update_process_buttons(bool(self.active_runs))
        self.refresh_visible_queue_manager()
        self.update_queue_status_label()
        self.process_deferred_archives()
        self.request_dispatch_queue()

    def run_is_ssd_independent_archive_candidate(self, run: dict) -> bool:
        queue_item = run.get("queue_item")
        if queue_item is None:
            return False
        calculation_root = str(run.get("calculation_root_dir", "") or "").strip()
        if not calculation_root:
            calculation_root = str(getattr(queue_item, "calculation_root_dir", "") or "").strip()
        return is_ssd_independent_work_dir(
            work_dir=str(run.get("work_dir", "") or ""),
            calculation_root_dir=calculation_root,
            job_name=str(run.get("job_name", "") or ""),
            cleanup_after_archive=bool(run.get("cleanup_after_archive", False)),
        )

    def run_allows_archive_move(self, run: dict) -> bool:
        queue_item = run.get("queue_item")
        status = str(getattr(queue_item, "status", "") if queue_item is not None else "")
        return status in (STATUS_COMPLETED, STATUS_DATACHECK_COMPLETED)

    def archive_move_reserved_key_for_run(self, run: dict) -> tuple[str, str]:
        return scheduler_managed_job_key(
            str(run.get("work_dir", "") or ""),
            str(run.get("job_name", "") or ""),
        )

    def mark_archive_move_result(self, run: dict, status: str, error: str) -> None:
        run["archive_status"] = status
        run["archive_error"] = error
        queue_item = run.get("queue_item")
        if queue_item is not None:
            queue_item.archive_status = status
            queue_item.archive_error = error

    def enqueue_archive_move(self, run_key: str, run: dict) -> bool:
        if not self.run_is_ssd_independent_archive_candidate(run):
            return False
        if not self.run_allows_archive_move(run):
            work_dir = str(run.get("work_dir", "") or "")
            self.mark_archive_move_result(run, "保留目录", "")
            self.append_history(f"作业未成功完成，保留 SSD 计算目录供检查：{work_dir}")
            return True

        archive_root = str(run.get("archive_dir", "") or "").strip()
        work_dir = str(run.get("work_dir", "") or "").strip()
        job_name = str(run.get("job_name", "") or "").strip()
        if not archive_root or not work_dir or not job_name:
            message = "SSD 整体归档缺少归档根目录、计算目录或作业名"
            self.mark_archive_move_result(run, "归档失败", message)
            self.append_history(f"SSD 计算目录归档失败：{job_name or run.get('job_name', '')}\n{message}")
            return True

        task_id = uuid4().hex
        queue_item = run.get("queue_item")
        task = ArchiveMoveTask(
            task_id=task_id,
            run_key=run_key,
            job_name=job_name,
            plan=build_archive_move_plan(
                work_dir=work_dir,
                archive_root=archive_root,
                job_name=job_name,
            ),
            queue_item_id=getattr(queue_item, "item_id", "") if queue_item is not None else "",
        )
        reserved_key = self.archive_move_reserved_key_for_run(run)
        self._archive_move_contexts[task_id] = {
            "run_key": run_key,
            "reserved_key": reserved_key,
        }
        self._archive_move_reserved_keys.add(reserved_key)
        self.mark_archive_move_result(run, "等待归档", "")

        if self.archive_move_service.enqueue(task):
            self.append_history(f"SSD 计算目录等待整体归档：{job_name}\n{work_dir}")
            return True

        self._archive_move_contexts.pop(task_id, None)
        self._archive_move_reserved_keys.discard(reserved_key)
        message = "归档任务未启动：窗口正在关闭"
        self.mark_archive_move_result(run, "归档失败", message)
        self.append_history(f"SSD 计算目录归档失败：{job_name}\n{message}")
        return True

    def release_archive_move_context(self, task: ArchiveMoveTask) -> dict | None:
        context = self._archive_move_contexts.pop(task.task_id, None)
        if context is not None:
            reserved_key = context.get("reserved_key")
            if reserved_key:
                self._archive_move_reserved_keys.discard(reserved_key)
        return context

    def archive_move_context_run(self, task: ArchiveMoveTask) -> tuple[dict | None, dict | None]:
        context = self.release_archive_move_context(task)
        if self._closing or context is None:
            return context, None
        run = self.run_records.get(str(context.get("run_key", "")))
        return context, run

    def on_archive_move_succeeded(self, task: ArchiveMoveTask, result) -> None:
        _context, run = self.archive_move_context_run(task)
        if run is None:
            return
        run["archive_destination"] = result.archive_destination
        self.mark_archive_move_result(run, "已归档", "")
        self.append_history(f"SSD 计算目录已归档：{run.get('job_name', task.job_name)}\n{result.archive_destination}")
        self.refresh_visible_queue_manager()
        self.update_queue_status_label()
        self.request_dispatch_queue()

    def on_archive_move_blocked(self, task: ArchiveMoveTask, message: str) -> None:
        _context, run = self.archive_move_context_run(task)
        if run is None:
            return
        self.mark_archive_move_result(run, "归档阻塞", message)
        self.append_history(f"SSD 计算目录归档阻塞：{run.get('job_name', task.job_name)}\n{message}")
        self.refresh_visible_queue_manager()
        self.update_queue_status_label()
        self.request_dispatch_queue()

    def on_archive_move_failed(self, task: ArchiveMoveTask, message: str) -> None:
        _context, run = self.archive_move_context_run(task)
        if run is None:
            return
        self.mark_archive_move_result(run, "归档失败", message)
        self.append_history(
            f"SSD 计算目录归档失败：{run.get('job_name', task.job_name)}\n"
            f"{message}\n"
            "请人工检查源目录和归档目录，跨盘移动失败时可能存在部分文件。"
        )
        self.refresh_visible_queue_manager()
        self.update_queue_status_label()
        self.request_dispatch_queue()

    def archive_or_defer_finished_job(self, run: dict) -> None:
        if self.run_is_ssd_independent_archive_candidate(run):
            if not self.run_allows_archive_move(run):
                work_dir = str(run.get("work_dir", "") or "")
                self.mark_archive_move_result(run, "保留目录", "")
                self.append_history(f"作业未成功完成，保留 SSD 计算目录供检查：{work_dir}")
                return

            coordinator = ArchiveCoordinator(
                self.queue_items,
                self.deferred_archive_runs,
            )
            should_defer, dependents = coordinator.should_defer_archive(run)
            if should_defer:
                run["archive_deferred"] = True
                run["archive_deferred_reason"] = ", ".join(item.job_name for item in dependents)
                self.deferred_archive_runs[run["key"]] = run
                coordinator.mark_archive_result(
                    run,
                    "等待重启动依赖",
                    f"等待依赖作业结束：{run['archive_deferred_reason']}",
                )
                self.append_history(
                    f"暂缓归档：{run['job_name']} 仍被重启动作业依赖，等待：{run['archive_deferred_reason']}"
                )
                return

            if self.enqueue_archive_move(str(run.get("key", "")), run):
                return

        outcome = ArchiveCoordinator(
            self.queue_items,
            self.deferred_archive_runs,
        ).archive_or_defer_run(run)
        if outcome["action"] == "deferred":
            self.append_history(
                f"暂缓归档：{run['job_name']} 仍被重启动作业依赖，等待：{run['archive_deferred_reason']}"
            )
            return
        self.handle_archive_result(
            run,
            outcome["result"],
        )

    def process_deferred_archives(self) -> None:
        coordinator = ArchiveCoordinator(
            self.queue_items,
            self.deferred_archive_runs,
        )
        for run_key, run in list(self.deferred_archive_runs.items()):
            should_defer, _dependents = coordinator.should_defer_archive(run)
            if should_defer:
                continue
            self.deferred_archive_runs.pop(run_key, None)
            run["archive_deferred"] = False
            self.append_history(f"依赖作业已结束，开始归档：{run['job_name']}")
            if self.run_is_ssd_independent_archive_candidate(run) and self.enqueue_archive_move(run_key, run):
                continue
            result = coordinator.archive_run(run)
            self.handle_archive_result(
                run,
                result,
            )

    def archive_finished_job(self, run: dict) -> None:
        result = ArchiveCoordinator(
            self.queue_items,
            self.deferred_archive_runs,
        ).archive_run(run)
        self.handle_archive_result(
            run,
            result,
        )

    def handle_archive_result(self, run: dict, result: dict) -> None:
        if result.get("exception"):
            self.append_history(f"归档结果文件失败：{run['job_name']}\n{result['error']}")
            return
        if not result.get("status"):
            return
        if result.get("message"):
            self.append_history(result["message"])
        if result.get("error"):
            self.append_history(f"归档过程中存在错误：\n{result['error']}")

    def terminate_job(
        self,
    ) -> None:
        """
        手动终止当前 Job。

        发送 terminate 命令后，不立即判定失败；
        等待 LCK 释放，再统一标记为已终止。
        """
        job_key = self.selected_job_key()

        run = self.active_runs.get(job_key)

        if run is None:
            return

        if run.get(
            "finalized",
            False,
        ):
            return

        queue_item = run.get("queue_item")

        if queue_item is not None:
            queue_item.status = STATUS_TERMINATING

            queue_item.message = "正在终止"

        self.status_label.setText("Terminating")

        self.pause_btn.setEnabled(False)

        self.runtime_controller.terminate_job(job_key)

        self.refresh_visible_queue_manager()
        self.update_queue_status_label()


__all__ = [
    "FinalizationDecision",
    "FinalizationInput",
    "JobController",
    "resolve_datacheck_final_status",
    "resolve_final_status_from_console",
    "resolve_finalization_status",
    "run_is_datacheck",
]
