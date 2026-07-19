"""Restart 前置作业从解析到归档解锁的统一生命周期。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .command import SubmitOptions, derive_oldjob_name, inp_has_restart_keyword
from .models import QueueItem
from .queue_scheduler import (
    RestartOldjobReference,
    build_restart_oldjob_reference,
    managed_job_key,
    oldjob_name_from_item,
    refresh_queue_dependencies,
    resolve_oldjob_source_dir,
    restart_dependents_using_current_work_dir,
)


@dataclass(frozen=True)
class RestartDependencyResolution:
    """一次提交所需的完整 Restart 依赖解析结果。"""

    required: bool
    ready: bool
    oldjob_name: str = ""
    source_dir: str = ""
    message: str = ""
    reference: RestartOldjobReference = RestartOldjobReference("", "", "", "", "")


class RestartDependencyLifecycle:
    """集中维护依赖解析、引用记录、归档阻塞和失效规则。"""

    def __init__(
        self,
        queue_items: list[QueueItem],
        run_records: Mapping[str, dict],
        archive_reserved_keys: set[tuple[str, str]],
    ) -> None:
        self.queue_items = queue_items
        self.run_records = run_records
        self.archive_reserved_keys = archive_reserved_keys
        self._reference_cache: dict[str, RestartOldjobReference] = {}

    def replace_queue_items(self, queue_items: list[QueueItem]) -> None:
        """在持久化队列整体恢复后更新生命周期的数据源。"""
        self.queue_items = queue_items

    @staticmethod
    def requires_dependency(options: SubmitOptions, queue_item: QueueItem | None) -> bool:
        if queue_item is not None and queue_item.run_mode == "restart":
            return True
        return inp_has_restart_keyword(options.inp_file)

    @staticmethod
    def oldjob_name(options: SubmitOptions, queue_item: QueueItem | None) -> str:
        name = derive_oldjob_name(options.oldjob_path)
        if not name and queue_item is not None:
            name = oldjob_name_from_item(queue_item)
        return name

    @staticmethod
    def manual_candidate_paths(queue_item: QueueItem | None, oldjob_name: str) -> list[str]:
        if queue_item is None:
            return []
        return [
            queue_item.oldjob_path,
            str(Path(queue_item.oldjob_dir) / f"{oldjob_name}.odb")
            if queue_item.oldjob_dir and oldjob_name
            else "",
        ]

    def resolve_source(self, options: SubmitOptions, queue_item: QueueItem | None) -> str:
        oldjob_name = self.oldjob_name(options, queue_item)
        if not oldjob_name:
            return ""
        candidate_paths = [options.oldjob_path]
        candidate_paths.extend(self.manual_candidate_paths(queue_item, oldjob_name))
        return resolve_oldjob_source_dir(
            oldjob_name,
            self.queue_items,
            current_item=queue_item,
            run_records=self.run_records.values(),
            candidate_paths=candidate_paths,
        )

    def resolve(self, options: SubmitOptions, queue_item: QueueItem | None) -> RestartDependencyResolution:
        required = self.requires_dependency(options, queue_item)
        if not required:
            return RestartDependencyResolution(required=False, ready=True)

        oldjob_name = self.oldjob_name(options, queue_item)
        if not oldjob_name:
            return RestartDependencyResolution(
                required=True,
                ready=False,
                message="未选择有效的 Restart 前置作业",
            )

        source_dir = self.resolve_source(options, queue_item)
        if not source_dir:
            return RestartDependencyResolution(
                required=True,
                ready=False,
                oldjob_name=oldjob_name,
                message=f"未找到 Restart 前置作业 ODB：{oldjob_name}",
            )
        if self.source_is_reserved(source_dir, oldjob_name):
            return RestartDependencyResolution(
                required=True,
                ready=False,
                oldjob_name=oldjob_name,
                source_dir=source_dir,
                message=f"前置作业正在归档，等待归档完成后提交：{oldjob_name}",
            )

        reference = self.build_reference(options, queue_item, source_dir)
        return RestartDependencyResolution(
            required=True,
            ready=True,
            oldjob_name=oldjob_name,
            source_dir=source_dir,
            reference=reference,
        )

    def build_reference(
        self,
        options: SubmitOptions,
        queue_item: QueueItem | None,
        source_dir: str,
    ) -> RestartOldjobReference:
        oldjob_name = self.oldjob_name(options, queue_item)
        if not oldjob_name or not source_dir:
            return RestartOldjobReference("", "", "", "", "")
        reference = build_restart_oldjob_reference(
            oldjob_name,
            source_dir,
            self.queue_items,
            current_item=queue_item,
            run_records=self.run_records.values(),
            manual_candidate_paths=self.manual_candidate_paths(queue_item, oldjob_name),
            external_candidate_paths=[options.oldjob_path],
        )
        if not reference.reference_key:
            return reference
        return self._reference_cache.setdefault(reference.reference_key, reference)

    def source_is_reserved(self, source_dir: str, oldjob_name: str) -> bool:
        if not source_dir or not oldjob_name:
            return False
        return managed_job_key(source_dir, oldjob_name) in self.archive_reserved_keys

    def refresh_queue(self) -> None:
        refresh_queue_dependencies(self.queue_items)

    @staticmethod
    def record_workspace(
        workspace_info: dict,
        reference: RestartOldjobReference,
    ) -> None:
        if not reference.oldjob_arg:
            return
        workspace_info.update(
            resolved_oldjob_arg=reference.oldjob_arg,
            resolved_oldjob_source=reference.source_kind,
            resolved_oldjob_dir=reference.source_dir,
            resolved_oldjob_reference_key=reference.reference_key,
        )

    @staticmethod
    def record_queue_item(queue_item: QueueItem, source: Mapping[str, object]) -> None:
        queue_item.resolved_oldjob_arg = str(source.get("resolved_oldjob_arg", "") or "")
        queue_item.resolved_oldjob_source = str(source.get("resolved_oldjob_source", "") or "")
        queue_item.resolved_oldjob_dir = str(source.get("resolved_oldjob_dir", "") or "")
        queue_item.resolved_oldjob_reference_key = str(
            source.get("resolved_oldjob_reference_key", "") or ""
        )

    @staticmethod
    def clear_queue_item(queue_item: QueueItem) -> None:
        RestartDependencyLifecycle.record_queue_item(queue_item, {})

    @staticmethod
    def archive_blockers(run: dict, queue_items: Iterable[QueueItem]) -> list[QueueItem]:
        return restart_dependents_using_current_work_dir(run, list(queue_items))
