from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import re
from typing import Any

from host_brain.core.ids import now_ms
from host_brain.persona.isolated_surface_continuity import (
    ThreeLayerContinuityContext,
    build_surface_input_from_three_layer_context,
)
from host_brain.persona.isolated_surface_harness import IsolatedSurfaceInput


SURFACE_MEMORY_RECALL_FAILED = "SURFACE_MEMORY_RECALL_FAILED"
SURFACE_MEMORY_RECALL_SKIPPED = "SURFACE_MEMORY_RECALL_SKIPPED"
SURFACE_MEMORY_WRITE_FAILED = "SURFACE_MEMORY_WRITE_FAILED"
SURFACE_MEMORY_EVIDENCE_REQUIRED = "SURFACE_MEMORY_EVIDENCE_REQUIRED"
SURFACE_MEMORY_EMPTY_TURN = "SURFACE_MEMORY_EMPTY_TURN"

_URI_RE = re.compile(r"\b(?:chat|memory|ledger|evd|doc|memo)://[^\s，。；;、]+")
_SPACE_RE = re.compile(r"\s+")
_JSON_MARK_RE = re.compile(r"[{}\[\]\"]+")
_FIELD_LABEL_RE = re.compile(r"\b(?:text|content|detail|memory_text)\s*[:：]\s*", re.IGNORECASE)
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
    "daily_chat",
    "daily_good_morning",
    "daily_greeting_cn",
    "daily_greeting_en",
    "greeting",
    "presence",
    "short_ack",
    "wait",
})
_RECALL_TASKS = frozenset({"recall", "recall_memory", "memory_followup"})
_TECH_TASKS = frozenset({"advice_continue", "complaint", "help_takeover", "next_step", "small_failure", "write_takeover"})
_EMOTION_TASKS = frozenset({"apology", "bad_mood", "give_up", "headache", "quiet", "sleepy", "tired"})
_IDENTITY_TASKS = frozenset({"identity"})


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
class SurfaceMemoryRecallPacket:
    """Compressed recall output allowed to cross into the final surface layer."""

    query: str
    facts: tuple[str, ...] = ()
    rows: tuple[SurfaceMemoryFactRow, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    route_ids: tuple[str, ...] = ()
    candidate_count: int = 0
    degraded: bool = False
    reason_code: str | None = None
    trace: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "facts": list(self.facts),
            "rows": [row.as_dict() for row in self.rows],
            "evidence_refs": list(self.evidence_refs),
            "source_refs": list(self.source_refs),
            "route_ids": list(self.route_ids),
            "candidate_count": int(self.candidate_count),
            "degraded": bool(self.degraded),
            "reason_code": self.reason_code,
            "trace": dict(self.trace or {}),
        }


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
    target_store_ids: tuple[str, ...] = ()
    max_facts: int = 0
    max_chars_per_fact: int = 96
    time_lookback_days: int | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "should_recall": bool(self.should_recall),
            "query": self.query,
            "task_mode": self.task_mode,
            "target_store_ids": list(self.target_store_ids),
            "max_facts": int(self.max_facts),
            "max_chars_per_fact": int(self.max_chars_per_fact),
            "time_lookback_days": self.time_lookback_days,
            "reason": self.reason,
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
    has_recall_hint = _has_recall_hint(user_message)

    if not user_message:
        return SurfaceMemoryRecallPlan(
            should_recall=False,
            query=query,
            task_mode=task_mode,
            reason="empty_user_message",
        )
    if _should_skip_low_signal_recall(user_message=user_message, task_mode=task_mode, has_recall_hint=has_recall_hint):
        return SurfaceMemoryRecallPlan(
            should_recall=False,
            query=query,
            task_mode=task_mode,
            reason="low_signal_surface_turn",
        )

    if task_mode in _RECALL_TASKS or has_recall_hint:
        return SurfaceMemoryRecallPlan(
            should_recall=True,
            query=query,
            task_mode=task_mode,
            target_store_ids=("episodic_vector", "sqlite_ledger", "neo4j_bond"),
            max_facts=4,
            max_chars_per_fact=96,
            time_lookback_days=_infer_lookback_days(user_message),
            reason="explicit_memory_recall",
        )
    if task_mode in _TECH_TASKS:
        return SurfaceMemoryRecallPlan(
            should_recall=True,
            query=query,
            task_mode=task_mode,
            target_store_ids=("artifact_vault", "episodic_vector", "sqlite_ledger"),
            max_facts=3,
            max_chars_per_fact=112,
            time_lookback_days=_infer_lookback_days(user_message),
            reason="technical_or_task_context",
        )
    if task_mode in _EMOTION_TASKS:
        return SurfaceMemoryRecallPlan(
            should_recall=True,
            query=query,
            task_mode=task_mode,
            target_store_ids=("neo4j_bond", "episodic_vector"),
            max_facts=3,
            max_chars_per_fact=96,
            time_lookback_days=_infer_lookback_days(user_message),
            reason="emotion_or_relationship_context",
        )
    if task_mode in _IDENTITY_TASKS:
        return SurfaceMemoryRecallPlan(
            should_recall=True,
            query=query,
            task_mode=task_mode,
            target_store_ids=("world_canon", "episodic_vector"),
            max_facts=2,
            max_chars_per_fact=88,
            time_lookback_days=None,
            reason="identity_anchor_context",
        )
    return SurfaceMemoryRecallPlan(
        should_recall=True,
        query=query,
        task_mode=task_mode,
        target_store_ids=("episodic_vector",),
        max_facts=2,
        max_chars_per_fact=88,
        time_lookback_days=_infer_lookback_days(user_message),
        reason="daily_context_light_memory",
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
    )


