"""将账本拥有的持久任务事实绑定为只读状态投影。"""

from __future__ import annotations

from typing import Any

from host_brain.persistence.durable_task_status_projection import (
    DurableTaskStatusProjection,
)


def build_durable_task_status_projection(*, ledger: Any) -> DurableTaskStatusProjection:
    """绑定规范 SQLite 账本供进度查询。

    投影仅解释已持久化的任务/收据记录，不能启动、重试、取消或修复任务；调用者不能
    因读取到“进行中”便假定 worker 仍存活，恢复仍归 Task Runtime 所有。
    """

    return DurableTaskStatusProjection.from_ledger(ledger)


__all__ = ["build_durable_task_status_projection"]
