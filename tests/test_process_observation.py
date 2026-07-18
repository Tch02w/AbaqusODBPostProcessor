import unittest

from abaqus_submitter_qt.process_observation import build_process_snapshot
from abaqus_submitter_qt.process_scanner import (
    build_abaqus_job_memory_usage,
    scan_running_abaqus_jobs_by_psutil,
)


class ProcessObservationTests(unittest.TestCase):
    def test_snapshot_builds_pid_and_solver_indexes_once(self):
        rows = [
            {"ProcessId": 10, "Name": "standard.exe", "CommandLine": "standard job=demo"},
            {"ProcessId": 20, "Name": "python.exe", "CommandLine": "worker.py"},
        ]

        snapshot = build_process_snapshot(rows, observed_at=12.0)

        self.assertIs(snapshot.rows[0], rows[0])
        self.assertIs(snapshot.by_pid[10], rows[0])
        self.assertEqual([row["ProcessId"] for row in snapshot.solver_rows], [10])
        self.assertEqual(snapshot.observed_at, 12.0)

    def test_memory_projection_reuses_supplied_rows(self):
        rows = [
            {
                "ProcessId": 10,
                "ParentProcessId": 0,
                "Name": "standard.exe",
                "CommandLine": "standard job=demo",
                "WorkingSetSize": 100,
                "PrivatePageCount": 80,
            }
        ]

        usage = build_abaqus_job_memory_usage(rows)

        self.assertEqual(usage["demo"]["working_set"], 100)
        self.assertEqual(usage["demo"]["private_memory"], 80)

    def test_external_scan_accepts_shared_empty_snapshot(self):
        jobs, skipped = scan_running_abaqus_jobs_by_psutil(
            r"D:\jobs",
            process_rows=[],
        )

        self.assertEqual(jobs, [])
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
