"""Collect and evaluate runtime evidence for internally submitted Abaqus jobs."""

from __future__ import annotations

import time
from pathlib import Path

from .abaqus_diagnostics import (
    classify_job_text,
    decode_abaqus_text,
    inspect_sta_structure,
)
from .constants import SOLVER_START_GRACE_SECONDS, STA_POLL_INTERVAL_MS
from .process_scanner import get_runtime_process_evidence
from .diagnostics import hang_probe, hang_probe_log


STA_STABLE_POLLS_REQUIRED = 3
FINISH_CANDIDATE_QUIET_SECONDS = 10.0
TERMINATION_STABLE_POLLS_REQUIRED = 3
TERMINATION_STABLE_SECONDS = (
    (TERMINATION_STABLE_POLLS_REQUIRED - 1) * STA_POLL_INTERVAL_MS / 1000.0
)
ORPHANED_RUNTIME_STABLE_POLLS_REQUIRED = 6
ORPHANED_RUNTIME_STABLE_SECONDS = 30.0
SLOW_DIAGNOSTIC_INTERVAL_SECONDS = 10.0


def _empty_evidence() -> dict:
    return {
        "log_pre_started": False,
        "log_pre_finished": False,
        "log_solver_started": False,
        "solver_kind": "",
        "sta_exists": False,
        "sta_is_current_run": False,
        "sta_has_header": False,
        "sta_has_progress_rows": False,
        "sta_parse_ok": False,
        "sta_tail_failure_hint": False,
        "sta_reason": "",
        "sta_last_step": None,
        "sta_last_increment": None,
        "sta_valid": False,
        "sta_changed": False,
        "sta_signature": None,
        "lck_exists": False,
        "solver_pid_active": False,
        "solver_pid_confidence": "",
        "solver_pids": (),
        "diagnostic_status": "",
        "diagnostic_detail": "",
        "diagnostic_failed": False,
        "log_delta": "",
        "sta_delta": "",
    }


def _baseline_signature(run: dict, extension: str) -> tuple[int, int] | None:
    signature = (run.get("diagnostic_baseline") or {}).get(extension)
    if signature is None:
        return None
    try:
        return int(signature[0]), int(signature[1])
    except (TypeError, ValueError, IndexError):
        return None


def _read_file_delta(run: dict, extension: str, position_key: str) -> tuple[str, bool]:
    path = Path(run["work_dir"]) / f"{run['job_name']}{extension}"
    signature_key = f"{position_key}_signature"
    try:
        stat_result = path.stat()
    except OSError:
        run[position_key] = 0
        run.pop(signature_key, None)
        return "", False

    current_signature = (stat_result.st_mtime_ns, stat_result.st_size)
    baseline_signature = _baseline_signature(run, extension)
    position = int(run.get(position_key, 0) or 0)

    if position == 0 and baseline_signature is not None:
        if current_signature == baseline_signature:
            return "", False
        baseline_size = baseline_signature[1]
        position = baseline_size if stat_result.st_size >= baseline_size else 0

    if stat_result.st_size < position:
        position = 0

    if run.get(signature_key) == current_signature and position >= stat_result.st_size:
        return "", False

    try:
        with path.open("rb") as stream:
            stream.seek(position)
            data = stream.read()
            run[position_key] = stream.tell()
            run[signature_key] = current_signature
    except OSError:
        return "", False

    if not data:
        return "", False
    return decode_abaqus_text(data), True


def read_log_delta(run: dict) -> str:
    text, _changed = _read_file_delta(run, ".log", "log_position")
    return text


def read_sta_delta(run: dict) -> str:
    text, _changed = _read_file_delta(run, ".sta", "sta_position")
    return text


def build_sta_signature(run: dict) -> tuple[int, int] | None:
    path = Path(run["work_dir"]) / f"{run['job_name']}.sta"
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return stat_result.st_size, stat_result.st_mtime_ns


def _sta_is_current_run(run: dict, signature: tuple[int, int] | None) -> bool:
    if signature is None or signature[0] <= 0:
        return False
    baseline = _baseline_signature(run, ".sta")
    if baseline is None:
        return True
    baseline_sta_signature = (baseline[1], baseline[0])
    return signature != baseline_sta_signature


