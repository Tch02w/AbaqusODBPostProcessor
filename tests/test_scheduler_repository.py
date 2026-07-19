import tempfile
import unittest
import sqlite3
from pathlib import Path

from abaqus_submitter.scheduler_repository import SQLiteSchedulerRepository
from abaqus_submitter.scheduling import (
    ExecutionEvent,
    ExecutionEventKind,
    JobSpecification,
    JobState,
    ResourceRequest,
    ResourceSnapshot,
    SchedulerCore,
)


class _FailingEventRepository(SQLiteSchedulerRepository):
    def append_event(self, **_kwargs) -> None:
        raise RuntimeError("event write failed")


class SQLiteSchedulerRepositoryTests(unittest.TestCase):
    def test_incompatible_schema_is_preserved_and_recreated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scheduler.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE scheduler_jobs(job_id TEXT PRIMARY KEY)")
            connection.commit()
            connection.close()

            repository = SQLiteSchedulerRepository(path)

            self.assertIn("调度状态库损坏", repository.recovery_message)
            self.assertEqual(repository.load_jobs(), [])
            repository.close()

    def test_snapshot_and_event_are_rolled_back_together(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scheduler.db"
            repository = _FailingEventRepository(path)
            scheduler = SchedulerCore(repository)

            with self.assertRaisesRegex(RuntimeError, "event write failed"):
                scheduler.submit(JobSpecification(job_id="job-1", job_name="job-1", work_dir="D:/jobs"))

            self.assertIsNone(scheduler.get("job-1"))
            repository.close()
            reopened = SQLiteSchedulerRepository(path)
            self.assertEqual(reopened.load_jobs(), [])
            reopened.close()

    def test_corrupt_database_is_preserved_and_recreated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scheduler.db"
            path.write_bytes(b"not a sqlite database")

            repository = SQLiteSchedulerRepository(path)

            self.assertIn("调度状态库损坏", repository.recovery_message)
            self.assertTrue(path.exists())
            self.assertEqual(len(list(Path(temp_dir).glob("scheduler.db.corrupt-*"))), 1)
            self.assertEqual(repository.load_jobs(), [])
            repository.close()

    def test_job_and_event_history_survive_reopen(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scheduler.db"
            repository = SQLiteSchedulerRepository(path)
            scheduler = SchedulerCore(repository)
            scheduler.submit(
                JobSpecification(
                    job_id="job-1",
                    job_name="演示作业",
                    work_dir="D:/jobs/demo",
                    resources=ResourceRequest(cores=4, memory_bytes=1024),
                    dependency_job_ids=("base",),
                    metadata={"source": "测试"},
                )
            )
            scheduler.submit(
                JobSpecification(
                    job_id="base",
                    job_name="base",
                    work_dir="D:/jobs/base",
                ),
                state=JobState.COMPLETED,
            )
            allocation = scheduler.plan(ResourceSnapshot(available_slots=1)).allocations[0]
            scheduler.apply_execution_event(
                ExecutionEvent("job-1", allocation.attempt_id, ExecutionEventKind.STARTED)
            )
            scheduler.close()

            reopened_repository = SQLiteSchedulerRepository(path)
            reopened = SchedulerCore(reopened_repository)

            restored = reopened.get("job-1")
            self.assertEqual(restored.state, JobState.RUNNING)
            self.assertEqual(restored.specification.job_name, "演示作业")
            self.assertEqual(restored.specification.resources.cores, 4)
            events = reopened_repository.load_events(job_id="job-1")
            self.assertEqual(events[-1]["event_type"], "EXECUTION_STARTED")
            reopened.close()


if __name__ == "__main__":
    unittest.main()
