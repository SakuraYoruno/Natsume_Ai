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
    r"(?:这本书|这部书|这本|这部|这书|它).{0,8}(?:好看|能看|值得看|怎么样|如何))",
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
    "daily_greeting_cn",
    "daily_greeting_en",
    "first_ask",
    "greeting",
    "phatic",
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
            trace={"skipped": True, "plan": plan.as_dict()},
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
    return {
        "schema": "Natsume.surface_recall_need.v1",
        "query_kind": kind,
        "temporal_scope": _clean_text(temporal_scope),
        "target_domains": domains,
        "recall_contract_id": contract,
        "need_type": _recall_need_type(kind=kind, contract=contract, primary_layer=primary),
        "primary_memory_layer": primary,
        "expected_memory_layers": expected_layers,
        "min_collection_items": max(0, _coerce_int(min_collection_items)),
        "validation_requirements": required,
        "answer_obligations": obligations,
        "evidence_depth": evidence_depth,
    }


def _recall_need_type(*, kind: str, contract: str, primary_layer: str) -> str:
    if contract in {"collection_date", "collection_recent"} or kind in {"date_lookup", "collection_summary"}:
        return "collection"
    if kind == "anaphoric_selection":
        return "entity_or_collection_selection" if primary_layer == "life_memory_entity" else "collection_selection"
    if kind == "failure_debug":
        return "failure_episode"
    if kind == "preference_lookup":
        return "semantic_or_episode_preference"
    if kind == "correction":
        return "correction_chain"
    if kind == "self_change":
        return "self_change_episode"
    if kind == "event_lookup":
        return "episode_event"
    return "memory_hit"


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
    if not required:
        dimensions["validation"] = 1.0 if selected_layers else 0.0
    score = round(sum(float(value) for value in dimensions.values()) / max(1, len(dimensions)), 6)
    return {
        "schema": "Natsume.surface_recall_coverage.v1",
        "score": score,
        "passed": bool(validation_trace.get("passed", True)) and score >= 0.66,
        "missing": missing,
        "dimensions": {key: round(float(value), 6) for key, value in dimensions.items()},
        "required_count": len(required),
        "passed_required_count": len(passed_checks),
        "selected_item_count": selected_item_count,
        "min_collection_items": item_required,
        "evidence_ref_count": evidence_ref_count,
        "source_ref_count": source_ref_count,
        "selected_store_counts": dict(sorted(selected_store_counts.items())),
        "candidate_store_counts": dict(sorted(candidate_store_counts.items())),
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
) -> tuple[bool, str]:
    if requirement == "scope_match":
        expected = _expected_collection_scope_key(temporal_scope, ts_ms=_coerce_int(output.get("ts_ms")))
        scope_keys = _candidate_scope_keys(candidates)
        if not expected:
            return (bool(candidates), "all-time or unspecified scope accepts any non-empty candidate set")
        if expected == "recent":
            passed = any(key == "recent" or key.startswith("rolling:") for key in scope_keys)
            return passed, f"expected recent/rolling collection scope, seen={scope_keys}"
        return expected in scope_keys, f"expected {expected}, seen={scope_keys}"
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
        or _clean_text(query_kind) in {"anaphoric_selection", "collection_summary"}
    )
    if not should_filter:
        return rows
    matched: list[dict[str, Any]] = []
    for candidate in rows:
        candidate_domains = _candidate_domains(candidate)
        if not candidate_domains or candidate_domains.intersection(domains):
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
    return exact


def _expanded_target_domains(target_domains: Sequence[str] | None) -> set[str]:
    domains = {_clean_text(item) for item in list(target_domains or []) if _clean_text(item)}
    if "lightnovel_recommendation" in domains:
        domains.add("recommendation")
    if "recommendation" in domains:
        domains.add("lightnovel_recommendation")
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
            candidate.get("text"),
            candidate.get("snippet"),
        )
    )
    if "lightnovel" in text or "轻小说" in text or "lightnovel.fun" in text:
        domains.update({"lightnovel_recommendation", "recommendation"})
    elif "recommendation" in text or "推荐" in text:
        domains.add("recommendation")
    if any(token in text for token in ("failure", "failed", "timeout", "失败", "异常", "报错")):
        domains.add("failure")
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
    for key in ("items", "selected_recommendations", "recommendations", "selected_items", "delivered_items"):
        items = metadata.get(key)
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
            item_count = max(item_count, len(items))
    return max(item_count, _item_count_from_candidate_text(candidate))


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
            "scope_key": scope_key,
            "index": _coerce_int(item.get("position") or item_meta.get("position"), default=0),
            "title": title,
            "detail_url": source_ref if "lightnovel.fun" in source_ref else "",
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
            "index": _coerce_int(item.get("position"), default=0),
            "title": title,
            "detail_url": source_ref if "lightnovel.fun" in source_ref else "",
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
    rows = _filter_rows_for_preference_rule_query(rows, query=query)
    rows = _filter_rows_for_explicit_memory_query(rows, query=query)
    rows = _filter_rows_for_anaphoric_selection(rows, query=query)
    rows = _filter_rows_for_collection_scope(rows, query=query)
    rows = sorted(rows, key=_surface_fact_row_rank, reverse=True)
    selected: list[SurfaceMemoryFactRow] = []
    seen_stores: set[str] = set()
    for row in rows:
        if len(selected) >= cap:
            break
        if row.store_id in seen_stores:
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


