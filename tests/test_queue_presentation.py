import unittest

from abaqus_submitter.models import QueueItem
from abaqus_submitter.queue_presentation import (
    QueueRefreshBatch,
    formal_row_projection,
    runtime_cell_projection,
)


class QueuePresentationTests(unittest.TestCase):
    def test_incremental_requests_are_coalesced(self):
        batch = QueueRefreshBatch()
        batch.request_items({"a", "b"})
        batch.request_items({"b", "c"})
        request = batch.take()
        self.assertFalse(request.full)
        self.assertEqual(request.item_ids, frozenset({"a", "b", "c"}))
        self.assertFalse(batch.pending)

    def test_full_refresh_supersedes_dirty_rows(self):
        batch = QueueRefreshBatch()
        batch.request_items({"a"})
        batch.request_full()
        batch.request_items({"b"})
        request = batch.take()
        self.assertTrue(request.full)
        self.assertFalse(request.item_ids)

    def test_runtime_projection_only_contains_volatile_cells(self):
        item = QueueItem(job_name="demo", rss_bytes=2 * 1024**3, status="运行中", message="求解中")
        self.assertEqual(runtime_cell_projection(item), {7: "2.0 GB", 10: "运行中", 11: "求解中"})
        row = formal_row_projection(item, 3)
        self.assertEqual(row[0], "3")
        self.assertEqual(row[7:12], ("2.0 GB", "0", "可调度", "运行中", "求解中"))


if __name__ == "__main__":
    unittest.main()
