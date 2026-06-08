from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, replace
import re
from typing import Any

from host_brain.core.ids import now_ms
from host_brain.persona.isolated_surface_continuity import (
    ThreeLayerContinuityContext,
    build_surface_input_from_three_layer_context,
)
from host_brain.persona.isolated_surface_harness import IsolatedSurfaceInput
from host_brain.memory.life_memory_query import (
    TEMPORAL_CURRENT,
    TEMPORAL_DATE_PREFIX,
    TEMPORAL_DAY_BEFORE_YESTERDAY,
    TEMPORAL_RECENT_DAYS,
    TEMPORAL_TODAY,
    TEMPORAL_UNSPECIFIED,
    TEMPORAL_YESTERDAY,
    plan_life_memory_query,
)


SURFACE_MEMORY_RECALL_FAILED = "SURFACE_MEMORY_RECALL_FAILED"
SURFACE_MEMORY_RECALL_VALIDATION_FAILED = "SURFACE_MEMORY_RECALL_VALIDATION_FAILED"
SURFACE_MEMORY_RECALL_COVERAGE_FAILED = "SURFACE_MEMORY_RECALL_COVERAGE_FAILED"
SURFACE_MEMORY_RECALL_SKIPPED = "SURFACE_MEMORY_RECALL_SKIPPED"
SURFACE_MEMORY_WRITE_FAILED = "SURFACE_MEMORY_WRITE_FAILED"
SURFACE_MEMORY_EVIDENCE_REQUIRED = "SURFACE_MEMORY_EVIDENCE_REQUIRED"
SURFACE_MEMORY_EMPTY_TURN = "SURFACE_MEMORY_EMPTY_TURN"
RECALL_POLICY_MUST = "MUST_RECALL"
RECALL_POLICY_MAY = "MAY_RECALL"
RECALL_POLICY_NO = "NO_RECALL"

_URI_RE = re.compile(r"\b(?:chat|memory|ledger|evd|doc|memo)://[^\s，。；;、]+")
_SPACE_RE = re.compile(r"\s+")
_JSON_MARK_RE = re.compile(r"[{}\[\]\"]+")
_FIELD_LABEL_RE = re.compile(r"\b(?:text|content|detail|memory_text)\s*[:：]\s*", re.IGNORECASE)
_ANAPHORIC_SELECTION_RE = re.compile(
    r"(?:哪(?:本|部|个|一(?:本|部|个))|哪个|哪部|哪本|"
    r"(?:我问你|刚问你|问你).{0,12}哪|"
    r"(?:喜欢|想看|先看|推荐|选|挑|好看).{0,8}哪|"
    r"(?:这本书|这部书|这本|这部|这书|它).{0,8}(?:好看|能看|值得看|怎么样|如何)|"
    r"(?:这|那|昨天那|前天那|今天这)(?:几|三|两|二|十几)?(?:本|部|个).{0,8}(?:好看|能看|值得看|怎么样|如何|喜欢|想看|先看))",
)
_LOCAL_MEMORY_TZ = timezone(timedelta(hours=8))
_RECALL_HINTS = (
    "还记得",
    "记得吗",
    "记不记得",
    "上次",
    "之前",
    "以前",
    "那天",
    "刚才",
    "刚刚",
    "跑完",
    "完成了吗",
    "失败原因",
    "今日推荐",
    "推荐结果",
    "说好",
    "约定",
    "继续刚才",
    "remember",
    "last time",
)
_SHORT_ACKS = frozenset({"嗯", "嗯嗯", "好", "好的", "哦", "行", "继续", "然后呢"})
_LOW_SIGNAL_TASKS = frozenset({
    "back",
    "busy",
    "chat",
    "daily_chat",
    "daily_good_morning",
    "first_ask",
    "greeting",
    "plain_answer",
    "plain_hold",
    "presence",
    "short_ack",
    "wait",
})
_RECALL_TASKS = frozenset({"recall", "recall_memory", "memory_followup", "memory_recall"})
_MEMORY_WRITE_TASKS = frozenset({"memory_write", "memory_store", "remember"})
_TECH_TASKS = frozenset({
    "advice_continue",
    "complaint",
    "help_takeover",
    "next_step",
    "small_failure",
    "technical_help",
    "triage_first_step",
    "write_takeover",
})
_EMOTION_TASKS = frozenset({"apology", "bad_mood", "give_up", "headache", "quiet", "sleepy", "tired"})
_IDENTITY_TASKS = frozenset({"identity"})
SURFACE_USER_FACT_STORES = frozenset({
    "life_memory_collection",
    "life_memory_entity",
    "life_episode",
    "episodic_vector",
    "episodic",
    "sqlite_ledger",
    "semantic_memory",
})
SURFACE_BOND_STORES = frozenset({"neo4j_bond", "bond_graph"})
SURFACE_MAIN_CHAIN_RECALL_STORES = SURFACE_USER_FACT_STORES | SURFACE_BOND_STORES


@dataclass(frozen=True)
class SurfaceMemoryFactRow:
    fact: str
    store_id: str
    source_ref: str = ""
    evidence_ref: str = ""
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact": self.fact,
            "store_id": self.store_id,
            "source_ref": self.source_ref,
            "evidence_ref": self.evidence_ref,
            "score": round(float(self.score or 0.0), 6),
        }


@dataclass(frozen=True)
class MemoryRecallPacket:
    """Compressed recall output allowed to cross into the final surface layer."""

    facts: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()
    relationship_clues: tuple[str, ...] = ()
    must_not_do: tuple[str, ...] = ()
    confidence: float = 0.0
    evidence_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "facts": list(self.facts),
            "style_anchor": list(self.style_anchor),
            "relationship_clues": list(self.relationship_clues),
            "must_not_do": list(self.must_not_do),
            "confidence": round(float(self.confidence or 0.0), 6),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class SurfaceMemoryRecallPacket(MemoryRecallPacket):
    """Detailed packet kept for recall diagnostics before the surface boundary."""

    query: str = ""
    rows: tuple[SurfaceMemoryFactRow, ...] = ()
    source_refs: tuple[str, ...] = ()
    route_ids: tuple[str, ...] = ()
    candidate_count: int = 0
    degraded: bool = False
    reason_code: str | None = None
    trace: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = super().as_dict()
        payload.update(
            {
                "query": self.query,
                "rows": [row.as_dict() for row in self.rows],
                "source_refs": list(self.source_refs),
                "route_ids": list(self.route_ids),
                "candidate_count": int(self.candidate_count),
                "degraded": bool(self.degraded),
                "reason_code": self.reason_code,
                "trace": dict(self.trace or {}),
            }
        )
        return payload


@dataclass(frozen=True)
class SurfaceMemoryWriteResult:
    success: bool
    reason_code: str | None
    detail: str
    memory_id: str | None = None
    text: str = ""
    evidence_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": bool(self.success),
            "reason_code": self.reason_code,
            "detail": self.detail,
            "memory_id": self.memory_id,
            "text": self.text,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class SurfaceMemoryRecallPlan:
    """Recall plan tailored for the current minimal surface chain."""

    should_recall: bool
    query: str
    task_mode: str
    recall_policy: str = RECALL_POLICY_NO
    target_store_ids: tuple[str, ...] = ()
    max_facts: int = 0
    max_chars_per_fact: int = 96
    time_lookback_days: int | None = None
    reason: str = ""
    query_kind: str = ""
    temporal_scope: str = ""
    target_domains: tuple[str, ...] = ()
    validation_requirements: tuple[str, ...] = ()
    recall_contract_id: str = ""
    primary_memory_layer: str = ""
    expected_memory_layers: tuple[str, ...] = ()
    min_collection_items: int = 0
    answer_obligations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "should_recall": bool(self.should_recall),
            "query": self.query,
            "task_mode": self.task_mode,
            "recall_policy": self.recall_policy,
            "target_store_ids": list(self.target_store_ids),
            "max_facts": int(self.max_facts),
            "max_chars_per_fact": int(self.max_chars_per_fact),
            "time_lookback_days": self.time_lookback_days,
            "reason": self.reason,
            "query_kind": self.query_kind,
            "temporal_scope": self.temporal_scope,
            "target_domains": list(self.target_domains),
            "validation_requirements": list(self.validation_requirements),
            "recall_contract_id": self.recall_contract_id,
            "primary_memory_layer": self.primary_memory_layer,
            "expected_memory_layers": list(self.expected_memory_layers),
            "min_collection_items": int(self.min_collection_items or 0),
            "answer_obligations": list(self.answer_obligations),
        }


def build_surface_memory_recall_query(
    context: ThreeLayerContinuityContext,
    *,
    max_chars: int = 240,
) -> str:
    """Build a recall query from compressed main-chain inputs only."""

    parts = _clean_list(
        [
            context.current_user_message,
            *_clean_list(context.necessary_facts)[:2],
            _clean_text(context.mid_term_summary),
        ]
    )
    return _truncate_text("；".join(parts), limit=max_chars)


def plan_surface_memory_recall(
    context: ThreeLayerContinuityContext,
    *,
    max_query_chars: int = 240,
) -> SurfaceMemoryRecallPlan:
    task_mode = _clean_text(context.task_mode_hint).lower() or "daily_chat"
    user_message = _clean_text(context.current_user_message)
    query = build_surface_memory_recall_query(context, max_chars=max_query_chars)
    anaphoric_selection = _looks_like_anaphoric_selection_query(user_message)
    life_query_plan = plan_life_memory_query(user_message, task_mode=task_mode)
    has_recall_hint = _has_recall_hint(user_message) or anaphoric_selection or life_query_plan.should_recall

    if not user_message:
        return SurfaceMemoryRecallPlan(
            should_recall=False,
            query=query,
            task_mode=task_mode,
            recall_policy=RECALL_POLICY_NO,
            reason="empty_user_message",
            query_kind=life_query_plan.query_kind,
            temporal_scope=life_query_plan.temporal_scope,
            target_domains=life_query_plan.target_domains,
            validation_requirements=life_query_plan.validation_requirements,
            recall_contract_id=life_query_plan.recall_contract_id,
            primary_memory_layer=life_query_plan.primary_memory_layer,
            expected_memory_layers=life_query_plan.expected_memory_layers,
            min_collection_items=life_query_plan.min_collection_items,
            answer_obligations=life_query_plan.answer_obligations,
        )
    if _should_skip_low_signal_recall(user_message=user_message, task_mode=task_mode, has_recall_hint=has_recall_hint):
        return SurfaceMemoryRecallPlan(
            should_recall=False,
            query=query,
            task_mode=task_mode,
            recall_policy=RECALL_POLICY_NO,
            reason="low_signal_surface_turn",
            query_kind=life_query_plan.query_kind,
            temporal_scope=life_query_plan.temporal_scope,
            target_domains=life_query_plan.target_domains,
            validation_requirements=life_query_plan.validation_requirements,
            recall_contract_id=life_query_plan.recall_contract_id,
            primary_memory_layer=life_query_plan.primary_memory_layer,
            expected_memory_layers=life_query_plan.expected_memory_layers,
            min_collection_items=life_query_plan.min_collection_items,
            answer_obligations=life_query_plan.answer_obligations,
        )
    if task_mode in _MEMORY_WRITE_TASKS or _looks_like_memory_write(user_message):
        return SurfaceMemoryRecallPlan(
            should_recall=False,
            query=query,
            task_mode=task_mode,
            recall_policy=RECALL_POLICY_NO,
            reason="memory_write_current_turn_only",
            query_kind=life_query_plan.query_kind,
            temporal_scope=life_query_plan.temporal_scope,
            target_domains=life_query_plan.target_domains,
            validation_requirements=life_query_plan.validation_requirements,
            recall_contract_id=life_query_plan.recall_contract_id,
            primary_memory_layer=life_query_plan.primary_memory_layer,
            expected_memory_layers=life_query_plan.expected_memory_layers,
            min_collection_items=life_query_plan.min_collection_items,
            answer_obligations=life_query_plan.answer_obligations,
        )
    if not life_query_plan.should_recall and life_query_plan.reason in {"response_style_feedback_current_turn"}:
        return SurfaceMemoryRecallPlan(
            should_recall=False,
            query=query,
            task_mode=task_mode,
            recall_policy=RECALL_POLICY_NO,
            reason=life_query_plan.reason,
            query_kind=life_query_plan.query_kind,
            temporal_scope=life_query_plan.temporal_scope,
            target_domains=life_query_plan.target_domains,
            validation_requirements=life_query_plan.validation_requirements,
            recall_contract_id=life_query_plan.recall_contract_id,
            primary_memory_layer=life_query_plan.primary_memory_layer,
            expected_memory_layers=life_query_plan.expected_memory_layers,
            min_collection_items=life_query_plan.min_collection_items,
            answer_obligations=life_query_plan.answer_obligations,
        )

    if life_query_plan.should_recall and life_query_plan.reason == "collection_memory_recall":
        return SurfaceMemoryRecallPlan(
            should_recall=True,
            query=query,
            task_mode=task_mode,
            recall_policy=RECALL_POLICY_MUST,
            target_store_ids=life_query_plan.target_store_ids,
            max_facts=life_query_plan.max_facts,
            max_chars_per_fact=life_query_plan.max_chars_per_fact,
            time_lookback_days=life_query_plan.time_lookback_days,
            reason=life_query_plan.reason,
            query_kind=life_query_plan.query_kind,
            temporal_scope=life_query_plan.temporal_scope,
            target_domains=life_query_plan.target_domains,
            validation_requirements=life_query_plan.validation_requirements,
            recall_contract_id=life_query_plan.recall_contract_id,
            primary_memory_layer=life_query_plan.primary_memory_layer,
            expected_memory_layers=life_query_plan.expected_memory_layers,
            min_collection_items=life_query_plan.min_collection_items,
            answer_obligations=life_query_plan.answer_obligations,
        )

    if anaphoric_selection:
        return SurfaceMemoryRecallPlan(
            should_recall=True,
            query=query,
            task_mode=task_mode,
            recall_policy=RECALL_POLICY_MUST,
            target_store_ids=life_query_plan.target_store_ids or (
                "life_memory_collection",
                "life_memory_entity",
                "life_episode",
                "episodic_vector",
                "sqlite_ledger",
                "semantic_memory",
            ),
            max_facts=max(4, life_query_plan.max_facts),
            max_chars_per_fact=max(700, life_query_plan.max_chars_per_fact),
            time_lookback_days=(
                life_query_plan.time_lookback_days
                if life_query_plan.time_lookback_days is not None or life_query_plan.primary_memory_layer == "life_memory_entity"
                else 1
            ),
            reason="anaphoric_selection_recall",
            query_kind=life_query_plan.query_kind,
            temporal_scope=life_query_plan.temporal_scope,
            target_domains=life_query_plan.target_domains,
            validation_requirements=life_query_plan.validation_requirements,
            recall_contract_id=life_query_plan.recall_contract_id,
            primary_memory_layer=life_query_plan.primary_memory_layer,
            expected_memory_layers=life_query_plan.expected_memory_layers,
            min_collection_items=life_query_plan.min_collection_items,
            answer_obligations=life_query_plan.answer_obligations,
        )

    if task_mode in _RECALL_TASKS or has_recall_hint:
        return SurfaceMemoryRecallPlan(
            should_recall=True,
            query=query,
            task_mode=task_mode,
            recall_policy=RECALL_POLICY_MUST,
            target_store_ids=life_query_plan.target_store_ids or (
                "life_episode",
                "semantic_memory",
                "episodic_vector",
                "sqlite_ledger",
                "neo4j_bond",
            ),
            max_facts=max(4, life_query_plan.max_facts),
            max_chars_per_fact=max(96, life_query_plan.max_chars_per_fact),
            time_lookback_days=life_query_plan.time_lookback_days if life_query_plan.should_recall else _infer_lookback_days(user_message),
            reason=life_query_plan.reason if life_query_plan.should_recall else "explicit_memory_recall",
            query_kind=life_query_plan.query_kind,
            temporal_scope=life_query_plan.temporal_scope,
            target_domains=life_query_plan.target_domains,
            validation_requirements=life_query_plan.validation_requirements,
            recall_contract_id=life_query_plan.recall_contract_id,
            primary_memory_layer=life_query_plan.primary_memory_layer,
            expected_memory_layers=life_query_plan.expected_memory_layers,
            min_collection_items=life_query_plan.min_collection_items,
            answer_obligations=life_query_plan.answer_obligations,
        )
    if task_mode in _TECH_TASKS:
        return SurfaceMemoryRecallPlan(
            should_recall=True,
            query=query,
            task_mode=task_mode,
            recall_policy=RECALL_POLICY_MAY,
            target_store_ids=("life_episode", "semantic_memory", "episodic_vector", "sqlite_ledger"),
            max_facts=3,
            max_chars_per_fact=96,
            time_lookback_days=_infer_lookback_days(user_message),
            reason="technical_user_memory_context",
            query_kind=life_query_plan.query_kind,
            temporal_scope=life_query_plan.temporal_scope,
            target_domains=life_query_plan.target_domains,
            validation_requirements=life_query_plan.validation_requirements,
            recall_contract_id=life_query_plan.recall_contract_id,
            primary_memory_layer=life_query_plan.primary_memory_layer,
            expected_memory_layers=life_query_plan.expected_memory_layers,
            min_collection_items=life_query_plan.min_collection_items,
            answer_obligations=life_query_plan.answer_obligations,
        )
    if task_mode in _EMOTION_TASKS:
        return SurfaceMemoryRecallPlan(
            should_recall=True,
            query=query,
            task_mode=task_mode,
            recall_policy=RECALL_POLICY_MAY,
            target_store_ids=("life_episode", "neo4j_bond", "semantic_memory", "episodic_vector"),
            max_facts=3,
            max_chars_per_fact=96,
            time_lookback_days=_infer_lookback_days(user_message),
            reason="emotion_or_relationship_context",
            query_kind=life_query_plan.query_kind,
            temporal_scope=life_query_plan.temporal_scope,
            target_domains=life_query_plan.target_domains,
            validation_requirements=life_query_plan.validation_requirements,
            recall_contract_id=life_query_plan.recall_contract_id,
            primary_memory_layer=life_query_plan.primary_memory_layer,
            expected_memory_layers=life_query_plan.expected_memory_layers,
            min_collection_items=life_query_plan.min_collection_items,
            answer_obligations=life_query_plan.answer_obligations,
        )
    if task_mode in _IDENTITY_TASKS:
        return SurfaceMemoryRecallPlan(
            should_recall=True,
            query=query,
            task_mode=task_mode,
            recall_policy=RECALL_POLICY_MAY,
            target_store_ids=("life_episode", "neo4j_bond", "semantic_memory", "episodic_vector"),
            max_facts=2,
            max_chars_per_fact=88,
            time_lookback_days=None,
            reason="identity_relationship_context",
            query_kind=life_query_plan.query_kind,
            temporal_scope=life_query_plan.temporal_scope,
            target_domains=life_query_plan.target_domains,
            validation_requirements=life_query_plan.validation_requirements,
            recall_contract_id=life_query_plan.recall_contract_id,
            primary_memory_layer=life_query_plan.primary_memory_layer,
            expected_memory_layers=life_query_plan.expected_memory_layers,
            min_collection_items=life_query_plan.min_collection_items,
            answer_obligations=life_query_plan.answer_obligations,
        )
    return SurfaceMemoryRecallPlan(
        should_recall=False,
        query=query,
        task_mode=task_mode,
        recall_policy=RECALL_POLICY_NO,
        reason="no_memory_recall_needed",
        query_kind=life_query_plan.query_kind,
        temporal_scope=life_query_plan.temporal_scope,
        target_domains=life_query_plan.target_domains,
        validation_requirements=life_query_plan.validation_requirements,
        recall_contract_id=life_query_plan.recall_contract_id,
        primary_memory_layer=life_query_plan.primary_memory_layer,
        expected_memory_layers=life_query_plan.expected_memory_layers,
        min_collection_items=life_query_plan.min_collection_items,
        answer_obligations=life_query_plan.answer_obligations,
    )


def recall_surface_memory_for_context(
    *,
    recall_service: Any,
    context: ThreeLayerContinuityContext,
    request_id: str,
    query_embedding: Sequence[float] | None,
    intent_id: str | None = None,
    person_id: str | None = None,
    session_id: str | None = None,
    ts_ms: int | None = None,
    emotion_filter: str | None = None,
    mood_state: str | None = None,
    bond_summary: Mapping[str, Any] | None = None,
    motive_state: Mapping[str, Any] | None = None,
    render_target_lang: str | None = "zh",
    fail_soft: bool = True,
) -> SurfaceMemoryRecallPacket:
    plan = plan_surface_memory_recall(context)
    if not plan.should_recall:
        return SurfaceMemoryRecallPacket(
            query=plan.query,
            degraded=False,
            reason_code=SURFACE_MEMORY_RECALL_SKIPPED,
            trace={
                "skipped": True,
                "plan": plan.as_dict(),
                "recall_decision": build_surface_memory_recall_decision_trace(plan, invoked=False),
            },
        )
    packet = recall_surface_memory_packet(
        recall_service=recall_service,
        request_id=request_id,
        query=plan.query,
        query_embedding=list(query_embedding) if query_embedding is not None else [],
        intent_id=intent_id,
        person_id=person_id,
        session_id=session_id,
        ts_ms=ts_ms,
        emotion_filter=emotion_filter,
        mood_state=mood_state,
        bond_summary=bond_summary,
        motive_state=motive_state,
        render_target_lang=render_target_lang,
        target_store_ids=plan.target_store_ids,
        time_lookback_days=plan.time_lookback_days,
        max_facts=plan.max_facts,
        max_chars_per_fact=plan.max_chars_per_fact,
        validation_requirements=plan.validation_requirements,
        temporal_scope=plan.temporal_scope,
        query_kind=plan.query_kind,
        target_domains=plan.target_domains,
        recall_contract_id=plan.recall_contract_id,
        primary_memory_layer=plan.primary_memory_layer,
        expected_memory_layers=plan.expected_memory_layers,
        min_collection_items=plan.min_collection_items,
        answer_obligations=plan.answer_obligations,
        fail_soft=fail_soft,
    )
    return replace(
        packet,
        trace={
            **dict(packet.trace or {}),
            "skipped": False,
            "plan": plan.as_dict(),
            "recall_decision": build_surface_memory_recall_decision_trace(plan, invoked=True),
        },
    )


