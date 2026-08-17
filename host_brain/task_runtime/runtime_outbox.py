"""Task Runtime Outbox 消息的进程内构造与串行发布。

该边界只拥有工厂、发布器、发布上限及一个进程内锁；耐久 Outbox 状态仍由注入
端口管理，不能因为本地锁或调用结果而被当作已可靠投递。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Mapping, Sequence
from typing import Any

from .ports import RuntimeOutboxPublisherPort, TaskOutboxFactoryPort


class RuntimeOutbox:
    """构造规范运行时消息，并串行化一个发布器实例。

    身份由聚合版本和事件类型决定；不同进程仍依赖持久 Outbox 的幂等与重试，
    本地锁仅避免同一应用实例在恢复和在线路径中同时调用发布器。
    """

    def __init__(
        self,
        *,
        publisher: RuntimeOutboxPublisherPort,
        factory: TaskOutboxFactoryPort,
        publish_limit: int,
    ) -> None:
        self._publisher = publisher
        self._factory = factory
        self._publish_limit = max(1, int(publish_limit))
        self._publish_lock = asyncio.Lock()

    def build(
        self,
        *,
        command_id: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        graph_revision: int | None = None,
        attempt_id: str | None = None,
        evidence_refs: Sequence[str] = (),
    ) -> Any:
        """构造绑定聚合版本的 Outbox 消息，并收敛重复证据引用。"""
        # 同一聚合版本和事件类型导出稳定幂等键；崩溃后重放不会制造第二条领域事件。
        identity = (
            command_id,
            aggregate_type,
            aggregate_id,
            str(aggregate_version),
            event_type,
        )
        material = "\n".join(identity)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        version = int(aggregate_version)
        payload_aggregate_id = aggregate_id
        if aggregate_type == "TASK_GRAPH":
            # 图 ID 单独不足以定位事件，必须纳入修订号以防旧图事件污染当前计划。
            if type(graph_revision) is not int or graph_revision < 1:
                raise ValueError(
                    "TASK_GRAPH Runtime Outbox requires a positive graph_revision"
                )
            payload_aggregate_id = f"{aggregate_id}@{graph_revision}"
        return self._factory.create(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=version,
            event_type=event_type,
            payload_ref=(
                f"taskstore://event/{aggregate_type}/{payload_aggregate_id}/{version}"
            ),
            idempotency_key=f"governed:{digest}",
            graph_revision=graph_revision,
            attempt_id=attempt_id,
            evidence_refs=tuple(
                dict.fromkeys(
                    _text(item) for item in evidence_refs if _text(item)
                )
            ),
            outbox_id=f"outbox::governed::{digest}",
        )

    async def publish_pending(
        self,
        *,
        best_effort: bool = False,
    ) -> dict[str, Any]:
        """发布一批耐久待发消息；仅在显式 best-effort 时降级而不伪造成功。"""
        # 进程内串行发布仅降低重复投递竞争；可靠性仍来自持久 outbox 的幂等与重试机制。
        async with self._publish_lock:
            try:
                result = self._publisher.publish_pending(limit=self._publish_limit)
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                # 清理/恢复路径可选择降级返回，但不把投递故障伪装成已发布。
                if best_effort:
                    return {"status": "DEGRADED"}
                raise
        return dict(result) if isinstance(result, Mapping) else {"status": "OK"}


def _text(value: Any) -> str:
    """规范化可枚举标识文本，供 Outbox 证据去重使用。"""
    return str(getattr(value, "value", value) or "").strip()


__all__ = ["RuntimeOutbox"]
