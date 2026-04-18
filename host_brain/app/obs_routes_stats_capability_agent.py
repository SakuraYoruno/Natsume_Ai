"""obs_routes_stats_capability_agent.py — Agent能力/Forge/检索/免疫门类统计端点。

拆分自 obs_routes_stats_capability.py（P2-9）：
  GET /obs/semantic-recall/stats
  GET /obs/immune-gate/stats
  GET /obs/immune-gate/replay/{request_id}
  GET /obs/retrieval/runtime
  GET /obs/capability-gaps/stats
  GET /obs/capability-gaps/forge-candidates
  POST /obs/capability-gaps/forge-proposals/preview
  POST /obs/capability-gaps/forge-proposals
  GET  /obs/capability-gaps/forge-proposals
  GET  /obs/capability-gaps/forge-proposals/{proposal_id}
  POST /obs/capability-gaps/forge-proposals/{proposal_id}/approve
  POST /obs/capability-gaps/forge-proposals/{proposal_id}/rollback
  POST /obs/capability-gaps/forge-proposals/runtime-match
  GET  /obs/capability-gaps/forge-runtime/stats
  GET  /obs/capability-gaps/forge-runtime/tuning
  GET  /obs/capability-gaps/forge-runtime/quality
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field
from host_brain.retrieval.model_suite import resolve_retrieval_model_suite

from host_brain.app.obs_routes_core import (
    router,
    _env_int,
    _get_ledger,
    _get_obs_route_cached_response,
    _now_ms,
    _safe_json,
)

logger = logging.getLogger("Natsume.obs")
_CAPABILITY_GAP_STATS_CACHE_TTL_MS = _env_int("OBS_CAPABILITY_GAP_STATS_CACHE_TTL_MS", 5000, minimum=0)


# ---------------------------------------------------------------------------
# Ref #103: GET /obs/semantic-recall/stats
# ---------------------------------------------------------------------------
@dataclass
class SemanticRecallStatsResponse:
    use_mock: bool
    chroma_host: str
    chroma_port: int
    top_k: int
    similarity_threshold: float
    collection_name: str
    mock_doc_count: int
    status: str
    timestamp: int


@router.get("/semantic-recall/stats")
def get_semantic_recall_stats():
    """Ref #103: 语义召回器统计端点。"""
    now = _now_ms()
    try:
        from host_brain.app.dependencies import get_semantic_recaller
        recaller = get_semantic_recaller()
        mock_doc_count = len(getattr(recaller, "_mock_store", []))
        return SemanticRecallStatsResponse(
            use_mock=recaller._use_mock, chroma_host=recaller._chroma_host,
            chroma_port=recaller._chroma_port, top_k=recaller._top_k,
            similarity_threshold=recaller._threshold, collection_name=recaller._collection_name,
            mock_doc_count=mock_doc_count, status="OK", timestamp=now,
        )
    except Exception as exc:
        return SemanticRecallStatsResponse(
            use_mock=True, chroma_host="localhost", chroma_port=8000, top_k=5,
            similarity_threshold=0.70, collection_name="episodic_memory",
            mock_doc_count=0, status=f"DEGRADED: {exc}", timestamp=now,
        )


# ---------------------------------------------------------------------------
# GET /obs/immune-gate/stats
# ---------------------------------------------------------------------------
class ImmuneGateReasonItem(BaseModel):
    reason_code: str
    count: int
    pct: float


class ImmuneGateRiskItem(BaseModel):
    risk_level: str
    count: int
    pct: float


class ImmuneGateRecentItem(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    event_type: str
    decision: str
    risk_level: Optional[str] = None
    score: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)
    detail: Optional[str] = None
    ts_ms: int


class ImmuneGateReplayDecisionItem(BaseModel):
    event_type: str
    stage: Optional[str] = None
    direction: Optional[str] = None
    phase: Optional[str] = None
    decision: str
    risk_level: Optional[str] = None
    score: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)
    matched_rules: List[str] = Field(default_factory=list)
    input_preview: Optional[str] = None
    detail: Optional[str] = None
    ts_ms: int


class ImmuneGateReplaySummary(BaseModel):
    event_count: int
    first_decision: Optional[str] = None
    last_decision: Optional[str] = None
    max_score: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)
    matched_rules: List[str] = Field(default_factory=list)