def recall_surface_memory_packet(
    *,
    recall_service: Any,
    request_id: str,
    query: str,
    query_embedding: Sequence[float],
    intent_id: str | None = None,
    person_id: str | None = None,
    session_id: str | None = None,
    ts_ms: int | None = None,
    emotion_filter: str | None = None,
    mood_state: str | None = None,
    bond_summary: Mapping[str, Any] | None = None,
    motive_state: Mapping[str, Any] | None = None,
    render_target_lang: str | None = "zh",
    target_store_ids: Sequence[str] | None = None,
    time_lookback_days: int | None = None,
    max_facts: int = 4,
    max_chars_per_fact: int = 96,
    validation_requirements: Sequence[str] | None = None,
    temporal_scope: str = "",
    query_kind: str = "",
    target_domains: Sequence[str] | None = None,
    recall_contract_id: str = "",
    primary_memory_layer: str = "",
    expected_memory_layers: Sequence[str] | None = None,
    min_collection_items: int = 0,
    answer_obligations: Sequence[str] | None = None,
    fail_soft: bool = True,
) -> SurfaceMemoryRecallPacket:
    try:
        result = recall_service.recall(
            request_id=request_id,
            query=query,
            query_embedding=list(query_embedding) if query_embedding is not None else [],
            intent_id=intent_id,
            person_id=person_id,
            session_id=session_id,
            ts_ms=ts_ms,
            emotion_filter=emotion_filter,
            mood_state=mood_state,
            bond_summary=bond_summary,
            motive_state=motive_state,
            render_target_lang=render_target_lang,
            target_store_ids=list(target_store_ids or []) or None,
            time_lookback_days=time_lookback_days,
        )
    except Exception as exc:
        if not fail_soft:
            raise
        return SurfaceMemoryRecallPacket(
            query=_clean_text(query),
            degraded=True,
            reason_code=SURFACE_MEMORY_RECALL_FAILED,
            trace={
                "error_type": type(exc).__name__,
                "detail": _truncate_text(str(exc), limit=160),
            },
        )
    return build_surface_memory_recall_packet(
        result,
        query=query,
        max_facts=max_facts,
        max_chars_per_fact=max_chars_per_fact,
        validation_requirements=validation_requirements,
        temporal_scope=temporal_scope,
        query_kind=query_kind,
        target_domains=target_domains,
        recall_contract_id=recall_contract_id,
        primary_memory_layer=primary_memory_layer,
        expected_memory_layers=expected_memory_layers,
        min_collection_items=min_collection_items,
        answer_obligations=answer_obligations,
    )


def build_surface_memory_recall_packet(
    recall_result: Any,
    *,
    query: str | None = None,
    max_facts: int = 4,
    max_chars_per_fact: int = 96,
    validation_requirements: Sequence[str] | None = None,
    temporal_scope: str = "",
    query_kind: str = "",
    target_domains: Sequence[str] | None = None,
    recall_contract_id: str = "",
    primary_memory_layer: str = "",
    expected_memory_layers: Sequence[str] | None = None,
    min_collection_items: int = 0,
    answer_obligations: Sequence[str] | None = None,
) -> SurfaceMemoryRecallPacket:
    output = _recall_output(recall_result)
    candidates = _candidate_list(output)
    candidates_for_temporal_scope = _filter_candidates_for_temporal_scope(
        candidates,
        temporal_scope=temporal_scope,
        ts_ms=_coerce_int(output.get("ts_ms")),
    )
    candidates_for_selection = _filter_candidates_for_target_domains(
        candidates_for_temporal_scope,
        target_domains=target_domains,
        query_kind=query_kind,
        primary_memory_layer=primary_memory_layer,
    )
    candidates_for_selection = _filter_candidates_for_collection_count_contract(
        candidates_for_selection,
        temporal_scope=temporal_scope,
        validation_requirements=validation_requirements,
        min_collection_items=min_collection_items,
    )
    rows = _select_fact_rows(
        candidates_for_selection,
        query=query,
        max_facts=max_facts,
        max_chars_per_fact=max_chars_per_fact,
    )
    evidence_refs = tuple(_unique(row.evidence_ref for row in rows if row.evidence_ref))
    source_refs = tuple(_unique(row.source_ref for row in rows if row.source_ref))
    route_ids = tuple(_extract_route_ids(output, allowed_store_ids=SURFACE_MAIN_CHAIN_RECALL_STORES))
    memory_rows = tuple(row for row in rows if _is_user_fact_store(row.store_id))
    relationship_rows = tuple(row for row in rows if _is_relationship_store(row.store_id))
    relationship_clues = tuple(row.fact for row in relationship_rows)
    must_not_do = tuple(
        _build_packet_must_not_do(
            candidates=candidates,
            rows=rows,
            degraded=bool(output.get("degraded", False)),
            query=query,
        )
    )
    style_anchor = tuple(_build_packet_style_anchor(rows, query=query))
    store_counts: dict[str, int] = {}
    for row in rows:
        store_counts[row.store_id] = store_counts.get(row.store_id, 0) + 1
    validation_trace = _build_recall_validation_trace(
        candidates=candidates_for_selection,
        rows=rows,
        output=output,
        query=query if query is not None else output.get("query"),
        validation_requirements=validation_requirements,
        temporal_scope=temporal_scope,
        query_kind=query_kind,
        target_domains=target_domains,
        recall_contract_id=recall_contract_id,
        primary_memory_layer=primary_memory_layer,
        expected_memory_layers=expected_memory_layers,
        min_collection_items=min_collection_items,
        answer_obligations=answer_obligations,
    )
    validation_failed = bool(validation_trace.get("required")) and not bool(validation_trace.get("passed"))
    trace = {
        "status": _clean_text(output.get("status")),
        "stage": _clean_text(output.get("stage")),
        "route_count": len(route_ids),
        "route_error_count": len(list(output.get("route_errors") or [])),
        "candidate_count_before_temporal_filter": len(candidates),
        "candidate_count_after_temporal_filter": len(candidates_for_temporal_scope),
        "candidate_count_after_domain_filter": len(candidates_for_selection),
        "selected_fact_count": len(rows),
        "selected_user_fact_count": len(memory_rows),
        "selected_bond_count": len(relationship_rows),
        "store_counts": dict(sorted(store_counts.items())),
        "recall_contract_id": _clean_text(recall_contract_id),
        "primary_memory_layer": _clean_text(primary_memory_layer),
        "expected_memory_layers": list(_unique(list(expected_memory_layers or []))),
        "min_collection_items": max(0, _coerce_int(min_collection_items)),
        "answer_obligations": list(_unique(list(answer_obligations or []))),
    }
    selected_collection_items = _selected_collection_items_for_trace(
        candidates=candidates_for_selection,
        rows=rows,
    )
    if selected_collection_items:
        trace["selected_collection_items"] = selected_collection_items
    selected_entity_items = _selected_entity_items_for_trace(
        candidates=candidates_for_selection,
        rows=rows,
    )
    if selected_entity_items:
        trace["selected_entity_items"] = selected_entity_items
    semantic_usage = _build_semantic_usage_trace(rows, query=query if query is not None else output.get("query"))
    if semantic_usage:
        trace["semantic_usage"] = semantic_usage
    if validation_trace["required"]:
        trace["validation"] = validation_trace
    recall_need = _build_recall_need_trace(
        query_kind=query_kind,
        temporal_scope=temporal_scope,
        target_domains=target_domains,
        recall_contract_id=recall_contract_id,
        primary_memory_layer=primary_memory_layer,
        expected_memory_layers=expected_memory_layers,
        min_collection_items=min_collection_items,
        validation_requirements=validation_requirements,
        answer_obligations=answer_obligations,
    )
    trace["recall_need"] = recall_need
    trace["recall_receipt"] = _build_recall_receipt_trace(
        candidates=candidates_for_selection,
        rows=rows,
        query=query if query is not None else output.get("query"),
        validation_trace=validation_trace,
        temporal_scope=temporal_scope,
        query_kind=query_kind,
        target_domains=target_domains,
        recall_contract_id=recall_contract_id,
        primary_memory_layer=primary_memory_layer,
        expected_memory_layers=expected_memory_layers,
        min_collection_items=min_collection_items,
        answer_obligations=answer_obligations,
        selected_collection_items=selected_collection_items,
        selected_entity_items=selected_entity_items,
        ts_ms=_coerce_int(output.get("ts_ms")),
    )
    trace["recall_coverage"] = _build_recall_coverage_trace(
        recall_need=recall_need,
        recall_receipt=trace["recall_receipt"],
        validation_trace=validation_trace,
    )
    trace["coverage_score"] = trace["recall_coverage"]["score"]
    coverage_failed = (not validation_failed) and not bool(trace["recall_coverage"].get("passed", True))
    answer_readiness = _build_recall_answer_readiness_trace(
        recall_need=recall_need,
        recall_receipt=trace["recall_receipt"],
        recall_coverage=trace["recall_coverage"],
        validation_trace=validation_trace,
        selected_fact_count=len(rows),
        selected_user_fact_count=len(memory_rows),
        selected_bond_count=len(relationship_rows),
        validation_failed=validation_failed,
        coverage_failed=coverage_failed,
    )
    trace["recall_answer_readiness"] = answer_readiness
    trace["recall_answer_strategy"] = _build_recall_answer_strategy_trace(
        recall_need=recall_need,
        recall_receipt=trace["recall_receipt"],
        recall_coverage=trace["recall_coverage"],
        answer_readiness=answer_readiness,
    )
    trace["answer_ready"] = bool(answer_readiness.get("ready_to_answer"))
    if validation_failed or coverage_failed:
        trace["validation_failed"] = bool(validation_failed)
        if coverage_failed:
            trace["coverage_failed"] = True
        memory_rows = ()
        relationship_clues = ()
        style_anchor = ()
        evidence_refs = ()
        source_refs = ()
        failure_rules: list[str] = []
        if validation_failed:
            failure_rules.append("召回校验失败时，不要把未通过时间、对象或证据校验的候选当事实；只能说没有找到足够可靠的记忆。")
        if coverage_failed:
            failure_rules.append("召回覆盖度不足时，不要把局部命中的候选当成完整记忆；需要说明没有找到足够贴合这次问题的记忆。")
        must_not_do = tuple(
            _unique(
                [
                    *must_not_do,
                    *failure_rules,
                ]
            )
        )
    reason_code = _clean_text(output.get("reason_code")) or None
    if validation_failed:
        reason_code = SURFACE_MEMORY_RECALL_VALIDATION_FAILED
    elif coverage_failed:
        reason_code = SURFACE_MEMORY_RECALL_COVERAGE_FAILED
    return SurfaceMemoryRecallPacket(
        query=_clean_text(query if query is not None else output.get("query")),
        facts=tuple(row.fact for row in memory_rows),
        style_anchor=style_anchor,
        relationship_clues=relationship_clues,
        must_not_do=must_not_do,
        confidence=0.0 if (validation_failed or coverage_failed) else _packet_confidence(rows, output=output),
        rows=tuple(rows),
        evidence_refs=evidence_refs,
        source_refs=source_refs,
        route_ids=route_ids,
        candidate_count=_coerce_int(output.get("candidate_count"), default=len(candidates)),
        degraded=bool(output.get("degraded", False)) or validation_failed or coverage_failed,
        reason_code=reason_code,
        trace=trace,
    )


def _build_recall_need_trace(
    *,
    query_kind: str,
    temporal_scope: str,
    target_domains: Sequence[str] | None,
    recall_contract_id: str,
    primary_memory_layer: str,
    expected_memory_layers: Sequence[str] | None,
    min_collection_items: int,
    validation_requirements: Sequence[str] | None,
    answer_obligations: Sequence[str] | None,
) -> dict[str, Any]:
    kind = _clean_text(query_kind)
    contract = _clean_text(recall_contract_id)
    obligations = list(_unique(list(answer_obligations or [])))
    required = list(_unique(list(validation_requirements or [])))
    expected_layers = list(_unique(list(expected_memory_layers or [])))
    domains = list(_unique(list(target_domains or [])))
    primary = _clean_text(primary_memory_layer)
    evidence_depth = "memory_hit"
    if any(item in obligations for item in ("use_detail_or_body_evidence", "read_detail_evidence_before_deep_quality_judgment")):
        evidence_depth = "detail_or_body_evidence"
    elif "evidence_refs_present" in required:
        evidence_depth = "evidence_anchor"
    need_type = _recall_need_type(kind=kind, contract=contract, primary_layer=primary)
    grain = _recall_grain_trace(
        need_type=need_type,
        query_kind=kind,
        contract_id=contract,
        primary_memory_layer=primary,
        evidence_depth=evidence_depth,
        temporal_scope=_clean_text(temporal_scope),
        min_collection_items=min_collection_items,
    )
    return {
        "schema": "Natsume.surface_recall_need.v1",
        "query_kind": kind,
        "temporal_scope": _clean_text(temporal_scope),
        "target_domains": domains,
        "recall_contract_id": contract,
        "need_type": need_type,
        "recall_grain": grain["recall_grain"],
        "retrieval_unit": grain["retrieval_unit"],
        "answer_scope": grain["answer_scope"],
        "verification_gate": grain["verification_gate"],
        "primary_memory_layer": primary,
        "expected_memory_layers": expected_layers,
        "min_collection_items": max(0, _coerce_int(min_collection_items)),
        "validation_requirements": required,
        "answer_obligations": obligations,
        "evidence_depth": evidence_depth,
    }


def _recall_need_type(*, kind: str, contract: str, primary_layer: str) -> str:
    if contract in {"collection_date", "collection_recent"} or kind == "collection_summary":
        return "collection"
    if kind == "anaphoric_selection":
        return "entity_or_collection_selection" if primary_layer == "life_memory_entity" else "collection_selection"
    if kind == "failure_debug":
        return "failure_episode"
    if kind == "preference_lookup":
        return "semantic_or_episode_preference"
    if kind == "open_loop" or contract == "open_loop":
        return "open_loop_memory"
    if kind == "correction":
        return "correction_chain"
    if kind == "self_change":
        return "self_change_memory"
    if kind == "event_lookup" or contract == "event_lookup":
        return "episode_event"
    if kind == "evidence_review" or contract == "evidence_review":
        return "evidence_review"
    return "memory_hit"


def _recall_grain_trace(
    *,
    need_type: str,
    query_kind: str,
    contract_id: str,
    primary_memory_layer: str,
    evidence_depth: str,
    temporal_scope: str,
    min_collection_items: int,
) -> dict[str, str]:
    """Describe the intended recall granularity in audit-friendly terms."""

    need = _clean_text(need_type)
    kind = _clean_text(query_kind)
    contract = _clean_text(contract_id)
    primary = _clean_text(primary_memory_layer)
    depth = _clean_text(evidence_depth)
    temporal = _clean_text(temporal_scope)
    min_items = max(0, _coerce_int(min_collection_items))

    if need == "collection":
        gate = "scope_match+collection_items_present+evidence_refs_present"
        if min_items > 0:
            gate += f"+min_items:{min_items}"
        return {
            "recall_grain": "collection",
            "retrieval_unit": "life_memory_collection.items",
            "answer_scope": "list_or_summarize_requested_collection",
            "verification_gate": gate,
        }
    if need == "collection_selection":
        gate = "collection_or_episode_hit+evidence_refs_present+no_self_echo"
        if min_items > 0:
            gate += f"+min_items:{min_items}"
        if depth == "detail_or_body_evidence":
            gate += "+detail_or_body_evidence_before_quality_judgment"
        return {
            "recall_grain": "collection_selection",
            "retrieval_unit": "life_memory_collection.items",
            "answer_scope": "compare_recalled_items_with_detail_gate",
            "verification_gate": gate,
        }
    if need == "entity_or_collection_selection":
        gate = "entity_or_collection_hit+evidence_refs_present+no_self_echo"
        if depth == "detail_or_body_evidence":
            gate += "+detail_or_body_evidence_before_quality_judgment"
        return {
            "recall_grain": "entity_selection",
            "retrieval_unit": "life_memory_entity.with_source_collection",
            "answer_scope": "judge_named_or_referenced_item_only",
            "verification_gate": gate,
        }
    if need == "evidence_review":
        return {
            "recall_grain": "evidence",
            "retrieval_unit": "life_episode.evidence",
            "answer_scope": "judge_claim_from_bound_evidence",
            "verification_gate": "trusted_memory_hit+source_ref_present+target_domain_match+detail_or_body_evidence",
        }
    if need in {"episode_event", "failure_episode"}:
        return {
            "recall_grain": "episode",
            "retrieval_unit": "life_episode",
            "answer_scope": "answer_action_result_or_failure_cause",
            "verification_gate": "event_or_failure_episode_hit+source_ref_present",
        }
    if need == "correction_chain":
        return {
            "recall_grain": "correction_chain",
            "retrieval_unit": "life_memory_correction+life_episode",
            "answer_scope": "prefer_latest_trusted_correction",
            "verification_gate": "correction_state_checked+suppressed_not_fact",
        }
    if need in {"semantic_or_episode_preference", "open_loop_memory", "self_change_memory"}:
        return {
            "recall_grain": "semantic_first",
            "retrieval_unit": "semantic_memory.with_life_episode_provenance",
            "answer_scope": "state_stable_rule_status_or_self_change",
            "verification_gate": "trusted_memory_hit+source_ref_present",
        }
    if contract == "no_recall" or kind == "fresh_topic" or not primary:
        return {
            "recall_grain": "none",
            "retrieval_unit": "none",
            "answer_scope": "answer_current_turn_only",
            "verification_gate": "no_recall_hint",
        }
    return {
        "recall_grain": primary or "memory",
        "retrieval_unit": primary or "memory",
        "answer_scope": f"answer_from_{need or 'memory'}",
        "verification_gate": f"temporal:{temporal or 'unspecified'}+evidence:{depth or 'memory_hit'}",
    }


def build_surface_memory_recall_decision_trace(
    plan: SurfaceMemoryRecallPlan,
    *,
    invoked: bool | None = None,
) -> dict[str, Any]:
    """Explain why the surface chain will or will not recall memory."""

    need = _build_recall_need_trace(
        query_kind=plan.query_kind,
        temporal_scope=plan.temporal_scope,
        target_domains=plan.target_domains,
        recall_contract_id=plan.recall_contract_id,
        primary_memory_layer=plan.primary_memory_layer,
        expected_memory_layers=plan.expected_memory_layers,
        min_collection_items=plan.min_collection_items,
        validation_requirements=plan.validation_requirements,
        answer_obligations=plan.answer_obligations,
    )
    will_recall = bool(plan.should_recall)
    invoked_value = will_recall if invoked is None else bool(invoked)
    basis = _clean_list(
        [
            f"policy:{plan.recall_policy}" if plan.recall_policy else "",
            f"reason:{plan.reason}" if plan.reason else "",
            f"query_kind:{plan.query_kind}" if plan.query_kind else "",
            f"temporal_scope:{plan.temporal_scope}" if plan.temporal_scope else "",
            f"contract:{plan.recall_contract_id}" if plan.recall_contract_id else "",
            f"need_type:{need.get('need_type')}" if need.get("need_type") else "",
            f"recall_grain:{need.get('recall_grain')}" if need.get("recall_grain") else "",
            f"primary_layer:{plan.primary_memory_layer}" if plan.primary_memory_layer else "",
            f"stores:{','.join(plan.target_store_ids)}" if plan.target_store_ids else "",
            f"domains:{','.join(plan.target_domains)}" if plan.target_domains else "",
            f"evidence_depth:{need.get('evidence_depth')}" if need.get("evidence_depth") else "",
            f"min_items:{int(plan.min_collection_items or 0)}" if int(plan.min_collection_items or 0) > 0 else "",
        ]
    )
    return {
        "schema": "Natsume.surface_recall_decision.v1",
        "decision": "recall" if will_recall else "skip",
        "invoked": invoked_value,
        "skip_reason": "" if will_recall else _clean_text(plan.reason),
        "recall_policy": _clean_text(plan.recall_policy),
        "reason": _clean_text(plan.reason),
        "task_mode": _clean_text(plan.task_mode),
        "query_kind": _clean_text(plan.query_kind),
        "temporal_scope": _clean_text(plan.temporal_scope),
        "recall_contract_id": _clean_text(plan.recall_contract_id),
        "need_type": _clean_text(need.get("need_type")),
        "recall_grain": _clean_text(need.get("recall_grain")),
        "retrieval_unit": _clean_text(need.get("retrieval_unit")),
        "answer_scope": _clean_text(need.get("answer_scope")),
        "verification_gate": _clean_text(need.get("verification_gate")),
        "primary_memory_layer": _clean_text(plan.primary_memory_layer),
        "expected_memory_layers": list(_unique(list(plan.expected_memory_layers or []))),
        "target_store_ids": list(_unique(list(plan.target_store_ids or []))),
        "target_domains": list(_unique(list(plan.target_domains or []))),
        "validation_requirements": list(_unique(list(plan.validation_requirements or []))),
        "answer_obligations": list(_unique(list(plan.answer_obligations or []))),
        "evidence_depth": _clean_text(need.get("evidence_depth")),
        "min_collection_items": max(0, _coerce_int(plan.min_collection_items)),
        "time_lookback_days": plan.time_lookback_days,
        "max_facts": max(0, _coerce_int(plan.max_facts)),
        "max_chars_per_fact": max(0, _coerce_int(plan.max_chars_per_fact)),
        "selection_basis": basis,
    }


