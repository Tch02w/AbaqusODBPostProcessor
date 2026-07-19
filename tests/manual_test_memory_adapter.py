# ruff: noqa: E402

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_runtime_dir = tempfile.TemporaryDirectory()
os.environ["ABAQUS_SUBMITTER_DATA_DIR"] = _runtime_dir.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import abaqus_submitter.memory_adapter as memory_adapter_module
from abaqus_submitter.main import MainWindow
from abaqus_submitter.memory_adapter import QtMemoryMonitorAdapter
from abaqus_submitter.memory_monitor import MemoryMonitorService
from abaqus_submitter.models import QueueItem
from abaqus_submitter.qt_compat import QtCore, QtWidgets
from abaqus_submitter.queue_scheduler import get_managed_active_job_keys


def wait_for_signal(signal, trigger, timeout_ms=3000):
    loop = QtCore.QEventLoop()
    result = {}

    def done(payload=None):
        result["payload"] = payload
        loop.quit()

    signal.connect(done)
    QtCore.QTimer.singleShot(timeout_ms, loop.quit)
    trigger()
    loop.exec()
    try:
        signal.disconnect(done)
    except (TypeError, RuntimeError):
        pass
    assert "payload" in result
    return result["payload"]


def main() -> int:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])

    calls = {"count": 0}

    def fake_scan(force=False):
        calls["count"] += 1
        return {
            "JobA": {
                "working_set": 2 * 1024**3,
                "private_memory": 2 * 1024**3,
                "process_count": 2,
                "process_names": "standard.exe, EqsEquationSolver",
            },
            "JobB": {
                "working_set": 3 * 1024**3,
                "private_memory": 3 * 1024**3,
                "process_count": 1,
                "process_names": "standard.exe",
            },
        }

    original_scan = memory_adapter_module.get_abaqus_job_memory_usage
    original_memory = memory_adapter_module.get_system_memory_info
    memory_adapter_module.get_abaqus_job_memory_usage = fake_scan
    memory_adapter_module.get_system_memory_info = lambda: {
        "total": 64 * 1024**3,
        "available": 32 * 1024**3,
    }
    try:
        service = MemoryMonitorService(
            learning_interval_ms=1,
            patrol_interval_ms=10,
            min_samples=2,
            stable_polls=2,
            stable_relative_delta=0.05,
            unlimited_job_slots=99,
        )
        adapter = QtMemoryMonitorAdapter(service=service)
        adapter.register_job(job_key="k1", job_name="JobA", work_dir="G:/test")
        adapter.register_job(job_key="k2", job_name="JobB", work_dir="G:/test")
        adapter.activate_job("k1")
        adapter.activate_job("k2")
        assert adapter.timer.isActive()

        adapter.memory_scan_running = True
        adapter.schedule_memory_scan()
        assert adapter._thread is None
        adapter.memory_scan_running = False

        payload = wait_for_signal(adapter.scanFinished, adapter.schedule_memory_scan)
        assert calls["count"] == 1
        result = adapter.apply_scan_payload(
            payload,
            step_by_job_key={"k1": "Step-1", "k2": "Step-2"},
            active_job_names={"JobA", "JobB"},
        )
        assert {job["job_name"] for job in result["updated_jobs"]} == {"JobA", "JobB"}

        queue_items = [
            QueueItem(item_id="a", job_name="JobA", status="运行中"),
            QueueItem(item_id="b", job_name="JobB", status="运行中"),
        ]
        for item in queue_items:
            usage = result["usage_by_job"][item.job_name]
            item.rss_bytes = int(usage["private_memory"] or usage["working_set"] or 0)
        assert queue_items[0].rss_bytes == 2 * 1024**3
        assert queue_items[1].rss_bytes == 3 * 1024**3

        for _ in range(3):
            service.tracking_states["k1"].next_sample_at = 0
            service.tracking_states["k2"].next_sample_at = 0
            result = adapter.apply_scan_payload(
                {
                    "usage_by_job": fake_scan(force=True),
                    "system_memory": {"available": 32 * 1024**3},
                },
                step_by_job_key={"k1": "Step-1", "k2": "Step-2"},
                active_job_names={"JobA", "JobB"},
            )
        assert service.tracking_states["k1"].monitor_mode == "patrol"

        spike = {
            "JobA": {
                "working_set": 4 * 1024**3,
                "private_memory": 4 * 1024**3,
                "process_count": 2,
                "process_names": "standard.exe",
            }
        }
        service.tracking_states["k1"].next_sample_at = 0
        adapter.apply_scan_payload(
            {"usage_by_job": spike, "system_memory": {"available": 32 * 1024**3}},
            step_by_job_key={"k1": "Step-1"},
            active_job_names={"JobA"},
        )
        assert service.tracking_states["k1"].monitor_mode == "learning"
        assert service.job_estimates["JobA"].step_peaks["Step-1"] == 4 * 1024**3

        estimate = service.update_external_job_estimate(
            job_name="ExternalJob",
            rss_bytes=5 * 1024**3,
            process_count=1,
            process_names="standard.exe",
        )
        assert estimate is not None
        assert estimate.estimated_memory >= 5 * 1024**3

        empty_service = MemoryMonitorService(unlimited_job_slots=77)
        empty_slots = empty_service.estimate_available_slots(available_memory=16 * 1024**3)
        assert empty_slots.slots == 77

        window = MainWindow()
        window.active_runs = {
            "run1": {
                "work_dir": "G:/test",
                "job_name": "JobA",
                "queue_item": None,
            }
        }
        window.queue_items = [
            QueueItem(
                job_name="JobA",
                inp_path="G:/test/JobA.inp",
                status="运行中",
                is_external=True,
                external_work_dir="G:/test",
            )
        ]
        assert len(get_managed_active_job_keys(window.active_runs, window.queue_items)) == 1
        window.close()

        adapter.stop()
        adapter.schedule_memory_scan()
        assert adapter._thread is None
    finally:
        memory_adapter_module.get_abaqus_job_memory_usage = original_scan
        memory_adapter_module.get_system_memory_info = original_memory

    _runtime_dir.cleanup()
    print("manual_test_memory_adapter: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
