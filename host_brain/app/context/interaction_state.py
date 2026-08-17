"""普通聊天链的交互语义快照，汇合解释、关系与当前话题线索。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _normalize_text(raw: Any) -> str:
    return str(raw or "").strip()


@dataclass(frozen=True)
class InteractionState:
    """普通聊天链使用的规范交互语义状态。

    它保存本轮推断出的话题与互动线索，并复制各子状态以供下游提示词/渲染读取；
    不把这些软性判断提升为持久事实、用户授权或外部动作完成收据。
    """

    current_topic: str = ""
    interaction_move: str = ""
    user_stance: str = ""
    followup_target: str = ""
    followup_excerpt: str = ""
    followup_context_excerpt: str = ""
    ongoing_thread: str = ""
    affect: dict[str, Any] = field(default_factory=dict)
    relationship: dict[str, Any] = field(default_factory=dict)
    drive: dict[str, Any] = field(default_factory=dict)
    topic_ledger: dict[str, Any] = field(default_factory=dict)
    tactic_ledger: dict[str, Any] = field(default_factory=dict)
    perception_summary: str = ""
    reason_tags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "current_topic": self.current_topic,
            "interaction_move": self.interaction_move,
            "user_stance": self.user_stance,
            "followup_target": self.followup_target,
            "followup_excerpt": self.followup_excerpt,
            "followup_context_excerpt": self.followup_context_excerpt,
            "ongoing_thread": self.ongoing_thread,
            "affect": dict(self.affect or {}),
            "relationship": dict(self.relationship or {}),
            "drive": dict(self.drive or {}),
            "topic_ledger": dict(self.topic_ledger or {}),
            "tactic_ledger": dict(self.tactic_ledger or {}),
            "perception_summary": self.perception_summary,
            "reason_tags": list(self.reason_tags),
        }


def coerce_interaction_state(raw: Mapping[str, Any] | None) -> InteractionState:
    """将兼容输入收敛为不可变状态对象；错误形状退化为空而不保留外部可变引用。"""
    payload = dict(raw or {}) if isinstance(raw, Mapping) else {}
    return InteractionState(
        current_topic=_normalize_text(payload.get("current_topic")),
        interaction_move=_normalize_text(payload.get("interaction_move")),
        user_stance=_normalize_text(payload.get("user_stance")),
        followup_target=_normalize_text(payload.get("followup_target")),
        followup_excerpt=_normalize_text(payload.get("followup_excerpt")),
        followup_context_excerpt=_normalize_text(payload.get("followup_context_excerpt")),
        ongoing_thread=_normalize_text(payload.get("ongoing_thread")),
        affect=dict(payload.get("affect") or {}),
        relationship=dict(payload.get("relationship") or {}),
        drive=dict(payload.get("drive") or {}),
        topic_ledger=dict(payload.get("topic_ledger") or {}),
        tactic_ledger=dict(payload.get("tactic_ledger") or {}),
        perception_summary=_normalize_text(payload.get("perception_summary")),
        reason_tags=tuple(
            str(item).strip()
            for item in list(payload.get("reason_tags") or [])
            if str(item).strip()
        ),
    )


def build_interaction_state(
    *,
    interpretation: Mapping[str, Any],
    interaction_thread: Mapping[str, Any],
    affect_state: Mapping[str, Any],
    relationship_state: Mapping[str, Any],
    drive_state: Mapping[str, Any],
    topic_ledger: Mapping[str, Any],
    tactic_ledger: Mapping[str, Any],
    perception_summary: str = "",
) -> InteractionState:
    """从本轮解释与各状态投影组装快照，不写入任一状态源。"""
    interpretation_payload = dict(interpretation or {})
    thread_payload = dict(interaction_thread or {})
    return InteractionState(
        current_topic=_normalize_text(interpretation_payload.get("literal_topic")),
        interaction_move=_normalize_text(interpretation_payload.get("interaction_move")),
        user_stance=_normalize_text(interpretation_payload.get("user_stance")),
        followup_target=_normalize_text(
            interpretation_payload.get("followup_target") or thread_payload.get("source_topic")
        ),
        followup_excerpt=_normalize_text(thread_payload.get("source_excerpt")),
        followup_context_excerpt=_normalize_text(thread_payload.get("source_context_excerpt")),
        ongoing_thread=_normalize_text(
            interpretation_payload.get("ongoing_thread") or thread_payload.get("thread_kind")
        ),
        affect=dict(affect_state or {}),
        relationship=dict(relationship_state or {}),
        drive=dict(drive_state or {}),
        topic_ledger=dict(topic_ledger or {}),
        tactic_ledger=dict(tactic_ledger or {}),
        perception_summary=_normalize_text(perception_summary),
        reason_tags=tuple(
            str(item).strip()
            for item in list(interpretation_payload.get("reason_tags") or [])
            if str(item).strip()
        ),
    )


__all__ = [
    "InteractionState",
    "build_interaction_state",
    "coerce_interaction_state",
]
