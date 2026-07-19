import unittest
from types import SimpleNamespace

from abaqus_submitter.job_controller import JobController


class JobOrchestrationInterfaceTests(unittest.TestCase):
    def test_finish_callbacks_forward_all_arguments_explicitly(self):
        calls = []
        host = SimpleNamespace(
            inspect_finished_job=lambda job_key: ("完成", job_key),
            notify_job_finished=lambda run, status, detail="": calls.append((run, status, detail)),
        )
        controller = JobController(host)
        run = {"job_name": "demo"}

        self.assertEqual(controller.inspect_finished_job("run-key"), ("完成", "run-key"))
        controller.notify_job_finished(run, "完成", "计算完成")

        self.assertEqual(calls, [(run, "完成", "计算完成")])

    def test_controller_has_no_reflective_forwarding_magic(self):
        self.assertNotIn("__getattr__", JobController.__dict__)
        self.assertNotIn("__setattr__", JobController.__dict__)


if __name__ == "__main__":
    unittest.main()
