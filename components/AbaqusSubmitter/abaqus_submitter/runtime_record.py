"""运行记录的统一构造与状态迁移接口。"""

from __future__ import annotations

import time
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any


class RuntimeRecord(dict[str, Any]):
    """支持字典访问的运行记录，同时集中维护跨来源状态不变量。"""

    REQUIRED_FIELDS = (
        "key",
        "work_dir",
        "job_name",
        "queue_item",
        "runtime_phase",
        "submitted_at",
    )

    @classmethod
    def for_internal(
        cls,
        *,
        key: str,
        options: Any,
        command: str,
        queue_item: Any,
        workspace_info: Mapping[str, Any],
        existing_result_info: Mapping[str, Any],
        diagnostic_baseline: Mapping[str, Any],
        calculation_root_dir: str = "",
        submitted_at: float | None = None,
        monotonic_now: float | None = None,
    ) -> RuntimeRecord:
        """为本程序提交的作业创建完整运行记录。"""
        now = time.monotonic() if monotonic_now is None else monotonic_now
        submitted = time.time() if submitted_at is None else submitted_at
        work_dir = str(Path(options.inp_file).parent)
        existing_action = str(existing_result_info.get("action", ""))
        is_backup = existing_action == "backup"
        record = cls(
            key=key,
            process=None,
            timer=None,
            work_dir=work_dir,
            job_name=options.job_name,
            scheduler_job_id=str(getattr(queue_item, "job_id", "") or ""),
            attempt_id=str(getattr(queue_item, "attempt_id", "") or ""),
            command=command,
            is_external=False,
            is_scan_attached=False,
            is_paused=False,
            datacheck_only=bool(options.datacheck),
            terminating=False,
            terminating_at=0.0,
            sta_position=0,
            sta_state={},
            log="",
            queue_item=queue_item,
            source_inp_path=workspace_info.get("source_inp_path", ""),
            calculation_root_dir=calculation_root_dir,
            archive_dir=workspace_info.get("archive_dir", ""),
            archive_destination="",
            archive_status="",
            archive_error="",
            resolved_oldjob_arg=workspace_info.get("resolved_oldjob_arg", ""),
            resolved_oldjob_source=workspace_info.get("resolved_oldjob_source", ""),
            resolved_oldjob_dir=workspace_info.get("resolved_oldjob_dir", ""),
            resolved_oldjob_reference_key=workspace_info.get(
                "resolved_oldjob_reference_key",
                "",
            ),
            cleanup_after_archive=bool(workspace_info.get("cleanup_after_archive", False)),
            existing_result_action=existing_action,
            backup_odb_path=existing_result_info.get("odb", "") if is_backup else "",
            backup_sta_path=existing_result_info.get("sta", "") if is_backup else "",
            memory_monitor_activated=False,
            memory_stable_logged=False,
            memory_current=0,
            memory_peak=0,
            memory_estimated=0,
            memory_monitor_mode="learning",
            memory_monitor_stable=False,
            launcher_finished=False,
            launcher_exit_code=None,
            launcher_exit_status=None,
            launcher_finished_at=None,
            launcher_finished_monotonic=None,
            launcher_started=False,
            launcher_started_monotonic=None,
            console_output="",
            console_failed=False,
            console_failed_detail="",
            activity_seen=False,
            solver_started=False,
            solver_kind="",
            known_solver_pids=(),
            solver_pid_seen_at=None,
            solver_pid_last_seen_at=None,
            solver_pid_confidence="",
            runtime_phase="STARTING",
            runtime_started_monotonic=now,
            last_runtime_activity_at=now,
            runtime_phase_text_pending="",
            runtime_diagnostic_status="",
            runtime_diagnostic_detail="",
            log_position=0,
            msg_position=0,
            dat_position=0,
            solver_start_timeout=False,
            solver_start_timeout_detail="",
            runtime_completion_confirmed=False,
            runtime_completion_reason="",
            pre_started=False,
            pre_finished=False,
            standard_started=False,
            package_started=False,
            explicit_started=False,
            seen_sta=False,
            sta_valid=False,
            datacheck_stable_polls=0,
            sta_signature=None,
            sta_stable_polls=0,
            finish_candidate_since=None,
            termination_stable_polls=0,
            termination_candidate_since=None,
            diagnostic_baseline=dict(diagnostic_baseline),
            submitted_at=submitted,
            finish_emitted=False,
            finalizing=False,
            finalized=False,
        )
        cls.validate(record)
        return record

    @classmethod
    def for_attached(
        cls,
        *,
        key: str,
        item: Any,
        work_dir: str,
        source_label: str,
        solver_pids: tuple[int, ...],
        solver_kind: str,
        rss_bytes: int,
        submitted_at: float,
        monotonic_now: float | None = None,
        wall_now: float | None = None,
    ) -> RuntimeRecord:
        """为扫描发现并接管的作业创建完整运行记录。"""
        now = time.monotonic() if monotonic_now is None else monotonic_now
        finished_at = time.time() if wall_now is None else wall_now
        has_solver = bool(solver_pids)
        record = cls(
            key=key,
            process=None,
            timer=None,
            work_dir=work_dir,
            job_name=item.job_name,
            scheduler_job_id=str(getattr(item, "job_id", "") or ""),
            attempt_id=str(getattr(item, "attempt_id", "") or ""),
            command=f"{source_label} Abaqus job",
            is_external=bool(item.is_external),
            is_scan_attached=True,
            is_paused=False,
            datacheck_only=bool(item.datacheck_only),
            terminating=False,
            terminating_at=0.0,
            sta_position=0,
            sta_state={},
            log=f"{source_label.capitalize()} Abaqus job attached to runtime monitor.",
            queue_item=item,
            source_inp_path=item.source_inp_path or item.inp_path,
            calculation_root_dir=item.calculation_root_dir or "",
            archive_dir="",
            archive_destination="",
            archive_status="",
            archive_error="",
            resolved_oldjob_arg="",
            resolved_oldjob_source="",
            resolved_oldjob_dir="",
            resolved_oldjob_reference_key="",
            cleanup_after_archive=False,
            existing_result_action="",
            backup_odb_path="",
            backup_sta_path="",
            memory_monitor_activated=True,
            memory_stable_logged=False,
            memory_current=rss_bytes,
            memory_peak=rss_bytes,
            memory_estimated=rss_bytes,
            memory_monitor_mode="external",
            memory_monitor_stable=True,
            launcher_finished=True,
            launcher_exit_code=0,
            launcher_exit_status=None,
            launcher_finished_at=finished_at,
            launcher_finished_monotonic=now,
            launcher_started=True,
            launcher_started_monotonic=now,
            console_output="",
            console_failed=False,
            console_failed_detail="",
            activity_seen=True,
            solver_started=True,
            solver_kind=solver_kind,
            known_solver_pids=solver_pids,
            solver_pid_seen_at=now if has_solver else None,
            solver_pid_last_seen_at=now if has_solver else None,
            solver_pid_confidence="high" if has_solver else "",
            runtime_phase="SOLVING",
            runtime_started_monotonic=now,
            last_runtime_activity_at=now,
            runtime_phase_text_pending="",
            runtime_diagnostic_status="",
            runtime_diagnostic_detail="",
            log_position=0,
            msg_position=0,
            dat_position=0,
            solver_start_timeout=False,
            solver_start_timeout_detail="",
            runtime_completion_confirmed=False,
            runtime_completion_reason="",
            pre_started=False,
            pre_finished=False,
            standard_started=solver_kind == "standard",
            package_started=False,
            explicit_started=solver_kind == "explicit",
            seen_sta=False,
            sta_valid=False,
            datacheck_stable_polls=0,
            sta_signature=None,
            sta_stable_polls=0,
            finish_candidate_since=None,
            termination_stable_polls=0,
            termination_candidate_since=None,
            diagnostic_baseline={},
            submitted_at=submitted_at,
            finish_emitted=False,
            finalizing=False,
            finalized=False,
        )
        cls.validate(record)
        return record

    @staticmethod
    def prepare_internal_monitor(
        run: MutableMapping[str, Any],
        *,
        process: Any,
        timer: Any,
        monotonic_now: float | None = None,
    ) -> None:
        """补齐内部进程监控所需状态，并绑定进程与计时器。"""
        now = time.monotonic() if monotonic_now is None else monotonic_now
        run["process"] = process
        run["timer"] = timer
        RuntimeRecord._apply_defaults(
            run,
            activity_seen=False,
            solver_started=False,
            solver_kind="",
            known_solver_pids=(),
            solver_pid_seen_at=None,
            solver_pid_last_seen_at=None,
            solver_pid_confidence="",
            runtime_phase="STARTING",
            runtime_started_monotonic=now,
            last_runtime_activity_at=now,
            runtime_phase_text_pending="",
            runtime_diagnostic_status="",
            runtime_diagnostic_detail="",
            log_position=0,
            msg_position=0,
            dat_position=0,
            launcher_finished_at=None,
            launcher_finished_monotonic=None,
            launcher_started=False,
            launcher_started_monotonic=None,
            solver_start_timeout=False,
            solver_start_timeout_detail="",
            seen_sta=False,
            sta_valid=False,
            datacheck_stable_polls=0,
            sta_signature=None,
            sta_stable_polls=0,
            finish_candidate_since=None,
            termination_stable_polls=0,
            termination_candidate_since=None,
            runtime_completion_confirmed=False,
            runtime_completion_reason="",
            finish_emitted=False,
        )

    @staticmethod
    def prepare_external_monitor(
        run: MutableMapping[str, Any],
        *,
        timer: Any,
        monotonic_now: float | None = None,
        wall_now: float | None = None,
    ) -> None:
        """补齐外部接管监控所需状态，并绑定轮询计时器。"""
        now = time.monotonic() if monotonic_now is None else monotonic_now
        finished_at = time.time() if wall_now is None else wall_now
        has_solver = bool(run.get("known_solver_pids"))
        run["process"] = None
        run["timer"] = timer
        RuntimeRecord._apply_defaults(
            run,
            is_external=True,
            activity_seen=True,
            solver_started=True,
            solver_kind="",
            known_solver_pids=(),
            solver_pid_seen_at=now if has_solver else None,
            solver_pid_last_seen_at=now if has_solver else None,
            solver_pid_confidence="high" if has_solver else "",
            runtime_phase="SOLVING",
            runtime_started_monotonic=now,
            last_runtime_activity_at=now,
            runtime_phase_text_pending="",
            runtime_diagnostic_status="",
            runtime_diagnostic_detail="",
            log_position=0,
            msg_position=0,
            dat_position=0,
            launcher_finished=True,
            launcher_exit_code=0,
            launcher_exit_status=None,
            launcher_finished_at=finished_at,
            launcher_finished_monotonic=now,
            launcher_started=True,
            launcher_started_monotonic=now,
            solver_start_timeout=False,
            solver_start_timeout_detail="",
            seen_sta=False,
            sta_valid=False,
            datacheck_stable_polls=0,
            sta_signature=None,
            sta_stable_polls=0,
            finish_candidate_since=None,
            termination_stable_polls=0,
            termination_candidate_since=None,
            runtime_completion_confirmed=False,
            runtime_completion_reason="",
            finish_emitted=False,
            _process_connections=(),
        )

    @staticmethod
    def update_memory(
        run: MutableMapping[str, Any],
        *,
        current: int,
        peak: int | None = None,
        estimated: int | None = None,
        mode: str | None = None,
        stable: bool | None = None,
    ) -> None:
        """原子更新内存投影，避免调用方各自维护字段组合。"""
        current_value = max(0, int(current or 0))
        run["memory_current"] = current_value
        if peak is not None:
            run["memory_peak"] = max(0, int(peak or 0))
        if estimated is not None:
            run["memory_estimated"] = max(0, int(estimated or 0))
        if mode is not None:
            run["memory_monitor_mode"] = mode or "learning"
        if stable is not None:
            run["memory_monitor_stable"] = bool(stable)

    @classmethod
    def validate(cls, run: Mapping[str, Any]) -> None:
        """验证所有来源都必须满足的最小运行记录协议。"""
        missing = [field for field in cls.REQUIRED_FIELDS if field not in run]
        if missing:
            raise ValueError(f"运行记录缺少必要字段：{', '.join(missing)}")
        if not str(run.get("key") or "").strip():
            raise ValueError("运行记录 key 不能为空")
        if not str(run.get("job_name") or "").strip():
            raise ValueError("运行记录 job_name 不能为空")

    @staticmethod
    def _apply_defaults(run: MutableMapping[str, Any], **defaults: Any) -> None:
        for key, value in defaults.items():
            run.setdefault(key, value)