def _build_recall_coverage_trace(
    *,
    recall_need: Mapping[str, Any],
    recall_receipt: Mapping[str, Any],
    validation_trace: Mapping[str, Any],
) -> dict[str, Any]:
    required = list(validation_trace.get("required") or [])
    checks = [dict(item) for item in list(validation_trace.get("checks") or []) if isinstance(item, Mapping)]
    passed_checks = [item for item in checks if bool(item.get("passed"))]
    missing = list(validation_trace.get("missing") or [])
    required_score = 1.0
    if required:
        required_score = len(passed_checks) / max(1, len(required))
    selected_store_counts = dict(recall_receipt.get("selected_store_counts") or {})
    candidate_store_counts = dict(recall_receipt.get("candidate_store_counts") or {})
    expected_layers = {
        _clean_text(item)
        for item in list(recall_need.get("expected_memory_layers") or [])
        if _clean_text(item)
    }
    primary = _clean_text(recall_need.get("primary_memory_layer"))
    selected_layers = set(selected_store_counts) | set(candidate_store_counts)
    item_required = max(0, _coerce_int(recall_need.get("min_collection_items")))
    selected_item_count = max(0, _coerce_int(recall_receipt.get("selected_item_count")))
    evidence_ref_count = max(0, _coerce_int(recall_receipt.get("evidence_ref_count")))
    source_ref_count = max(0, _coerce_int(recall_receipt.get("source_ref_count")))
    detail_evidence = dict(recall_receipt.get("detail_evidence") or {})
    query_relevance = dict(recall_receipt.get("query_relevance") or {})
    relevance_required = bool(query_relevance.get("required"))
    relevance_score = _coerce_float(query_relevance.get("score"))
    detail_required = (
        _clean_text(recall_need.get("evidence_depth")) == "detail_or_body_evidence"
        or bool(detail_evidence.get("required"))
    )
    answer_obligations = {
        _clean_text(item)
        for item in list(recall_need.get("answer_obligations") or [])
        if _clean_text(item)
    }
    body_required = detail_required and (
        _clean_text(recall_need.get("need_type")) == "evidence_review"
        or "read_detail_evidence_before_deep_quality_judgment" in answer_obligations
    )
    detail_body_count = max(0, _coerce_int(detail_evidence.get("body_item_count")))
    detail_anchor_count = max(0, _coerce_int(detail_evidence.get("anchor_item_count")))
    detail_ready = bool(detail_evidence.get("ready_for_deep_review"))
    detail_ready_for_coverage = (detail_body_count > 0) if body_required else detail_ready
    dimensions = {
        "validation": round(required_score, 6),
        "primary_layer": 1.0 if (not primary or primary in selected_layers) else 0.0,
        "expected_layer": 1.0 if (not expected_layers or bool(expected_layers.intersection(selected_layers))) else 0.0,
        "item_count": (
            min(1.0, selected_item_count / item_required)
            if item_required > 0
            else (1.0 if selected_item_count > 0 or _clean_text(recall_need.get("need_type")) not in {"collection", "collection_selection", "entity_or_collection_selection"} else 0.0)
        ),
        "evidence": 1.0 if evidence_ref_count > 0 else 0.0,
        "source": 1.0 if source_ref_count > 0 else 0.0,
    }
    if detail_required:
        dimensions["detail_evidence"] = 1.0 if detail_body_count > 0 else (0.75 if detail_ready and not body_required else 0.0)
    if relevance_required:
        dimensions["query_relevance"] = max(0.0, min(1.0, relevance_score))
    if not required:
        dimensions["validation"] = 1.0 if selected_layers else 0.0
    score = round(sum(float(value) for value in dimensions.values()) / max(1, len(dimensions)), 6)
    coverage_missing = list(missing)
    if detail_required and not detail_ready_for_coverage and "detail_evidence" not in coverage_missing:
        coverage_missing.append("detail_evidence")
    if relevance_required and relevance_score < 0.34 and "query_relevance" not in coverage_missing:
        coverage_missing.append("query_relevance")
    return {
        "schema": "Natsume.surface_recall_coverage.v1",
        "score": score,
        "passed": bool(validation_trace.get("passed", True))
        and score >= 0.66
        and (not detail_required or detail_ready_for_coverage)
        and (not relevance_required or relevance_score >= 0.34),
        "missing": coverage_missing,
        "dimensions": {key: round(float(value), 6) for key, value in dimensions.items()},
        "required_count": len(required),
        "passed_required_count": len(passed_checks),
        "selected_item_count": selected_item_count,
        "min_collection_items": item_required,
        "evidence_ref_count": evidence_ref_count,
        "source_ref_count": source_ref_count,
        "detail_evidence": detail_evidence,
        "query_relevance": query_relevance,
        "detail_body_item_count": detail_body_count,
        "detail_anchor_item_count": detail_anchor_count,
        "selected_store_counts": dict(sorted(selected_store_counts.items())),
        "candidate_store_counts": dict(sorted(candidate_store_counts.items())),
    }


def _build_recall_answer_readiness_trace(
    *,
    recall_need: Mapping[str, Any],
    recall_receipt: Mapping[str, Any],
    recall_coverage: Mapping[str, Any],
    validation_trace: Mapping[str, Any],
    selected_fact_count: int,
    selected_user_fact_count: int,
    selected_bond_count: int,
    validation_failed: bool,
    coverage_failed: bool,
) -> dict[str, Any]:
    """Answer whether the recalled memory is sufficient for this specific turn."""

    need_type = _clean_text(recall_need.get("need_type"))
    evidence_depth = _clean_text(recall_need.get("evidence_depth"))
    detail_evidence = dict(recall_coverage.get("detail_evidence") or {})
    query_relevance = dict(recall_coverage.get("query_relevance") or {})
    detail_required = evidence_depth == "detail_or_body_evidence" or bool(detail_evidence.get("required"))
    detail_body_count = max(0, _coerce_int(detail_evidence.get("body_item_count")))
    answer_obligations = {
        _clean_text(item)
        for item in list(recall_need.get("answer_obligations") or [])
        if _clean_text(item)
    }
    body_required = detail_required and (
        need_type == "evidence_review"
        or "read_detail_evidence_before_deep_quality_judgment" in answer_obligations
    )
    detail_ready = (not detail_required) or (detail_body_count > 0 if body_required else bool(detail_evidence.get("ready_for_deep_review")))
    coverage_passed = bool(recall_coverage.get("passed"))
    validation_passed = bool(validation_trace.get("passed", True))
    selected_items = max(0, _coerce_int(recall_coverage.get("selected_item_count")))
    required_items = max(0, _coerce_int(recall_coverage.get("min_collection_items")))
    required_count = max(0, _coerce_int(recall_coverage.get("required_count")))
    passed_required_count = max(0, _coerce_int(recall_coverage.get("passed_required_count")))
    missing = list(
        _unique(
            [
                *list(validation_trace.get("missing") or []),
                *list(recall_coverage.get("missing") or []),
            ]
        )
    )
    reasons: list[str] = []
    response_mode = "answer_from_memory"
    status = "READY"
    surface_action = "answer"
    recommended_rule = "可以自然承接，但只能使用已召回且通过校验的事实回答。"

    if validation_failed or not validation_passed:
        status = "NOT_READY"
        response_mode = "state_recall_validation_failed"
        surface_action = "say_missing_memory"
        reasons.append("validation_failed")
        recommended_rule = "不要把未通过时间、对象或证据校验的候选当事实；需要说明没有找到足够可靠的记忆。"
    elif coverage_failed or not coverage_passed:
        status = "NOT_READY"
        reasons.append("coverage_failed")
        surface_action = "say_missing_memory"
        response_mode = "state_recall_coverage_missing"
        recommended_rule = "不要把局部命中的候选当完整记忆；需要说明召回不足。"
        if detail_required and not detail_ready:
            response_mode = "fetch_or_request_detail_evidence"
            surface_action = "fetch_more_evidence"
            reasons.append("missing_detail_evidence")
            recommended_rule = "当前记忆只够确认发生过相关事件，不够做具体判断；需要先获取详情、正文或感知证据。"
    elif max(0, _coerce_int(selected_user_fact_count)) > 0:
        if need_type == "evidence_review":
            response_mode = "answer_from_evidence"
            recommended_rule = "可以基于召回的证据锚点回答；涉及质量判断时只引用已有详情或正文证据。"
        elif need_type in {"collection", "collection_selection", "entity_or_collection_selection"}:
            response_mode = "answer_from_collection_memory"
            recommended_rule = "可以围绕召回到的集合、条目和证据锚点回答；不要补不存在的条目。"
        elif need_type in {"episode_event", "failure_episode", "open_loop_memory", "self_change_memory", "correction_chain"}:
            response_mode = "answer_from_episode_memory"
            recommended_rule = "可以基于召回到的 episode 事实回答；纠错链以最新可信状态为准。"
    elif max(0, _coerce_int(selected_bond_count)) > 0:
        status = "PARTIAL"
        response_mode = "answer_with_relationship_context"
        surface_action = "answer_cautiously"
        reasons.append("relationship_only_no_factual_memory")
        recommended_rule = "只能用关系/风格线索承接，不能声称记得具体事实。"
    else:
        status = "NOT_READY"
        response_mode = "state_memory_missing"
        surface_action = "say_missing_memory"
        reasons.append("no_selected_memory")
        recommended_rule = "没有可用记忆时不要假装记得；应直接说明没有找到。"

    if detail_required and not detail_ready and "missing_detail_evidence" not in reasons:
        reasons.append("missing_detail_evidence")
    if bool(query_relevance.get("required")) and _coerce_float(query_relevance.get("score")) < 0.34 and "query_relevance" not in reasons:
        reasons.append("query_relevance")
    if required_items > 0 and selected_items < required_items and "insufficient_item_count" not in reasons:
        reasons.append("insufficient_item_count")
    if selected_fact_count <= 0 and "no_selected_memory" not in reasons:
        reasons.append("no_selected_memory")

    ready_to_answer = status == "READY"
    return {
        "schema": "Natsume.surface_recall_answer_readiness.v1",
        "status": status,
        "ready_to_answer": ready_to_answer,
        "response_mode": response_mode,
        "surface_action": surface_action,
        "recommended_surface_rule": recommended_rule,
        "reasons": list(_unique(reasons)),
        "need_type": need_type,
        "recall_contract_id": _clean_text(recall_need.get("recall_contract_id")),
        "query_kind": _clean_text(recall_need.get("query_kind")),
        "temporal_scope": _clean_text(recall_need.get("temporal_scope")),
        "evidence_depth": evidence_depth,
        "coverage_score": round(_coerce_float(recall_coverage.get("score")), 6),
        "coverage_passed": coverage_passed,
        "validation_passed": validation_passed,
        "validation_failed": bool(validation_failed),
        "coverage_failed": bool(coverage_failed),
        "missing": missing[:12],
        "selected_fact_count": max(0, _coerce_int(selected_fact_count)),
        "selected_user_fact_count": max(0, _coerce_int(selected_user_fact_count)),
        "selected_bond_count": max(0, _coerce_int(selected_bond_count)),
        "selected_item_count": selected_items,
        "min_collection_items": required_items,
        "required_count": required_count,
        "passed_required_count": passed_required_count,
        "detail_evidence": {
            "required": detail_required,
            "ready_for_deep_review": bool(detail_evidence.get("ready_for_deep_review")),
            "basis": _clean_text(detail_evidence.get("basis")),
            "body_item_count": detail_body_count,
            "anchor_item_count": max(0, _coerce_int(detail_evidence.get("anchor_item_count"))),
            "structured_item_count": max(0, _coerce_int(detail_evidence.get("structured_item_count"))),
        },
        "query_relevance": {
            "required": bool(query_relevance.get("required")),
            "score": round(_coerce_float(query_relevance.get("score")), 6),
            "basis": _clean_text(query_relevance.get("basis")),
            "matched": list(_unique(list(query_relevance.get("matched") or [])))[:12],
            "missing": list(_unique(list(query_relevance.get("missing") or [])))[:12],
        },
        "receipt_decision": _clean_text(recall_receipt.get("decision")),
        "receipt_selection_basis": list(_unique(list(recall_receipt.get("selection_basis") or [])))[:12],
    }


def _build_recall_answer_strategy_trace(
    *,
    recall_need: Mapping[str, Any],
    recall_receipt: Mapping[str, Any],
    recall_coverage: Mapping[str, Any],
    answer_readiness: Mapping[str, Any],
) -> dict[str, Any]:
    need_type = _clean_text(recall_need.get("need_type"))
    contract_id = _clean_text(recall_need.get("recall_contract_id"))
    query_kind = _clean_text(recall_need.get("query_kind"))
    evidence_depth = _clean_text(recall_need.get("evidence_depth"))
    readiness_status = _clean_text(answer_readiness.get("status")).upper()
    response_mode = _clean_text(answer_readiness.get("response_mode"))
    detail = dict(answer_readiness.get("detail_evidence") or {})
    detail_required = bool(detail.get("required")) or evidence_depth == "detail_or_body_evidence"
    detail_basis = _clean_text(detail.get("basis"))
    detail_body_count = max(0, _coerce_int(detail.get("body_item_count")))
    detail_anchor_count = max(0, _coerce_int(detail.get("anchor_item_count")))
    coverage_score = _coerce_float(recall_coverage.get("score"))
    selected_items = max(0, _coerce_int(answer_readiness.get("selected_item_count")))
    reasons = list(_unique(list(answer_readiness.get("reasons") or [])))
    action = "direct_answer"
    claim_scope = "memory_fact"
    evidence_level = "memory"
    confidence_band = "high" if coverage_score >= 0.82 else ("medium" if coverage_score >= 0.66 else "low")
    recommended_rule = _clean_text(answer_readiness.get("recommended_surface_rule"))

    if readiness_status == "NOT_READY":
        action = "fetch_more_evidence" if response_mode == "fetch_or_request_detail_evidence" else "say_missing_memory"
        claim_scope = "no_fact_claim"
        evidence_level = "missing"
    elif readiness_status == "PARTIAL":
        action = "answer_cautiously"
        claim_scope = "relationship_context_only"
        evidence_level = "relationship"
    elif detail_required and need_type in {"collection_selection", "entity_or_collection_selection", "evidence_review"}:
        if detail_body_count > 0 or detail_basis in {"lightnovel_deep_review", "generic_evidence_review", "body_evidence"}:
            action = "direct_answer"
            claim_scope = "detail_judgment"
            evidence_level = "detail_or_body"
        elif detail_anchor_count > 0 or detail_basis == "detail_anchor":
            action = "fetch_more_evidence"
            claim_scope = "memory_only_no_quality_judgment"
            evidence_level = "detail_anchor_only"
            if "needs_detail_fetch_before_judgment" not in reasons:
                reasons.append("needs_detail_fetch_before_judgment")
            recommended_rule = "只能确认记得这些条目和详情入口；涉及哪本更好看时，需要先读取详情页、正文或缓存深读结果。"
        else:
            action = "fetch_more_evidence"
            claim_scope = "memory_only_no_quality_judgment"
            evidence_level = "memory_anchor_only"
            if "missing_detail_evidence" not in reasons:
                reasons.append("missing_detail_evidence")
            recommended_rule = "当前只够确认发生过相关事件，不够做质量判断；需要先补详情或正文证据。"
    elif need_type in {"collection", "collection_selection", "entity_or_collection_selection"}:
        claim_scope = "collection_memory"
        evidence_level = "collection_anchor"
    elif need_type == "evidence_review":
        claim_scope = "evidence_judgment"
        evidence_level = "detail_or_body" if detail_body_count > 0 else "evidence_anchor"

    return {
        "schema": "Natsume.surface_recall_answer_strategy.v1",
        "action": action,
        "claim_scope": claim_scope,
        "evidence_level": evidence_level,
        "confidence_band": confidence_band,
        "ready_to_answer": action == "direct_answer",
        "should_fetch_more_evidence": action == "fetch_more_evidence",
        "should_state_memory_only": claim_scope == "memory_only_no_quality_judgment",
        "recall_contract_id": contract_id,
        "query_kind": query_kind,
        "need_type": need_type,
        "response_mode": response_mode,
        "coverage_score": round(coverage_score, 6),
        "selected_item_count": selected_items,
        "detail_evidence": {
            "required": detail_required,
            "basis": detail_basis,
            "body_item_count": detail_body_count,
            "anchor_item_count": detail_anchor_count,
        },
        "reasons": list(_unique(reasons)),
        "recommended_surface_rule": recommended_rule,
        "receipt_decision": _clean_text(recall_receipt.get("decision")),
    }


def _build_recall_validation_trace(
    *,
    candidates: Sequence[Mapping[str, Any]],
    rows: Sequence[SurfaceMemoryFactRow],
    output: Mapping[str, Any],
    query: Any,
    validation_requirements: Sequence[str] | None,
    temporal_scope: str,
    query_kind: str,
    target_domains: Sequence[str] | None,
    recall_contract_id: str,
    primary_memory_layer: str,
    expected_memory_layers: Sequence[str] | None,
    min_collection_items: int,
    answer_obligations: Sequence[str] | None,
) -> dict[str, Any]:
    required = [
        _clean_text(item)
        for item in list(validation_requirements or [])
        if _clean_text(item)
    ]
    candidate_store_counts = _store_counts_from_candidates(candidates)
    selected_store_counts: dict[str, int] = {}
    for row in rows:
        selected_store_counts[row.store_id] = selected_store_counts.get(row.store_id, 0) + 1
    trace = {
        "required": required,
        "passed": True,
        "missing": [],
        "checks": [],
        "query_kind": _clean_text(query_kind),
        "temporal_scope": _clean_text(temporal_scope),
        "target_domains": list(_unique(list(target_domains or []))),
        "recall_contract_id": _clean_text(recall_contract_id),
        "primary_memory_layer": _clean_text(primary_memory_layer),
        "expected_memory_layers": list(_unique(list(expected_memory_layers or []))),
        "min_collection_items": max(0, _coerce_int(min_collection_items)),
        "answer_obligations": list(_unique(list(answer_obligations or []))),
        "candidate_store_counts": dict(sorted(candidate_store_counts.items())),
        "selected_store_counts": dict(sorted(selected_store_counts.items())),
    }
    if not required:
        return trace

    missing: list[str] = []
    checks: list[dict[str, Any]] = []
    for requirement in required:
        passed, detail = _evaluate_recall_validation_requirement(
            requirement,
            candidates=candidates,
            rows=rows,
            output=output,
            query=query,
            temporal_scope=temporal_scope,
            min_collection_items=min_collection_items,
            target_domains=target_domains,
        )
        checks.append({"name": requirement, "passed": passed, "detail": detail})
        if not passed:
            missing.append(requirement)
    trace["checks"] = checks
    trace["missing"] = missing
    trace["passed"] = not missing
    return trace


