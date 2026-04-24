"""chat_retrieval_helpers.py — 检索辅助函数（从 chat_stream_handler.py 迁移）。

提供：
  - _run_retrieval: 并行执行检索
  - _build_skipped_retrieval_summary / _build_degraded_retrieval_summary: 降级摘要
"""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional

from host_brain.retrieval.orchestrator import (
    build_degraded_retrieval_summary as _orchestrator_degraded_summary,
    build_skipped_retrieval_summary as _orchestrator_skipped_summary,
)

_CONTEXTUAL_FOLLOWUP_RE = re.compile(
    r"^\s*(?:"
    r"什么意思|啥意思|什么情况|怎么理解|怎么说|这话什么意思|这句什么意思|刚才那句什么意思|你这是什么意思|你这话是什么意思|"
    r"知道什么|懂什么|明白什么|清楚什么|看到什么|看见什么|说什么|回什么|回复什么|"
    r"(?:你|我)(?:刚才|刚刚|上一句|上句|前一句|前面).{0,16}(?:什么意思|说了什么|发了什么|回了什么|回复了什么|是什么)|"
    r"(?:上一句|上句|前一句|刚才说的|刚刚说的).{0,12}(?:什么意思|是什么|说了什么)?"
    r")\s*[?？!！。…~]*$",
    re.IGNORECASE,
)
_PROMPT_KEEP_STORE_IDS: frozenset[str] = frozenset({"bond_graph", "neo4j_bond"})
_LOW_SIGNAL_SHORT_QUERY_MAX_LEN = 12
_LOW_SIGNAL_SCORE_FLOOR = 0.12
_LOW_SIGNAL_CONFIDENCE_FLOOR = 0.65
_LOW_SIGNAL_COVERAGE_FLOOR = 0.25


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_store_id(item: Mapping[str, Any]) -> str:
    metadata_raw = item.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    return str(
        item.get("store_id")
        or metadata.get("store_id")
        or item.get("slot_name")
        or item.get("kind")
        or item.get("source")
        or ""
    ).strip().lower()


def _is_contextual_followup_query(user_message: str) -> bool:
    normalized = str(user_message or "").strip()
    if not normalized:
        return False
    return bool(_CONTEXTUAL_FOLLOWUP_RE.match(normalized))


def _should_trim_low_signal_short_chat(
    *,
    user_message: str,
    retrieval_summary: Mapping[str, Any] | None,
) -> bool:
    normalized_message = str(user_message or "").strip()
    if not normalized_message:
        return False
    if _is_contextual_followup_query(normalized_message):
        return True
    if len(normalized_message) > _LOW_SIGNAL_SHORT_QUERY_MAX_LEN:
        return False

    summary = dict(retrieval_summary or {})
    query_plan_raw = summary.get("query_plan")
    query_plan = dict(query_plan_raw) if isinstance(query_plan_raw, Mapping) else {}
    intent = str(query_plan.get("intent") or "").strip().lower()
    if intent not in {"chat", "phatic"}:
        return False

    agentic_meta_raw = summary.get("agentic_meta")
    agentic_meta = dict(agentic_meta_raw) if isinstance(agentic_meta_raw, Mapping) else {}
    final_eval_raw = agentic_meta.get("final_evaluation")
    final_eval = dict(final_eval_raw) if isinstance(final_eval_raw, Mapping) else {}
    if not final_eval:
        return False
    if bool(final_eval.get("sufficient")):
        return False

    top_score = _coerce_float(final_eval.get("top_score")) or 0.0
    confidence = _coerce_float(final_eval.get("confidence")) or 0.0
    coverage = _coerce_float(final_eval.get("coverage_score")) or 0.0
    return (
        top_score < _LOW_SIGNAL_SCORE_FLOOR
        or confidence < _LOW_SIGNAL_CONFIDENCE_FLOOR
        or coverage < _LOW_SIGNAL_COVERAGE_FLOOR
    )


def filter_retrieval_evidence_items_for_prompt(
    *,
    user_message: str,
    retrieval_summary: Mapping[str, Any] | None,
    retrieval_evidence_items: list[dict[str, Any]],
    session_id: str | None = None,
    person_id: str | None = None,
    ledger: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = [
        dict(item) if isinstance(item, Mapping) else item
        for item in list(retrieval_evidence_items or [])
        if isinstance(item, Mapping)
    ]
    if not items:
        return [], {"applied": False, "mode": "", "removed_count": 0, "kept_count": 0}

    contextual_followup = _is_contextual_followup_query(user_message)
    low_signal_short_chat = _should_trim_low_signal_short_chat(
        user_message=user_message,
        retrieval_summary=retrieval_summary,
    )
    if not contextual_followup and not low_signal_short_chat:
        return items, {"applied": False, "mode": "", "removed_count": 0, "kept_count": len(items)}

    filtered: list[dict[str, Any]] = []
    for item in items:
        store_id = _resolve_store_id(item)
        if store_id in _PROMPT_KEEP_STORE_IDS:
            filtered.append(item)
            continue
        if contextual_followup:
            continue
        score = _coerce_float(item.get("confidence"))
        if score is None:
            score = _coerce_float(item.get("score"))
        if score is not None and score >= _LOW_SIGNAL_SCORE_FLOOR:
            filtered.append(item)

    if not filtered and not contextual_followup:
        filtered = [item for item in items if _resolve_store_id(item) in _PROMPT_KEEP_STORE_IDS]

    removed_count = max(0, len(items) - len(filtered))
    return filtered, {
        "applied": removed_count > 0,
        "mode": "contextual_followup" if contextual_followup else "low_signal_short_chat",
        "removed_count": removed_count,
        "kept_count": len(filtered),
    }


async def _run_retrieval(
    *,
    request_id: str,
    message: str,
    person_id: str,
    session_id: str,
    ts_ms: int,
    emotion_tag: Optional[str],
    render_target_lang: str,
    mood_state: Optional[str] = None,
    bond_summary: Mapping[str, Any] | None = None,
    motive_state: Mapping[str, Any] | None = None,
) -> tuple[Dict[str, Any], list[dict[str, Any]]]:
    """并行执行检索，返回 (retrieval_summary, retrieval_evidence_items)。"""
    from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: PLC0415

    return await get_hippocampus_agent().recall(
        request_id=request_id,
        message=message,
        person_id=person_id,
        session_id=session_id,
        ts_ms=ts_ms,
        emotion_tag=emotion_tag,
        render_target_lang=render_target_lang,
        mood_state=mood_state,
        bond_summary=bond_summary,
        motive_state=motive_state,
    )


def _build_skipped_retrieval_summary(reason_code: str) -> Dict[str, Any]:
    return _orchestrator_skipped_summary(reason_code)


def _build_degraded_retrieval_summary(reason_code: str) -> Dict[str, Any]:
    return _orchestrator_degraded_summary(reason_code)
