"""chat_retrieval_helpers.py — 检索辅助函数（从 chat_stream_handler.py 迁移）。

提供：
  - _run_retrieval: 并行执行检索
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import os
import re
import time
from functools import partial
from typing import Any, Callable, Dict, Mapping, Optional

from host_brain.core.runtime_helpers import (
    _coerce_float,
    _coerce_int,
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
_EXPLICIT_MEMORY_WRITE_MARKERS: tuple[str, ...] = (
    "帮我记住",
    "替我记住",
    "给我记住",
    "帮我记一下",
    "替我记一下",
    "给我记一下",
    "帮我记",
    "替我记",
    "记下来",
    "记一下",
    "记着",
    "记住",
)
_EXPLICIT_MEMORY_QUERY_ANCHORS: tuple[str, ...] = (
    "让你记",
    "叫你记",
    "要你记",
    "帮我记",
    "替我记",
    "给我记",
    "你记了",
    "你记住",
    "你刚才记",
    "你刚刚记",
)
_EXPLICIT_MEMORY_QUERY_WORDS: tuple[str, ...] = ("什么", "啥", "哪", "哪些", "内容")
_GENERIC_SURFACE_TASK_MODES: frozenset[str] = frozenset(
    {"chat", "daily_chat", "first_ask", "neutral", "plain_answer", "plain_hold"}
)
_COLLECTION_FASTPATH_CONTRACTS: frozenset[str] = frozenset({"collection_date", "collection_recent"})
_SURFACE_MEMORY_RECALL_COVERAGE_FAILED = "SURFACE_MEMORY_RECALL_COVERAGE_FAILED"
_SURFACE_MEMORY_RECALL_VALIDATION_FAILED = "SURFACE_MEMORY_RECALL_VALIDATION_FAILED"
_DATED_TEMPORAL_SCOPES: frozenset[str] = frozenset({"today", "yesterday", "day_before_yesterday"})
_OPTIONAL_SEMANTIC_EMBEDDING_BUDGET_ENV = "HOST_SURFACE_OPTIONAL_SEMANTIC_BUDGET_MS"
_EXPANDED_RECALL_STORES: tuple[str, ...] = (
    "life_memory_collection",
    "life_memory_entity",
    "life_episode",
    "semantic_memory",
    "episodic_vector",
    "sqlite_ledger",
)


def _parse_bool_env(name: str, *, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "y", "on", "enabled"}


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
    if intent != "chat":
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


def _surface_memory_distortion_bias(
    person_id: str | None,
    *,
    life_snapshot_reader: Any | None = None,
) -> float:
    """B1 记忆变糊：从 life-state 内在天气读 memory_distortion_bias（app 层读，作 float 传给召回；失败=0.0）。"""
    try:
        from host_brain.digital_life.application_boundary import read_life_snapshot  # noqa: PLC0415

        snapshot = read_life_snapshot(
            str(person_id or "default"),
            reader=life_snapshot_reader,
        )
        weather = dict(snapshot.get("state") or {}).get("inner_weather")
        if isinstance(weather, dict):
            return max(0.0, min(1.0, float(weather.get("memory_distortion_bias") or 0.0)))
    except Exception:
        return 0.0
    return 0.0


async def _run_retrieval(
    *,
    life_snapshot_reader: Any,
    life_memory_store: Any,
    conversation_history: Any | None = None,
    build_user_preference_pack: Callable[..., Any] | None = None,
    recall_service_factory: Callable[[], Any] | None = None,
    active_knowledge_search: Callable[..., list[dict[str, Any]]] | None = None,
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
    intent_draft: Mapping[str, Any] | None = None,
) -> tuple[Dict[str, Any], list[dict[str, Any]]]:
    """Surface memory recall bridge.

    The old app path used to call the legacy retrieval stack here. The current
    main chain only needs a compressed surface_memory_recall_packet; raw recall
    rows and evidence packs must not be returned to the final text layer.
    """
    runtime_started = time.perf_counter()
    runtime_timings_ms: dict[str, int] = {
        "plan": 0,
        "explicit_memory": 0,
        "collection_fastpath": 0,
        "embedding": 0,
        "service_resolve": 0,
        "recall": 0,
        "active_knowledge": 0,
        "lightnovel_review": 0,
        "generic_evidence_review": 0,
        "total": 0,
    }
    try:
        plan_started = time.perf_counter()
        from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext  # noqa: PLC0415
        from host_brain.persona.surface_memory_recall import (  # noqa: PLC0415
            SURFACE_MEMORY_RECALL_COVERAGE_FAILED,
            SURFACE_MEMORY_RECALL_SKIPPED,
            SurfaceMemoryRecallPacket,
            SurfaceMemoryFactRow,
            build_surface_memory_recall_packet,
            build_surface_memory_recall_decision_trace,
            plan_surface_memory_recall,
            recall_surface_memory_packet,
        )

        context = ThreeLayerContinuityContext(
            current_user_message=str(message or ""),
            task_mode_hint=_surface_task_mode_from_intent(message=message, intent_draft=intent_draft),
            relationship_distance=_surface_relationship_distance(bond_summary),
            necessary_facts=_surface_memory_query_facts(
                mood_state=mood_state,
                bond_summary=bond_summary,
                motive_state=motive_state,
            ),
        )
        plan = plan_surface_memory_recall(context)
        runtime_timings_ms["plan"] = _elapsed_perf_ms(plan_started)
        embedding_model_id = ""
        # ``NO_RECALL`` is the common low-latency companion path.  Make the
        # plan-level short circuit explicit instead of entering the separate
        # explicit-memory compatibility helper.  That helper already rejects
        # unrelated messages before querying history; this is therefore a
        # contract simplification, not by itself a measured latency win.
        explicit_memory_started = time.perf_counter()
        short_term_memory_facts = (
            _recent_explicit_memory_facts(
                message=message,
                session_id=session_id,
                request_id=request_id,
                conversation_history=conversation_history,
            )
            if plan.should_recall
            else []
        )
        runtime_timings_ms["explicit_memory"] = _elapsed_perf_ms(
            explicit_memory_started
        )
        if plan.should_recall and short_term_memory_facts:
            packet = SurfaceMemoryRecallPacket(
                facts=tuple(short_term_memory_facts),
                confidence=1.0,
                query=plan.query,
                route_ids=("recent_turn_explicit_memory",),
                candidate_count=len(short_term_memory_facts),
                trace={
                    "skipped": False,
                    "plan": plan.as_dict(),
                    "recall_decision": build_surface_memory_recall_decision_trace(plan, invoked=False),
                    "route_count": 1,
                    "short_term_explicit_memory": True,
                },
            )
        if not plan.should_recall:
            packet = SurfaceMemoryRecallPacket(
                query=plan.query,
                degraded=False,
                reason_code=SURFACE_MEMORY_RECALL_SKIPPED,
                trace={
                    "skipped": True,
                    "plan": plan.as_dict(),
                    "recall_decision": build_surface_memory_recall_decision_trace(plan, invoked=False),
                },
            )
        elif not short_term_memory_facts:
            collection_fastpath_started = time.perf_counter()
            packet = _maybe_recall_collection_fastpath(
                plan=plan,
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                ts_ms=ts_ms,
                build_packet_fn=build_surface_memory_recall_packet,
                life_memory_store=life_memory_store,
            )
            runtime_timings_ms["collection_fastpath"] = _elapsed_perf_ms(
                collection_fastpath_started
            )
            recall_invoked = packet is not None
            if packet is None:
                # Semantic model import/load can take tens of seconds on the
                # first request.  Keep it off the ASGI event loop so Cortex's
                # bounded relational-retrieval timeout can actually fire and
                # the conversation can continue with canonical short-term and
                # relationship context while the local model warms in a worker.
                embedding_started = time.perf_counter()
                embed_task = asyncio.to_thread(
                    _embed_surface_memory_query,
                    plan.query,
                )
                if str(plan.recall_policy or "").strip().upper() == "MAY_RECALL":
                    optional_budget_ms = max(
                        25,
                        min(
                            2_000,
                            _coerce_int(
                                os.environ.get(_OPTIONAL_SEMANTIC_EMBEDDING_BUDGET_ENV),
                                default=350,
                            ),
                        ),
                    )
                    try:
                        query_embedding, embedding_model_id = await asyncio.wait_for(
                            embed_task,
                            timeout=float(optional_budget_ms) / 1_000.0,
                        )
                    except asyncio.TimeoutError:
                        # Optional relationship/emotion context must never turn
                        # a cold local embedding import into several seconds of
                        # dead air.  The worker thread may finish warming the
                        # process while this turn continues with canonical Life
                        # and short-term relationship context.
                        packet = SurfaceMemoryRecallPacket(
                            query=plan.query,
                            degraded=True,
                            reason_code="SURFACE_OPTIONAL_SEMANTIC_WARMING",
                            trace={
                                "skipped": False,
                                "plan": plan.as_dict(),
                                "recall_decision": build_surface_memory_recall_decision_trace(
                                    plan,
                                    invoked=False,
                                ),
                                "route_count": 0,
                                "optional_embedding_budget_ms": optional_budget_ms,
                            },
                        )
                else:
                    query_embedding, embedding_model_id = await embed_task
                runtime_timings_ms["embedding"] = _elapsed_perf_ms(
                    embedding_started
                )
                if packet is None:
                    recall_invoked = True
                    service_resolve_started = time.perf_counter()
                    recall_service = (
                        recall_service_factory or _build_surface_memory_recall_service
                    )()
                    runtime_timings_ms["service_resolve"] = _elapsed_perf_ms(
                        service_resolve_started
                    )
                    recall_started = time.perf_counter()
                    packet = recall_surface_memory_packet(
                        recall_service=recall_service,
                        request_id=request_id,
                        query=plan.query,
                        query_embedding=query_embedding,
                        intent_id=_surface_intent_id(intent_draft),
                        person_id=person_id,
                        session_id=session_id,
                        ts_ms=ts_ms,
                        emotion_filter=emotion_tag,
                        mood_state=mood_state,
                        bond_summary=bond_summary,
                        motive_state=motive_state,
                        render_target_lang=render_target_lang,
                        target_store_ids=plan.target_store_ids,
                        time_lookback_days=plan.time_lookback_days,
                        max_facts=plan.max_facts,
                        distortion_bias=_surface_memory_distortion_bias(
                            person_id,
                            life_snapshot_reader=life_snapshot_reader,
                        ),
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
                        fail_soft=True,
                    )
                    packet = _maybe_retry_surface_memory_recall_with_expanded_scope(
                        packet=packet,
                        plan=plan,
                        recall_service=recall_service,
                        recall_packet_fn=recall_surface_memory_packet,
                        request_id=request_id,
                        query_embedding=query_embedding,
                        intent_id=_surface_intent_id(intent_draft),
                        person_id=person_id,
                        session_id=session_id,
                        ts_ms=ts_ms,
                        emotion_tag=emotion_tag,
                        mood_state=mood_state,
                        bond_summary=bond_summary,
                        motive_state=motive_state,
                        render_target_lang=render_target_lang,
                        coverage_failed_reason_code=SURFACE_MEMORY_RECALL_COVERAGE_FAILED,
                    )
                    runtime_timings_ms["recall"] = _elapsed_perf_ms(
                        recall_started
                    )
            packet = replace(
                packet,
                trace={
                    **dict(packet.trace or {}),
                    "skipped": False,
                    "plan": plan.as_dict(),
                    "recall_decision": build_surface_memory_recall_decision_trace(
                        plan,
                        invoked=recall_invoked,
                    ),
                },
            )
        active_knowledge_started = time.perf_counter()
        packet = _apply_active_knowledge_to_surface_packet(
            packet=packet,
            plan=plan,
            search=active_knowledge_search,
            person_id=person_id,
            fact_row_type=SurfaceMemoryFactRow,
        )
        runtime_timings_ms["active_knowledge"] = _elapsed_perf_ms(
            active_knowledge_started
        )
        lightnovel_review_started = time.perf_counter()
        packet = await _maybe_apply_lightnovel_deep_review(
            packet=packet,
            message=message,
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            ts_ms=ts_ms,
            life_memory_store=life_memory_store,
            build_user_preference_pack=build_user_preference_pack,
        )
        runtime_timings_ms["lightnovel_review"] = _elapsed_perf_ms(
            lightnovel_review_started
        )
        generic_review_started = time.perf_counter()
        packet = await _maybe_apply_generic_evidence_review(
            packet=packet,
            message=message,
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            ts_ms=ts_ms,
            life_memory_store=life_memory_store,
        )
        runtime_timings_ms["generic_evidence_review"] = _elapsed_perf_ms(
            generic_review_started
        )
        runtime_timings_ms["total"] = _elapsed_perf_ms(runtime_started)
        summary = _surface_memory_retrieval_summary(
            request_id=request_id,
            ts_ms=ts_ms,
            packet=packet,
            embedding_model_id=embedding_model_id,
        )
        summary["runtime_timings_ms"] = dict(runtime_timings_ms)
        return summary, []
    except Exception as exc:
        return (
            {
                "request_id": request_id,
                "status": "DEGRADED",
                "reason_code": "SURFACE_MEMORY_RECALL_BRIDGE_FAILED",
                "error": type(exc).__name__,
                "degraded": True,
                "route_count": 0,
                "candidate_count": 0,
                "topk_count": 0,
                "routes": [],
                "query_plan": {"intent": "surface_memory_recall", "text_len": len(str(message or ""))},
                "target_stores": [],
                "ts_ms": int(ts_ms),
            },
            [],
        )


def _elapsed_perf_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - float(started)) * 1000)))


def _apply_active_knowledge_to_surface_packet(
    *,
    packet: Any,
    plan: Any,
    search: Callable[..., list[dict[str, Any]]] | None,
    person_id: str,
    fact_row_type: Any,
) -> Any:
    """Merge a bounded active Knowledge projection into a recall packet.

    The planner remains the admission gate.  A normal no-recall turn returns
    before invoking the repository, and a failed optional read leaves the
    existing memory packet unchanged.
    """

    if not bool(getattr(plan, "should_recall", False)) or not callable(search):
        return packet
    try:
        rows = search(
            query=str(getattr(plan, "query", "") or ""),
            person_id=str(person_id or ""),
            top_k=max(1, min(int(getattr(plan, "max_facts", 4) or 4), 4)),
            threshold=0.05,
        )
    except Exception:
        return packet
    selected = [dict(item) for item in list(rows or []) if isinstance(item, Mapping)]
    if not selected:
        return packet
    knowledge_rows: list[Any] = []
    knowledge_facts: list[str] = []
    evidence_refs: list[str] = []
    source_refs: list[str] = []
    scores: list[float] = []
    for item in selected[:4]:
        content = re.sub(r"\s+", " ", str(item.get("content") or "").strip())[:320]
        if not content:
            continue
        metadata = dict(item.get("metadata") or {})
        digest = str(metadata.get("entry_digest") or "").strip().lower()
        evidence_ref = (
            f"evolution-knowledge-entry://sha256/{digest}"
            if re.fullmatch(r"[0-9a-f]{64}", digest)
            else str(item.get("doc_id") or "").strip()
        )
        source_ref = str(metadata.get("provenance_ref") or "").strip()
        score = max(0.0, min(float(item.get("score") or 0.0), 1.0))
        fact = f"已激活知识：{content}"
        knowledge_facts.append(fact)
        if evidence_ref:
            evidence_refs.append(evidence_ref)
        if source_ref:
            source_refs.append(source_ref)
        scores.append(score)
        knowledge_rows.append(
            fact_row_type(
                fact=fact,
                store_id="semantic_memory",
                source_ref=source_ref,
                evidence_ref=evidence_ref,
                score=score,
            )
        )
    if not knowledge_rows:
        return packet
    trace = dict(getattr(packet, "trace", None) or {})
    route_ids = tuple(
        dict.fromkeys(
            ["knowledge_os_v2", *list(getattr(packet, "route_ids", ()) or ())]
        )
    )
    trace["knowledge_os_active"] = {
        "status": "SELECTED",
        "selected_count": len(knowledge_rows),
        "read_only": True,
    }
    trace["route_count"] = len(route_ids)
    return replace(
        packet,
        facts=tuple(
            dict.fromkeys(
                [*knowledge_facts, *list(getattr(packet, "facts", ()) or ())]
            )
        )[:8],
        rows=tuple([*knowledge_rows, *list(getattr(packet, "rows", ()) or ())])[:8],
        evidence_refs=tuple(
            dict.fromkeys(
                [*evidence_refs, *list(getattr(packet, "evidence_refs", ()) or ())]
            )
        )[:16],
        source_refs=tuple(
            dict.fromkeys(
                [*source_refs, *list(getattr(packet, "source_refs", ()) or ())]
            )
        )[:16],
        route_ids=route_ids,
        candidate_count=int(getattr(packet, "candidate_count", 0) or 0)
        + len(knowledge_rows),
        confidence=max(
            float(getattr(packet, "confidence", 0.0) or 0.0),
            max(scores or [0.0]),
        ),
        degraded=False,
        reason_code=None,
        trace=trace,
    )


def _maybe_recall_collection_fastpath(
    *,
    plan: Any,
    request_id: str,
    person_id: str,
    session_id: str,
    ts_ms: int,
    build_packet_fn: Any,
    life_memory_store: Any,
) -> Any | None:
    if not _parse_bool_env("HOST_SURFACE_COLLECTION_FAST_RECALL_ENABLED", default=True):
        return None
    if str(getattr(plan, "recall_contract_id", "") or "") not in _COLLECTION_FASTPATH_CONTRACTS:
        return None
    if str(getattr(plan, "primary_memory_layer", "") or "") != "life_memory_collection":
        return None
    started_ms = int(time.time() * 1000)
    try:
        from dataclasses import replace  # noqa: PLC0415
        raw_packet = life_memory_store.recall_collection_packet(
            query=str(getattr(plan, "query", "") or ""),
            top_k=max(5, int(getattr(plan, "max_facts", 0) or 0)),
            person_id=person_id,
            session_id=session_id,
            now_ts_ms_value=int(ts_ms),
        )
        candidates = list(getattr(raw_packet, "candidates", []) or [])
        packet = build_packet_fn(
            {
                "output": {
                    "request_id": request_id,
                    "query": str(getattr(plan, "query", "") or ""),
                    "status": "OK",
                    "stage": "collection_fastpath",
                    "reason_code": None,
                    "degraded": False,
                    "routes": [{"route_id": "surface_life_collection_fastpath", "store_id": "life_memory_collection"}],
                    "route_rows": [
                        {
                            "route_id": "surface_life_collection_fastpath",
                            "store_id": "life_memory_collection",
                            "candidate_count": len(candidates),
                            "status": "OK",
                            "latency_ms": max(0, int(time.time() * 1000) - started_ms),
                        }
                    ],
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                    "ts_ms": int(ts_ms),
                }
            },
            query=str(getattr(plan, "query", "") or ""),
            max_facts=int(getattr(plan, "max_facts", 0) or 4),
            max_chars_per_fact=int(getattr(plan, "max_chars_per_fact", 0) or 96),
            validation_requirements=tuple(getattr(plan, "validation_requirements", ()) or ()),
            temporal_scope=str(getattr(plan, "temporal_scope", "") or ""),
            query_kind=str(getattr(plan, "query_kind", "") or ""),
            target_domains=tuple(getattr(plan, "target_domains", ()) or ()),
            recall_contract_id=str(getattr(plan, "recall_contract_id", "") or ""),
            primary_memory_layer=str(getattr(plan, "primary_memory_layer", "") or ""),
            expected_memory_layers=tuple(getattr(plan, "expected_memory_layers", ()) or ()),
            min_collection_items=int(getattr(plan, "min_collection_items", 0) or 0),
            answer_obligations=tuple(getattr(plan, "answer_obligations", ()) or ()),
        )
        trace = dict(getattr(packet, "trace", None) or {})
        readiness = dict(trace.get("recall_answer_readiness") or {}) if isinstance(trace.get("recall_answer_readiness"), Mapping) else {}
        fast_meta = {
            "attempted": True,
            "latency_ms": max(0, int(time.time() * 1000) - started_ms),
            "candidate_count": len(candidates),
            "status": "HIT" if str(readiness.get("status") or "").upper() == "READY" and not getattr(packet, "reason_code", None) else "MISS",
        }
        packet = replace(packet, trace={**trace, "collection_fastpath": fast_meta})
        if fast_meta["status"] == "HIT":
            return packet
    except Exception:
        return None
    return None


def _recent_lightnovel_delivery_packet_payload(
    *,
    packet_payload: Mapping[str, Any],
    message: str,
    person_id: str,
    session_id: str,
    build_user_preference_pack: Callable[..., Any] | None,
) -> dict[str, Any]:
    if not callable(build_user_preference_pack):
        return dict(packet_payload or {})

    try:
        preference_pack = build_user_preference_pack(
            person_id=str(person_id or "").strip() or None,
            query_text=str(message or ""),
            session_id=str(session_id or "").strip() or None,
            conversation_id=str(session_id or "").strip() or None,
        )
    except Exception:
        return dict(packet_payload or {})
    if not isinstance(preference_pack, Mapping):
        return dict(packet_payload or {})
    recent_context = preference_pack.get("recent_recommendation_context")
    if not isinstance(recent_context, Mapping):
        return dict(packet_payload or {})
    raw_items = recent_context.get("items")
    if not isinstance(raw_items, list):
        return dict(packet_payload or {})
    referenced_item = recent_context.get("referenced_item")
    source_items = [referenced_item] if isinstance(referenced_item, Mapping) and referenced_item else raw_items[:6]
    review_items: list[dict[str, Any]] = []
    for item in source_items:
        if not isinstance(item, Mapping):
            continue
        rank = _coerce_int(item.get("rank"), default=0)
        title = str(item.get("title") or "").strip()
        detail_url = str(item.get("detail_url") or "").strip()
        if not title and not detail_url:
            continue
        review_item = {
            "index": rank or len(review_items) + 1,
            "rank": rank or len(review_items) + 1,
            "title": title,
            "detail_url": detail_url,
            "source_ref": detail_url,
            "selection_reason": str(item.get("selection_reason") or "").strip(),
            "intro_snippet": str(item.get("intro_snippet") or "").strip(),
            "content_access_state": str(item.get("content_access_state") or "").strip(),
        }
        review_items.append({key: value for key, value in review_item.items() if value not in ("", None, [], {})})
    if not review_items:
        return dict(packet_payload or {})

    payload = dict(packet_payload or {})
    trace = dict(payload.get("trace") or {}) if isinstance(payload.get("trace"), Mapping) else {}
    existing = list(trace.get("selected_collection_items") or []) if isinstance(trace.get("selected_collection_items"), list) else []
    seen = {
        (str(item.get("detail_url") or item.get("source_ref") or ""), str(item.get("title") or ""))
        for item in existing
        if isinstance(item, Mapping)
    }
    for item in review_items:
        key = (str(item.get("detail_url") or item.get("source_ref") or ""), str(item.get("title") or ""))
        if key not in seen:
            existing.append(item)
            seen.add(key)
    trace["selected_collection_items"] = existing
    plan = dict(trace.get("plan") or {}) if isinstance(trace.get("plan"), Mapping) else {}
    domains = [
        str(item)
        for item in list(plan.get("target_domains") or [])
        if str(item).strip()
    ]
    if "lightnovel_recommendation" not in domains:
        domains.append("lightnovel_recommendation")
    plan["target_domains"] = domains
    trace["plan"] = plan
    payload["trace"] = trace

    facts = list(payload.get("facts") or []) if isinstance(payload.get("facts"), list) else []
    fact_parts = []
    for item in review_items[:6]:
        rank = _coerce_int(item.get("rank") or item.get("index"), default=0)
        title = str(item.get("title") or "").strip()
        detail_url = str(item.get("detail_url") or "").strip()
        intro = str(item.get("intro_snippet") or "").strip()
        if title or detail_url:
            fact_parts.append(
                f"{rank}. {title}｜详情：{detail_url}" + (f"｜简介：{intro[:160]}" if intro else "")
            )
    if fact_parts:
        facts.append("可比较证据：" + "；".join(fact_parts))
        payload["facts"] = list(dict.fromkeys(str(item) for item in facts if str(item).strip()))
    source_refs = list(payload.get("source_refs") or []) if isinstance(payload.get("source_refs"), list) else []
    for item in review_items:
        detail_url = str(item.get("detail_url") or "").strip()
        if detail_url and detail_url not in source_refs:
            source_refs.append(detail_url)
    if source_refs:
        payload["source_refs"] = source_refs
    return payload


def _maybe_retry_surface_memory_recall_with_expanded_scope(
    *,
    packet: Any,
    plan: Any,
    recall_service: Any,
    recall_packet_fn: Any,
    request_id: str,
    query_embedding: Any,
    intent_id: str | None,
    person_id: str,
    session_id: str,
    ts_ms: int,
    emotion_tag: str | None,
    mood_state: str | None,
    bond_summary: Mapping[str, Any] | None,
    motive_state: Mapping[str, Any] | None,
    render_target_lang: str,
    coverage_failed_reason_code: str = _SURFACE_MEMORY_RECALL_COVERAGE_FAILED,
) -> Any:
    if not _should_retry_surface_memory_recall(plan=plan, packet=packet, coverage_failed_reason_code=coverage_failed_reason_code):
        return packet
    try:
        from dataclasses import replace as dataclass_replace  # noqa: PLC0415
    except Exception:
        return packet
    original_temporal_scope = str(getattr(plan, "temporal_scope", "") or "").strip()
    original_lookback = _coerce_int(getattr(plan, "time_lookback_days", None), default=0)
    expanded_stores = tuple(
        dict.fromkeys([*_EXPANDED_RECALL_STORES, *list(getattr(plan, "target_store_ids", ()) or ())])
    )
    try:
        expanded_plan = dataclass_replace(
            plan,
            temporal_scope="recent_days",
            target_store_ids=expanded_stores,
            time_lookback_days=max(original_lookback, 14),
            max_facts=max(_coerce_int(getattr(plan, "max_facts", 0), default=0), 8),
            max_chars_per_fact=max(_coerce_int(getattr(plan, "max_chars_per_fact", 0), default=0), 900),
        )
        retry_packet = recall_packet_fn(
            recall_service=recall_service,
            request_id=request_id,
            query=str(getattr(expanded_plan, "query", "") or ""),
            query_embedding=list(query_embedding or []),
            intent_id=intent_id,
            person_id=person_id,
            session_id=session_id,
            ts_ms=ts_ms,
            emotion_filter=emotion_tag,
            mood_state=mood_state,
            bond_summary=bond_summary,
            motive_state=motive_state,
            render_target_lang=render_target_lang,
            target_store_ids=getattr(expanded_plan, "target_store_ids", ()) or (),
            time_lookback_days=getattr(expanded_plan, "time_lookback_days", None),
            max_facts=getattr(expanded_plan, "max_facts", 4),
            max_chars_per_fact=getattr(expanded_plan, "max_chars_per_fact", 96),
            validation_requirements=getattr(expanded_plan, "validation_requirements", ()) or (),
            temporal_scope=getattr(expanded_plan, "temporal_scope", "") or "",
            query_kind=getattr(expanded_plan, "query_kind", "") or "",
            target_domains=getattr(expanded_plan, "target_domains", ()) or (),
            recall_contract_id=getattr(expanded_plan, "recall_contract_id", "") or "",
            primary_memory_layer=getattr(expanded_plan, "primary_memory_layer", "") or "",
            expected_memory_layers=getattr(expanded_plan, "expected_memory_layers", ()) or (),
            min_collection_items=getattr(expanded_plan, "min_collection_items", 0) or 0,
            answer_obligations=getattr(expanded_plan, "answer_obligations", ()) or (),
            fail_soft=True,
        )
    except Exception:
        return packet
    use_retry = _expanded_surface_memory_recall_is_better(original=packet, retry=retry_packet)
    retry_meta = {
        "attempted": True,
        "status": "USED" if use_retry else "KEPT_ORIGINAL",
        "reason": "coverage_failed_expand_recent_collection_scope",
        "from_temporal_scope": original_temporal_scope,
        "to_temporal_scope": str(getattr(expanded_plan, "temporal_scope", "") or ""),
        "from_time_lookback_days": getattr(plan, "time_lookback_days", None),
        "to_time_lookback_days": getattr(expanded_plan, "time_lookback_days", None),
        "original_reason_code": str(getattr(packet, "reason_code", "") or ""),
        "retry_reason_code": str(getattr(retry_packet, "reason_code", "") or ""),
        "original_coverage_score": _packet_coverage_score(packet),
        "retry_coverage_score": _packet_coverage_score(retry_packet),
        "original_readiness": _packet_readiness_status(packet),
        "retry_readiness": _packet_readiness_status(retry_packet),
    }
    selected_packet = retry_packet if use_retry else packet
    try:
        trace = dict(getattr(selected_packet, "trace", None) or {})
        return dataclass_replace(selected_packet, trace={**trace, "recall_retry": retry_meta})
    except Exception:
        return selected_packet


def _should_retry_surface_memory_recall(*, plan: Any, packet: Any, coverage_failed_reason_code: str) -> bool:
    reason_code = str(getattr(packet, "reason_code", "") or "").strip()
    if reason_code not in {coverage_failed_reason_code, _SURFACE_MEMORY_RECALL_VALIDATION_FAILED}:
        return False
    contract_id = str(getattr(plan, "recall_contract_id", "") or "").strip()
    query_kind = str(getattr(plan, "query_kind", "") or "").strip()
    if contract_id not in {"collection_recent", "anaphoric_selection"} and query_kind not in {"collection_summary", "anaphoric_selection"}:
        return False
    temporal_scope = str(getattr(plan, "temporal_scope", "") or "").strip()
    if temporal_scope in _DATED_TEMPORAL_SCOPES or temporal_scope.startswith("date:"):
        return False
    reasons = set(_packet_readiness_reasons(packet))
    if not reasons.intersection({"coverage_failed", "insufficient_item_count", "no_selected_memory"}):
        return False
    target_stores = {str(item or "").strip() for item in list(getattr(plan, "target_store_ids", ()) or ())}
    return bool(target_stores.intersection({"life_memory_collection", "life_memory_entity", "life_episode"}))


def _expanded_surface_memory_recall_is_better(*, original: Any, retry: Any) -> bool:
    retry_status = _packet_readiness_status(retry).upper()
    original_status = _packet_readiness_status(original).upper()
    if retry_status == "READY" and original_status != "READY":
        return True
    retry_reason = str(getattr(retry, "reason_code", "") or "").strip()
    original_reason = str(getattr(original, "reason_code", "") or "").strip()
    if not retry_reason and original_reason:
        return True
    return _packet_coverage_score(retry) > _packet_coverage_score(original) + 0.08


def _packet_readiness(packet: Any) -> dict[str, Any]:
    trace_raw = getattr(packet, "trace", None)
    trace = dict(trace_raw) if isinstance(trace_raw, Mapping) else {}
    readiness_raw = trace.get("recall_answer_readiness")
    return dict(readiness_raw) if isinstance(readiness_raw, Mapping) else {}


def _packet_readiness_status(packet: Any) -> str:
    return str(_packet_readiness(packet).get("status") or "").strip()


def _packet_readiness_reasons(packet: Any) -> list[str]:
    reasons = _packet_readiness(packet).get("reasons")
    if isinstance(reasons, list):
        return [str(item or "").strip() for item in reasons if str(item or "").strip()]
    return []


def _packet_coverage_score(packet: Any) -> float:
    trace_raw = getattr(packet, "trace", None)
    trace = dict(trace_raw) if isinstance(trace_raw, Mapping) else {}
    coverage_raw = trace.get("recall_coverage")
    coverage = dict(coverage_raw) if isinstance(coverage_raw, Mapping) else {}
    value = _coerce_float(coverage.get("score"))
    return float(value) if value is not None else 0.0


async def _maybe_apply_lightnovel_deep_review(
    *,
    packet: Any,
    message: str,
    request_id: str,
    person_id: str,
    session_id: str,
    ts_ms: int,
    life_memory_store: Any,
    build_user_preference_pack: Callable[..., Any] | None,
) -> Any:
    try:
        from host_brain.agent_runtime.lightnovel_deep_reviewer import (  # noqa: PLC0415
            may_deep_review_lightnovel_selection,
            should_deep_review_lightnovel_selection,
        )
    except Exception:
        return packet
    # Do not query the durable recommendation/preference ledger on every
    # ordinary companion turn.  The message predicate is a necessary (not
    # sufficient) condition; the full packet-aware predicate still decides
    # whether a deep review is allowed after contextual enrichment.
    if not may_deep_review_lightnovel_selection(user_message=message):
        return packet
    packet_payload = packet.as_dict() if hasattr(packet, "as_dict") else {}
    packet_payload = _recent_lightnovel_delivery_packet_payload(
        packet_payload=packet_payload,
        message=message,
        person_id=person_id,
        session_id=session_id,
        build_user_preference_pack=build_user_preference_pack,
    )
    if not should_deep_review_lightnovel_selection(user_message=message, packet=packet_payload):
        return packet
    try:
        from dataclasses import replace  # noqa: PLC0415

        from host_brain.agent_runtime.lightnovel_deep_reviewer import (  # noqa: PLC0415
            review_lightnovel_selection_from_memory_packet,
        )
        from host_brain.app.observability_persist import (  # noqa: PLC0415
            persist_life_action_event_memory,
        )
    except Exception:
        return packet
    started_ms = int(time.time() * 1000)
    try:
        import asyncio  # noqa: PLC0415

        review = await asyncio.to_thread(
            review_lightnovel_selection_from_memory_packet,
            packet=packet_payload,
            user_message=message,
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            ts_ms=ts_ms,
            life_memory_store=life_memory_store,
            persist_life_action_event_memory=partial(
                persist_life_action_event_memory,
                life_memory_store=life_memory_store,
            ),
        )
    except Exception as exc:
        review = {
            "status": "DEGRADED",
            "reason_code": "LIGHTNOVEL_DEEP_REVIEW_FAILED",
            "fact": f"我尝试回去查这些书的详情页，但深读步骤失败了：{type(exc).__name__}。",
            "evidence_refs": [],
            "cached": False,
            "source": "error",
        }
    latency_ms = max(0, int(time.time() * 1000) - started_ms)
    fact = str(review.get("fact") or "").strip()
    if not fact:
        return packet
    previous_facts = list(getattr(packet, "facts", ()) or [])
    anchor_fact = _lightnovel_recommendation_anchor_fact(packet_payload)
    if anchor_fact and (
        not previous_facts
        or not any(_lightnovel_anchor_fact_already_present(anchor_fact, existing) for existing in previous_facts)
    ):
        previous_facts.append(anchor_fact)
    source_card_count = _coerce_int(review.get("source_card_count"))
    reviewed_item_count = _coerce_int(review.get("reviewed_item_count") or len(list(review.get("items") or [])))
    partial_review = bool(review.get("partial_review")) or bool(source_card_count > reviewed_item_count > 0)
    trace = dict(getattr(packet, "trace", None) or {})
    trace["lightnovel_deep_review"] = {
        "status": str(review.get("status") or ""),
        "reason_code": str(review.get("reason_code") or ""),
        "selected": dict(review.get("selected") or {}) if isinstance(review.get("selected"), Mapping) else {},
        "item_count": len(list(review.get("items") or [])),
        "source_card_count": source_card_count,
        "reviewed_item_count": reviewed_item_count,
        "partial_review": partial_review,
        "latency_ms": latency_ms,
        "cached": bool(review.get("cached")),
        "source": str(review.get("source") or ("cache" if bool(review.get("cached")) else "fetch")),
        "recommendation_signature": str(review.get("recommendation_signature") or ""),
        "source_episode_id": str(review.get("source_episode_id") or ""),
        "persisted_episode_id": str(review.get("persisted_episode_id") or ""),
        "persist_status": str(review.get("persist_status") or ""),
        "evidence_ref_count": len(list(review.get("evidence_refs") or [])),
    }
    review_ok = str(review.get("status") or "").strip().upper() == "OK"
    old_reason_code = str(getattr(packet, "reason_code", None) or "").strip()
    resolved_coverage_failure = review_ok and old_reason_code == "SURFACE_MEMORY_RECALL_COVERAGE_FAILED"
    deep_review_makes_answer_ready = review_ok and old_reason_code in {
        "",
        "SURFACE_MEMORY_RECALL_COVERAGE_FAILED",
        "SURFACE_MEMORY_RECALL_VALIDATION_FAILED",
    }
    if deep_review_makes_answer_ready:
        previous_readiness = dict(trace.get("recall_answer_readiness") or {}) if isinstance(trace.get("recall_answer_readiness"), Mapping) else {}
        if previous_readiness:
            trace["recall_answer_readiness_before_deep_review"] = previous_readiness
        detail = dict(previous_readiness.get("detail_evidence") or {}) if isinstance(previous_readiness.get("detail_evidence"), Mapping) else {}
        detail.update(
            {
                "required": True,
                "ready_for_deep_review": True,
                "basis": "lightnovel_deep_review",
                "body_item_count": max(1, _coerce_int(detail.get("body_item_count"))),
                "anchor_item_count": max(
                    _coerce_int(detail.get("anchor_item_count")),
                    len(list(review.get("evidence_refs") or [])),
                ),
            }
        )
        trace["recall_answer_readiness"] = {
            **previous_readiness,
            "status": "READY",
            "ready_to_answer": True,
            "response_mode": "answer_from_evidence",
            "surface_action": "answer",
            "evidence_depth": "detail_or_body_evidence",
            "reasons": [reason for reason in list(previous_readiness.get("reasons") or []) if reason != "missing_detail_evidence"],
            "coverage_passed": True,
            "coverage_failed": False,
            "detail_evidence": detail,
            "review_scope": {
                "partial": partial_review,
                "source_card_count": source_card_count,
                "reviewed_item_count": reviewed_item_count,
            },
        }
        trace["recall_answer_strategy"] = {
            "schema": "Natsume.surface_recall_answer_strategy.v1",
            "action": "direct_answer",
            "claim_scope": "partial_detail_judgment" if partial_review else "detail_judgment",
            "evidence_level": "partial_detail_or_body" if partial_review else "detail_or_body",
            "confidence_band": "medium" if partial_review else "high",
            "ready_to_answer": True,
            "should_fetch_more_evidence": False,
            "should_state_memory_only": False,
            "recall_contract_id": str(previous_readiness.get("recall_contract_id") or ""),
            "query_kind": str(previous_readiness.get("query_kind") or ""),
            "need_type": str(previous_readiness.get("need_type") or ""),
            "response_mode": "answer_from_evidence",
            "coverage_score": max(0.85, _coerce_float(previous_readiness.get("coverage_score")) or 0.0),
            "selected_item_count": _coerce_int(previous_readiness.get("selected_item_count")),
            "detail_evidence": {
                "required": True,
                "basis": "lightnovel_deep_review",
                "body_item_count": max(1, _coerce_int(detail.get("body_item_count"))),
                "anchor_item_count": max(
                    _coerce_int(detail.get("anchor_item_count")),
                    len(list(review.get("evidence_refs") or [])),
                ),
                "source_card_count": source_card_count,
                "reviewed_item_count": reviewed_item_count,
                "partial_review": partial_review,
            },
            "reasons": ["partial_detail_review"] if partial_review else [],
            "recommended_surface_rule": (
                "只能说这是已补读到的几本里的判断；不要把部分深读样本说成全部十几本里的最终最好。"
                if partial_review
                else "可以基于轻小说详情/正文深读结果自然回答选择题；不要把证据过程念出来。"
            ),
        }
        trace["answer_ready"] = True
        if resolved_coverage_failure:
            trace["coverage_failed_resolved_by_deep_review"] = True
    elif not review_ok:
        previous_readiness = dict(trace.get("recall_answer_readiness") or {}) if isinstance(trace.get("recall_answer_readiness"), Mapping) else {}
        if previous_readiness:
            trace["recall_answer_readiness_before_deep_review"] = previous_readiness
        detail = dict(previous_readiness.get("detail_evidence") or {}) if isinstance(previous_readiness.get("detail_evidence"), Mapping) else {}
        detail.update(
            {
                "required": True,
                "ready_for_deep_review": False,
                "basis": "missing_detail_evidence",
                "body_item_count": 0,
                "anchor_item_count": max(
                    _coerce_int(detail.get("anchor_item_count")),
                    len(list(review.get("evidence_refs") or [])),
                ),
            }
        )
        reasons = [
            reason
            for reason in list(previous_readiness.get("reasons") or [])
            if str(reason or "").strip()
        ]
        for reason in ("missing_detail_evidence", "deep_review_failed"):
            if reason not in reasons:
                reasons.append(reason)
        trace["recall_answer_readiness"] = {
            **previous_readiness,
            "status": "NOT_READY",
            "ready_to_answer": False,
            "response_mode": "fetch_or_request_detail_evidence",
            "surface_action": "fetch_more_evidence",
            "evidence_depth": "detail_or_body_evidence",
            "reasons": reasons,
            "coverage_passed": False,
            "coverage_failed": True,
            "detail_evidence": detail,
        }
        trace["recall_answer_strategy"] = {
            "schema": "Natsume.surface_recall_answer_strategy.v1",
            "action": "fetch_more_evidence",
            "claim_scope": "memory_only_no_quality_judgment",
            "evidence_level": "detail_anchor_only",
            "confidence_band": "low",
            "ready_to_answer": False,
            "should_fetch_more_evidence": True,
            "should_state_memory_only": True,
            "recall_contract_id": str(previous_readiness.get("recall_contract_id") or ""),
            "query_kind": str(previous_readiness.get("query_kind") or ""),
            "need_type": str(previous_readiness.get("need_type") or ""),
            "response_mode": "fetch_or_request_detail_evidence",
            "coverage_score": _coerce_float(previous_readiness.get("coverage_score")) or 0.0,
            "selected_item_count": _coerce_int(previous_readiness.get("selected_item_count")),
            "detail_evidence": {
                "required": True,
                "basis": "missing_detail_evidence",
                "body_item_count": 0,
                "anchor_item_count": _coerce_int(detail.get("anchor_item_count")),
                "source_card_count": source_card_count,
                "reviewed_item_count": reviewed_item_count,
                "partial_review": partial_review,
            },
            "reasons": reasons,
            "recommended_surface_rule": "只能确认记得这些条目；详情页或正文没读到时，不要判断哪本更好看。",
        }
        trace["answer_ready"] = False
    return replace(
        packet,
        facts=tuple([fact, *previous_facts]),
        style_anchor=tuple(
            [
                "轻小说选择追问：优先使用网页/正文深读结果；像正常人聊书一样给口味判断，不要念证据条目。",
                *list(getattr(packet, "style_anchor", ()) or []),
            ]
        ),
        must_not_do=tuple(
            [
                "不要把轻小说选择追问只按标题、热度或旧推荐摘要回答；有网页/正文深读结果时必须基于深读证据。",
                "不要直接说“我是按正文看下来的”“正文有多少字”“证据更站得住”；把证据转成自然理由。",
                "不要臆测用户昨天有没有看、是不是忘了、是不是终于想起来问；只说我这次补读后的判断。",
                *list(getattr(packet, "must_not_do", ()) or []),
            ]
        ),
        evidence_refs=tuple(
            dict.fromkeys([*list(getattr(packet, "evidence_refs", ()) or []), *list(review.get("evidence_refs") or [])])
        ),
        confidence=max(float(getattr(packet, "confidence", 0.0) or 0.0), 0.85) if deep_review_makes_answer_ready else getattr(packet, "confidence", 0.0),
        degraded=False if deep_review_makes_answer_ready else (True if not review_ok else getattr(packet, "degraded", False)),
        reason_code=None if deep_review_makes_answer_ready else (str(review.get("reason_code") or "") or getattr(packet, "reason_code", None) if not review_ok else getattr(packet, "reason_code", None)),
        trace=trace,
    )


def _lightnovel_recommendation_anchor_fact(packet: Mapping[str, Any]) -> str:
    rows = packet.get("rows") if isinstance(packet, Mapping) else []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            fact = str(row.get("fact") or row.get("text") or "").strip()
            if "可比较证据" in fact and ("推荐" in fact or "轻小说" in fact):
                return fact[:1200].rstrip("，,、；;。")
    trace = packet.get("trace") if isinstance(packet, Mapping) else {}
    if not isinstance(trace, Mapping):
        return ""
    items: list[Mapping[str, Any]] = []
    for key in ("selected_collection_items", "selected_entity_items"):
        raw_items = trace.get(key)
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if isinstance(item, Mapping):
                items.append(item)
    if not items:
        return ""
    cards: list[str] = []
    for index, item in enumerate(items[:6], start=1):
        title = str(item.get("title") or item.get("item_title") or item.get("entity_title") or "").strip()
        if not title:
            continue
        reason = str(item.get("selection_reason") or item.get("reason") or item.get("facet") or "").strip()
        intro = str(item.get("intro_snippet") or item.get("item_summary") or item.get("summary") or "").strip()
        detail_url = str(item.get("detail_url") or item.get("source_ref") or "").strip()
        parts = [f"{index}. {title}"]
        if reason:
            parts.append(f"看点：{reason}")
        if intro:
            parts.append(f"简介：{intro}")
        if detail_url.startswith(("http://", "https://")):
            parts.append(f"详情：{detail_url}")
        cards.append("｜".join(parts))
    if not cards:
        return ""
    return f"记忆线索：我刚才推荐了 {len(items)} 本轻小说；可比较证据：" + "；".join(cards)


def _lightnovel_anchor_fact_already_present(anchor_fact: str, existing_fact: Any) -> bool:
    anchor = re.sub(r"\s+", "", str(anchor_fact or ""))
    existing = re.sub(r"\s+", "", str(existing_fact or ""))
    if not anchor or not existing:
        return False
    if anchor in existing or existing in anchor:
        return True
    anchor_titles = set(re.findall(r"\d+\.\s*([^｜；;]+)", str(anchor_fact or "")))
    existing_titles = set(re.findall(r"\d+\.\s*([^｜；;]+)", str(existing_fact or "")))
    return bool(anchor_titles and existing_titles and anchor_titles.issubset(existing_titles))


async def _maybe_apply_generic_evidence_review(
    *,
    packet: Any,
    message: str,
    request_id: str,
    person_id: str,
    session_id: str,
    ts_ms: int,
    life_memory_store: Any,
) -> Any:
    try:
        from host_brain.agent_runtime.generic_evidence_reviewer import (  # noqa: PLC0415
            should_review_generic_evidence,
        )
    except Exception:
        return packet
    packet_payload = packet.as_dict() if hasattr(packet, "as_dict") else {}
    if not should_review_generic_evidence(user_message=message, packet=packet_payload):
        return packet
    try:
        from dataclasses import replace  # noqa: PLC0415

        from host_brain.agent_runtime.generic_evidence_reviewer import (  # noqa: PLC0415
            review_generic_evidence_from_memory_packet,
        )
        from host_brain.app.observability_persist import (  # noqa: PLC0415
            persist_life_action_event_memory,
        )
    except Exception:
        return packet
    started_ms = int(time.time() * 1000)
    try:
        import asyncio  # noqa: PLC0415

        review = await asyncio.to_thread(
            review_generic_evidence_from_memory_packet,
            packet=packet_payload,
            user_message=message,
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            ts_ms=ts_ms,
            persist_life_action_event_memory=partial(
                persist_life_action_event_memory,
                life_memory_store=life_memory_store,
            ),
        )
    except Exception as exc:
        review = {
            "status": "DEGRADED",
            "reason_code": "GENERIC_EVIDENCE_REVIEW_FAILED",
            "fact": f"我尝试重新读取记忆里的证据来源，但补证据步骤失败了：{type(exc).__name__}。",
            "evidence_refs": [],
            "cached": False,
            "source": "error",
            "items": [],
        }
    latency_ms = max(0, int(time.time() * 1000) - started_ms)
    fact = str(review.get("fact") or "").strip()
    if not fact:
        return packet
    trace = dict(getattr(packet, "trace", None) or {})
    trace["generic_evidence_review"] = {
        "status": str(review.get("status") or ""),
        "reason_code": str(review.get("reason_code") or ""),
        "item_count": len(list(review.get("items") or [])),
        "latency_ms": latency_ms,
        "cached": bool(review.get("cached")),
        "source": str(review.get("source") or "fetch"),
        "persisted_episode_id": str(review.get("persisted_episode_id") or ""),
        "persist_status": str(review.get("persist_status") or ""),
        "evidence_ref_count": len(list(review.get("evidence_refs") or [])),
    }
    review_ok = str(review.get("status") or "").strip().upper() == "OK"
    old_reason_code = str(getattr(packet, "reason_code", None) or "").strip()
    resolved_coverage_failure = review_ok and old_reason_code == "SURFACE_MEMORY_RECALL_COVERAGE_FAILED"
    if resolved_coverage_failure:
        previous_readiness = dict(trace.get("recall_answer_readiness") or {}) if isinstance(trace.get("recall_answer_readiness"), Mapping) else {}
        if previous_readiness:
            trace["recall_answer_readiness_before_generic_evidence_review"] = previous_readiness
        detail = dict(previous_readiness.get("detail_evidence") or {}) if isinstance(previous_readiness.get("detail_evidence"), Mapping) else {}
        detail.update(
            {
                "required": True,
                "ready_for_deep_review": True,
                "basis": "generic_evidence_review",
                "body_item_count": max(1, _coerce_int(detail.get("body_item_count"))),
                "anchor_item_count": max(
                    _coerce_int(detail.get("anchor_item_count")),
                    len(list(review.get("evidence_refs") or [])),
                ),
            }
        )
        trace["recall_answer_readiness"] = {
            **previous_readiness,
            "status": "READY",
            "ready_to_answer": True,
            "response_mode": "answer_from_evidence",
            "surface_action": "answer",
            "reasons": [],
            "coverage_passed": True,
            "coverage_failed": False,
            "detail_evidence": detail,
        }
        trace["answer_ready"] = True
        trace["coverage_failed_resolved_by_generic_evidence_review"] = True
    return replace(
        packet,
        facts=tuple([fact, *list(getattr(packet, "facts", ()) or [])]),
        style_anchor=tuple(
            [
                "证据追问：优先使用刚补读到的网页/文件正文依据；回答时把证据转成自然判断，不要念调试字段。",
                *list(getattr(packet, "style_anchor", ()) or []),
            ]
        ),
        must_not_do=tuple(
            [
                "不要把只有旧摘要的记忆当成完整依据；有证据补读结果时必须基于补读正文回答。",
                "不要暴露 memory://、evd://、chat:// 或内部 trace 字段。",
                *list(getattr(packet, "must_not_do", ()) or []),
            ]
        ),
        evidence_refs=tuple(
            dict.fromkeys([*list(getattr(packet, "evidence_refs", ()) or []), *list(review.get("evidence_refs") or [])])
        ),
        confidence=max(float(getattr(packet, "confidence", 0.0) or 0.0), 0.82) if resolved_coverage_failure else getattr(packet, "confidence", 0.0),
        degraded=False if resolved_coverage_failure else getattr(packet, "degraded", False),
        reason_code=None if resolved_coverage_failure else getattr(packet, "reason_code", None),
        trace=trace,
    )


from host_brain.app.chat_retrieval_projection import (
    _build_surface_memory_recall_service,
    _embed_surface_memory_query,
    _recent_explicit_memory_facts,
    _surface_intent_id,
    _surface_memory_query_facts,
    _surface_memory_retrieval_summary,
    _surface_relationship_distance,
    _surface_task_mode_from_intent,
)