def _evaluate_recall_validation_requirement(
    requirement: str,
    *,
    candidates: Sequence[Mapping[str, Any]],
    rows: Sequence[SurfaceMemoryFactRow],
    output: Mapping[str, Any],
    query: Any,
    temporal_scope: str,
    min_collection_items: int = 0,
    target_domains: Sequence[str] | None = None,
) -> tuple[bool, str]:
    if requirement == "scope_match":
        expected = _expected_collection_scope_key(temporal_scope, ts_ms=_coerce_int(output.get("ts_ms")))
        scope_keys = _candidate_scope_keys(candidates)
        if not expected:
            return (bool(candidates), "all-time or unspecified scope accepts any non-empty candidate set")
        if expected == "recent":
            if _clean_text(temporal_scope) == TEMPORAL_CURRENT:
                passed = any(key == "recent" or key.startswith("rolling:") or key.startswith("delivery:") for key in scope_keys)
                return passed, f"expected current delivery/recent/rolling collection scope, seen={scope_keys}"
            passed = any(key == "recent" or key.startswith("rolling:") for key in scope_keys)
            return passed, f"expected recent/rolling collection scope, seen={scope_keys}"
        expected_date = expected.removeprefix("daily:")
        dates = _candidate_local_dates(candidates)
        delivery_scope_for_date = any(key.startswith("delivery:") for key in scope_keys) and bool(dates) and all(date == expected_date for date in dates)
        passed = expected in scope_keys or delivery_scope_for_date
        return passed, f"expected {expected}, seen={scope_keys}, dates={dates}"
    if requirement == "collection_items_present":
        item_counts = [
            _coerce_int(_candidate_metadata(candidate).get("item_count"))
            for candidate in candidates
            if _candidate_store_id(candidate) == "life_memory_collection"
        ]
        passed = any(count > 0 for count in item_counts)
        return passed, f"collection item counts={item_counts}"
    if requirement == "collection_item_min_count":
        item_counts = _candidate_collection_item_counts(candidates)
        required_count = max(1, _coerce_int(min_collection_items))
        passed = any(count >= required_count for count in item_counts)
        return passed, f"required>={required_count}, collection item counts={item_counts}"
    if requirement == "collection_item_exact_count":
        item_counts = _candidate_collection_item_counts(candidates)
        required_count = max(1, _coerce_int(min_collection_items))
        passed = any(count == required_count for count in item_counts)
        return passed, f"required=={required_count}, collection item counts={item_counts}"
    if requirement == "evidence_refs_present":
        refs = _candidate_evidence_refs(candidates)
        if not refs:
            refs = [row.evidence_ref for row in rows if row.evidence_ref]
        return bool(refs), f"evidence refs={len(refs)}"
    if requirement == "collection_or_episode_hit":
        stores = set(_store_counts_from_candidates(candidates)) | {row.store_id for row in rows}
        passed = bool(stores.intersection({"life_memory_collection", "life_memory_entity", "life_episode"}))
        return passed, f"stores={sorted(stores)}"
    if requirement == "event_time_scope_match":
        expected = _expected_collection_scope_key(temporal_scope, ts_ms=_coerce_int(output.get("ts_ms")))
        if not expected or expected == "recent":
            return (bool(candidates or rows), "recent/all-time event scope accepts non-empty filtered candidates")
        scope_keys = _candidate_scope_keys(candidates)
        dates = _candidate_local_dates(candidates)
        expected_date = expected.removeprefix("daily:")
        passed = bool(candidates or rows) and (
            expected in scope_keys
            or (bool(dates) and all(date == expected_date for date in dates))
        )
        return passed, f"expected {expected}, scope_keys={scope_keys}, dates={dates}"
    if requirement == "event_or_collection_hit":
        stores = set(_store_counts_from_candidates(candidates)) | {row.store_id for row in rows}
        passed = bool(stores.intersection({"life_episode", "life_memory_collection", "semantic_memory", "sqlite_ledger", "episodic_vector"}))
        return passed, f"stores={sorted(stores)}"
    if requirement == "source_ref_present":
        refs = _candidate_source_refs(candidates)
        if not refs:
            refs = [row.source_ref for row in rows if row.source_ref]
        return bool(refs), f"source refs={len(refs)}"
    if requirement == "trusted_memory_hit":
        passed = bool(rows) and not _contains_suppressed_candidate(candidates)
        return passed, f"selected rows={len(rows)}, suppressed={_contains_suppressed_candidate(candidates)}"
    if requirement == "target_domain_match":
        targets = _expanded_target_domains(target_domains)
        if not targets:
            return (bool(candidates or rows), "no target domains requested")
        seen_domains = _domains_from_candidates_and_rows(candidates=candidates, rows=rows)
        passed = bool(seen_domains.intersection(targets))
        return passed, f"target={sorted(targets)}, seen={sorted(seen_domains)}"
    if requirement == "error_episode_or_semantic_hit":
        stores = set(_store_counts_from_candidates(candidates)) | {row.store_id for row in rows}
        passed = bool(stores.intersection({"life_episode", "semantic_memory", "sqlite_ledger", "episodic_vector"}))
        return passed, f"stores={sorted(stores)}"
    if requirement == "correction_state_checked":
        suppressed = _contains_suppressed_candidate(candidates)
        return not suppressed, f"suppressed candidates present={suppressed}"
    if requirement == "suppressed_not_fact":
        suppressed = _contains_suppressed_candidate(candidates)
        return not suppressed, f"suppressed candidates present={suppressed}"
    if requirement == "no_self_echo":
        query_text = _clean_text(query)
        exact_echo = bool(query_text) and any(_clean_text(row.fact) == query_text for row in rows)
        return not exact_echo, f"exact query echo present={exact_echo}"
    return False, "unknown validation requirement"


def _build_recall_receipt_trace(
    *,
    candidates: Sequence[Mapping[str, Any]],
    rows: Sequence[SurfaceMemoryFactRow],
    query: Any,
    validation_trace: Mapping[str, Any],
    temporal_scope: str,
    query_kind: str,
    target_domains: Sequence[str] | None,
    recall_contract_id: str,
    primary_memory_layer: str,
    expected_memory_layers: Sequence[str] | None,
    min_collection_items: int,
    answer_obligations: Sequence[str] | None,
    selected_collection_items: Sequence[Mapping[str, Any]],
    selected_entity_items: Sequence[Mapping[str, Any]],
    ts_ms: int,
) -> dict[str, Any]:
    """Compact receipt explaining why this recall set satisfies the current query."""

    selected_candidates = _selected_candidates_for_rows(candidates=candidates, rows=rows)
    candidate_basis = selected_candidates or [dict(item) for item in candidates if isinstance(item, Mapping)]
    selected_store_counts: dict[str, int] = {}
    for row in rows:
        selected_store_counts[row.store_id] = selected_store_counts.get(row.store_id, 0) + 1
    item_payloads = [dict(item) for item in [*selected_collection_items, *selected_entity_items] if isinstance(item, Mapping)]
    selected_item_count = len(item_payloads)
    if selected_item_count <= 0:
        selected_item_count = max(_candidate_collection_item_counts(selected_candidates), default=0)
    required_min = max(0, _coerce_int(min_collection_items))
    exact_required = "collection_item_exact_count" in set(validation_trace.get("required") or [])
    evidence_ref_count = len(_unique([row.evidence_ref for row in rows if row.evidence_ref]))
    source_ref_count = len(_unique([row.source_ref for row in rows if row.source_ref]))
    required = list(validation_trace.get("required") or [])
    validation_passed = bool(validation_trace.get("passed", True))
    if required and not validation_passed:
        decision = "validation_failed"
    elif required:
        decision = "validated"
    elif rows:
        decision = "selected_without_contract"
    else:
        decision = "empty"
    expected_scope_key = _expected_collection_scope_key(_clean_text(temporal_scope), ts_ms=ts_ms)
    scope_keys = _candidate_scope_keys(candidate_basis)
    titles = _unique(
        [
            _clean_text(item.get("title") or item.get("item_title") or item.get("entity_title"))
            for item in item_payloads
            if _clean_text(item.get("title") or item.get("item_title") or item.get("entity_title"))
        ]
    )
    detail_evidence = _build_detail_evidence_receipt(
        item_payloads=item_payloads,
        candidate_basis=candidate_basis,
        answer_obligations=answer_obligations,
    )
    semantic_provenance = _build_semantic_provenance_receipt(candidate_basis)
    relevance = _build_query_relevance_receipt(
        query=query,
        rows=rows,
        item_payloads=item_payloads,
        candidate_basis=candidate_basis,
        query_kind=query_kind,
        recall_contract_id=recall_contract_id,
    )
    primary = _clean_text(primary_memory_layer)
    selected_store_ids = set(selected_store_counts)
    candidate_store_counts = _store_counts_from_candidates(candidate_basis)
    expected_layer_set = {
        _clean_text(item)
        for item in list(expected_memory_layers or [])
        if _clean_text(item)
    }
    selection_basis = _clean_list(
        [
            f"query_kind:{_clean_text(query_kind)}" if _clean_text(query_kind) else "",
            f"temporal_scope:{_clean_text(temporal_scope)}" if _clean_text(temporal_scope) else "",
            f"primary_layer:{primary}" if primary else "",
            f"scope:{', '.join(scope_keys)}" if scope_keys else "",
            f"items:{selected_item_count}" if selected_item_count else "",
            "evidence_refs:present" if evidence_ref_count else "",
            "semantic_provenance:resolved" if semantic_provenance.get("resolved_count") else "",
            f"detail_evidence:{detail_evidence.get('basis')}" if detail_evidence.get("required") else "",
        ]
    )
    return {
        "decision": decision,
        "validation_passed": validation_passed,
        "missing": list(validation_trace.get("missing") or []),
        "recall_contract_id": _clean_text(recall_contract_id),
        "query_kind": _clean_text(query_kind),
        "temporal_scope": _clean_text(temporal_scope),
        "expected_scope_key": expected_scope_key,
        "target_domains": list(_unique(list(target_domains or []))),
        "primary_memory_layer": primary,
        "expected_memory_layers": list(_unique(list(expected_memory_layers or []))),
        "selected_store_counts": dict(sorted(selected_store_counts.items())),
        "candidate_store_counts": dict(sorted(candidate_store_counts.items())),
        "selected_candidate_count": len(selected_candidates),
        "candidate_count": len(candidates),
        "selected_scope_keys": scope_keys,
        "selected_item_count": selected_item_count,
        "selected_titles": titles[:24],
        "evidence_ref_count": evidence_ref_count,
        "source_ref_count": source_ref_count,
        "detail_evidence": detail_evidence,
        "semantic_provenance": semantic_provenance,
        "query_relevance": relevance,
        "meets_primary_layer": (not primary) or primary in selected_store_ids or primary in candidate_store_counts,
        "meets_expected_layer": (
            not expected_layer_set
            or bool(selected_store_ids.intersection(expected_layer_set))
            or bool(set(candidate_store_counts).intersection(expected_layer_set))
        ),
        "coverage": {
            "min_collection_items": required_min,
            "collection_min_passed": (selected_item_count >= required_min) if required_min > 0 else True,
            "collection_exact_required": exact_required,
            "collection_exact_passed": (selected_item_count == required_min) if exact_required and required_min > 0 else None,
        },
        "answer_obligations": list(_unique(list(answer_obligations or []))),
        "selection_basis": selection_basis,
    }


def _build_semantic_provenance_receipt(candidate_basis: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    semantic_candidates = [
        dict(candidate)
        for candidate in candidate_basis
        if isinstance(candidate, Mapping) and _candidate_store_id(candidate) == "semantic_memory"
    ]
    resolved_ids: list[str] = []
    unresolved_ids: list[str] = []
    evidence_refs: list[str] = []
    source_refs: list[str] = []
    resolved_count = 0
    unresolved_count = 0
    for candidate in semantic_candidates:
        metadata = _candidate_metadata(candidate)
        semantic_id = _clean_text(metadata.get("semantic_id") or candidate.get("doc_id") or candidate.get("memory_id"))
        refs = _trusted_surface_provenance_refs(
            candidate.get("evidence_ref"),
            candidate.get("evidence_refs"),
            metadata.get("semantic_evidence_refs"),
            metadata.get("evidence_refs"),
            metadata.get("evidence_refs_primary"),
        )
        sources = _trusted_surface_provenance_refs(
            candidate.get("source_ref"),
            metadata.get("semantic_source_refs"),
            metadata.get("source_ref"),
        )
        evidence_refs.extend(refs)
        source_refs.extend(sources)
        status = _clean_text(metadata.get("semantic_provenance_status")).lower()
        resolved = status == "resolved" or bool(refs)
        if resolved:
            resolved_count += 1
            if semantic_id:
                resolved_ids.append(semantic_id)
        else:
            unresolved_count += 1
            if semantic_id:
                unresolved_ids.append(semantic_id)
    return {
        "schema": "Natsume.surface_semantic_provenance_receipt.v1",
        "semantic_candidate_count": len(semantic_candidates),
        "resolved_count": resolved_count,
        "unresolved_count": unresolved_count,
        "resolved_semantic_ids": _unique(resolved_ids)[:12],
        "unresolved_semantic_ids": _unique(unresolved_ids)[:12],
        "evidence_refs": _unique(evidence_refs)[:24],
        "source_refs": _unique(source_refs)[:24],
    }


def _trusted_surface_provenance_refs(*values: Any) -> list[str]:
    refs: list[str] = []
    for value in values:
        for ref in _coerce_sequence(value):
            cleaned = _clean_text(ref)
            lowered = cleaned.lower()
            if not lowered:
                continue
            if lowered.startswith(("semantic://", "ledger://semantic_memory/", "life_episode::", "life_memory_collection::")):
                continue
            if lowered.startswith(("chat://person/", "chat://session/", "source:")):
                continue
            if "://" not in lowered:
                continue
            refs.append(cleaned)
    return _unique(refs)


def _build_detail_evidence_receipt(
    *,
    item_payloads: Sequence[Mapping[str, Any]],
    candidate_basis: Sequence[Mapping[str, Any]],
    answer_obligations: Sequence[str] | None,
) -> dict[str, Any]:
    obligations = {_clean_text(item) for item in list(answer_obligations or []) if _clean_text(item)}
    required = bool(
        obligations.intersection(
            {
                "read_detail_evidence_before_deep_quality_judgment",
                "use_detail_or_body_evidence",
            }
        )
    )
    structured_items = [dict(item) for item in item_payloads if isinstance(item, Mapping)]
    if not structured_items:
        structured_items = _detail_items_from_candidates(candidate_basis)
    body_items = [item for item in structured_items if _item_has_body_detail_evidence(item)]
    anchor_items = [item for item in structured_items if _item_has_detail_anchor(item)]
    text_anchor_refs = _detail_refs_from_candidate_text(candidate_basis)
    anchor_count = max(len(anchor_items), len(text_anchor_refs))
    body_count = len(body_items)
    ready = body_count > 0 or anchor_count > 0
    basis = "not_required"
    if required:
        if body_count > 0:
            basis = "body_evidence"
        elif anchor_count > 0:
            basis = "detail_anchor"
        else:
            basis = "missing_detail_evidence"
    elif ready:
        basis = "available"
    return {
        "required": required,
        "ready_for_deep_review": ready,
        "basis": basis,
        "body_item_count": body_count,
        "anchor_item_count": anchor_count,
        "structured_item_count": len(structured_items),
        "detail_refs": list(_unique([*_detail_refs_from_items(structured_items), *text_anchor_refs]))[:24],
    }


def _build_query_relevance_receipt(
    *,
    query: Any,
    rows: Sequence[SurfaceMemoryFactRow],
    item_payloads: Sequence[Mapping[str, Any]],
    candidate_basis: Sequence[Mapping[str, Any]],
    query_kind: str,
    recall_contract_id: str,
) -> dict[str, Any]:
    anchors = _query_relevance_anchors(_query_relevance_focus_text(query))
    need_kind = _clean_text(query_kind)
    contract = _clean_text(recall_contract_id)
    requires = bool(anchors) and (
        need_kind
        in {
            "correction",
            "event_lookup",
            "failure_debug",
            "open_loop",
            "preference_lookup",
            "self_change",
            "evidence_review",
            "explicit_memory",
            "open_loop",
        }
        or contract
        in {
            "correction",
            "event_lookup",
            "failure_debug",
            "preference",
            "self_change",
            "evidence_review",
            "explicit_memory",
            "open_loop",
        }
    )
    if not anchors:
        return {
            "required": False,
            "score": 1.0,
            "anchors": [],
            "matched": [],
            "missing": [],
            "basis": "no_specific_query_anchors",
        }
    corpus_parts: list[str] = []
    corpus_parts.extend(row.fact for row in rows)
    corpus_parts.extend(row.source_ref for row in rows)
    corpus_parts.extend(row.evidence_ref for row in rows)
    for item in item_payloads:
        corpus_parts.extend(
            _clean_text(item.get(key))
            for key in (
                "title",
                "item_title",
                "entity_title",
                "canonical_name",
                "selection_reason",
                "item_summary",
                "intro_snippet",
                "detail_url",
                "source_url",
                "body_ref",
                "content_excerpt",
            )
        )
    for candidate in candidate_basis:
        metadata = _candidate_metadata(candidate)
        corpus_parts.extend(
            _clean_text(value)
            for value in (
                candidate.get("text"),
                candidate.get("snippet"),
                candidate.get("source_ref"),
                candidate.get("evidence_ref"),
                metadata.get("source_ref"),
                metadata.get("primary_ref"),
                metadata.get("scope_key"),
                metadata.get("domain"),
                metadata.get("memory_class"),
            )
        )
    corpus = _clean_text(" ".join(part for part in corpus_parts if _clean_text(part))).lower()
    matched = [anchor for anchor in anchors if _query_relevance_anchor_matches(anchor, corpus)]
    missing = [anchor for anchor in anchors if anchor not in matched]
    score = round(len(matched) / max(1, len(anchors)), 6)
    if contract in {"collection_date", "collection_recent", "anaphoric_selection"} and item_payloads:
        score = max(score, 0.72)
    return {
        "required": requires,
        "score": score,
        "anchors": anchors[:12],
        "matched": matched[:12],
        "missing": missing[:12],
        "basis": "anchor_overlap" if matched else "anchor_miss",
    }


def _query_relevance_focus_text(query: Any) -> str:
    """Use only the user's current turn for relevance gates.

    Retrieval queries intentionally include current turn + compact continuity
    facts. Relevance gates, however, must judge whether the selected memory
    answers the user's current question; otherwise unrelated continuity text can
    dilute a precise hit such as "DeepSeek" or "封面猫".
    """

    text = _clean_text(query)
    if not text:
        return ""
    parts = [_clean_text(part) for part in re.split(r"[；;\n]+", text) if _clean_text(part)]
    if not parts:
        return text
    first = parts[0]
    first = re.sub(r"^(?:当前用户(?:消息|问题)?|用户(?:消息|问题)?|current user(?: message)?)\s*[:：]\s*", "", first, flags=re.IGNORECASE)
    return _clean_text(first) or text


def _query_relevance_anchors(query: Any) -> list[str]:
    text = _clean_text(query)
    if not text:
        return []
    anchors: list[str] = []

    def add(value: str) -> None:
        cleaned = _clean_text(value).strip("《》「」『』【】（）()[]，,。.!！?？；;:：、").lower()
        cleaned = re.sub(r"(?:吗|呢|吧|啊)$", "", cleaned)
        if len(cleaned) < 2 or cleaned in anchors or cleaned in _QUERY_RELEVANCE_STOPWORDS:
            return
        anchors.append(cleaned)

    for title in re.findall(r"《([^》]{2,80})》", text):
        add(title)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,40}", text):
        add(token)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        stripped = re.sub(
            r"(?:还记得|记得我说|记不记得|我之前说|我之前|之前说|以前说|上次说|我说|你说|让我记|让你记|"
            r"昨天|今天|今日|前天|刚才|刚刚|最近|这几天|这两天|这三天|这些|那些|这个|那个|"
            r"这三本|那三本|这几本|那几本|这十几本|那十几本|这本|那本|这部|那部|"
            r"哪本|哪部|哪个|哪些|哪几本|哪几个|哪里|还有|问题|什么|为什么|怎么|怎样|如何|是不是|是否|"
            r"是什么|好看|喜欢|推荐|选择|挑|选|依据|内容|详情|原因|失败|异常|任务|结果|"
            r"靠谱吗|可靠|值得|看吗|做了|做过|做完了|做完|未完成|没完成|已完成|完成了|完成|继续做|继续|打开|浏览器|查了|查过|记错了|记错|纠正|"
            r"偏好|要求|规则|约定|我的|你的|用户|了吗|要|是|吗|呢|吧|啊|你|我)",
            " ",
            chunk,
        )
        for token in re.split(r"\s+", stripped):
            if len(token) >= 2:
                add(token)
    return anchors[:12]


def _query_relevance_anchor_matches(anchor: str, corpus: str) -> bool:
    value = _clean_text(anchor).lower()
    if not value:
        return False
    if value in corpus:
        return True
    if "正文" in value and "正文" in corpus and any(token in corpus for token in ("深读", "翻看", "打开详情", "详情页", "比较")):
        return True
    synonyms = {
        "格式": ("简介", "换行", "分段", "卡片", "模板腔", "排版"),
        "路由": ("provider", "model", "模型", "主链", "fallback"),
        "模型": ("provider", "model", "路由", "主链", "fallback"),
    }
    if any(token in corpus for token in synonyms.get(value, ())):
        return True
    if _query_relevance_semantic_group_matches(value, corpus):
        return True
    compact_anchor = re.sub(r"[有的之这那本部个一条项\s]+", "", value)
    compact_corpus = re.sub(r"[有的之这那本部个一条项\s]+", "", corpus)
    return bool(compact_anchor) and len(compact_anchor) >= 2 and compact_anchor in compact_corpus


def _query_relevance_semantic_group_matches(anchor: str, corpus: str) -> bool:
    value = _clean_text(anchor).lower()
    text = _clean_text(corpus).lower()
    if not value or not text:
        return False
    for group in _QUERY_RELEVANCE_SEMANTIC_GROUPS:
        if any(token in value for token in group) and any(token in text for token in group):
            return True
    return False


