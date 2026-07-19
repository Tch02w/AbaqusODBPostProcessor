import unittest

from abaqus_submitter.scheduling import (
    ExecutionEvent,
    ExecutionEventKind,
    InMemorySchedulerRepository,
    JobSpecification,
    JobState,
    PendingReason,
    ResourceRequest,
    ResourceSnapshot,
    SchedulerCore,
)


def specification(
    job_id: str,
    *,
    priority: int = 0,
    order: int = 0,
    cores: int = 1,
    memory: int = 0,
    dependencies: tuple[str, ...] = (),
    conflict_key: tuple[str, str] = ("", ""),
):
    return JobSpecification(
        job_id=job_id,
        job_name=job_id,
        work_dir=f"D:/jobs/{job_id}",
        resources=ResourceRequest(cores=cores, memory_bytes=memory),
        priority=priority,
        submitted_order=order,
        submitted_at=float(order + 1),
        dependency_job_ids=dependencies,
        conflict_key=conflict_key,
    )


class SchedulerCoreTests(unittest.TestCase):
    def test_orphaned_active_attempt_is_marked_lost_during_recovery(self):
        scheduler = SchedulerCore()
        scheduler.submit(
            JobSpecification(job_id="orphan", job_name="orphan", work_dir="D:/jobs"),
            state=JobState.RUNNING,
            attempt_id="old-attempt",
        )

        recovered = scheduler.recover_orphaned_attempts(set())

        self.assertEqual([snapshot.job_id for snapshot in recovered], ["orphan"])
        self.assertEqual(scheduler.get("orphan").state, JobState.LOST)

    def setUp(self):
        self.repository = InMemorySchedulerRepository()
        self.scheduler = SchedulerCore(self.repository)

    def test_priority_then_submission_order_controls_dispatch(self):
        self.scheduler.submit(specification("normal", priority=0, order=0))
        self.scheduler.submit(specification("urgent-late", priority=10, order=2))
        self.scheduler.submit(specification("urgent-early", priority=10, order=1))

        plan = self.scheduler.plan(ResourceSnapshot(available_slots=2))

        self.assertEqual(
            [allocation.job_id for allocation in plan.allocations],
            ["urgent-early", "urgent-late"],
        )
        self.assertEqual(self.scheduler.get("normal").pending_reason, PendingReason.SLOT_LIMIT)

    def test_dependency_releases_only_after_success(self):
        self.scheduler.submit(specification("base", order=0))
        self.scheduler.submit(specification("restart", order=1, dependencies=("base",)))
        first = self.scheduler.plan(ResourceSnapshot(available_slots=2))
        self.assertEqual([allocation.job_id for allocation in first.allocations], ["base"])
        self.assertEqual(self.scheduler.get("restart").pending_reason, PendingReason.DEPENDENCY)

        base = self.scheduler.get("base")
        self.scheduler.apply_execution_event(
            ExecutionEvent("base", base.attempt_id, ExecutionEventKind.STARTED)
        )
        self.scheduler.apply_execution_event(
            ExecutionEvent("base", base.attempt_id, ExecutionEventKind.SUCCEEDED)
        )
        second = self.scheduler.plan(ResourceSnapshot(available_slots=1))
        self.assertEqual([allocation.job_id for allocation in second.allocations], ["restart"])

    def test_hold_and_release_are_scheduler_commands(self):
        self.scheduler.submit(specification("held"))
        held = self.scheduler.hold("held", True)

        self.assertEqual(held.pending_reason, PendingReason.USER_HOLD)
        self.assertEqual(self.scheduler.plan(ResourceSnapshot(available_slots=1)).allocations, ())

        released = self.scheduler.hold("held", False)
        self.assertEqual(released.pending_reason, PendingReason.NONE)
        self.assertEqual(
            [allocation.job_id for allocation in self.scheduler.plan(ResourceSnapshot(available_slots=1)).allocations],
            ["held"],
        )
        event_types = [event["event_type"] for event in self.repository.events]
        self.assertIn("JOB_HELD", event_types)
        self.assertIn("JOB_RELEASED", event_types)

    def test_failed_job_can_be_requeued_with_attempt_fence_cleared(self):
        self.scheduler.submit(specification("retry"), state=JobState.FAILED, attempt_id="old-attempt")

        snapshot = self.scheduler.requeue("retry")

        self.assertEqual(snapshot.state, JobState.PENDING)
        self.assertEqual(snapshot.attempt_id, "")

    def test_resource_ledger_backfills_smaller_job(self):
        self.scheduler.submit(specification("large", order=0, cores=8, memory=16))
        self.scheduler.submit(specification("small", order=1, cores=2, memory=4))

        plan = self.scheduler.plan(
            ResourceSnapshot(
                available_slots=2,
                available_cores=4,
                available_memory_bytes=8,
            )
        )

        self.assertEqual([allocation.job_id for allocation in plan.allocations], ["small"])
        self.assertEqual(self.scheduler.get("large").pending_reason, PendingReason.CPU_LIMIT)

    def test_active_conflict_prevents_duplicate_workdir_job(self):
        conflict = ("d:/jobs/demo", "demo")
        self.scheduler.submit(specification("first", order=0, conflict_key=conflict))
        self.scheduler.submit(specification("second", order=1, conflict_key=conflict))

        plan = self.scheduler.plan(ResourceSnapshot(available_slots=2))

        self.assertEqual([allocation.job_id for allocation in plan.allocations], ["first"])
        self.assertEqual(self.scheduler.get("second").pending_reason, PendingReason.ACTIVE_CONFLICT)

    def test_stale_attempt_event_is_ignored(self):
        self.scheduler.submit(specification("demo"))
        plan = self.scheduler.plan(ResourceSnapshot(available_slots=1))
        allocation = plan.allocations[0]

        snapshot = self.scheduler.apply_execution_event(
            ExecutionEvent("demo", "old-attempt", ExecutionEventKind.SUCCEEDED)
        )

        self.assertEqual(snapshot.state, JobState.DISPATCHED)
        self.assertEqual(snapshot.attempt_id, allocation.attempt_id)

    def test_repository_is_the_observable_event_history(self):
        self.scheduler.submit(specification("demo"))
        allocation = self.scheduler.plan(ResourceSnapshot(available_slots=1)).allocations[0]
        self.scheduler.apply_execution_event(
            ExecutionEvent("demo", allocation.attempt_id, ExecutionEventKind.STARTED)
        )

        self.assertEqual(self.scheduler.get("demo").state, JobState.RUNNING)
        self.assertIn("EXECUTION_STARTED", [event["event_type"] for event in self.repository.events])


if __name__ == "__main__":
    unittest.main()
