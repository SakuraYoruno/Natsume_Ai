"""chat_retrieval_helpers.py — 检索辅助函数（从 chat_stream_handler.py 迁移）。

提供：
  - _run_retrieval: 并行执行检索
  - _build_skipped_retrieval_summary / _build_degraded_retrieval_summary: 降级摘要
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Mapping, Optional

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
    {"chat", "daily_chat", "first_ask", "neutral", "phatic", "plain_answer", "plain_hold"}
)


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
    intent_draft: Mapping[str, Any] | None = None,
) -> tuple[Dict[str, Any], list[dict[str, Any]]]:
    """Surface memory recall bridge.

    The old app path used to call the legacy retrieval stack here. The current
    main chain only needs a compressed surface_memory_recall_packet; raw recall
    rows and evidence packs must not be returned to the final text layer.
    """
    try:
        from dataclasses import replace  # noqa: PLC0415

        from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext  # noqa: PLC0415
        from host_brain.persona.surface_memory_recall import (  # noqa: PLC0415
            SURFACE_MEMORY_RECALL_SKIPPED,
            SurfaceMemoryRecallPacket,
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
        embedding_model_id = ""
        short_term_memory_facts = _recent_explicit_memory_facts(
            message=message,
            session_id=session_id,
            request_id=request_id,
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
                    "route_count": 1,
                    "short_term_explicit_memory": True,
                },
            )
        if not plan.should_recall:
            packet = SurfaceMemoryRecallPacket(
                query=plan.query,
                degraded=False,
                reason_code=SURFACE_MEMORY_RECALL_SKIPPED,
                trace={"skipped": True, "plan": plan.as_dict()},
            )
        elif not short_term_memory_facts:
            query_embedding, embedding_model_id = _embed_surface_memory_query(plan.query)
            packet = recall_surface_memory_packet(
                recall_service=_build_surface_memory_recall_service(),
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
            packet = replace(
                packet,
                trace={
                    **dict(packet.trace or {}),
                    "skipped": False,
                    "plan": plan.as_dict(),
                },
            )
        packet = await _maybe_apply_lightnovel_deep_review(
            packet=packet,
            message=message,
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            ts_ms=ts_ms,
        )
        return _surface_memory_retrieval_summary(
            request_id=request_id,
            ts_ms=ts_ms,
            packet=packet,
            embedding_model_id=embedding_model_id,
        ), []
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


async def _maybe_apply_lightnovel_deep_review(
    *,
    packet: Any,
    message: str,
    request_id: str,
    person_id: str,
    session_id: str,
    ts_ms: int,
) -> Any:
    try:
        from dataclasses import replace  # noqa: PLC0415

        from host_brain.codex_runtime.lightnovel_deep_reviewer import (  # noqa: PLC0415
            review_lightnovel_selection_from_memory_packet,
            should_deep_review_lightnovel_selection,
        )
    except Exception:
        return packet
    packet_payload = packet.as_dict() if hasattr(packet, "as_dict") else {}
    if not should_deep_review_lightnovel_selection(user_message=message, packet=packet_payload):
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
    trace = dict(getattr(packet, "trace", None) or {})
    trace["lightnovel_deep_review"] = {
        "status": str(review.get("status") or ""),
        "reason_code": str(review.get("reason_code") or ""),
        "selected": dict(review.get("selected") or {}) if isinstance(review.get("selected"), Mapping) else {},
        "item_count": len(list(review.get("items") or [])),
        "latency_ms": latency_ms,
        "cached": bool(review.get("cached")),
        "source": str(review.get("source") or ("cache" if bool(review.get("cached")) else "fetch")),
        "recommendation_signature": str(review.get("recommendation_signature") or ""),
        "source_episode_id": str(review.get("source_episode_id") or ""),
        "persisted_episode_id": str(review.get("persisted_episode_id") or ""),
        "persist_status": str(review.get("persist_status") or ""),
        "evidence_ref_count": len(list(review.get("evidence_refs") or [])),
    }
    return replace(
        packet,
        facts=tuple([fact, *list(getattr(packet, "facts", ()) or [])]),
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
                *list(getattr(packet, "must_not_do", ()) or []),
            ]
        ),
        evidence_refs=tuple(
            dict.fromkeys([*list(getattr(packet, "evidence_refs", ()) or []), *list(review.get("evidence_refs") or [])])
        ),
        trace=trace,
    )


def _surface_memory_retrieval_summary(
    *,
    request_id: str,
    ts_ms: int,
    packet: Any,
    embedding_model_id: str,
) -> Dict[str, Any]:
    trace = dict(getattr(packet, "trace", None) or {})
    plan = dict(trace.get("plan") or {}) if isinstance(trace.get("plan"), Mapping) else {}
    reason_code = str(getattr(packet, "reason_code", None) or "").strip()
    skipped = bool(trace.get("skipped")) or reason_code == "SURFACE_MEMORY_RECALL_SKIPPED"
    degraded = bool(getattr(packet, "degraded", False))
    status = "SKIPPED" if skipped else "DEGRADED" if degraded else "OK"
    route_count = int(dict(trace).get("route_count") or 0)
    return {
        "request_id": request_id,
        "status": status,
        "reason_code": reason_code or ("SURFACE_MEMORY_RECALL_SKIPPED" if skipped else "SURFACE_MEMORY_RECALL_OK"),
        "degraded": degraded,
        "route_count": route_count,
        "candidate_count": int(getattr(packet, "candidate_count", 0) or 0),
        "topk_count": 0,
        "routes": list(getattr(packet, "route_ids", ()) or []),
        "query_plan": {
            "intent": "surface_memory_recall",
            "task_mode": str(plan.get("task_mode") or ""),
            "recall_policy": str(plan.get("recall_policy") or ""),
            "reason": str(plan.get("reason") or ""),
            "query_kind": str(plan.get("query_kind") or ""),
            "temporal_scope": str(plan.get("temporal_scope") or ""),
            "target_domains": list(plan.get("target_domains") or []),
            "validation_requirements": list(plan.get("validation_requirements") or []),
            "text_len": len(str(getattr(packet, "query", "") or "")),
        },
        "target_stores": list(plan.get("target_store_ids") or []),
        "embedding_model_id": embedding_model_id,
        "surface_memory_recall_packet": packet.as_dict() if hasattr(packet, "as_dict") else {},
        "ts_ms": int(ts_ms),
    }


def _surface_task_mode_from_intent(*, message: str, intent_draft: Mapping[str, Any] | None) -> str:
    draft = dict(intent_draft or {}) if isinstance(intent_draft, Mapping) else {}
    raw = str(
        draft.get("task_mode")
        or draft.get("route")
        or draft.get("intent")
        or draft.get("primary_topic")
        or ""
    ).strip().lower()
    if raw and raw not in _GENERIC_SURFACE_TASK_MODES:
        return raw
    text = str(message or "").strip()
    if any(token in text for token in ("记一下", "记住", "帮我记", "替我记", "记着", "记下来")):
        return "memory_write"
    if any(token in text for token in ("还记得", "记得吗", "上次", "之前", "刚才", "跑完", "完成了吗", "失败原因", "今日推荐", "推荐结果")):
        return "recall_memory"
    if any(token in text for token in ("报错", "错误", "测试", "提交", "仓库", "代码", "怎么改", "下一步")):
        return "technical_help"
    if any(token in text for token in ("累", "困", "难受", "头疼")):
        return "tired"
    return "daily_chat"


def _surface_relationship_distance(bond_summary: Mapping[str, Any] | None) -> str:
    bond = dict(bond_summary or {}) if isinstance(bond_summary, Mapping) else {}
    return str(
        bond.get("relationship_distance")
        or bond.get("stage")
        or bond.get("stage_label")
        or "familiar"
    ).strip()


def _surface_intent_id(intent_draft: Mapping[str, Any] | None) -> str | None:
    draft = dict(intent_draft or {}) if isinstance(intent_draft, Mapping) else {}
    value = str(draft.get("intent_id") or draft.get("id") or "").strip()
    return value or None


def _surface_memory_query_facts(
    *,
    mood_state: Optional[str],
    bond_summary: Mapping[str, Any] | None,
    motive_state: Mapping[str, Any] | None,
) -> list[str]:
    facts: list[str] = []
    if mood_state:
        facts.append(f"当前语气状态：{str(mood_state).strip()}")
    distance = _surface_relationship_distance(bond_summary)
    if distance:
        facts.append(f"关系距离：{distance}")
    motive = dict(motive_state or {}) if isinstance(motive_state, Mapping) else {}
    topic = str(motive.get("topic") or motive.get("focus") or "").strip()
    if topic:
        facts.append(f"当前关注：{topic}")
    return facts[:3]


def _recent_explicit_memory_facts(*, message: str, session_id: str, request_id: str) -> list[str]:
    if not _looks_like_explicit_memory_content_query(message):
        return []
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return []
    try:
        from host_brain.app.dependencies import get_conversation_history  # noqa: PLC0415

        turns = get_conversation_history().fetch_recent_turns(
            session_id=normalized_session_id,
            max_turns=8,
            exclude_request_id=str(request_id or "").strip() or None,
        )
    except Exception:
        return []

    for turn in reversed(list(turns or [])):
        if getattr(turn, "role", "") != "user":
            continue
        payload = _extract_explicit_memory_write_payload(getattr(turn, "content", ""))
        if payload:
            return [f"记忆线索：用户刚才让你记住：{payload}"]
    return []


def _looks_like_explicit_memory_content_query(message: str) -> bool:
    text = _normalize_memory_text(message)
    if not text:
        return False
    if not any(word in text for word in _EXPLICIT_MEMORY_QUERY_WORDS):
        return False
    return any(anchor in text for anchor in _EXPLICIT_MEMORY_QUERY_ANCHORS)


def _extract_explicit_memory_write_payload(text: str) -> str:
    normalized = _normalize_memory_text(text)
    if not normalized:
        return ""
    for marker in sorted(_EXPLICIT_MEMORY_WRITE_MARKERS, key=len, reverse=True):
        index = normalized.find(marker)
        if index < 0:
            continue
        payload = normalized[index + len(marker) :]
        payload = payload.lstrip(" ，,。.!！?？:：；;、")
        payload = payload.strip(" \t\r\n，,。.!！?？；;")
        if len(payload) >= 2:
            return payload[:96].rstrip("，,、 ")
    return ""


def _normalize_memory_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _embed_surface_memory_query(query: str) -> tuple[list[float], str]:
    from host_brain.memory.chroma_client import DEFAULT_COLLECTION, embed_text, load_chroma_runtime_config  # noqa: PLC0415

    dim: int | None = None
    try:
        cfg_result = load_chroma_runtime_config()
        cfg = cfg_result.config if cfg_result.success else None
        profiles = dict(getattr(cfg, "collection_profiles", {}) or {})
        default_collection = str(getattr(cfg, "default_collection", DEFAULT_COLLECTION) or DEFAULT_COLLECTION)
        raw_dim = profiles.get(default_collection)
        if raw_dim is not None:
            dim = max(1, int(raw_dim))
    except Exception:
        dim = None
    values, model_id = embed_text(str(query or "").strip(), dim=dim)
    return list(values or []), str(model_id or "")


def _build_surface_memory_recall_service() -> Any:
    from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
    from host_brain.retrieval.recall_fusion import RecallFusionServiceContract  # noqa: PLC0415
    from host_brain.retrieval.recall_schemas import RecallRouteConfig, RecallRuntimeConfig  # noqa: PLC0415

    node_id = "surface_main_chain_memory_recall"
    routes = {
        "surface_life_collection": RecallRouteConfig(
            route_id="surface_life_collection",
            store_id="life_memory_collection",
            collection="life_memory_collection",
            top_k=5,
            score_weight=1.32,
            node_selector=node_id,
        ),
        "surface_life_episode": RecallRouteConfig(
            route_id="surface_life_episode",
            store_id="life_episode",
            collection="life_episode",
            top_k=6,
            score_weight=1.25,
            node_selector=node_id,
        ),
        "surface_semantic": RecallRouteConfig(
            route_id="surface_semantic",
            store_id="semantic_memory",
            collection="semantic_memory",
            top_k=4,
            score_weight=1.05,
            node_selector=node_id,
        ),
        "surface_episodic": RecallRouteConfig(
            route_id="surface_episodic",
            store_id="episodic_vector",
            collection="episodic",
            top_k=8,
            score_weight=1.0,
            node_selector=node_id,
        ),
        "surface_sqlite": RecallRouteConfig(
            route_id="surface_sqlite",
            store_id="sqlite_ledger",
            collection="sqlite_ledger",
            top_k=4,
            score_weight=0.85,
            node_selector=node_id,
        ),
        "surface_bond": RecallRouteConfig(
            route_id="surface_bond",
            store_id="neo4j_bond",
            collection="neo4j_bond",
            top_k=4,
            score_weight=0.95,
            node_selector=node_id,
        ),
    }
    try:
        from host_brain.app.dependencies import get_bond_neo4j_client  # noqa: PLC0415

        neo4j_client = get_bond_neo4j_client()
    except Exception:
        neo4j_client = None
    return RecallFusionServiceContract(
        ledger=get_ledger(),
        neo4j_client=neo4j_client,
        runtime_config=RecallRuntimeConfig(
            enabled=True,
            default_route="surface_episodic",
            routes=routes,
        ),
        node_id=node_id,
        use_hybrid=False,
    )


def _build_skipped_retrieval_summary(reason_code: str) -> Dict[str, Any]:
    return {
        "status": "SKIPPED",
        "reason_code": str(reason_code or "RETRIEVAL_SKIPPED"),
        "degraded": False,
        "route_count": 0,
        "candidate_count": 0,
        "topk_count": 0,
        "routes": [],
    }


def _build_degraded_retrieval_summary(reason_code: str) -> Dict[str, Any]:
    return {
        "status": "DEGRADED",
        "reason_code": str(reason_code or "RETRIEVAL_DEGRADED"),
        "degraded": True,
        "route_count": 0,
        "candidate_count": 0,
        "topk_count": 0,
        "routes": [],
    }
