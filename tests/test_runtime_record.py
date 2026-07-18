import unittest

from abaqus_submitter_qt.command import SubmitOptions
from abaqus_submitter_qt.models import QueueItem
from abaqus_submitter_qt.runtime_record import RuntimeRecord


class RuntimeRecordTests(unittest.TestCase):
    def test_internal_factory_establishes_common_invariants(self):
        options = SubmitOptions(
            inp_file=r"D:\jobs\demo.inp",
            job_name="demo",
            datacheck=True,
        )

        run = RuntimeRecord.for_internal(
            key="d:/jobs::demo",
            options=options,
            command="abaqus job=demo",
            queue_item=None,
            workspace_info={"source_inp_path": options.inp_file},
            existing_result_info={"action": "backup", "odb": "old.odb", "sta": "old.sta"},
            diagnostic_baseline={"demo.sta": (1, 2)},
            monotonic_now=12.5,
            submitted_at=100.0,
        )

        self.assertIsInstance(run, dict)
        self.assertEqual(run["runtime_phase"], "STARTING")
        self.assertEqual(run["runtime_started_monotonic"], 12.5)
        self.assertEqual(run["last_runtime_activity_at"], 12.5)
        self.assertEqual(run["backup_odb_path"], "old.odb")
        self.assertTrue(run["datacheck_only"])
        RuntimeRecord.validate(run)

    def test_attached_factory_establishes_external_monitor_invariants(self):
        item = QueueItem(
            inp_path=r"D:\jobs\external.inp",
            job_name="external",
            is_external=True,
            external_work_dir=r"D:\jobs",
        )

        run = RuntimeRecord.for_attached(
            key="d:/jobs::external",
            item=item,
            work_dir=r"D:\jobs",
            source_label="external",
            solver_pids=(12, 34),
            solver_kind="standard",
            rss_bytes=4096,
            submitted_at=90.0,
            monotonic_now=15.0,
            wall_now=101.0,
        )

        self.assertEqual(run["runtime_phase"], "SOLVING")
        self.assertEqual(run["known_solver_pids"], (12, 34))
        self.assertEqual(run["solver_pid_confidence"], "high")
        self.assertTrue(run["standard_started"])
        self.assertEqual(run["memory_peak"], 4096)
        RuntimeRecord.validate(run)

    def test_monitor_preparation_preserves_existing_evidence(self):
        process = object()
        timer = object()
        run = {"activity_seen": True, "runtime_started_monotonic": 8.0}

        RuntimeRecord.prepare_internal_monitor(
            run,
            process=process,
            timer=timer,
            monotonic_now=20.0,
        )

        self.assertIs(run["process"], process)
        self.assertIs(run["timer"], timer)
        self.assertTrue(run["activity_seen"])
        self.assertEqual(run["runtime_started_monotonic"], 8.0)
        self.assertEqual(run["last_runtime_activity_at"], 20.0)

    def test_memory_update_changes_projection_as_one_operation(self):
        run = {
            "memory_current": 1,
            "memory_peak": 2,
            "memory_estimated": 3,
            "memory_monitor_mode": "learning",
            "memory_monitor_stable": False,
        }

        RuntimeRecord.update_memory(
            run,
            current=10,
            peak=20,
            estimated=30,
            mode="external",
            stable=True,
        )

        self.assertEqual(
            (
                run["memory_current"],
                run["memory_peak"],
                run["memory_estimated"],
                run["memory_monitor_mode"],
                run["memory_monitor_stable"],
            ),
            (10, 20, 30, "external", True),
        )


if __name__ == "__main__":
    unittest.main()