def _phase_evidence_from_text(text: str) -> dict:
    upper_text = str(text or "").upper()
    standard_started = (
        "BEGIN ABAQUS/STANDARD ANALYSIS" in upper_text
        or "RUN STANDARD.EXE" in upper_text
    )
    explicit_started = (
        "BEGIN ABAQUS/EXPLICIT ANALYSIS" in upper_text
        or "RUN EXPLICIT.EXE" in upper_text
    )
    return {
        "log_pre_started": any(
            marker in upper_text
            for marker in (
                "BEGIN ANALYSIS INPUT FILE PROCESSOR",
                "RUN PRE.EXE",
                "RUN PACKAGE.EXE",
                "ABAQUS/EXPLICIT PACKAGER",
            )
        ),
        "log_pre_finished": "END ANALYSIS INPUT FILE PROCESSOR" in upper_text,
        "log_solver_started": standard_started or explicit_started,
        "solver_kind": "explicit" if explicit_started else ("standard" if standard_started else ""),
    }


def _inspect_sta_structure_cached(run: dict, sta_path: Path) -> dict:
    signature = build_sta_signature(run)
    cached_signature = run.get("sta_structure_signature")
    cached_info = run.get("sta_structure_info")
    if signature is not None and cached_signature == signature and isinstance(cached_info, dict):
        return cached_info

    sta_info = inspect_sta_structure(
        sta_path,
        baseline=_baseline_signature(run, ".sta"),
        submitted_after=run.get("submitted_at"),
    )
    if signature is not None and sta_info.get("exists"):
        run["sta_structure_signature"] = signature
        run["sta_structure_info"] = sta_info
    else:
        run.pop("sta_structure_signature", None)
        run.pop("sta_structure_info", None)
    return sta_info


def _solver_start_grace_near(run: dict, now: float) -> bool:
    launcher_finished_at = run.get("launcher_finished_monotonic")
    if launcher_finished_at is None:
        return False
    try:
        elapsed = now - float(launcher_finished_at)
    except (TypeError, ValueError):
        return False
    return elapsed >= max(0.0, SOLVER_START_GRACE_SECONDS - 10.0)


def _should_read_slow_diagnostics(run: dict, evidence: dict, now: float) -> bool:
    if run.get("terminating"):
        return True
    if run.get("finish_candidate_since") is not None:
        return True
    if int(run.get("sta_stable_polls", 0) or 0) >= STA_STABLE_POLLS_REQUIRED:
        return True
    if run.get("launcher_finished") and not run.get("slow_diagnostic_launcher_finished_read"):
        return True
    if _solver_start_grace_near(run, now):
        return True
    if run.get("runtime_diagnostic_status") in {"失败", "终止"}:
        return True
    if evidence.get("diagnostic_status") in {"失败", "终止"}:
        return True

    last_read_at = run.get("slow_diagnostic_last_read_at")
    if last_read_at is None:
        return True
    try:
        return now - float(last_read_at) >= SLOW_DIAGNOSTIC_INTERVAL_SECONDS
    except (TypeError, ValueError):
        return True


