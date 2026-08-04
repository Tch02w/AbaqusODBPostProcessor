"""Manual smoke tests for the UI-independent memory monitor module."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from abaqus_submitter.memory_monitor import (  # noqa: E402
    JobMemoryUsage,
    MemoryMonitorService,
    format_memory_size,
)


GB = 1024**3
MB = 1024**2


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def main() -> None:
    assert_equal(format_memory_size(0), "未统计", "zero memory")
    assert_equal(format_memory_size(512 * MB), "512 MB", "MB formatting")
    assert_equal(format_memory_size(2 * GB), "2.0 GB", "GB formatting")

    service = MemoryMonitorService(
        learning_interval_ms=1000,
        patrol_interval_ms=5000,
        max_samples=8,
        min_samples=2,
        stable_polls=2,
        stable_relative_delta=0.08,
        safety_factor=1.10,
        unlimited_job_slots=999999,
    )

    state = service.register_job(job_key="GJB::JobA", job_name="JobA", work_dir="G:/test")
    assert_equal(state.monitor_active, False, "registered inactive")
    service.activate_job("GJB::JobA", now=10.0)
    assert_equal(len(service.get_due_jobs(now=10.0)), 1, "due after activation")
    assert_equal(service.get_next_delay_ms(now=10.0), 1000, "due delay")

    samples = [2.0, 2.4, 2.5, 2.5, 2.5]
    events = []
    for index, sample_gb in enumerate(samples):
        events.extend(
            service.apply_usage_snapshot(
                due_job_keys=["GJB::JobA"],
                usage_by_job={
                    "JobA": JobMemoryUsage(
                        job_name="JobA",
                        private_memory=int(sample_gb * GB),
                        process_count=2,
                        process_names=("standard.exe", "SMASolver.exe"),
                    )
                },
                step_by_job_key={"GJB::JobA": "Step-1" if index < 2 else "Step-2"},
                now=10.0 + index,
            )
        )
        service.activate_job("GJB::JobA", now=10.0 + index + 0.1)

    estimate = service.job_estimates["JobA"]
    assert_equal(estimate.peak_memory, int(2.5 * GB), "peak memory")
    assert estimate.estimated_memory >= int(2.5 * GB * 1.10)
    assert_equal(estimate.sample_count, 5, "sample count")
    assert_equal(estimate.step_peaks["Step-1"], int(2.4 * GB), "Step-1 peak")
    assert_equal(estimate.step_peaks["Step-2"], int(2.5 * GB), "Step-2 peak")
    assert state.memory_stable_polls >= 2
    assert_equal(state.monitor_mode, "patrol", "learning switches to patrol")
    assert any(event["event_type"] == "memory_estimate_stable" for event in events)

    external = service.update_external_job_estimate(
        job_name="ExternalJob",
        rss_bytes=8 * GB,
        process_count=3,
        process_names="external",
    )
    assert external is not None
    assert_equal(external.peak_memory, 8 * GB, "external peak")
    assert external.estimated_memory >= int(8 * GB * 1.10)

    slots = service.estimate_available_slots(
        available_memory=42 * GB,
        usage_by_job={
            "RunningA": JobMemoryUsage(job_name="RunningA", private_memory=6 * GB),
        },
        active_job_names={"RunningA"},
    )
    assert slots.slots >= 1
    assert slots.memory_limited is True
    assert slots.per_job_memory >= int(8 * GB * 1.10)

    empty_service = MemoryMonitorService(unlimited_job_slots=12345)
    unlimited = empty_service.estimate_available_slots(available_memory=42 * GB)
    assert_equal(unlimited.slots, 12345, "unlimited slots without samples")
    assert_equal(unlimited.memory_limited, False, "not memory limited without samples")

    service.finalize_job("GJB::JobA")
    assert_equal(service.tracking_states["GJB::JobA"].monitor_active, False, "finalize deactivates")

    print("manual_test_memory_monitor: OK")
    print(f"events={len(events)}")
    print(
        f"JobA peak={format_memory_size(estimate.peak_memory)} estimated={format_memory_size(estimate.estimated_memory)}"
    )
    print(
        f"ExternalJob peak={format_memory_size(external.peak_memory)} estimated={format_memory_size(external.estimated_memory)}"
    )
    print(f"slots={slots.slots} per_job={format_memory_size(slots.per_job_memory)}")


if __name__ == "__main__":
    main()
