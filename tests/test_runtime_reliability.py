from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from abaqus_submitter import job_runtime, runtime_evidence
from abaqus_submitter.abaqus_diagnostics import inspect_sta_structure
from abaqus_submitter.constants import (
    STATUS_DATACHECK_COMPLETED,
    STATUS_DATACHECK_FAILED,
)
from abaqus_submitter.job_controller import (
    FinalizationInput,
    resolve_finalization_status,
)
from abaqus_submitter.main import MainWindow
from abaqus_submitter.process_scanner import get_runtime_process_evidence
from abaqus_submitter.runtime_evidence import (
    collect_runtime_evidence,
    runtime_completion_ready,
    runtime_termination_ready,
    update_file_stability,
    update_runtime_phase,
)


STA_HEADER = " STEP INC ATT SEVERE EQUIL TOTAL TOTAL_TIME STEP_TIME INC_TIME\n"
STA_ROW = " 1 1 1 0 0 1 0.100 0.100 0.100\n"


def base_run(work_dir: str, *, job_name: str = "Job_A") -> dict:
    return {
        "work_dir": work_dir,
        "job_name": job_name,
        "diagnostic_baseline": {},
        "submitted_at": time.time() - 1,
        "runtime_phase_text_pending": "",
        "log_position": 0,
        "sta_position": 0,
        "msg_position": 0,
        "dat_position": 0,
        "sta_signature": None,
        "sta_stable_polls": 0,
        "finish_candidate_since": None,
        "solver_started": False,
        "seen_sta": False,
        "sta_valid": False,
        "activity_seen": False,
        "runtime_phase": "STARTING",
    }


def inactive_process_evidence(*_args, **_kwargs) -> dict:
    return {
        "active": False,
        "confidence": "",
        "pids": (),
        "solver_kind": "",
    }


class FakeTimer:
    def __init__(self) -> None:
        self.stopped = False
        self.deleted = False

    def stop(self) -> None:
        self.stopped = True

    def deleteLater(self) -> None:
        self.deleted = True


class FakeSignal:
    def __init__(self) -> None:
        self.disconnected = False
        self.connected = []

    def disconnect(self) -> None:
        self.disconnected = True

    def connect(self, callback) -> None:
        self.connected.append(callback)


class FakeProcess:
    def __init__(self, *, running: bool) -> None:
        self.running = running
        self.deleted = False
        self.readyReadStandardOutput = FakeSignal()
        self.finished = FakeSignal()
        self.errorOccurred = FakeSignal()

    def state(self):
        if self.running:
            return job_runtime.QtCore.QProcess.ProcessState.Running
        return job_runtime.QtCore.QProcess.ProcessState.NotRunning

    def deleteLater(self) -> None:
        self.deleted = True


class FakeMemoryAdapter:
    def activate_job(self, _job_key: str) -> None:
        pass