def collect_runtime_evidence(run: dict) -> dict:
    probe_start = time.monotonic()
    job_name = run.get("job_name")
    evidence = _empty_evidence()
    step_start = time.monotonic()
    log_delta = read_log_delta(run)
    hang_probe_log(
        "collect_runtime_evidence.step",
        time.monotonic() - step_start,
        threshold=0.2,
        job_name=job_name,
        step="read_log_delta",
    )
    step_start = time.monotonic()
    sta_delta = read_sta_delta(run)
    hang_probe_log(
        "collect_runtime_evidence.step",
        time.monotonic() - step_start,
        threshold=0.2,
        job_name=job_name,
        step="read_sta_delta",
    )
    pending_text = str(run.pop("runtime_phase_text_pending", "") or "")
    phase_evidence = _phase_evidence_from_text("\n".join(part for part in (pending_text, log_delta) if part))
    evidence.update(phase_evidence)

    sta_path = Path(run["work_dir"]) / f"{run['job_name']}.sta"
    step_start = time.monotonic()
    sta_info = _inspect_sta_structure_cached(run, sta_path)
    hang_probe_log(
        "collect_runtime_evidence.step",
        time.monotonic() - step_start,
        threshold=0.2,
        job_name=job_name,
        step="inspect_sta_structure",
    )
    sta_signature = sta_info.get("signature")
    evidence["log_delta"] = log_delta
    evidence["sta_delta"] = sta_delta
    evidence["sta_exists"] = bool(sta_info.get("exists"))
    evidence["sta_is_current_run"] = bool(sta_info.get("is_current_run"))
    evidence["sta_has_header"] = bool(sta_info.get("has_header"))
    evidence["sta_has_progress_rows"] = bool(sta_info.get("has_progress_rows"))
    evidence["sta_parse_ok"] = bool(sta_info.get("parse_ok"))
    evidence["sta_tail_failure_hint"] = bool(sta_info.get("tail_failure_hint"))
    evidence["sta_reason"] = str(sta_info.get("reason", "") or "")
    evidence["sta_last_step"] = sta_info.get("last_step")
    evidence["sta_last_increment"] = sta_info.get("last_increment")
    evidence["sta_valid"] = (
        evidence["sta_is_current_run"]
        and evidence["sta_parse_ok"]
        and evidence["sta_has_progress_rows"]
        and not evidence["sta_tail_failure_hint"]
    )
    evidence["sta_signature"] = sta_signature if evidence["sta_valid"] else None
    evidence["sta_changed"] = bool(sta_delta) or (
        evidence["sta_valid"]
        and run.get("sta_signature") is not None
        and run.get("sta_signature") != sta_signature
    )
    evidence["lck_exists"] = (Path(run["work_dir"]) / f"{run['job_name']}.lck").exists()

    step_start = time.monotonic()
    process_snapshot_available = bool(run.get("process_snapshot_available"))
    process_evidence = get_runtime_process_evidence(
        str(run.get("work_dir", "") or ""),
        str(run.get("job_name", "") or ""),
        process_rows=run.get("process_snapshot") if process_snapshot_available else None,
        snapshot_available=process_snapshot_available,
        known_solver_pids=tuple(run.get("known_solver_pids") or ()),
    )
    hang_probe_log(
        "collect_runtime_evidence.step",
        time.monotonic() - step_start,
        threshold=0.2,
        job_name=job_name,
        step="get_runtime_process_evidence",
    )
    evidence["solver_pid_active"] = bool(process_evidence.get("active"))
    evidence["solver_pid_confidence"] = str(process_evidence.get("confidence", "") or "")
    evidence["solver_pids"] = tuple(process_evidence.get("pids") or ())
    evidence["known_pid_active"] = bool(process_evidence.get("known_pid_active"))
    evidence["fallback_match_used"] = bool(process_evidence.get("fallback_match_used"))
    if evidence["solver_pid_confidence"] == "high" and evidence["solver_pids"]:
        known_pids = set()
        for pid in run.get("known_solver_pids") or ():
            try:
                known_pids.add(int(pid))
            except (TypeError, ValueError):
                continue
        known_pids.update(int(pid) for pid in evidence["solver_pids"])
        run["known_solver_pids"] = tuple(sorted(known_pids))
        now = time.monotonic()
        run.setdefault("solver_pid_seen_at", now)
        run["solver_pid_last_seen_at"] = now
        run["solver_pid_confidence"] = evidence["solver_pid_confidence"]
    if not evidence["solver_kind"]:
        evidence["solver_kind"] = str(process_evidence.get("solver_kind", "") or "")

    diagnostic_parts = [log_delta, sta_delta]
    now = time.monotonic()
    if _should_read_slow_diagnostics(run, evidence, now):
        for extension in (".msg", ".dat"):
            step_start = time.monotonic()
            text, _changed = _read_file_delta(
                run,
                extension,
                f"{extension.lstrip('.')}_position",
            )
            hang_probe_log(
                "collect_runtime_evidence.step",
                time.monotonic() - step_start,
                threshold=0.2,
                job_name=job_name,
                step=f"read_{extension}",
            )
            if text:
                diagnostic_parts.append(text)
        run["slow_diagnostic_last_read_at"] = now
        if run.get("launcher_finished"):
            run["slow_diagnostic_launcher_finished_read"] = True
    diagnostic_status, diagnostic_detail = classify_job_text("\n".join(diagnostic_parts))
    if evidence["sta_tail_failure_hint"] and not diagnostic_status:
        diagnostic_status = "失败"
        diagnostic_detail = str(sta_info.get("reason", "") or "STA 尾部包含失败提示")
    evidence["diagnostic_status"] = diagnostic_status
    evidence["diagnostic_detail"] = diagnostic_detail
    evidence["diagnostic_failed"] = diagnostic_status in {"失败", "终止"}
    hang_probe_log(
        "collect_runtime_evidence",
        time.monotonic() - probe_start,
        threshold=0.2,
        job_name=job_name,
    )
    return evidence


