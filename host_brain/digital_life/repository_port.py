"""Digital Life 应用层消费的持久化端口。

端口定义快照/事件读取、命令 claim、aggregate 围栏、projection 与 outbox 收据的
最小能力集合。应用层只依赖这些语义，具体 SQLite 实现留在 persistence，避免
领域流程与存储技术或事务细节耦合。
"""

from __future__ import annotations

from typing import Any, Protocol

from natsume_contracts.life import (
    LifeAggregateClaim,
    LifeCommand,
    LifeCommandClaim,
    LifeCommandPreview,
    LifeCommandReceipt,
    LifeDeliveryReceipt,
    LifeEvent,
    LifeOutboxMessage,
    LifeProjectionIntent,
    LifeProjectionMessage,
    LifeProjectionReceipt,
    LifeSnapshot,
)


class LifeCommandRepositoryPort(Protocol):
    """服务、读取器与 dispatcher 所需的持久化操作。

    claim/release、commit 与 receipt 共同提供幂等和恢复边界：调用方可重试未完成
    工作，却不能在已有不可变回执后再次执行 projection 或外部投递。具体 SQLite
    适配器有意位于 ``host_brain.persistence``。
    """

    def get_snapshot(self, person_id: str) -> LifeSnapshot: ...
    def get_receipt(self, command_id: str) -> LifeCommandReceipt | None: ...
    def list_events(self, person_id: str, *, after_sequence: int = 0, limit: int = 100) -> list[LifeEvent]: ...
    def list_affective_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...
    def list_relationship_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...
    def list_companion_profile_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...
    def list_affective_turn_delivery_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...
    def list_affective_expression_delivery_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        after_delivery_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...
    def last_affective_expression_delivery_head(
        self,
        person_id: str,
        *,
        workspace_id: str,
        relationship_subject_id: str,
        session_id: str,
    ) -> dict[str, Any] | None: ...
    def list_companion_proactive_targets(
        self,
        *,
        limit: int = 1_000,
    ) -> list[dict[str, Any]]: ...
    def list_unadopted_projection_person_ids(self, *, limit: int = 1_000) -> list[str]: ...
    def try_claim_command(self, command: LifeCommand, **kwargs: Any) -> LifeCommandClaim | LifeCommandReceipt: ...
    def release_command_claim(self, claim: LifeCommandClaim) -> None: ...
    def acquire_aggregate_claim(self, person_id: str, **kwargs: Any) -> LifeAggregateClaim | None: ...
    def release_aggregate_claim(self, claim: LifeAggregateClaim) -> None: ...
    def commit_command(self, command: LifeCommand, preview: LifeCommandPreview, **kwargs: Any) -> LifeCommandReceipt: ...
    def adopt_legacy_projection(self, person_id: str, **kwargs: Any) -> LifeSnapshot: ...
    def list_replay_rows(self, person_id: str) -> list[Any]: ...
    def rebuild_projection(self, person_id: str, **kwargs: Any) -> LifeSnapshot: ...
    def list_projection_intents(self, command_id: str) -> list[LifeProjectionIntent]: ...
    def claim_projection_intents(self, **kwargs: Any) -> list[LifeProjectionMessage]: ...
    def renew_projection_claim(self, message: LifeProjectionMessage, **kwargs: Any) -> None: ...
    def complete_projection(self, message: LifeProjectionMessage, **kwargs: Any) -> LifeProjectionReceipt: ...
    def fail_projection(self, message: LifeProjectionMessage, **kwargs: Any) -> None: ...
    def retry_projection(self, intent_id: str, **kwargs: Any) -> bool: ...
    def get_projection_receipt(self, intent_id: str) -> LifeProjectionReceipt | None: ...
    def claim_deliveries(self, **kwargs: Any) -> list[LifeOutboxMessage]: ...
    def renew_delivery_claim(self, message: LifeOutboxMessage, **kwargs: Any) -> LifeOutboxMessage | None: ...
    def complete_delivery(self, message: LifeOutboxMessage, **kwargs: Any) -> LifeDeliveryReceipt: ...
    def fail_delivery(self, message: LifeOutboxMessage, **kwargs: Any) -> None: ...
    def retry_delivery(self, message: LifeOutboxMessage, **kwargs: Any) -> None: ...
    def get_delivery_receipt(self, delivery_key: str) -> LifeDeliveryReceipt | None: ...
    def list_completed_companion_proactive_messages(
        self,
        *,
        limit: int = 100,
    ) -> list[LifeOutboxMessage]: ...


__all__ = ["LifeCommandRepositoryPort"]