_QUERY_RELEVANCE_SEMANTIC_GROUPS: tuple[tuple[str, ...], ...] = (
    (
        "文本质量",
        "不像人话",
        "正常聊天",
        "模板腔",
        "台词腔",
        "证据条目",
        "生硬",
        "很呆",
        "自然",
    ),
    (
        "承诺",
        "待办",
        "open_loop",
        "openloop",
        "后续要",
        "未完成",
        "没完成",
        "已完成",
        "完成",
        "关闭",
    ),
    (
        "自我改变",
        "记忆系统",
        "生命经历",
        "经历锚点",
        "模型路由",
        "策略",
        "能力",
        "主链迁移",
        "资源管理器",
    ),
)


_QUERY_RELEVANCE_STOPWORDS: frozenset[str] = frozenset(
    {
        "昨天",
        "今天",
        "今日",
        "前天",
        "刚才",
        "刚刚",
        "最近",
        "这几天",
        "这两天",
        "这三天",
        "这些",
        "那些",
        "这个",
        "那个",
        "这本",
        "那本",
        "三本",
        "几本",
        "十几本",
        "哪本",
        "哪部",
        "哪个",
        "什么",
        "为什么",
        "怎么",
        "怎样",
        "如何",
        "原因",
        "结果",
        "内容",
        "详情",
        "好看",
        "喜欢",
        "推荐",
        "选择",
        "先看",
        "任务",
        "失败",
        "异常",
        "依据",
        "靠谱",
    }
)


def _detail_items_from_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        metadata = _candidate_metadata(candidate)
        containers = [metadata]
        episode_metadata = metadata.get("episode_metadata")
        if isinstance(episode_metadata, Mapping):
            containers.append(dict(episode_metadata))
        collection_metadata = metadata.get("collection_metadata")
        if isinstance(collection_metadata, Mapping):
            containers.append(dict(collection_metadata))
        for container in containers:
            for key in ("items", "selected_recommendations", "recommendations", "selected_items", "delivered_items", "cards"):
                value = container.get(key)
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                    out.extend(dict(item) for item in value if isinstance(item, Mapping))
    return out


def _item_has_body_detail_evidence(item: Mapping[str, Any]) -> bool:
    for key in (
        "body_observations",
        "body_excerpt",
        "content_excerpt",
        "content_text",
        "chapter_signal",
        "detail_observations",
        "deep_review_fact",
    ):
        value = item.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and any(_clean_text(part) for part in value):
            return True
        if _clean_text(value):
            return True
    return _coerce_int(item.get("content_char_count")) >= 240


def _item_has_detail_anchor(item: Mapping[str, Any]) -> bool:
    return bool(_detail_refs_from_items([item]))


def _detail_refs_from_items(items: Sequence[Mapping[str, Any]]) -> list[str]:
    refs: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for key in (
            "detail_url",
            "source_url",
            "url",
            "href",
            "document_ref",
            "file_ref",
            "file_path",
            "epub_path",
            "body_ref",
            "detail_evidence_ref",
            "source_ref",
        ):
            value = _truncate_text(_clean_text(item.get(key)), limit=400)
            if _looks_like_detail_anchor(value):
                refs.append(value)
    return list(_unique(refs))


def _detail_refs_from_candidate_text(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    refs: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        text = "\n".join(
            _clean_text(value)
            for value in (
                candidate.get("text"),
                candidate.get("snippet"),
                candidate.get("source_ref"),
                _candidate_metadata(candidate).get("source_ref"),
            )
            if _clean_text(value)
        )
        for match in re.findall(r"(?:https?://|file://|epub://|doc://)[^\s，。；;、]+", text):
            cleaned = _truncate_text(_clean_text(match.rstrip("。），,)]}")), limit=400)
            if _looks_like_detail_anchor(cleaned):
                refs.append(cleaned)
    return list(_unique(refs))


def _looks_like_detail_anchor(value: str) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    lower = text.lower()
    if lower.startswith(("http://", "https://", "file://", "epub://", "doc://", "document://")):
        return True
    if lower.endswith((".epub", ".pdf", ".txt", ".md", ".html", ".htm")):
        return True
    return bool(re.search(r"[/\\][^/\\]+\.(?:epub|pdf|txt|md|html?)$", text, flags=re.IGNORECASE))


def _selected_candidates_for_rows(
    *,
    candidates: Sequence[Mapping[str, Any]],
    rows: Sequence[SurfaceMemoryFactRow],
) -> list[dict[str, Any]]:
    selected_refs = {
        _clean_text(ref)
        for row in rows
        for ref in (row.source_ref, row.evidence_ref)
        if _clean_text(ref)
    }
    if not selected_refs:
        return []
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        metadata = _candidate_metadata(candidate)
        refs = {
            _clean_text(ref)
            for ref in (
                candidate.get("source_ref"),
                candidate.get("evidence_ref"),
                candidate.get("evidence_refs"),
                metadata.get("source_ref"),
                metadata.get("primary_ref"),
                metadata.get("evidence_ref"),
                metadata.get("evidence_refs_primary"),
            )
            if _clean_text(ref)
        }
        if refs.intersection(selected_refs):
            out.append(dict(candidate))
    return out


def _expected_collection_scope_key(temporal_scope: str, *, ts_ms: int) -> str:
    scope = _clean_text(temporal_scope)
    if scope in {TEMPORAL_CURRENT, TEMPORAL_RECENT_DAYS, TEMPORAL_UNSPECIFIED, ""}:
        return "recent"
    if scope.startswith(TEMPORAL_DATE_PREFIX):
        day_key = scope.removeprefix(TEMPORAL_DATE_PREFIX).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_key):
            return f"daily:{day_key}"
    if ts_ms <= 0:
        return ""
    today = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=_LOCAL_MEMORY_TZ).date()
    if scope == TEMPORAL_TODAY:
        return f"daily:{today.isoformat()}"
    if scope == TEMPORAL_YESTERDAY:
        return f"daily:{(today - timedelta(days=1)).isoformat()}"
    if scope == TEMPORAL_DAY_BEFORE_YESTERDAY:
        return f"daily:{(today - timedelta(days=2)).isoformat()}"
    return ""


def _filter_candidates_for_temporal_scope(
    candidates: Sequence[Mapping[str, Any]],
    *,
    temporal_scope: str,
    ts_ms: int,
) -> list[dict[str, Any]]:
    rows = [dict(candidate) for candidate in candidates if isinstance(candidate, Mapping)]
    expected = _expected_collection_scope_key(temporal_scope, ts_ms=ts_ms)
    if not expected or expected == "recent":
        return rows
    expected_date = expected.removeprefix("daily:")
    datable: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    for candidate in rows:
        metadata = _candidate_metadata(candidate)
        scope_key = _clean_text(metadata.get("scope_key"))
        candidate_date = _candidate_local_date(candidate)
        if scope_key or candidate_date:
            datable.append(candidate)
        if scope_key == expected or candidate_date == expected_date:
            matched.append(candidate)
    if not datable:
        return rows
    return matched


def _filter_candidates_for_target_domains(
    candidates: Sequence[Mapping[str, Any]],
    *,
    target_domains: Sequence[str] | None,
    query_kind: str,
    primary_memory_layer: str,
) -> list[dict[str, Any]]:
    rows = [dict(candidate) for candidate in candidates if isinstance(candidate, Mapping)]
    domains = _expanded_target_domains(target_domains)
    if not rows or not domains:
        return rows
    should_filter = (
        _clean_text(primary_memory_layer) == "life_memory_collection"
        or _clean_text(query_kind)
        in {
            "anaphoric_selection",
            "collection_summary",
            "correction",
            "evidence_review",
            "failure_debug",
            "open_loop",
            "preference_lookup",
            "self_change",
        }
    )
    if not should_filter:
        return rows
    strict_domain_match = _clean_text(query_kind) in {
        "correction",
        "evidence_review",
        "failure_debug",
        "open_loop",
        "preference_lookup",
        "self_change",
    }
    matched: list[dict[str, Any]] = []
    for candidate in rows:
        candidate_domains = _candidate_domains(candidate)
        if candidate_domains.intersection(domains) or (not candidate_domains and not strict_domain_match):
            matched.append(candidate)
    return matched or rows


def _filter_candidates_for_collection_count_contract(
    candidates: Sequence[Mapping[str, Any]],
    *,
    temporal_scope: str,
    validation_requirements: Sequence[str] | None,
    min_collection_items: int,
) -> list[dict[str, Any]]:
    rows = [dict(candidate) for candidate in candidates if isinstance(candidate, Mapping)]
    requirements = {_clean_text(item) for item in list(validation_requirements or []) if _clean_text(item)}
    required_count = max(0, _coerce_int(min_collection_items))
    if not rows or required_count <= 0 or "collection_item_exact_count" not in requirements:
        return rows
    exact = [candidate for candidate in rows if _candidate_collection_item_count(candidate) == required_count]
    if not exact:
        return rows
    if _clean_text(temporal_scope) == TEMPORAL_CURRENT:
        delivery_exact = [
            candidate
            for candidate in exact
            if _clean_text(_candidate_metadata(candidate).get("scope_key")).startswith("delivery:")
        ]
        if delivery_exact:
            return delivery_exact
    same_session_daily_exact = [
        candidate
        for candidate in exact
        if _clean_text(_candidate_metadata(candidate).get("scope_key")).startswith("daily:")
        and _candidate_session_boost(candidate) > 0.0
    ]
    if same_session_daily_exact:
        return same_session_daily_exact
    same_session_delivery_exact = [
        candidate
        for candidate in exact
        if _clean_text(_candidate_metadata(candidate).get("scope_key")).startswith("delivery:")
        and _candidate_session_boost(candidate) > 0.0
    ]
    if same_session_delivery_exact:
        return same_session_delivery_exact
    dated_daily_exact = [
        candidate
        for candidate in exact
        if _clean_text(_candidate_metadata(candidate).get("scope_key")).startswith("daily:")
    ]
    if dated_daily_exact:
        return dated_daily_exact
    return exact


def _expanded_target_domains(target_domains: Sequence[str] | None) -> set[str]:
    domains = {_clean_text(item) for item in list(target_domains or []) if _clean_text(item)}
    if "lightnovel_recommendation" in domains:
        domains.add("recommendation")
    if "recommendation" in domains:
        domains.add("lightnovel_recommendation")
    if "web" in domains or "file" in domains:
        domains.add("content")
    return domains


def _candidate_domains(candidate: Mapping[str, Any]) -> set[str]:
    metadata = _candidate_metadata(candidate)
    domains: set[str] = set()
    for key in ("domain", "recommendation_domain", "content_domain", "target_domain", "memory_domain"):
        value = _clean_text(metadata.get(key) or candidate.get(key))
        if value:
            domains.add(value)
    action = _clean_text(metadata.get("action") or metadata.get("action_kind") or candidate.get("action")).lower()
    text = " ".join(
        _clean_text(value).lower()
        for value in (
            action,
            metadata.get("collection_kind"),
            metadata.get("intent"),
            metadata.get("source_ref"),
            metadata.get("evidence_refs_primary"),
            metadata.get("evidence_refs"),
            metadata.get("primary_ref"),
            candidate.get("text"),
            candidate.get("snippet"),
            candidate.get("source_ref"),
            candidate.get("evidence_ref"),
        )
    )
    domains.update(_domains_from_text_and_refs(text))
    if "lightnovel" in text or "轻小说" in text or "lightnovel.fun" in text:
        domains.update({"lightnovel_recommendation", "recommendation"})
    elif "recommendation" in text or "推荐" in text:
        domains.add("recommendation")
    if any(token in text for token in ("failure", "failed", "timeout", "失败", "异常", "报错")):
        domains.add("failure")
    if any(
        token in text
        for token in (
            "semantic_kind:user_preference",
            "user_preference",
            "用户偏好",
            "稳定偏好",
            "用户要求",
            "规则",
            "约束",
            "格式",
            "换行",
            "模板腔",
            "不要用",
            "别用",
            "主链不要",
        )
    ):
        domains.add("preference")
    if any(
        token in text
        for token in (
            "semantic_kind:relationship_feedback",
            "relationship_feedback",
            "关系反馈",
            "文本质量",
            "不像人话",
            "台词腔",
            "正常聊天",
        )
    ):
        domains.update({"preference", "relationship_feedback"})
    if any(token in text for token in ("semantic_kind:commitment", "open_loop", "openloop", "承诺和待办", "待办", "后续要")):
        domains.update({"open_loop", "task"})
    if any(
        token in text
        for token in (
            "semantic_kind:self_change",
            "self_change",
            "自我改变",
            "生命经历",
            "经历锚点",
            "可召回记忆",
            "记忆系统",
        )
    ):
        domains.add("self_change")
    if any(token in text for token in ("semantic_kind:correction", "correction", "纠错", "修正", "撤回", "记错")):
        domains.add("correction")
    return domains


def _domains_from_candidates_and_rows(
    *,
    candidates: Sequence[Mapping[str, Any]],
    rows: Sequence[SurfaceMemoryFactRow],
) -> set[str]:
    domains: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            domains.update(_candidate_domains(candidate))
    for row in rows:
        domains.update(_domains_from_text_and_refs(" ".join([row.fact, row.source_ref, row.evidence_ref])))
    return domains


def _domains_from_text_and_refs(value: str) -> set[str]:
    text = _clean_text(value).lower()
    domains: set[str] = set()
    if not text:
        return domains
    if re.search(r"https?://|www\.|网页|网站|页面|链接|url|web|站点|浏览器|browser", text, flags=re.IGNORECASE):
        domains.update({"web", "content"})
    if re.search(r"file://|doc://|document://|epub://|\.epub\b|\.pdf\b|\.txt\b|\.md\b|\.html?\b|文件|文档|报告|附件|表格", text, flags=re.IGNORECASE):
        domains.update({"file", "content"})
    if re.search(r"正文|内容|原文|详情|截图|图片|语音|转写|工具结果|执行结果", text):
        domains.add("content")
    if re.search(r"vision://|perception://|图片|截图|视觉|看见|观察", text):
        domains.update({"perception", "content"})
    return domains