def update_file_stability(run: dict, evidence: dict, now: float | None = None) -> None:
    current_time = time.monotonic() if now is None else now
    signature = evidence.get("sta_signature")
    previous_signature = run.get("sta_signature")

    if evidence.get("sta_valid") and signature is not None:
        if previous_signature == signature:
            run["sta_stable_polls"] = int(run.get("sta_stable_polls", 0)) + 1
        else:
            run["sta_signature"] = signature
            run["sta_stable_polls"] = 0
            run["finish_candidate_since"] = None
    else:
        run["sta_signature"] = None
        run["sta_stable_polls"] = 0
        run["finish_candidate_since"] = None

    if run.get("datacheck_only"):
        if evidence.get("lck_exists") or evidence.get("solver_pid_active"):
            run["datacheck_stable_polls"] = 0
        else:
            run["datacheck_stable_polls"] = int(run.get("datacheck_stable_polls", 0)) + 1

    if evidence.get("lck_exists") or evidence.get("solver_pid_active"):
        run["finish_candidate_since"] = None

    if (
        evidence.get("log_pre_started")
        or evidence.get("log_pre_finished")
        or evidence.get("log_solver_started")
        or evidence.get("sta_changed")
        or evidence.get("lck_exists")
        or evidence.get("solver_pid_active")
    ):
        run["last_runtime_activity_at"] = current_time


def update_runtime_phase(run: dict, evidence: dict, now: float | None = None) -> None:
    current_time = time.monotonic() if now is None else now
    if (
        evidence.get("log_pre_started")
        or evidence.get("log_pre_finished")
        or evidence.get("lck_exists")
    ):
        run["activity_seen"] = True
        if not run.get("solver_started"):
            run["runtime_phase"] = "PREPROCESSING"

    solver_started = (
        bool(evidence.get("log_solver_started"))
        or bool(evidence.get("sta_valid"))
        or (
            bool(evidence.get("solver_pid_active"))
            and evidence.get("solver_pid_confidence") == "high"
        )
    )
    if solver_started:
        run["activity_seen"] = True
        run["solver_started"] = True
        run["runtime_phase"] = "SOLVING"
        solver_kind = str(evidence.get("solver_kind", "") or "")
        if solver_kind:
            run["solver_kind"] = solver_kind

    if evidence.get("sta_valid"):
        run["seen_sta"] = True
    run["sta_valid"] = bool(evidence.get("sta_valid"))

    if evidence.get("diagnostic_status"):
        run["runtime_diagnostic_status"] = evidence["diagnostic_status"]
        run["runtime_diagnostic_detail"] = evidence.get("diagnostic_detail", "")

    launcher_finished_at = run.get("launcher_finished_monotonic")
    if (
        run.get("launcher_finished")
        and run.get("activity_seen")
        and not run.get("solver_started")
        and not evidence.get("sta_valid")
        and launcher_finished_at is not None
    ):
        try:
            grace_elapsed = current_time - float(launcher_finished_at) >= SOLVER_START_GRACE_SECONDS
        except (TypeError, ValueError):
            grace_elapsed = False
        if grace_elapsed:
            run["runtime_phase"] = "UNKNOWN"
            run["solver_start_timeout"] = True
            run["solver_start_timeout_detail"] = (
                f"预处理阶段后未在 {SOLVER_START_GRACE_SECONDS} 秒内进入后台求解"
            )


