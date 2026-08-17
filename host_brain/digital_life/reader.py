"""Digital Life 快照与 journal 的纯只读门面。

读取器只将仓储的不可变事实暴露给应用服务；不持有缓存、不创建目标、不改变关系
许可。把读路径单独封装，使恢复代码能从 durable projection 重新建立视图。
"""

from __future__ import annotations

from natsume_contracts.life import (
    LifeCommandReceipt,
    LifeDeliveryReceipt,
    LifeEvent,
    LifeOutboxMessage,
    LifeProjectionIntent,
    LifeProjectionReceipt,
    LifeSnapshot,
)
from host_brain.digital_life.repository_port import LifeCommandRepositoryPort


class LifeSnapshotReader:
    """面向应用层的只读端口适配器，所有状态权威性仍来自仓储快照与事件账本。"""
    def __init__(self, repository: LifeCommandRepositoryPort) -> None:
        self.repository = repository

    def get_snapshot(self, person_id: str) -> LifeSnapshot:
        """读取 aggregate 当前快照；调用方应在写入时仍依赖 UoW 的实际版本。"""
        return self.repository.get_snapshot(person_id)

    def list_events(
        self,
        person_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[LifeEvent]:
        """按严格 sequence 游标分页读取事件；不保证调用间快照静止。"""
        return self.repository.list_events(
            person_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def list_affective_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """读取受界 DL-01 投影而不成为 writer，游标由时间与事件 ID 共同稳定。"""

        return self.repository.list_affective_event_heads(
            person_id,
            after_created_at_ms=after_created_at_ms,
            after_event_id=after_event_id,
            limit=limit,
        )

    def list_relationship_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """读取 DL-03 关系投影而不成为 writer，供生命周期重开既有关系事实。"""

        return self.repository.list_relationship_event_heads(
            person_id,
            after_created_at_ms=after_created_at_ms,
            after_event_id=after_event_id,
            limit=limit,
        )

    def list_companion_profile_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """读取 DL-06 profile 生命周期投影，不包含文本或媒体内容。"""

        return self.repository.list_companion_profile_event_heads(
            person_id,
            after_created_at_ms=after_created_at_ms,
            after_event_id=after_event_id,
            limit=limit,
        )

    def list_affective_turn_delivery_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """读取隐私最小化的 DL-04 计划/句子投递回执，并非物理设备结果。"""

        return self.repository.list_affective_turn_delivery_event_heads(
            person_id,
            after_created_at_ms=after_created_at_ms,
            after_event_id=after_event_id,
            limit=limit,
        )

    def list_affective_expression_delivery_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        after_delivery_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """读取隐私最小化的 DL-05 Host 表达投递头，供恢复与去重使用。"""

        return self.repository.list_affective_expression_delivery_event_heads(
            person_id,
            after_created_at_ms=after_created_at_ms,
            after_event_id=after_event_id,
            after_delivery_id=after_delivery_id,
            limit=limit,
        )

    def last_affective_expression_delivery_head(
        self,
        person_id: str,
        *,
        workspace_id: str,
        relationship_subject_id: str,
        session_id: str,
    ) -> dict[str, object] | None:
        """Return the exact-scope durable Host avatar-transition head.

        The row records protocol staging, not physical device application.
        """

        return self.repository.last_affective_expression_delivery_head(
            person_id,
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )

    def list_companion_proactive_targets(
        self,
        *,
        limit: int = 1_000,
    ) -> list[dict[str, object]]:
        """List currently eligible, scope-valid canonical companion targets.

        The registry is reconstructed from durable Life projections rather
        than an in-process queue.  This read has no authority to create,
        alter or infer a target or relationship permission.
        """

        return self.repository.list_companion_proactive_targets(limit=limit)

    def get_receipt(self, command_id: str) -> LifeCommandReceipt | None:
        """按确定性命令 ID 查询回执，是幂等重开而非提交成功推断的依据。"""
        return self.repository.get_receipt(command_id)

    def get_delivery_receipt(
        self,
        outbox_id: str,
    ) -> LifeDeliveryReceipt | None:
        """读取外部投递的不可变回执；存在即禁止 dispatcher 再次执行该动作。"""
        return self.repository.get_delivery_receipt(outbox_id)

    def list_completed_companion_proactive_messages(
        self,
        *,
        limit: int = 100,
    ) -> list[LifeOutboxMessage]:
        """Read only receipt-backed companion messages missing a relation event."""

        return self.repository.list_completed_companion_proactive_messages(
            limit=limit
        )

    def list_projection_intents(
        self,
        command_id: str,
    ) -> list[LifeProjectionIntent]:
        """读取某命令已持久化的 projection intent，不触发任何处理。"""
        return self.repository.list_projection_intents(command_id)

    def get_projection_receipt(
        self,
        intent_id: str,
    ) -> LifeProjectionReceipt | None:
        """查询 projection 收据，供竞争恢复和幂等调用判定完成边界。"""
        return self.repository.get_projection_receipt(intent_id)

    def list_unadopted_projection_person_ids(self, *, limit: int = 1_000) -> list[str]:
        return self.repository.list_unadopted_projection_person_ids(limit=limit)


__all__ = ["LifeSnapshotReader"]
