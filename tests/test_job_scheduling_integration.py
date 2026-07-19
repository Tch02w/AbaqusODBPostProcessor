import tempfile
import unittest
from pathlib import Path

from abaqus_submitter.constants import STATUS_PENDING_RUN
from abaqus_submitter.job_controller import JobController
from abaqus_submitter.models import QueueItem
from abaqus_submitter.scheduling import SchedulerCore


class _SpinBox:
    def __init__(self, value: int):
        self._value = value

    def value(self) -> int:
        return self._value


class _MemoryMonitor:
    job_estimates = {}


class _SchedulingHost:
    def __init__(self, items: list[QueueItem], slots: int):
        self.queue_items = items
        self.active_runs = {}
        self.run_records = {}
        self.queue_active = True
        self.queue_stop_requested = False
        self.scheduler = SchedulerCore()
        self.memory_monitor_service = _MemoryMonitor()
        self.latest_system_memory = {}
        self.cpus_spin = _SpinBox(4)
        self.max_parallel_spin = _SpinBox(slots)
        self._archive_move_reserved_keys = set()
        self.history_messages = []
        self.save_requests = 0

    def refresh_queue_dependencies(self) -> None:
        return

    def estimate_effective_available_slots(self) -> dict:
        slots = self.max_parallel_spin.value()
        return {
            "manual_limit": slots,
            "managed_active_count": 0,
            "manual_available_slots": slots,
            "memory_available_slots": slots,
            "effective_available_slots": slots,
        }

    def append_history(self, message: str, **_kwargs) -> None:
        self.history_messages.append(message)

    def update_queue_status_label(self) -> None:
        return

    def refresh_visible_queue_manager(self, *_args) -> None:
        return

    def process_deferred_archives(self) -> None:
        return

    def request_joblist_save(self) -> None:
        self.save_requests += 1


class JobSchedulingIntegrationTests(unittest.TestCase):
    def test_one_dispatch_cycle_launches_every_allocation_in_priority_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            low_path = root / "low.inp"
            high_path = root / "high.inp"
            low_path.write_text("*Heading\n", encoding="utf-8")
            high_path.write_text("*Heading\n", encoding="utf-8")
            low = QueueItem(
                job_name="low",
                inp_path=str(low_path),
                effective_work_dir=str(root / "low-work"),
                status=STATUS_PENDING_RUN,
                cores=1,
                priority=0,
            )
            high = QueueItem(
                job_name="high",
                inp_path=str(high_path),
                effective_work_dir=str(root / "high-work"),
                status=STATUS_PENDING_RUN,
                cores=1,
                priority=100,
            )
            host = _SchedulingHost([low, high], slots=2)
            controller = JobController(host)
            launched = []

            def record_start(options, queue_item=None, *, queue_mode=False):
                launched.append((options.job_name, queue_item.attempt_id, queue_mode))
                return True

            controller.start_job = record_start

            controller.dispatch_queue_now()

            self.assertEqual([entry[0] for entry in launched], ["high", "low"])
            self.assertTrue(all(entry[1] for entry in launched))
            self.assertTrue(all(entry[2] for entry in launched))
            self.assertNotEqual(high.attempt_id, low.attempt_id)
            self.assertGreaterEqual(host.save_requests, 1)


if __name__ == "__main__":
    unittest.main()