class ImmuneGateReplayResponse(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    input_preview: Optional[str] = None
    decisions: List[ImmuneGateReplayDecisionItem]
    summary: ImmuneGateReplaySummary
    timestamp: int


class ImmuneGateRuntimeState(BaseModel):
    status: str
    enabled: Optional[bool] = None
    blocking_enabled: Optional[bool] = None
    warn_threshold: Optional[float] = None
    block_threshold: Optional[float] = None
    template_id: Optional[str] = None
    template_rule_count: int = 0
    total_rule_count: int = 0
    counters: Dict[str, int] = Field(default_factory=dict)
    last_decision: Optional[str] = None
    last_risk_level: Optional[str] = None
    last_score: Optional[float] = None
    last_reason_codes: List[str] = Field(default_factory=list)
    detail: Optional[str] = None


class ImmuneGateStatsResponse(BaseModel):
    window_minutes: int
    total: int
    allow: int
    report: int
    block: int
    block_rate: float
    reason_codes: List[ImmuneGateReasonItem]
    risk_levels: List[ImmuneGateRiskItem]
    recent: List[ImmuneGateRecentItem]
    runtime_state: ImmuneGateRuntimeState
    timestamp: int


def _build_immune_gate_runtime_state() -> ImmuneGateRuntimeState:
    try:
        from host_brain.tribe.immune_agent import get_immune_agent
        raw = dict(get_immune_agent().export_runtime_state() or {})
        counters = dict(raw.get("counters") or {})
        last = dict(raw.get("last_inspection") or {})
        return ImmuneGateRuntimeState(
            status="live", enabled=bool(raw.get("enabled", True)),
            blocking_enabled=bool(raw.get("blocking_enabled", False)),
            warn_threshold=(float(raw.get("warn_threshold")) if raw.get("warn_threshold") is not None else None),
            block_threshold=(float(raw.get("block_threshold")) if raw.get("block_threshold") is not None else None),
            template_id=(str(raw.get("template_id") or "").strip() or None),
            template_rule_count=int(raw.get("template_rule_count") or 0),
            total_rule_count=int(raw.get("total_rule_count") or 0),
            counters={"total": int(counters.get("total") or 0), "allow": int(counters.get("allow") or 0), "report": int(counters.get("report") or 0), "block": int(counters.get("block") or 0)},
            last_decision=(str(last.get("decision") or "").strip().upper() or None),
            last_risk_level=(str(last.get("risk_level") or "").strip().lower() or None),
            last_score=(float(last.get("score")) if last.get("score") is not None else None),
            last_reason_codes=[str(c).strip() for c in list(last.get("reason_codes") or []) if str(c).strip()],
        )
    except Exception as exc:
        return ImmuneGateRuntimeState(status="degraded", detail=str(exc)[:160])


def _extract_immune_gate_event_fields(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event_type = str(row.get("event_type") or "").strip().upper()
    payload = _safe_json(row.get("payload"))
    payload_dict = dict(payload) if isinstance(payload, dict) else {}
    source_payload: Optional[Dict[str, Any]] = None
    if event_type == "IMMUNE_CHAT_GATE":
        source_payload = payload_dict
    elif event_type.startswith("INPUT_"):
        gate_payload = payload_dict.get("immune_gate")
        if isinstance(gate_payload, dict):
            source_payload = dict(gate_payload)
    if source_payload is None:
        return None
    decision = str(source_payload.get("decision") or "").strip().upper()
    if decision not in {"ALLOW", "REPORT", "BLOCK"}:
        reason_code = str(row.get("reason_code") or "").strip().upper()
        decision = "BLOCK" if "IMMUNE_BLOCKED" in reason_code else "ALLOW"
    risk_level = str(source_payload.get("risk_level") or "").strip().lower() or None
    score: Optional[float] = None
    try:
        if source_payload.get("score") is not None:
            score = float(source_payload.get("score"))
    except (TypeError, ValueError):
        pass
    raw_reason_codes = source_payload.get("reason_codes")
    reason_codes: list[str] = []
    if isinstance(raw_reason_codes, list):
        reason_codes = [str(c).strip() for c in raw_reason_codes if str(c).strip()]
    elif raw_reason_codes is not None:
        normalized = str(raw_reason_codes).strip()
        if normalized:
            reason_codes = [normalized]
    row_reason_code = str(row.get("reason_code") or "").strip()
    if row_reason_code and row_reason_code not in reason_codes and row_reason_code.upper().startswith("IMMUNE_"):
        reason_codes.append(row_reason_code)
    raw_matched_rules = source_payload.get("matched_rules")
    matched_rules: list[str] = []
    if isinstance(raw_matched_rules, list):
        matched_rules = [str(r).strip() for r in raw_matched_rules if str(r).strip()]
    elif raw_matched_rules is not None:
        normalized_rule = str(raw_matched_rules).strip()
        if normalized_rule:
            matched_rules = [normalized_rule]
    input_preview = None
    for candidate in (payload_dict.get("input_preview"), payload_dict.get("text_preview"), payload_dict.get("raw_text"), source_payload.get("input_preview")):
        normalized_candidate = str(candidate or "").strip()
        if normalized_candidate:
            input_preview = normalized_candidate[:240]
            break
    return {"request_id": str(row.get("request_id") or ""), "session_id": (str(row.get("session_id") or "").strip() or None), "event_type": event_type, "stage": (str(row.get("stage") or "").strip() or None), "direction": (str(row.get("direction") or "").strip() or None), "phase": (str(payload_dict.get("phase") or "").strip() or None), "decision": decision, "risk_level": risk_level, "score": score, "reason_codes": reason_codes, "matched_rules": matched_rules, "input_preview": input_preview, "detail": (str(row.get("detail") or "").strip() or None), "ts_ms": int(row.get("ts_ms") or 0)}


def _dedupe_ordered_values(values: List[str], *, limit: int) -> List[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
        if len(deduped) >= limit:
            break
    return deduped


@router.get("/immune-gate/stats", response_model=ImmuneGateStatsResponse)
def get_immune_gate_stats(window_minutes: int = Query(60, ge=1, le=10080), limit: int = Query(20, ge=1, le=200)):
    """免疫门统计。"""
    from collections import Counter
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    try:
        rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []
    observed: list[dict[str, Any]] = [item for row in rows if isinstance(row, dict) for item in [_extract_immune_gate_event_fields(row)] if item is not None]
    total = len(observed)
    allow = sum(1 for item in observed if item.get("decision") == "ALLOW")
    report = sum(1 for item in observed if item.get("decision") == "REPORT")
    block = sum(1 for item in observed if item.get("decision") == "BLOCK")
    block_rate = round(block / total * 100, 1) if total > 0 else 0.0
    reason_counter: Counter = Counter()
    risk_counter: Counter = Counter()
    for item in observed:
        for rc in list(item.get("reason_codes") or []):
            reason_counter[rc] += 1
        rl = str(item.get("risk_level") or "").strip().lower()
        if rl:
            risk_counter[rl] += 1
    reason_codes = [ImmuneGateReasonItem(reason_code=code, count=cnt, pct=round(cnt / total * 100, 1) if total > 0 else 0.0) for code, cnt in reason_counter.most_common(10)]
    risk_levels = [ImmuneGateRiskItem(risk_level=level, count=cnt, pct=round(cnt / total * 100, 1) if total > 0 else 0.0) for level, cnt in risk_counter.most_common(10)]
    recent_rows = sorted(observed, key=lambda item: int(item.get("ts_ms") or 0), reverse=True)[:limit]
    recent = [ImmuneGateRecentItem(request_id=str(item.get("request_id") or ""), session_id=item.get("session_id"), event_type=str(item.get("event_type") or ""), decision=str(item.get("decision") or "ALLOW"), risk_level=item.get("risk_level"), score=item.get("score"), reason_codes=[str(c) for c in list(item.get("reason_codes") or [])], detail=item.get("detail"), ts_ms=int(item.get("ts_ms") or 0)) for item in recent_rows]
    return ImmuneGateStatsResponse(window_minutes=window_minutes, total=total, allow=allow, report=report, block=block, block_rate=block_rate, reason_codes=reason_codes, risk_levels=risk_levels, recent=recent, runtime_state=_build_immune_gate_runtime_state(), timestamp=now)


@router.get("/immune-gate/replay/{request_id}", response_model=ImmuneGateReplayResponse)
def get_immune_gate_replay(request_id: str, limit: int = Query(120, ge=1, le=500)):
    """免疫门回放。"""
    request_key = str(request_id or "").strip()
    if not request_key:
        raise HTTPException(status_code=422, detail={"reason_code": "IMMUNE_REPLAY_INVALID_REQUEST_ID", "detail": "request_id is required"})
    ledger = _get_ledger()
    try:
        rows = ledger.fetch_by_request_id("voice_event_log", request_key) if hasattr(ledger, "fetch_by_request_id") else []
    except Exception:
        rows = []
    observed: list[dict[str, Any]] = [item for row in rows if isinstance(row, dict) for item in [_extract_immune_gate_event_fields(row)] if item is not None]
    if not observed:
        raise HTTPException(status_code=404, detail={"reason_code": "IMMUNE_REPLAY_NOT_FOUND", "detail": f"no immune gate events for request_id={request_key}"})
    ordered_rows = sorted(observed, key=lambda item: int(item.get("ts_ms") or 0))
    if len(ordered_rows) > limit:
        ordered_rows = ordered_rows[-limit:]
    decision_items = [ImmuneGateReplayDecisionItem(event_type=str(item.get("event_type") or ""), stage=item.get("stage"), direction=item.get("direction"), phase=item.get("phase"), decision=str(item.get("decision") or "ALLOW"), risk_level=item.get("risk_level"), score=item.get("score"), reason_codes=[str(c) for c in list(item.get("reason_codes") or [])][:8], matched_rules=[str(r) for r in list(item.get("matched_rules") or [])][:8], input_preview=item.get("input_preview"), detail=item.get("detail"), ts_ms=int(item.get("ts_ms") or 0)) for item in ordered_rows]
    score_values = [float(item.score) for item in decision_items if item.score is not None]
    all_reason_codes = [str(c) for item in decision_items for c in list(item.reason_codes or [])]
    all_matched_rules = [str(r) for item in decision_items for r in list(item.matched_rules or [])]
    session_id = next((str(item.get("session_id")).strip() for item in ordered_rows if str(item.get("session_id") or "").strip()), None)
    input_preview = next((str(item.get("input_preview")).strip() for item in ordered_rows if str(item.get("input_preview") or "").strip()), None)
    return ImmuneGateReplayResponse(request_id=request_key, session_id=session_id, input_preview=input_preview, decisions=decision_items, summary=ImmuneGateReplaySummary(event_count=len(decision_items), first_decision=(decision_items[0].decision if decision_items else None), last_decision=(decision_items[-1].decision if decision_items else None), max_score=(max(score_values) if score_values else None), reason_codes=_dedupe_ordered_values(all_reason_codes, limit=12), matched_rules=_dedupe_ordered_values(all_matched_rules, limit=12)), timestamp=_now_ms())


# ---------------------------------------------------------------------------
# GET /obs/retrieval/runtime
# ---------------------------------------------------------------------------
class RetrievalRuntimeRouteItem(BaseModel):
    route_id: str
    provider: str
    model_id: str
    dim: Optional[int] = None
    top_k: Optional[int] = None
    timeout_ms: int
    node_selector: str


class RetrievalRuntimeConfiguredSection(BaseModel):
    enabled: bool
    default_route: str
    routes: List[RetrievalRuntimeRouteItem]


class RetrievalRuntimeObservedItem(BaseModel):
    request_id: str
    status: str
    reason_code: Optional[str]
    detail: str
    candidate_count: int
    rerank_topk: int
    embedding_model_id: Optional[str]
    embedding_provider: Optional[str]
    effective_embedding_provider: Optional[str]
    fallback_embedding_used: bool
    fallback_embedding_reason_code: Optional[str]
    rerank_model_id: Optional[str]
    rerank_provider: Optional[str]
    effective_rerank_provider: Optional[str]
    fallback_reranker_used: bool
    fallback_reranker_reason_code: Optional[str]
    runtime_fields_complete: bool = False
    ts_ms: int


class RetrievalRuntimeResponse(BaseModel):
    suite_profile: Optional[str] = None
    suite_force: bool = False
    episodic_write_embedding_model_id: Optional[str] = None
    episodic_write_embedding_provider: Optional[str] = None
    read_write_model_aligned: bool = False
    embedding: RetrievalRuntimeConfiguredSection
    reranker: RetrievalRuntimeConfiguredSection
    latest: Optional[RetrievalRuntimeObservedItem]
    recent: List[RetrievalRuntimeObservedItem]
    recent_total: int
    recent_embedding_fallbacks: int
    recent_reranker_fallbacks: int
    timestamp: int


def _build_retrieval_runtime_observed_item(row: dict[str, Any]) -> RetrievalRuntimeObservedItem:
    payload = _safe_json(row.get("payload"))
    payload_dict = dict(payload) if isinstance(payload, dict) else {}
    embedding_model_id = str(payload_dict.get("embedding_model_id") or "").strip() or None
    rerank_model_id = str(payload_dict.get("rerank_model_id") or "").strip() or None
    return RetrievalRuntimeObservedItem(
        request_id=str(row.get("request_id") or ""), status=str(row.get("status") or "UNKNOWN"),
        reason_code=(str(row.get("reason_code") or "").strip() or None), detail=str(row.get("detail") or ""),
        candidate_count=int(payload_dict.get("candidate_count") or 0), rerank_topk=int(payload_dict.get("rerank_topk") or 0),
        embedding_model_id=embedding_model_id, embedding_provider=(str(payload_dict.get("embedding_provider") or "").strip() or None),
        effective_embedding_provider=(str(payload_dict.get("effective_embedding_provider") or "").strip() or None),
        fallback_embedding_used=bool(payload_dict.get("fallback_embedding_used", False)),
        fallback_embedding_reason_code=(str(payload_dict.get("fallback_embedding_reason_code") or "").strip() or None),
        rerank_model_id=rerank_model_id, rerank_provider=(str(payload_dict.get("rerank_provider") or "").strip() or None),
        effective_rerank_provider=(str(payload_dict.get("effective_rerank_provider") or "").strip() or None),
        fallback_reranker_used=bool(payload_dict.get("fallback_reranker_used", False)),
        fallback_reranker_reason_code=(str(payload_dict.get("fallback_reranker_reason_code") or "").strip() or None),
        runtime_fields_complete=bool(embedding_model_id or rerank_model_id), ts_ms=int(row.get("ts_ms") or 0),
    )


def _build_retrieval_runtime_route_items(config: Any, *, kind: str) -> List[RetrievalRuntimeRouteItem]:
    routes = []
    for route_id, route in sorted(dict(getattr(config, "routes", {}) or {}).items()):
        routes.append(RetrievalRuntimeRouteItem(
            route_id=str(route_id), provider=str(getattr(route, "provider", "") or ""), model_id=str(getattr(route, "model_id", "") or ""),
            dim=(int(getattr(route, "dim", 0)) if kind == "embedding" and getattr(route, "dim", None) is not None else None),
            top_k=(int(getattr(route, "top_k", 0)) if kind == "reranker" and getattr(route, "top_k", None) is not None else None),
            timeout_ms=int(getattr(route, "timeout_ms", 0) or 0), node_selector=str(getattr(route, "node_selector", "") or ""),
        ))
    return routes


def _get_retrieval_runtime_sync(request_id, limit, include_legacy):
    if request_id is not None:
        request_id = str(request_id).strip() or None
    limit = int(limit if isinstance(limit, int) else getattr(limit, "default", 10))
    include_legacy = bool(include_legacy if isinstance(include_legacy, bool) else getattr(include_legacy, "default", False))
    now = _now_ms()
    ledger = _get_ledger()
    from host_brain.retrieval.embedding import load_embedding_runtime_config
    from host_brain.retrieval.reranker_config import load_reranker_runtime_config
    suite = resolve_retrieval_model_suite(os.environ)
    embed_cfg = load_embedding_runtime_config(env=os.environ).config
    rerank_cfg = load_reranker_runtime_config(env=os.environ).config
    rows = []
    try:
        rows = [r for r in reversed(list(ledger.retrieval_event_log)) if str(r.get("op_kind") or "").strip().upper() == "PIPELINE"]
    except Exception:
        pass
    recent_items: List[RetrievalRuntimeObservedItem] = []
    seen_request_ids: set[str] = set()
    for row in rows:
        try:
            item = _build_retrieval_runtime_observed_item(dict(row))
        except Exception:
            continue
        if not include_legacy and not item.runtime_fields_complete:
            continue
        if item.request_id in seen_request_ids:
            continue
        seen_request_ids.add(item.request_id)
        recent_items.append(item)
        if len(recent_items) >= max(1, int(limit)):
            break
    latest_item: Optional[RetrievalRuntimeObservedItem] = None
    if request_id:
        for row in rows:
            if str(row.get("request_id") or "").strip() != str(request_id).strip():
                continue
            candidate = _build_retrieval_runtime_observed_item(dict(row))
            if include_legacy or candidate.runtime_fields_complete:
                latest_item = candidate
                break
        if latest_item is None:
            for row in rows:
                if str(row.get("request_id") or "").strip() == str(request_id).strip():
                    latest_item = _build_retrieval_runtime_observed_item(dict(row))
                    break
    if latest_item is None and recent_items:
        latest_item = recent_items[0]
    # 获取写侧 embedding 配置
    cfg_model_id = None
    cfg_provider = None
    default_route_id = str(getattr(embed_cfg, "default_route", "") or "").strip()
    routes = getattr(embed_cfg, "routes", None)
    if default_route_id and isinstance(routes, Mapping):
        route = routes.get(default_route_id)
        if route is not None:
            cfg_model_id = str(getattr(route, "model_id", "") or "").strip() or None
            cfg_provider = str(getattr(route, "provider", "") or "").strip() or None
    episodic_write_model_id = cfg_model_id
    episodic_write_provider = cfg_provider
    if not episodic_write_model_id or not episodic_write_provider:
        try:
            import host_brain.app.dependencies as _deps
            episodic_store = _deps.get_episodic_store()
            if not episodic_write_model_id:
                episodic_write_model_id = getattr(episodic_store, "embedding_model_id_hint", None)
            if not episodic_write_provider:
                episodic_write_provider = getattr(episodic_store, "embedding_provider_hint", None)
        except Exception:
            pass
    latest_embed_model = latest_item.embedding_model_id if latest_item is not None else None
    return RetrievalRuntimeResponse(
        suite_profile=(suite.profile_id if suite is not None else None),
        suite_force=bool(str(os.environ.get("RETR_MODEL_SUITE_FORCE") or "").strip().lower() in {"1", "true", "yes", "on"}),
        episodic_write_embedding_model_id=episodic_write_model_id, episodic_write_embedding_provider=episodic_write_provider,
        read_write_model_aligned=bool(episodic_write_model_id and latest_embed_model and episodic_write_model_id == latest_embed_model),
        embedding=RetrievalRuntimeConfiguredSection(enabled=bool(getattr(embed_cfg, "enabled", False)), default_route=str(getattr(embed_cfg, "default_route", "") or ""), routes=_build_retrieval_runtime_route_items(embed_cfg, kind="embedding") if embed_cfg is not None else []),
        reranker=RetrievalRuntimeConfiguredSection(enabled=bool(getattr(rerank_cfg, "enabled", False)), default_route=str(getattr(rerank_cfg, "default_route", "") or ""), routes=_build_retrieval_runtime_route_items(rerank_cfg, kind="reranker") if rerank_cfg is not None else []),
        latest=latest_item, recent=recent_items, recent_total=len(recent_items),
        recent_embedding_fallbacks=sum(1 for item in recent_items if item.fallback_embedding_used),
        recent_reranker_fallbacks=sum(1 for item in recent_items if item.fallback_reranker_used),
        timestamp=now,
    )


@router.get("/retrieval/runtime", response_model=RetrievalRuntimeResponse)
async def get_retrieval_runtime(
    request_id: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    include_legacy: bool = Query(False),
):
    """检索运行时配置与最近实际生效模型/降级情况。"""
    return await asyncio.to_thread(_get_retrieval_runtime_sync, request_id, limit, include_legacy)


# ---------------------------------------------------------------------------
# GET /obs/capability-gaps/stats 及相关端点
# ---------------------------------------------------------------------------
class CapabilityGapStatItem(BaseModel):
    key: str
    count: int


class CapabilityGapHotspotItem(BaseModel):
    candidate_key: str
    reason_code: str
    suggested_family: str
    missing_capability_tags: List[str]
    detected_count: int
    executed_count: int
    failed_count: int
    success_count: int
    failure_rate: float
    impact_sessions: int
    last_detected_ts_ms: Optional[int]


class CapabilityGapCandidateThresholds(BaseModel):
    min_detected_count: int
    min_unique_sessions: int
    min_failure_rate: float
    min_failed_count: int


class CapabilityGapCandidateExecutorItem(BaseModel):
    key: str
    count: int


class CapabilityGapForgeCandidateItem(BaseModel):
    candidate_key: str
    stage: str
    reason_code: str
    suggested_family: str
    missing_capability_tags: List[str]
    detected_count: int
    executed_count: int
    success_count: int
    failed_count: int
    failure_rate: float
    success_rate: float
    impact_sessions: int
    impact_requests: int
    priority_score: float
    recommended_action: str
    top_executor_targets: List[CapabilityGapCandidateExecutorItem]
    first_detected_ts_ms: Optional[int]
    last_detected_ts_ms: Optional[int]
    last_execution_ts_ms: Optional[int]


class CapabilityGapForgeRuntimeQualityItem(BaseModel):
    proposal_id: str
    status: str
    stage: str
    health_score: float
    window_failure_rate: float
    window_p95_latency_ms: int
    adaptive_tune_count: int
    last_tune_action: str
    updated_ts_ms: int


class CapabilityGapForgeRuntimeQualitySnapshot(BaseModel):
    timestamp: int
    summary: Dict[str, Any]
    items: List[CapabilityGapForgeRuntimeQualityItem]


class CapabilityGapStatsResponse(BaseModel):
    timestamp: int
    detected_total: int
    executed_total: int
    success_total: int
    failed_total: int
    success_rate: float
    active_runtime_capabilities: int
    by_reason: List[CapabilityGapStatItem]
    by_family: List[CapabilityGapStatItem]
    by_executor: List[CapabilityGapStatItem]
    hotspots: List[CapabilityGapHotspotItem]
    forge_candidates: List[CapabilityGapForgeCandidateItem]
    forge_runtime_quality: CapabilityGapForgeRuntimeQualitySnapshot
    candidate_thresholds: CapabilityGapCandidateThresholds
    recent_events: List[Dict[str, Any]]


class CapabilityGapForgeCandidatesResponse(BaseModel):
    timestamp: int
    candidate_thresholds: CapabilityGapCandidateThresholds
    total_candidates: int
    candidates: List[CapabilityGapForgeCandidateItem]


class CapabilityForgePreviewRequest(BaseModel):
    candidate_key: Optional[str] = None
    canary_traffic_percent: int = Field(5, ge=1, le=100)


class CapabilityForgeCreateRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    proposer: str = Field(..., min_length=1, max_length=128)
    candidate_key: Optional[str] = None
    canary_traffic_percent: int = Field(5, ge=1, le=100)


class CapabilityForgeApproveRequest(BaseModel):
    approval_token_ref: str = Field(..., min_length=6, max_length=256)
    approver: str = Field(..., min_length=1, max_length=128)
    target_stage: str = Field("shadow", description="shadow/canary/full")


class CapabilityForgeRollbackRequest(BaseModel):
    operator: str = Field(..., min_length=1, max_length=128)
    reason: Optional[str] = Field(None, max_length=200)


class CapabilityForgeRuntimeMatchRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=128)
    suggested_family: str = Field(..., min_length=1, max_length=64)
    missing_capability_tags: List[str] = Field(default_factory=list)
    reason_code: Optional[str] = None


class CapabilityForgePreviewResponse(BaseModel):
    timestamp: int
    candidate: Dict[str, Any]
    recommended_release_plan: Dict[str, Any]
    recommendation: str


class CapabilityForgeProposalResponse(BaseModel):
    timestamp: int
    proposal: Dict[str, Any]


class CapabilityForgeProposalListResponse(BaseModel):
    timestamp: int
    total: int
    items: List[Dict[str, Any]]


class CapabilityForgeRuntimeMatchResponse(BaseModel):
    timestamp: int
    decision: Dict[str, Any]


class CapabilityForgeRuntimeStatsResponse(BaseModel):
    timestamp: int
    counters: Dict[str, int]
    apply_rate: float


class CapabilityForgeRuntimeTuningResponse(BaseModel):
    timestamp: int
    summary: Dict[str, int]
    items: List[Dict[str, Any]]


class CapabilityForgeRuntimeQualityResponse(BaseModel):
    timestamp: int
    summary: Dict[str, Any]
    items: List[Dict[str, Any]]


def _build_capability_gap_hotspots(snapshot: Mapping[str, Any]) -> List[CapabilityGapHotspotItem]:
    return [CapabilityGapHotspotItem(candidate_key=str(item.get("candidate_key") or ""), reason_code=str(item.get("reason_code") or "UNKNOWN"), suggested_family=str(item.get("suggested_family") or "unknown"), missing_capability_tags=[str(tag) for tag in list(item.get("missing_capability_tags") or []) if str(tag or "").strip()], detected_count=int(item.get("detected_count") or 0), executed_count=int(item.get("executed_count") or 0), failed_count=int(item.get("failed_count") or 0), success_count=int(item.get("success_count") or 0), failure_rate=float(item.get("failure_rate") or 0.0), impact_sessions=int(item.get("impact_sessions") or 0), last_detected_ts_ms=(int(item.get("last_detected_ts_ms")) if item.get("last_detected_ts_ms") is not None else None)) for item in list(snapshot.get("hotspots") or []) if isinstance(item, Mapping)]


def _build_capability_gap_forge_candidates(snapshot: Mapping[str, Any]) -> List[CapabilityGapForgeCandidateItem]:
    return [CapabilityGapForgeCandidateItem(candidate_key=str(item.get("candidate_key") or ""), stage=str(item.get("stage") or "OBSERVE"), reason_code=str(item.get("reason_code") or "UNKNOWN"), suggested_family=str(item.get("suggested_family") or "unknown"), missing_capability_tags=[str(tag) for tag in list(item.get("missing_capability_tags") or []) if str(tag or "").strip()], detected_count=int(item.get("detected_count") or 0), executed_count=int(item.get("executed_count") or 0), success_count=int(item.get("success_count") or 0), failed_count=int(item.get("failed_count") or 0), failure_rate=float(item.get("failure_rate") or 0.0), success_rate=float(item.get("success_rate") or 0.0), impact_sessions=int(item.get("impact_sessions") or 0), impact_requests=int(item.get("impact_requests") or 0), priority_score=float(item.get("priority_score") or 0.0), recommended_action=str(item.get("recommended_action") or "observe"), top_executor_targets=[CapabilityGapCandidateExecutorItem(key=str(e.get("key") or "unknown"), count=int(e.get("count") or 0)) for e in list(item.get("top_executor_targets") or []) if isinstance(e, Mapping)], first_detected_ts_ms=(int(item.get("first_detected_ts_ms")) if item.get("first_detected_ts_ms") is not None else None), last_detected_ts_ms=(int(item.get("last_detected_ts_ms")) if item.get("last_detected_ts_ms") is not None else None), last_execution_ts_ms=(int(item.get("last_execution_ts_ms")) if item.get("last_execution_ts_ms") is not None else None)) for item in list(snapshot.get("forge_candidates") or []) if isinstance(item, Mapping)]


def _build_capability_gap_thresholds(snapshot: Mapping[str, Any]) -> CapabilityGapCandidateThresholds:
    parsed = dict(snapshot.get("candidate_thresholds") or {}) if isinstance(snapshot.get("candidate_thresholds"), Mapping) else {}
    return CapabilityGapCandidateThresholds(min_detected_count=int(parsed.get("min_detected_count") or 0), min_unique_sessions=int(parsed.get("min_unique_sessions") or 0), min_failure_rate=float(parsed.get("min_failure_rate") or 0.0), min_failed_count=int(parsed.get("min_failed_count") or 0))


def _build_capability_gap_runtime_quality(snapshot: Optional[Mapping[str, Any]]) -> CapabilityGapForgeRuntimeQualitySnapshot:
    parsed = dict(snapshot) if isinstance(snapshot, Mapping) else {}
    summary = dict(parsed.get("summary") or {}) if isinstance(parsed.get("summary"), Mapping) else {}
    return CapabilityGapForgeRuntimeQualitySnapshot(timestamp=int(parsed.get("timestamp") or _now_ms()), summary=summary, items=[CapabilityGapForgeRuntimeQualityItem(proposal_id=str(item.get("proposal_id") or ""), status=str(item.get("status") or "UNKNOWN"), stage=str(item.get("stage") or "PROPOSED"), health_score=float(item.get("health_score") or 0.0), window_failure_rate=float(item.get("window_failure_rate") or 0.0), window_p95_latency_ms=int(item.get("window_p95_latency_ms") or 0), adaptive_tune_count=int(item.get("adaptive_tune_count") or 0), last_tune_action=str(item.get("last_tune_action") or "NONE"), updated_ts_ms=int(item.get("updated_ts_ms") or 0)) for item in list(parsed.get("items") or []) if isinstance(item, Mapping)])


@router.get("/capability-gaps/stats", response_model=CapabilityGapStatsResponse)
def get_capability_gaps_stats(limit_recent: int = Query(20, ge=1, le=200), limit_hotspots: int = Query(8, ge=1, le=50), limit_candidates: int = Query(8, ge=1, le=50), min_candidate_detected: int = Query(3, ge=1, le=100), min_candidate_sessions: int = Query(2, ge=1, le=100), min_candidate_failure_rate: float = Query(0.35, ge=0.0, le=1.0), min_candidate_failed: int = Query(2, ge=0, le=100)):
    """能力缺口统计。"""
    def _build_response() -> CapabilityGapStatsResponse:
        from host_brain.app.dependencies import get_capability_forge_service, get_capability_gap_telemetry

        snapshot = get_capability_gap_telemetry().snapshot(limit_recent=limit_recent, limit_hotspots=limit_hotspots, limit_candidates=limit_candidates, min_candidate_detected=min_candidate_detected, min_candidate_sessions=min_candidate_sessions, min_candidate_failure_rate=min_candidate_failure_rate, min_candidate_failed=min_candidate_failed)
        try:
            forge_quality_snapshot = get_capability_forge_service().runtime_quality_stats(limit=8)
        except Exception:
            forge_quality_snapshot = {"timestamp": _now_ms(), "summary": {"tracked_proposals": 0, "unhealthy_proposals": 0, "critical_proposals": 0, "avg_health_score": 0.0, "avg_window_failure_rate": 0.0, "avg_window_p95_latency_ms": 0.0}, "items": []}
        return CapabilityGapStatsResponse(timestamp=int(snapshot.get("timestamp") or _now_ms()), detected_total=int(snapshot.get("detected_total") or 0), executed_total=int(snapshot.get("executed_total") or 0), success_total=int(snapshot.get("success_total") or 0), failed_total=int(snapshot.get("failed_total") or 0), success_rate=float(snapshot.get("success_rate") or 0.0), active_runtime_capabilities=int(snapshot.get("active_runtime_capabilities") or 0), by_reason=[CapabilityGapStatItem(key=str(item.get("key") or "UNKNOWN"), count=int(item.get("count") or 0)) for item in list(snapshot.get("by_reason") or []) if isinstance(item, Mapping)], by_family=[CapabilityGapStatItem(key=str(item.get("key") or "UNKNOWN"), count=int(item.get("count") or 0)) for item in list(snapshot.get("by_family") or []) if isinstance(item, Mapping)], by_executor=[CapabilityGapStatItem(key=str(item.get("key") or "UNKNOWN"), count=int(item.get("count") or 0)) for item in list(snapshot.get("by_executor") or []) if isinstance(item, Mapping)], hotspots=_build_capability_gap_hotspots(snapshot), forge_candidates=_build_capability_gap_forge_candidates(snapshot), forge_runtime_quality=_build_capability_gap_runtime_quality(forge_quality_snapshot), candidate_thresholds=_build_capability_gap_thresholds(snapshot), recent_events=[dict(item) for item in list(snapshot.get("recent_events") or []) if isinstance(item, Mapping)])

    return _get_obs_route_cached_response(
        cache_name="capability_gap_stats",
        cache_key={
            "limit_recent": int(limit_recent),
            "limit_hotspots": int(limit_hotspots),
            "limit_candidates": int(limit_candidates),
            "min_candidate_detected": int(min_candidate_detected),
            "min_candidate_sessions": int(min_candidate_sessions),
            "min_candidate_failure_rate": float(min_candidate_failure_rate),
            "min_candidate_failed": int(min_candidate_failed),
        },
        ttl_ms=_CAPABILITY_GAP_STATS_CACHE_TTL_MS,
        builder=_build_response,
    )


@router.get("/capability-gaps/forge-candidates", response_model=CapabilityGapForgeCandidatesResponse)
def get_capability_gap_forge_candidates(limit: int = Query(20, ge=1, le=200), min_candidate_detected: int = Query(3, ge=1, le=100), min_candidate_sessions: int = Query(2, ge=1, le=100), min_candidate_failure_rate: float = Query(0.35, ge=0.0, le=1.0), min_candidate_failed: int = Query(2, ge=0, le=100)):
    """能力缺口 Forge 候选列表。"""
    from host_brain.app.dependencies import get_capability_gap_telemetry
    snapshot = get_capability_gap_telemetry().snapshot(limit_recent=10, limit_hotspots=5, limit_candidates=limit, min_candidate_detected=min_candidate_detected, min_candidate_sessions=min_candidate_sessions, min_candidate_failure_rate=min_candidate_failure_rate, min_candidate_failed=min_candidate_failed)
    candidates = _build_capability_gap_forge_candidates(snapshot)
    return CapabilityGapForgeCandidatesResponse(timestamp=int(snapshot.get("timestamp") or _now_ms()), candidate_thresholds=_build_capability_gap_thresholds(snapshot), total_candidates=len(candidates), candidates=candidates)


@router.post("/capability-gaps/forge-proposals/preview", response_model=CapabilityForgePreviewResponse)
def preview_capability_forge_proposal(body: CapabilityForgePreviewRequest) -> CapabilityForgePreviewResponse:
    from host_brain.app.dependencies import get_capability_forge_service
    candidate = get_capability_forge_service().resolve_candidate(body.candidate_key)
    if candidate is None:
        raise HTTPException(status_code=404, detail={"reason_code": "CAPABILITY_FORGE_CANDIDATE_NOT_FOUND", "detail": "No forge candidate or hotspot is currently available"})
    failure_rate = float(candidate.get("failure_rate") or 0.0)
    detected_count = int(candidate.get("detected_count") or 0)
    impact_sessions = int(candidate.get("impact_sessions") or 0)
    recommendation = "forge_with_reliability_tests" if failure_rate >= 0.5 else ("forge_to_full_candidate_after_short_canary" if detected_count >= 6 and impact_sessions >= 3 else "forge_with_canary")
    return CapabilityForgePreviewResponse(timestamp=_now_ms(), candidate=dict(candidate), recommended_release_plan={"shadow": {"enabled": True}, "canary": {"enabled": True, "traffic_percent": int(body.canary_traffic_percent)}, "full": {"enabled": True}, "rollback": {"enabled": True, "observe_window_minutes": 30}}, recommendation=recommendation)


@router.post("/capability-gaps/forge-proposals", response_model=CapabilityForgeProposalResponse)
def create_capability_forge_proposal(body: CapabilityForgeCreateRequest) -> CapabilityForgeProposalResponse:
    from host_brain.app.dependencies import get_capability_forge_service
    try:
        proposal = get_capability_forge_service().create_proposal(session_id=body.session_id, proposer=body.proposer, candidate_key=body.candidate_key, canary_traffic_percent=body.canary_traffic_percent)
    except ValueError as exc:
        if str(exc) == "CAPABILITY_FORGE_CANDIDATE_NOT_FOUND":
            raise HTTPException(status_code=404, detail={"reason_code": str(exc), "detail": "candidate not found"})
        raise HTTPException(status_code=422, detail={"reason_code": str(exc), "detail": str(exc)})
    return CapabilityForgeProposalResponse(timestamp=_now_ms(), proposal=proposal)


@router.get("/capability-gaps/forge-proposals", response_model=CapabilityForgeProposalListResponse)
def list_capability_forge_proposals(status: Optional[str] = Query(None), limit: int = Query(20, ge=1, le=200)) -> CapabilityForgeProposalListResponse:
    from host_brain.app.dependencies import get_capability_forge_service
    items = get_capability_forge_service().list_proposals(status=status, limit=limit)
    return CapabilityForgeProposalListResponse(timestamp=_now_ms(), total=len(items), items=items)


@router.get("/capability-gaps/forge-proposals/{proposal_id}", response_model=CapabilityForgeProposalResponse)
def get_capability_forge_proposal(proposal_id: str) -> CapabilityForgeProposalResponse:
    from host_brain.app.dependencies import get_capability_forge_service
    proposal = get_capability_forge_service().get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail={"reason_code": "CAPABILITY_FORGE_PROPOSAL_NOT_FOUND", "detail": f"proposal_id={proposal_id}"})
    return CapabilityForgeProposalResponse(timestamp=_now_ms(), proposal=proposal)


@router.post("/capability-gaps/forge-proposals/{proposal_id}/approve", response_model=CapabilityForgeProposalResponse)
def approve_capability_forge_proposal(proposal_id: str, body: CapabilityForgeApproveRequest) -> CapabilityForgeProposalResponse:
    from host_brain.app.dependencies import get_capability_forge_service
    stage = str(body.target_stage or "").strip().lower()
    if stage not in {"shadow", "canary", "full"}:
        raise HTTPException(status_code=422, detail={"reason_code": "CAPABILITY_FORGE_INVALID_STAGE", "detail": "target_stage must be shadow/canary/full"})
    try:
        proposal = get_capability_forge_service().approve_and_promote(proposal_id=proposal_id, approval_token_ref=body.approval_token_ref, approver=body.approver, target_stage=stage)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"reason_code": str(exc), "detail": f"proposal_id={proposal_id}"})
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail={"reason_code": str(exc), "detail": "approval token validation failed"})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"reason_code": str(exc), "detail": str(exc)})
    return CapabilityForgeProposalResponse(timestamp=_now_ms(), proposal=proposal)