def build_surface_memory_recall_packet(
    recall_result: Any,
    *,
    query: str | None = None,
    max_facts: int = 4,
    max_chars_per_fact: int = 96,
) -> SurfaceMemoryRecallPacket:
    output = _recall_output(recall_result)
    candidates = _candidate_list(output)
    rows = _select_fact_rows(
        candidates,
        max_facts=max_facts,
        max_chars_per_fact=max_chars_per_fact,
    )
    evidence_refs = tuple(_unique(row.evidence_ref for row in rows if row.evidence_ref))
    source_refs = tuple(_unique(row.source_ref for row in rows if row.source_ref))
    route_ids = tuple(_extract_route_ids(output))
    store_counts: dict[str, int] = {}
    for row in rows:
        store_counts[row.store_id] = store_counts.get(row.store_id, 0) + 1
    return SurfaceMemoryRecallPacket(
        query=_clean_text(query if query is not None else output.get("query")),
        facts=tuple(row.fact for row in rows),
        rows=tuple(rows),
        evidence_refs=evidence_refs,
        source_refs=source_refs,
        route_ids=route_ids,
        candidate_count=_coerce_int(output.get("candidate_count"), default=len(candidates)),
        degraded=bool(output.get("degraded", False)),
        reason_code=_clean_text(output.get("reason_code")) or None,
        trace={
            "status": _clean_text(output.get("status")),
            "stage": _clean_text(output.get("stage")),
            "route_count": len(route_ids),
            "route_error_count": len(list(output.get("route_errors") or [])),
            "selected_fact_count": len(rows),
            "store_counts": dict(sorted(store_counts.items())),
        },
    )


def apply_surface_memory_recall_packet(
    context: ThreeLayerContinuityContext,
    packet: SurfaceMemoryRecallPacket,
) -> ThreeLayerContinuityContext:
    recall_facts = _clean_list([*context.recall_facts, *packet.facts])
    style_anchor = _clean_list(
        [
            *context.style_anchor,
            "多路召回压缩" if packet.facts else "",
        ]
    )
    return replace(
        context,
        recall_facts=tuple(recall_facts),
        style_anchor=tuple(style_anchor),
    )


def build_surface_input_with_memory_recall(
    context: ThreeLayerContinuityContext,
    *,
    packet: SurfaceMemoryRecallPacket,
    soul_md: str,
    case_id: str = "",
    max_facts: int = 6,
    max_recent_turns: int = 6,
) -> IsolatedSurfaceInput:
    return build_surface_input_from_three_layer_context(
        apply_surface_memory_recall_packet(context, packet),
        soul_md=soul_md,
        case_id=case_id,
        max_facts=max_facts,
        max_recent_turns=max_recent_turns,
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
        text = f"当前回合摘要：用户提到「{user_text}」；新海天回应「{assistant_text}」。"
    elif user_text:
        text = f"当前回合摘要：用户提到「{user_text}」。"
    else:
        text = f"当前回合摘要：新海天回应「{assistant_text}」。"
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
    max_facts: int,
    max_chars_per_fact: int,
) -> list[SurfaceMemoryFactRow]:
    cap = max(0, int(max_facts or 0))
    if cap <= 0:
        return []
    rows = [_build_fact_row(item, max_chars_per_fact=max_chars_per_fact) for item in candidates]
    rows = [row for row in rows if row is not None]
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
    text = _surface_safe_text(
        candidate.get("snippet") or candidate.get("text"),
        limit=max_chars_per_fact,
    )
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


def _fact_prefix(store_id: str) -> str:
    normalized = _clean_text(store_id).lower()
    if normalized in {"episodic_vector", "episodic", "sqlite_ledger"}:
        return "记忆线索"
    if normalized in {"neo4j_bond", "bond_graph"}:
        return "关系线索"
    if normalized == "world_canon":
        return "世界事实"
    if normalized == "artifact_vault":
        return "制品线索"
    return "召回线索"


def _extract_route_ids(output: Mapping[str, Any]) -> list[str]:
    route_ids: list[str] = []
    for route in list(output.get("routes") or []):
        if isinstance(route, Mapping):
            route_id = _clean_text(route.get("route_id") or route.get("store_id") or route.get("id"))
        else:
            route_id = _clean_text(route)
        if route_id and route_id not in route_ids:
            route_ids.append(route_id)
    for row in list(output.get("route_rows") or []):
        if not isinstance(row, Mapping):
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
    text = text.replace("User:", "用户提到：").replace("Assistant:", "新海天回应：")
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


__all__ = [
    "SURFACE_MEMORY_EMPTY_TURN",
    "SURFACE_MEMORY_EVIDENCE_REQUIRED",
    "SURFACE_MEMORY_RECALL_FAILED",
    "SURFACE_MEMORY_RECALL_SKIPPED",
    "SURFACE_MEMORY_WRITE_FAILED",
    "SurfaceMemoryFactRow",
    "SurfaceMemoryRecallPlan",
    "SurfaceMemoryRecallPacket",
    "SurfaceMemoryWriteResult",
    "apply_surface_memory_recall_packet",
    "build_surface_input_with_memory_recall",
    "build_surface_memory_recall_query",
    "build_surface_memory_recall_packet",
    "build_surface_turn_memory_text",
    "plan_surface_memory_recall",
    "recall_surface_memory_for_context",
    "recall_surface_memory_packet",
    "write_surface_turn_memory",
]