def runtime_completion_ready(run: dict, evidence: dict, now: float | None = None) -> bool:
    with hang_probe("runtime_completion_ready", job_name=run.get("job_name")):
        current_time = time.monotonic() if now is None else now
        diagnostic_status = evidence.get("diagnostic_status") or run.get("runtime_diagnostic_status")
        if (
            diagnostic_status in {"失败", "终止"}
            or evidence.get("diagnostic_failed")
            or evidence.get("sta_tail_failure_hint")
            or run.get("console_failed")
        ):
            run["finish_candidate_since"] = None
            return False

        eligible = (
            bool(run.get("solver_started"))
            and bool(run.get("seen_sta"))
            and bool(evidence.get("sta_valid"))
            and not evidence.get("lck_exists")
            and not evidence.get("solver_pid_active")
            and int(run.get("sta_stable_polls", 0)) >= STA_STABLE_POLLS_REQUIRED
        )
        if not eligible:
            run["finish_candidate_since"] = None
            return False

        run["runtime_phase"] = "FINISH_CANDIDATE"
        candidate_since = run.get("finish_candidate_since")
        if candidate_since is None:
            run["finish_candidate_since"] = current_time
            return False
        try:
            return current_time - float(candidate_since) >= FINISH_CANDIDATE_QUIET_SECONDS
        except (TypeError, ValueError):
            run["finish_candidate_since"] = current_time
            return False


def runtime_termination_ready(
    run: dict,
    evidence: dict,
    now: float | None = None,
) -> bool:
    if evidence.get("lck_exists") or evidence.get("solver_pid_active"):
        run["termination_stable_polls"] = 0
        run["termination_candidate_since"] = None
        return False

    current_time = time.monotonic() if now is None else now
    candidate_since = run.get("termination_candidate_since")
    if candidate_since is None:
        candidate_since = current_time
        run["termination_candidate_since"] = current_time

    run["termination_stable_polls"] = int(run.get("termination_stable_polls", 0)) + 1
    return (
        int(run["termination_stable_polls"]) >= TERMINATION_STABLE_POLLS_REQUIRED
        and current_time - float(candidate_since) >= TERMINATION_STABLE_SECONDS
    )


def runtime_orphaned_after_external_stop_ready(
    run: dict,
    evidence: dict,
    now: float | None = None,
) -> bool:
    """Return True when an already-started job lost every runtime signal.

    This covers jobs terminated outside the app, or directories manually cleaned
    after termination. It is deliberately separate from normal completion:
    missing STA evidence can only become an interrupted/terminated outcome, not
    a successful completion.
    """
    if run.get("terminating"):
        return False
    if not run.get("launcher_finished"):
        return False
    if not (run.get("solver_started") or run.get("seen_sta")):
        return False
    if not run.get("process_snapshot_available"):
        run["orphaned_runtime_stable_polls"] = 0
        run["orphaned_runtime_candidate_since"] = None
        return False
    if evidence.get("lck_exists") or evidence.get("solver_pid_active") or evidence.get("sta_valid"):
        run["orphaned_runtime_stable_polls"] = 0
        run["orphaned_runtime_candidate_since"] = None
        return False
    if evidence.get("log_delta") or evidence.get("sta_delta"):
        run["orphaned_runtime_stable_polls"] = 0
        run["orphaned_runtime_candidate_since"] = None
        return False

    current_time = time.monotonic() if now is None else now
    candidate_since = run.get("orphaned_runtime_candidate_since")
    if candidate_since is None:
        candidate_since = current_time
        run["orphaned_runtime_candidate_since"] = current_time

    run["orphaned_runtime_stable_polls"] = int(run.get("orphaned_runtime_stable_polls", 0)) + 1
    return (
        int(run["orphaned_runtime_stable_polls"]) >= ORPHANED_RUNTIME_STABLE_POLLS_REQUIRED
        and current_time - float(candidate_since) >= ORPHANED_RUNTIME_STABLE_SECONDS
    )


__all__ = [
    "build_sta_signature",
    "collect_runtime_evidence",
    "read_log_delta",
    "read_sta_delta",
    "runtime_completion_ready",
    "runtime_orphaned_after_external_stop_ready",
    "runtime_termination_ready",
    "update_file_stability",
    "update_runtime_phase",
]
