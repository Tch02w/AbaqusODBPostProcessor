"""队列数据投影与界面刷新合并。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import QueueItem
from .qt_compat import QtCore


def format_runtime_memory(size_bytes: int) -> str:
    """将最近一次统计到的内存占用量格式化为 MB 或 GB。"""
    try:
        value = int(size_bytes or 0)
    except (TypeError, ValueError):
        return "未统计"
    if value <= 0:
        return "未统计"
    gib = value / 1024**3
    if gib >= 1:
        return f"{gib:.1f} GB"
    return f"{value / 1024**2:.0f} MB"


def candidate_row_projection(item: QueueItem, index: int) -> tuple[str, ...]:
    """把候选作业映射为稳定的表格行。"""
    dependency = item.oldjob_name or (Path(item.oldjob_path).stem if item.oldjob_path else "")
    return (
        "",
        str(index),
        item.job_name,
        item.inp_path,
        item.source or "文件",
        item.job_type or ("重启动" if item.run_mode == "restart" else "普通"),
        dependency,
        os.path.basename(item.fortran_path) if item.fortran_path else "",
        item.message,
    )


def formal_row_projection(item: QueueItem, index: int) -> tuple[str, ...]:
    """把正式队列作业映射为稳定的表格行。"""
    dependency = item.oldjob_name or (Path(item.oldjob_path).stem if item.oldjob_path else "")
    return (
        str(index),
        item.job_name,
        item.inp_path,
        item.job_type or ("重启动" if item.run_mode == "restart" else "普通"),
        dependency,
        os.path.basename(item.fortran_path) if item.fortran_path else "",
        "" if int(item.cores or 0) <= 0 else str(item.cores),
        format_runtime_memory(item.rss_bytes),
        item.status,
        item.message,
    )


def runtime_cell_projection(item: QueueItem) -> dict[int, str]:
    """仅投影高频变化的正式队列单元格。"""
    return {
        7: format_runtime_memory(item.rss_bytes),
        8: item.status,
        9: item.message,
    }


@dataclass(frozen=True)
class QueueRefreshRequest:
    full: bool
    item_ids: frozenset[str]


class QueueRefreshBatch:
    """合并一个事件循环周期内的刷新请求。"""

    def __init__(self) -> None:
        self._full = False
        self._item_ids: set[str] = set()

    @property
    def pending(self) -> bool:
        return self._full or bool(self._item_ids)

    def request_full(self) -> None:
        self._full = True
        self._item_ids.clear()

    def request_items(self, item_ids: set[str]) -> None:
        if not self._full:
            self._item_ids.update(item_ids)

    def take(self) -> QueueRefreshRequest:
        request = QueueRefreshRequest(self._full, frozenset(self._item_ids))
        self._full = False
        self._item_ids.clear()
        return request


class QueuePresentationCoordinator(QtCore.QObject):
    """把高频刷新合并为每个事件循环最多一次的界面更新。"""

    def __init__(
        self,
        *,
        full_refresh: Callable[[], None],
        runtime_refresh: Callable[[set[str]], None],
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._full_refresh = full_refresh
        self._runtime_refresh = runtime_refresh
        self._batch = QueueRefreshBatch()
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.flush)

    def request_full(self) -> None:
        self._batch.request_full()
        self._schedule()

    def request_items(self, item_ids: set[str]) -> None:
        if not item_ids:
            return
        self._batch.request_items(item_ids)
        self._schedule()

    def flush(self) -> None:
        request = self._batch.take()
        if request.full:
            self._full_refresh()
        elif request.item_ids:
            self._runtime_refresh(set(request.item_ids))

    def cancel(self) -> None:
        self._timer.stop()
        self._batch.take()

    def _schedule(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
