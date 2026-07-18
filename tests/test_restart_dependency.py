import tempfile
import unittest
from pathlib import Path

from abaqus_submitter_qt.command import SubmitOptions
from abaqus_submitter_qt.constants import STATUS_COMPLETED, STATUS_RUNNING
from abaqus_submitter_qt.models import QueueItem
from abaqus_submitter_qt.queue_scheduler import managed_job_key
from abaqus_submitter_qt.restart_dependency import RestartDependencyLifecycle


class RestartDependencyLifecycleTests(unittest.TestCase):
    def test_resolve_covers_reference_and_archive_reservation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            oldjob_dir = Path(temp_dir) / "old"
            oldjob_dir.mkdir()
            (oldjob_dir / "base.odb").write_bytes(b"odb")
            dependency = QueueItem(
                job_name="base",
                status=STATUS_COMPLETED,
                archive_destination=str(oldjob_dir),
            )
            current = QueueItem(job_name="restart", run_mode="restart", oldjob_name="base")
            reserved: set[tuple[str, str]] = set()
            lifecycle = RestartDependencyLifecycle([dependency, current], {}, reserved)
            options = SubmitOptions(inp_file=str(Path(temp_dir) / "restart.inp"), job_name="restart")

            resolution = lifecycle.resolve(options, current)

            self.assertTrue(resolution.ready)
            self.assertEqual(resolution.source_dir, str(oldjob_dir))
            self.assertEqual(resolution.reference.oldjob_arg, str(oldjob_dir / "base"))

            reserved.add(managed_job_key(str(oldjob_dir), "base"))
            blocked = lifecycle.resolve(options, current)
            self.assertFalse(blocked.ready)
            self.assertIn("正在归档", blocked.message)

    def test_missing_dependency_has_single_canonical_message(self):
        item = QueueItem(job_name="restart", run_mode="restart")
        lifecycle = RestartDependencyLifecycle([item], {}, set())

        resolution = lifecycle.resolve(SubmitOptions(job_name="restart"), item)

        self.assertFalse(resolution.ready)
        self.assertEqual(resolution.message, "未选择有效的 Restart 前置作业")

    def test_reference_record_and_clear_are_symmetric(self):
        item = QueueItem(job_name="restart")
        workspace = {
            "resolved_oldjob_arg": "D:/old/base",
            "resolved_oldjob_source": "archive",
            "resolved_oldjob_dir": "D:/old",
            "resolved_oldjob_reference_key": "d:/old::base",
        }

        RestartDependencyLifecycle.record_queue_item(item, workspace)
        self.assertEqual(item.resolved_oldjob_source, "archive")
        RestartDependencyLifecycle.clear_queue_item(item)
        self.assertEqual(item.resolved_oldjob_arg, "")
        self.assertEqual(item.resolved_oldjob_reference_key, "")

    def test_active_restart_dependent_blocks_archive(self):
        run = {"job_name": "base", "work_dir": "D:/jobs/base"}
        dependent = QueueItem(
            job_name="restart",
            oldjob_name="base",
            status=STATUS_RUNNING,
        )

        blockers = RestartDependencyLifecycle.archive_blockers(run, [dependent])

        self.assertEqual(blockers, [dependent])


if __name__ == "__main__":
    unittest.main()