@router.post("/capability-gaps/forge-proposals/{proposal_id}/rollback", response_model=CapabilityForgeProposalResponse)
def rollback_capability_forge_proposal(proposal_id: str, body: CapabilityForgeRollbackRequest) -> CapabilityForgeProposalResponse:
    from host_brain.app.dependencies import get_capability_forge_service
    try:
        proposal = get_capability_forge_service().rollback(proposal_id=proposal_id, operator=body.operator, reason=body.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail={"reason_code": "CAPABILITY_FORGE_PROPOSAL_NOT_FOUND", "detail": f"proposal_id={proposal_id}"})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"reason_code": str(exc), "detail": str(exc)})
    return CapabilityForgeProposalResponse(timestamp=_now_ms(), proposal=proposal)


@router.post("/capability-gaps/forge-proposals/runtime-match", response_model=CapabilityForgeRuntimeMatchResponse)
def capability_forge_runtime_match(body: CapabilityForgeRuntimeMatchRequest) -> CapabilityForgeRuntimeMatchResponse:
    from host_brain.app.dependencies import get_capability_forge_service
    decision = get_capability_forge_service().get_runtime_route_override(request_id=body.request_id, suggested_family=body.suggested_family, missing_capability_tags=body.missing_capability_tags, reason_code=body.reason_code)
    return CapabilityForgeRuntimeMatchResponse(timestamp=_now_ms(), decision=dict(decision))