def _candidate_scope_keys(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    return _unique(
        [
            _candidate_metadata(candidate).get("scope_key")
            for candidate in candidates
            if _candidate_store_id(candidate) == "life_memory_collection"
        ]
    )


def _candidate_local_dates(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    return _unique(_candidate_local_date(candidate) for candidate in candidates)


def _candidate_local_date(candidate: Mapping[str, Any]) -> str:
    ts_ms = _candidate_ts_ms(candidate)
    if ts_ms <= 0:
        return ""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=_LOCAL_MEMORY_TZ).date().isoformat()


def _candidate_ts_ms(candidate: Mapping[str, Any]) -> int:
    metadata = _candidate_metadata(candidate)
    for value in (
        candidate.get("created_ts_ms"),
        candidate.get("updated_ts_ms"),
        candidate.get("ts_ms"),
        metadata.get("created_ts_ms"),
        metadata.get("updated_ts_ms"),
        metadata.get("last_touched_ts_ms"),
        metadata.get("created_at_ts_ms"),
    ):
        ts_ms = _coerce_int(value)
        if ts_ms > 0:
            return ts_ms
    return 0


def _store_counts_from_candidates(candidates: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        store_id = _candidate_store_id(candidate)
        if not store_id:
            continue
        counts[store_id] = counts.get(store_id, 0) + 1
    return counts


def _candidate_store_id(candidate: Mapping[str, Any]) -> str:
    metadata = _candidate_metadata(candidate)
    return _clean_text(
        candidate.get("store_id")
        or metadata.get("store_id")
        or metadata.get("collection")
        or "episodic_vector"
    ).lower()


def _candidate_metadata(candidate: Mapping[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _candidate_evidence_refs(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    refs: list[Any] = []
    for candidate in candidates:
        metadata = _candidate_metadata(candidate)
        for value in (
            candidate.get("evidence_ref"),
            candidate.get("evidence_refs"),
            metadata.get("evidence_ref"),
            metadata.get("evidence_refs"),
            metadata.get("evidence_refs_primary"),
        ):
            refs.extend(_coerce_sequence(value))
    return _unique(refs)


def _candidate_collection_item_counts(candidates: Sequence[Mapping[str, Any]]) -> list[int]:
    counts: list[int] = []
    for candidate in candidates:
        item_count = _candidate_collection_item_count(candidate)
        if item_count > 0:
            counts.append(item_count)
    return counts


def _candidate_collection_item_count(candidate: Mapping[str, Any]) -> int:
    store_id = _candidate_store_id(candidate)
    if store_id not in {"life_memory_collection", "life_episode"}:
        return 0
    metadata = _candidate_metadata(candidate)
    item_count = _coerce_int(metadata.get("item_count"))
    metadata_containers = [metadata]
    for nested_key in ("episode_metadata", "collection_metadata", "payload", "result"):
        nested = metadata.get(nested_key)
        if isinstance(nested, Mapping):
            metadata_containers.append(dict(nested))
    for container in metadata_containers:
        for key in ("items", "selected_recommendations", "recommendations", "selected_items", "delivered_items", "cards"):
            items = container.get(key)
            if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
                item_count = max(item_count, len(items))
    return max(item_count, _item_count_from_candidate_text(candidate))


def _candidate_session_boost(candidate: Mapping[str, Any]) -> float:
    metadata = _candidate_metadata(candidate)
    boost = metadata.get("score_boost")
    if isinstance(boost, Mapping):
        return _coerce_float(boost.get("session_boost"))
    return 0.0


def _item_count_from_candidate_text(candidate: Mapping[str, Any]) -> int:
    text = _clean_text(candidate.get("text") or candidate.get("snippet"))
    if not text:
        return 0
    match = re.search(r"(?:推荐(?:了|过)?|记录)\s*(\d+|三|两|二|十几)\s*(?:本|部|个|条目)", text)
    if not match:
        return 0
    token = match.group(1)
    if token == "十几":
        return 10
    if token == "三":
        return 3
    if token in {"两", "二"}:
        return 2
    return _coerce_int(token)


def _candidate_source_refs(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    refs: list[Any] = []
    for candidate in candidates:
        metadata = _candidate_metadata(candidate)
        for value in (candidate.get("source_ref"), metadata.get("source_ref"), metadata.get("primary_ref")):
            refs.extend(_coerce_sequence(value))
    return _unique(refs)


def _selected_collection_items_for_trace(
    *,
    candidates: Sequence[Mapping[str, Any]],
    rows: Sequence[SurfaceMemoryFactRow],
) -> list[dict[str, Any]]:
    selected_refs = {_clean_text(row.source_ref) for row in rows if _clean_text(row.source_ref)}
    if not selected_refs:
        return []
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        if _candidate_store_id(candidate) != "life_memory_collection":
            continue
        metadata = _candidate_metadata(candidate)
        source_ref = _clean_text(candidate.get("source_ref") or metadata.get("source_ref"))
        if source_ref and source_ref not in selected_refs:
            continue
        collection_id = _clean_text(metadata.get("collection_id"))
        scope_key = _clean_text(metadata.get("scope_key"))
        for item in list(metadata.get("items") or [])[:24]:
            if not isinstance(item, Mapping):
                continue
            card = _trace_collection_item_payload(
                item,
                collection_id=collection_id,
                scope_key=scope_key,
            )
            if card and card not in out:
                card["index"] = len(out) + 1
                out.append(card)
    return out


def _selected_entity_items_for_trace(
    *,
    candidates: Sequence[Mapping[str, Any]],
    rows: Sequence[SurfaceMemoryFactRow],
) -> list[dict[str, Any]]:
    selected_refs = {_clean_text(row.source_ref) for row in rows if _clean_text(row.source_ref)}
    if not selected_refs:
        return []
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        if _candidate_store_id(candidate) != "life_memory_entity":
            continue
        metadata = _candidate_metadata(candidate)
        source_ref = _clean_text(candidate.get("source_ref") or metadata.get("source_ref"))
        if source_ref and source_ref not in selected_refs:
            continue
        entity_id = _clean_text(metadata.get("entity_id"))
        entity_title = _truncate_text(_clean_text(metadata.get("canonical_name")), limit=120)
        for item in list(metadata.get("mentions") or [])[:12]:
            if not isinstance(item, Mapping):
                continue
            card = _trace_entity_item_payload(
                item,
                entity_id=entity_id,
                entity_title=entity_title,
            )
            if card and card not in out:
                card["index"] = len(out) + 1
                out.append(card)
    return out


def _trace_collection_item_payload(
    item: Mapping[str, Any],
    *,
    collection_id: str,
    scope_key: str,
) -> dict[str, Any]:
    metadata = item.get("metadata")
    item_meta = dict(metadata) if isinstance(metadata, Mapping) else {}
    title = _first_item_text(
        {**item_meta, **dict(item)},
        ("title", "title_display", "item_title", "name", "label"),
        limit=120,
    )
    source_ref = _first_item_text(
        {**item_meta, **dict(item)},
        ("detail_url", "url", "source_url", "source_ref"),
        limit=260,
    )
    if not title and not source_ref:
        return {}
    summary = _first_item_text(item, ("item_summary", "summary", "description", "intro_snippet", "intro"), limit=360)
    return {
        key: value
        for key, value in {
            "collection_id": collection_id,
            "collection_item_id": _clean_text(item.get("collection_item_id")),
            "episode_id": _clean_text(item.get("episode_id")),
            "item_key": _clean_text(item.get("item_key")),
            "scope_key": scope_key,
            "source_day": _first_item_text({**item_meta, **dict(item)}, ("source_day", "day_key", "collection_day"), limit=24),
            "index": _coerce_int(item.get("position") or item_meta.get("position"), default=0),
            "title": title,
            "canonical_title": _first_item_text({**item_meta, **dict(item)}, ("canonical_title", "series_title", "work_title", "book_title"), limit=160),
            "canonical_key": _first_item_text({**item_meta, **dict(item)}, ("canonical_key", "normalized_key"), limit=220),
            "detail_url": source_ref if _looks_like_detail_anchor(source_ref) else "",
            "source_ref": source_ref,
            "evidence_ref": _first_item_text(item, ("evidence_ref",), limit=260),
            "selection_reason": _first_item_text(
                {**item_meta, **dict(item)},
                ("selection_reason", "rationale", "reason", "why", "look_point"),
                limit=140,
            ),
            "intro_snippet": _first_item_text(
                {**item_meta, **dict(item), "summary": summary},
                ("intro_snippet", "intro", "description", "summary", "abstract"),
                limit=220,
            ),
            "content_excerpt": _first_item_text(
                {**item_meta, **dict(item)},
                ("content_excerpt", "body_excerpt", "content_text", "chapter_signal", "detail_observations", "body_observations"),
                limit=320,
            ),
            "body_ref": _first_item_text({**item_meta, **dict(item)}, ("body_ref", "detail_evidence_ref", "document_ref", "file_ref"), limit=260),
            "content_char_count": _coerce_int(item.get("content_char_count") or item_meta.get("content_char_count"), default=0),
            "update_time": _first_item_text({**item_meta, **dict(item)}, ("update_time", "updated_at", "freshness"), limit=80),
            "view_count": _first_item_text({**item_meta, **dict(item)}, ("view_count", "views"), limit=40),
            "comment_count": _first_item_text({**item_meta, **dict(item)}, ("comment_count", "comments"), limit=40),
        }.items()
        if value not in ("", 0, None, [], {})
    }


def _trace_entity_item_payload(
    item: Mapping[str, Any],
    *,
    entity_id: str,
    entity_title: str,
) -> dict[str, Any]:
    item_meta_raw = item.get("item_metadata")
    item_meta = dict(item_meta_raw) if isinstance(item_meta_raw, Mapping) else {}
    metadata_raw = item.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    merged = {**item_meta, **metadata, **dict(item)}
    title = _first_item_text(
        {"item_title": entity_title, **merged},
        ("title", "title_display", "item_title", "name", "label"),
        limit=120,
    )
    source_ref = _first_item_text(
        merged,
        ("detail_url", "url", "source_url", "source_ref", "item_source_ref"),
        limit=260,
    )
    if not title and not source_ref:
        return {}
    summary = _first_item_text(merged, ("item_summary", "summary", "description", "intro_snippet", "intro"), limit=360)
    return {
        key: value
        for key, value in {
            "entity_id": entity_id,
            "collection_id": _clean_text(item.get("collection_id")),
            "collection_item_id": _clean_text(item.get("collection_item_id")),
            "scope_key": _clean_text(item.get("scope_key")),
            "source_day": _first_item_text(merged, ("source_day", "day_key", "collection_day"), limit=24),
            "index": _coerce_int(item.get("position"), default=0),
            "title": title,
            "canonical_title": _first_item_text(merged, ("canonical_title", "series_title", "work_title", "book_title"), limit=160),
            "canonical_key": _first_item_text(merged, ("canonical_key", "normalized_key"), limit=220),
            "detail_url": source_ref if _looks_like_detail_anchor(source_ref) else "",
            "source_ref": source_ref,
            "evidence_ref": _first_item_text(merged, ("evidence_ref", "item_evidence_ref"), limit=260),
            "selection_reason": _first_item_text(
                merged,
                ("selection_reason", "rationale", "reason", "why", "look_point"),
                limit=140,
            ),
            "intro_snippet": _first_item_text(
                {**merged, "summary": summary},
                ("intro_snippet", "intro", "description", "summary", "abstract"),
                limit=220,
            ),
            "content_excerpt": _first_item_text(
                merged,
                ("content_excerpt", "body_excerpt", "content_text", "chapter_signal", "detail_observations", "body_observations"),
                limit=320,
            ),
            "body_ref": _first_item_text(merged, ("body_ref", "detail_evidence_ref", "document_ref", "file_ref"), limit=260),
            "content_char_count": _coerce_int(merged.get("content_char_count"), default=0),
            "update_time": _first_item_text(merged, ("update_time", "updated_at", "freshness"), limit=80),
            "view_count": _first_item_text(merged, ("view_count", "views"), limit=40),
            "comment_count": _first_item_text(merged, ("comment_count", "comments"), limit=40),
        }.items()
        if value not in ("", 0, None, [], {})
    }


def _contains_suppressed_candidate(candidates: Sequence[Mapping[str, Any]]) -> bool:
    for candidate in candidates:
        metadata = _candidate_metadata(candidate)
        values = (
            candidate.get("status"),
            candidate.get("truth_state"),
            candidate.get("retention_state"),
            metadata.get("status"),
            metadata.get("truth_state"),
            metadata.get("retention_state"),
        )
        if any(_clean_text(value).upper() in {"SUPPRESSED", "CORRECTED", "GONE"} for value in values):
            return True
    return False


def apply_surface_memory_recall_packet(
    context: ThreeLayerContinuityContext,
    packet: MemoryRecallPacket,
) -> ThreeLayerContinuityContext:
    recall_facts = _clean_list(
        [
            *context.recall_facts,
            *(_ensure_packet_fact_prefix(fact, prefix="记忆线索") for fact in packet.facts),
            *(_ensure_packet_fact_prefix(clue, prefix="关系线索") for clue in packet.relationship_clues),
        ]
    )
    style_anchor = _clean_list(
        [
            *context.style_anchor,
            *packet.style_anchor,
            "多路召回压缩" if packet.facts or packet.relationship_clues else "",
        ]
    )
    must_not_do = _clean_list([*context.must_not_do, *packet.must_not_do])
    return replace(
        context,
        recall_facts=tuple(recall_facts),
        style_anchor=tuple(style_anchor),
        must_not_do=tuple(must_not_do),
    )


def _ensure_packet_fact_prefix(text: Any, *, prefix: str) -> str:
    value = _clean_text(text)
    if not value:
        return ""
    if any(value.startswith(marker) for marker in ("记忆线索：", "关系线索：", "召回线索：", "长期事实：")):
        return value
    return f"{prefix}：{value}"


def build_surface_input_with_memory_recall(
    context: ThreeLayerContinuityContext,
    *,
    packet: MemoryRecallPacket,
    soul_md: str,
    case_id: str = "",
    max_facts: int = 6,
    max_recent_turns: int = 6,
) -> IsolatedSurfaceInput:
    return build_surface_input_from_three_layer_context(
        context,
        soul_md=soul_md,
        case_id=case_id,
        memory_packet=packet,
        max_facts=max_facts,
        max_recent_turns=max_recent_turns,
    )


def memory_recall_packet_from_mapping(raw: Mapping[str, Any] | MemoryRecallPacket) -> MemoryRecallPacket:
    """Coerce an already-compressed packet from tests/tools without accepting raw memory."""

    if isinstance(raw, MemoryRecallPacket):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError("memory recall packet must be a mapping")
    return MemoryRecallPacket(
        facts=tuple(_clean_list(_coerce_sequence(raw.get("facts")))),
        style_anchor=tuple(_clean_list(_coerce_sequence(raw.get("style_anchor")))),
        relationship_clues=tuple(_clean_list(_coerce_sequence(raw.get("relationship_clues")))),
        must_not_do=tuple(_clean_list(_coerce_sequence(raw.get("must_not_do")))),
        confidence=_clamp01(_coerce_float(raw.get("confidence"))),
        evidence_refs=tuple(_clean_list(_coerce_sequence(raw.get("evidence_refs")))),
    )


def build_surface_turn_memory_text(
    *,
    user_message: str,
    assistant_reply: str,
    max_chars: int = 180,
) -> str:
    user_text = _surface_safe_text(user_message, limit=80)
    assistant_text = _surface_safe_text(assistant_reply, limit=96)
    if not user_text and not assistant_text:
        return ""
    if user_text and assistant_text:
        text = f"当前回合摘要：用户提到「{user_text}」；回复「{assistant_text}」。"
    elif user_text:
        text = f"当前回合摘要：用户提到「{user_text}」。"
    else:
        text = f"当前回合摘要：回复「{assistant_text}」。"
    return _truncate_text(text, limit=max_chars)


def write_surface_turn_memory(
    *,
    episodic_store: Any,
    request_id: str | None,
    session_id: str | None,
    person_id: str | None,
    user_message: str,
    assistant_reply: str,
    ts_ms: int | None = None,
    intent_id: str | None = None,
    evidence_refs: Sequence[str] | None = None,
    decay_profile_id: str = "SLOW",
    visibility: str = "internal",
    emotion_tag: str = "NEUTRAL",
    emotion_valence: float = 0.0,
    emotion_arousal: float = 0.3,
) -> SurfaceMemoryWriteResult:
    refs = _build_evidence_refs(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        evidence_refs=evidence_refs,
    )
    if not refs:
        return SurfaceMemoryWriteResult(
            success=False,
            reason_code=SURFACE_MEMORY_EVIDENCE_REQUIRED,
            detail="surface memory write requires at least one evidence ref",
        )
    text = build_surface_turn_memory_text(
        user_message=user_message,
        assistant_reply=assistant_reply,
    )
    if not text:
        return SurfaceMemoryWriteResult(
            success=False,
            reason_code=SURFACE_MEMORY_EMPTY_TURN,
            detail="surface memory write skipped: empty turn",
            evidence_refs=tuple(refs),
        )
    try:
        result = episodic_store.write_memory(
            text=text,
            evidence_refs=list(refs),
            request_id=request_id,
            intent_id=intent_id,
            decay_profile_id=decay_profile_id,
            visibility=visibility,
            ts_ms=int(ts_ms if ts_ms is not None else now_ms()),
            emotion_tag=emotion_tag,
            emotion_valence=emotion_valence,
            emotion_arousal=emotion_arousal,
        )
    except Exception as exc:
        return SurfaceMemoryWriteResult(
            success=False,
            reason_code=SURFACE_MEMORY_WRITE_FAILED,
            detail=f"surface memory write failed: {_truncate_text(str(exc), limit=160)}",
            text=text,
            evidence_refs=tuple(refs),
        )
    return SurfaceMemoryWriteResult(
        success=bool(getattr(result, "success", False)),
        reason_code=getattr(result, "reason_code", None),
        detail=_clean_text(getattr(result, "detail", "")) or "surface memory write completed",
        memory_id=getattr(result, "memory_id", None),
        text=text,
        evidence_refs=tuple(refs),
    )


def _recall_output(recall_result: Any) -> dict[str, Any]:
    if isinstance(recall_result, Mapping):
        raw_output = recall_result.get("output", recall_result)
    else:
        raw_output = getattr(recall_result, "output", None)
    return dict(raw_output) if isinstance(raw_output, Mapping) else {}


def _candidate_list(output: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = output.get("candidates") or output.get("topk") or output.get("evidence_items") or []
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _select_fact_rows(
    candidates: Sequence[Mapping[str, Any]],
    *,
    query: str | None = None,
    max_facts: int,
    max_chars_per_fact: int,
) -> list[SurfaceMemoryFactRow]:
    cap = max(0, int(max_facts or 0))
    if cap <= 0:
        return []
    rows = [_build_fact_row(item, max_chars_per_fact=max_chars_per_fact) for item in candidates]
    rows = [row for row in rows if row is not None]
    rows = _filter_rows_for_open_loop_query(rows, query=query)
    rows = _filter_rows_for_self_change_query(rows, query=query)
    rows = _filter_rows_for_relationship_feedback_query(rows, query=query)
    rows = _filter_rows_for_preference_rule_query(rows, query=query)
    rows = _filter_rows_for_correction_query(rows, query=query)
    rows = _filter_rows_for_failure_debug_query(rows, query=query)
    rows = _filter_rows_for_explicit_memory_query(rows, query=query)
    rows = _filter_rows_for_anaphoric_selection(rows, query=query)
    rows = _filter_rows_for_collection_scope(rows, query=query)
    rows = sorted(rows, key=lambda row: _surface_fact_row_rank(row, query=query), reverse=True)
    allow_same_store = _looks_like_explicit_memory_content_query(_clean_text(query))
    selected: list[SurfaceMemoryFactRow] = []
    seen_stores: set[str] = set()
    for row in rows:
        if len(selected) >= cap:
            break
        if not allow_same_store and row.store_id in seen_stores:
            continue
        selected.append(row)
        seen_stores.add(row.store_id)
    for row in rows:
        if len(selected) >= cap:
            break
        if row.fact in {item.fact for item in selected}:
            continue
        selected.append(row)
    return selected


def _filter_rows_for_open_loop_query(
    rows: Sequence[SurfaceMemoryFactRow],
    *,
    query: str | None,
) -> list[SurfaceMemoryFactRow]:
    if not rows:
        return []
    query_text = _clean_text(query)
    if not _looks_like_open_loop_memory_query(query_text):
        return list(rows)
    commitment_rows = [row for row in rows if _looks_like_open_loop_memory_fact(row.fact)]
    if not commitment_rows:
        return list(rows)
    wants_closed = _surface_query_wants_closed_open_loop(query_text)
    preferred = [
        row
        for row in commitment_rows
        if _looks_like_closed_open_loop_memory_fact(row.fact) is wants_closed
    ]
    if preferred:
        return [
            *preferred,
            *(row for row in rows if _clean_text(row.store_id).lower() in {"neo4j_bond", "bond_graph"}),
        ]
    return commitment_rows


def _filter_rows_for_failure_debug_query(
    rows: Sequence[SurfaceMemoryFactRow],
    *,
    query: str | None,
) -> list[SurfaceMemoryFactRow]:
    if not rows:
        return []
    query_text = _query_relevance_focus_text(query)
    if not _looks_like_failure_debug_memory_query(query_text):
        return list(rows)
    anchors = _query_relevance_anchors(query_text)
    if not anchors:
        return list(rows)
    matched = [
        row
        for row in rows
        if _failure_row_matches_query_anchors(row, anchors=anchors)
    ]
    return matched or list(rows)


def _looks_like_failure_debug_memory_query(text: str | None) -> bool:
    normalized = _clean_text(text).lower()
    if not normalized:
        return False
    return any(token in normalized for token in ("失败", "失败原因", "报错", "错误", "异常", "timeout", "超时", "原因"))


def _failure_row_matches_query_anchors(row: SurfaceMemoryFactRow, *, anchors: Sequence[str]) -> bool:
    corpus = _clean_text(" ".join([row.fact, row.source_ref, row.evidence_ref])).lower()
    if not corpus:
        return False
    if not any(_query_relevance_anchor_matches(anchor, corpus) for anchor in anchors):
        return False
    return any(token in corpus for token in ("failed", "failure", "timeout", "error", "失败", "报错", "异常", "超时"))


def _filter_rows_for_self_change_query(
    rows: Sequence[SurfaceMemoryFactRow],
    *,
    query: str | None,
) -> list[SurfaceMemoryFactRow]:
    if not rows:
        return []
    query_text = _clean_text(query)
    if _looks_like_open_loop_memory_query(query_text):
        return list(rows)
    if not _looks_like_self_change_memory_query(query_text):
        return list(rows)
    preferred = [row for row in rows if _looks_like_self_change_memory_fact(row.fact)]
    semantic_preferred = [row for row in preferred if _clean_text(row.store_id).lower() == "semantic_memory"]
    if semantic_preferred:
        return [
            *semantic_preferred,
            *(row for row in rows if _clean_text(row.store_id).lower() in {"neo4j_bond", "bond_graph"}),
        ]
    if preferred:
        return preferred
    return list(rows)


def _surface_fact_row_source_priority(row: SurfaceMemoryFactRow, *, query: str | None = None) -> float:
    fact = _clean_text(row.fact)
    query_text = _clean_text(query)
    corpus = _clean_text(" ".join([fact, row.source_ref, row.evidence_ref])).lower()
    priority = 0.0
    if any(token in corpus for token in ("assistant_reply", "assistant:", "assistant：", "助手回复", "我回：", "assistant ", "surface://reply", "skill://exec")):
        priority -= 4.0
    if any(token in fact for token in ("；回复", "回复「", "回复：", "Assistant:", "Assistant：")):
        priority -= 3.0
    terminal_codex_fact = any(token in fact.upper() for token in ("CODEX_PRODUCTION", "LIGHTNOVEL_"))
    if any(token in fact for token in ("FAILED", "APP_LAUNCH_FAILED", "open_app FAILED", "打开应用失败")) and not terminal_codex_fact:
        priority -= 8.0
    if any(token in fact for token in ("项目事实", "测试事实", "记忆事实", "用户纠正", "用户更正", "修正为", "纠正为", "更正为")):
        priority += 6.0
    if any(token in fact for token in ("纠正测试代号", "测试代号纠正", "改成", "作废", "以 R2 为准", "以R2为准")):
        priority += 8.0
    if any(token in fact for token in ("记一下", "记住", "记下来", "帮我记", "替我记", "用户提到", "用户说", "用户要求")):
        priority += 3.0
    if _looks_like_explicit_memory_content_query(query_text) and _looks_like_explicit_memory_fact(fact):
        priority += 2.0
    if "gateway://message" in corpus or "user_message" in corpus or "role=user" in corpus:
        priority += 1.0
    return priority


def _surface_fact_row_rank(row: SurfaceMemoryFactRow, *, query: str | None = None) -> tuple[float, float, float, float, float]:
    store_id = _clean_text(row.store_id).lower()
    fact_text = _clean_text(row.fact).upper()
    query_text = _clean_text(query)
    source_priority = _surface_fact_row_source_priority(row, query=query_text)
    semantic_rule_priority = 0.0
    if _looks_like_preference_rule_memory_query(query_text):
        if store_id == "semantic_memory" and _looks_like_preference_rule_memory_fact(row.fact):
            semantic_rule_priority = 2.0
        elif _looks_like_preference_rule_memory_fact(row.fact):
            semantic_rule_priority = 1.0
    terminal_background_priority = 1 if (
        ("CODEX_PRODUCTION" in fact_text or "LIGHTNOVEL_" in fact_text)
        and any(marker in fact_text for marker in ("FAILED", "COMPLETED", "LIGHTNOVEL_NATIVE_NO_RECOMMENDATIONS"))
    ) else 0
    store_priority = {
        "life_memory_collection": 5,
        "life_memory_entity": 4,
        "life_episode": 4,
        "semantic_memory": 3,
        "episodic_vector": 2,
        "episodic": 2,
        "sqlite_ledger": 2,
        "neo4j_bond": 1,
        "bond_graph": 1,
    }.get(store_id, 0)
    return (source_priority, semantic_rule_priority, float(terminal_background_priority), _coerce_float(row.score), float(store_priority))


def _filter_rows_for_explicit_memory_query(
    rows: Sequence[SurfaceMemoryFactRow],
    *,
    query: str | None,
) -> list[SurfaceMemoryFactRow]:
    if not rows:
        return []
    query_text = _clean_text(query)
    if not _looks_like_explicit_memory_content_query(query_text):
        return list(rows)
    preferred = [
        row
        for row in rows
        if _looks_like_explicit_memory_fact(row.fact)
        or _surface_fact_row_source_priority(row, query=query_text) >= 5.0
    ]
    if preferred:
        return preferred
    return list(rows)


def _filter_rows_for_preference_rule_query(
    rows: Sequence[SurfaceMemoryFactRow],
    *,
    query: str | None,
) -> list[SurfaceMemoryFactRow]:
    if not rows:
        return []
    query_text = _clean_text(query)
    if not _looks_like_preference_rule_memory_query(query_text):
        return list(rows)
    preferred = [
        row
        for row in rows
        if _looks_like_preference_rule_memory_fact(row.fact)
        and not _looks_like_broad_collection_fact(row.fact)
    ]
    topic = _preference_rule_query_topic(query_text)
    if topic:
        topic_matched = [row for row in preferred if _preference_rule_fact_matches_topic(row.fact, topic=topic)]
        if topic_matched:
            preferred = topic_matched
    semantic_preferred = [row for row in preferred if _clean_text(row.store_id).lower() == "semantic_memory"]
    if semantic_preferred:
        return [
            *semantic_preferred,
            *(row for row in rows if _clean_text(row.store_id).lower() in {"neo4j_bond", "bond_graph"}),
        ]
    if preferred:
        return preferred
    return list(rows)


def _filter_rows_for_relationship_feedback_query(
    rows: Sequence[SurfaceMemoryFactRow],
    *,
    query: str | None,
) -> list[SurfaceMemoryFactRow]:
    if not rows:
        return []
    topic = _relationship_feedback_query_topic(query)
    if not topic:
        return list(rows)
    preferred = [row for row in rows if _relationship_feedback_fact_matches_topic(row.fact, topic=topic)]
    if not preferred:
        return list(rows)
    bond_rows = [row for row in rows if _clean_text(row.store_id).lower() in {"neo4j_bond", "bond_graph"}]
    return [*preferred, *bond_rows]


def _filter_rows_for_correction_query(
    rows: Sequence[SurfaceMemoryFactRow],
    *,
    query: str | None,
) -> list[SurfaceMemoryFactRow]:
    if not rows:
        return []
    query_text = _clean_text(query)
    if not _looks_like_correction_memory_query(query_text):
        return list(rows)
    preferred = [row for row in rows if _looks_like_correction_memory_fact(row.fact)]
    if preferred:
        return preferred
    return list(rows)


def _filter_rows_for_anaphoric_selection(
    rows: Sequence[SurfaceMemoryFactRow],
    *,
    query: str | None,
) -> list[SurfaceMemoryFactRow]:
    if not rows:
        return []
    query_text = _clean_text(query)
    if not _looks_like_anaphoric_selection_query(query_text):
        return list(rows)
    scrubbed: list[SurfaceMemoryFactRow] = []
    for row in rows:
        store_id = _clean_text(row.store_id).lower()
        fact = _clean_text(row.fact)
        if (
            "当前回合摘要" in fact
            or ("用户提到：" in fact and "回复：" in fact)
            or (query_text and query_text in fact and ("回复" in fact or "Assistant:" in fact))
            or _looks_like_anaphoric_scheduler_noise(fact)
        ):
            continue
        scrubbed.append(row)
    if not scrubbed:
        return list(rows)
    if _looks_like_specific_item_reference(query_text):
        matched_rows = [row for row in scrubbed if _specific_item_row_matches(row, query=query_text)]
        entity_rows = [row for row in matched_rows if _clean_text(row.store_id).lower() == "life_memory_entity"]
        if entity_rows:
            return [
                *entity_rows,
                *(row for row in scrubbed if _clean_text(row.store_id).lower() in {"neo4j_bond", "bond_graph"}),
            ]
        focused_collection_rows = [
            row
            for row in matched_rows
            if _clean_text(row.store_id).lower() == "life_memory_collection"
            and not _looks_like_broad_collection_fact(row.fact)
        ]
        if focused_collection_rows:
            return [
                *focused_collection_rows,
                *(row for row in scrubbed if _clean_text(row.store_id).lower() in {"neo4j_bond", "bond_graph"}),
            ]
    primary_rows = [
        row
        for row in scrubbed
        if _clean_text(row.store_id).lower() in {"life_memory_collection", "life_memory_entity", "life_episode"}
    ]
    if primary_rows:
        return [
            *primary_rows,
            *(row for row in scrubbed if _clean_text(row.store_id).lower() in {"neo4j_bond", "bond_graph"}),
        ]
    return scrubbed


def _filter_rows_for_collection_scope(
    rows: Sequence[SurfaceMemoryFactRow],
    *,
    query: str | None,
) -> list[SurfaceMemoryFactRow]:
    if not rows:
        return []
    query_text = _clean_text(query)
    if not _looks_like_collection_scope_query(query_text):
        return list(rows)
    collection_rows = [
        row
        for row in rows
        if _clean_text(row.store_id).lower() == "life_memory_collection"
    ]
    if not collection_rows:
        return list(rows)
    collection_rows = _dedupe_collection_scope_rows(collection_rows)
    collection_rows = sorted(collection_rows, key=_collection_row_prefer_key, reverse=True)[:1]
    return [
        *collection_rows,
        *(row for row in rows if _clean_text(row.store_id).lower() in {"neo4j_bond", "bond_graph"}),
    ]


def _dedupe_collection_scope_rows(rows: Sequence[SurfaceMemoryFactRow]) -> list[SurfaceMemoryFactRow]:
    best_by_scope: dict[str, SurfaceMemoryFactRow] = {}
    for row in rows:
        scope_key = _collection_scope_key_from_fact(row.fact) or row.source_ref or row.fact
        current = best_by_scope.get(scope_key)
        if current is None or _collection_row_prefer_key(row) > _collection_row_prefer_key(current):
            best_by_scope[scope_key] = row
    return list(best_by_scope.values())


def _collection_row_prefer_key(row: SurfaceMemoryFactRow) -> tuple[int, float, int]:
    return (
        _collection_item_count_from_fact(row.fact),
        float(row.score or 0.0),
        _collection_scope_prefer_rank(_collection_scope_key_from_fact(row.fact)),
    )


def _collection_scope_prefer_rank(scope_key: str) -> int:
    value = _clean_text(scope_key)
    if value.startswith("rolling:"):
        return 3
    if value == "recent":
        return 2
    if value.startswith("daily:") or value.startswith("delivery:"):
        return 1
    return 0


def _collection_item_count_from_fact(text: str) -> int:
    match = re.search(r"记录\s*(\d+)\s*个条目", str(text or ""))
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _collection_scope_key_from_fact(text: str) -> str:
    value = _clean_text(text)
    if "最近" in value[:16] and "集合" in value[:40]:
        return "recent"
    match = re.search(r"近\s*(\d+)\s*天.{0,12}集合", value)
    if match:
        return f"rolling:{match.group(1)}d"
    match = re.search(r"(20\d{2}-\d{2}-\d{2}).{0,12}集合", value)
    if match:
        return f"daily:{match.group(1)}"
    return ""


def _looks_like_anaphoric_scheduler_noise(text: str) -> bool:
    value = _clean_text(text).lower()
    if not value:
        return False
    if any(anchor in value for anchor in ("推荐", "lightnovel", "小说", "书", "qq", "wechat", "微信", "推送", "发送")):
        return False
    if "后台任务记忆" in value and "scheduler " in value:
        return True
    return any(
        token in value
        for token in (
            "emotion_decay_tick",
            "inner_world_numeric_tick",
            "silence_tick",
            "heartbeat",
            "health_check",
            "resource_guard",
        )
    )


def _build_fact_row(
    candidate: Mapping[str, Any],
    *,
    max_chars_per_fact: int,
) -> SurfaceMemoryFactRow | None:
    metadata = dict(candidate.get("metadata") or {}) if isinstance(candidate.get("metadata"), Mapping) else {}
    store_id = _clean_text(
        candidate.get("store_id")
        or metadata.get("store_id")
        or metadata.get("collection")
        or "episodic_vector"
    ).lower()
    if store_id not in SURFACE_MAIN_CHAIN_RECALL_STORES:
        return None
    text = _surface_fact_text(candidate, metadata=metadata, store_id=store_id, limit=max_chars_per_fact)
    if not text:
        return None
    fact = f"{_fact_prefix(store_id)}：{text}"
    return SurfaceMemoryFactRow(
        fact=fact,
        store_id=store_id or "unknown",
        source_ref=_clean_text(candidate.get("source_ref") or metadata.get("source_ref")),
        evidence_ref=_first_clean_ref(
            candidate.get("evidence_ref"),
            candidate.get("evidence_refs"),
            metadata.get("evidence_ref"),
            metadata.get("evidence_refs"),
            metadata.get("evidence_refs_primary"),
        ),
        score=_coerce_float(candidate.get("confidence"), default=_coerce_float(candidate.get("score"))),
    )


def _first_clean_ref(*values: Any) -> str:
    for value in values:
        for item in _coerce_sequence(value):
            cleaned = _clean_text(item)
            if cleaned:
                return cleaned
    return ""


def _surface_fact_text(
    candidate: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any],
    store_id: str,
    limit: int,
) -> str:
    raw = _clean_text(candidate.get("snippet") or candidate.get("text"))
    if not raw:
        return ""
    if _clean_text(store_id).lower() == "semantic_memory":
        return _surface_safe_text(_humanize_semantic_memory_recall_text(raw, metadata=metadata), limit=limit)
    if _clean_text(store_id).lower() in {"life_episode", "life_memory_collection"}:
        structured = _structured_life_episode_recall_text(raw, metadata=metadata, limit=limit)
        if structured:
            return _surface_safe_text(structured, limit=max(limit, 420))
    if _clean_text(store_id).lower() == "life_episode":
        raw = _humanize_life_episode_recall_text(raw, metadata=metadata)
    return _surface_safe_text(raw, limit=limit)


def _humanize_semantic_memory_recall_text(text: str, *, metadata: Mapping[str, Any]) -> str:
    value = _clean_text(text)
    value = re.sub(r"^(?:记忆线索|长期记忆|长期事实|召回线索)\s*[:：]\s*", "", value).strip()
    tags = " ".join(str(item or "") for item in _coerce_sequence(metadata.get("tags"))).lower()
    if "semantic_kind:user_preference" in tags or _looks_like_preference_rule_memory_fact(value):
        value = re.sub(r"^(?:用户偏好|稳定偏好|偏好|用户要求|要求|规则|约束)\s*[:：]\s*", "", value).strip()
    if "semantic_kind:relationship_feedback" in tags:
        value = re.sub(r"^(?:关系反馈|质量反馈|互动反馈)\s*[:：]\s*", "", value).strip()
    if "semantic_kind:commitment" in tags:
        value = re.sub(r"^(?:承诺和待办|承诺完成|承诺取消|待办|承诺)\s*[:：]\s*", "", value).strip()
    if "semantic_kind:self_change" in tags:
        value = re.sub(r"^(?:自我改变|能力改变|策略改变)\s*[:：]\s*", "", value).strip()
    if "semantic_kind:action_pattern" in tags:
        value = _humanize_action_pattern_recall_text(value)
    return value


def _humanize_action_pattern_recall_text(text: str) -> str:
    value = re.sub(r"^(?:主动行动模式|行动模式|主动行动)\s*[:：]\s*", "", _clean_text(text)).strip()
    if not value:
        return ""
    value = re.sub(
        r"；已记录\s*\d+\s*次深读/回看行动(?:，其中[^。]+)?",
        "；这不是口头说说，之前确实有过实际回看记录",
        value,
    )
    value = value.replace("她会回去", "我会回去")
    value = value.replace("她应该直接说明", "我应该直接说明")
    value = value.replace("正文级证据", "正文")
    return value


def _structured_life_episode_recall_text(text: str, *, metadata: Mapping[str, Any], limit: int) -> str:
    items = _life_episode_recall_items(metadata)
    if not items:
        return ""
    overview = _collection_overview_from_metadata(metadata, item_count=len(items)) or _humanize_life_episode_recall_text(text, metadata=metadata)
    overview = _truncate_text(overview, limit=min(max(120, limit // 2), 220))
    item_lines: list[str] = []
    max_items = _structured_recall_item_limit(limit=limit, available=len(items))
    compact_many = max_items > 4
    for index, item in enumerate(items[:max_items], start=1):
        title = _first_item_text(item, ("title", "title_display", "item_title", "name", "label"), limit=74)
        if not title:
            continue
        parts = [f"{index}. {title}"]
        rationale = _first_item_text(item, ("selection_reason", "rationale", "reason", "why", "look_point"), limit=50 if compact_many else 80)
        intro = _first_item_text(item, ("intro_snippet", "intro", "description", "content_excerpt", "body_excerpt", "item_summary", "summary", "abstract"), limit=58 if compact_many else 96)
        topic = _first_item_text(item, ("genre", "category", "topic", "theme", "tags"), limit=50)
        update = _first_item_text(item, ("update_time", "updated_at", "freshness"), limit=32)
        heat = _heat_text(item)
        detail_url = "" if compact_many else _first_item_text(item, ("detail_url", "url", "source_url", "source_ref"), limit=180)
        if rationale:
            parts.append(f"看点：{rationale}")
        if intro and intro != rationale:
            parts.append(f"简介：{intro}")
        if topic:
            parts.append(f"题材：{topic}")
        if update:
            parts.append(f"更新：{update}")
        if heat:
            parts.append(f"热度：{heat}")
        if detail_url:
            parts.append(f"详情：{detail_url}")
        item_lines.append("｜".join(parts))
    if not item_lines:
        return ""
    joined_items = "；".join(item_lines)
    prefix = f"{overview}；" if overview else ""
    return _clean_text(f"{prefix}可比较证据：{joined_items}")


def _structured_recall_item_limit(*, limit: int, available: int) -> int:
    budget = max(0, int(limit or 0))
    if budget >= 1200:
        return min(max(1, int(available or 0)), 12)
    if budget >= 860:
        return min(max(1, int(available or 0)), 10)
    if budget >= 680:
        return min(max(1, int(available or 0)), 8)
    return min(max(1, int(available or 0)), 4)


def _collection_overview_from_metadata(metadata: Mapping[str, Any], *, item_count: int) -> str:
    if _clean_text(metadata.get("store_id")).lower() != "life_memory_collection":
        return ""
    scope_key = _clean_text(metadata.get("scope_key"))
    domain = _clean_text(metadata.get("domain") or "collection")
    count = _coerce_int(metadata.get("item_count"), default=max(0, int(item_count or 0)))
    domain_label = "轻小说推荐" if "lightnovel" in domain or "轻小说" in domain else domain.replace("_", " ")
    if scope_key == "recent":
        return f"最近{domain_label}集合，记录 {count} 个条目"
    rolling = re.fullmatch(r"rolling:(\d+)d", scope_key)
    if rolling:
        return f"近 {rolling.group(1)} 天{domain_label}集合，记录 {count} 个条目"
    if scope_key.startswith("daily:"):
        return f"{scope_key.removeprefix('daily:')} 当天{domain_label}集合，记录 {count} 个条目"
    if scope_key.startswith("delivery:"):
        return f"本次{domain_label}集合，记录 {count} 个条目"
    title = _clean_text(metadata.get("title"))
    return f"{title}，记录 {count} 个条目" if title else ""


def _life_episode_recall_items(metadata: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    containers: list[Mapping[str, Any]] = []
    episode_meta = metadata.get("episode_metadata")
    if isinstance(episode_meta, Mapping):
        containers.append(episode_meta)
    for evidence in list(metadata.get("evidence") or []):
        if not isinstance(evidence, Mapping):
            continue
        payload = evidence.get("payload")
        if not isinstance(payload, Mapping):
            continue
        containers.append(payload)
        payload_meta = payload.get("metadata")
        if isinstance(payload_meta, Mapping):
            containers.append(payload_meta)
    containers.append(metadata)

    items: list[Mapping[str, Any]] = []
    for container in containers:
        for key in (
            "selected_recommendations",
            "recommendations",
            "selected_items",
            "items",
            "cards",
            "delivered_items",
        ):
            raw = container.get(key)
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
                continue
            for item in raw:
                if isinstance(item, Mapping) and item not in items:
                    items.append(item)
            if items:
                return items
    return items


def _first_item_text(item: Mapping[str, Any], keys: Sequence[str], *, limit: int) -> str:
    for key in keys:
        raw = item.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            raw = "、".join(_clean_text(value) for value in raw if _clean_text(value))
        value = _truncate_text(_clean_text(raw), limit=limit)
        if value:
            return value
    return ""


def _heat_text(item: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key, label in (
        ("view_count", "浏览"),
        ("views", "浏览"),
        ("comment_count", "评论"),
        ("comments", "评论"),
        ("hotness", "热度"),
        ("heat", "热度"),
    ):
        value = _first_item_text(item, (key,), limit=24)
        if value:
            parts.append(f"{value}{label}" if not any(marker in value for marker in (label, "热度")) else value)
    return " / ".join(dict.fromkeys(parts))


def _humanize_life_episode_recall_text(text: str, *, metadata: Mapping[str, Any]) -> str:
    value = _clean_text(text)
    if not value:
        return ""
    compact = re.sub(r"\s+", "", value)
    if any(token in compact for token in ("文本质量", "不像人话", "不像正常人", "台词腔", "很呆", "太呆")):
        return "用户强烈反馈：回答要像正常聊天，避免模板腔、台词腔和生硬的证据条目话术。"
    actionish = _clean_text(metadata.get("episode_kind")).lower() in {
        "action_episode",
        "background_episode",
        "perception_episode",
    }
    if actionish and "；" in value:
        segments = [_clean_text(part) for part in value.split("；") if _clean_text(part)]
        meaningful = [part for part in segments if not part.upper().startswith(("CODEX_PRODUCTION ", "ACTION ", "BACKGROUND "))]
        numbered_count = sum(1 for part in meaningful if re.search(r"(?<!\d)\d{1,2}[.、]\s*", part))
        value = "；".join(meaningful) if numbered_count >= 2 else (meaningful[-1] if meaningful else segments[-1])
    return _compress_numbered_recall_text(value)


def _compress_numbered_recall_text(text: str, *, item_limit: int = 42, max_items: int = 6) -> str:
    value = _clean_text(text)
    matches = list(re.finditer(r"(?<!\d)(\d{1,2})[.、]\s*([^；\n]+)", value))
    if len(matches) < 2:
        return value
    prefix = _truncate_text(value[: matches[0].start()], limit=90)
    items: list[str] = []
    for match in matches[: max(1, int(max_items))]:
        index = match.group(1)
        item_text = _truncate_text(match.group(2), limit=max(12, int(item_limit)))
        if item_text:
            items.append(f"{index}. {item_text}")
    if not items:
        return value
    return _clean_text(f"{prefix}{'：' if prefix and not prefix.endswith(('：', ':')) else ''}{'；'.join(items)}")


def _fact_prefix(store_id: str) -> str:
    normalized = _clean_text(store_id).lower()
    if normalized == "semantic_memory":
        return "长期事实"
    if normalized == "life_memory_collection":
        return "集合记忆"
    if normalized == "life_memory_entity":
        return "实体记忆"
    if normalized in {"life_episode", "episodic_vector", "episodic", "sqlite_ledger"}:
        return "记忆线索"
    if normalized in {"neo4j_bond", "bond_graph"}:
        return "关系线索"
    return "召回线索"


def _build_semantic_usage_trace(rows: Sequence[SurfaceMemoryFactRow], *, query: str | None = None) -> dict[str, Any]:
    semantic_rows = [row for row in rows if _clean_text(row.store_id).lower() == "semantic_memory"]
    if not semantic_rows:
        return {}
    query_text = _clean_text(query)
    explicit_lookup = _looks_like_preference_rule_memory_query(query_text) or _looks_like_explicit_memory_content_query(query_text)
    normalized_facts = [_truncate_text(item, limit=160) for item in _clean_list([_semantic_fact_for_surface(row.fact) for row in semantic_rows])[:4]]
    return {
        "schema": "Natsume.surface_semantic_usage.v1",
        "mode": "answer_memory_lookup" if explicit_lookup else "apply_silently",
        "fact_count": len(semantic_rows),
        "facts": normalized_facts,
        "source_labels_hidden": True,
        "rule": (
            "用户主动问记忆或偏好时，直接说出规则内容；普通任务里只把长期事实当行为约束。"
            if explicit_lookup
            else "把长期事实默默用于当前回复，不主动解释来源。"
        ),
    }


def _semantic_fact_for_surface(text: Any) -> str:
    value = _clean_text(text)
    value = re.sub(r"^(?:记忆线索|长期记忆|长期事实|召回线索)\s*[:：]\s*", "", value).strip()
    value = re.sub(r"^(?:用户偏好|稳定偏好|偏好|用户要求|要求|规则|约束)\s*[:：]\s*", "", value).strip()
    if re.match(r"^(?:主动行动模式|行动模式|主动行动)\s*[:：]", value):
        return _humanize_action_pattern_recall_text(value)
    value = re.sub(r"^(?:关系反馈|质量反馈|互动反馈|承诺和待办|承诺完成|承诺取消|待办|承诺|自我改变|能力改变|策略改变)\s*[:：]\s*", "", value).strip()
    return value


def _is_user_fact_store(store_id: str) -> bool:
    return _clean_text(store_id).lower() in SURFACE_USER_FACT_STORES


def _is_relationship_store(store_id: str) -> bool:
    return _clean_text(store_id).lower() in {"neo4j_bond", "bond_graph"}


def _build_packet_style_anchor(rows: Sequence[SurfaceMemoryFactRow], *, query: str | None = None) -> list[str]:
    if not rows:
        return []
    anchors = ["记忆只作为压缩事实承接，不展示召回过程"]
    store_ids = {_clean_text(row.store_id).lower() for row in rows}
    if _looks_like_anaphoric_selection_query(_clean_text(query)) and store_ids & SURFACE_USER_FACT_STORES:
        anchors.append("指代选择追问：把记忆线索当作刚才对话的当前上下文，自然接话，不要像查表或任务回执。")
        anchors.append("选择类追问：可以给一点主观口味，但不要只复述编号和标题，也不要编剧情。")
        if any(_has_comparison_detail(row.fact) for row in rows):
            anchors.append("选择类追问：基于已召回的看点、简介、题材、更新或热度做短比较，不要只看标题。")
    if store_ids & SURFACE_USER_FACT_STORES:
        anchors.append("用户事实记忆只补当前承接，不扩写成未确认细节")
    if "semantic_memory" in store_ids:
        anchors.append("长期事实只补稳定偏好和关系事实，不替代当前用户句子")
        anchors.append("长期偏好默认默默影响行为；只有用户明确问记得什么或偏好是什么时，才自然说出规则内容。")
    if store_ids & {"neo4j_bond", "bond_graph"}:
        anchors.append("按关系线索控制距离，不因为记忆升格亲密")
    return _clean_list(anchors)


def _build_packet_must_not_do(
    *,
    candidates: Sequence[Mapping[str, Any]],
    rows: Sequence[SurfaceMemoryFactRow],
    degraded: bool,
    query: str | None = None,
) -> list[str]:
    rules: list[str] = []
    for candidate in candidates:
        metadata = dict(candidate.get("metadata") or {}) if isinstance(candidate.get("metadata"), Mapping) else {}
        rules.extend(_coerce_sequence(candidate.get("must_not_do")))
        rules.extend(_coerce_sequence(metadata.get("must_not_do")))
    if any(row.evidence_ref or row.source_ref for row in rows):
        rules.append("不要在回复里暴露 memory://、evd://、chat:// 或召回来源")
    if any(_clean_text(row.store_id).lower() == "semantic_memory" for row in rows):
        rules.append("不要把“长期事实/长期记忆/用户偏好/根据记忆”这些标签说给用户听；把它们转成自然表达或直接落实到行为。")
    if any("不升级亲密" in row.fact or "别升级亲密" in row.fact for row in rows):
        rules.append("不要根据记忆升级亲密关系")
    if degraded:
        rules.append("不要声称记得未召回的细节")
    if _looks_like_anaphoric_selection_query(_clean_text(query)) and rows:
        rules.append("不要把指代选择追问当成无关新话题；不要说“突然问这个”“硬要说的话”“最近在看另一本”。")
        rules.append("不要只输出“第几本 + 标题”；需要像聊天一样给一句很短的偏好理由，理由只能来自标题或已召回简介。")
        rules.append("不要臆测用户昨天有没有看、看没看过、是不是忘了、终于想起来或终于来问；没有明确证据时只谈这些书本身。")
        if any(_has_comparison_detail(row.fact) for row in rows):
            rules.append("召回里有看点/简介/题材/更新/热度时，不要只按标题或名字判断。")
    return _clean_list(rules)


def _has_comparison_detail(text: str) -> bool:
    return bool(re.search(r"看点[:：]|简介[:：]|题材[:：]|更新[:：]|热度[:：]|评论|浏览", str(text or "")))


def _packet_confidence(rows: Sequence[SurfaceMemoryFactRow], *, output: Mapping[str, Any]) -> float:
    if rows:
        return _clamp01(max(float(row.score or 0.0) for row in rows))
    return _clamp01(_coerce_float(output.get("confidence")))


def _extract_route_ids(output: Mapping[str, Any], *, allowed_store_ids: Sequence[str] | set[str] | frozenset[str] | None = None) -> list[str]:
    allowed = {_clean_text(item).lower() for item in list(allowed_store_ids or []) if _clean_text(item)}
    route_ids: list[str] = []
    for route in list(output.get("routes") or []):
        if isinstance(route, Mapping):
            store_id = _clean_text(route.get("store_id") or route.get("collection") or route.get("id")).lower()
            if allowed and store_id and store_id not in allowed:
                continue
            route_id = _clean_text(route.get("route_id") or route.get("store_id") or route.get("id"))
        else:
            route_id = _clean_text(route)
        if route_id and route_id not in route_ids:
            route_ids.append(route_id)
    for row in list(output.get("route_rows") or []):
        if not isinstance(row, Mapping):
            continue
        store_id = _clean_text(row.get("store_id") or row.get("collection") or row.get("route_id")).lower()
        if allowed and store_id and store_id not in allowed:
            continue
        route_id = _clean_text(row.get("route_id") or row.get("store_id"))
        if route_id and route_id not in route_ids:
            route_ids.append(route_id)
    return route_ids


def _build_evidence_refs(
    *,
    request_id: str | None,
    session_id: str | None,
    person_id: str | None,
    evidence_refs: Sequence[str] | None,
) -> list[str]:
    refs = _clean_list(evidence_refs)
    if _clean_text(request_id):
        refs.append(f"chat://request/{_clean_text(request_id)}")
    if _clean_text(session_id):
        refs.append(f"chat://session/{_clean_text(session_id)}")
    if _clean_text(person_id):
        refs.append(f"chat://person/{_clean_text(person_id)}")
    return _unique(refs)


def _looks_like_memory_acceptance_query(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    if any(token in normalized for token in ("回忆检查", "记忆验收", "同会话验收", "新会话验收", "最终同会话验收")):
        return True
    if "验收" in normalized and any(token in normalized for token in ("列出", "复述", "回答", "说出")):
        return True
    return bool(
        any(token in normalized for token in ("测试代号", "海边灯塔", "预热指标", "推荐格式"))
        and any(token in normalized for token in ("是什么", "指什么", "列出", "复述", "当前", "刚才", "刚刚"))
    )


def _has_recall_hint(text: str) -> bool:
    lowered = _clean_text(text).lower()
    return any(hint.lower() in lowered for hint in _RECALL_HINTS) or _looks_like_memory_acceptance_query(text)


def _looks_like_anaphoric_selection_query(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    return bool(_ANAPHORIC_SELECTION_RE.search(normalized))


def _looks_like_specific_item_reference(text: str) -> bool:
    return bool(_specific_item_query_prefix(text))


def _specific_item_query_prefix(text: str) -> str:
    value = _clean_text(text)
    if not value or any(token in value for token in ("这三本", "这几本", "这些", "那些", "十几本")):
        return ""
    quoted = re.search(r"《([^》]{2,80})》", value)
    if quoted and any(token in value for token in ("好看", "能看", "值得看", "怎么样", "如何", "推荐", "想看")):
        return _compact_text(quoted.group(1))
    marker = re.search(r"(.{2,80}?)(?:这本书|这部书|这本|这部|这书|它).{0,12}(?:好看|能看|值得看|怎么样|如何|推荐|想看)", value)
    if not marker:
        return ""
    prefix = re.sub(r"[\s，。！？?、：:；;（）()\[\]【】《》「」『』]+", "", marker.group(1))
    prefix = re.sub(r"^(?:我问你|问你|你觉得|你说|刚才|刚刚|那个|这个|那本|这本)+", "", prefix)
    compact = _compact_text(prefix)
    if len(compact) < 2:
        return ""
    generic = {"我", "你", "他", "她", "它", "这", "那", "哪", "本", "部", "个", "书", "小说"}
    if all(char in generic for char in compact):
        return ""
    return compact


def _specific_item_row_matches(row: SurfaceMemoryFactRow, *, query: str) -> bool:
    prefix = _specific_item_query_prefix(query)
    if not prefix:
        return False
    return prefix in _compact_specific_item_text(row.fact)


def _looks_like_broad_collection_fact(text: str) -> bool:
    value = _clean_text(text)
    return any(
        token in value
        for token in (
            "最近轻小说推荐集合",
            "轻小说推荐集合",
            "经历集合",
            "近 3 天",
            "近 7 天",
            "近 30 天",
        )
    ) or bool(
        re.search(r"(?:最近|近\s*\d+\s*天|20\d{2}-\d{2}-\d{2}).{0,30}记录\s*\d+\s*个条目", value)
    )


def _compact_text(text: Any) -> str:
    return re.sub(r"\s+", "", _clean_text(text)).lower()


def _compact_specific_item_text(text: Any) -> str:
    compact = _compact_text(text)
    return re.sub(r"[\s，。！？?、：:；;（）()\[\]【】《》「」『』｜|/\\]+", "", compact)


def _looks_like_collection_scope_query(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    if any(token in normalized for token in ("昨天", "前天", "这几天", "最近", "今日", "今天", "十几本")) and any(
        token in normalized for token in ("推荐", "三本", "几本", "十几本", "哪些", "书", "小说")
    ):
        return True
    return bool("推荐过" in normalized and any(token in normalized for token in ("哪些", "几", "最近")))


def _looks_like_memory_write(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    if any(token in normalized for token in ("还记得", "记得吗", "记不记得", "我之前", "之前说", "以前说", "上次说", "我说过")):
        return False
    if any(token in normalized for token in ("记一下", "记住", "帮我记", "替我记", "记着", "记下来")):
        return True
    preference_update_markers = (
        "以后",
        "不要",
        "别再",
        "必须",
        "优先使用",
        "主链不要",
        "不要用",
        "别用",
        "改成",
        "修改下",
        "换成",
        "关了",
        "关闭",
    )
    preference_domains = (
        "格式",
        "推送",
        "推荐",
        "发送",
        "回复",
        "卡片",
        "简介",
        "模型",
        "pro",
        "拟人化",
        "模板",
        "台词腔",
        "分段",
        "换行",
    )
    return any(marker in normalized for marker in preference_update_markers) and any(
        domain in normalized for domain in preference_domains
    )


def _looks_like_explicit_memory_content_query(text: str) -> bool:
    normalized = _clean_text(text)
    return bool(normalized) and (
        any(token in normalized for token in ("让你记", "叫你记", "刚才记", "刚刚记", "记了什么", "记住什么", "记一下"))
        or ("还记得" in normalized and any(token in normalized for token in ("我说", "要求", "格式", "要怎样", "怎么发", "怎样发")))
        or ("我" in normalized and "记" in normalized and "什么" in normalized)
        or _looks_like_memory_acceptance_query(normalized)
    )


def _looks_like_explicit_memory_fact(text: str) -> bool:
    normalized = _clean_text(text)
    if any(token in normalized for token in ("项目事实", "测试事实", "记忆事实", "测试代号", "海边灯塔", "预热指标", "修正为", "纠正为", "更正为", "改成", "作废", "为准")):
        return True
    if any(token in normalized for token in ("记一下", "记住", "帮我记", "替我记", "记着", "记下来")):
        return True
    if any(token in normalized for token in ("喜欢", "不喜欢", "偏好", "讨厌", "常喝", "常吃")):
        return True
    if _looks_like_preference_rule_memory_fact(normalized):
        return True
    return False


def _looks_like_preference_rule_memory_query(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    if any(token in normalized for token in ("偏好", "要求", "规则", "约束", "不要", "别再", "必须", "优先使用", "主链不要", "用什么模型")):
        return True
    if not any(token in normalized for token in ("记得", "记不记得", "还记得", "我说", "我之前", "之前说", "以前说", "上次说", "以后")):
        return False
    return any(
        token in normalized
        for token in (
            "格式",
            "怎么发",
            "怎样发",
            "要怎样",
            "换行",
            "模板腔",
            "不像人话",
            "正常聊天",
            "台词腔",
            "文本质量",
            "很呆",
            "分段",
            "卡片",
            "简介后",
            "推荐",
            "推送",
            "发送",
        )
    )


def _relationship_feedback_query_topic(text: str | None) -> str:
    normalized = _clean_text(text).lower()
    if not normalized:
        return ""
    if any(token in normalized for token in ("不像人话", "正常聊天", "模板腔", "台词腔", "文本质量", "很呆", "太呆")):
        return "natural_chat"
    if any(token in normalized for token in ("胡说八道", "编造", "召回", "证据", "没核实", "核实", "事实")):
        return "recall_quality"
    if any(token in normalized for token in ("白费功夫", "服务没起来", "没起来", "连不上")):
        return "runtime_readiness"
    return ""


def _preference_rule_query_topic(text: str | None) -> str:
    normalized = _clean_text(text).lower()
    if not normalized:
        return ""
    if any(
        marker in normalized
        for marker in (
            "推荐格式",
            "推送格式",
            "怎么发",
            "怎样发",
            "要怎样",
            "换行",
            "卡片",
            "分段",
            "简介",
            "总览",
            "拟人化",
            "轻小说推荐",
        )
    ):
        return "recommendation_format"
    if any(marker in normalized for marker in ("不要用pro", "不用pro", "pro", "模型池", "deepseek", "gemini", "codex", "主链", "模型路由")):
        return "llm_routing"
    return ""


def _preference_rule_fact_matches_topic(text: str, *, topic: str) -> bool:
    normalized = _clean_text(text).lower()
    if not normalized:
        return False
    recommendation_markers = (
        "推荐轻小说",
        "轻小说",
        "推荐格式",
        "推送",
        "总览",
        "卡片",
        "分段",
        "换行",
        "简介",
        "拟人化",
        "每本书",
    )
    routing_markers = ("thalamus", "模型池", "deepseek", "gemini", "codex", "不要用 pro", "不要用pro", "不用pro", "主链不要", "模型路由")
    if topic == "recommendation_format":
        return any(marker in normalized for marker in recommendation_markers)
    if topic == "llm_routing":
        return any(marker in normalized for marker in routing_markers)
    return True


def _relationship_feedback_fact_matches_topic(text: str, *, topic: str) -> bool:
    normalized = _clean_text(text).lower()
    if not normalized:
        return False
    natural_markers = ("回答要像正常聊天", "模板腔", "台词腔", "更自然的聊天", "生硬", "文本质量", "不像人话")
    recall_markers = ("胡说八道", "编造", "召回", "证据", "没核实", "核实", "事实")
    runtime_markers = ("白费功夫", "服务没起来", "没起来", "连不上")
    if _relationship_feedback_fact_is_lookup_echo(normalized):
        return False
    if topic == "natural_chat":
        if any(marker in normalized for marker in ("回答要像正常聊天", "台词腔", "更自然的聊天", "生硬", "文本质量", "不像人话")):
            return True
        if "模板腔" in normalized and not _relationship_feedback_fact_is_recommendation_format_preference(normalized):
            return True
        return any(marker in normalized for marker in ("文本质量", "不像人话")) and not any(
            marker in normalized for marker in recall_markers
        )
    if topic == "recall_quality":
        return any(marker in normalized for marker in recall_markers)
    if topic == "runtime_readiness":
        return any(marker in normalized for marker in runtime_markers)
    return any(marker in normalized for marker in natural_markers)


def _relationship_feedback_fact_is_lookup_echo(normalized: str) -> bool:
    if "当前回合摘要" not in normalized and "用户提到「" not in normalized:
        return False
    if any(marker in normalized for marker in ("回答要像正常聊天", "更自然的聊天方式", "用户强烈反馈")):
        return False
    return any(
        marker in normalized
        for marker in (
            "是哪里的问题",
            "哪里的问题",
            "为什么",
            "怎么",
            "吗",
            "？",
            "我之前说",
            "之前说",
            "还记得",
        )
    )


def _relationship_feedback_fact_is_recommendation_format_preference(normalized: str) -> bool:
    return "模板腔" in normalized and any(
        marker in normalized
        for marker in (
            "推荐",
            "推送",
            "格式",
            "卡片",
            "分段",
            "换行",
            "简介",
            "每本书",
            "轻小说",
        )
    )


def _looks_like_open_loop_memory_query(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    return any(
        token in normalized
        for token in (
            "待办",
            "承诺",
            "open loop",
            "open_loop",
            "openloop",
            "说好",
            "还没做",
            "未完成",
            "没完成",
            "完成了吗",
            "跑完了吗",
            "做完了吗",
        )
    )


def _surface_query_wants_closed_open_loop(text: str) -> bool:
    normalized = _clean_text(text).lower()
    if any(token in normalized for token in ("未完成", "没完成", "还没", "要继续", "继续做", "待做", "open")):
        return False
    return any(token in normalized for token in ("已完成", "完成了", "做完", "搞定", "关闭", "closed", "done", "resolved"))


def _looks_like_open_loop_memory_fact(text: str) -> bool:
    normalized = _clean_text(text).lower()
    return any(
        token in normalized
        for token in (
            "承诺和待办",
            "承诺完成",
            "承诺取消",
            "待办",
            "承诺",
            "open_loop",
            "openloop",
            "后续要",
            "下次继续",
        )
    )


def _looks_like_closed_open_loop_memory_fact(text: str) -> bool:
    normalized = _clean_text(text).lower()
    return any(
        token in normalized
        for token in (
            "承诺完成",
            "承诺取消",
            "已完成",
            "完成了",
            "已经完成",
            "做完",
            "搞定",
            "关闭这个待办",
            "关掉这个待办",
            "open_loop:closed",
            "open_loop:cancelled",
            "open_loop:superseded",
        )
    )


def _looks_like_self_change_memory_query(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    return any(
        token in normalized
        for token in (
            "自我改变",
            "改过什么",
            "改了什么",
            "记忆系统",
            "模型路由",
            "能力",
            "策略",
            "主链迁移",
            "删除资源管理器",
        )
    )


def _looks_like_self_change_memory_fact(text: str) -> bool:
    normalized = _clean_text(text)
    if _looks_like_open_loop_memory_fact(normalized):
        return False
    if any(
        token in normalized
        for token in (
            "自我改变",
            "模型路由",
            "能力被删除",
            "删除资源管理器",
            "策略被用户纠正",
            "主链迁移",
        )
    ):
        return True
    if any(
        token in normalized
        for token in (
            "生命经历",
            "经历锚点",
            "主动记忆",
            "可召回记忆",
            "语义记忆",
            "遗忘系统",
            "睡眠回放",
            "所有做过的事",
            "真实生命",
        )
    ):
        return True
    return "记忆系统" in normalized and any(
        token in normalized
        for token in (
            "生命经历",
            "经历锚点",
            "主动记忆",
            "可召回记忆",
            "语义记忆",
            "遗忘系统",
            "睡眠回放",
            "所有做过的事",
            "真实生命",
        )
    )


def _looks_like_preference_rule_memory_fact(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    if any(token in normalized for token in ("命中偏好", "轻小说推荐报告", "推荐候选", "推荐列表")):
        return False
    return any(
        token in normalized
        for token in (
            "用户偏好",
            "关系反馈",
            "偏好",
            "用户要求",
            "以后",
            "不要",
            "别再",
            "必须",
            "优先使用",
            "应该",
            "格式",
            "换行",
            "模板腔",
            "不像人话",
            "正常聊天",
            "台词腔",
            "文本质量",
            "很呆",
            "分段",
            "卡片格式",
            "简介后",
            "不要用",
            "别用",
        )
    )


def _looks_like_correction_memory_query(text: str) -> bool:
    normalized = _clean_text(text)
    return bool(normalized) and any(token in normalized for token in ("记错", "不对", "错了", "纠正", "撤回", "没有这本", "没有封面"))


def _looks_like_correction_memory_fact(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    if any(token in normalized for token in ("我没记错", "没有记错", "明明记得", "确实有只猫", "记性比我还差")):
        return False
    return any(token in normalized for token in ("不对", "没有封面有猫", "没有这本", "没有这种", "你说得对", "撤回", "纠正", "修正"))


def _should_skip_low_signal_recall(
    *,
    user_message: str,
    task_mode: str,
    has_recall_hint: bool,
) -> bool:
    normalized = _clean_text(user_message)
    if has_recall_hint:
        return False
    if task_mode not in _LOW_SIGNAL_TASKS:
        return False
    if normalized in _SHORT_ACKS:
        return True
    return len(normalized) <= 8


def _infer_lookback_days(text: str) -> int | None:
    normalized = _clean_text(text)
    if any(token in normalized for token in ("刚才", "刚刚", "上一句", "前一句")):
        return 1
    if any(token in normalized for token in ("昨天", "昨晚")):
        return 2
    if any(token in normalized for token in ("前天",)):
        return 3
    if any(token in normalized for token in ("上次", "最近")):
        return 7
    if any(token in normalized for token in ("之前", "以前")):
        return 30
    return None


def _surface_safe_text(value: Any, *, limit: int) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = _URI_RE.sub("", text)
    text = text.replace("memory written:", "")
    text = text.replace("User:", "用户提到：").replace("Assistant:", "回复：")
    text = text.replace("当前人格回应", "回复").replace("旧角色回应", "回复")
    text = _JSON_MARK_RE.sub("", text)
    text = _FIELD_LABEL_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text).strip(" ,，;；、")
    return _truncate_text(text, limit=limit)


def _truncate_text(value: Any, *, limit: int) -> str:
    text = _clean_text(value)
    cap = max(1, int(limit or 1))
    if len(text) <= cap:
        return text
    return text[: cap - 1].rstrip() + "…"


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _clean_list(values: Sequence[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _unique(values: Sequence[Any] | Any) -> list[str]:
    if values is None:
        items: Sequence[Any] = []
    elif isinstance(values, (str, bytes, bytearray)):
        items = [values]
    elif isinstance(values, Sequence):
        items = values
    else:
        try:
            items = list(values)
        except TypeError:
            items = [values]
    return _clean_list(items)


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = _clean_text(value)
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value or 0.0)))


__all__ = [
    "MemoryRecallPacket",
    "RECALL_POLICY_MAY",
    "RECALL_POLICY_MUST",
    "RECALL_POLICY_NO",
    "SURFACE_MEMORY_EMPTY_TURN",
    "SURFACE_MEMORY_EVIDENCE_REQUIRED",
    "SURFACE_MEMORY_RECALL_COVERAGE_FAILED",
    "SURFACE_MEMORY_RECALL_FAILED",
    "SURFACE_MEMORY_RECALL_SKIPPED",
    "SURFACE_MEMORY_RECALL_VALIDATION_FAILED",
    "SURFACE_MEMORY_WRITE_FAILED",
    "SURFACE_BOND_STORES",
    "SURFACE_MAIN_CHAIN_RECALL_STORES",
    "SURFACE_USER_FACT_STORES",
    "SurfaceMemoryFactRow",
    "SurfaceMemoryRecallPlan",
    "SurfaceMemoryRecallPacket",
    "SurfaceMemoryWriteResult",
    "apply_surface_memory_recall_packet",
    "build_surface_input_with_memory_recall",
    "build_surface_memory_recall_decision_trace",
    "build_surface_memory_recall_query",
    "build_surface_memory_recall_packet",
    "build_surface_turn_memory_text",
    "memory_recall_packet_from_mapping",
    "plan_surface_memory_recall",
    "recall_surface_memory_for_context",
    "recall_surface_memory_packet",
    "write_surface_turn_memory",
]
