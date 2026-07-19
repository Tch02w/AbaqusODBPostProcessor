"""UI-independent Abaqus memory monitoring and slot estimation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from .constants import (
    JOB_MEMORY_BASE_SAFETY_FACTOR,
    JOB_MEMORY_LEARNING_INTERVAL_MS,
    JOB_MEMORY_MAX_SAMPLES,
    JOB_MEMORY_MIN_SAMPLES,
    JOB_MEMORY_PATROL_INTERVAL_MS,
    JOB_MEMORY_STABLE_POLLS,
    JOB_MEMORY_STABLE_RELATIVE_DELTA,
    UNLIMITED_JOB_SLOTS,
)


@dataclass
class JobMemoryUsage:
    job_name: str
    rss_bytes: int = 0
    working_set: int = 0
    private_memory: int = 0
    process_count: int = 0
    process_names: tuple[str, ...] = ()
    pids: tuple[int, ...] = ()


@dataclass
class JobMemoryEstimate:
    job_name: str
    group: str = ""
    estimated_memory: int = 0
    peak_memory: int = 0
    sample_count: int = 0
    process_count: int = 0
    process_names: str = ""
    updated_at: float = 0.0
    stable: bool = False
    step_peaks: dict[str, int] = field(default_factory=dict)


@dataclass
class JobMemoryTrackingState:
    job_key: str
    job_name: str
    work_dir: str
    finalized: bool = False
    monitor_active: bool = False
    monitor_mode: str = "learning"
    next_sample_at: float = 0.0
    memory_samples: list[int] = field(default_factory=list)
    memory_peak: int = 0
    memory_stable_polls: int = 0


@dataclass
class MemorySlotEstimate:
    slots: int
    memory_slots: int
    memory_limited: bool
    available_memory: int
    usable_memory: int
    current_abaqus_memory: int
    memory_available_for_new_jobs: int
    per_job_memory: int
    memory_sample_count: int
    usage_by_job: dict[str, JobMemoryUsage] = field(default_factory=dict)


def format_memory_size(size_bytes: int) -> str:
    """Format memory size using the same compact style as the GUI."""
    size_bytes = int(size_bytes or 0)
    if size_bytes <= 0:
        return "未统计"
    gib = size_bytes / 1024**3
    if gib >= 1:
        return f"{gib:.1f} GB"
    return f"{size_bytes / 1024**2:.0f} MB"


def infer_model_group(job_name: str) -> str:
    """Best-effort group name; never required for memory estimation."""
    if not job_name:
        return ""
    if "_" in job_name:
        return job_name.split("_", 1)[0]
    return ""


def get_memory_safety_factor(
    *,
    group: str = "",
    base_factor: float = JOB_MEMORY_BASE_SAFETY_FACTOR,
) -> float:
    """Return the default memory safety factor for a job group."""
    return float(base_factor)


class MemoryMonitorService:
    """
    UI-independent Abaqus memory monitoring service.

    It owns samples, learned estimates and slot estimation logic.
    UI layers are responsible for timers, threads and rendering.
    """

    def __init__(
        self,
        *,
        learning_interval_ms: int = JOB_MEMORY_LEARNING_INTERVAL_MS,
        patrol_interval_ms: int = JOB_MEMORY_PATROL_INTERVAL_MS,
        max_samples: int = JOB_MEMORY_MAX_SAMPLES,
        min_samples: int = JOB_MEMORY_MIN_SAMPLES,
        stable_polls: int = JOB_MEMORY_STABLE_POLLS,
        stable_relative_delta: float = JOB_MEMORY_STABLE_RELATIVE_DELTA,
        usable_memory_ratio: float = 0.85,
        safety_factor: float = JOB_MEMORY_BASE_SAFETY_FACTOR,
        unlimited_job_slots: int = UNLIMITED_JOB_SLOTS,
    ) -> None:
        self.learning_interval_ms = int(learning_interval_ms)
        self.patrol_interval_ms = int(patrol_interval_ms)
        self.max_samples = int(max_samples)
        self.min_samples = int(min_samples)
        self.stable_polls = int(stable_polls)
        self.stable_relative_delta = float(stable_relative_delta)
        self.usable_memory_ratio = float(usable_memory_ratio)
        self.safety_factor = float(safety_factor)
        self.unlimited_job_slots = int(unlimited_job_slots)
        self.job_estimates: dict[str, JobMemoryEstimate] = {}
        self.tracking_states: dict[str, JobMemoryTrackingState] = {}

    def register_job(
        self,
        *,
        job_key: str,
        job_name: str,
        work_dir: str,
        monitor_mode: str = "learning",
    ) -> JobMemoryTrackingState:
        state = self.tracking_states.get(job_key)
        if state is not None:
            return state
        state = JobMemoryTrackingState(
            job_key=job_key,
            job_name=job_name,
            work_dir=work_dir,
            monitor_mode=monitor_mode or "learning",
        )
        self.tracking_states[job_key] = state
        return state

    def activate_job(self, job_key: str, *, now: float | None = None) -> None:
        state = self.tracking_states.get(job_key)
        if state is None or state.finalized:
            return
        now = time.monotonic() if now is None else float(now)
        state.monitor_active = True
        state.next_sample_at = now

    def finalize_job(self, job_key: str) -> None:
        state = self.tracking_states.get(job_key)
        if state is None:
            return
        state.finalized = True
        state.monitor_active = False

    def get_due_jobs(self, *, now: float | None = None) -> list[JobMemoryTrackingState]:
        now = time.monotonic() if now is None else float(now)
        return [
            state
            for state in self.tracking_states.values()
            if state.monitor_active and not state.finalized and float(state.next_sample_at or 0) <= now
        ]

    def get_next_delay_ms(
        self,
        *,
        now: float | None = None,
        minimum_delay_ms: int = 1000,
    ) -> int | None:
        now = time.monotonic() if now is None else float(now)
        next_times = [
            float(state.next_sample_at or now)
            for state in self.tracking_states.values()
            if state.monitor_active and not state.finalized
        ]
        if not next_times:
            return None
        next_due = min(next_times)
        if next_due <= now:
            return int(minimum_delay_ms)
        return max(int(minimum_delay_ms), int((next_due - now) * 1000))

    def apply_usage_snapshot(
        self,
        *,
        due_job_keys: list[str],
        usage_by_job: Mapping[str, JobMemoryUsage | Mapping[str, Any]],
        step_by_job_key: Mapping[str, str] | None = None,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        now = time.monotonic() if now is None else float(now)
        step_by_job_key = step_by_job_key or {}
        events: list[dict[str, Any]] = []
        for job_key in due_job_keys:
            state = self.tracking_states.get(job_key)
            if state is None or state.finalized:
                continue

            usage = self._get_usage_for_state(state, usage_by_job)
            if usage is None:
                state.next_sample_at = now + self._interval_seconds(state)
                continue

            sample_event, stable_event = self._apply_usage_to_state(
                state,
                usage,
                now=now,
                step=step_by_job_key.get(job_key) or "unknown",
            )
            if sample_event:
                events.append(sample_event)
            if stable_event:
                events.append(stable_event)
            state.next_sample_at = now + self._interval_seconds(state)
        return events

    def update_external_job_estimate(
        self,
        *,
        job_name: str,
        rss_bytes: int,
        process_count: int = 0,
        process_names: str = "external",
        now: float | None = None,
    ) -> JobMemoryEstimate | None:
        memory = int(rss_bytes or 0)
        if not job_name or memory <= 0:
            return None
        now = time.time() if now is None else float(now)
        estimate = self._estimate_for_job(job_name)
        estimate.peak_memory = max(estimate.peak_memory, memory)
        estimate.estimated_memory = max(
            estimate.estimated_memory,
            int(estimate.peak_memory * self.safety_factor),
        )
        estimate.sample_count += 1
        estimate.process_count = int(process_count or 0)
        estimate.process_names = process_names
        estimate.updated_at = now
        return estimate

    def estimate_per_job_memory(
        self,
        *,
        usage_by_job: Mapping[str, JobMemoryUsage | Mapping[str, Any]] | None = None,
        active_job_names: set[str] | None = None,
    ) -> tuple[int, int]:
        samples: list[int] = []
        active_job_names = active_job_names or set()

        for job_name, estimate in self.job_estimates.items():
            if active_job_names and job_name in active_job_names:
                continue
            memory = int(estimate.estimated_memory or estimate.peak_memory or 0)
            if memory > 0:
                samples.append(memory)
            for step_peak in estimate.step_peaks.values():
                if step_peak > 0:
                    samples.append(int(step_peak * self.safety_factor))

        for job_name, usage in (usage_by_job or {}).items():
            if active_job_names and job_name not in active_job_names:
                continue
            memory = self._usage_memory(self._coerce_usage(job_name, usage))
            if memory > 0:
                samples.append(memory)

        if not samples:
            return 0, 0
        return max(samples), len(samples)

    def estimate_available_slots(
        self,
        *,
        available_memory: int,
        usage_by_job: Mapping[str, JobMemoryUsage | Mapping[str, Any]] | None = None,
        active_job_names: set[str] | None = None,
    ) -> MemorySlotEstimate:
        usage = {job_name: self._coerce_usage(job_name, value) for job_name, value in (usage_by_job or {}).items()}
        current_abaqus_memory = sum(self._usage_memory(value) for value in usage.values())
        usable_memory = int(max(0, int(available_memory or 0)) * self.usable_memory_ratio)
        memory_available_for_new_jobs = max(0, usable_memory - current_abaqus_memory)
        per_job_memory, sample_count = self.estimate_per_job_memory(
            usage_by_job=usage,
            active_job_names=active_job_names,
        )
        if per_job_memory > 0:
            memory_slots = memory_available_for_new_jobs // per_job_memory
            memory_limited = True
        else:
            memory_slots = self.unlimited_job_slots
            memory_limited = False
        return MemorySlotEstimate(
            slots=max(0, int(memory_slots)),
            memory_slots=max(0, int(memory_slots)),
            memory_limited=memory_limited,
            available_memory=int(available_memory or 0),
            usable_memory=usable_memory,
            current_abaqus_memory=current_abaqus_memory,
            memory_available_for_new_jobs=memory_available_for_new_jobs,
            per_job_memory=per_job_memory,
            memory_sample_count=sample_count,
            usage_by_job=usage,
        )

    def _interval_seconds(self, state: JobMemoryTrackingState) -> float:
        interval_ms = self.patrol_interval_ms if state.monitor_mode == "patrol" else self.learning_interval_ms
        return interval_ms / 1000.0

    def _estimate_for_job(self, job_name: str) -> JobMemoryEstimate:
        estimate = self.job_estimates.get(job_name)
        if estimate is None:
            estimate = JobMemoryEstimate(job_name=job_name, group=infer_model_group(job_name))
            self.job_estimates[job_name] = estimate
        return estimate

    def _get_usage_for_state(
        self,
        state: JobMemoryTrackingState,
        usage_by_job: Mapping[str, JobMemoryUsage | Mapping[str, Any]],
    ) -> JobMemoryUsage | None:
        value = usage_by_job.get(state.job_name) or usage_by_job.get(state.job_key)
        if value is None:
            return None
        return self._coerce_usage(state.job_name, value)

    @staticmethod
    def _coerce_usage(job_name: str, value: JobMemoryUsage | Mapping[str, Any]) -> JobMemoryUsage:
        if isinstance(value, JobMemoryUsage):
            return value
        process_names = value.get("process_names", ())
        if isinstance(process_names, str):
            process_names = tuple(part.strip() for part in process_names.split(",") if part.strip())
        return JobMemoryUsage(
            job_name=str(value.get("job_name") or job_name),
            rss_bytes=int(value.get("rss_bytes") or 0),
            working_set=int(value.get("working_set") or 0),
            private_memory=int(value.get("private_memory") or 0),
            process_count=int(value.get("process_count") or 0),
            process_names=tuple(process_names or ()),
            pids=tuple(value.get("pids") or ()),
        )

    @staticmethod
    def _usage_memory(usage: JobMemoryUsage) -> int:
        return int(usage.private_memory or usage.working_set or usage.rss_bytes or 0)

    def _apply_usage_to_state(
        self,
        state: JobMemoryTrackingState,
        usage: JobMemoryUsage,
        *,
        now: float,
        step: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        memory = self._usage_memory(usage)
        if memory <= 0:
            return None, None

        samples = state.memory_samples
        peak_before = state.memory_peak
        peak_after = max(peak_before, memory)
        state.memory_peak = peak_after
        samples.append(memory)
        if len(samples) > self.max_samples:
            del samples[: -self.max_samples]

        estimate = self._estimate_for_job(state.job_name)
        estimate.step_peaks[step] = max(int(estimate.step_peaks.get(step, 0)), memory)
        estimate.peak_memory = peak_after
        estimate.estimated_memory = max(
            estimate.estimated_memory,
            int(peak_after * self.safety_factor),
        )
        estimate.sample_count = len(samples)
        estimate.process_count = usage.process_count
        estimate.process_names = ", ".join(usage.process_names)
        estimate.updated_at = now

        mode_before = state.monitor_mode
        new_peak_ratio = memory / peak_before if peak_before > 0 else 999.0
        if mode_before == "patrol" and peak_before > 0 and new_peak_ratio > 1 + self.stable_relative_delta:
            state.monitor_mode = "learning"
            state.memory_stable_polls = 0
            estimate.stable = False
        elif peak_after > peak_before * (1 + self.stable_relative_delta):
            state.memory_stable_polls = 0
        elif mode_before == "learning" and len(samples) >= self.min_samples:
            state.memory_stable_polls += 1

        stable_event = None
        stable_now = mode_before == "learning" and (
            state.memory_stable_polls >= self.stable_polls or len(samples) >= self.max_samples
        )
        if stable_now:
            estimate.stable = True
            state.monitor_mode = "patrol"
            stable_event = self._memory_event("memory_estimate_stable", state, estimate, memory)

        return self._memory_event("memory_sample_updated", state, estimate, memory), stable_event

    @staticmethod
    def _memory_event(
        event_type: str,
        state: JobMemoryTrackingState,
        estimate: JobMemoryEstimate,
        rss_bytes: int,
    ) -> dict[str, Any]:
        return {
            "event_type": event_type,
            "job_key": state.job_key,
            "job_name": state.job_name,
            "rss_bytes": rss_bytes,
            "peak_memory": state.memory_peak,
            "estimated_memory": estimate.estimated_memory,
            "sample_count": estimate.sample_count,
            "stable": estimate.stable,
            "monitor_mode": state.monitor_mode,
        }


__all__ = [
    "JobMemoryUsage",
    "JobMemoryEstimate",
    "JobMemoryTrackingState",
    "MemorySlotEstimate",
    "MemoryMonitorService",
    "format_memory_size",
    "infer_model_group",
    "get_memory_safety_factor",
]
