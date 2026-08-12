"""Surface memory query projection and recall service construction."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional


_EXPLICIT_MEMORY_WRITE_MARKERS: tuple[str, ...] = (
    "帮我记住", "替我记住", "给我记住", "帮我记一下", "替我记一下",
    "给我记一下", "帮我记", "替我记", "记下来", "记一下", "记着", "记住",
)
_EXPLICIT_MEMORY_QUERY_ANCHORS: tuple[str, ...] = (
    "让你记", "叫你记", "要你记", "帮我记", "替我记", "给我记", "你记了",
    "你记住", "你刚才记", "你刚刚记",
)
_EXPLICIT_MEMORY_QUERY_WORDS: tuple[str, ...] = ("什么", "啥", "哪", "哪些", "内容")
_GENERIC_SURFACE_TASK_MODES: frozenset[str] = frozenset(
    {"chat", "daily_chat", "first_ask", "neutral", "plain_answer", "plain_hold"}
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


def _recent_explicit_memory_facts(
    *,
    message: str,
    session_id: str,
    request_id: str,
    conversation_history: Any | None,
) -> list[str]:
    if not _looks_like_explicit_memory_content_query(message):
        return []
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return []
    if conversation_history is None:
        return []
    try:
        turns = conversation_history.fetch_recent_turns(
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


def _build_surface_memory_recall_service(
    *,
    ledger: Any | None = None,
    life_memory_store: Any | None = None,
    bond_neo4j_client: Any | None = None,
    knowledge_graph_neo4j_client: Any | None = None,
    chroma_client: Any | None = None,
) -> Any:
    from host_brain.retrieval.recall_fusion import RecallFusionServiceContract  # noqa: PLC0415
    from host_brain.retrieval.recall_schemas import RecallRouteConfig, RecallRuntimeConfig  # noqa: PLC0415

    if ledger is None or life_memory_store is None:
        raise RuntimeError("HOST_CHAT_RETRIEVAL_AUTHORITY_NOT_INJECTED")

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
            top_k=8,
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
    }
    # The canonical Host no longer supplies a Bond graph.  Leaving this route
    # registered with a ``None`` client would still advertise legacy score/
    # stage evidence to downstream planners.  Explicit offline compatibility
    # roots may pass a client during the rollback window.
    if bond_neo4j_client is not None:
        routes["surface_bond"] = RecallRouteConfig(
            route_id="surface_bond",
            store_id="neo4j_bond",
            collection="neo4j_bond",
            top_k=4,
            score_weight=0.95,
            node_selector=node_id,
        )
    knowledge_graph_retriever = None
    if knowledge_graph_neo4j_client is not None:
        from host_brain.retrieval.knowledge_graph import KnowledgeGraphRetriever  # noqa: PLC0415

        knowledge_graph_retriever = KnowledgeGraphRetriever(
            neo4j_client=knowledge_graph_neo4j_client
        )
        routes["surface_knowledge_graph"] = RecallRouteConfig(
            route_id="surface_knowledge_graph",
            store_id="knowledge_graph",
            collection="knowledge_graph",
            top_k=6,
            score_weight=1.12,
            node_selector=node_id,
        )
    return RecallFusionServiceContract(
        ledger=ledger,
        life_memory_store=life_memory_store,
        chroma_client=chroma_client,
        neo4j_client=bond_neo4j_client,
        knowledge_graph_retriever=knowledge_graph_retriever,
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
