"""Task 所有的受治理 Chat 句子流式可见性授权。

该边界刻意窄于 Surface 与 Digital Life：它只能把已冻结的情感计划及已门控的
最终文本绑定到当前持有 attempt/lease/grant 的精确 TaskNode。它不能写入 Life、
Memory、Outbox、Voice、Avatar 或传输通道。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from natsume_contracts.life import digest_json

from host_brain.persistence.task_chat_delivery_store import (
    TASK_CHAT_DELIVERY_RECOVERY_REQUIRED,
    TaskChatDeliveryContext,
    TaskChatDeliveryStore,
    TaskChatDeliveryStoreError,
)


TASK_BOUND_CHAT_DELIVERY_AUTHORITY_INVALID = "TASK_BOUND_CHAT_DELIVERY_AUTHORITY_INVALID"


class TaskBoundChatDeliveryAuthorityError(RuntimeError):
    """受治理 worker 暴露文本前的稳定失败。"""

    def __init__(self, detail: str) -> None:
        self.reason_code = TASK_BOUND_CHAT_DELIVERY_AUTHORITY_INVALID
        self.detail = str(detail or "task-bound chat delivery authority is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


class TaskBoundChatDeliveryAuthority:
    """将一个冻结 Chat 节点绑定到不可变的可见句子回执。

    每个句子通过 Task 交付账本按计划、租约和围栏写入，允许安全恢复可见前缀；
    该对象不拥有任何对话状态或外部发送权限。
    """

    is_task_bound_delivery_authority = True

    def __init__(
        self,
        *,
        store: TaskChatDeliveryStore,
        frozen: Any,
        workspace_id: str,
        relationship_subject_id: str,
    ) -> None:
        if not isinstance(store, TaskChatDeliveryStore):
            raise TaskBoundChatDeliveryAuthorityError("canonical delivery store is required")
        route = str(getattr(frozen, "route", "") or "").strip().lower()
        if route != "chat":
            raise TaskBoundChatDeliveryAuthorityError("only frozen chat nodes may stream sentences")
        self._store = store
        self._frozen = frozen
        self._workspace_id = _required_text(workspace_id, "workspace_id")
        self._relationship_subject_id = _required_text(
            relationship_subject_id,
            "relationship_subject_id",
        )

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    def stage_plan(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        """持久化本次尝试的唯一情感计划；已存在时要求走恢复而非重跑模型。"""
        context = self._context_for(plan)
        receipt = self._store.stage_plan(context)
        if bool(receipt.get("replayed")):
            # 同一活跃尝试的第二次调用无权再次调用 provider。不可变计划标记的目的
            # 正是让恢复通过独立认证 reader 回放已暂存前缀，而非重新生成内容。
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_RECOVERY_REQUIRED,
                "governed Chat plan was already staged; reconcile before rerunning the model",
            )
        return self._with_scope(receipt, context=context)

    def stage_sentence(
        self,
        *,
        plan: Mapping[str, Any],
        sentence: str,
        sentence_index: int,
    ) -> dict[str, Any]:
        """按严格递增的 sentence_index 暂存一句已门控文本。"""
        context = self._context_for(plan)
        receipt = self._store.stage_sentence(
            context,
            sentence=sentence,
            sentence_index=sentence_index,
        )
        return self._with_scope(receipt, context=context)

    def record_stream_control(
        self,
        *,
        plan: Mapping[str, Any],
        event_kind: str,
        sent_sentence_count: int,
        reason_code: str = "",
    ) -> dict[str, Any]:
        """记录流控制事件，供恢复区分已发送前缀、停止与失败原因。"""
        context = self._context_for(plan)
        receipt = self._store.record_stream_control(
            context,
            event_kind=event_kind,
            sent_sentence_count=sent_sentence_count,
            reason_code=reason_code,
        )
        return self._with_scope(receipt, context=context)

    def _context_for(self, plan: Mapping[str, Any]) -> TaskChatDeliveryContext:
        """交叉验证情感计划与冻结节点，并构造账本的精确并发上下文。"""
        raw = dict(plan) if isinstance(plan, Mapping) else {}
        expression = raw.get("expression_plan")
        expression = dict(expression) if isinstance(expression, Mapping) else {}
        if raw.get("schema_version") != "affective_turn_plan.v1":
            raise TaskBoundChatDeliveryAuthorityError("frozen affective turn plan is required")
        if str(raw.get("self_id") or "").strip() != "natsume_ai":
            raise TaskBoundChatDeliveryAuthorityError("affective plan self identity differs")

        relationship_subject_id = _required_text(
            raw.get("relationship_subject_id"), "relationship_subject_id"
        )
        if relationship_subject_id != self._relationship_subject_id:
            raise TaskBoundChatDeliveryAuthorityError(
                "affective plan relationship subject differs from frozen context"
            )
        session_id = _required_text(raw.get("session_id"), "session_id")
        if session_id != _required_text(getattr(self._frozen, "session_id", ""), "session_id"):
            raise TaskBoundChatDeliveryAuthorityError(
                "affective plan session differs from frozen node"
            )
        workspace_id = _required_text(raw.get("workspace_id"), "workspace_id")
        if workspace_id != self._workspace_id:
            raise TaskBoundChatDeliveryAuthorityError(
                "affective plan workspace differs from frozen node"
            )
        synchronization_id = _required_text(
            expression.get("synchronization_id"), "synchronization_id", maximum=512
        )
        expression_revision = _non_negative_int(
            expression.get("self_affect_revision"), "expression.self_affect_revision"
        )
        self_affect_revision = _non_negative_int(
            raw.get("self_affect_revision"), "self_affect_revision"
        )
        if expression_revision != self_affect_revision:
            raise TaskBoundChatDeliveryAuthorityError(
                "expression self-affect revision differs from affective plan"
            )
        try:
            return TaskChatDeliveryContext(
                graph_id=_required_text(getattr(self._frozen, "graph_id", ""), "graph_id"),
                graph_revision=_positive_int(
                    getattr(self._frozen, "graph_revision", 0), "graph_revision"
                ),
                node_id=_required_text(getattr(self._frozen, "node_id", ""), "node_id"),
                attempt_id=_required_text(getattr(self._frozen, "attempt_id", ""), "attempt_id"),
                lease_id=_required_text(getattr(self._frozen, "lease_id", ""), "lease_id"),
                lease_owner_id=_required_text(
                    getattr(self._frozen, "lease_owner_id", ""), "lease_owner_id"
                ),
                fencing_token=_positive_int(
                    getattr(self._frozen, "fencing_token", 0), "fencing_token"
                ),
                node_grant_ref=_required_text(
                    getattr(self._frozen, "node_grant_ref", ""), "node_grant_ref", maximum=1024
                ),
                request_id=_required_text(getattr(self._frozen, "request_id", ""), "request_id"),
                session_id=session_id,
                principal_id=_required_text(
                    getattr(self._frozen, "principal_id", ""), "principal_id"
                ),
                self_id="natsume_ai",
                relationship_subject_id=relationship_subject_id,
                workspace_id=workspace_id,
                plan_id=_required_text(raw.get("plan_id"), "plan_id", maximum=512),
                plan_digest=digest_json(raw),
                synchronization_id=synchronization_id,
                self_affect_revision=self_affect_revision,
            )
        except TaskChatDeliveryStoreError as exc:
            raise TaskBoundChatDeliveryAuthorityError(exc.detail) from exc

    @staticmethod
    def _with_scope(
        receipt: Mapping[str, Any], *, context: TaskChatDeliveryContext
    ) -> dict[str, Any]:
        """将不可变计划范围复制到回执，便于调用方验证而不暴露冻结节点对象。"""
        return {
            **dict(receipt),
            "plan_id": context.plan_id,
            "plan_digest": context.plan_digest,
            "synchronization_id": context.synchronization_id,
            "self_affect_revision": context.self_affect_revision,
            "self_id": context.self_id,
            "relationship_subject_id": context.relationship_subject_id,
            "workspace_id": context.workspace_id,
        }


def _required_text(value: Any, field_name: str, *, maximum: int = 240) -> str:
    """校验受控标识文本的存在与长度，防止账本键被无界输入放大。"""
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise TaskBoundChatDeliveryAuthorityError(f"{field_name} is required and bounded")
    return text


def _non_negative_int(value: Any, field_name: str) -> int:
    """严格解析非负整数，拒绝布尔值等会模糊账本顺序的类型。"""
    if isinstance(value, bool):
        raise TaskBoundChatDeliveryAuthorityError(f"{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskBoundChatDeliveryAuthorityError(f"{field_name} must be an integer") from exc
    if parsed < 0:
        raise TaskBoundChatDeliveryAuthorityError(f"{field_name} must be non-negative")
    return parsed


def _positive_int(value: Any, field_name: str) -> int:
    """校验从 1 开始的图修订、围栏等身份字段。"""
    parsed = _non_negative_int(value, field_name)
    if parsed < 1:
        raise TaskBoundChatDeliveryAuthorityError(f"{field_name} must be positive")
    return parsed


__all__ = [
    "TASK_BOUND_CHAT_DELIVERY_AUTHORITY_INVALID",
    "TaskBoundChatDeliveryAuthority",
    "TaskBoundChatDeliveryAuthorityError",
]
