import unittest

from abaqus_submitter.constants import STATUS_PENDING_RUN, STATUS_RUNNING, STATUS_WAITING_DEPENDENCY
from abaqus_submitter.models import QueueItem
from abaqus_submitter.scheduler_adapter import (
    build_dependency_job_ids,
    parse_memory_request_bytes,
    queue_item_to_job_specification,
    scheduler_state_from_queue_status,
    reconcile_scheduler_from_queue,
)
from abaqus_submitter.scheduling import JobState, SchedulerCore


class SchedulerAdapterTests(unittest.TestCase):
    def test_queue_item_without_job_id_gets_stable_job_id(self):
        item = QueueItem(item_id="row-1", job_id="", job_name="demo")
        self.assertEqual(item.job_id, "row-1")

    def test_restart_name_is_migrated_to_job_id_dependency(self):
        base = QueueItem(job_id="job-base", job_name="base")
        restart = QueueItem(job_id="job-restart", job_name="restart", oldjob_name="base")
        self.assertEqual(build_dependency_job_ids(restart, [base, restart]), ("job-base",))

    def test_resource_request_reads_common_memory_units(self):
        self.assertEqual(parse_memory_request_bytes("1.5 GB"), int(1.5 * 1024**3))
        self.assertEqual(parse_memory_request_bytes("512MB"), 512 * 1024**2)
        self.assertEqual(parse_memory_request_bytes("80%"), 0)

    def test_queue_item_maps_to_job_specification(self):
        item = QueueItem(
            item_id="item-1",
            job_name="demo",
            inp_path="D:/jobs/demo.inp",
            cores=8,
            memory="4 GB",
            priority=20,
        )
        specification = queue_item_to_job_specification(
            item,
            queue_items=[item],
            submitted_order=3,
        )
        self.assertEqual(specification.job_id, "item-1")
        self.assertEqual(specification.resources.cores, 8)
        self.assertEqual(specification.resources.memory_bytes, 4 * 1024**3)
        self.assertEqual(specification.priority, 20)

    def test_ui_statuses_map_to_scheduler_state(self):
        self.assertEqual(scheduler_state_from_queue_status(STATUS_PENDING_RUN), JobState.PENDING)
        self.assertEqual(scheduler_state_from_queue_status(STATUS_WAITING_DEPENDENCY), JobState.PENDING)
        self.assertEqual(scheduler_state_from_queue_status(STATUS_RUNNING), JobState.RUNNING)

    def test_attached_running_job_receives_an_execution_attempt(self):
        item = QueueItem(job_name="external", status=STATUS_RUNNING, is_external=True)
        scheduler = SchedulerCore()

        reconcile_scheduler_from_queue(scheduler, [item])

        self.assertTrue(item.attempt_id)
        self.assertEqual(scheduler.get(item.job_id).attempt_id, item.attempt_id)

    def test_unchanged_queue_reconciliation_does_not_write_events(self):
        item = QueueItem(job_name="stable", status=STATUS_PENDING_RUN)
        scheduler = SchedulerCore()
        reconcile_scheduler_from_queue(scheduler, [item])
        event_count = len(scheduler.repository.events)
        version = scheduler.get(item.job_id).version

        reconcile_scheduler_from_queue(scheduler, [item])

        self.assertGreater(item.submitted_at, 0)
        self.assertEqual(len(scheduler.repository.events), event_count)
        self.assertEqual(scheduler.get(item.job_id).version, version)


if __name__ == "__main__":
    unittest.main()
