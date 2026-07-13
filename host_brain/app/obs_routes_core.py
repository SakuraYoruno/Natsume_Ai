"""Observability REST API — M18 监控面板后端数据接口。

提供 M17 React 前端所需的全套监控数据端点：
  GET /obs/services/health     — 全服务健康聚合（边缘服务 + 9 个 LLM 云节点）
  GET /obs/sessions            — 会话列表（分页 + 过滤）
  GET /obs/trace/{request_id}  — 单次会话完整链路节点详情
  GET /obs/metrics/timeseries  — 多维度时序数据（请求量/拒绝/延迟）
  GET /obs/llm/nodes           — LLM 云节点独立健康状态
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.observability.api_models import (
    GraphSchemaEdge,
    GraphSchemaNode,
    GraphSchemaResponse,
    LiveTraceFeedItem,
    LlmNodeItem,
    LlmNodesResponse,
    MetricsTimeseriesResponse,
    ServiceHealthItem,
    ServicesHealthResponse,
    SessionItem,
    SessionsResponse,
    StreamEventItem,
    TimeseriesPoint,
    TraceEdge,
    TraceLiveFeedDelta,
    TraceLiveFeedDeltaItem,
    TraceLiveFeedNodeDelta,
    TraceLiveFeedSnapshot,
    TraceNode,
    TraceResponse,
    TraceStreamEventsResponse,
    TribeEventItem,
    TribeEventsResponse,
)
from host_brain.observability.service_health_aggregator import ServiceHealthAggregator
from host_brain.observability.session_query import SessionQueryService
from host_brain.observability.metrics_timeseries import MetricsTimeseriesService
from host_brain.observability.process_state_projection import (
    RESOURCE_MANAGER_KEY,
    RESOURCE_MANAGER_KIND,
    RESOURCE_MANAGER_MAX_AGE_MS,
    read_process_projection,
)
from host_brain.observability.projection_ledger_view import get_projection_ledger_view
from host_brain.observability.service_registry import (
    LLM_NODE_REGISTRY as _LLM_NODE_REGISTRY,
    EDGE_SERVICE_EVENT_PREFIXES as _EDGE_SERVICE_EVENT_PREFIXES,
)

logger = logging.getLogger("Natsume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])
_DEBUG_SESSION_PREFIXES = ("smoke_", "sess-clarify-", "debug-", "chat-debug-")
_CORTEX_ORCHESTRATOR_NODE_IDS = {"cortex_orchestrator"}
_EXECUTIVE_ROUTER_NODE_IDS = {"executive_router"}


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return max(default, minimum)
    try:
        return max(float(raw), minimum)
    except (TypeError, ValueError):
        return max(default, minimum)


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return max(int(default), minimum)
    try:
        return max(int(raw), minimum)
    except (TypeError, ValueError):
        return max(int(default), minimum)


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


_TRACE_BUILD_TIMEOUT_SEC = _env_float("OBS_TRACE_BUILD_TIMEOUT_SEC", 3.5, minimum=1.0)
_TRACE_BUILD_FALLBACK_TIMEOUT_SEC = _env_float("OBS_TRACE_BUILD_FALLBACK_TIMEOUT_SEC", 1.5, minimum=0.5)
_TRACE_OPTIONAL_STAGE_TIMEOUT_SEC = _env_float("OBS_TRACE_OPTIONAL_STAGE_TIMEOUT_SEC", 0.6, minimum=0.1)
_TRACE_BUILD_SLOW_LOG_MS = int(_env_float("OBS_TRACE_BUILD_SLOW_LOG_MS", 1200.0, minimum=200.0))
_OBS_CACHE_WAIT_TIMEOUT_SEC = _env_float("OBS_CACHE_WAIT_TIMEOUT_SEC", 10.0, minimum=1.0)
_OBS_SESSIONS_CACHE_TTL_MS = _env_int("OBS_SESSIONS_CACHE_TTL_MS", 3000, minimum=0)

# ---------------------------------------------------------------------------
# 依赖注入
# ---------------------------------------------------------------------------
_ledger_provider = None

def set_ledger_provider(provider) -> None:
    global _ledger_provider
    _ledger_provider = provider

def _get_ledger() -> SQLiteLedger:
    if _ledger_provider is not None:
        return _ledger_provider()
    # Fallback to shared dependency factory to avoid import-order/cycle issues.
    from host_brain.app.dependencies import get_ledger as _deps_get_ledger
    return _deps_get_ledger()

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _now_ms() -> int:
    return int(time.time() * 1000)

def _safe_json(text: Any) -> Any:
    if isinstance(text, str):
        try:
            return json.loads(text)
        except Exception:
            return text
    return text


_OBS_ROUTE_RESPONSE_CACHE: dict[str, dict[str, Any]] = {}
_OBS_ROUTE_RESPONSE_INFLIGHT: dict[str, concurrent.futures.Future[Any]] = {}
_OBS_ROUTE_RESPONSE_CACHE_LOCK = threading.Lock()


def _clone_obs_cached_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        if hasattr(value, "model_copy"):
            return value.model_copy(deep=True)
        if hasattr(value, "copy"):
            return value.copy(deep=True)
    return copy.deepcopy(value)


def _get_obs_route_cached_response(
    *,
    cache_name: str,
    cache_key: Any,
    ttl_ms: int,
    builder: Callable[[], Any],
) -> Any:
    ttl_ms = max(0, int(ttl_ms))
    cache_slot = f"{str(cache_name).strip()}::{repr(cache_key)}"
    now = _now_ms()
    owner = False

    with _OBS_ROUTE_RESPONSE_CACHE_LOCK:
        cached = _OBS_ROUTE_RESPONSE_CACHE.get(cache_slot)
        if ttl_ms > 0 and isinstance(cached, dict) and int(cached.get("expires_at_ms") or 0) > now:
            return _clone_obs_cached_value(cached.get("value"))

        future = _OBS_ROUTE_RESPONSE_INFLIGHT.get(cache_slot)
        if future is None:
            future = concurrent.futures.Future()
            _OBS_ROUTE_RESPONSE_INFLIGHT[cache_slot] = future
            owner = True

    if not owner:
        value = future.result(timeout=_OBS_CACHE_WAIT_TIMEOUT_SEC)
        return _clone_obs_cached_value(value)

    try:
        value = builder()
        cloned = _clone_obs_cached_value(value)
        if ttl_ms > 0:
            with _OBS_ROUTE_RESPONSE_CACHE_LOCK:
                _OBS_ROUTE_RESPONSE_CACHE[cache_slot] = {
                    "expires_at_ms": _now_ms() + ttl_ms,
                    "value": cloned,
                }
        future.set_result(cloned)
        return value
    except Exception as exc:
        future.set_exception(exc)
        raise
    finally:
        with _OBS_ROUTE_RESPONSE_CACHE_LOCK:
            current = _OBS_ROUTE_RESPONSE_INFLIGHT.get(cache_slot)
            if current is future:
                _OBS_ROUTE_RESPONSE_INFLIGHT.pop(cache_slot, None)


def _clear_obs_route_response_cache() -> None:
    with _OBS_ROUTE_RESPONSE_CACHE_LOCK:
        _OBS_ROUTE_RESPONSE_CACHE.clear()
        _OBS_ROUTE_RESPONSE_INFLIGHT.clear()


def _voice_event_node_type(event_type: str) -> tuple[str, str]:
    normalized = str(event_type or "").strip().upper()
    if "IMMUNE" in normalized:
        return "immune", "Immune Gate"
    if "TTS" in normalized:
        return "tts", "TTS"
    if "VTS" in normalized:
        return "vts", "VTS"
    if "ASR" in normalized:
        return "asr", "ASR"
    return "edge", (normalized or "Edge Voice")


def _build_voice_trace_nodes(rows: List[Dict[str, Any]]) -> List[TraceNode]:
    nodes: List[TraceNode] = []
    ordered = sorted(
        [dict(row) for row in rows if isinstance(row, dict)],
        key=lambda row: (
            int(row.get("ts_ms") or 0),
            int(row.get("seq") or 0),
            str(row.get("event_type") or ""),
        ),
    )
    for idx, row in enumerate(ordered, start=1):
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        event_type = str(row.get("event_type") or "").strip()
        node_type, display_name = _voice_event_node_type(event_type)
        status = _normalize_trace_node_status(
            payload_dict.get("status") or row.get("status") or "UNKNOWN",
            node_type=node_type,
            reason_code=payload_dict.get("reason_code") or row.get("reason_code"),
            output_data=payload_dict,
        )
        reason_code = (
            str(payload_dict.get("reason_code") or row.get("reason_code") or "").strip()
            or None
        )
        if reason_code is None and node_type == "immune":
            reason_codes = list(payload_dict.get("reason_codes") or [])
            first_reason = str(reason_codes[0] if reason_codes else "").strip()
            reason_code = first_reason or None
        latency_ms = payload_dict.get("latency_ms")
        if latency_ms is None:
            latency_ms = payload_dict.get("roundtrip_latency_ms")
        if latency_ms is None:
            latency_ms = payload_dict.get("roundtrip_ms")
        try:
            latency_ms = int(latency_ms) if latency_ms is not None else None
        except (TypeError, ValueError):
            latency_ms = None

        output_data: Any = payload_dict if payload_dict else None
        if output_data is None and row.get("detail"):
            output_data = {"detail": row.get("detail")}
        input_data = {
            "event_type": event_type or None,
            "direction": row.get("direction"),
            "stage": row.get("stage"),
            "seq": row.get("seq"),
        }
        if not any(value is not None for value in input_data.values()):
            input_data = None

        nodes.append(
            TraceNode(
                node_id=f"voice_{node_type}_{int(row.get('seq') or idx)}",
                node_type=node_type,
                display_name=display_name,
                side=("host" if node_type == "immune" else "edge"),
                status=status,
                reason_code=reason_code,
                latency_ms=latency_ms,
                input_data=input_data,
                output_data=output_data,
                ts_ms=row.get("ts_ms"),
                error=reason_code,
            )
        )
    return nodes


def _build_trace_edges(
    existing_nodes: List[TraceNode],
    existing_edges: List[TraceEdge],
    appended_nodes: List[TraceNode],
) -> List[TraceEdge]:
    merged_edges = list(existing_edges)
    if not appended_nodes:
        return merged_edges

    last_existing_node_id = None
    if existing_nodes:
        last_existing_node_id = str(existing_nodes[-1].node_id or "").strip() or None

    if last_existing_node_id is not None:
        merged_edges.append(
            TraceEdge(
                source=last_existing_node_id,
                target=str(appended_nodes[0].node_id or ""),
            )
        )

    for idx in range(len(appended_nodes) - 1):
        merged_edges.append(
            TraceEdge(
                source=str(appended_nodes[idx].node_id or ""),
                target=str(appended_nodes[idx + 1].node_id or ""),
            )
        )
    return merged_edges


def _clone_trace_node(node: TraceNode, **updates: Any) -> TraceNode:
    payload = _model_dump_compat(node)
    payload.update(updates)
    return TraceNode(**payload)


def _infer_trace_group_id(
    *,
    node_id: Any,
    node_type: Any = "",
    side: Any = None,
) -> Optional[str]:
    normalized_id = str(node_id or "").strip()
    normalized_type = str(node_type or "").strip().lower()
    normalized_side = str(side or "").strip().lower()

    if normalized_type in {"tts", "vts"}:
        return "postprocess"
    if normalized_type in {"asr", "edge"} or normalized_id in {"asr", "gateway_webhook", "input_reporter"}:
        return "edge"
    if normalized_id.startswith("voice_"):
        return "edge"

    if normalized_id in {"host_input", "airlock_ingress", "governor"}:
        return "entry"

    if normalized_id in {"query_planner", "agentic_rag", "recall_fusion"} or normalized_id.startswith("retrieval_"):
        return "retrieval"

    if normalized_id in {"emotion_context", "persona_resident_memory", "motive_state"}:
        return "persona"

    if normalized_id in _CORTEX_ORCHESTRATOR_NODE_IDS or normalized_id == "render_policy":
        return "orchestration"

    if normalized_id in _EXECUTIVE_ROUTER_NODE_IDS or normalized_id in {"surface_reply"} or normalized_id.startswith("sub_agent"):
        return "agent"

    if normalized_id in {"prompt_builder", "llm_stream", "llm_gateway", "cortex_orchestrator_llm_stream"} or normalized_type == "llm" or normalized_id.startswith("llm_"):
        return "llm"

    if normalized_id in {"airlock_egress", "pii_filter", "audit_snapshot"}:
        return "postprocess"
    if normalized_id.startswith("episodic_store") or normalized_id.startswith("bond_update"):
        return "postprocess"

    if normalized_side == "edge":
        return "edge"
    if normalized_side == "host":
        return "orchestration"
    return None


def _enrich_trace_nodes(nodes: List[TraceNode]) -> List[TraceNode]:
    enriched: List[TraceNode] = []
    for node in nodes:
        group_id = node.group_id or _infer_trace_group_id(
            node_id=node.node_id,
            node_type=node.node_type,
            side=node.side,
        )
        enriched.append(
            _clone_trace_node(
                node,
                group_id=group_id,
            )
        )
    return enriched


def _infer_subgraph_owner_node_id(subgraph_id: str) -> Optional[str]:
    normalized = str(subgraph_id or "").strip().lower()
    if not normalized:
        return None
    if "intent" in normalized:
        return "sub_agent_intent"
    if "code" in normalized:
        return "sub_agent_code"
    if "translate" in normalized:
        return "sub_agent_translate"
    if "vision" in normalized:
        return "sub_agent_vision"
    return None


def _build_trace_subgraphs(request_id: str) -> List[Dict[str, Any]]:
    try:
        from host_brain.integrations.graph_evidence import get_evidence_plane

        rows = get_evidence_plane().query_subgraph_summary(request_id=request_id)
    except Exception as exc:
        logger.debug("_build_trace_subgraphs degraded: %s", exc)
        return []

    subgraphs: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue

        raw_subgraph_id = str(row.get("subgraph_id") or "").strip()
        subgraph_id = raw_subgraph_id or f"subgraph_{idx}"
        owner_node_id = _infer_subgraph_owner_node_id(subgraph_id)
        graph_trace_flat = _safe_json(row.get("graph_trace_flat_json"))
        flat_rows = list(graph_trace_flat or []) if isinstance(graph_trace_flat, list) else []

        nodes: List[Dict[str, Any]] = []
        prev_node_id: Optional[str] = None
        for step_index, entry in enumerate(flat_rows, start=1):
            if not isinstance(entry, dict):
                continue
            raw_node_id = str(entry.get("node_id") or f"step_{step_index}").strip() or f"step_{step_index}"
            node_id = f"{subgraph_id}:{raw_node_id}:{step_index}"
            reason_code = str(entry.get("reason_code") or "").strip() or None
            detail = str(entry.get("detail") or "").strip() or None
            node = TraceNode(
                node_id=node_id,
                node_type=str(entry.get("node_type") or "agent"),
                display_name=str(entry.get("display_name") or raw_node_id),
                side="host",
                status=_normalize_trace_node_status(
                    entry.get("status", "UNKNOWN"),
                    node_type=str(entry.get("node_type") or "agent"),
                    reason_code=reason_code,
                    output_data={"detail": detail} if detail else None,
                ),
                reason_code=reason_code,
                input_data={
                    "raw_node_id": raw_node_id,
                    "step_index": step_index,
                },
                output_data={
                    "detail": detail,
                    "entry": dict(entry),
                },
                ts_ms=entry.get("ts_ms"),
                error=reason_code,
                group_id="agent",
                owner_node_id=owner_node_id,
                subgraph_id=subgraph_id,
                parent_node_id=prev_node_id,
            )
            nodes.append(_model_dump_compat(node))
            prev_node_id = node_id

        edges = [
            {"source": nodes[index]["node_id"], "target": nodes[index + 1]["node_id"]}
            for index in range(len(nodes) - 1)
        ]

        try:
            node_count = int(row.get("node_count") or len(nodes))
        except (TypeError, ValueError):
            node_count = len(nodes)
        try:
            success_count = int(row.get("success_count") or 0)
        except (TypeError, ValueError):
            success_count = 0
        try:
            fail_count = int(row.get("fail_count") or 0)
        except (TypeError, ValueError):
            fail_count = 0
        try:
            committed_at_ms = int(row.get("committed_at_ms") or 0)
        except (TypeError, ValueError):
            committed_at_ms = 0

        subgraphs.append(
            {
                "subgraph_id": subgraph_id,
                "owner_node_id": owner_node_id,
                "node_count": node_count,
                "success_count": success_count,
                "fail_count": fail_count,
                "committed_at_ms": committed_at_ms,
                "nodes": nodes,
                "edges": edges,
                "graph_trace_flat": flat_rows,
            }
        )
    return subgraphs


def _build_trace_subgraphs_from_nodes_raw(nodes_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    subgraphs: List[Dict[str, Any]] = []
    for idx, node_row in enumerate(nodes_raw, start=1):
        if not isinstance(node_row, dict):
            continue
        owner_node_id = str(node_row.get("node_id") or "").strip() or f"sub_agent_{idx}"
        output_data = node_row.get("output_data")
        if not isinstance(output_data, dict):
            continue
        audit_refs = output_data.get("audit_refs")
        if not isinstance(audit_refs, dict):
            continue

        summary = (
            dict(audit_refs.get("subgraph_commit_summary") or {})
            if isinstance(audit_refs.get("subgraph_commit_summary"), dict)
            else {}
        )
        flat_rows_raw = summary.get("graph_trace_flat")
        flat_rows = (
            [dict(entry) for entry in flat_rows_raw if isinstance(entry, dict)]
            if isinstance(flat_rows_raw, list)
            else []
        )
        if not flat_rows:
            trace_rows_raw = audit_refs.get("subgraph_trace")
            flat_rows = (
                [dict(entry) for entry in trace_rows_raw if isinstance(entry, dict)]
                if isinstance(trace_rows_raw, list)
                else []
            )
        if not flat_rows:
            continue

        subgraph_id = (
            str(summary.get("subgraph_id") or audit_refs.get("subgraph_id") or "").strip()
            or f"{owner_node_id}_subgraph"
        )
        nodes: List[Dict[str, Any]] = []
        prev_node_id: Optional[str] = None
        for step_index, entry in enumerate(flat_rows, start=1):
            raw_node_id = str(entry.get("node_id") or f"step_{step_index}").strip() or f"step_{step_index}"
            node_id = f"{subgraph_id}:{raw_node_id}:{step_index}"
            reason_code = str(entry.get("reason_code") or "").strip() or None
            detail = str(entry.get("detail") or "").strip() or None
            node = TraceNode(
                node_id=node_id,
                node_type=str(entry.get("node_type") or "agent"),
                display_name=str(entry.get("display_name") or raw_node_id),
                side="host",
                status=_normalize_trace_node_status(
                    entry.get("status", "UNKNOWN"),
                    node_type=str(entry.get("node_type") or "agent"),
                    reason_code=reason_code,
                    output_data={"detail": detail} if detail else None,
                ),
                reason_code=reason_code,
                input_data={
                    "raw_node_id": raw_node_id,
                    "step_index": step_index,
                },
                output_data={
                    "detail": detail,
                    "entry": dict(entry),
                },
                ts_ms=entry.get("ts_ms"),
                error=reason_code,
                group_id="agent",
                owner_node_id=owner_node_id,
                subgraph_id=subgraph_id,
                parent_node_id=prev_node_id,
            )
            nodes.append(_model_dump_compat(node))
            prev_node_id = node_id

        edges = [
            {"source": nodes[index]["node_id"], "target": nodes[index + 1]["node_id"]}
            for index in range(len(nodes) - 1)
        ]
        try:
            node_count = int(summary.get("node_count") or len(nodes))
        except (TypeError, ValueError):
            node_count = len(nodes)
        try:
            success_count = int(summary.get("success_count") or 0)
        except (TypeError, ValueError):
            success_count = 0
        if success_count <= 0:
            success_count = sum(
                1
                for entry in flat_rows
                if str(entry.get("status") or "").strip().upper() in {"OK", "SUCCESS"}
            )
        try:
            fail_count = int(summary.get("fail_count") or 0)
        except (TypeError, ValueError):
            fail_count = 0
        if fail_count <= 0:
            fail_count = sum(
                1
                for entry in flat_rows
                if str(entry.get("status") or "").strip().upper() in {"FAILED", "ERROR", "DEGRADED"}
            )
        try:
            committed_at_ms = int(summary.get("committed_at_ms") or node_row.get("ts_ms") or _now_ms())
        except (TypeError, ValueError):
            committed_at_ms = _now_ms()

        subgraphs.append(
            {
                "subgraph_id": subgraph_id,
                "owner_node_id": owner_node_id,
                "node_count": node_count,
                "success_count": success_count,
                "fail_count": fail_count,
                "committed_at_ms": committed_at_ms,
                "nodes": nodes,
                "edges": edges,
                "graph_trace_flat": flat_rows,
            }
        )
    return subgraphs


def _merge_trace_subgraphs(
    base_subgraphs: List[Dict[str, Any]],
    extra_subgraphs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged = list(base_subgraphs)
    existing_ids = {
        str(item.get("subgraph_id") or "").strip()
        for item in merged
        if isinstance(item, dict)
    }
    for item in extra_subgraphs:
        if not isinstance(item, dict):
            continue
        subgraph_id = str(item.get("subgraph_id") or "").strip()
        if subgraph_id and subgraph_id in existing_ids:
            continue
        merged.append(item)
        if subgraph_id:
            existing_ids.add(subgraph_id)
    return merged


def _build_trace_stream_events(request_id: str) -> List[Dict[str, Any]]:
    try:
        from host_brain.integrations.graph_evidence import get_evidence_plane

        rows = get_evidence_plane().query_stream_events(request_id=request_id)
    except Exception as exc:
        logger.debug("_build_trace_stream_events degraded: %s", exc)
        return []

    events: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ts_ms = int(row.get("ts_ms") or 0)
        except (TypeError, ValueError):
            ts_ms = 0
        events.append(
            {
                "request_id": str(row.get("request_id", "")),
                "graph_id": str(row.get("graph_id", "")),
                "phase": str(row.get("phase", "")),
                "event_type": str(row.get("event_type", "")),
                "node_id": row.get("node_id"),
                "chunk": row.get("chunk"),
                "ts_ms": ts_ms,
            }
        )
    return events


def _coalesce_trace_status(base_status: str, nodes: List[TraceNode]) -> str:
    normalized_base = str(base_status or "OK").strip().upper() or "OK"
    success_statuses = {
        "OK",
        "UP",
        "APPROVE",
        "APPROVED",
        "APPLIED",
        "ACCEPTED",
        "RECEIVED",
        "DISPATCHED",
        "QUEUED",
        "STARTED",
        "PLAYING",
        "SUCCESS",
        "SENT",
        "SKIPPED",
    }
    for node in nodes:
        node_status = str(node.status or "").strip().upper()
        if not node_status or node_status in success_statuses:
            continue
        if _is_optional_node_degradation(node=node, node_status=node_status):
            continue
        if normalized_base == "FAIL":
            return normalized_base
        return "DEGRADED"
    return normalized_base


def _normalize_trace_node_status(
    raw_status: Any,
    *,
    node_type: str = "",
    reason_code: Any = None,
    output_data: Any = None,
    input_data: Any = None,
) -> str:
    normalized = _normalize_trace_status(raw_status, default="UNKNOWN")
    if normalized in {
        "APPROVE",
        "APPROVED",
        "APPLIED",
        "ACCEPTED",
        "RECEIVED",
        "DISPATCHED",
        "QUEUED",
        "SENT",
    }:
        return "OK"
    if normalized == "REJECT":
        return "FAIL"

    candidate_count = None
    if isinstance(output_data, dict):
        candidate_count = output_data.get("candidate_count")
        if candidate_count is None and isinstance(output_data.get("payload"), dict):
            candidate_count = output_data["payload"].get("candidate_count")
    if (
        normalized == "DEGRADED"
        and str(node_type or "").strip().lower() == "recall"
        and not str(reason_code or "").strip()
    ):
        try:
            if int(candidate_count or 0) == 0:
                return "OK"
        except (TypeError, ValueError):
            pass

    _ = input_data
    return normalized


def _is_optional_node_degradation(*, node: TraceNode, node_status: str) -> bool:
    normalized = str(node_status or "").strip().upper()
    if normalized in {"OK", "UP", "SUCCESS", "SENT"}:
        return False
    node_type = str(getattr(node, "node_type", "") or "").strip().lower()
    if node_type in {"tts", "vts"}:
        return True
    if node_type == "recall":
        if str(getattr(node, "reason_code", "") or "").strip():
            return False
        output_data = getattr(node, "output_data", None)
        if isinstance(output_data, dict):
            candidate_count = output_data.get("candidate_count")
            try:
                if int(candidate_count or 0) == 0:
                    return True
            except (TypeError, ValueError):
                return False
    input_data = getattr(node, "input_data", None)
    if isinstance(input_data, dict):
        event_type = str(input_data.get("event_type") or "").strip().upper()
        if "TTS" in event_type or "VTS" in event_type:
            return True
    return False


def _extract_output_from_trace_nodes_raw(nodes_raw: List[Dict[str, Any]]) -> Optional[str]:
    for node_row in reversed(nodes_raw or []):
        if not isinstance(node_row, dict):
            continue
        output_data = node_row.get("output_data")
        if not isinstance(output_data, dict):
            continue
        for key in ("final_response", "narrative", "output_text", "reply_preview", "text", "content"):
            value = output_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_recall_summary_from_trace_nodes_raw(nodes_raw: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    recall_node = None
    for node_row in reversed(nodes_raw or []):
        if not isinstance(node_row, dict):
            continue
        node_id = str(node_row.get("node_id") or "").strip().lower()
        node_type = str(node_row.get("node_type") or "").strip().lower()
        if node_id == "recall_fusion" or node_type == "recall":
            recall_node = node_row
            break
    if not isinstance(recall_node, dict):
        return None

    output_data = recall_node.get("output_data")
    output_dict = dict(output_data) if isinstance(output_data, dict) else {}
    payload = output_dict.get("payload")
    payload_dict = dict(payload) if isinstance(payload, dict) else {}
    source = payload_dict or output_dict

    candidate_count = source.get("candidate_count")
    route_count = source.get("route_count")
    route_error_count = source.get("route_error_count")
    if candidate_count is None and route_count is None and route_error_count is None and not output_dict:
        return None

    summary = {
        "candidate_count": candidate_count,
        "route_count": route_count,
        "route_error_count": route_error_count,
        "content_available": _payload_contains_recall_content(source),
        "message": "Retrieval events were not persisted for this request; showing trace snapshot only.",
        "stages": [
            {
                "op_kind": "RECALL",
                "status": _normalize_trace_node_status(
                    recall_node.get("status"),
                    node_type="recall",
                    reason_code=recall_node.get("reason_code"),
                    output_data=output_dict,
                ),
                "reason_code": recall_node.get("reason_code"),
                "detail": output_dict.get("detail"),
                "payload": source,
                "ts_ms": recall_node.get("ts_ms"),
            }
        ],
    }
    for key in (
        "items",
        "topk",
        "evidence_topk",
        "selected_refs",
        "evidence_refs",
        "evidence_refs_count",
        "pack_id",
        "fallback_embedding_used",
    ):
        if key in source and source.get(key) is not None:
            summary[key] = source.get(key)
    if "topk" not in summary and output_dict.get("topk") is not None:
        summary["topk"] = output_dict.get("topk")
    if summary.get("topk") or summary.get("items") or summary.get("evidence_topk"):
        summary["content_available"] = True
    return summary


def _fetch_request_rows(ledger: SQLiteLedger, table: str, request_id: str) -> List[Dict[str, Any]]:
    try:
        return ledger.fetch_by_request_id(table, request_id)
    except Exception:
        return []


def _normalize_trace_status(raw_status: Any, *, default: str = "UNKNOWN") -> str:
    normalized = str(raw_status or "").strip().upper()
    if not normalized:
        return default
    return {
        "SUCCESS": "OK",
        "HEALTHY": "OK",
        "ERROR": "FAIL",
        "FAILED": "FAIL",
        "REJECTED": "FAIL",
    }.get(normalized, normalized)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _extract_input_text(intent_row: Dict[str, Any]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    normalized = _safe_json(
        _first_present(
            intent_row.get("normalized_json"),
            intent_row.get("proposed_intent_draft_json"),
        )
    )
    normalized_dict = dict(normalized) if isinstance(normalized, dict) else None
    args = normalized_dict.get("args") if isinstance(normalized_dict, dict) else None
    text = None
    if isinstance(args, dict):
        text = _first_present(
            args.get("text"),
            args.get("query"),
            args.get("message"),
            args.get("content"),
        )
    text = _first_present(text, intent_row.get("raw_text"))
    return (str(text).strip() if isinstance(text, str) and text.strip() else None, normalized_dict)


def _extract_input_text_from_voice_rows(voice_rows: List[Dict[str, Any]]) -> Optional[str]:
    for row in reversed(voice_rows):
        event_type = str(row.get("event_type") or "").strip().upper()
        if event_type not in {"INPUT_ACCEPTED", "INPUT_DISPATCHED", "INPUT_COMPLETED"}:
            continue
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        for key in ("input_preview", "content_preview", "text_preview"):
            value = payload_dict.get(key)
            if isinstance(value, str):
                preview = value.strip()
                if preview and not _is_placeholder_preview(preview):
                    return preview
    return None


def _extract_bond_input_hint(bond_rows: List[Dict[str, Any]]) -> Optional[str]:
    for row in reversed(bond_rows):
        topic_id = row.get("topic_id")
        if not isinstance(topic_id, str):
            continue
        preview = topic_id.strip()
        if preview and not _is_placeholder_preview(preview):
            return preview
    return None


def _is_placeholder_preview(value: str) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return True
    return all(ch in {"?", "？"} for ch in normalized)


def _extract_dialogue_pair(memory_rows: List[Dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    for row in reversed(memory_rows):
        detail = str(row.get("detail") or "").strip()
        if "User:" not in detail:
            continue
        user_part = detail.split("User:", 1)[1]
        user_text, _, reply_text = user_part.partition("Natsume:")
        user_text = user_text.strip() or None
        reply_text = reply_text.strip() or None
        return user_text, reply_text
    return None, None


def _extract_output_from_voice_rows(voice_rows: List[Dict[str, Any]]) -> Optional[str]:
    for row in reversed(voice_rows):
        event_type = str(row.get("event_type") or "").strip().upper()
        if event_type != "INPUT_COMPLETED":
            continue
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        for key in ("reply_preview", "reply_text", "output_text", "content"):
            value = payload_dict.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        detail = str(row.get("detail") or "").strip()
        marker = "reply_preview="
        if marker in detail:
            preview = detail.split(marker, 1)[1].strip()
            if preview:
                return preview
    return None


def _extract_latest_audit_snapshot(error_rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for row in reversed(error_rows):
        if str(row.get("stage") or "").strip().upper() != "AUDIT_SNAPSHOT":
            continue
        detail = _safe_json(row.get("detail"))
        if isinstance(detail, dict):
            merged = dict(detail)
            merged.setdefault("stage", row.get("stage"))
            merged.setdefault("reason_code", row.get("reason_code"))
            merged.setdefault("ts_ms", row.get("ts_ms"))
            return merged
    return None


def _summarize_recall(retrieval_rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not retrieval_rows:
        return None

    summary: Dict[str, Any] = {
        "content_available": False,
        "message": "Raw recall documents are not persisted for this request; showing stage payloads instead.",
        "stages": [],
    }

    for row in retrieval_rows:
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        summary["stages"].append(
            {
                "op_kind": row.get("op_kind"),
                "status": _normalize_trace_status(row.get("status"), default="OK"),
                "reason_code": row.get("reason_code"),
                "detail": row.get("detail"),
                "payload": payload_dict if payload_dict else payload,
                "ts_ms": row.get("ts_ms"),
            }
        )
        if _payload_contains_recall_content(payload_dict):
            summary["content_available"] = True

        op_kind = str(row.get("op_kind") or "").strip().upper()
        if op_kind == "RECALL":
            summary["candidate_count"] = payload_dict.get("candidate_count")
            summary["route_count"] = payload_dict.get("route_count")
            summary["route_error_count"] = payload_dict.get("route_error_count")
            if payload_dict.get("items"):
                summary["items"] = payload_dict.get("items")
        elif op_kind == "RERANK":
            summary["topk_count"] = payload_dict.get("topk_count")
            summary["rerank_provider"] = payload_dict.get("provider")
            if payload_dict.get("topk"):
                summary["topk"] = payload_dict.get("topk")
                summary["content_available"] = True
        elif op_kind == "EVIDENCE_PACK":
            summary["evidence_refs_count"] = payload_dict.get("evidence_refs_count")
            summary["pack_id"] = payload_dict.get("pack_id")
            if payload_dict.get("evidence_refs"):
                summary["evidence_refs"] = payload_dict.get("evidence_refs")
                summary["content_available"] = True
            if payload_dict.get("topk"):
                summary["evidence_topk"] = payload_dict.get("topk")
        elif op_kind == "PIPELINE":
            summary["fallback_embedding_used"] = payload_dict.get("fallback_embedding_used")
            if payload_dict.get("selected_refs"):
                summary["selected_refs"] = payload_dict.get("selected_refs")

    if summary["content_available"]:
        summary["message"] = "Persisted recall snippets are available for this request."

    return summary


def _payload_contains_recall_content(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("items", "topk", "selected_refs", "slots"):
        value = payload.get(key)
        if isinstance(value, (list, tuple)) and len(value) > 0:
            return True
        if isinstance(value, dict) and bool(value):
            return True
        if isinstance(value, str) and value.strip():
            return True
    evidence_refs = payload.get("evidence_refs")
    if isinstance(evidence_refs, (list, tuple)) and len(evidence_refs) > 0:
        return True
    if isinstance(evidence_refs, str) and evidence_refs.strip():
        return True
    return False


def _build_trace_summary(
    request_id: str,
    intent_rows: List[Dict[str, Any]],
    governor_rows: List[Dict[str, Any]],
    retrieval_rows: List[Dict[str, Any]],
    llm_rows: List[Dict[str, Any]],
    airlock_rows: List[Dict[str, Any]],
    bond_rows: List[Dict[str, Any]],
    memory_rows: List[Dict[str, Any]],
    error_rows: List[Dict[str, Any]],
    voice_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    intent_row = intent_rows[-1] if intent_rows else {}
    governor_row = governor_rows[-1] if governor_rows else {}
    input_text, normalized_intent = _extract_input_text(intent_row)
    voice_input_text = _extract_input_text_from_voice_rows(voice_rows)
    bond_input_hint = _extract_bond_input_hint(bond_rows)
    memory_input_text, output_text = _extract_dialogue_pair(memory_rows)
    voice_output_text = _extract_output_from_voice_rows(voice_rows)
    audit_snapshot = _extract_latest_audit_snapshot(error_rows) or {}
    recall_summary = _summarize_recall(retrieval_rows)

    audit_tags = list(audit_snapshot.get("audit_tags") or [])
    metadata_keys = list(audit_snapshot.get("metadata_keys") or [])
    node_timings_ms = dict(audit_snapshot.get("node_timings_ms") or {})

    routing_selected = next(
        (tag.split(":", 1)[1] for tag in audit_tags if str(tag).startswith("ROUTING_SELECTED:")),
        None,
    )
    emotion_tags = [
        tag for tag in audit_tags
        if str(tag).startswith("EMOTION_TIMELINE:")
        or str(tag).startswith("EMOTION_INFERRED:")
        or str(tag).startswith("EPISODIC_EMOTION_")
    ]
    latest_bond_payload: Dict[str, Any] = {}
    for row in reversed(bond_rows):
        payload = _safe_json(row.get("payload"))
        if not isinstance(payload, dict):
            continue
        payload_dict = dict(payload)
        if payload_dict.get("to_stage") or payload_dict.get("total_score") is not None:
            latest_bond_payload = payload_dict
            break
        if not latest_bond_payload:
            latest_bond_payload = payload_dict

    total_latency_ms = None
    for row in reversed(voice_rows):
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        total_latency_ms = _extract_input_dispatch_latency_ms(row, payload_dict)
        if total_latency_ms is not None:
            break
    if total_latency_ms is None and node_timings_ms:
        try:
            total_latency_ms = int(sum(int(v or 0) for v in node_timings_ms.values()))
        except Exception:
            total_latency_ms = None

    return {
        "request_id": request_id,
        "session_id": _first_present(
            intent_row.get("session_id"),
            audit_snapshot.get("session_id"),
            (bond_rows[-1] if bond_rows else {}).get("session_id"),
        ),
        "input_text": _first_present(input_text, memory_input_text, voice_input_text, bond_input_hint),
        "output_text": _first_present(output_text, voice_output_text),
        "intent": normalized_intent,
        "task_type": audit_snapshot.get("task_type"),
        "decision": governor_row.get("decision"),
        "reason_code": governor_row.get("reason_code"),
        "audit_tags": audit_tags,
        "metadata_keys": metadata_keys,
        "routing_selected": routing_selected,
        "emotion_tags": emotion_tags,
        "bond_stage": latest_bond_payload.get("to_stage") or latest_bond_payload.get("from_stage"),
        "bond_score": latest_bond_payload.get("total_score"),
        "recall": recall_summary,
        "node_timings_ms": node_timings_ms,
        "failure_event": audit_snapshot.get("failure_event"),
        "governor_passed": audit_snapshot.get("governor_passed"),
        "final_response_chars": audit_snapshot.get("final_response_chars"),
        "airlock_blocked": any(bool(row.get("blocked")) for row in airlock_rows),
        "llm_calls": len(llm_rows),
        "total_latency_ms": total_latency_ms,
        "audit_snapshot": audit_snapshot or None,
    }


def _build_intent_trace_node(intent_row: Dict[str, Any]) -> Optional[TraceNode]:
    if not intent_row:
        return None
    input_text, normalized_intent = _extract_input_text(intent_row)
    lint_result = _safe_json(intent_row.get("lint_result"))
    return TraceNode(
        node_id="host_input",
        node_type="intent",
        display_name="Host Input",
        side="host",
        status="OK",
        input_data={"raw_text": input_text},
        output_data={
            "parse_stage": intent_row.get("parse_stage"),
            "normalized_intent": normalized_intent,
            "lint_result": lint_result,
        },
        ts_ms=intent_row.get("ts_ms"),
    )


def _build_governor_trace_node(
    governor_row: Dict[str, Any],
    summary: Dict[str, Any],
) -> Optional[TraceNode]:
    if not governor_row:
        return None
    rule_trace = _safe_json(governor_row.get("rule_trace"))
    snapshot_refs = _safe_json(governor_row.get("snapshot_refs"))
    tokens = _safe_json(governor_row.get("tokens"))
    node_timings_ms = dict(summary.get("node_timings_ms") or {})
    decision = str(governor_row.get("decision") or "").strip().upper()
    status = "OK" if decision == "APPROVE" else "FAIL"
    return TraceNode(
        node_id="governor",
        node_type="system",
        display_name="Governor",
        side="host",
        status=status,
        reason_code=governor_row.get("reason_code"),
        latency_ms=node_timings_ms.get("governor"),
        input_data={
            "stage": governor_row.get("stage"),
            "rule_trace": rule_trace,
        },
        output_data={
            "decision": decision or None,
            "snapshot_refs": snapshot_refs,
            "tokens": tokens,
        },
        ts_ms=governor_row.get("ts_ms"),
        error=governor_row.get("reason_code"),
    )


def _build_retrieval_trace_nodes(
    retrieval_rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
) -> List[TraceNode]:
    if not retrieval_rows:
        return []

    node_timings_ms = dict(summary.get("node_timings_ms") or {})
    display_names = {
        "QUERY_PLANNER": "Query Planner",
        "AGENTIC_RAG": "Agentic RAG",
        "EMBEDDING": "Recall Embedding",
        "RECALL": "Recall Fusion",
        "EVIDENCE_PACK": "Evidence Pack",
        "PIPELINE": "Retrieval Pipeline",
    }
    node_ids = {
        "QUERY_PLANNER": "query_planner",
        "AGENTIC_RAG": "agentic_rag",
        "EMBEDDING": "retrieval_embedding",
        "RECALL": "recall_fusion",
        "EVIDENCE_PACK": "retrieval_evidence_pack",
        "PIPELINE": "retrieval_pipeline",
    }

    nodes: List[TraceNode] = []
    query_planner_emitted = False
    agentic_rag_emitted = False
    for row in retrieval_rows:
        op_kind = str(row.get("op_kind") or "").strip().upper() or "UNKNOWN"
        if op_kind == "RERANK":
            continue
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        latency_ms = payload_dict.get("latency_ms")
        if latency_ms is None and op_kind in ("RECALL", "PIPELINE"):
            latency_ms = node_timings_ms.get("recall_fusion")
        if op_kind == "QUERY_PLANNER":
            query_planner_emitted = True
        if op_kind == "AGENTIC_RAG":
            agentic_rag_emitted = True

        if op_kind == "PIPELINE" and isinstance(payload_dict.get("query_plan"), dict) and not query_planner_emitted:
            nodes.append(
                TraceNode(
                    node_id="query_planner",
                    node_type="retrieval",
                    display_name="Query Planner",
                    side="host",
                    status=_normalize_trace_node_status(
                        row.get("status"),
                        node_type="retrieval",
                        reason_code=row.get("reason_code"),
                        output_data=payload_dict.get("query_plan"),
                    ),
                    reason_code=row.get("reason_code"),
                    latency_ms=node_timings_ms.get("query_planner"),
                    input_data={"stage": row.get("stage"), "op_kind": "QUERY_PLANNER"},
                    output_data=payload_dict.get("query_plan"),
                    ts_ms=row.get("ts_ms"),
                    error=row.get("reason_code"),
                )
            )
            query_planner_emitted = True

        if op_kind == "PIPELINE" and isinstance(payload_dict.get("agentic_meta"), dict) and not agentic_rag_emitted:
            nodes.append(
                TraceNode(
                    node_id="agentic_rag",
                    node_type="retrieval",
                    display_name="Agentic RAG",
                    side="host",
                    status=_normalize_trace_node_status(
                        row.get("status"),
                        node_type="retrieval",
                        reason_code=row.get("reason_code"),
                        output_data=payload_dict.get("agentic_meta"),
                    ),
                    reason_code=row.get("reason_code"),
                    latency_ms=node_timings_ms.get("agentic_rag"),
                    input_data={"stage": row.get("stage"), "op_kind": "AGENTIC_RAG"},
                    output_data=payload_dict.get("agentic_meta"),
                    ts_ms=row.get("ts_ms"),
                    error=row.get("reason_code"),
                )
            )
            agentic_rag_emitted = True

        nodes.append(
            TraceNode(
                node_id=node_ids.get(op_kind, f"retrieval_{op_kind.lower()}"),
                node_type="recall" if op_kind == "RECALL" else "retrieval",
                display_name=display_names.get(op_kind, op_kind.title()),
                side="host",
                status=_normalize_trace_node_status(
                    row.get("status"),
                    node_type="recall" if op_kind == "RECALL" else "retrieval",
                    reason_code=row.get("reason_code"),
                    output_data={
                        "payload": payload_dict if payload_dict else payload,
                        "detail": row.get("detail"),
                        "content_available": _payload_contains_recall_content(payload_dict),
                    },
                ),
                reason_code=row.get("reason_code"),
                latency_ms=latency_ms,
                input_data={
                    "stage": row.get("stage"),
                    "op_kind": op_kind,
                },
                output_data={
                    "payload": payload_dict if payload_dict else payload,
                    "detail": row.get("detail"),
                    "content_available": _payload_contains_recall_content(payload_dict),
                },
                ts_ms=row.get("ts_ms"),
                error=row.get("reason_code"),
            )
        )
    return nodes


def _build_persona_trace_nodes(summary: Dict[str, Any]) -> List[TraceNode]:
    audit_snapshot = dict(summary.get("audit_snapshot") or {})
    node_timings_ms = dict(summary.get("node_timings_ms") or {})
    ts_ms = audit_snapshot.get("ts_ms")
    emotion_context = audit_snapshot.get("emotion_context")
    if not isinstance(emotion_context, dict):
        emotion_context = {}
    emotion_tags = list(summary.get("emotion_tags") or [])
    if emotion_tags and not emotion_context:
        emotion_context = {"emotion_tags": emotion_tags}

    resident_memory = audit_snapshot.get("persona_resident_memory")
    if resident_memory is None:
        resident_memory = audit_snapshot.get("resident_memory")
    motive_state = audit_snapshot.get("motive_state")
    if not isinstance(motive_state, dict):
        motive_state = {}

    nodes: List[TraceNode] = []
    if emotion_context:
        nodes.append(
            TraceNode(
                node_id="emotion_context",
                node_type="system",
                display_name="Emotion Context",
                side="host",
                status="OK",
                latency_ms=node_timings_ms.get("emotion_context"),
                output_data=emotion_context,
                ts_ms=ts_ms,
            )
        )
    if resident_memory:
        nodes.append(
            TraceNode(
                node_id="persona_resident_memory",
                node_type="memory",
                display_name="Persona Resident Memory",
                side="host",
                status="OK",
                latency_ms=node_timings_ms.get("persona_resident_memory"),
                output_data=resident_memory,
                ts_ms=ts_ms,
            )
        )
    if motive_state:
        nodes.append(
            TraceNode(
                node_id="motive_state",
                node_type="system",
                display_name="Motive State",
                side="host",
                status="OK",
                latency_ms=node_timings_ms.get("motive_state"),
                output_data=motive_state,
                ts_ms=ts_ms,
            )
        )
    return nodes


def _build_orchestration_trace_nodes(
    summary: Dict[str, Any],
    subgraphs: List[Dict[str, Any]],
) -> List[TraceNode]:
    audit_snapshot = dict(summary.get("audit_snapshot") or {})
    node_timings_ms = dict(summary.get("node_timings_ms") or {})
    ts_ms = audit_snapshot.get("ts_ms") or _now_ms()
    failure_event = summary.get("failure_event")
    render_policy = audit_snapshot.get("render_policy")

    nodes: List[TraceNode] = []
    if isinstance(render_policy, dict) and render_policy:
        nodes.append(
            TraceNode(
                node_id="render_policy",
                node_type="system",
                display_name="Render Policy",
                side="host",
                status="OK",
                latency_ms=node_timings_ms.get("render_policy"),
                output_data=render_policy,
                ts_ms=ts_ms,
            )
        )

    if summary.get("routing_selected") or summary.get("output_text") or failure_event:
        nodes.append(
            TraceNode(
                node_id="cortex_orchestrator",
                node_type="agent",
                display_name="Cortex Orchestrator",
                side="host",
                status="FAIL" if failure_event else "OK",
                reason_code=failure_event,
                latency_ms=_first_present(
                    node_timings_ms.get("cortex_orchestrator"),
                ),
                output_data={
                    "routing_selected": summary.get("routing_selected"),
                    "final_response_chars": summary.get("final_response_chars"),
                    "output_text": summary.get("output_text"),
                },
                ts_ms=ts_ms,
                error=failure_event,
            )
        )

    if subgraphs:
        has_subgraph_fail = any(int(item.get("fail_count") or 0) > 0 for item in subgraphs if isinstance(item, dict))
        nodes.append(
            TraceNode(
                node_id="executive_router",
                node_type="agent",
                display_name="Executive Router",
                side="host",
                status="DEGRADED" if has_subgraph_fail else "OK",
                latency_ms=_first_present(
                    node_timings_ms.get("executive_router"),
                ),
                output_data={
                    "subgraph_count": len(subgraphs),
                    "subgraph_ids": [
                        str(item.get("subgraph_id") or "")
                        for item in subgraphs
                        if isinstance(item, dict)
                    ],
                },
                ts_ms=ts_ms,
                error="SUBGRAPH_FAILED" if has_subgraph_fail else None,
            )
        )

    if summary.get("output_text") or failure_event:
        nodes.append(
            TraceNode(
                node_id="surface_reply",
                node_type="agent",
                display_name="Surface Reply",
                side="host",
                status="FAIL" if failure_event else "OK",
                reason_code=failure_event,
                latency_ms=node_timings_ms.get("surface_reply"),
                output_data={
                    "final_response": summary.get("output_text"),
                    "final_response_chars": summary.get("final_response_chars"),
                },
                ts_ms=ts_ms,
                error=failure_event,
            )
        )
    return nodes


def _append_missing_trace_nodes(
    existing_nodes: List[TraceNode],
    candidate_nodes: List[TraceNode],
) -> List[TraceNode]:
    known_ids = {
        str(node.node_id or "").strip()
        for node in existing_nodes
        if str(node.node_id or "").strip()
    }
    appended: List[TraceNode] = []
    for node in candidate_nodes:
        node_id = str(node.node_id or "").strip()
        if not node_id or node_id in known_ids:
            continue
        appended.append(node)
        known_ids.add(node_id)
    return appended


def _build_llm_trace_nodes(
    llm_rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
) -> List[TraceNode]:
    nodes: List[TraceNode] = []
    for row in llm_rows:
        registry_meta = _LLM_NODE_REGISTRY.get(str(row.get("node_id") or ""))
        nodes.append(
            TraceNode(
                node_id=str(row.get("node_id") or "llm_node"),
                node_type="llm",
                display_name=(registry_meta or {}).get("role") or str(row.get("node_id") or "LLM"),
                side="host",
                provider=row.get("provider"),
                model_id=row.get("model_id"),
                status=_normalize_trace_status(row.get("status"), default="OK"),
                reason_code=row.get("reason_code"),
                latency_ms=row.get("latency_ms"),
                input_data={"input_digest": row.get("input_digest")},
                output_data={"output_digest": row.get("output_digest")},
                ts_ms=row.get("ts_ms"),
                error=row.get("reason_code"),
            )
        )

    audit_tags = list(summary.get("audit_tags") or [])
    if summary.get("routing_selected") or summary.get("output_text") or summary.get("failure_event"):
        nodes.append(
            TraceNode(
                node_id="llm_gateway",
                node_type="llm",
                display_name="LLM Gateway",
                side="host",
                provider="delegated" if not llm_rows else None,
                model_id=summary.get("routing_selected"),
                status="FAIL" if summary.get("failure_event") else "OK",
                reason_code=summary.get("failure_event"),
                latency_ms=(summary.get("node_timings_ms") or {}).get("llm_gateway"),
                input_data={
                    "routing_selected": summary.get("routing_selected"),
                    "emotion_tags": summary.get("emotion_tags"),
                },
                output_data={
                    "final_response": summary.get("output_text"),
                    "audit_tags": audit_tags,
                    "final_response_chars": summary.get("final_response_chars"),
                },
                ts_ms=((summary.get("audit_snapshot") or {}).get("ts_ms")),
                error=summary.get("failure_event"),
            )
        )
    return nodes


def _build_airlock_trace_nodes(
    airlock_rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
) -> List[TraceNode]:
    nodes: List[TraceNode] = []
    for idx, row in enumerate(airlock_rows, start=1):
        blocked = bool(row.get("blocked"))
        direction = str(row.get("direction") or "").strip().upper() or "UNKNOWN"
        nodes.append(
            TraceNode(
                node_id="airlock" if idx == 1 else f"airlock_{idx}",
                node_type="governor",
                display_name=f"Airlock {direction.title()}",
                side="host",
                status="FAIL" if blocked else "OK",
                reason_code=row.get("reason_code"),
                latency_ms=(summary.get("node_timings_ms") or {}).get("airlock_quarantine") if idx == 1 else None,
                input_data={"direction": direction},
                output_data={
                    "blocked": blocked,
                    "detail": row.get("detail"),
                    "ingress_classification": row.get("ingress_classification"),
                },
                ts_ms=row.get("ts_ms"),
                error=row.get("reason_code") if blocked else None,
            )
        )
    return nodes


def _build_bond_trace_nodes(bond_rows: List[Dict[str, Any]]) -> List[TraceNode]:
    nodes: List[TraceNode] = []
    for idx, row in enumerate(bond_rows, start=1):
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        update_kind = str(row.get("update_kind") or "").strip().upper() or "BOND"
        nodes.append(
            TraceNode(
                node_id=f"bond_update_{idx}",
                node_type="bond",
                display_name="Bond Update" if update_kind != "STAGE_TRANSITION" else "Bond Stage",
                side="host",
                status=_normalize_trace_status(row.get("status"), default="OK"),
                reason_code=row.get("reason_code"),
                input_data={
                    "update_kind": update_kind,
                    "stage": row.get("stage"),
                    "evidence_refs": row.get("evidence_refs"),
                },
                output_data={
                    "payload": payload_dict if payload_dict else payload,
                    "detail": row.get("detail"),
                },
                ts_ms=row.get("ts_ms"),
                error=row.get("reason_code"),
            )
        )
    return nodes


def _build_memory_trace_nodes(
    memory_rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
) -> List[TraceNode]:
    nodes: List[TraceNode] = []
    for idx, row in enumerate(memory_rows, start=1):
        nodes.append(
            TraceNode(
                node_id="episodic_store" if idx == len(memory_rows) else f"episodic_store_{idx}",
                node_type="memory",
                display_name="Episodic Store",
                side="host",
                status=_normalize_trace_status(row.get("status"), default="OK"),
                reason_code=row.get("reason_code"),
                latency_ms=(summary.get("node_timings_ms") or {}).get("episodic_store") if idx == len(memory_rows) else None,
                input_data={
                    "op_kind": row.get("op_kind"),
                    "evidence_refs": row.get("evidence_refs"),
                    "source_memory_ids": row.get("source_memory_ids"),
                },
                output_data={
                    "detail": row.get("detail"),
                    "output_text": summary.get("output_text"),
                },
                ts_ms=row.get("ts_ms"),
                error=row.get("reason_code"),
            )
        )
    return nodes


def _build_audit_snapshot_node(summary: Dict[str, Any]) -> Optional[TraceNode]:
    audit_snapshot = summary.get("audit_snapshot")
    if not isinstance(audit_snapshot, dict) or not audit_snapshot:
        return None
    return TraceNode(
        node_id="audit_snapshot",
        node_type="system",
        display_name="Audit Snapshot",
        side="host",
        status="FAIL" if summary.get("failure_event") else "OK",
        reason_code=summary.get("failure_event"),
        input_data={
            "task_type": summary.get("task_type"),
            "governor_passed": summary.get("governor_passed"),
        },
        output_data=audit_snapshot,
        ts_ms=audit_snapshot.get("ts_ms"),
        error=summary.get("failure_event"),
    )


def _sort_trace_nodes(nodes: List[TraceNode]) -> List[TraceNode]:
    sorted_items = sorted(
        enumerate(nodes),
        key=lambda item: (
            _trace_node_order_rank(item[1]),
            item[1].ts_ms is None,
            int(item[1].ts_ms or 0),
            item[0],
        ),
    )
    return [node for _, node in sorted_items]


def _trace_node_order_rank(node: TraceNode) -> int:
    """Phase XIV v4 节点排序权重。"""
    node_id = str(node.node_id or "")
    # Edge 入站事件（voice_event_log）
    if node_id.startswith("voice_"):
        event_type = str((node.input_data or {}).get("event_type") or "").upper()
        if "ACCEPTED" in event_type:
            return 0
        if "DISPATCHED" in event_type:
            return 5
        if "COMPLETED" in event_type or "TTS" in event_type:
            return 90
        return 1
    # Host 入口
    if node_id in ("host_input", "airlock_ingress"):
        return 10
    if node_id == "governor":
        return 20
    if node_id == "query_planner":
        return 25
    if node_id == "agentic_rag":
        return 28
    # 检索
    if node_id.startswith("retrieval_embedding"):
        return 30
    if node_id in ("recall_fusion",) or node_id.startswith("recall_fusion"):
        return 31
    if node_id.startswith("retrieval_evidence_pack"):
        return 33
    if node_id.startswith("retrieval_pipeline"):
        return 34
    if node_id == "emotion_context":
        return 35
    if node_id == "persona_resident_memory":
        return 36
    if node_id == "motive_state":
        return 37
    if node_id == "render_policy":
        return 38
    if node_id == "cortex_orchestrator":
        return 39
    if node_id == "executive_router":
        return 41
    if node_id == "surface_reply":
        return 43
    # Prompt + LLM
    if node_id == "prompt_builder":
        return 45
    if node_id in ("llm_stream", "llm_gateway", "cortex_orchestrator_llm_stream"):
        return 50
    if node_id.startswith("llm_"):
        return 51
    # 子 Agent
    if node_id.startswith("sub_agent"):
        return 42
    # 出口
    if node_id == "airlock_egress":
        return 60
    if node_id.startswith("airlock"):
        return 15
    if node_id == "pii_filter":
        return 62
    # 记忆 + Bond
    if node_id.startswith("episodic_store"):
        return 65
    if node_id.startswith("bond_update"):
        return 70
    if node_id == "audit_snapshot":
        return 80
    return 45


def _build_linear_trace_edges(nodes: List[TraceNode]) -> List[TraceEdge]:
    filtered = [node for node in nodes if str(node.node_id or "").strip()]
    return [
        TraceEdge(source=str(prev.node_id), target=str(curr.node_id))
        for prev, curr in zip(filtered, filtered[1:])
    ]

def _compute_node_health(rows: List[Dict], window: int = 20) -> Dict[str, Any]:
    """根据最近 window 条调用记录计算节点健康状态。"""
    if not rows:
        return {"status": "UNKNOWN", "success_rate": None, "p95_ms": None, "last_call_ts": None}
    recent = rows[-window:]
    total = len(recent)
    ok_count = sum(1 for r in recent if str(r.get("status", "")).upper() == "OK")
    success_rate = round(ok_count / total * 100, 1) if total > 0 else 0.0
    latencies = [r["latency_ms"] for r in recent if r.get("latency_ms") is not None]
    p95_ms = None
    if latencies:
        latencies.sort()
        idx = max(0, int(len(latencies) * 0.95) - 1)
        p95_ms = latencies[idx]
    last_call_ts = max((r["ts_ms"] for r in recent if r.get("ts_ms")), default=None)
    if success_rate >= 80:
        status = "UP"
    elif success_rate >= 50:
        status = "DEGRADED"
    else:
        status = "DOWN"
    return {
        "status": status,
        "success_rate": success_rate,
        "p95_ms": p95_ms,
        "last_call_ts": last_call_ts,
        "call_count": total,
    }

_LEGACY_INPUT_DISPATCH_EVENTS: frozenset[str] = frozenset(
    {
        "ACCEPTED",
        "DISPATCHED",
        "COMPLETED",
        "FAILED",
        "NOT_WIRED",
        "ASR_ACCEPTED",
        "ASR_COMPLETED",
        "ASR_FAILED",
        "WECHAT_ACCEPTED",
        "WECHAT_COMPLETED",
        "WECHAT_FAILED",
    }
)


def _is_input_dispatch_event(row: dict[str, Any]) -> bool:
    event_type = str(row.get("event_type") or row.get("status") or "").strip().upper()
    return bool(event_type) and (
        event_type.startswith("INPUT_") or event_type in _LEGACY_INPUT_DISPATCH_EVENTS
    )


def _normalize_input_dispatch_source(raw_source: Any, event_type: str) -> str:
    src = str(raw_source or "").strip().upper()
    evt = str(event_type or "").strip().upper()
    if "ASR" in src or "ASR" in evt:
        return "ASR"
    if "GATEWAY" in src or "GATEWAY" in evt:
        return "GATEWAY"
    if "WECHAT" in src or "WECHAT" in evt or "WX" in src:
        return "WECHAT"
    if "QQ" in src or "QQ" in evt:
        return "QQ"
    return src or "UNKNOWN"


def _extract_input_dispatch_latency_ms(row: dict[str, Any], payload: dict[str, Any]) -> Optional[int]:
    for candidate in (payload.get("latency_ms"), row.get("latency_ms")):
        if candidate is None or candidate == "":
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None

def _model_dump_compat(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _format_sse_event(event: str, payload: Dict[str, Any], *, retry_ms: Optional[int] = None) -> str:
    lines: List[str] = []
    if retry_ms is not None:
        lines.append(f"retry: {int(retry_ms)}")
    lines.append(f"event: {event}")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    for line in data.splitlines() or ["{}"]:
        lines.append(f"data: {line}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _build_sessions_response(
    ledger: SQLiteLedger | None,
    *,
    page: int,
    page_size: int,
    status: Optional[str],
    window_minutes: int,
    keyword: Optional[str],
) -> SessionsResponse:
    if ledger is None:
        from host_brain.app.dependencies import get_query_projection_store

        svc = SessionQueryService(None, projection_store=get_query_projection_store())
    else:
        svc = SessionQueryService(ledger)
    since_ms = _now_ms() - window_minutes * 60 * 1000
    result = svc.query(
        page=page,
        page_size=page_size,
        status=status,
        keyword=keyword,
        start_time=since_ms,
        end_time=_now_ms(),
    )

    items = [
        SessionItem(
            request_id=r["request_id"],
            session_id=r.get("session_id"),
            ts_ms=r.get("ts_ms", 0),
            status=r.get("status", "error").upper(),
            decision=r.get("decision"),
            reason_code=r.get("reason_code"),
            total_latency_ms=r.get("duration_ms"),
            input_preview=r.get("input_preview"),
        )
        for r in result.get("items", [])
    ]

    return SessionsResponse(
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        items=items,
        timestamp=_now_ms(),
        projection=result.get("projection"),
    )


def _mark_trace_summary_degraded(
    summary: Dict[str, Any],
    *,
    reason: str,
    message: str,
) -> Dict[str, Any]:
    payload = dict(summary or {})
    payload["degraded"] = True
    payload["degraded_reason"] = reason
    payload["degraded_message"] = message
    payload["degraded_at"] = _now_ms()
    return payload


def _load_optional_trace_payload(
    request_id: str,
    stage_name: str,
    builder,
    *,
    default_value,
):
    timeout_sec = _TRACE_OPTIONAL_STAGE_TIMEOUT_SEC
    if timeout_sec <= 0:
        try:
            return builder(request_id)
        except Exception as exc:
            logger.debug("trace optional payload degraded: request_id=%s stage=%s err=%s", request_id, stage_name, exc)
            return default_value

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"trace-{stage_name}")
    future = executor.submit(builder, request_id)
    try:
        return future.result(timeout=timeout_sec)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "trace optional payload timeout: request_id=%s stage=%s timeout_sec=%.2f",
            request_id,
            stage_name,
            timeout_sec,
        )
        future.cancel()
        return default_value
    except Exception as exc:
        logger.debug("trace optional payload degraded: request_id=%s stage=%s err=%s", request_id, stage_name, exc)
        return default_value
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _log_trace_build_cost(
    request_id: str,
    *,
    mode: str,
    total_ms: int,
    fetch_ms: int,
    summary_ms: int,
    optional_ms: int,
    compose_ms: int,
    node_count: int,
) -> None:
    msg = (
        "trace build mode=%s request_id=%s total_ms=%d fetch_ms=%d summary_ms=%d optional_ms=%d compose_ms=%d node_count=%d"
    )
    args = (mode, request_id, total_ms, fetch_ms, summary_ms, optional_ms, compose_ms, node_count)
    if total_ms >= _TRACE_BUILD_SLOW_LOG_MS:
        logger.info(msg, *args)
    else:
        logger.debug(msg, *args)


def _build_trace_response_lightweight(
    ledger: SQLiteLedger,
    request_id: str,
    *,
    reason: str,
    message: str,
) -> TraceResponse:
    trace_rows = _fetch_request_rows(ledger, "graph_run_trace", request_id)
    intent_rows = _fetch_request_rows(ledger, "intent_log", request_id)
    governor_rows = _fetch_request_rows(ledger, "governor_log", request_id)
    voice_rows = _fetch_request_rows(ledger, "voice_event_log", request_id)

    if not trace_rows and not governor_rows and not intent_rows and not voice_rows:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "request_id": request_id},
        )

    summary = _build_trace_summary(
        request_id=request_id,
        intent_rows=intent_rows,
        governor_rows=governor_rows,
        retrieval_rows=[],
        llm_rows=[],
        airlock_rows=[],
        bond_rows=[],
        memory_rows=[],
        error_rows=[],
        voice_rows=voice_rows,
    )
    summary = _mark_trace_summary_degraded(summary, reason=reason, message=message)

    if trace_rows:
        row_dict = trace_rows[-1]
        nodes_raw = _safe_json(row_dict.get("nodes_json")) or []
        edges_raw = _safe_json(row_dict.get("edges_json")) or []

        nodes: List[TraceNode] = []
        if isinstance(nodes_raw, list):
            for node_row in nodes_raw:
                if not isinstance(node_row, dict):
                    continue
                output_data = node_row.get("output_data")
                input_data = node_row.get("input_data")
                nodes.append(
                    TraceNode(
                        node_id=node_row.get("node_id", ""),
                        node_type=node_row.get("node_type", "system"),
                        display_name=node_row.get("display_name", node_row.get("node_id", "")),
                        side=node_row.get("side"),
                        provider=node_row.get("provider"),
                        model_id=node_row.get("model_id"),
                        status=_normalize_trace_node_status(
                            node_row.get("status", "OK"),
                            node_type=node_row.get("node_type", "system"),
                            reason_code=node_row.get("reason_code"),
                            output_data=output_data,
                            input_data=input_data,
                        ),
                        reason_code=node_row.get("reason_code"),
                        latency_ms=node_row.get("latency_ms"),
                        input_data=input_data,
                        output_data=output_data,
                        ts_ms=node_row.get("ts_ms"),
                        error=node_row.get("error"),
                        group_id=node_row.get("group_id"),
                        owner_node_id=node_row.get("owner_node_id"),
                        subgraph_id=node_row.get("subgraph_id"),
                        parent_node_id=node_row.get("parent_node_id"),
                    )
                )

        supplement_nodes: List[TraceNode] = []
        intent_node = _build_intent_trace_node(intent_rows[-1] if intent_rows else {})
        if intent_node is not None:
            supplement_nodes.append(intent_node)
        governor_node = _build_governor_trace_node(governor_rows[-1] if governor_rows else {}, summary)
        if governor_node is not None:
            supplement_nodes.append(governor_node)
        supplement_nodes = _append_missing_trace_nodes(nodes, supplement_nodes)

        edges = [
            TraceEdge(source=edge.get("source", ""), target=edge.get("target", ""))
            for edge in edges_raw
            if isinstance(edge, dict)
        ] if isinstance(edges_raw, list) else []
        edges = _build_trace_edges(nodes, edges, supplement_nodes)
        nodes.extend(supplement_nodes)

        voice_nodes = _build_voice_trace_nodes(voice_rows)
        edges = _build_trace_edges(nodes, edges, voice_nodes)
        nodes.extend(voice_nodes)
        nodes = _enrich_trace_nodes(nodes)
        trace_status = _coalesce_trace_status(str(row_dict.get("status", "OK")), nodes)

        return TraceResponse(
            request_id=request_id,
            session_id=_first_present(row_dict.get("session_id"), summary.get("session_id")),
            status=trace_status,
            total_latency_ms=_first_present(row_dict.get("total_latency_ms"), summary.get("total_latency_ms")),
            node_count=len(nodes),
            nodes=nodes,
            edges=edges,
            summary=summary,
            subgraphs=[],
            stream_events=[],
            ts_ms=row_dict.get("ts_ms", _now_ms()),
            timestamp=_now_ms(),
        )

    synthesized_nodes = _build_voice_trace_nodes(voice_rows)
    intent_node = _build_intent_trace_node(intent_rows[-1] if intent_rows else {})
    if intent_node is not None:
        synthesized_nodes.append(intent_node)
    governor_node = _build_governor_trace_node(governor_rows[-1] if governor_rows else {}, summary)
    if governor_node is not None:
        synthesized_nodes.append(governor_node)

    nodes = _enrich_trace_nodes(_sort_trace_nodes(synthesized_nodes))
    edges = _build_linear_trace_edges(nodes)
    decision = str(summary.get("decision") or "").strip().upper()
    base_status = "FAIL" if decision and decision != "APPROVE" else "OK"
    trace_status = _coalesce_trace_status(base_status, nodes)
    trace_ts = next((node.ts_ms for node in nodes if node.ts_ms is not None), _now_ms())
    return TraceResponse(
        request_id=request_id,
        session_id=summary.get("session_id"),
        status=trace_status,
        total_latency_ms=summary.get("total_latency_ms"),
        node_count=len(nodes),
        nodes=nodes,
        edges=edges,
        summary=summary,
        subgraphs=[],
        stream_events=[],
        ts_ms=trace_ts,
        timestamp=_now_ms(),
    )


def _build_trace_response(ledger: SQLiteLedger, request_id: str) -> TraceResponse:
    started_at = time.perf_counter()
    trace_rows = _fetch_request_rows(ledger, "graph_run_trace", request_id)
    intent_rows = _fetch_request_rows(ledger, "intent_log", request_id)
    governor_rows = _fetch_request_rows(ledger, "governor_log", request_id)
    retrieval_rows = _fetch_request_rows(ledger, "retrieval_event_log", request_id)
    llm_rows = _fetch_request_rows(ledger, "llm_node_call_log", request_id)
    airlock_rows = _fetch_request_rows(ledger, "airlock_log", request_id)
    bond_rows = _fetch_request_rows(ledger, "bond_update_log", request_id)
    memory_rows = _fetch_request_rows(ledger, "memory_ops_log", request_id)
    error_rows = _fetch_request_rows(ledger, "error_log", request_id)
    voice_rows = _fetch_request_rows(ledger, "voice_event_log", request_id)
    fetch_ms = int((time.perf_counter() - started_at) * 1000)

    if not trace_rows and not governor_rows and not intent_rows and not voice_rows:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "request_id": request_id},
        )

    summary = _build_trace_summary(
        request_id=request_id,
        intent_rows=intent_rows,
        governor_rows=governor_rows,
        retrieval_rows=retrieval_rows,
        llm_rows=llm_rows,
        airlock_rows=airlock_rows,
        bond_rows=bond_rows,
        memory_rows=memory_rows,
        error_rows=error_rows,
        voice_rows=voice_rows,
    )
    summary_ms = int((time.perf_counter() - started_at) * 1000) - fetch_ms
    optional_started = time.perf_counter()
    subgraphs = _load_optional_trace_payload(
        request_id,
        "subgraphs",
        _build_trace_subgraphs,
        default_value=[],
    )
    stream_events = _load_optional_trace_payload(
        request_id,
        "stream-events",
        _build_trace_stream_events,
        default_value=[],
    )
    optional_ms = int((time.perf_counter() - optional_started) * 1000)

    if trace_rows:
        compose_started = time.perf_counter()
        row_dict = trace_rows[-1]
        nodes_raw = _safe_json(row_dict.get("nodes_json")) or []
        edges_raw = _safe_json(row_dict.get("edges_json")) or []
        if not summary.get("output_text"):
            trace_output_text = _extract_output_from_trace_nodes_raw(nodes_raw if isinstance(nodes_raw, list) else [])
            if trace_output_text:
                summary["output_text"] = trace_output_text
        if not summary.get("recall"):
            trace_recall = _extract_recall_summary_from_trace_nodes_raw(nodes_raw if isinstance(nodes_raw, list) else [])
            if trace_recall:
                summary["recall"] = trace_recall
        if isinstance(nodes_raw, list):
            subgraphs = _merge_trace_subgraphs(
                subgraphs,
                _build_trace_subgraphs_from_nodes_raw(nodes_raw),
            )

        nodes = []
        for node_row in nodes_raw:
            output_data = node_row.get("output_data")
            input_data = node_row.get("input_data")
            nodes.append(TraceNode(
                node_id=node_row.get("node_id", ""),
                node_type=node_row.get("node_type", "system"),
                display_name=node_row.get("display_name", node_row.get("node_id", "")),
                side=node_row.get("side"),
                provider=node_row.get("provider"),
                model_id=node_row.get("model_id"),
                status=_normalize_trace_node_status(
                    node_row.get("status", "OK"),
                    node_type=node_row.get("node_type", "system"),
                    reason_code=node_row.get("reason_code"),
                    output_data=output_data,
                    input_data=input_data,
                ),
                reason_code=node_row.get("reason_code"),
                latency_ms=node_row.get("latency_ms"),
                input_data=input_data,
                output_data=output_data,
                ts_ms=node_row.get("ts_ms"),
                error=node_row.get("error"),
                group_id=node_row.get("group_id"),
                owner_node_id=node_row.get("owner_node_id"),
                subgraph_id=node_row.get("subgraph_id"),
                parent_node_id=node_row.get("parent_node_id"),
            ))

        supplement_nodes: List[TraceNode] = []
        intent_node = _build_intent_trace_node(intent_rows[-1] if intent_rows else {})
        if intent_node is not None:
            supplement_nodes.append(intent_node)
        governor_node = _build_governor_trace_node(governor_rows[-1] if governor_rows else {}, summary)
        if governor_node is not None:
            supplement_nodes.append(governor_node)
        supplement_nodes.extend(_build_airlock_trace_nodes(airlock_rows, summary))
        supplement_nodes.extend(_build_retrieval_trace_nodes(retrieval_rows, summary))
        supplement_nodes.extend(_build_persona_trace_nodes(summary))
        supplement_nodes.extend(_build_orchestration_trace_nodes(summary, subgraphs))
        supplement_nodes.extend(_build_llm_trace_nodes(llm_rows, summary))
        supplement_nodes.extend(_build_bond_trace_nodes(bond_rows))
        supplement_nodes.extend(_build_memory_trace_nodes(memory_rows, summary))
        audit_snapshot_node = _build_audit_snapshot_node(summary)
        if audit_snapshot_node is not None:
            supplement_nodes.append(audit_snapshot_node)
        supplement_nodes = _append_missing_trace_nodes(nodes, supplement_nodes)

        edges = [TraceEdge(source=edge.get("source", ""), target=edge.get("target", "")) for edge in edges_raw]
        edges = _build_trace_edges(nodes, edges, supplement_nodes)
        nodes.extend(supplement_nodes)
        voice_nodes = _build_voice_trace_nodes(voice_rows)
        edges = _build_trace_edges(nodes, edges, voice_nodes)
        nodes.extend(voice_nodes)
        nodes = _enrich_trace_nodes(nodes)
        trace_status = _coalesce_trace_status(str(row_dict.get("status", "OK")), nodes)
        compose_ms = int((time.perf_counter() - compose_started) * 1000)
        total_ms = int((time.perf_counter() - started_at) * 1000)
        _log_trace_build_cost(
            request_id,
            mode="full",
            total_ms=total_ms,
            fetch_ms=fetch_ms,
            summary_ms=summary_ms,
            optional_ms=optional_ms,
            compose_ms=compose_ms,
            node_count=len(nodes),
        )

        return TraceResponse(
            request_id=request_id,
            session_id=_first_present(row_dict.get("session_id"), summary.get("session_id")),
            status=trace_status,
            total_latency_ms=_first_present(row_dict.get("total_latency_ms"), summary.get("total_latency_ms")),
            node_count=len(nodes),
            nodes=nodes,
            edges=edges,
            summary=summary,
            subgraphs=subgraphs,
            stream_events=stream_events,
            ts_ms=row_dict.get("ts_ms", _now_ms()),
            timestamp=_now_ms(),
        )

    synthesized_nodes = _build_voice_trace_nodes(voice_rows)
    intent_node = _build_intent_trace_node(intent_rows[-1] if intent_rows else {})
    if intent_node is not None:
        synthesized_nodes.append(intent_node)
    governor_node = _build_governor_trace_node(governor_rows[-1] if governor_rows else {}, summary)
    if governor_node is not None:
        synthesized_nodes.append(governor_node)
    synthesized_nodes.extend(_build_airlock_trace_nodes(airlock_rows, summary))
    synthesized_nodes.extend(_build_retrieval_trace_nodes(retrieval_rows, summary))
    synthesized_nodes.extend(_build_persona_trace_nodes(summary))
    synthesized_nodes.extend(_build_orchestration_trace_nodes(summary, subgraphs))
    synthesized_nodes.extend(_build_llm_trace_nodes(llm_rows, summary))
    synthesized_nodes.extend(_build_bond_trace_nodes(bond_rows))
    synthesized_nodes.extend(_build_memory_trace_nodes(memory_rows, summary))
    audit_snapshot_node = _build_audit_snapshot_node(summary)
    if audit_snapshot_node is not None:
        synthesized_nodes.append(audit_snapshot_node)

    nodes = _enrich_trace_nodes(_sort_trace_nodes(synthesized_nodes))
    edges = _build_linear_trace_edges(nodes)
    decision = str(summary.get("decision") or "").strip().upper()
    base_status = "FAIL" if decision and decision != "APPROVE" else "OK"
    trace_status = _coalesce_trace_status(base_status, nodes)
    trace_ts = next((node.ts_ms for node in nodes if node.ts_ms is not None), _now_ms())
    compose_ms = int((time.perf_counter() - started_at) * 1000) - fetch_ms - summary_ms - optional_ms
    total_ms = int((time.perf_counter() - started_at) * 1000)
    _log_trace_build_cost(
        request_id,
        mode="fallback",
        total_ms=total_ms,
        fetch_ms=fetch_ms,
        summary_ms=summary_ms,
        optional_ms=optional_ms,
        compose_ms=max(compose_ms, 0),
        node_count=len(nodes),
    )

    return TraceResponse(
        request_id=request_id,
        session_id=summary.get("session_id"),
        status=trace_status,
        total_latency_ms=summary.get("total_latency_ms"),
        node_count=len(nodes),
        nodes=nodes,
        edges=edges,
        summary=summary,
        subgraphs=subgraphs,
        stream_events=stream_events,
        ts_ms=trace_ts,
        timestamp=_now_ms(),
    )


def _dedup_sessions_by_content(sessions: List[SessionItem]) -> List[SessionItem]:
    """同 session_id + 相同 input_preview 的多条请求只保留最新的一条，抑制 ASR 重复识别噪声。

    去重规则：
    1. session_id 为空的请求不参与去重（保留全部）
    2. 同一 session_id 下，input_preview 完全相同的请求视为噪声重复，只保留 ts_ms 最大的
    3. input_preview 为空时以空字符串作为 key（同 session_id 的空预览请求也会被合并）
    """
    # dedup_key = (session_id, input_preview_normalized) -> 最新的 SessionItem
    best: Dict[str, SessionItem] = {}
    no_session: List[SessionItem] = []

    for session in sessions:
        sid = (session.session_id or "").strip()
        if not sid:
            no_session.append(session)
            continue
        preview = (session.input_preview or "").strip().casefold()
        key = f"{sid}::{preview}"
        existing = best.get(key)
        if existing is None or session.ts_ms > existing.ts_ms:
            best[key] = session

    # 保持原始时间倒序
    deduped = sorted(best.values(), key=lambda s: s.ts_ms, reverse=True)
    # 无 session_id 的放在最前面（通常是手动触发的请求）
    return no_session + deduped


def _is_placeholder_session_preview(value: Optional[str]) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return False
    return all(ch in {"?", "？"} for ch in normalized)


def _is_debug_session_id(session_id: Optional[str]) -> bool:
    normalized = str(session_id or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith(_DEBUG_SESSION_PREFIXES)


def _normalize_session_preview(value: Optional[str]) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _sanitize_session_items_for_export(
    sessions: List[SessionItem],
    *,
    keyword: Optional[str] = None,
) -> List[SessionItem]:
    keyword_active = bool(str(keyword or "").strip())
    filtered: List[SessionItem] = []
    for session in sessions:
        preview_key = _normalize_session_preview(session.input_preview)
        if _is_placeholder_session_preview(session.input_preview):
            continue
        if not keyword_active and not preview_key:
            continue
        if not keyword_active and _is_debug_session_id(session.session_id):
            continue
        filtered.append(session)

    best: Dict[str, SessionItem] = {}
    passthrough: List[SessionItem] = []
    for session in filtered:
        session_key = str(session.session_id or "").strip().lower()
        preview_key = _normalize_session_preview(session.input_preview)
        if not session_key or not preview_key:
            passthrough.append(session)
            continue
        dedupe_key = f"{session_key}::{preview_key}"
        previous = best.get(dedupe_key)
        if previous is None or session.ts_ms > previous.ts_ms:
            best[dedupe_key] = session

    sanitized = passthrough + sorted(best.values(), key=lambda s: s.ts_ms, reverse=True)
    sanitized.sort(key=lambda s: s.ts_ms, reverse=True)
    return sanitized


def _build_trace_live_feed_snapshot(
    ledger: SQLiteLedger,
    *,
    limit: int,
    window_minutes: int,
    keyword: Optional[str],
    active_within_ms: int,
) -> TraceLiveFeedSnapshot:
    # 多拉一些候选（去重后可能缩减），确保去重后仍有足够条目
    fetch_size = min(limit * 3, 36)
    sessions_response = _build_sessions_response(
        ledger,
        page=1,
        page_size=fetch_size,
        status=None,
        window_minutes=window_minutes,
        keyword=keyword,
    )

    # 噪声去重：同 session_id + 相同 input_preview 只保留最新一条
    deduped_sessions = _dedup_sessions_by_content(sessions_response.items)[:limit]

    active_cutoff_ms = _now_ms() - max(0, int(active_within_ms))
    ranked_items: List[tuple[int, LiveTraceFeedItem]] = []
    for session in deduped_sessions:
        try:
            trace = _build_trace_response(ledger, session.request_id)
            item = LiveTraceFeedItem(session=session, trace=trace)
            last_active_ts = _resolve_trace_live_feed_last_active_ts(item)
            if keyword is None and last_active_ts < active_cutoff_ms:
                continue
            ranked_items.append((last_active_ts, item))
        except HTTPException as exc:
            if exc.status_code == 404:
                item = LiveTraceFeedItem(session=session, trace=None, error="not_found")
                last_active_ts = _resolve_trace_live_feed_last_active_ts(item)
                if keyword is None and last_active_ts < active_cutoff_ms:
                    continue
                ranked_items.append((last_active_ts, item))
                continue
            raise

    ranked_items.sort(
        key=lambda pair: (
            int(pair[0] or 0),
            str(pair[1].session.request_id or ""),
        ),
        reverse=True,
    )
    items = [item for _, item in ranked_items[:limit]]

    return TraceLiveFeedSnapshot(
        items=items,
        count=len(items),
        timestamp=_now_ms(),
    )


def _resolve_trace_live_feed_last_active_ts(item: LiveTraceFeedItem) -> int:
    timestamps: List[int] = []
    session = item.session
    if session and session.ts_ms is not None:
        try:
            timestamps.append(int(session.ts_ms))
        except (TypeError, ValueError):
            pass
    trace = item.trace
    if trace is not None:
        if trace.ts_ms is not None:
            try:
                timestamps.append(int(trace.ts_ms))
            except (TypeError, ValueError):
                pass
        for node in trace.nodes or []:
            if node.ts_ms is None:
                continue
            try:
                timestamps.append(int(node.ts_ms))
            except (TypeError, ValueError):
                continue
    return max(timestamps) if timestamps else 0


def _trace_live_feed_signature(snapshot: TraceLiveFeedSnapshot) -> str:
    signature_rows: List[Dict[str, Any]] = []
    for item in snapshot.items:
        trace = item.trace
        latest_node = None
        if trace and trace.nodes:
            latest = max(
                trace.nodes,
                key=lambda node: (
                    int(node.ts_ms or 0),
                    str(node.node_id or ""),
                    str(node.status or ""),
                ),
            )
            latest_node = {
                "node_id": latest.node_id,
                "status": latest.status,
                "ts_ms": latest.ts_ms,
                "reason_code": latest.reason_code,
            }
        summary = trace.summary if trace and isinstance(trace.summary, dict) else {}
        signature_rows.append({
            "request_id": item.session.request_id,
            "session_ts_ms": item.session.ts_ms,
            "session_status": item.session.status,
            "trace_status": trace.status if trace else None,
            "trace_ts_ms": trace.ts_ms if trace else None,
            "trace_node_count": trace.node_count if trace else None,
            "trace_total_latency_ms": trace.total_latency_ms if trace else None,
            "trace_nodes": [_trace_node_signature(node) for node in trace.nodes] if trace else [],
            "output_text": summary.get("output_text"),
            "failure_event": summary.get("failure_event"),
            "latest_node": latest_node,
            "error": item.error,
        })
    return json.dumps(signature_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _trace_node_signature(node: TraceNode) -> str:
    payload = _model_dump_compat(node)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _build_trace_live_feed_delta(
    previous: Optional[TraceLiveFeedSnapshot],
    current: TraceLiveFeedSnapshot,
) -> Optional[TraceLiveFeedDelta]:
    if previous is None:
        return None

    previous_items = {
        item.session.request_id: item
        for item in previous.items
        if item.session and item.session.request_id
    }
    delta_items: List[TraceLiveFeedDeltaItem] = []

    for item in current.items:
        request_id = item.session.request_id if item.session else None
        if not request_id or not item.trace:
            continue

        previous_item = previous_items.get(request_id)
        previous_trace = previous_item.trace if previous_item else None
        previous_nodes = {
            node.node_id: node
            for node in (previous_trace.nodes if previous_trace else [])
            if node.node_id
        }
        previous_signatures = {
            node_id: _trace_node_signature(node)
            for node_id, node in previous_nodes.items()
        }

        node_changes: List[TraceLiveFeedNodeDelta] = []
        for node in item.trace.nodes:
            node_id = str(node.node_id or "").strip()
            if not node_id:
                continue
            previous_node = previous_nodes.get(node_id)
            if previous_node is None:
                node_changes.append(
                    TraceLiveFeedNodeDelta(
                        change_type="added",
                        node=node,
                    )
                )
                continue
            if previous_signatures.get(node_id) != _trace_node_signature(node):
                node_changes.append(
                    TraceLiveFeedNodeDelta(
                        change_type="updated",
                        node=node,
                        previous_status=previous_node.status,
                    )
                )

        if not node_changes:
            continue

        latest_change = max(
            node_changes,
            key=lambda change: (
                int(change.node.ts_ms or 0),
                str(change.node.node_id or ""),
            ),
        )
        delta_items.append(
            TraceLiveFeedDeltaItem(
                request_id=request_id,
                session_id=_first_present(item.trace.session_id, item.session.session_id),
                session=item.session,
                trace_status=item.trace.status,
                total_latency_ms=item.trace.total_latency_ms,
                latest_node=latest_change.node,
                changes=node_changes,
                change_count=len(node_changes),
            )
        )

    if not delta_items:
        return None

    delta_items.sort(
        key=lambda item: (
            int(item.latest_node.ts_ms if item.latest_node else 0),
            str(item.request_id or ""),
        ),
        reverse=True,
    )
    return TraceLiveFeedDelta(
        items=delta_items,
        count=len(delta_items),
        timestamp=_now_ms(),
    )

# ---------------------------------------------------------------------------
# GET /obs/services/health
# ---------------------------------------------------------------------------
async def get_services_health(
    window_minutes: int = Query(60, ge=1, le=1440, description="统计窗口（分钟）"),
    side: Optional[str] = Query(None, description="过滤端：host|edge"),
):
    """WARN-BE-001: 调用 ServiceHealthAggregator.aggregate() 而非内联聚合逻辑。"""
    ledger = _get_ledger()
    since_ms = _now_ms() - window_minutes * 60 * 1000
    try:
        from host_brain.observability import service_health_heartbeat as heartbeat

        if not heartbeat.has_fresh_core_service_cache():
            await heartbeat.prime_core_service_cache_once()
    except Exception:
        pass

    # 使用 ledger 数据构建探测函数注册表（被动监控）
    probe_registry, extra_data = await asyncio.to_thread(_build_probe_registry, ledger, since_ms)
    aggregator = ServiceHealthAggregator(probe_registry=probe_registry)
    result = await aggregator.aggregate(side_filter=side)

    # 将 ServiceHealthAggregator 返回的通用结构映射到 ServicesHealthResponse
    services = []
    for svc in result.get("services", []):
        sid = svc["service_id"]
        extra = extra_data.get(sid, {})
        services.append(ServiceHealthItem(
            service_id=sid,
            service_type=svc.get("side", "edge"),
            display_name=svc.get("name", sid),
            role=extra.get("role"),
            provider=extra.get("provider"),
            model_id=extra.get("model_id"),
            status=svc["status"],
            success_rate=extra.get("success_rate"),
            p95_ms=svc.get("latency_ms"),
            last_call_ts=svc.get("last_check_ts"),
            call_count=extra.get("call_count"),
        ))

    return ServicesHealthResponse(
        total=result["total"],
        up=result["up"],
        degraded=result["degraded"],
        down=result["down"],
        unknown=result["unknown"],
        services=services,
        timestamp=_now_ms(),
    )


def _build_probe_registry(ledger, since_ms: int):
    """基于 ledger 历史数据构建被动探测函数注册表，同时返回 extra_data 字典（含 success_rate/call_count 等）。
    若当前窗口内无数据，则用 7 日窗口回退，避免所有服务显示 UNKNOWN（探测不到服务码）。
    优先使用心跳缓存（service_health_heartbeat）：若某服务在 TTL 内有心跳结果，则直接返回，不再依赖 ledger。
    """
    from host_brain.observability.service_health_aggregator import ServiceHealthResult, ALL_SERVICES as _AGGREGATOR_ALL_SERVICES
    from host_brain.observability import service_health_heartbeat as heartbeat

    now_ms = _now_ms()
    host_services_for_aggregator = [s for s in _AGGREGATOR_ALL_SERVICES if s.get("side") == "host"]
    # 预加载当前窗口数据
    try:
        voice_rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now_ms)
    except Exception:
        voice_rows = []
    try:
        llm_rows = ledger.fetch_by_time_range("llm_node_call_log", start_ms=since_ms, end_ms=now_ms)
    except Exception:
        llm_rows = []

    # 若当前窗口无任何记录，用 7 日窗口回退，以便至少能显示历史状态
    fallback_since_ms = now_ms - 7 * 24 * 60 * 60 * 1000
    if (not voice_rows and not llm_rows) and since_ms > fallback_since_ms:
        try:
            voice_rows = ledger.fetch_by_time_range(
                "voice_event_log", start_ms=fallback_since_ms, end_ms=now_ms, limit=5000
            )
        except Exception:
            pass
        try:
            llm_rows = ledger.fetch_by_time_range(
                "llm_node_call_log", start_ms=fallback_since_ms, end_ms=now_ms, limit=5000
            )
        except Exception:
            pass

    registry = {}
    extra_data = {}  # service_id -> {success_rate, call_count, role, provider, model_id}

    # 边缘服务探测：优先用心跳缓存，否则用 ledger
    for sid, prefixes in _EDGE_SERVICE_EVENT_PREFIXES.items():
        cached = heartbeat.get_cached(sid)
        if cached:
            cap = (
                str(cached.get("status") or "UNKNOWN").strip().upper(),
                cached.get("latency_ms"),
                cached.get("last_check_ts"),
                sid,
                cached.get("name") or sid.upper().replace("_", " "),
            )

            async def _make_edge_probe_cached(_sid_ignored=None, c=cap):
                st, p95, last_ts, sid_val, dname = c
                try:
                    p95_int = int(p95) if p95 is not None else None
                except (TypeError, ValueError):
                    p95_int = None
                try:
                    last_int = int(last_ts) if last_ts is not None else None
                except (TypeError, ValueError):
                    last_int = None
                return ServiceHealthResult(sid_val, dname, "edge", st, latency_ms=p95_int, last_check_ts=last_int)

            registry[sid] = _make_edge_probe_cached
            extra_data[sid] = {"success_rate": None, "call_count": 0}
            continue
        svc_rows = [
            r for r in voice_rows
            if any(str(r.get("event_type", "")).upper().startswith(p) for p in prefixes)
        ]
        health = _compute_node_health(svc_rows)
        _status = health["status"]
        _p95 = health["p95_ms"]
        _last = health["last_call_ts"]
        display_name = sid.upper().replace("_", " ") if sid == "edge_rpc" else sid.upper()

        async def _make_edge_probe(_sid_ignored=None, s=_status, p=_p95, l=_last, _sid=sid, _dname=display_name):
            return ServiceHealthResult(_sid, _dname, "edge", s, latency_ms=p, last_check_ts=l)

        registry[sid] = _make_edge_probe
        extra_data[sid] = {
            "success_rate": health["success_rate"],
            "call_count":   health.get("call_count", 0),
        }

    # Host 侧 15 服务（llm_gateway, neo4j, ...）：优先用心跳缓存，否则无 probe → aggregator 返回 UNKNOWN
    for svc in host_services_for_aggregator:
        sid = svc["service_id"]
        if sid in registry:
            continue
        cached = heartbeat.get_cached(sid)
        if cached:
            cap = (
                str(cached.get("status") or "UNKNOWN").strip().upper(),
                cached.get("latency_ms"),
                cached.get("last_check_ts"),
                sid,
                cached.get("name") or svc.get("name") or sid,
            )

            async def _make_host_probe_cached(_sid_ignored=None, c=cap):
                st, p95, last_ts, sid_val, dname = c
                try:
                    p95_int = int(p95) if p95 is not None else None
                except (TypeError, ValueError):
                    p95_int = None
                try:
                    last_int = int(last_ts) if last_ts is not None else None
                except (TypeError, ValueError):
                    last_int = None
                return ServiceHealthResult(sid_val, dname, "host", st, latency_ms=p95_int, last_check_ts=last_int)

            registry[sid] = _make_host_probe_cached
            extra_data[sid] = {"success_rate": None, "call_count": 0}

    # LLM 节点探测：优先用心跳缓存（Host 整体），否则用 ledger
    for node_id, meta in _LLM_NODE_REGISTRY.items():
        cached = heartbeat.get_cached(node_id)
        if cached:
            cap = (
                str(cached.get("status") or "UNKNOWN").strip().upper(),
                cached.get("latency_ms"),
                cached.get("last_check_ts"),
                node_id,
                meta,
            )

            async def _make_llm_probe_cached(_sid_ignored=None, c=cap):
                st, p95, last_ts, nid, m = c
                try:
                    p95_int = int(p95) if p95 is not None else None
                except (TypeError, ValueError):
                    p95_int = None
                try:
                    last_int = int(last_ts) if last_ts is not None else None
                except (TypeError, ValueError):
                    last_int = None
                return ServiceHealthResult(nid, m["role"], "host", st, latency_ms=p95_int, last_check_ts=last_int)

            registry[node_id] = _make_llm_probe_cached
            extra_data[node_id] = {
                "role": meta["role"],
                "provider": meta["provider"],
                "model_id": meta["model_id"],
                "success_rate": None,
                "call_count": 0,
            }
            continue
        node_rows = [r for r in llm_rows if r.get("node_id") == node_id]
        health = _compute_node_health(node_rows)
        _status = health["status"]
        _p95 = health["p95_ms"]
        _last = health["last_call_ts"]

        async def _make_llm_probe(_sid_ignored=None, s=_status, p=_p95, l=_last, nid=node_id, m=meta):
            return ServiceHealthResult(nid, m["role"], "host", s, latency_ms=p, last_check_ts=l)

        registry[node_id] = _make_llm_probe
        extra_data[node_id] = {
            "role":         meta["role"],
            "provider":     meta["provider"],
            "model_id":     meta["model_id"],
            "success_rate": health["success_rate"],
            "call_count":   health.get("call_count", 0),
        }

    return registry, extra_data

# ---------------------------------------------------------------------------
# GET /obs/sessions
# ---------------------------------------------------------------------------
@router.get("/sessions", response_model=SessionsResponse)
def get_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="状态过滤: ok/rejected/error"),
    window_minutes: int = Query(60, ge=1, le=10080),
    keyword: Optional[str] = Query(None),
):
    """WARN-BE-002: 调用 SessionQueryService.query() 而非内联查询逻辑。"""
    return _get_obs_route_cached_response(
        cache_name="obs_sessions",
        cache_key={
            "page": int(page),
            "page_size": int(page_size),
            "status": status or "",
            "window_minutes": int(window_minutes),
            "keyword": keyword or "",
        },
        ttl_ms=_OBS_SESSIONS_CACHE_TTL_MS,
        builder=lambda: _build_sessions_response(
            None,
            page=page,
            page_size=page_size,
            status=status,
            window_minutes=window_minutes,
            keyword=keyword,
        ),
    )


def get_tribe_events(
    limit: int = Query(20, ge=1, le=200, description="返回最近事件条数"),
    event_type: Optional[str] = Query(None, description="按事件类型过滤"),
    source_agent: Optional[str] = Query(None, description="按 source_agent 过滤"),
    target_agent: Optional[str] = Query(None, description="按 target_agent 过滤"),
    request_id: Optional[str] = Query(None, description="按 request_id 过滤"),
    session_id: Optional[str] = Query(None, description="按 session_id 过滤"),
    conversation_id: Optional[str] = Query(None, description="按 conversation_id 过滤"),
):
    """Tribe 事件观测窗口（Host 内存总线最近事件）。"""
    try:
        from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

        bus = get_tribe_event_bus()
        items = [
            TribeEventItem(**event.to_dict())
            for event in bus.recent(
                limit=limit,
                event_type=event_type,
                source_agent=source_agent,
                target_agent=target_agent,
                request_id=request_id,
                session_id=session_id,
                conversation_id=conversation_id,
            )
        ]
    except Exception as exc:
        logger.debug("get_tribe_events degraded: %s", exc)
        items = []

    timestamp = items[0].ts_ms if items else _now_ms()
    return TribeEventsResponse(
        total=len(items),
        items=items,
        timestamp=int(timestamp),
    )

# ---------------------------------------------------------------------------
# GET /obs/sessions/export  — Ref #75: 会话列表 CSV 导出
# ---------------------------------------------------------------------------
@router.get("/sessions/export")
def export_sessions_csv(
    status: Optional[str] = Query(None, description="状态过滤: ok/rejected/error"),
    window_minutes: int = Query(1440, ge=1, le=10080, description="导出时间窗口（分钟），默认 24h"),
    keyword: Optional[str] = Query(None),
    max_rows: int = Query(5000, ge=1, le=10000, description="最大导出行数"),
):
    """导出会话列表为 CSV 文件（支持时间范围 + 状态过滤）。

    返回 text/csv 响应，文件名为 sessions_{timestamp}.csv。
    """
    import csv
    import io
    from fastapi.responses import StreamingResponse

    sessions_response = _build_sessions_response(
        None,
        page=1,
        page_size=max_rows,
        status=status,
        window_minutes=window_minutes,
        keyword=keyword,
    )

    rows = _sanitize_session_items_for_export(
        list(sessions_response.items),
        keyword=keyword,
    )

    # 构建 CSV
    output = io.StringIO()
    writer = csv.writer(output)
    # 表头
    writer.writerow([
        "request_id", "session_id", "ts_ms", "datetime",
        "status", "decision", "reason_code", "total_latency_ms",
    ])
    # 数据行
    import datetime as _dt
    for r in rows:
        ts = r.ts_ms or 0
        dt_str = _dt.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        writer.writerow([
            r.request_id,
            r.session_id or "",
            ts,
            dt_str,
            r.status or "",
            r.decision or "",
            r.reason_code or "",
            r.total_latency_ms or "",
        ])

    output.seek(0)
    filename = f"sessions_{int(_now_ms() / 1000)}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# GET /obs/trace/{request_id}
# ---------------------------------------------------------------------------
async def get_trace(request_id: str):
    """Compatibility route delegated to the durable projection-only reader."""

    from host_brain.app.obs_routes_trace import get_trace as get_projected_trace

    return await get_projected_trace(request_id)


# ---------------------------------------------------------------------------
# GET /obs/trace/{request_id}/stream_events — Chat Stream SSE 链路详情
# ---------------------------------------------------------------------------
async def stream_trace_live_feed(
    request: Request,
    limit: int = Query(6, ge=1, le=12),
    window_minutes: int = Query(1440, ge=1, le=10080),
    active_within_ms: int = Query(60000, ge=1000, le=3600000),
    interval_ms: int = Query(1000, ge=250, le=10000),
    keyword: Optional[str] = Query(None),
    once: bool = Query(False),
):
    """流式推送 Trace 首页活跃链路快照，供 WebUI 实时渲染活跃 feed。"""

    async def _event_stream():
        last_signature: Optional[str] = None
        last_snapshot: Optional[TraceLiveFeedSnapshot] = None
        first_event = True
        ledger = _get_ledger()
        while True:
            if await request.is_disconnected():
                break

            try:
                snapshot = await asyncio.to_thread(
                    _build_trace_live_feed_snapshot,
                    ledger,
                    limit=limit,
                    window_minutes=window_minutes,
                    keyword=keyword,
                    active_within_ms=active_within_ms,
                )
                signature = _trace_live_feed_signature(snapshot)
                if signature != last_signature:
                    delta = _build_trace_live_feed_delta(last_snapshot, snapshot)
                    yield _format_sse_event(
                        "snapshot",
                        _model_dump_compat(snapshot),
                        retry_ms=interval_ms if first_event else None,
                    )
                    if delta is not None:
                        yield _format_sse_event(
                            "delta",
                            _model_dump_compat(delta),
                        )
                    last_signature = signature
                    last_snapshot = snapshot
                    first_event = False
                else:
                    yield _format_sse_event(
                        "heartbeat",
                        {
                            "type": "heartbeat",
                            "count": snapshot.count,
                            "timestamp": _now_ms(),
                        },
                    )
            except Exception as exc:
                logger.warning("trace live feed push error: %s", exc)
                yield _format_sse_event(
                    "trace_error",
                    {
                        "type": "error",
                        "detail": str(exc),
                        "timestamp": _now_ms(),
                    },
                )

            if once:
                break
            await asyncio.sleep(interval_ms / 1000)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def get_trace_stream_events(request_id: str):
    """获取单次请求的 Chat Stream SSE 事件流（PRESENCE/AGENT_SELECTED/AGENT_HANDOFF 等），供链路详情展示。"""
    try:
        from host_brain.integrations.graph_evidence import get_evidence_plane
        ep = get_evidence_plane()
        rows = ep.query_stream_events(request_id=request_id)
    except Exception as exc:
        logger.debug("get_trace_stream_events: %s", exc)
        rows = []
    events = [
        StreamEventItem(
            request_id=str(r.get("request_id", "")),
            graph_id=str(r.get("graph_id", "")),
            phase=str(r.get("phase", "")),
            event_type=str(r.get("event_type", "")),
            node_id=r.get("node_id"),
            chunk=r.get("chunk"),
            ts_ms=int(r.get("ts_ms") or 0),
        )
        for r in rows
    ]
    return TraceStreamEventsResponse(
        request_id=request_id,
        events=events,
        count=len(events),
        timestamp=_now_ms(),
    )


def get_graph_schema():
    """获取当前 Host 图编排定义（节点、边、graph_meta），供 WebUI 展示图拓扑。"""
    try:
        from host_brain.app.graph_config_builder import (
            CHAT_GRAPH_NODE_ORDER,
            _build_graph_config_by_profile,
            _HOST_CHAT_14B_DEFAULT_PROVIDER,
            _HOST_CHAT_14B_DEFAULT_MODEL_ID,
        )
        from host_brain.app.execution_profile import PROFILE_CHAT_FAST

        # 使用默认参数构建图结构（不依赖运行时 LLM），仅用于展示拓扑
        graph_config = _build_graph_config_by_profile(
            execution_profile=PROFILE_CHAT_FAST,
            provider_name=_HOST_CHAT_14B_DEFAULT_PROVIDER,
            model_id=_HOST_CHAT_14B_DEFAULT_MODEL_ID or "gemini-2.5-flash-lite",
            timeout_ms=60000,
            available=False,
            api_key=None,
            provider_fn=lambda _: None,
        )
        edges_raw = graph_config.get("edges") or []
        graph_meta = graph_config.get("graph_meta") or {}
        active = list(graph_meta.get("active_nodes") or CHAT_GRAPH_NODE_ORDER)
        nodes = [GraphSchemaNode(node_id=n) for n in active]
        edges = [GraphSchemaEdge(source=e.get("source", ""), target=e.get("target", "")) for e in edges_raw if isinstance(e, dict)]
        return GraphSchemaResponse(
            graph_id=graph_config.get("graph_id", "host.chat.realtime"),
            version=graph_config.get("version", "v1"),
            entry_node=graph_config.get("entry_node"),
            active_nodes=active,
            nodes=nodes,
            edges=edges,
            graph_meta=graph_meta,
            timestamp=_now_ms(),
        )
    except Exception as exc:
        logger.warning("get_graph_schema failed: %s", exc)
        from host_brain.app.graph_config_builder import CHAT_GRAPH_NODE_ORDER
        return GraphSchemaResponse(
            graph_id="host.chat.realtime",
            version="v1",
            active_nodes=CHAT_GRAPH_NODE_ORDER,
            nodes=[GraphSchemaNode(node_id=n) for n in CHAT_GRAPH_NODE_ORDER],
            edges=[],
            timestamp=_now_ms(),
        )


# ---------------------------------------------------------------------------
# GET /obs/metrics/timeseries
# ---------------------------------------------------------------------------
@router.get("/metrics/timeseries", response_model=MetricsTimeseriesResponse)
def get_metrics_timeseries(
    range_minutes: int = Query(60, ge=5, le=10080, description="时间范围（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="聚合间隔（分钟）"),
):
    """WARN-BE-003: 调用 MetricsTimeseriesService.query() 而非内联聚合逻辑。"""
    projection_view = get_projection_ledger_view()
    svc = MetricsTimeseriesService(projection_view)
    now = _now_ms()
    start_time = now - range_minutes * 60 * 1000
    interval_map = {1: "1m", 5: "5m", 60: "1h"}
    interval = interval_map.get(interval_minutes, "5m")

    result = svc.query(
        start_time=start_time,
        end_time=now,
        interval=interval,
    )

    # 将 MetricsTimeseriesService 返回的通用结构映射到 MetricsTimeseriesResponse
    timeseries = result.get("timeseries", {})
    timestamps = timeseries.get("timestamps", [])
    totals = timeseries.get("total", [])
    rej_rates = timeseries.get("rejection_rate", [])
    interval_ms = interval_minutes * 60 * 1000

    points = []
    for i, ts_label in enumerate(timestamps):
        req = totals[i] if i < len(totals) else 0
        rej_rate_val = rej_rates[i] if i < len(rej_rates) else 0.0
        rej = int(req * rej_rate_val / 100) if req > 0 else 0
        points.append(TimeseriesPoint(
            ts_ms=start_time + i * interval_ms,
            requests=req,
            rejections=rej,
            errors=0,
            avg_latency_ms=None,
        ))

    summary = result.get("summary", {})
    total_req = summary.get("total_requests", 0)
    total_rej = summary.get("total_rejections", 0)
    rej_rate = summary.get("rejection_rate", 0.0)

    return MetricsTimeseriesResponse(
        range_ms=range_minutes * 60 * 1000,
        interval_ms=interval_ms,
        points=points,
        total_requests=total_req,
        total_rejections=total_rej,
        rejection_rate=rej_rate,
        timestamp=now,
    )

# ---------------------------------------------------------------------------
# GET /obs/llm/nodes
# ---------------------------------------------------------------------------
def get_llm_nodes(
    window_minutes: int = Query(60, ge=1, le=1440),
):
    """9 个 LLM 云节点独立健康状态（成功率/P95延迟/最后调用时间）。"""
    ledger = get_projection_ledger_view()
    since_ms = _now_ms() - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("llm_node_call_log", start_ms=since_ms, end_ms=_now_ms())
    except Exception:
        rows = []

    nodes = []
    for node_id, meta in _LLM_NODE_REGISTRY.items():
        node_rows = [r for r in rows if r.get("node_id") == node_id]
        health = _compute_node_health(node_rows)
        nodes.append(LlmNodeItem(
            node_id=node_id,
            role=meta["role"],
            provider=meta["provider"],
            model_id=meta["model_id"],
            status=health["status"],
            success_rate=health["success_rate"],
            p95_ms=health["p95_ms"],
            last_call_ts=health["last_call_ts"],
            call_count=health.get("call_count", 0),
        ))

    return LlmNodesResponse(nodes=nodes, timestamp=_now_ms())


def _load_resource_manager_status_file() -> dict[str, Any]:
    status_path = Path(__file__).resolve().parents[1] / "data" / "runtime_resource_manager.json"
    if not status_path.exists():
        return {}
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("resource manager status file read failed: %s", exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _compact_resource_manager_status(payload: dict[str, Any]) -> dict[str, Any]:
    last_snapshot = payload.get("last_snapshot") if isinstance(payload.get("last_snapshot"), dict) else {}
    last_decision = payload.get("last_decision") if isinstance(payload.get("last_decision"), dict) else {}
    last_actions = payload.get("last_actions") if isinstance(payload.get("last_actions"), list) else []
    last_gate_decisions = payload.get("last_gate_decisions") if isinstance(payload.get("last_gate_decisions"), list) else []
    last_llm_attempts = payload.get("last_llm_attempts") if isinstance(payload.get("last_llm_attempts"), list) else []
    degradation_state = payload.get("degradation_state") if isinstance(payload.get("degradation_state"), dict) else {}
    return {
        "running": bool(payload.get("running")),
        "interval_s": payload.get("interval_s"),
        "llm_enabled": bool(payload.get("llm_enabled")),
        "auto_gc_enabled": bool(payload.get("auto_gc_enabled")),
        "auto_trim_enabled": bool(payload.get("auto_trim_enabled")),
        "last_tick_ts_ms": payload.get("last_tick_ts_ms"),
        "tick_count": payload.get("tick_count") or 0,
        "analysis_count": payload.get("analysis_count") or 0,
        "auto_action_count": payload.get("auto_action_count") or 0,
        "last_error": payload.get("last_error"),
        "last_llm_error": payload.get("last_llm_error"),
        "last_llm_attempts": last_llm_attempts[:8],
        "last_snapshot": last_snapshot,
        "last_decision": last_decision,
        "last_actions": last_actions[:12],
        "last_gate_decisions": last_gate_decisions[:20],
        "degradation_state": degradation_state,
        "status_path": payload.get("status_path"),
    }


@router.get("/resource-manager/status")
def get_resource_manager_status():
    """Whole-system resource manager status for scheduler explainability."""
    payload, projection = read_process_projection(
        RESOURCE_MANAGER_KIND,
        RESOURCE_MANAGER_KEY,
        max_age_ms=RESOURCE_MANAGER_MAX_AGE_MS,
    )
    if payload is None:
        payload = {
            "status": "UNAVAILABLE",
            "source": "projection",
            "timestamp": _now_ms(),
            "running": False,
            "registered": False,
            "status_ref": "resource-manager://status/current",
        }
    return {**payload, "projection": projection}


# ---------------------------------------------------------------------------
# 子模块路由注册（P2-7 拆分后的子路由）
# ---------------------------------------------------------------------------
def _register_sub_routers() -> None:
    """将拆分出的子模块路由注册到主 router。
    使用延迟注册避免循环导入，在应用启动时调用一次即可。
    """
    try:
        from host_brain.app.obs_routes_health import router as _health_router
        from host_brain.app.obs_routes_sessions import router as _sessions_router
        from host_brain.app.obs_routes_trace import router as _trace_router
        from host_brain.app.obs_routes_metrics import router as _metrics_router
        # 注意：子路由已含 /obs 前缀，include 时不再重复添加
        router.include_router(_health_router, prefix="")
        router.include_router(_sessions_router, prefix="")
        router.include_router(_trace_router, prefix="")
        router.include_router(_metrics_router, prefix="")
    except Exception as exc:
        import logging as _logging
        _logging.getLogger("Natsume.obs").warning("obs sub-router registration failed: %s", exc)