class Stage7RuntimeReliabilityTests(unittest.TestCase):
    def inspect(self, content: str, *, baseline=None) -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Job_A.sta"
            path.write_text(content, encoding="utf-8")
            return inspect_sta_structure(
                path,
                baseline=baseline,
                submitted_after=time.time() - 1,
            )

    def collect(self, content: str, *, baseline=None) -> tuple[dict, dict]:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Job_A.sta"
            path.write_text(content, encoding="utf-8")
            run = base_run(temp_dir)
            if baseline is not None:
                run["diagnostic_baseline"][".sta"] = baseline
            with mock.patch.object(
                runtime_evidence,
                "get_runtime_process_evidence",
                side_effect=inactive_process_evidence,
            ):
                evidence = collect_runtime_evidence(run)
            return run, evidence

    def test_missing_sta_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run = base_run(temp_dir)
            with mock.patch.object(
                runtime_evidence,
                "get_runtime_process_evidence",
                side_effect=inactive_process_evidence,
            ):
                evidence = collect_runtime_evidence(run)
        self.assertFalse(evidence["sta_exists"])
        self.assertFalse(evidence["sta_valid"])

    def test_unchanged_old_sta_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Job_A.sta"
            path.write_text(STA_HEADER + STA_ROW, encoding="utf-8")
            stat_result = path.stat()
            baseline = (stat_result.st_mtime_ns, stat_result.st_size)
            run = base_run(temp_dir)
            run["diagnostic_baseline"][".sta"] = baseline
            with mock.patch.object(
                runtime_evidence,
                "get_runtime_process_evidence",
                side_effect=inactive_process_evidence,
            ):
                evidence = collect_runtime_evidence(run)
            update_runtime_phase(run, evidence, now=1.0)
        self.assertFalse(evidence["sta_is_current_run"])
        self.assertFalse(evidence["sta_valid"])
        self.assertFalse(run["seen_sta"])

    def test_nonempty_unstructured_sta_is_invalid(self) -> None:
        info = self.inspect("nonempty but not an Abaqus status file\n")
        self.assertTrue(info["is_current_run"])
        self.assertFalse(info["has_header"])
        self.assertFalse(info["has_progress_rows"])
        self.assertFalse(info["parse_ok"])

    def test_header_without_rows_is_invalid(self) -> None:
        _run, evidence = self.collect(STA_HEADER)
        self.assertTrue(evidence["sta_has_header"])
        self.assertFalse(evidence["sta_has_progress_rows"])
        self.assertFalse(evidence["sta_valid"])

    def test_increment_row_makes_current_sta_valid(self) -> None:
        run, evidence = self.collect(STA_HEADER + STA_ROW)
        update_runtime_phase(run, evidence, now=1.0)
        self.assertTrue(evidence["sta_valid"])
        self.assertEqual(evidence["sta_last_step"], 1)
        self.assertEqual(evidence["sta_last_increment"], 1)
        self.assertTrue(run["seen_sta"])
        self.assertTrue(run["sta_valid"])

    def test_increment_row_without_header_is_still_parseable(self) -> None:
        _run, evidence = self.collect(STA_ROW)
        self.assertFalse(evidence["sta_has_header"])
        self.assertTrue(evidence["sta_has_progress_rows"])
        self.assertTrue(evidence["sta_parse_ok"])
        self.assertTrue(evidence["sta_valid"])

    def test_sta_failure_tail_is_invalid(self) -> None:
        _run, evidence = self.collect(
            STA_HEADER
            + STA_ROW
            + "THE ANALYSIS HAS NOT BEEN COMPLETED\n"
        )
        self.assertTrue(evidence["sta_tail_failure_hint"])
        self.assertFalse(evidence["sta_valid"])
        self.assertFalse(
            runtime_completion_ready(
                {
                    "solver_started": True,
                    "seen_sta": True,
                    "sta_stable_polls": 10,
                    "finish_candidate_since": 0.0,
                },
                evidence,
                now=20.0,
            )
        )

    def test_plain_error_line_is_a_failure_hint(self) -> None:
        _run, evidence = self.collect(STA_HEADER + STA_ROW + "ERROR: analysis stopped\n")
        self.assertTrue(evidence["sta_tail_failure_hint"])
        self.assertFalse(evidence["sta_valid"])

    def test_invalid_nonempty_sta_cannot_complete(self) -> None:
        _run, evidence = self.collect("nonsense but nonempty\n")
        self.assertFalse(
            runtime_completion_ready(
                {
                    "solver_started": True,
                    "seen_sta": False,
                    "sta_valid": False,
                    "sta_stable_polls": 20,
                    "finish_candidate_since": 0.0,
                },
                evidence,
                now=100.0,
            )
        )

    def test_valid_stable_sta_can_complete(self) -> None:
        run = {
            "solver_started": True,
            "seen_sta": True,
            "sta_valid": True,
            "sta_signature": None,
            "sta_stable_polls": 0,
            "finish_candidate_since": None,
        }
        evidence = {
            "sta_valid": True,
            "sta_signature": (100, 1000),
            "sta_changed": False,
            "sta_tail_failure_hint": False,
            "lck_exists": False,
            "solver_pid_active": False,
            "diagnostic_status": "",
        }
        for now in (0.0, 5.0, 10.0, 15.0):
            update_file_stability(run, evidence, now=now)
            self.assertFalse(runtime_completion_ready(run, evidence, now=now))
        self.assertFalse(runtime_completion_ready(run, evidence, now=20.0))
        self.assertTrue(runtime_completion_ready(run, evidence, now=25.0))

    def test_datacheck_does_not_require_sta_or_solver(self) -> None:
        completed = resolve_finalization_status(
            FinalizationInput(
                is_datacheck=True,
                terminating=False,
                launcher_exit_code=0,
                console_failed=False,
                console_failed_detail="",
                diagnostic_status="",
                diagnostic_detail="",
            )
        )
        failed = resolve_finalization_status(
            FinalizationInput(
                is_datacheck=True,
                terminating=False,
                launcher_exit_code=0,
                console_failed=True,
                console_failed_detail="input error",
                diagnostic_status="",
                diagnostic_detail="",
            )
        )
        self.assertEqual(completed.status, STATUS_DATACHECK_COMPLETED)
        self.assertEqual(failed.status, STATUS_DATACHECK_FAILED)

    def test_datacheck_runtime_finishes_without_sta(self) -> None:
        controller = job_runtime.JobRuntimeController(memory_adapter=FakeMemoryAdapter())
        emitted = []
        controller.jobFinished.connect(emitted.append)
        run = {
            "job_name": "Check_A",
            "datacheck_only": True,
            "queue_item": None,
            "launcher_finished": True,
            "launcher_exit_code": 0,
            "finish_emitted": False,
            "finalized": False,
            "sta_signature": None,
            "sta_stable_polls": 0,
            "finish_candidate_since": None,
            "datacheck_stable_polls": 0,
            "termination_stable_polls": 0,
            "termination_candidate_since": None,
            "activity_seen": False,
            "solver_started": False,
            "runtime_phase": "STARTING",
        }
        controller.register_run("Check_A", run)
        evidence = {
            "sta_valid": False,
            "sta_signature": None,
            "sta_changed": False,
            "sta_delta": "",
            "sta_tail_failure_hint": False,
            "lck_exists": False,
            "solver_pid_active": False,
            "diagnostic_status": "",
            "diagnostic_detail": "",
            "log_pre_started": False,
            "log_pre_finished": False,
            "log_solver_started": False,
            "solver_pid_confidence": "",
            "solver_kind": "",
        }
        with mock.patch.object(job_runtime, "collect_runtime_evidence", return_value=evidence):
            controller.poll_sta_file("Check_A")
            controller.poll_sta_file("Check_A")
            controller.poll_sta_file("Check_A")
            controller.poll_sta_file("Check_A")
        self.assertEqual(emitted, ["Check_A"])

    def test_completion_clock_uses_monotonic_time(self) -> None:
        run = {
            "solver_started": True,
            "seen_sta": True,
            "sta_valid": True,
            "sta_stable_polls": 3,
            "finish_candidate_since": None,
        }
        evidence = {
            "sta_valid": True,
            "sta_tail_failure_hint": False,
            "lck_exists": False,
            "solver_pid_active": False,
            "diagnostic_status": "",
        }
        with mock.patch.object(runtime_evidence.time, "monotonic", side_effect=(100.0, 105.0, 111.0)), mock.patch.object(
            runtime_evidence.time,
            "time",
            side_effect=(1000.0, 999999.0, -1000.0),
        ):
            self.assertFalse(runtime_completion_ready(run, evidence))
            self.assertFalse(runtime_completion_ready(run, evidence))
            self.assertTrue(runtime_completion_ready(run, evidence))

    def test_solver_grace_uses_monotonic_time(self) -> None:
        run = {"launcher_finished_monotonic": 100.0}
        with mock.patch.object(job_runtime.time, "monotonic", return_value=161.0), mock.patch.object(
            job_runtime.time,
            "time",
            return_value=-100000.0,
        ):
            self.assertTrue(job_runtime.solver_start_grace_elapsed(run))

    def test_termination_wait_uses_monotonic_time(self) -> None:
        run = {
            "termination_stable_polls": 0,
            "termination_candidate_since": None,
        }
        evidence = {
            "lck_exists": False,
            "solver_pid_active": False,
        }
        with mock.patch.object(
            runtime_evidence.time,
            "monotonic",
            side_effect=(100.0, 105.0, 110.0),
        ), mock.patch.object(
            runtime_evidence.time,
            "time",
            side_effect=(1000.0, -50000.0, 999999.0),
        ):
            self.assertFalse(runtime_termination_ready(run, evidence))
            self.assertFalse(runtime_termination_ready(run, evidence))
            self.assertTrue(runtime_termination_ready(run, evidence))

    def test_unregister_releases_stopped_timer_and_finished_process(self) -> None:
        controller = job_runtime.JobRuntimeController(memory_adapter=FakeMemoryAdapter())
        timer = FakeTimer()
        process = FakeProcess(running=False)
        run = {"timer": timer, "process": process}
        controller.register_run("Job_A", run)
        controller.unregister_run("Job_A")
        self.assertNotIn("Job_A", controller.runs)
        self.assertTrue(timer.stopped)
        self.assertTrue(timer.deleted)
        self.assertTrue(process.deleted)
        self.assertIsNone(run["timer"])
        self.assertIsNone(run["process"])

    def test_unregister_does_not_delete_running_process(self) -> None:
        controller = job_runtime.JobRuntimeController(memory_adapter=FakeMemoryAdapter())
        process = FakeProcess(running=True)
        run = {"timer": FakeTimer(), "process": process}
        controller.register_run("Job_A", run)
        controller.unregister_run("Job_A")
        self.assertFalse(process.deleted)
        self.assertEqual(process.finished.connected, [process.deleteLater])

    def test_finish_signal_is_emitted_once(self) -> None:
        controller = job_runtime.JobRuntimeController(memory_adapter=FakeMemoryAdapter())
        emitted = []
        controller.jobFinished.connect(emitted.append)
        run = {"finish_emitted": False, "finalized": False}
        controller.emit_job_finished_once("Job_A", run, reason="confirmed")
        controller.emit_job_finished_once("Job_A", run, reason="confirmed")
        self.assertEqual(emitted, ["Job_A"])

    def test_runtime_timer_uses_interval_constant(self) -> None:
        source = Path(job_runtime.__file__).read_text(encoding="utf-8")
        self.assertIn("timer.setInterval(STA_POLL_INTERVAL_MS)", source)
        self.assertNotIn("timer.setInterval(5000)", source)

    def test_shared_process_index_avoids_rescanning_snapshot(self) -> None:
        class NonIterableRows(list):
            def __iter__(self):
                raise AssertionError("shared process rows should not be rescanned")

        evidence = get_runtime_process_evidence(
            "G:/jobs",
            "Job_A",
            process_rows=NonIterableRows(),
            process_by_pid={},
            solver_rows=[],
        )
        self.assertFalse(evidence["active"])

    def test_runtime_update_uses_incremental_queue_refresh(self) -> None:
        refreshed = []
        host = SimpleNamespace(
            run_records={"run1": {"queue_item": SimpleNamespace(item_id="item1")}},
            selected_job_key=lambda: "",
            refresh_selected_run_status=lambda _job_key: None,
            refresh_visible_queue_manager=refreshed.append,
            update_queue_status_label=lambda: None,
        )
        MainWindow.on_runtime_job_updated(host, "run1")
        self.assertEqual(refreshed, [{"item1"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