def _surface_fact_row_rank(row: SurfaceMemoryFactRow) -> tuple[float, float, int]:
    store_id = _clean_text(row.store_id).lower()
    fact_text = _clean_text(row.fact).upper()
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
    return (float(terminal_background_priority), _coerce_float(row.score), store_priority)


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
    preferred = [row for row in rows if _looks_like_explicit_memory_fact(row.fact)]
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
    preferred = [row for row in rows if _looks_like_preference_rule_memory_fact(row.fact)]
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
        evidence_ref=_clean_text(
            candidate.get("evidence_ref")
            or candidate.get("evidence_refs")
            or metadata.get("evidence_ref")
            or metadata.get("evidence_refs_primary")
        ),
        score=_coerce_float(candidate.get("confidence"), default=_coerce_float(candidate.get("score"))),
    )


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
    if _clean_text(store_id).lower() in {"life_episode", "life_memory_collection"}:
        structured = _structured_life_episode_recall_text(raw, metadata=metadata, limit=limit)
        if structured:
            return _surface_safe_text(structured, limit=max(limit, 420))
    if _clean_text(store_id).lower() == "life_episode":
        raw = _humanize_life_episode_recall_text(raw, metadata=metadata)
    return _surface_safe_text(raw, limit=limit)


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
        intro = _first_item_text(item, ("intro_snippet", "intro", "description", "item_summary", "summary", "abstract"), limit=58 if compact_many else 96)
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
        return "长期记忆"
    if normalized == "life_memory_collection":
        return "集合记忆"
    if normalized == "life_memory_entity":
        return "实体记忆"
    if normalized in {"life_episode", "episodic_vector", "episodic", "sqlite_ledger"}:
        return "记忆线索"
    if normalized in {"neo4j_bond", "bond_graph"}:
        return "关系线索"
    return "召回线索"


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
        anchors.append("长期记忆只补稳定偏好和关系事实，不替代当前用户句子")
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
    if any("不升级亲密" in row.fact or "别升级亲密" in row.fact for row in rows):
        rules.append("不要根据记忆升级亲密关系")
    if degraded:
        rules.append("不要声称记得未召回的细节")
    if _looks_like_anaphoric_selection_query(_clean_text(query)) and rows:
        rules.append("不要把指代选择追问当成无关新话题；不要说“突然问这个”“硬要说的话”“最近在看另一本”。")
        rules.append("不要只输出“第几本 + 标题”；需要像聊天一样给一句很短的偏好理由，理由只能来自标题或已召回简介。")
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


def _has_recall_hint(text: str) -> bool:
    lowered = _clean_text(text).lower()
    return any(hint.lower() in lowered for hint in _RECALL_HINTS)


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
    return prefix in _compact_text(row.fact)


def _looks_like_broad_collection_fact(text: str) -> bool:
    value = _clean_text(text)
    return any(token in value for token in ("最近轻小说推荐集合", "近 3 天", "近 7 天", "近 30 天")) or bool(
        re.search(r"(?:最近|近\s*\d+\s*天).{0,30}记录\s*\d+\s*个条目", value)
    )


def _compact_text(text: Any) -> str:
    return re.sub(r"\s+", "", _clean_text(text)).lower()


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
    return any(token in normalized for token in ("记一下", "记住", "帮我记", "替我记", "记着", "记下来"))


def _looks_like_explicit_memory_content_query(text: str) -> bool:
    normalized = _clean_text(text)
    return bool(normalized) and (
        any(token in normalized for token in ("让你记", "叫你记", "刚才记", "刚刚记", "记了什么", "记住什么", "记一下"))
        or ("还记得" in normalized and any(token in normalized for token in ("我说", "要求", "格式", "要怎样", "怎么发", "怎样发")))
        or ("我" in normalized and "记" in normalized and "什么" in normalized)
    )


def _looks_like_explicit_memory_fact(text: str) -> bool:
    normalized = _clean_text(text)
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
    if not any(token in normalized for token in ("记得", "记不记得", "还记得", "我说", "要求", "以后", "不要", "别再", "必须", "偏好")):
        return False
    return any(token in normalized for token in ("格式", "怎么发", "怎样发", "要怎样", "换行", "模板腔", "分段", "卡片", "简介后", "推荐", "推送", "发送"))


def _looks_like_preference_rule_memory_fact(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    return any(
        token in normalized
        for token in (
            "用户偏好",
            "偏好",
            "以后",
            "不要",
            "别再",
            "必须",
            "应该",
            "格式",
            "换行",
            "模板腔",
            "分段",
            "卡片格式",
            "简介后",
        )
    )


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
    text = text.replace("新海天回应", "回复").replace("夏目蓝回应", "回复")
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
    "build_surface_memory_recall_query",
    "build_surface_memory_recall_packet",
    "build_surface_turn_memory_text",
    "memory_recall_packet_from_mapping",
    "plan_surface_memory_recall",
    "recall_surface_memory_for_context",
    "recall_surface_memory_packet",
    "write_surface_turn_memory",
]