@router.get("/capability-gaps/forge-runtime/stats", response_model=CapabilityForgeRuntimeStatsResponse)
def capability_forge_runtime_stats() -> CapabilityForgeRuntimeStatsResponse:
    from host_brain.app.dependencies import get_capability_forge_service
    snapshot = get_capability_forge_service().runtime_match_stats()
    return CapabilityForgeRuntimeStatsResponse(timestamp=int(snapshot.get("timestamp") or _now_ms()), counters={str(k): int(v or 0) for k, v in dict(snapshot.get("counters") or {}).items()}, apply_rate=float(snapshot.get("apply_rate") or 0.0))


@router.get("/capability-gaps/forge-runtime/tuning", response_model=CapabilityForgeRuntimeTuningResponse)
def capability_forge_runtime_tuning_stats(limit: int = Query(20, ge=1, le=200)) -> CapabilityForgeRuntimeTuningResponse:
    from host_brain.app.dependencies import get_capability_forge_service
    snapshot = get_capability_forge_service().runtime_tuning_stats(limit=limit)
    return CapabilityForgeRuntimeTuningResponse(timestamp=int(snapshot.get("timestamp") or _now_ms()), summary={str(k): int(v or 0) for k, v in dict(snapshot.get("summary") or {}).items()}, items=[dict(item) for item in list(snapshot.get("items") or []) if isinstance(item, Mapping)])


@router.get("/capability-gaps/forge-runtime/quality", response_model=CapabilityForgeRuntimeQualityResponse)
def capability_forge_runtime_quality_stats(limit: int = Query(20, ge=1, le=200)) -> CapabilityForgeRuntimeQualityResponse:
    from host_brain.app.dependencies import get_capability_forge_service
    snapshot = get_capability_forge_service().runtime_quality_stats(limit=limit)
    return CapabilityForgeRuntimeQualityResponse(timestamp=int(snapshot.get("timestamp") or _now_ms()), summary=dict(snapshot.get("summary") or {}), items=[dict(item) for item in list(snapshot.get("items") or []) if isinstance(item, Mapping)])
