"""调度控制面与本机 Abaqus 执行之间的 Interface。"""

from __future__ import annotations

from typing import Protocol


class LocalExecutionBackend(Protocol):
    """Scheduler allocation 的本机执行 Adapter 所需能力。"""

    def start_process(self, *, job_key: str, run: dict, command: str) -> bool: ...

    def unregister_run(self, job_key: str) -> None: ...


__all__ = ["LocalExecutionBackend"]
