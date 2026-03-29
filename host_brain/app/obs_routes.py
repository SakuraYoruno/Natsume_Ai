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
from dataclasses import dataclass
import json
import logging
import os
import time
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.observability.service_health_aggregator import ServiceHealthAggregator
from host_brain.observability.session_query import SessionQueryService
from host_brain.observability.metrics_timeseries import MetricsTimeseriesService

logger = logging.getLogger("Natsume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])

# ---------------------------------------------------------------------------
# 9 个 LLM 云节点静态注册表（来自 node_registry.yaml）
# ---------------------------------------------------------------------------
_LLM_NODE_REGISTRY: Dict[str, Dict[str, str]] = {
    "chat_direct_ja":           {"role": "聊天直出",      "provider": "gemini", "model_id": "gemini-2.5-flash"},
    "core_reasoner_primary":    {"role": "主推理",      "provider": "gemini", "model_id": "gemini-2.5-pro"},
    "core_reasoner_secondary":  {"role": "备用推理",    "provider": "gemini", "model_id": "gemini-2.5-flash"},
    "planner_constraints":      {"role": "规划约束",    "provider": "gemini", "model_id": "gemini-2.5-pro"},
    "tool_planner":             {"role": "工具规划",    "provider": "gemini", "model_id": "gemini-2.5-flash"},
    "safety_verifier":          {"role": "安全验证",    "provider": "gemini", "model_id": "gemini-2.5-flash-lite"},
    "consistency_verifier":     {"role": "一致性验证",  "provider": "gemini", "model_id": "gemini-2.5-flash-lite"},
    "renderer_ja_c_style":      {"role": "日文渲染",    "provider": "gemini", "model_id": "gemini-2.5-flash"},
    "workbench_translate":      {"role": "翻译工作台",  "provider": "gemini", "model_id": "gemini-2.5-pro"},
    "workbench_code_assist":    {"role": "代码辅助",    "provider": "gemini", "model_id": "gemini-2.5-pro"},
}

# 边缘服务：与 ServiceHealthAggregator 的 _EDGE_SERVICES 一致（service_id）；value 为 voice_event_log 的 event_type 前缀列表
_EDGE_SERVICE_EVENT_PREFIXES: Dict[str, List[str]] = {
    "asr": ["ASR"],
    "tts": ["TTS"],
    "vts": ["VTS"],
    "audio_playback": ["VOICE_RESOURCE_GATE", "AUDIO", "AUDIO_PLAYBACK"],
    "edge_rpc": ["EDGE", "RPC", "GOVERNOR", "AIRLOCK"],
}

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


def _voice_event_node_type(event_type: str) -> tuple[str, str]:
    normalized = str(event_type or "").strip().upper()
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
        status = str(payload_dict.get("status") or row.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
        reason_code = (
            str(payload_dict.get("reason_code") or row.get("reason_code") or "").strip()
            or None
        )
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
                side="edge",
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


def _coalesce_trace_status(base_status: str, nodes: List[TraceNode]) -> str:
    normalized_base = str(base_status or "OK").strip().upper() or "OK"
    success_statuses = {
        "OK",
        "UP",
        "APPLIED",
        "ACCEPTED",
        "QUEUED",
        "STARTED",
        "PLAYING",
        "SUCCESS",
        "SKIPPED",
    }
    for node in nodes:
        node_status = str(node.status or "").strip().upper()
        if not node_status or node_status in success_statuses:
            continue
        if normalized_base == "FAIL":
            return normalized_base
        return "DEGRADED"
    return normalized_base


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


def _extract_dialogue_pair(memory_rows: List[Dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    for row in reversed(memory_rows):
        detail = str(row.get("detail") or "").strip()
        if "User:" not in detail or "Natsume:" not in detail:
            continue
        user_part = detail.split("User:", 1)[1]
        user_text, _, reply_text = user_part.partition("Natsume:")
        user_text = user_text.strip() or None
        reply_text = reply_text.strip() or None
        return user_text, reply_text
    return None, None


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
            summary["rerank_provider"] = payload_dict.get("provider") or payload_dict.get("effective_provider")
            if payload_dict.get("topk"):
                summary["topk"] = payload_dict.get("topk")
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
    memory_input_text, output_text = _extract_dialogue_pair(memory_rows)
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
        "input_text": _first_present(input_text, memory_input_text),
        "output_text": output_text,
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
        "EMBEDDING": "Recall Embedding",
        "RECALL": "Recall Fusion",
        "RERANK": "Recall Rerank",
        "EVIDENCE_PACK": "Evidence Pack",
        "PIPELINE": "Retrieval Pipeline",
    }
    node_ids = {
        "EMBEDDING": "retrieval_embedding",
        "RECALL": "recall_fusion",
        "RERANK": "retrieval_rerank",
        "EVIDENCE_PACK": "retrieval_evidence_pack",
        "PIPELINE": "retrieval_pipeline",
    }

    nodes: List[TraceNode] = []
    for row in retrieval_rows:
        op_kind = str(row.get("op_kind") or "").strip().upper() or "UNKNOWN"
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        latency_ms = payload_dict.get("latency_ms")
        if latency_ms is None and op_kind in ("RECALL", "PIPELINE"):
            latency_ms = node_timings_ms.get("recall_fusion")
        nodes.append(
            TraceNode(
                node_id=node_ids.get(op_kind, f"retrieval_{op_kind.lower()}"),
                node_type="recall" if op_kind == "RECALL" else "retrieval",
                display_name=display_names.get(op_kind, op_kind.title()),
                side="host",
                status=_normalize_trace_status(row.get("status"), default="OK"),
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
    node_id = str(node.node_id or "")
    if node_id.startswith("voice_"):
        event_type = str((node.input_data or {}).get("event_type") or "").upper()
        if "ACCEPTED" in event_type:
            return 0
        if "DISPATCHED" in event_type:
            return 5
        if "COMPLETED" in event_type:
            return 90
        return 1
    if node_id == "host_input":
        return 10
    if node_id.startswith("airlock"):
        return 15
    if node_id == "governor":
        return 20
    if node_id.startswith("retrieval_embedding"):
        return 30
    if node_id.startswith("recall_fusion"):
        return 31
    if node_id.startswith("retrieval_rerank"):
        return 32
    if node_id.startswith("retrieval_evidence_pack"):
        return 33
    if node_id.startswith("retrieval_pipeline"):
        return 34
    if node_id.startswith("llm_"):
        return 50
    if node_id.startswith("episodic_store"):
        return 60
    if node_id.startswith("bond_update"):
        return 70
    if node_id == "audit_snapshot":
        return 80
    return 40


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

# ---------------------------------------------------------------------------
# Pydantic 响应模型
# ---------------------------------------------------------------------------
class ServiceHealthItem(BaseModel):
    service_id: str
    service_type: str  # "edge" | "llm_node"
    display_name: str
    role: Optional[str] = None
    provider: Optional[str] = None
    model_id: Optional[str] = None
    status: str
    success_rate: Optional[float] = None
    p95_ms: Optional[int] = None
    last_call_ts: Optional[int] = None
    call_count: Optional[int] = None

class ServicesHealthResponse(BaseModel):
    total: int
    up: int
    degraded: int
    down: int
    unknown: int
    services: List[ServiceHealthItem]
    timestamp: int


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

class SessionItem(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    ts_ms: int
    status: str
    decision: Optional[str] = None
    reason_code: Optional[str] = None
    node_count: Optional[int] = None
    total_latency_ms: Optional[int] = None
    input_preview: Optional[str] = None

class SessionsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[SessionItem]
    timestamp: int

class TraceNode(BaseModel):
    node_id: str
    node_type: str  # "edge" | "llm_node" | "system"
    display_name: str
    side: Optional[str] = None
    provider: Optional[str] = None
    model_id: Optional[str] = None
    status: str
    reason_code: Optional[str] = None
    latency_ms: Optional[int] = None
    input_data: Optional[Any] = None
    output_data: Optional[Any] = None
    ts_ms: Optional[int] = None
    error: Optional[str] = None

class TraceEdge(BaseModel):
    source: str
    target: str

class TraceResponse(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    status: str
    total_latency_ms: Optional[int] = None
    node_count: int
    nodes: List[TraceNode]
    edges: List[TraceEdge]
    summary: Optional[Dict[str, Any]] = None
    ts_ms: int
    timestamp: int


class LiveTraceFeedItem(BaseModel):
    session: SessionItem
    trace: Optional[TraceResponse] = None
    error: Optional[str] = None


class TraceLiveFeedSnapshot(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    items: List[LiveTraceFeedItem] = Field(default_factory=list)
    count: int
    timestamp: int


class TraceLiveFeedNodeDelta(BaseModel):
    change_type: Literal["added", "updated"]
    node: TraceNode
    previous_status: Optional[str] = None


class TraceLiveFeedDeltaItem(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    session: Optional[SessionItem] = None
    trace_status: Optional[str] = None
    total_latency_ms: Optional[int] = None
    latest_node: Optional[TraceNode] = None
    changes: List[TraceLiveFeedNodeDelta] = Field(default_factory=list)
    change_count: int = 0


class TraceLiveFeedDelta(BaseModel):
    type: Literal["delta"] = "delta"
    items: List[TraceLiveFeedDeltaItem] = Field(default_factory=list)
    count: int
    timestamp: int

class TimeseriesPoint(BaseModel):
    ts_ms: int
    requests: int
    rejections: int
    errors: int
    avg_latency_ms: Optional[float] = None

class MetricsTimeseriesResponse(BaseModel):
    range_ms: int
    interval_ms: int
    points: List[TimeseriesPoint]
    total_requests: int
    total_rejections: int
    rejection_rate: float
    timestamp: int

class LlmNodeItem(BaseModel):
    node_id: str
    role: str
    provider: str
    model_id: str
    status: str
    success_rate: Optional[float] = None
    p95_ms: Optional[int] = None
    last_call_ts: Optional[int] = None
    call_count: int

class LlmNodesResponse(BaseModel):
    nodes: List[LlmNodeItem]
    timestamp: int


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
    ledger: SQLiteLedger,
    *,
    page: int,
    page_size: int,
    status: Optional[str],
    window_minutes: int,
    keyword: Optional[str],
) -> SessionsResponse:
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
    )


def _build_trace_response(ledger: SQLiteLedger, request_id: str) -> TraceResponse:
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

    if trace_rows:
        row_dict = trace_rows[-1]
        nodes_raw = _safe_json(row_dict.get("nodes_json")) or []
        edges_raw = _safe_json(row_dict.get("edges_json")) or []

        nodes = []
        for node_row in nodes_raw:
            nodes.append(TraceNode(
                node_id=node_row.get("node_id", ""),
                node_type=node_row.get("node_type", "system"),
                display_name=node_row.get("display_name", node_row.get("node_id", "")),
                side=node_row.get("side"),
                provider=node_row.get("provider"),
                model_id=node_row.get("model_id"),
                status=node_row.get("status", "OK"),
                reason_code=node_row.get("reason_code"),
                latency_ms=node_row.get("latency_ms"),
                input_data=node_row.get("input_data"),
                output_data=node_row.get("output_data"),
                ts_ms=node_row.get("ts_ms"),
                error=node_row.get("error"),
            ))

        edges = [TraceEdge(source=edge.get("source", ""), target=edge.get("target", "")) for edge in edges_raw]
        voice_nodes = _build_voice_trace_nodes(voice_rows)
        edges = _build_trace_edges(nodes, edges, voice_nodes)
        nodes.extend(voice_nodes)
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
    synthesized_nodes.extend(_build_llm_trace_nodes(llm_rows, summary))
    synthesized_nodes.extend(_build_bond_trace_nodes(bond_rows))
    synthesized_nodes.extend(_build_memory_trace_nodes(memory_rows, summary))
    audit_snapshot_node = _build_audit_snapshot_node(summary)
    if audit_snapshot_node is not None:
        synthesized_nodes.append(audit_snapshot_node)

    nodes = _sort_trace_nodes(synthesized_nodes)
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
        ts_ms=trace_ts,
        timestamp=_now_ms(),
    )


def _build_trace_live_feed_snapshot(
    ledger: SQLiteLedger,
    *,
    limit: int,
    window_minutes: int,
    keyword: Optional[str],
) -> TraceLiveFeedSnapshot:
    sessions_response = _build_sessions_response(
        ledger,
        page=1,
        page_size=limit,
        status=None,
        window_minutes=window_minutes,
        keyword=keyword,
    )

    items: List[LiveTraceFeedItem] = []
    for session in sessions_response.items:
        try:
            trace = _build_trace_response(ledger, session.request_id)
            items.append(LiveTraceFeedItem(session=session, trace=trace))
        except HTTPException as exc:
            if exc.status_code == 404:
                items.append(LiveTraceFeedItem(session=session, trace=None, error="not_found"))
                continue
            raise

    return TraceLiveFeedSnapshot(
        items=items,
        count=len(items),
        timestamp=_now_ms(),
    )


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
@router.get("/services/health", response_model=ServicesHealthResponse)
async def get_services_health(
    window_minutes: int = Query(60, ge=1, le=1440, description="统计窗口（分钟）"),
    side: Optional[str] = Query(None, description="过滤端：host|edge"),
):
    """WARN-BE-001: 调用 ServiceHealthAggregator.aggregate() 而非内联聚合逻辑。"""
    ledger = _get_ledger()
    since_ms = _now_ms() - window_minutes * 60 * 1000

    # 使用 ledger 数据构建探测函数注册表（被动监控）
    probe_registry, extra_data = _build_probe_registry(ledger, since_ms)
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

        async def _make_edge_probe(s=_status, p=_p95, l=_last, _sid=sid, _dname=display_name):
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

        async def _make_llm_probe(s=_status, p=_p95, l=_last, nid=node_id, m=meta):
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
async def get_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="状态过滤: ok/rejected/error"),
    window_minutes: int = Query(60, ge=1, le=10080),
    keyword: Optional[str] = Query(None),
):
    """WARN-BE-002: 调用 SessionQueryService.query() 而非内联查询逻辑。"""
    return _build_sessions_response(
        _get_ledger(),
        page=page,
        page_size=page_size,
        status=status,
        window_minutes=window_minutes,
        keyword=keyword,
    )

# ---------------------------------------------------------------------------
# GET /obs/sessions/export  — TODO-75: 会话列表 CSV 导出
# ---------------------------------------------------------------------------
@router.get("/sessions/export")
async def export_sessions_csv(
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

    ledger = _get_ledger()
    svc = SessionQueryService(ledger)
    since_ms = _now_ms() - window_minutes * 60 * 1000

    # 一次性拉取所有数据（最多 max_rows 行）
    result = svc.query(
        page=1,
        page_size=max_rows,
        status=status,
        keyword=keyword,
        start_time=since_ms,
        end_time=_now_ms(),
    )

    rows = result.get("items", [])

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
        ts = r.get("ts_ms") or 0
        dt_str = _dt.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        writer.writerow([
            r.get("request_id", ""),
            r.get("session_id", ""),
            ts,
            dt_str,
            r.get("status", ""),
            r.get("decision", ""),
            r.get("reason_code", ""),
            r.get("duration_ms", ""),
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
@router.get("/trace/{request_id}", response_model=TraceResponse)
async def get_trace(request_id: str):
    return _build_trace_response(_get_ledger(), request_id)
    """获取单次会话的完整链路节点详情（含入参/出参/状态/延迟）。"""
    ledger = _get_ledger()

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

    # 2) 解析完整链路（fetch_by_request_id 已通过 _decode_row 自动反序列化 JSON 字段）
    if trace_rows:
        row_dict = trace_rows[-1]
        nodes_raw = _safe_json(row_dict.get("nodes_json")) or []
        edges_raw = _safe_json(row_dict.get("edges_json")) or []

        nodes = []
        for n in nodes_raw:
            nodes.append(TraceNode(
                node_id=n.get("node_id", ""),
                node_type=n.get("node_type", "system"),
                display_name=n.get("display_name", n.get("node_id", "")),
                side=n.get("side"),
                provider=n.get("provider"),
                model_id=n.get("model_id"),
                status=n.get("status", "OK"),
                reason_code=n.get("reason_code"),
                latency_ms=n.get("latency_ms"),
                input_data=n.get("input_data"),
                output_data=n.get("output_data"),
                ts_ms=n.get("ts_ms"),
                error=n.get("error"),
            ))

        edges = [TraceEdge(source=e.get("source", ""), target=e.get("target", "")) for e in edges_raw]
        voice_nodes = _build_voice_trace_nodes(voice_rows)
        edges = _build_trace_edges(nodes, edges, voice_nodes)
        nodes.extend(voice_nodes)
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
    synthesized_nodes.extend(_build_llm_trace_nodes(llm_rows, summary))
    synthesized_nodes.extend(_build_bond_trace_nodes(bond_rows))
    synthesized_nodes.extend(_build_memory_trace_nodes(memory_rows, summary))
    audit_snapshot_node = _build_audit_snapshot_node(summary)
    if audit_snapshot_node is not None:
        synthesized_nodes.append(audit_snapshot_node)

    nodes = _sort_trace_nodes(synthesized_nodes)
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
        ts_ms=trace_ts,
        timestamp=_now_ms(),
    )


# ---------------------------------------------------------------------------
# GET /obs/trace/{request_id}/stream_events — Chat Stream SSE 链路详情
# ---------------------------------------------------------------------------
@router.get("/trace-feed/live")
async def stream_trace_live_feed(
    request: Request,
    limit: int = Query(6, ge=1, le=12),
    window_minutes: int = Query(1440, ge=1, le=10080),
    interval_ms: int = Query(1000, ge=250, le=10000),
    keyword: Optional[str] = Query(None),
    once: bool = Query(False),
):
    """流式推送 Trace 首页活跃链路快照，供 WebUI 实时渲染活跃 feed。"""

    async def _event_stream():
        last_signature: Optional[str] = None
        last_snapshot: Optional[TraceLiveFeedSnapshot] = None
        first_event = True
        while True:
            if await request.is_disconnected():
                break

            try:
                snapshot = _build_trace_live_feed_snapshot(
                    _get_ledger(),
                    limit=limit,
                    window_minutes=window_minutes,
                    keyword=keyword,
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


class StreamEventItem(BaseModel):
    request_id: str
    graph_id: str
    phase: str
    event_type: str
    node_id: Optional[str] = None
    chunk: Optional[str] = None
    ts_ms: int


class TraceStreamEventsResponse(BaseModel):
    request_id: str
    events: List[StreamEventItem]
    count: int
    timestamp: int


@router.get("/trace/{request_id}/stream_events", response_model=TraceStreamEventsResponse)
async def get_trace_stream_events(request_id: str):
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


# ---------------------------------------------------------------------------
# GET /obs/graph/schema — 图的编排（当前 Host 图定义：节点与边）
# ---------------------------------------------------------------------------
class GraphSchemaNode(BaseModel):
    node_id: str
    display_name: Optional[str] = None


class GraphSchemaEdge(BaseModel):
    source: str
    target: str


class GraphSchemaResponse(BaseModel):
    graph_id: str
    version: str
    entry_node: Optional[str] = None
    active_nodes: List[str] = Field(default_factory=list)
    nodes: List[GraphSchemaNode] = Field(default_factory=list)
    edges: List[GraphSchemaEdge] = Field(default_factory=list)
    graph_meta: Optional[Dict[str, Any]] = None
    timestamp: int


@router.get("/graph/schema", response_model=GraphSchemaResponse)
async def get_graph_schema():
    """获取当前 Host 图编排定义（节点、边、graph_meta），供 WebUI 展示图拓扑。"""
    try:
        from host_brain.app.graph_config_builder import (
            CHAT_GRAPH_NODE_ORDER,
            _build_graph_config_by_profile,
            _HOST_CHAT_8B_DEFAULT_PROVIDER,
            _HOST_CHAT_8B_DEFAULT_MODEL_ID,
        )
        from host_brain.app.execution_profile import PROFILE_CHAT_FAST

        # 使用默认参数构建图结构（不依赖运行时 LLM），仅用于展示拓扑
        graph_config = _build_graph_config_by_profile(
            execution_profile=PROFILE_CHAT_FAST,
            provider_name=_HOST_CHAT_8B_DEFAULT_PROVIDER,
            model_id=_HOST_CHAT_8B_DEFAULT_MODEL_ID or "gemini-2.5-flash",
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
async def get_metrics_timeseries(
    range_minutes: int = Query(60, ge=5, le=10080, description="时间范围（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="聚合间隔（分钟）"),
):
    """WARN-BE-003: 调用 MetricsTimeseriesService.query() 而非内联聚合逻辑。"""
    ledger = _get_ledger()
    svc = MetricsTimeseriesService(ledger)
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
@router.get("/llm/nodes", response_model=LlmNodesResponse)
async def get_llm_nodes(
    window_minutes: int = Query(60, ge=1, le=1440),
):
    """9 个 LLM 云节点独立健康状态（成功率/P95延迟/最后调用时间）。"""
    ledger = _get_ledger()
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

# ---------------------------------------------------------------------------
# P1: GET /obs/governor/stats  — Governor 决策漏斗 + reason_code 分布
# ---------------------------------------------------------------------------
class GovernorReasonItem(BaseModel):
    reason_code: str
    count: int
    pct: float

class GovernorStatsResponse(BaseModel):
    window_minutes: int
    total: int
    approved: int
    rejected: int
    rejection_rate: float
    reason_codes: List[GovernorReasonItem]
    timeseries_labels: List[str]
    timeseries_approved: List[int]
    timeseries_rejected: List[int]
    timestamp: int

@router.get("/governor/stats", response_model=GovernorStatsResponse)
async def get_governor_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
):
    """P1: Governor 决策漏斗 — 批准/拒绝数量、拒绝率、reason_code 分布、时序趋势。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("governor_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    total = len(rows)
    approved = sum(1 for r in rows if str(r.get("decision", "")).upper() == "APPROVE")
    rejected = total - approved
    rejection_rate = round(rejected / total * 100, 1) if total > 0 else 0.0

    # reason_code 分布（仅拒绝行）
    from collections import Counter
    rej_rows = [r for r in rows if str(r.get("decision", "")).upper() != "APPROVE"]
    rc_counter = Counter(r.get("reason_code") or "UNKNOWN" for r in rej_rows)
    reason_codes = [
        GovernorReasonItem(
            reason_code=code,
            count=cnt,
            pct=round(cnt / rejected * 100, 1) if rejected > 0 else 0.0,
        )
        for code, cnt in rc_counter.most_common(10)
    ]

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_approved = [0] * bucket_count
    ts_rejected = [0] * bucket_count
    for r in rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            if str(r.get("decision", "")).upper() == "APPROVE":
                ts_approved[idx] += 1
            else:
                ts_rejected[idx] += 1

    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    return GovernorStatsResponse(
        window_minutes=window_minutes,
        total=total,
        approved=approved,
        rejected=rejected,
        rejection_rate=rejection_rate,
        reason_codes=reason_codes,
        timeseries_labels=labels,
        timeseries_approved=ts_approved,
        timeseries_rejected=ts_rejected,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# P1: GET /obs/errors  — 全局错误日志统计
# ---------------------------------------------------------------------------
class ErrorStageItem(BaseModel):
    stage: str
    count: int
    pct: float

class ErrorCodeItem(BaseModel):
    reason_code: str
    count: int

class ErrorsResponse(BaseModel):
    window_minutes: int
    total_errors: int
    stages: List[ErrorStageItem]
    top_codes: List[ErrorCodeItem]
    timeseries_labels: List[str]
    timeseries_counts: List[int]
    recent: List[Dict[str, Any]]
    timestamp: int

@router.get("/errors", response_model=ErrorsResponse)
async def get_errors(
    window_minutes: int = Query(60, ge=1, le=10080),
    interval_minutes: int = Query(5, ge=1, le=60),
    limit: int = Query(20, ge=1, le=100, description="最近错误条数"),
):
    """P1: 全局错误日志统计 — 错误总量、stage 分布、reason_code Top10、时序趋势、最近错误列表。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    total = len(rows)

    from collections import Counter
    stage_counter = Counter(r.get("stage") or "UNKNOWN" for r in rows)
    stages = [
        ErrorStageItem(
            stage=s,
            count=c,
            pct=round(c / total * 100, 1) if total > 0 else 0.0,
        )
        for s, c in stage_counter.most_common(10)
    ]

    code_counter = Counter(r.get("reason_code") or "UNKNOWN" for r in rows)
    top_codes = [
        ErrorCodeItem(reason_code=code, count=cnt)
        for code, cnt in code_counter.most_common(10)
    ]

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_counts = [0] * bucket_count
    for r in rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            ts_counts[idx] += 1

    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    # 最近 N 条错误（倒序）
    recent_rows = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent = [
        {
            "request_id": r.get("request_id"),
            "stage": r.get("stage"),
            "reason_code": r.get("reason_code"),
            "detail": (r.get("detail") or "")[:200],
            "ts_ms": r.get("ts_ms"),
        }
        for r in recent_rows
    ]

    return ErrorsResponse(
        window_minutes=window_minutes,
        total_errors=total,
        stages=stages,
        top_codes=top_codes,
        timeseries_labels=labels,
        timeseries_counts=ts_counts,
        recent=recent,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# P2/P3: GET /obs/subsystems/stats  — 子系统健康统计（Memory/Retrieval/Skill/Agent Loop/Airlock/Bond）
# ---------------------------------------------------------------------------
class SubsystemStatItem(BaseModel):
    subsystem: str
    display_name: str
    total: int
    success: int
    degraded: int = 0
    failed: int
    success_rate: Optional[float]
    p95_ms: Optional[int]
    last_ts: Optional[int]
    status: str = "UNKNOWN"
    detail: Optional[str] = None
    blocked: Optional[int] = None

class SubsystemsStatsResponse(BaseModel):
    window_minutes: int
    subsystems: List[SubsystemStatItem]
    timestamp: int

def _classify_subsystem_row_status(value: Any) -> str:
    if value is None:
        return "neutral"
    if isinstance(value, bool):
        return "success" if value else "failed"
    raw = str(value).strip().upper()
    if raw in ("", "NONE", "NULL"):
        return "neutral"
    if raw in ("OK", "SUCCESS", "DONE", "APPROVED", "PASS", "PASSED", "LENIENT_OK", "STRICT_OK", "COMPLETED"):
        return "success"
    if raw in ("DEGRADED", "WARN", "WARNING", "PARTIAL", "FALLBACK", "SKIPPED"):
        return "degraded"
    if raw in ("FAIL", "FAILED", "ERROR", "DOWN", "REJECTED", "BLOCKED", "TIMEOUT", "DENIED"):
        return "failed"
    return "neutral"


def _derive_subsystem_status(total: int, success: int, degraded: int, failed: int) -> str:
    if total <= 0:
        return "UNKNOWN"
    if success == 0 and degraded == 0 and failed == 0:
        return "UNKNOWN"
    if failed > 0 and success == 0 and degraded == 0:
        return "DOWN"
    if failed > 0 or degraded > 0:
        return "DEGRADED"
    return "UP"


def _intent_lint_is_clean(value: Any) -> bool:
    if value is None:
        return True
    parsed = _safe_json(value)
    if isinstance(parsed, str):
        raw = parsed.strip()
        return raw == "" or raw.upper() in ("OK", "PASS", "SUCCESS", "CLEAN")
    if isinstance(parsed, list):
        return len(parsed) == 0
    if isinstance(parsed, dict):
        for item in parsed.values():
            normalized = _safe_json(item)
            if isinstance(normalized, list) and normalized:
                return False
            if isinstance(normalized, dict) and any(bool(v) for v in normalized.values()):
                return False
            if isinstance(normalized, str) and normalized.strip():
                return False
            if isinstance(normalized, (int, float)) and normalized != 0:
                return False
            if isinstance(normalized, bool) and normalized:
                return False
        return True
    return not bool(parsed)


def _intent_row_passed(row: Dict[str, Any]) -> bool:
    parse_stage = str(row.get("parse_stage") or "").strip().upper()
    parse_ok = not parse_stage or parse_stage in ("OK", "PASS", "SUCCESS") or parse_stage.endswith("_OK")
    return parse_ok and _intent_lint_is_clean(row.get("lint_result"))


def _compute_subsystem_health(rows: List[Dict], status_field: str = "status", latency_field: Optional[str] = None) -> Dict[str, Any]:
    """通用子系统健康计算：成功率、P95延迟、最后活跃时间。"""
    total = len(rows)
    if total == 0:
        return {
            "total": 0,
            "success": 0,
            "degraded": 0,
            "failed": 0,
            "success_rate": None,
            "p95_ms": None,
            "last_ts": None,
            "status": "UNKNOWN",
            "detail": "近窗口无调用",
            "blocked": None,
        }

    success = 0
    degraded = 0
    failed = 0
    for row in rows:
        bucket = _classify_subsystem_row_status(row.get(status_field))
        if bucket == "success":
            success += 1
        elif bucket == "degraded":
            degraded += 1
        elif bucket == "failed":
            failed += 1

    measured_total = success + degraded + failed
    success_rate = round(success / measured_total * 100, 1) if measured_total > 0 else None

    p95_ms = None
    if latency_field:
        latencies = [r[latency_field] for r in rows if r.get(latency_field) is not None]
        if latencies:
            latencies.sort()
            idx = max(0, int(len(latencies) * 0.95) - 1)
            p95_ms = latencies[idx]

    last_ts = max((r.get("ts_ms") or 0 for r in rows), default=None)
    return {
        "total": total,
        "success": success,
        "degraded": degraded,
        "failed": failed,
        "success_rate": success_rate,
        "p95_ms": p95_ms,
        "last_ts": last_ts,
        "status": _derive_subsystem_status(total, success, degraded, failed),
        "detail": None,
        "blocked": None,
    }

@router.get("/subsystems/stats", response_model=SubsystemsStatsResponse)
async def get_subsystems_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
):
    """P2/P3: 子系统健康统计 — Memory/Retrieval/Skill/Agent Loop/Airlock/Bond/Vision/Sandbox/Channel。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # 定义各子系统：(subsystem_id, display_name, table, status_field, latency_field)
    SUBSYSTEM_DEFS = [
        ("memory",    "Memory 操作",    "memory_ops_log",     "status",        None),
        ("retrieval", "向量检索",        "retrieval_event_log","status",        None),
        ("skill",     "Skill 执行",     "skill_exec_log",     "result_status", None),
        ("agent_loop","Agent Loop",     "agent_loop_log",     "status",        "latency_ms"),
        ("airlock",   "Airlock 过滤",   "airlock_log",        "blocked",       None),
        ("bond",      "Bond 关系更新",  "bond_update_log",    "status",        None),
        ("vision",    "Vision 视觉",    "vision_event_log",   "status",        None),
        ("sandbox",   "Sandbox 执行",   "sandbox_exec_log",   "status",        "duration_ms"),
        ("channel",   "Channel I/O",   "channel_io_log",     "status",        None),
        ("intent",    "Intent 解析",    "intent_log",         "lint_result",   None),
        ("qa",        "QA 违规检查",    "qa_violation_log",   None,            None),
    ]

    subsystems = []
    for sid, dname, table, status_field, latency_field in SUBSYSTEM_DEFS:
        try:
            rows = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

        if sid == "airlock":
            # airlock: blocked=1 表示策略命中，但不代表子系统故障
            total = len(rows)
            blocked = sum(1 for r in rows if r.get("blocked") in (1, True, "1"))
            last_ts = max((r.get("ts_ms") or 0 for r in rows), default=None)
            h = {
                "total": total,
                "success": total,
                "degraded": 0,
                "failed": 0,
                "success_rate": 100.0 if total > 0 else None,
                "p95_ms": None,
                "last_ts": last_ts,
                "status": "UP" if total > 0 else "UNKNOWN",
                "detail": (
                    f"策略拦截 {blocked} 次"
                    if blocked > 0 else ("最近无策略拦截" if total > 0 else "近窗口无调用")
                ),
                "blocked": blocked,
            }
        elif sid == "qa":
            # qa_violation_log: 每行都是一次违规，代表内容质量告警，不代表 QA 子系统自身故障
            total = len(rows)
            h = {
                "total": total,
                "success": 0,
                "degraded": total,
                "failed": 0,
                "success_rate": 0.0 if total > 0 else None,
                "p95_ms": None,
                "last_ts": max((r.get("ts_ms") or 0 for r in rows), default=None),
                "status": "DEGRADED" if total > 0 else "UNKNOWN",
                "detail": f"检测到 {total} 条 QA 违规" if total > 0 else "近窗口无调用",
                "blocked": None,
            }
        elif sid == "intent":
            # intent_log: lint_result 可能是 {"args": [], "schema": []} 这类 JSON，空数组视为 lint 通过
            total = len(rows)
            success = sum(1 for r in rows if _intent_row_passed(r))
            failed = total - success
            last_ts = max((r.get("ts_ms") or 0 for r in rows), default=None)
            h = {
                "total": total,
                "success": success,
                "degraded": 0,
                "failed": failed,
                "success_rate": round(success / total * 100, 1) if total > 0 else None,
                "p95_ms": None,
                "last_ts": last_ts,
                "status": _derive_subsystem_status(total, success, 0, failed),
                "detail": f"{failed} 条 intent lint 未通过" if failed > 0 else None,
                "blocked": None,
            }
        else:
            h = _compute_subsystem_health(rows, status_field=status_field or "status", latency_field=latency_field)
            if sid == "retrieval" and h["degraded"] > 0 and h["failed"] == 0:
                h["detail"] = f"{h['degraded']} 次降级，最近多为检索链慢路径"

        subsystems.append(SubsystemStatItem(
            subsystem=sid,
            display_name=dname,
            total=h["total"],
            success=h["success"],
            degraded=h.get("degraded", 0),
            failed=h["failed"],
            success_rate=h["success_rate"],
            p95_ms=h.get("p95_ms"),
            last_ts=h.get("last_ts"),
            status=h.get("status", "UNKNOWN"),
            detail=h.get("detail"),
            blocked=h.get("blocked"),
        ))

    return SubsystemsStatsResponse(
        window_minutes=window_minutes,
        subsystems=subsystems,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# P3: GET /obs/agent_loop/stats  — Agent Loop 详细统计
# ---------------------------------------------------------------------------
class AgentLoopRoundItem(BaseModel):
    request_id: str
    session_id: Optional[str]
    goal: str
    total_rounds: int
    goal_achieved: bool
    total_actions: int
    success_actions: int
    failed_actions: int
    total_latency_ms: Optional[int]
    ts_ms: int

class AgentLoopStatsResponse(BaseModel):
    window_minutes: int
    total_loops: int
    achieved: int
    failed: int
    achievement_rate: float
    avg_rounds: Optional[float]
    recent_loops: List[AgentLoopRoundItem]
    timestamp: int

@router.get("/agent_loop/stats", response_model=AgentLoopStatsResponse)
async def get_agent_loop_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    limit: int = Query(10, ge=1, le=50),
):
    """P3: Agent Loop 详细统计 — 目标达成率、平均轮次、最近执行列表。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("agent_loop_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 按 request_id 聚合（取 LOOP_END 或最后一行）
    from collections import defaultdict
    loop_map: Dict[str, List] = defaultdict(list)
    for r in rows:
        loop_map[r.get("request_id", "")].append(r)

    loops = []
    for req_id, loop_rows in loop_map.items():
        # 找 LOOP_END 行
        end_rows = [r for r in loop_rows if str(r.get("phase", "")).upper() in ("LOOP_END", "LOOP_SUCCESS", "LOOP_FAILED")]
        summary_row = end_rows[-1] if end_rows else loop_rows[-1]

        total_rounds = max((r.get("round") or 0 for r in loop_rows), default=0)
        goal_achieved = bool(summary_row.get("goal_achieved"))
        total_actions = sum(r.get("action_count") or 0 for r in loop_rows)
        success_actions = sum(r.get("success_count") or 0 for r in loop_rows)
        failed_actions = sum(r.get("failed_count") or 0 for r in loop_rows)
        total_latency = sum(r.get("latency_ms") or 0 for r in loop_rows if r.get("latency_ms"))

        loops.append({
            "request_id": req_id,
            "session_id": summary_row.get("session_id"),
            "goal": (summary_row.get("goal") or "")[:80],
            "total_rounds": total_rounds,
            "goal_achieved": goal_achieved,
            "total_actions": total_actions,
            "success_actions": success_actions,
            "failed_actions": failed_actions,
            "total_latency_ms": total_latency or None,
            "ts_ms": summary_row.get("ts_ms") or 0,
        })

    total_loops = len(loops)
    achieved = sum(1 for l in loops if l["goal_achieved"])
    failed_loops = total_loops - achieved
    achievement_rate = round(achieved / total_loops * 100, 1) if total_loops > 0 else 0.0
    avg_rounds = round(sum(l["total_rounds"] for l in loops) / total_loops, 1) if total_loops > 0 else None

    # 最近 N 条（按 ts_ms 倒序）
    recent = sorted(loops, key=lambda l: l["ts_ms"], reverse=True)[:limit]

    return AgentLoopStatsResponse(
        window_minutes=window_minutes,
        total_loops=total_loops,
        achieved=achieved,
        failed=failed_loops,
        achievement_rate=achievement_rate,
        avg_rounds=avg_rounds,
        recent_loops=[AgentLoopRoundItem(**l) for l in recent],
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-26: GET /obs/memory/stats  — Memory 子系统详细统计
# ---------------------------------------------------------------------------
class MemorySubsystemItem(BaseModel):
    subsystem: str
    display_name: str
    total: int
    success: int
    failed: int
    success_rate: Optional[float]
    p95_ms: Optional[int]
    last_ts: Optional[int]
    extra: Optional[Dict[str, Any]] = None

class MemoryStatsResponse(BaseModel):
    window_minutes: int
    subsystems: List[MemorySubsystemItem]
    # 聚合指标
    total_writes: int
    guard_reject_rate: Optional[float]
    compress_rate: Optional[float]
    distill_count: int
    timestamp: int

@router.get("/memory/stats", response_model=MemoryStatsResponse)
async def get_memory_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-26: Memory 子系统详细统计。

    覆盖四个 Memory 子系统：
    - EpisodicStore：写入量、成功率
    - MemoryGuard：拒绝率（NARRATIVE_ONLY 过滤 + 宪法校验拒绝）
    - ContextCompressor：压缩率（压缩前/后 token 数比值）
    - LongTermDistiller：蒸馏次数、蒸馏成功率
    """
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── EpisodicStore 写入统计 ────────────────────────────────────────────
    try:
        episodic_rows = ledger.fetch_by_time_range("episodic_store_log", start_ms=since_ms, end_ms=now)
    except Exception:
        episodic_rows = []

    ep_total = len(episodic_rows)
    ep_success = sum(1 for r in episodic_rows if str(r.get("status", "")).upper() in ("OK", "WRITTEN", "SUCCESS"))
    ep_failed = ep_total - ep_success
    ep_rate = round(ep_success / ep_total * 100, 1) if ep_total > 0 else None
    ep_last = max((r.get("ts_ms") or 0 for r in episodic_rows), default=None)

    # ── MemoryGuard 拒绝统计 ──────────────────────────────────────────────
    try:
        guard_rows = ledger.fetch_by_time_range("memory_guard_log", start_ms=since_ms, end_ms=now)
    except Exception:
        guard_rows = []

    # 从 error_log 中补充 MemoryGuard 相关记录（MEMORY_GUARD_FILTERED 标签）
    try:
        error_rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
        guard_error_rows = [
            r for r in error_rows
            if "MEMORY_GUARD" in str(r.get("stage", "")).upper()
            or "MEMORY_GUARD" in str(r.get("reason_code", "")).upper()
        ]
    except Exception:
        guard_error_rows = []

    all_guard_rows = guard_rows + guard_error_rows
    guard_total = len(all_guard_rows)
    guard_rejected = sum(
        1 for r in all_guard_rows
        if str(r.get("status", "")).upper() in ("REJECTED", "BLOCKED", "FAILED")
        or str(r.get("reason_code", "")).upper().startswith("EPISODIC_")
        or str(r.get("reason_code", "")).upper().startswith("CANON_")
    )
    guard_reject_rate = round(guard_rejected / guard_total * 100, 1) if guard_total > 0 else None
    guard_last = max((r.get("ts_ms") or 0 for r in all_guard_rows), default=None)

    # ── ContextCompressor 压缩统计 ────────────────────────────────────────
    try:
        compress_rows = ledger.fetch_by_time_range("context_compress_log", start_ms=since_ms, end_ms=now)
    except Exception:
        compress_rows = []

    comp_total = len(compress_rows)
    comp_success = sum(1 for r in compress_rows if str(r.get("status", "")).upper() in ("OK", "DONE", "SUCCESS"))
    comp_failed = comp_total - comp_success
    comp_rate = None
    if compress_rows:
        # 计算平均压缩率：(1 - output_tokens / input_tokens) * 100
        ratios = []
        for r in compress_rows:
            inp = r.get("input_tokens") or r.get("input_chars")
            out = r.get("output_tokens") or r.get("output_chars")
            if inp and out and inp > 0:
                ratios.append(1.0 - out / inp)
        if ratios:
            comp_rate = round(sum(ratios) / len(ratios) * 100, 1)
    comp_last = max((r.get("ts_ms") or 0 for r in compress_rows), default=None)

    # ── LongTermDistiller 蒸馏统计 ────────────────────────────────────────
    try:
        distill_rows = ledger.fetch_by_time_range("distill_log", start_ms=since_ms, end_ms=now)
    except Exception:
        distill_rows = []

    distill_total = len(distill_rows)
    distill_success = sum(1 for r in distill_rows if str(r.get("status", "")).upper() in ("OK", "DONE", "SUCCESS"))
    distill_failed = distill_total - distill_success
    distill_rate = round(distill_success / distill_total * 100, 1) if distill_total > 0 else None
    distill_last = max((r.get("ts_ms") or 0 for r in distill_rows), default=None)

    subsystems = [
        MemorySubsystemItem(
            subsystem="episodic_store",
            display_name="EpisodicStore 写入",
            total=ep_total,
            success=ep_success,
            failed=ep_failed,
            success_rate=ep_rate,
            p95_ms=None,
            last_ts=ep_last,
            extra={"write_count": ep_success},
        ),
        MemorySubsystemItem(
            subsystem="memory_guard",
            display_name="MemoryGuard 宪法守卫",
            total=guard_total,
            success=guard_total - guard_rejected,
            failed=guard_rejected,
            success_rate=(100.0 - guard_reject_rate) if guard_reject_rate is not None else None,
            p95_ms=None,
            last_ts=guard_last,
            extra={"reject_rate": guard_reject_rate, "rejected_count": guard_rejected},
        ),
        MemorySubsystemItem(
            subsystem="context_compressor",
            display_name="ContextCompressor 压缩",
            total=comp_total,
            success=comp_success,
            failed=comp_failed,
            success_rate=round(comp_success / comp_total * 100, 1) if comp_total > 0 else None,
            p95_ms=None,
            last_ts=comp_last,
            extra={"avg_compress_rate_pct": comp_rate},
        ),
        MemorySubsystemItem(
            subsystem="long_term_distiller",
            display_name="LongTermDistiller 蒸馏",
            total=distill_total,
            success=distill_success,
            failed=distill_failed,
            success_rate=distill_rate,
            p95_ms=None,
            last_ts=distill_last,
            extra={"distill_count": distill_success},
        ),
    ]

    return MemoryStatsResponse(
        window_minutes=window_minutes,
        subsystems=subsystems,
        total_writes=ep_success,
        guard_reject_rate=guard_reject_rate,
        compress_rate=comp_rate,
        distill_count=distill_success,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-30: GET /obs/vts/stats  — VTS 通道统计
# ---------------------------------------------------------------------------
class VtsEmotionItem(BaseModel):
    emotion_tag: str
    count: int
    pct: float

class VtsStatsResponse(BaseModel):
    window_minutes: int
    total_events: int
    applied: int
    degraded: int
    rejected: int
    apply_rate: Optional[float]
    degrade_rate: Optional[float]
    reject_rate: Optional[float]
    top_emotions: List[VtsEmotionItem]
    talk_gate_total: int
    talk_gate_allowed: int
    talk_gate_blocked: int
    timestamp: int

@router.get("/vts/stats", response_model=VtsStatsResponse)
async def get_vts_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-30: VTS 通道统计 — 表情应用率/降级率/拒绝率、Top 情感标签、Talk Gate 事件统计。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── VTS 表情事件统计 ──────────────────────────────────────────────────
    try:
        vts_rows = ledger.fetch_by_time_range("vts_event_log", start_ms=since_ms, end_ms=now)
    except Exception:
        vts_rows = []

    # 补充从 voice_event_log 中过滤 VTS 相关事件
    try:
        voice_rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now)
        vts_voice_rows = [
            r for r in voice_rows
            if str(r.get("event_type", "")).upper().startswith("VTS")
        ]
    except Exception:
        vts_voice_rows = []

    all_vts_rows = vts_rows + vts_voice_rows
    total_events = len(all_vts_rows)

    applied = sum(
        1 for r in all_vts_rows
        if str(r.get("status", "")).upper() in ("APPLIED", "OK", "SUCCESS")
        or str(r.get("result", "")).upper() == "APPLIED"
    )
    degraded = sum(
        1 for r in all_vts_rows
        if str(r.get("status", "")).upper() in ("DEGRADED", "FALLBACK")
        or str(r.get("result", "")).upper() == "DEGRADED"
    )
    rejected = sum(
        1 for r in all_vts_rows
        if str(r.get("status", "")).upper() in ("REJECTED", "BLOCKED", "FAILED")
        or str(r.get("result", "")).upper() == "REJECTED"
    )

    apply_rate = round(applied / total_events * 100, 1) if total_events > 0 else None
    degrade_rate = round(degraded / total_events * 100, 1) if total_events > 0 else None
    reject_rate = round(rejected / total_events * 100, 1) if total_events > 0 else None

    # Top 情感标签分布
    from collections import Counter
    emotion_counter = Counter(
        str(r.get("emotion_tag") or r.get("expression") or "UNKNOWN").upper()
        for r in all_vts_rows
    )
    top_emotions = [
        VtsEmotionItem(
            emotion_tag=tag,
            count=cnt,
            pct=round(cnt / total_events * 100, 1) if total_events > 0 else 0.0,
        )
        for tag, cnt in emotion_counter.most_common(8)
    ]

    # ── Talk Gate 事件统计 ────────────────────────────────────────────────
    try:
        talk_gate_rows = ledger.fetch_by_time_range("talk_gate_log", start_ms=since_ms, end_ms=now)
    except Exception:
        talk_gate_rows = []

    talk_gate_total = len(talk_gate_rows)
    talk_gate_allowed = sum(
        1 for r in talk_gate_rows
        if str(r.get("decision", "")).upper() in ("ALLOW", "ALLOWED", "APPROVE", "OK")
    )
    talk_gate_blocked = talk_gate_total - talk_gate_allowed

    return VtsStatsResponse(
        window_minutes=window_minutes,
        total_events=total_events,
        applied=applied,
        degraded=degraded,
        rejected=rejected,
        apply_rate=apply_rate,
        degrade_rate=degrade_rate,
        reject_rate=reject_rate,
        top_emotions=top_emotions,
        talk_gate_total=talk_gate_total,
        talk_gate_allowed=talk_gate_allowed,
        talk_gate_blocked=talk_gate_blocked,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-31: GET /obs/distillation/stats  — LongTermDistiller 蒸馏统计
# ---------------------------------------------------------------------------
class DistillationCycleItem(BaseModel):
    cycle_id: str
    topics_processed: int
    semantic_memories_created: int
    proposals_submitted: int
    proposals_skipped: int
    error_count: int
    ts_ms: int
    detail: Optional[str] = None

class DistillationStatsResponse(BaseModel):
    window_minutes: int
    total_cycles: int
    success_cycles: int
    failed_cycles: int
    success_rate: Optional[float]
    total_proposals_submitted: int
    total_semantic_created: int
    recent_cycles: List[DistillationCycleItem]
    timestamp: int

@router.get("/distillation/stats", response_model=DistillationStatsResponse)
async def get_distillation_stats(
    window_minutes: int = Query(1440, ge=1, le=43200, description="统计窗口（分钟，默认 24h）"),
    limit: int = Query(10, ge=1, le=50, description="最近循环条数"),
):
    """TODO-31: LongTermDistiller 蒸馏统计 — 循环次数、提案提交量、蒸馏成功率。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("distillation_cycle_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 补充从 distill_log 查询（兼容旧表名）
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("distill_log", start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

    total_cycles = len(rows)
    success_cycles = sum(
        1 for r in rows
        if (r.get("errors") in (None, [], "") or len(r.get("errors") or []) == 0)
        and int(r.get("proposals_submitted") or 0) >= 0
    )
    failed_cycles = total_cycles - success_cycles
    success_rate = round(success_cycles / total_cycles * 100, 1) if total_cycles > 0 else None

    total_proposals = sum(int(r.get("proposals_submitted") or 0) for r in rows)
    total_semantic = sum(int(r.get("semantic_memories_created") or 0) for r in rows)

    # 最近 N 条（倒序）
    recent_rows = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent_cycles = [
        DistillationCycleItem(
            cycle_id=str(r.get("cycle_id") or r.get("request_id") or ""),
            topics_processed=int(r.get("topics_processed") or 0),
            semantic_memories_created=int(r.get("semantic_memories_created") or 0),
            proposals_submitted=int(r.get("proposals_submitted") or 0),
            proposals_skipped=int(r.get("proposals_skipped") or 0),
            error_count=len(r.get("errors") or []),
            ts_ms=int(r.get("ts_ms") or 0),
            detail=(r.get("detail") or "")[:200],
        )
        for r in recent_rows
    ]

    return DistillationStatsResponse(
        window_minutes=window_minutes,
        total_cycles=total_cycles,
        success_cycles=success_cycles,
        failed_cycles=failed_cycles,
        success_rate=success_rate,
        total_proposals_submitted=total_proposals,
        total_semantic_created=total_semantic,
        recent_cycles=recent_cycles,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-37: GET /obs/wechat/stats  — WeChat 通道统计
# ---------------------------------------------------------------------------
class WeChatRateLimitItem(BaseModel):
    wxid: str
    message_count: int
    last_ts: Optional[int]
    rate_limited: bool

class WeChatEventItem(BaseModel):
    request_id: Optional[str]
    wxid: Optional[str]
    direction: Optional[str]  # "inbound" | "outbound"
    status: str
    reason_code: Optional[str]
    ts_ms: int

class WeChatStatsResponse(BaseModel):
    window_minutes: int
    channel_status: str  # "UP" | "DEGRADED" | "DOWN" | "UNKNOWN"
    total_events: int
    inbound: int
    outbound: int
    success: int
    failed: int
    success_rate: Optional[float]
    rate_limited_count: int
    top_wxids: List[WeChatRateLimitItem]
    recent_events: List[WeChatEventItem]
    last_event_ts: Optional[int]
    timestamp: int

@router.get("/wechat/stats", response_model=WeChatStatsResponse)
async def get_wechat_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    limit: int = Query(20, ge=1, le=100, description="最近事件条数"),
):
    """TODO-37: WeChat 通道统计 — 通道健康状态、频率限制统计、最近 channel_io_log 事件。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── channel_io_log 中过滤 WeChat 相关事件 ────────────────────────────
    try:
        channel_rows = ledger.fetch_by_time_range("channel_io_log", start_ms=since_ms, end_ms=now)
        wechat_rows = [
            r for r in channel_rows
            if str(r.get("channel") or r.get("source") or "").upper() in ("WECHAT", "WX", "WEIXIN")
            or str(r.get("event_type") or "").upper().startswith("WECHAT")
        ]
    except Exception:
        wechat_rows = []

    # 补充从 voice_event_log 中过滤 WECHAT 相关事件
    try:
        voice_rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now)
        wechat_voice_rows = [
            r for r in voice_rows
            if str(r.get("event_type") or "").upper().startswith("WECHAT")
        ]
    except Exception:
        wechat_voice_rows = []

    all_rows = wechat_rows + wechat_voice_rows
    total_events = len(all_rows)

    inbound = sum(
        1 for r in all_rows
        if str(r.get("direction") or r.get("event_type") or "").upper()
        in ("INBOUND", "RECEIVE", "RECV", "WECHAT_RECV")
    )
    outbound = total_events - inbound

    success = sum(
        1 for r in all_rows
        if str(r.get("status") or "").upper() in ("OK", "SUCCESS", "SENT", "DELIVERED")
    )
    failed = total_events - success
    success_rate = round(success / total_events * 100, 1) if total_events > 0 else None

    # 频率限制统计
    rate_limited_count = sum(
        1 for r in all_rows
        if str(r.get("reason_code") or "").upper().startswith("RATE_LIMIT")
        or str(r.get("status") or "").upper() == "RATE_LIMITED"
    )

    # Top wxid 统计（按消息量排序）
    from collections import Counter, defaultdict
    wxid_counter: Counter = Counter()
    wxid_last_ts: dict = defaultdict(int)
    wxid_rate_limited: dict = defaultdict(bool)
    for r in all_rows:
        wxid = str(r.get("wxid") or r.get("from_wxid") or r.get("session_id") or "unknown")
        wxid_counter[wxid] += 1
        ts = r.get("ts_ms") or 0
        if ts > wxid_last_ts[wxid]:
            wxid_last_ts[wxid] = ts
        if str(r.get("reason_code") or "").upper().startswith("RATE_LIMIT"):
            wxid_rate_limited[wxid] = True

    top_wxids = [
        WeChatRateLimitItem(
            wxid=wxid,
            message_count=cnt,
            last_ts=wxid_last_ts.get(wxid),
            rate_limited=bool(wxid_rate_limited.get(wxid, False)),
        )
        for wxid, cnt in wxid_counter.most_common(10)
    ]

    # 通道健康状态
    if total_events == 0:
        channel_status = "UNKNOWN"
    elif success_rate is not None and success_rate >= 80:
        channel_status = "UP"
    elif success_rate is not None and success_rate >= 50:
        channel_status = "DEGRADED"
    else:
        channel_status = "DOWN"

    # 最近事件列表（倒序）
    recent_sorted = sorted(all_rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent_events = [
        WeChatEventItem(
            request_id=r.get("request_id"),
            wxid=str(r.get("wxid") or r.get("from_wxid") or r.get("session_id") or ""),
            direction=(
                "inbound"
                if str(r.get("direction") or r.get("event_type") or "").upper()
                in ("INBOUND", "RECEIVE", "RECV", "WECHAT_RECV")
                else "outbound"
            ),
            status=str(r.get("status") or "UNKNOWN").upper(),
            reason_code=r.get("reason_code"),
            ts_ms=int(r.get("ts_ms") or 0),
        )
        for r in recent_sorted
    ]

    last_event_ts = max((r.get("ts_ms") or 0 for r in all_rows), default=None)

    return WeChatStatsResponse(
        window_minutes=window_minutes,
        channel_status=channel_status,
        total_events=total_events,
        inbound=inbound,
        outbound=outbound,
        success=success,
        failed=failed,
        success_rate=success_rate,
        rate_limited_count=rate_limited_count,
        top_wxids=top_wxids,
        recent_events=recent_events,
        last_event_ts=last_event_ts,
        timestamp=now,
    )


@router.post("/wechat/reset", response_model=Dict[str, Any])
async def reset_wechat_channel(
    reason: Optional[str] = Query(None, description="重置原因"),
):
    """TODO-37: 手动重置 WeChat 通道状态（清除频率限制计数器）。"""
    try:
        from host_brain.channels.wechat import WeChatChannel  # noqa: PLC0415
        _channel = WeChatChannel.get_instance()
        if _channel is not None and hasattr(_channel, "reset_rate_limits"):
            _channel.reset_rate_limits()
            return {
                "status": "OK",
                "reason_code": "WECHAT_CHANNEL_RESET",
                "detail": f"WeChat 通道频率限制已重置（原因：{reason or 'manual'}）",
                "timestamp": _now_ms(),
            }
    except (ImportError, AttributeError):
        pass
    except Exception as exc:
        logger.warning("WeChat 通道重置失败: %s", exc)
        return {
            "status": "DEGRADED",
            "reason_code": "WECHAT_RESET_FAILED",
            "detail": str(exc)[:200],
            "timestamp": _now_ms(),
        }
    return {
        "status": "OK",
        "reason_code": "WECHAT_CHANNEL_RESET_NOOP",
        "detail": "WeChat 通道重置（无状态实例，已记录）",
        "timestamp": _now_ms(),
    }


# ---------------------------------------------------------------------------
# TODO-34: GET /obs/retention/stats  — AuditRetention 保留策略统计
# ---------------------------------------------------------------------------
class RetentionTableItem(BaseModel):
    table: str
    candidate_count: int
    deleted_count: int
    protected: bool

class RetentionStatsResponse(BaseModel):
    last_run_ts: Optional[int]
    last_run_status: Optional[str]
    total_deleted: int
    total_candidates: int
    total_protected: int
    tables: List[RetentionTableItem]
    timestamp: int

@router.get("/retention/stats", response_model=RetentionStatsResponse)
async def get_retention_stats(
    limit: int = Query(5, ge=1, le=20, description="最近执行记录条数"),
):
    """TODO-34: AuditRetention 保留策略执行统计。"""
    ledger = _get_ledger()
    now = _now_ms()

    # 从 error_log 中查询 S_AUDIT_RETENTION 阶段的记录
    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=0, end_ms=now)
        retention_rows = [
            r for r in rows
            if str(r.get("stage") or "").upper() == "S_AUDIT_RETENTION"
        ]
    except Exception:
        retention_rows = []

    # 取最近一次执行记录
    recent = sorted(retention_rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    last_run_ts = recent[0].get("ts_ms") if recent else None
    last_run_status = str(recent[0].get("reason_code") or "UNKNOWN") if recent else None

    # 解析最近一次执行的详情
    tables: list[RetentionTableItem] = []
    total_deleted = 0
    total_candidates = 0
    total_protected = 0

    if recent:
        try:
            detail_raw = recent[0].get("detail") or "{}"
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
            candidate_counts = dict(detail.get("candidate_counts") or {})
            deleted_counts = dict(detail.get("deleted_counts") or {})
            protected_counts = dict(detail.get("protected_counts") or {})

            all_tables = set(candidate_counts) | set(deleted_counts) | set(protected_counts)
            for tbl in sorted(all_tables):
                cand = int(candidate_counts.get(tbl) or 0)
                deleted = int(deleted_counts.get(tbl) or 0)
                protected = tbl in protected_counts
                tables.append(RetentionTableItem(
                    table=tbl,
                    candidate_count=cand,
                    deleted_count=deleted,
                    protected=protected,
                ))
                total_candidates += cand
                total_deleted += deleted
                total_protected += int(protected_counts.get(tbl) or 0)
        except Exception:
            pass

    return RetentionStatsResponse(
        last_run_ts=last_run_ts,
        last_run_status=last_run_status,
        total_deleted=total_deleted,
        total_candidates=total_candidates,
        total_protected=total_protected,
        tables=tables,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-40: GET /obs/qa/stats  — QA 违规统计
# ---------------------------------------------------------------------------
class QaViolationCodeItem(BaseModel):
    violation_code: str
    count: int
    pct: float

class QaStatsResponse(BaseModel):
    window_minutes: int
    total_violations: int
    degraded_count: int
    rewritten_count: int
    degrade_rate: Optional[float]
    rewrite_success_rate: Optional[float]
    violation_codes: List[QaViolationCodeItem]
    timeseries_labels: List[str]
    timeseries_counts: List[int]
    recent_violations: List[Dict[str, Any]]
    timestamp: int

@router.get("/qa/stats", response_model=QaStatsResponse)
async def get_qa_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
    limit: int = Query(20, ge=1, le=100, description="最近违规条数"),
):
    """TODO-40: QA 违规统计 — 违规码分布、降级率、重写成功率、时序趋势、最近违规列表。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("qa_violation_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 兼容旧表名 qa_violation
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("qa_violation", start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

    total = len(rows)
    degraded_count = sum(
        1 for r in rows
        if str(r.get("action") or r.get("result") or "").upper() in ("DEGRADED", "FALLBACK", "DOWNGRADE")
    )
    rewritten_count = sum(
        1 for r in rows
        if str(r.get("action") or r.get("result") or "").upper() in ("REWRITTEN", "REWRITE", "PATCHED")
    )
    degrade_rate = round(degraded_count / total * 100, 1) if total > 0 else None
    rewrite_success_rate = round(rewritten_count / (degraded_count + rewritten_count) * 100, 1) if (degraded_count + rewritten_count) > 0 else None

    # 违规码分布
    from collections import Counter
    code_counter = Counter(
        str(r.get("violation_code") or r.get("reason_code") or "UNKNOWN").upper()
        for r in rows
    )
    violation_codes = [
        QaViolationCodeItem(
            violation_code=code,
            count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
        )
        for code, cnt in code_counter.most_common(10)
    ]

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_counts = [0] * bucket_count
    for r in rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            ts_counts[idx] += 1

    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    # 最近 N 条（倒序）
    recent_sorted = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent_violations = [
        {
            "request_id": r.get("request_id"),
            "node_id": r.get("node_id"),
            "violation_code": str(r.get("violation_code") or r.get("reason_code") or "UNKNOWN").upper(),
            "action": str(r.get("action") or r.get("result") or "").upper() or None,
            "detail": (r.get("detail") or "")[:200],
            "ts_ms": r.get("ts_ms"),
        }
        for r in recent_sorted
    ]

    return QaStatsResponse(
        window_minutes=window_minutes,
        total_violations=total,
        degraded_count=degraded_count,
        rewritten_count=rewritten_count,
        degrade_rate=degrade_rate,
        rewrite_success_rate=rewrite_success_rate,
        violation_codes=violation_codes,
        timeseries_labels=labels,
        timeseries_counts=ts_counts,
        recent_violations=recent_violations,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-41: GET /obs/llm/call-stats  — LLM 调用成本统计
# ---------------------------------------------------------------------------
class LlmModelCallItem(BaseModel):
    model_id: str
    node_id: Optional[str]
    call_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    avg_latency_ms: Optional[float]
    p95_latency_ms: Optional[int]

class LlmCallStatsResponse(BaseModel):
    window_minutes: int
    total_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    avg_latency_ms: Optional[float]
    models: List[LlmModelCallItem]
    timeseries_labels: List[str]
    timeseries_calls: List[int]
    timeseries_tokens: List[int]
    timestamp: int

@router.get("/llm/call-stats", response_model=LlmCallStatsResponse)
async def get_llm_call_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
):
    """TODO-41: LLM 调用成本统计 — 按 model_id 分组的 token 消耗/平均延迟/调用次数。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("llm_call_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 兼容 llm_node_call_log 表名
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("llm_node_call_log", start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

    total_calls = len(rows)
    total_prompt = sum(int(r.get("prompt_tokens") or 0) for r in rows)
    total_completion = sum(int(r.get("completion_tokens") or 0) for r in rows)
    total_tokens = total_prompt + total_completion
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else None

    # 按 model_id 分组
    from collections import defaultdict
    model_map: Dict[str, List] = defaultdict(list)
    for r in rows:
        mid = str(r.get("model_id") or r.get("model") or "unknown")
        model_map[mid].append(r)

    models = []
    for mid, mrows in sorted(model_map.items()):
        m_prompt = sum(int(r.get("prompt_tokens") or 0) for r in mrows)
        m_completion = sum(int(r.get("completion_tokens") or 0) for r in mrows)
        m_latencies = [r["latency_ms"] for r in mrows if r.get("latency_ms") is not None]
        m_avg_lat = round(sum(m_latencies) / len(m_latencies), 1) if m_latencies else None
        m_p95 = None
        if m_latencies:
            sl = sorted(m_latencies)
            m_p95 = sl[max(0, int(len(sl) * 0.95) - 1)]
        node_ids = list({str(r.get("node_id") or "") for r in mrows if r.get("node_id")})
        models.append(LlmModelCallItem(
            model_id=mid,
            node_id=node_ids[0] if len(node_ids) == 1 else None,
            call_count=len(mrows),
            total_prompt_tokens=m_prompt,
            total_completion_tokens=m_completion,
            total_tokens=m_prompt + m_completion,
            avg_latency_ms=m_avg_lat,
            p95_latency_ms=m_p95,
        ))

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_calls = [0] * bucket_count
    ts_tokens = [0] * bucket_count
    for r in rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            ts_calls[idx] += 1
            ts_tokens[idx] += int(r.get("prompt_tokens") or 0) + int(r.get("completion_tokens") or 0)

    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    return LlmCallStatsResponse(
        window_minutes=window_minutes,
        total_calls=total_calls,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        total_tokens=total_tokens,
        avg_latency_ms=avg_latency,
        models=models,
        timeseries_labels=labels,
        timeseries_calls=ts_calls,
        timeseries_tokens=ts_tokens,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-42: GET /obs/checkpoints  — CheckpointManager 状态查询
# ---------------------------------------------------------------------------
class CheckpointItem(BaseModel):
    checkpoint_id: str
    request_id: Optional[str]
    interrupt_reason: Optional[str]
    resume_deadline_at: Optional[int]
    created_at_ms: Optional[int]
    is_expired: bool
    payload_summary: Optional[str]

class CheckpointsResponse(BaseModel):
    total: int
    items: List[CheckpointItem]
    timestamp: int

class CheckpointDetailResponse(BaseModel):
    checkpoint_id: str
    request_id: Optional[str]
    interrupt_reason: Optional[str]
    resume_deadline_at: Optional[int]
    created_at_ms: Optional[int]
    is_expired: bool
    payload: Optional[Any]
    timestamp: int

@router.get("/checkpoints", response_model=CheckpointsResponse)
async def get_checkpoints(
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
    include_expired: bool = Query(False, description="是否包含已超时的 checkpoint"),
):
    """TODO-42: 最近 checkpoint 列表（checkpoint_id/request_id/interrupt_reason/resume_deadline_at/是否已超时）。"""
    ledger = _get_ledger()
    now = _now_ms()

    try:
        rows = ledger.fetch_by_time_range("checkpoints", start_ms=0, end_ms=now)
    except Exception:
        rows = []

    # 兼容 graph_checkpoint 表名
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("graph_checkpoint", start_ms=0, end_ms=now)
        except Exception:
            rows = []

    # 按创建时间倒序
    rows_sorted = sorted(rows, key=lambda r: r.get("created_at_ms") or r.get("ts_ms") or 0, reverse=True)

    items = []
    for r in rows_sorted:
        deadline = r.get("resume_deadline_at")
        is_expired = bool(deadline and int(deadline) < now)
        if not include_expired and is_expired:
            continue
        payload_raw = r.get("payload") or r.get("payload_json")
        payload_summary = None
        if payload_raw:
            try:
                ps = json.dumps(payload_raw) if not isinstance(payload_raw, str) else payload_raw
                payload_summary = ps[:120] + "…" if len(ps) > 120 else ps
            except Exception:
                pass
        items.append(CheckpointItem(
            checkpoint_id=str(r.get("checkpoint_id") or r.get("id") or ""),
            request_id=r.get("request_id"),
            interrupt_reason=r.get("interrupt_reason"),
            resume_deadline_at=int(deadline) if deadline else None,
            created_at_ms=int(r.get("created_at_ms") or r.get("ts_ms") or 0),
            is_expired=is_expired,
            payload_summary=payload_summary,
        ))
        if len(items) >= limit:
            break

    return CheckpointsResponse(total=len(items), items=items, timestamp=now)


@router.get("/checkpoints/{checkpoint_id}", response_model=CheckpointDetailResponse)
async def get_checkpoint_detail(checkpoint_id: str):
    """TODO-42: 单个 checkpoint 详情（含完整 payload）。"""
    ledger = _get_ledger()
    now = _now_ms()

    rows = []
    for table in ("checkpoints", "graph_checkpoint"):
        try:
            all_rows = ledger.fetch_by_time_range(table, start_ms=0, end_ms=now)
            rows = [
                r for r in all_rows
                if str(r.get("checkpoint_id") or r.get("id") or "") == checkpoint_id
            ]
            if rows:
                break
        except Exception:
            continue

    if not rows:
        raise HTTPException(
            status_code=404,
            detail={"error": "checkpoint_not_found", "checkpoint_id": checkpoint_id},
        )

    r = rows[0]
    deadline = r.get("resume_deadline_at")
    is_expired = bool(deadline and int(deadline) < now)
    payload_raw = r.get("payload") or r.get("payload_json")
    payload = _safe_json(payload_raw) if payload_raw else None

    return CheckpointDetailResponse(
        checkpoint_id=checkpoint_id,
        request_id=r.get("request_id"),
        interrupt_reason=r.get("interrupt_reason"),
        resume_deadline_at=int(deadline) if deadline else None,
        created_at_ms=int(r.get("created_at_ms") or r.get("ts_ms") or 0),
        is_expired=is_expired,
        payload=payload,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-43: GET /obs/rate-limit/backend-stats  — RateLimitStore 后端状态
# ---------------------------------------------------------------------------
class RateLimitKeyItem(BaseModel):
    key: str
    current_count: int
    ttl_ms: Optional[int]
    window_ms: Optional[int]

class RateLimitBackendStatsResponse(BaseModel):
    backend_type: str          # "memory" | "sqlite" | "redis"
    is_degraded: bool
    degrade_reason: Optional[str]
    total_keys: int
    active_keys: int
    top_keys: List[RateLimitKeyItem]
    timestamp: int

@router.get("/rate-limit/backend-stats", response_model=RateLimitBackendStatsResponse)
async def get_rate_limit_backend_stats(
    top_k: int = Query(20, ge=1, le=100, description="返回 Top-K 活跃 key"),
):
    """TODO-43: RateLimitStore 后端状态 — 当前后端类型/是否降级/各 key 当前计数/TTL。"""
    ledger = _get_ledger()
    now = _now_ms()

    # 尝试从 rate_limit_store_log 读取最新状态快照
    backend_type = "memory"
    is_degraded = False
    degrade_reason = None
    top_keys: List[RateLimitKeyItem] = []

    try:
        rows = ledger.fetch_by_time_range("rate_limit_store_log", start_ms=0, end_ms=now)
        if rows:
            latest = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[0]
            backend_type = str(latest.get("backend_type") or "memory")
            is_degraded = bool(latest.get("is_degraded") or False)
            degrade_reason = latest.get("degrade_reason")
    except Exception:
        pass

    # 从 governor_log 中统计各 key 的速率限制计数（近 5 分钟）
    try:
        since_5m = now - 5 * 60 * 1000
        gov_rows = ledger.fetch_by_time_range("governor_log", start_ms=since_5m, end_ms=now)
        rl_rows = [
            r for r in gov_rows
            if str(r.get("reason_code") or "").upper().startswith("RATE_LIMIT")
        ]
        from collections import Counter
        key_counter = Counter(
            str(r.get("session_id") or r.get("wxid") or r.get("user_id") or "unknown")
            for r in rl_rows
        )
        top_keys = [
            RateLimitKeyItem(
                key=k,
                current_count=cnt,
                ttl_ms=None,
                window_ms=5 * 60 * 1000,
            )
            for k, cnt in key_counter.most_common(top_k)
        ]
    except Exception:
        pass

    total_keys = len(top_keys)
    active_keys = sum(1 for k in top_keys if k.current_count > 0)

    return RateLimitBackendStatsResponse(
        backend_type=backend_type,
        is_degraded=is_degraded,
        degrade_reason=degrade_reason,
        total_keys=total_keys,
        active_keys=active_keys,
        top_keys=top_keys,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-44: GET /obs/vault/stats  — ArtifactVault Manifest 统计
# ---------------------------------------------------------------------------
class VaultManifestItem(BaseModel):
    manifest_id: str
    artifact_type: Optional[str]
    status: str          # "ACTIVE" | "UNDONE" | "GC"
    created_at_ms: Optional[int]
    undone_at_ms: Optional[int]
    gc_at_ms: Optional[int]
    is_reversible: bool
    summary: Optional[str]

class VaultStatsResponse(BaseModel):
    total_manifests: int
    active_count: int
    undone_count: int
    gc_count: int
    reversible_count: int
    reversible_rate: Optional[float]
    recent_manifests: List[VaultManifestItem]
    timestamp: int

@router.get("/vault/stats", response_model=VaultStatsResponse)
async def get_vault_stats(
    limit: int = Query(20, ge=1, le=100, description="最近 manifest 条数"),
):
    """TODO-44: ArtifactVault Manifest 统计 — ACTIVE/UNDONE/GC 数量、可逆比例、最近 manifest 列表。数据来自 vault_manifest_index。"""
    ledger = _get_ledger()
    now = _now_ms()

    rows = []
    try:
        # 表名为 vault_manifest_index（与 ledger_schema / _ALLOWED_TABLES 一致）
        rows = ledger.fetch_by_time_range(
            "vault_manifest_index",
            start_ms=0,
            end_ms=now,
            limit=10_000,
        )
    except Exception as e:
        logger.warning("get_vault_stats fetch vault_manifest_index failed: %s", e)

    total = len(rows)
    active_count = sum(1 for r in rows if str(r.get("status") or "").upper() == "ACTIVE")
    undone_count = sum(1 for r in rows if str(r.get("status") or "").upper() in ("UNDONE", "ROLLED_BACK"))
    gc_count = sum(1 for r in rows if str(r.get("status") or "").upper() in ("GC", "GARBAGE_COLLECTED", "DELETED"))
    reversible_count = sum(1 for r in rows if bool(r.get("is_reversible") or r.get("reversible")))
    reversible_rate = round(reversible_count / total * 100, 1) if total > 0 else None

    # 最近 N 条（按 ts_ms 倒序）；vault_manifest_index 字段: manifest_id, status, ts_ms, reversible, skill_name, manifest_path
    recent_sorted = sorted(rows, key=lambda r: int(r.get("ts_ms") or 0), reverse=True)[:limit]
    recent_manifests = [
        VaultManifestItem(
            manifest_id=str(r.get("manifest_id") or r.get("id") or ""),
            artifact_type=r.get("skill_name") or r.get("artifact_type"),
            status=str(r.get("status") or "UNKNOWN").upper(),
            created_at_ms=int(r.get("ts_ms") or 0) or None,
            undone_at_ms=int(r.get("undone_at_ms") or 0) or None,
            gc_at_ms=int(r.get("gc_at_ms") or 0) or None,
            is_reversible=bool(r.get("is_reversible") or r.get("reversible")),
            summary=(r.get("manifest_path") or r.get("skill_name") or r.get("summary") or "")[:120] or None,
        )
        for r in recent_sorted
    ]

    return VaultStatsResponse(
        total_manifests=total,
        active_count=active_count,
        undone_count=undone_count,
        gc_count=gc_count,
        reversible_count=reversible_count,
        reversible_rate=reversible_rate,
        recent_manifests=recent_manifests,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-47: GET /obs/llm/route-stats  — RoutingMatrix 路由决策统计
# ---------------------------------------------------------------------------
class RouteModelItem(BaseModel):
    model_target: str
    select_count: int
    fallback_count: int
    fallback_rate: Optional[float]

class RouteTaskTypeItem(BaseModel):
    task_type: str
    count: int
    pct: float

class LlmRouteStatsResponse(BaseModel):
    window_minutes: int
    total_decisions: int
    fallback_count: int
    fallback_rate: Optional[float]
    models: List[RouteModelItem]
    task_types: List[RouteTaskTypeItem]
    timestamp: int

@router.get("/llm/route-stats", response_model=LlmRouteStatsResponse)
async def get_llm_route_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-47: RoutingMatrix 路由决策统计 — 按 model_id 分组的选择次数/降级次数/降级率/task_type 分布。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("llm_route_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    total = len(rows)
    fallback_count = sum(1 for r in rows if bool(r.get("is_fallback")))
    fallback_rate = round(fallback_count / total * 100, 1) if total > 0 else None

    # 按 model_target 分组
    from collections import Counter, defaultdict
    model_select: Counter = Counter()
    model_fallback: Counter = Counter()
    for r in rows:
        mid = str(r.get("selected_model") or r.get("model_target") or "unknown")
        model_select[mid] += 1
        if bool(r.get("is_fallback")):
            model_fallback[mid] += 1

    models = [
        RouteModelItem(
            model_target=mid,
            select_count=cnt,
            fallback_count=model_fallback.get(mid, 0),
            fallback_rate=round(model_fallback.get(mid, 0) / cnt * 100, 1) if cnt > 0 else None,
        )
        for mid, cnt in model_select.most_common()
    ]

    # task_type 分布
    task_counter: Counter = Counter(
        str(r.get("task_type") or "UNKNOWN").upper() for r in rows
    )
    task_types = [
        RouteTaskTypeItem(
            task_type=tt,
            count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
        )
        for tt, cnt in task_counter.most_common(10)
    ]

    return LlmRouteStatsResponse(
        window_minutes=window_minutes,
        total_decisions=total,
        fallback_count=fallback_count,
        fallback_rate=fallback_rate,
        models=models,
        task_types=task_types,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-48: GET /obs/input/dispatch-stats  — InputDispatch 生命周期统计
# ---------------------------------------------------------------------------
class InputSourceItem(BaseModel):
    source: str
    accepted: int
    completed: int
    failed: int
    not_wired: int
    success_rate: Optional[float]
    avg_latency_ms: Optional[float]

class InputDispatchStatsResponse(BaseModel):
    window_minutes: int
    total_accepted: int
    total_completed: int
    total_failed: int
    total_not_wired: int
    overall_success_rate: Optional[float]
    sources: List[InputSourceItem]
    timeseries_labels: List[str]
    timeseries_accepted: List[int]
    timeseries_completed: List[int]
    timestamp: int

@router.get("/input/dispatch-stats", response_model=InputDispatchStatsResponse)
async def get_input_dispatch_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
):
    """TODO-48: InputDispatch 生命周期统计 — 按 source 分组的 ACCEPTED/COMPLETED/FAILED/NOT_WIRED 数量、成功率、平均延迟。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # InputDispatch 事件实际落在 voice_event_log，并且 source/latency_ms 写在 payload 内。
    try:
        rows = ledger.fetch_by_time_range(
            "voice_event_log",
            start_ms=since_ms,
            end_ms=now,
            limit=10_000,
        )
        dispatch_rows = [r for r in rows if _is_input_dispatch_event(r)]
    except Exception:
        dispatch_rows = []

    # 按 source 分组统计
    from collections import defaultdict
    source_stats: dict = defaultdict(lambda: {"accepted": 0, "completed": 0, "failed": 0, "not_wired": 0, "latencies": []})

    for r in dispatch_rows:
        evt = str(r.get("event_type") or r.get("status") or "").upper()
        payload = _safe_json(r.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        src = _normalize_input_dispatch_source(
            payload_dict.get("source") or r.get("source") or r.get("channel"),
            evt,
        )

        if "ACCEPTED" in evt:
            source_stats[src]["accepted"] += 1
        elif "COMPLETED" in evt or "DONE" in evt:
            source_stats[src]["completed"] += 1
            latency_ms = _extract_input_dispatch_latency_ms(r, payload_dict)
            if latency_ms is not None:
                source_stats[src]["latencies"].append(latency_ms)
        elif "FAILED" in evt or "ERROR" in evt:
            source_stats[src]["failed"] += 1
        elif "NOT_WIRED" in evt:
            source_stats[src]["not_wired"] += 1

    total_accepted = sum(v["accepted"] for v in source_stats.values())
    total_completed = sum(v["completed"] for v in source_stats.values())
    total_failed = sum(v["failed"] for v in source_stats.values())
    total_not_wired = sum(v["not_wired"] for v in source_stats.values())
    total_dispatched = total_completed + total_failed + total_not_wired
    overall_success_rate = round(total_completed / total_dispatched * 100, 1) if total_dispatched > 0 else None

    sources = []
    for src, stats in sorted(source_stats.items()):
        dispatched = stats["completed"] + stats["failed"] + stats["not_wired"]
        sr = round(stats["completed"] / dispatched * 100, 1) if dispatched > 0 else None
        avg_lat = round(sum(stats["latencies"]) / len(stats["latencies"]), 1) if stats["latencies"] else None
        sources.append(InputSourceItem(
            source=src,
            accepted=stats["accepted"],
            completed=stats["completed"],
            failed=stats["failed"],
            not_wired=stats["not_wired"],
            success_rate=sr,
            avg_latency_ms=avg_lat,
        ))

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_accepted = [0] * bucket_count
    ts_completed = [0] * bucket_count
    for r in dispatch_rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            evt = str(r.get("event_type") or r.get("status") or "").upper()
            if "ACCEPTED" in evt:
                ts_accepted[idx] += 1
            elif "COMPLETED" in evt or "DONE" in evt:
                ts_completed[idx] += 1

    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    return InputDispatchStatsResponse(
        window_minutes=window_minutes,
        total_accepted=total_accepted,
        total_completed=total_completed,
        total_failed=total_failed,
        total_not_wired=total_not_wired,
        overall_success_rate=overall_success_rate,
        sources=sources,
        timeseries_labels=labels,
        timeseries_accepted=ts_accepted,
        timeseries_completed=ts_completed,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-49: GET /obs/recall-pack/stats  — RecallPack token 预算统计
# ---------------------------------------------------------------------------
class RecallPackSlotItem(BaseModel):
    slot: str
    avg_tokens: float
    truncation_rate: Optional[float]
    conflict_suppressed_count: int

class RecallPackStatsResponse(BaseModel):
    window_minutes: int
    total_packs: int
    avg_total_tokens: Optional[float]
    truncation_rate: Optional[float]
    conflict_rate: Optional[float]
    slots: List[RecallPackSlotItem]
    timestamp: int

@router.get("/recall-pack/stats", response_model=RecallPackStatsResponse)
async def get_recall_pack_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-49: RecallPack token 预算统计 — 各槽位平均 token 消耗/截断率/冲突抑制率。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # Read recall-pack traces from audit/error storage.
    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
        pack_rows = [
            r for r in rows
            if str(r.get("stage") or "").upper() in ("RECALL_PACK_TRACE", "RECALL_FUSION")
            and r.get("detail")
        ]
    except Exception:
        pack_rows = []

    # Read from the dedicated recall-pack log table when present.
    if not pack_rows:
        try:
            pack_rows = ledger.fetch_by_time_range("recall_pack_log", start_ms=since_ms, end_ms=now)
        except Exception:
            pack_rows = []

    total_packs = len(pack_rows)
    if total_packs == 0:
        return RecallPackStatsResponse(
            window_minutes=window_minutes,
            total_packs=0,
            avg_total_tokens=None,
            truncation_rate=None,
            conflict_rate=None,
            slots=[],
            timestamp=now,
        )

    # 解析 detail JSON，聚合各槽位统计
    from collections import defaultdict
    slot_tokens: dict = defaultdict(list)
    slot_truncated: dict = defaultdict(int)
    slot_conflict: dict = defaultdict(int)
    total_tokens_list: list = []
    conflict_pack_count = 0

    for r in pack_rows:
        try:
            detail_raw = r.get("detail") or "{}"
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
            trace = detail.get("trace") or []
            total_tokens = int(detail.get("total_tokens") or 0)
            if total_tokens > 0:
                total_tokens_list.append(total_tokens)
            conflict_events = int(detail.get("conflict_events") or 0)
            if conflict_events > 0:
                conflict_pack_count += 1
            for slot_info in trace:
                slot_name = str(slot_info.get("slot") or "unknown")
                actual_tokens = int(slot_info.get("actual_tokens") or 0)
                slot_tokens[slot_name].append(actual_tokens)
                if slot_info.get("truncated"):
                    slot_truncated[slot_name] += 1
                if slot_info.get("conflict_suppressed"):
                    slot_conflict[slot_name] += 1
        except Exception:
            continue

    avg_total_tokens = round(sum(total_tokens_list) / len(total_tokens_list), 1) if total_tokens_list else None
    truncation_rate = None
    if slot_tokens:
        total_slots = sum(len(v) for v in slot_tokens.values())
        total_truncated = sum(slot_truncated.values())
        truncation_rate = round(total_truncated / total_slots * 100, 1) if total_slots > 0 else None
    conflict_rate = round(conflict_pack_count / total_packs * 100, 1) if total_packs > 0 else None

    slots = [
        RecallPackSlotItem(
            slot=slot_name,
            avg_tokens=round(sum(tokens) / len(tokens), 1) if tokens else 0.0,
            truncation_rate=round(slot_truncated.get(slot_name, 0) / len(tokens) * 100, 1) if tokens else None,
            conflict_suppressed_count=slot_conflict.get(slot_name, 0),
        )
        for slot_name, tokens in sorted(slot_tokens.items())
    ]

    return RecallPackStatsResponse(
        window_minutes=window_minutes,
        total_packs=total_packs,
        avg_total_tokens=avg_total_tokens,
        truncation_rate=truncation_rate,
        conflict_rate=conflict_rate,
        slots=slots,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-50: GET /obs/ui-assertion/stats  — UIAssertionGuard 统计
# ---------------------------------------------------------------------------
class UIAssertionEventItem(BaseModel):
    audit_event: str
    count: int
    pct: float

class UIAssertionStatsResponse(BaseModel):
    window_minutes: int
    total_assertions: int
    sensitive_blocked: int
    precondition_failed: int
    postcondition_failed: int
    block_rate: Optional[float]
    top_events: List[UIAssertionEventItem]
    top_failed_reasons: List[Dict[str, Any]]
    timestamp: int

@router.get("/ui-assertion/stats", response_model=UIAssertionStatsResponse)
async def get_ui_assertion_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-50: UIAssertionGuard 统计 — SENSITIVE_UI_BLOCKED/UI_PRECONDITION_FAILED/UI_POSTCONDITION_FAILED 分布。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # 从 error_log 中查询 UI 断言相关事件
    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
        ui_rows = [
            r for r in rows
            if str(r.get("reason_code") or r.get("stage") or "").upper() in (
                "SENSITIVE_UI_BLOCKED",
                "UI_PRECONDITION_FAILED",
                "UI_POSTCONDITION_FAILED",
            )
        ]
    except Exception:
        ui_rows = []

    # 兼容 ui_assertion_log 表名
    if not ui_rows:
        try:
            ui_rows = ledger.fetch_by_time_range("ui_assertion_log", start_ms=since_ms, end_ms=now)
        except Exception:
            ui_rows = []

    total = len(ui_rows)
    sensitive_blocked = sum(
        1 for r in ui_rows
        if str(r.get("reason_code") or r.get("audit_event") or "").upper() == "SENSITIVE_UI_BLOCKED"
    )
    precondition_failed = sum(
        1 for r in ui_rows
        if str(r.get("reason_code") or r.get("audit_event") or "").upper() == "UI_PRECONDITION_FAILED"
    )
    postcondition_failed = sum(
        1 for r in ui_rows
        if str(r.get("reason_code") or r.get("audit_event") or "").upper() == "UI_POSTCONDITION_FAILED"
    )
    block_rate = round(sensitive_blocked / total * 100, 1) if total > 0 else None

    # 事件分布
    from collections import Counter
    event_counter = Counter(
        str(r.get("reason_code") or r.get("audit_event") or "UNKNOWN").upper()
        for r in ui_rows
    )
    top_events = [
        UIAssertionEventItem(
            audit_event=evt,
            count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
        )
        for evt, cnt in event_counter.most_common(10)
    ]

    # 前置断言失败原因分布（从 detail 中解析 failed_assertions）
    failed_reason_counter: Counter = Counter()
    for r in ui_rows:
        if str(r.get("reason_code") or "").upper() != "UI_PRECONDITION_FAILED":
            continue
        try:
            detail_raw = r.get("detail") or "{}"
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
            for fa in (detail.get("failed_assertions") or []):
                failed_reason_counter[str(fa)] += 1
        except Exception:
            pass

    top_failed_reasons = [
        {"reason": reason, "count": cnt}
        for reason, cnt in failed_reason_counter.most_common(5)
    ]

    return UIAssertionStatsResponse(
        window_minutes=window_minutes,
        total_assertions=total,
        sensitive_blocked=sensitive_blocked,
        precondition_failed=precondition_failed,
        postcondition_failed=postcondition_failed,
        block_rate=block_rate,
        top_events=top_events,
        top_failed_reasons=top_failed_reasons,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-53: GET /obs/failure/stats  — FailureMatrix 失败事件统计
# ---------------------------------------------------------------------------
class FailureEventItem(BaseModel):
    event: str
    count: int
    pct: float
    degraded_mode: Optional[str]

class FailureStatsResponse(BaseModel):
    window_minutes: int
    total_failures: int
    abort_count: int
    degraded_count: int
    abort_rate: Optional[float]
    top_events: List[FailureEventItem]
    hourly_trend: List[Dict[str, Any]]
    timestamp: int

@router.get("/failure/stats", response_model=FailureStatsResponse)
async def get_failure_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-53: FailureMatrix 失败事件统计 — 7 种失败事件分布、中止率、降级模式分布。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("failure_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 FailureHandler 处理的事件（通过 audit_tag 前缀识别）
    _failure_events = {
        "GOVERNOR_BLOCK", "EVIDENCE_EMPTY", "MODEL_FALLBACK",
        "QA_REWRITE", "EDGE_UNREACHABLE", "VISION_LOW_CONFIDENCE", "MODULE_TIMEOUT",
    }
    failure_rows = [
        r for r in rows
        if str(r.get("reason_code") or r.get("event") or r.get("stage") or "").upper()
        in _failure_events
    ]

    total = len(failure_rows)
    from collections import Counter, defaultdict

    event_counter: Counter = Counter()
    degraded_mode_counter: Counter = Counter()
    abort_count = 0

    _abort_events = {"GOVERNOR_BLOCK", "VISION_LOW_CONFIDENCE", "MODULE_TIMEOUT"}
    _degraded_mode_map = {
        "EVIDENCE_EMPTY": "conservative_answer",
        "MODEL_FALLBACK": "fallback_model",
        "QA_REWRITE": "rewritten_output",
        "EDGE_UNREACHABLE": "text_only",
    }

    for r in failure_rows:
        evt = str(r.get("reason_code") or r.get("event") or r.get("stage") or "UNKNOWN").upper()
        event_counter[evt] += 1
        if evt in _abort_events:
            abort_count += 1
        mode = _degraded_mode_map.get(evt)
        if mode:
            degraded_mode_counter[mode] += 1

    degraded_count = sum(degraded_mode_counter.values())
    abort_rate = round(abort_count / total * 100, 1) if total > 0 else None

    top_events = [
        FailureEventItem(
            event=evt,
            count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
            degraded_mode=_degraded_mode_map.get(evt),
        )
        for evt, cnt in event_counter.most_common(10)
    ]

    # 小时趋势（按小时分桶）
    hourly_buckets: dict = defaultdict(int)
    for r in failure_rows:
        ts = int(r.get("ts_ms") or r.get("created_at_ms") or 0)
        if ts > 0:
            hour_key = ts // (3600 * 1000)
            hourly_buckets[hour_key] += 1
    hourly_trend = [
        {"hour_ts_ms": h * 3600 * 1000, "count": c}
        for h, c in sorted(hourly_buckets.items())[-24:]
    ]

    return FailureStatsResponse(
        window_minutes=window_minutes,
        total_failures=total,
        abort_count=abort_count,
        degraded_count=degraded_count,
        abort_rate=abort_rate,
        top_events=top_events,
        hourly_trend=hourly_trend,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-54: GET /obs/permission/stats  — PermissionGuard 权限拒绝统计
# ---------------------------------------------------------------------------
class PermissionDeniedItem(BaseModel):
    tool_name: str
    deny_count: int
    pct: float
    proposer_class: Optional[str]

class PermissionStatsResponse(BaseModel):
    window_minutes: int
    total_checks: int
    allowed_count: int
    denied_count: int
    deny_rate: Optional[float]
    scheduler_denied_count: int
    top_denied_tools: List[PermissionDeniedItem]
    timestamp: int

@router.get("/permission/stats", response_model=PermissionStatsResponse)
async def get_permission_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-54: PermissionGuard 权限拒绝统计 — 工具调用允许/拒绝分布、SCHEDULER 拒绝次数、Top 拒绝工具。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("permission_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 PERMISSION_DENIED 事件
    denied_rows = [
        r for r in rows
        if "PERMISSION_DENIED" in str(r.get("audit_tag") or r.get("reason_code") or r.get("stage") or "").upper()
    ]
    allowed_rows = [
        r for r in rows
        if "PERMISSION_ALLOWED" in str(r.get("audit_tag") or r.get("stage") or "").upper()
    ]

    total_checks = len(denied_rows) + len(allowed_rows)
    denied_count = len(denied_rows)
    allowed_count = len(allowed_rows)
    deny_rate = round(denied_count / total_checks * 100, 1) if total_checks > 0 else None

    from collections import Counter
    tool_counter: Counter = Counter()
    scheduler_denied = 0
    proposer_map: dict = {}

    for r in denied_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}
        tool_name = str(detail.get("tool") or r.get("tool_name") or "unknown")
        proposer = str(detail.get("proposer_class") or r.get("proposer_class") or "USER").upper()
        tool_counter[tool_name] += 1
        proposer_map[tool_name] = proposer
        if proposer == "SCHEDULER":
            scheduler_denied += 1

    top_denied_tools = [
        PermissionDeniedItem(
            tool_name=tool,
            deny_count=cnt,
            pct=round(cnt / denied_count * 100, 1) if denied_count > 0 else 0.0,
            proposer_class=proposer_map.get(tool),
        )
        for tool, cnt in tool_counter.most_common(10)
    ]

    return PermissionStatsResponse(
        window_minutes=window_minutes,
        total_checks=total_checks,
        allowed_count=allowed_count,
        denied_count=denied_count,
        deny_rate=deny_rate,
        scheduler_denied_count=scheduler_denied,
        top_denied_tools=top_denied_tools,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-55: GET /obs/scheduler/history  — TaskScheduler 执行历史趋势
# ---------------------------------------------------------------------------
class SchedulerTaskItem(BaseModel):
    task_id: str
    name: Optional[str]
    status: str
    run_count: int
    error_count: int
    error_rate: Optional[float]
    last_run_ts: Optional[float]
    last_error: Optional[str]

class SchedulerHistoryResponse(BaseModel):
    window_minutes: int
    total_tasks: int
    running_count: int
    success_count: int
    failed_count: int
    disabled_count: int
    overall_error_rate: Optional[float]
    tasks: List[SchedulerTaskItem]
    run_trend: List[Dict[str, Any]]
    timestamp: int

@router.get("/scheduler/history", response_model=SchedulerHistoryResponse)
async def get_scheduler_history(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-55: TaskScheduler 执行历史趋势 — 任务状态分布、run_count/error_count 趋势。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("scheduler_log", "task_log", "audit_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 尝试从全局调度器单例获取实时快照
    scheduler_tasks: list[dict] = []
    try:
        from host_brain.app.automation.task_scheduler import get_scheduler  # noqa: PLC0415
        _sched = get_scheduler()
        health = _sched.health()
        scheduler_tasks = health.get("tasks") or []
    except Exception:
        pass

    # 若无实时数据，从日志中聚合
    if not scheduler_tasks:
        from collections import defaultdict
        task_agg: dict = defaultdict(lambda: {
            "task_id": "", "name": None, "status": "UNKNOWN",
            "run_count": 0, "error_count": 0, "last_run_ts": None, "last_error": None,
        })
        for r in rows:
            tid = str(r.get("task_id") or r.get("id") or "unknown")
            task_agg[tid]["task_id"] = tid
            task_agg[tid]["name"] = r.get("name") or task_agg[tid]["name"]
            task_agg[tid]["status"] = str(r.get("status") or "UNKNOWN").upper()
            task_agg[tid]["run_count"] += int(r.get("run_count") or 1)
            task_agg[tid]["error_count"] += int(r.get("error_count") or 0)
            ts = r.get("last_run_ts") or r.get("ts_ms")
            if ts:
                task_agg[tid]["last_run_ts"] = float(ts)
            if r.get("last_error"):
                task_agg[tid]["last_error"] = str(r["last_error"])[:200]
        scheduler_tasks = list(task_agg.values())

    total_tasks = len(scheduler_tasks)
    running_count = sum(1 for t in scheduler_tasks if str(t.get("status") or "").upper() == "RUNNING")
    success_count = sum(1 for t in scheduler_tasks if str(t.get("status") or "").upper() == "SUCCESS")
    failed_count = sum(1 for t in scheduler_tasks if str(t.get("status") or "").upper() == "FAILED")
    disabled_count = sum(1 for t in scheduler_tasks if str(t.get("status") or "").upper() == "DISABLED")

    total_runs = sum(int(t.get("run_count") or 0) for t in scheduler_tasks)
    total_errors = sum(int(t.get("error_count") or 0) for t in scheduler_tasks)
    overall_error_rate = round(total_errors / total_runs * 100, 1) if total_runs > 0 else None

    tasks = [
        SchedulerTaskItem(
            task_id=str(t.get("task_id") or ""),
            name=t.get("name"),
            status=str(t.get("status") or "UNKNOWN").upper(),
            run_count=int(t.get("run_count") or 0),
            error_count=int(t.get("error_count") or 0),
            error_rate=round(
                int(t.get("error_count") or 0) / int(t.get("run_count") or 1) * 100, 1
            ) if int(t.get("run_count") or 0) > 0 else None,
            last_run_ts=float(t["last_run_ts"]) if t.get("last_run_ts") else None,
            last_error=str(t["last_error"])[:200] if t.get("last_error") else None,
        )
        for t in scheduler_tasks
    ]

    # 执行趋势（从日志按小时分桶）
    from collections import defaultdict
    hourly_runs: dict = defaultdict(int)
    hourly_errors: dict = defaultdict(int)
    for r in rows:
        ts = int(r.get("ts_ms") or r.get("last_run_ts") or 0)
        if ts > 0:
            hour_key = ts // (3600 * 1000)
            hourly_runs[hour_key] += int(r.get("run_count") or 1)
            hourly_errors[hour_key] += int(r.get("error_count") or 0)
    all_hours = sorted(set(list(hourly_runs.keys()) + list(hourly_errors.keys())))
    run_trend = [
        {
            "hour_ts_ms": h * 3600 * 1000,
            "run_count": hourly_runs.get(h, 0),
            "error_count": hourly_errors.get(h, 0),
        }
        for h in all_hours[-24:]
    ]

    return SchedulerHistoryResponse(
        window_minutes=window_minutes,
        total_tasks=total_tasks,
        running_count=running_count,
        success_count=success_count,
        failed_count=failed_count,
        disabled_count=disabled_count,
        overall_error_rate=overall_error_rate,
        tasks=tasks,
        run_trend=run_trend,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-56: GET /obs/action-plan/stats  — ActionPlan 执行成功率/指令类型分布
# ---------------------------------------------------------------------------
class ActionTypeItem(BaseModel):
    action_type: str
    count: int
    pct: float

class ActionPlanStatsResponse(BaseModel):
    window_minutes: int
    total_plans: int
    success_count: int
    fallback_count: int
    success_rate: Optional[float]
    avg_confidence: Optional[float]
    avg_action_count: Optional[float]
    top_action_types: List[ActionTypeItem]
    fallback_reasons: List[Dict[str, Any]]
    timestamp: int

@router.get("/action-plan/stats", response_model=ActionPlanStatsResponse)
async def get_action_plan_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-56: ActionPlan 执行成功率/指令类型分布 — 成功率、平均置信度、指令类型分布、降级原因。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("action_plan_log", "vision_log", "audit_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    total = len(rows)
    if total == 0:
        return ActionPlanStatsResponse(
            window_minutes=window_minutes,
            total_plans=0,
            success_count=0,
            fallback_count=0,
            success_rate=None,
            avg_confidence=None,
            avg_action_count=None,
            top_action_types=[],
            fallback_reasons=[],
            timestamp=now,
        )

    from collections import Counter
    action_type_counter: Counter = Counter()
    fallback_reason_counter: Counter = Counter()
    confidence_list: list = []
    action_count_list: list = []
    success_count = 0
    fallback_count = 0

    for r in rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}

        # 判断是否降级计划
        extra = detail.get("extra") or {}
        is_fallback = bool(extra.get("fallback") or r.get("is_fallback"))
        if is_fallback:
            fallback_count += 1
            reason = str(extra.get("reason") or r.get("fallback_reason") or "UNKNOWN")
            fallback_reason_counter[reason] += 1
        else:
            success_count += 1

        # 置信度
        confidence = float(extra.get("confidence") or r.get("confidence") or 0.0)
        if confidence > 0:
            confidence_list.append(confidence)

        # 指令类型分布
        actions = detail.get("actions") or []
        action_count_list.append(len(actions))
        for action in actions:
            atype = str(action.get("type") or "unknown").lower()
            action_type_counter[atype] += 1

    success_rate = round(success_count / total * 100, 1) if total > 0 else None
    avg_confidence = round(sum(confidence_list) / len(confidence_list), 3) if confidence_list else None
    avg_action_count = round(sum(action_count_list) / len(action_count_list), 1) if action_count_list else None

    total_actions = sum(action_type_counter.values())
    top_action_types = [
        ActionTypeItem(
            action_type=atype,
            count=cnt,
            pct=round(cnt / total_actions * 100, 1) if total_actions > 0 else 0.0,
        )
        for atype, cnt in action_type_counter.most_common(10)
    ]

    fallback_reasons = [
        {"reason": reason, "count": cnt}
        for reason, cnt in fallback_reason_counter.most_common(5)
    ]

    return ActionPlanStatsResponse(
        window_minutes=window_minutes,
        total_plans=total,
        success_count=success_count,
        fallback_count=fallback_count,
        success_rate=success_rate,
        avg_confidence=avg_confidence,
        avg_action_count=avg_action_count,
        top_action_types=top_action_types,
        fallback_reasons=fallback_reasons,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-59: GET /obs/memory-guard/stats  — MemoryGuard 宪法校验统计
# ---------------------------------------------------------------------------
class MemoryGuardRejectItem(BaseModel):
    reason: str
    count: int
    pct: float

class MemoryGuardStatsResponse(BaseModel):
    window_minutes: int
    total_checks: int
    passed_count: int
    blocked_count: int
    block_rate: Optional[float]
    narrative_only_count: int
    dream_blocked_count: int
    distill_eligible_count: int
    top_reject_reasons: List[MemoryGuardRejectItem]
    timestamp: int

@router.get("/memory-guard/stats", response_model=MemoryGuardStatsResponse)
async def get_memory_guard_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-59: MemoryGuard 宪法校验统计 — 过滤率/拒绝原因分布/NARRATIVE_ONLY 比例/蒸馏资格。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("memory_guard_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 MemoryGuard 相关事件
    _mg_tags = {"MEMORY_GUARD_FILTERED", "MEMORY_GUARD_DREAM_BLOCKED",
                "MEMORY_GUARD_NARRATIVE_BLOCKED", "MEMORY_GUARD_DISTILL_ELIGIBLE",
                "EPISODIC_WRITTEN", "EPISODIC_SKIPPED"}
    mg_rows = [
        r for r in rows
        if any(tag in str(r.get("audit_tag") or r.get("stage") or r.get("reason_code") or "").upper()
               for tag in _mg_tags)
    ]

    # Supplement with memory_ops_log (actual write/skip records from MemoryGuard path)
    try:
        mem_ops = ledger.fetch_by_time_range("memory_ops_log", start_ms=since_ms, end_ms=now)
        if mem_ops:
            for row in mem_ops:
                op_kind = str(row.get("op_kind") or "").upper()
                status = str(row.get("status") or "").upper()
                if op_kind == "WRITE" and status == "SUCCESS":
                    mg_rows.append({**row, "audit_tag": "EPISODIC_WRITTEN"})
                elif op_kind == "WRITE" and status == "SKIPPED":
                    reason = str(row.get("reason_code") or "")
                    if "MEMORY_GUARD" in reason.upper():
                        mg_rows.append({**row, "audit_tag": "MEMORY_GUARD_FILTERED"})
                    else:
                        mg_rows.append({**row, "audit_tag": "EPISODIC_SKIPPED"})
    except Exception:
        pass

    from collections import Counter
    total = len(mg_rows)
    passed_count = sum(1 for r in mg_rows if "EPISODIC_WRITTEN" in str(r.get("audit_tag") or "").upper())
    blocked_count = sum(1 for r in mg_rows if any(
        t in str(r.get("audit_tag") or "").upper()
        for t in ("MEMORY_GUARD_FILTERED", "MEMORY_GUARD_DREAM_BLOCKED", "MEMORY_GUARD_NARRATIVE_BLOCKED")
    ))
    narrative_only_count = sum(1 for r in mg_rows if "NARRATIVE_BLOCKED" in str(r.get("audit_tag") or "").upper())
    dream_blocked_count = sum(1 for r in mg_rows if "DREAM_BLOCKED" in str(r.get("audit_tag") or "").upper())
    distill_eligible_count = sum(1 for r in mg_rows if "DISTILL_ELIGIBLE" in str(r.get("audit_tag") or "").upper())
    block_rate = round(blocked_count / total * 100, 1) if total > 0 else None

    reason_counter: Counter = Counter()
    for r in mg_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}
        reason = str(detail.get("reason") or r.get("reason_code") or "UNKNOWN").upper()
        if reason != "UNKNOWN":
            reason_counter[reason] += 1

    top_reject_reasons = [
        MemoryGuardRejectItem(
            reason=reason,
            count=cnt,
            pct=round(cnt / blocked_count * 100, 1) if blocked_count > 0 else 0.0,
        )
        for reason, cnt in reason_counter.most_common(10)
    ]

    return MemoryGuardStatsResponse(
        window_minutes=window_minutes,
        total_checks=total,
        passed_count=passed_count,
        blocked_count=blocked_count,
        block_rate=block_rate,
        narrative_only_count=narrative_only_count,
        dream_blocked_count=dream_blocked_count,
        distill_eligible_count=distill_eligible_count,
        top_reject_reasons=top_reject_reasons,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-60: GET /obs/state-arbiter/stats  — StateArbiter 事实仲裁统计
# ---------------------------------------------------------------------------
class StateSourceItem(BaseModel):
    source: str
    hit_count: int
    pct: float

class StateArbiterStatsResponse(BaseModel):
    window_minutes: int
    total_resolutions: int
    conflict_count: int
    fallback_count: int
    conflict_rate: Optional[float]
    fallback_rate: Optional[float]
    session_override_count: int
    top_sources: List[StateSourceItem]
    top_conflict_keys: List[Dict[str, Any]]
    timestamp: int

@router.get("/state-arbiter/stats", response_model=StateArbiterStatsResponse)
async def get_state_arbiter_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-60: StateArbiter 事实仲裁统计 — 冲突率/降级率/各源命中分布/SESSION_OVERRIDE 次数。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("state_arbiter_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 StateArbiter 相关事件
    _sa_tags = {"STATE_SOURCE_CONFLICT", "STATE_SOURCE_FALLBACK",
                "STATE_ARBITER_CONFLICT", "SESSION_OVERRIDE_WRITTEN"}
    sa_rows = [
        r for r in rows
        if any(tag in str(r.get("audit_tag") or r.get("stage") or r.get("reason_code") or "").upper()
               for tag in _sa_tags)
    ]

    from collections import Counter
    total = len(sa_rows)
    conflict_count = sum(1 for r in sa_rows if "CONFLICT" in str(r.get("audit_tag") or "").upper())
    fallback_count = sum(1 for r in sa_rows if "FALLBACK" in str(r.get("audit_tag") or "").upper())
    session_override_count = sum(1 for r in sa_rows if "SESSION_OVERRIDE" in str(r.get("audit_tag") or "").upper())
    conflict_rate = round(conflict_count / total * 100, 1) if total > 0 else None
    fallback_rate = round(fallback_count / total * 100, 1) if total > 0 else None

    source_counter: Counter = Counter()
    conflict_key_counter: Counter = Counter()

    for r in sa_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}
        winner_source = str(detail.get("winner_source") or r.get("source") or "UNKNOWN").upper()
        if winner_source != "UNKNOWN":
            source_counter[winner_source] += 1
        fact_key = str(detail.get("fact_key") or r.get("fact_key") or "")
        if fact_key and "CONFLICT" in str(r.get("audit_tag") or "").upper():
            conflict_key_counter[fact_key] += 1

    total_hits = sum(source_counter.values())
    top_sources = [
        StateSourceItem(
            source=src,
            hit_count=cnt,
            pct=round(cnt / total_hits * 100, 1) if total_hits > 0 else 0.0,
        )
        for src, cnt in source_counter.most_common(10)
    ]

    top_conflict_keys = [
        {"fact_key": key, "conflict_count": cnt}
        for key, cnt in conflict_key_counter.most_common(5)
    ]

    return StateArbiterStatsResponse(
        window_minutes=window_minutes,
        total_resolutions=total,
        conflict_count=conflict_count,
        fallback_count=fallback_count,
        conflict_rate=conflict_rate,
        fallback_rate=fallback_rate,
        session_override_count=session_override_count,
        top_sources=top_sources,
        top_conflict_keys=top_conflict_keys,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-61: GET /obs/context-compressor/stats  — ContextCompressor 压缩效率统计
# ---------------------------------------------------------------------------
class ContextCompressorStatsResponse(BaseModel):
    window_minutes: int
    total_compressions: int
    active_count: int
    degraded_count: int
    avg_compression_ratio: Optional[float]
    avg_token_saving: Optional[float]
    total_tokens_saved: int
    strategy_distribution: List[Dict[str, Any]]
    summary_generated_count: int
    timestamp: int

@router.get("/context-compressor/stats", response_model=ContextCompressorStatsResponse)
async def get_context_compressor_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-61: ContextCompressor 压缩效率统计 — 压缩率/token 节省/策略分布/摘要生成次数。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("context_compressor_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 ContextCompressor 相关事件
    cc_rows = [
        r for r in rows
        if "CTX_COMPRESSED" in str(r.get("audit_tag") or r.get("stage") or "").upper()
        or "CONTEXT_COMPRESSOR" in str(r.get("stage") or r.get("reason_code") or "").upper()
    ]

    from collections import Counter
    total = len(cc_rows)
    active_count = sum(1 for r in cc_rows if "CTX_COMPRESSED" in str(r.get("audit_tag") or "").upper())
    degraded_count = sum(1 for r in cc_rows if "DEGRADED" in str(r.get("audit_tag") or r.get("stage") or "").upper())

    compression_ratios: list = []
    token_savings: list = []
    total_tokens_saved = 0
    strategy_counter: Counter = Counter()
    summary_generated_count = 0

    for r in cc_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}

        input_turns = int(detail.get("input_turns") or r.get("input_turns") or 0)
        output_turns = int(detail.get("output_turns") or r.get("output_turns") or 0)
        total_tokens = int(detail.get("total_tokens") or r.get("total_tokens") or 0)
        strategy = str(detail.get("strategy") or r.get("strategy") or "unknown").lower()
        has_summary = bool(detail.get("has_summary") or r.get("has_summary"))

        if input_turns > 0 and output_turns > 0:
            ratio = round(output_turns / input_turns, 3)
            compression_ratios.append(ratio)
            saved = input_turns - output_turns
            token_savings.append(saved)
            total_tokens_saved += saved

        if strategy and strategy != "unknown":
            strategy_counter[strategy] += 1
        if has_summary:
            summary_generated_count += 1

    avg_compression_ratio = round(sum(compression_ratios) / len(compression_ratios), 3) if compression_ratios else None
    avg_token_saving = round(sum(token_savings) / len(token_savings), 1) if token_savings else None

    total_strategies = sum(strategy_counter.values())
    strategy_distribution = [
        {
            "strategy": strategy,
            "count": cnt,
            "pct": round(cnt / total_strategies * 100, 1) if total_strategies > 0 else 0.0,
        }
        for strategy, cnt in strategy_counter.most_common(5)
    ]

    return ContextCompressorStatsResponse(
        window_minutes=window_minutes,
        total_compressions=total,
        active_count=active_count,
        degraded_count=degraded_count,
        avg_compression_ratio=avg_compression_ratio,
        avg_token_saving=avg_token_saving,
        total_tokens_saved=total_tokens_saved,
        strategy_distribution=strategy_distribution,
        summary_generated_count=summary_generated_count,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-65: GET /obs/prompt-builder/stats  — PromptBuilder 构建统计
# ---------------------------------------------------------------------------
class PromptBuilderStatsResponse(BaseModel):
    window_minutes: int
    total_builds: int
    injection_detected_count: int
    injection_blocked_count: int
    injection_detection_rate: Optional[float]
    high_risk_count: int
    critical_risk_count: int
    slot_fill_distribution: List[Dict[str, Any]]
    avg_user_message_len: Optional[float]
    degraded_count: int
    timestamp: int

@router.get("/prompt-builder/stats", response_model=PromptBuilderStatsResponse)
async def get_prompt_builder_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-65: PromptBuilder 构建统计 — 注入检测率/slot 填充分布/高风险消息比例。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("prompt_builder_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 PromptBuilder 相关事件
    pb_rows = [
        r for r in rows
        if any(tag in str(r.get("audit_tag") or r.get("stage") or r.get("reason_code") or "").upper()
               for tag in ("PROMPT_BUILT", "PROMPT_INJECTION", "PROMPT_BUILDER", "INJECTION_DETECTED"))
    ]

    from collections import Counter
    total = len(pb_rows)
    injection_detected_count = sum(1 for r in pb_rows if "INJECTION_DETECTED" in str(r.get("audit_tag") or "").upper())
    injection_blocked_count = sum(1 for r in pb_rows if "INJECTION_BLOCKED" in str(r.get("audit_tag") or "").upper())
    injection_detection_rate = round(injection_detected_count / total * 100, 1) if total > 0 else None
    degraded_count = sum(1 for r in pb_rows if "DEGRADED" in str(r.get("audit_tag") or r.get("stage") or "").upper())

    high_risk_count = 0
    critical_risk_count = 0
    slot_counter: Counter = Counter()
    msg_lens: list = []

    for r in pb_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}

        risk_level = str(detail.get("risk_level") or r.get("risk_level") or "").upper()
        if risk_level == "HIGH":
            high_risk_count += 1
        elif risk_level == "CRITICAL":
            critical_risk_count += 1

        # slot 填充统计
        slots_filled = list(detail.get("slots_filled") or r.get("slots_filled") or [])
        for slot in slots_filled:
            slot_counter[str(slot)] += 1

        # 用户消息长度
        msg_len = int(detail.get("user_message_len") or r.get("user_message_len") or 0)
        if msg_len > 0:
            msg_lens.append(msg_len)

    avg_user_message_len = round(sum(msg_lens) / len(msg_lens), 1) if msg_lens else None
    total_slot_fills = sum(slot_counter.values())
    slot_fill_distribution = [
        {
            "slot": slot,
            "count": cnt,
            "pct": round(cnt / total_slot_fills * 100, 1) if total_slot_fills > 0 else 0.0,
        }
        for slot, cnt in slot_counter.most_common(10)
    ]

    return PromptBuilderStatsResponse(
        window_minutes=window_minutes,
        total_builds=total,
        injection_detected_count=injection_detected_count,
        injection_blocked_count=injection_blocked_count,
        injection_detection_rate=injection_detection_rate,
        high_risk_count=high_risk_count,
        critical_risk_count=critical_risk_count,
        slot_fill_distribution=slot_fill_distribution,
        avg_user_message_len=avg_user_message_len,
        degraded_count=degraded_count,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-66: GET /obs/episodic-store/stats  — EpisodicStore 写入统计
# ---------------------------------------------------------------------------
class EpisodicStoreStatsResponse(BaseModel):
    window_minutes: int
    total_attempts: int
    written_count: int
    skipped_count: int
    write_rate: Optional[float]
    memory_guard_blocked_count: int
    dream_blocked_count: int
    narrative_only_blocked_count: int
    distill_eligible_count: int
    avg_occurrence_count: Optional[float]
    timestamp: int

@router.get("/episodic-store/stats", response_model=EpisodicStoreStatsResponse)
async def get_episodic_store_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-66: EpisodicStore 写入统计 — 写入率/MemoryGuard 拦截率/蒸馏资格率/dream 拦截次数。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("episodic_store_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 EpisodicStore 相关事件
    _ep_tags = {"EPISODIC_WRITTEN", "EPISODIC_SKIPPED", "MEMORY_GUARD_DREAM_BLOCKED",
                "MEMORY_GUARD_NARRATIVE_BLOCKED", "MEMORY_GUARD_DISTILL_ELIGIBLE",
                "SKIPPED_MEMORY_GUARD"}
    ep_rows = [
        r for r in rows
        if any(tag in str(r.get("audit_tag") or r.get("stage") or r.get("reason_code") or "").upper()
               for tag in _ep_tags)
    ]

    # Supplement with memory_ops_log (actual episodic write records)
    try:
        mem_ops = ledger.fetch_by_time_range("memory_ops_log", start_ms=since_ms, end_ms=now)
        if mem_ops:
            # Map memory_ops SUCCESS rows to EPISODIC_WRITTEN synthetic tags
            for row in mem_ops:
                if str(row.get("op_kind") or "").upper() == "WRITE" and str(row.get("status") or "").upper() == "SUCCESS":
                    ep_rows.append({**row, "audit_tag": "EPISODIC_WRITTEN"})
                elif str(row.get("op_kind") or "").upper() == "WRITE" and str(row.get("status") or "").upper() == "SKIPPED":
                    ep_rows.append({**row, "audit_tag": "EPISODIC_SKIPPED"})
    except Exception:
        pass

    total = len(ep_rows)
    written_count = sum(1 for r in ep_rows if "EPISODIC_WRITTEN" in str(r.get("audit_tag") or "").upper())
    skipped_count = sum(1 for r in ep_rows if any(
        t in str(r.get("audit_tag") or r.get("stage") or "").upper()
        for t in ("EPISODIC_SKIPPED", "SKIPPED_MEMORY_GUARD")
    ))
    memory_guard_blocked_count = sum(1 for r in ep_rows if any(
        t in str(r.get("audit_tag") or "").upper()
        for t in ("MEMORY_GUARD_DREAM_BLOCKED", "MEMORY_GUARD_NARRATIVE_BLOCKED", "MEMORY_GUARD_FILTERED")
    ))
    dream_blocked_count = sum(1 for r in ep_rows if "DREAM_BLOCKED" in str(r.get("audit_tag") or "").upper())
    narrative_only_blocked_count = sum(1 for r in ep_rows if "NARRATIVE_BLOCKED" in str(r.get("audit_tag") or "").upper())
    distill_eligible_count = sum(1 for r in ep_rows if "DISTILL_ELIGIBLE" in str(r.get("audit_tag") or "").upper())
    write_rate = round(written_count / total * 100, 1) if total > 0 else None

    occurrence_counts: list = []
    for r in ep_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}
        occ = int(detail.get("occurrence_count") or r.get("occurrence_count") or 0)
        if occ > 0:
            occurrence_counts.append(occ)

    avg_occurrence_count = round(sum(occurrence_counts) / len(occurrence_counts), 1) if occurrence_counts else None

    return EpisodicStoreStatsResponse(
        window_minutes=window_minutes,
        total_attempts=total,
        written_count=written_count,
        skipped_count=skipped_count,
        write_rate=write_rate,
        memory_guard_blocked_count=memory_guard_blocked_count,
        dream_blocked_count=dream_blocked_count,
        narrative_only_blocked_count=narrative_only_blocked_count,
        distill_eligible_count=distill_eligible_count,
        avg_occurrence_count=avg_occurrence_count,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/chain/stats — TODO-82: 主链路节点统计
# ---------------------------------------------------------------------------

class ChainNodeStatItem(BaseModel):
    node_name: str
    total_calls: int
    degraded_count: int
    degraded_rate: float          # degraded / total * 100
    avg_latency_ms: Optional[float]
    p95_latency_ms: Optional[float]
    timeout_count: int


class ChainStatsResponse(BaseModel):
    window_minutes: int
    total_requests: int
    governor_block_count: int
    governor_block_rate: float
    recall_degraded_count: int
    episodic_written_count: int
    qa_rewrite_count: int
    canon_proposal_count: int
    node_stats: List[ChainNodeStatItem]
    timestamp: int


@router.get("/chain/stats", response_model=ChainStatsResponse)
async def get_chain_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-82: 主链路节点统计（各节点耗时分布/降级率/失败率）。

    从 audit_log（AUDIT_SNAPSHOT 阶段）中提取 audit_tags 和 node_timings，
    聚合各节点的调用次数、降级率、平均耗时、P95 耗时、超时次数。
    """
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # 从 audit_log 中拉取 AUDIT_SNAPSHOT 记录
    try:
        rows = ledger.fetch_by_time_range("audit_log", start_ms=since_ms, end_ms=now)
        audit_rows = [r for r in rows if str(r.get("stage") or "").upper() == "AUDIT_SNAPSHOT"]
    except Exception:
        audit_rows = []

    total_requests = len(audit_rows)

    # 解析 audit_tags 和 node_timings
    governor_block_count = 0
    recall_degraded_count = 0
    episodic_written_count = 0
    qa_rewrite_count = 0
    canon_proposal_count = 0

    # 节点耗时收集：node_name → [latency_ms, ...]
    node_latencies: dict[str, list[float]] = {}
    node_degraded: dict[str, int] = {}
    node_timeout: dict[str, int] = {}
    node_calls: dict[str, int] = {}

    _KNOWN_NODES = [
        "airlock_quarantine", "governor", "graph_executor", "recall_fusion",
        "llm_gateway", "qa_contract", "episodic_store", "audit",
        "canon_proposal", "airlock_sanitizer",
    ]
    for n in _KNOWN_NODES:
        node_latencies[n] = []
        node_degraded[n] = 0
        node_timeout[n] = 0
        node_calls[n] = 0

    for row in audit_rows:
        detail_raw = row.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}

        audit_tags = list(detail.get("audit_tags") or [])
        node_timings_ms = dict(detail.get("node_timings_ms") or {})

        # 全局统计
        if any("GOVERNOR_BLOCK" in t or "GOVERNOR_REJECT" in t for t in audit_tags):
            governor_block_count += 1
        if any("RECALL_DEGRADED" in t or "RECALL_CIRCUIT_OPEN" in t for t in audit_tags):
            recall_degraded_count += 1
        if "EPISODIC_WRITTEN" in audit_tags:
            episodic_written_count += 1
        if any("QA_REWRITE" in t for t in audit_tags):
            qa_rewrite_count += 1
        if any("CANON_PROPOSAL_SUBMITTED" in t for t in audit_tags):
            canon_proposal_count += 1

        # 节点耗时
        for node_name, latency_ms in node_timings_ms.items():
            if node_name not in node_latencies:
                node_latencies[node_name] = []
                node_degraded[node_name] = 0
                node_timeout[node_name] = 0
                node_calls[node_name] = 0
            node_calls[node_name] += 1
            if isinstance(latency_ms, (int, float)) and latency_ms > 0:
                node_latencies[node_name].append(float(latency_ms))

        # 降级/超时标记
        for tag in audit_tags:
            if "DEGRADED_FALLBACK" in tag or "DEGRADED" in tag:
                for n in _KNOWN_NODES:
                    if n.upper().replace("_", "") in tag.upper().replace("_", ""):
                        node_degraded[n] += 1
                        break
            if "MODULE_TIMEOUT" in tag:
                for n in _KNOWN_NODES:
                    if n.upper().replace("_", "") in tag.upper().replace("_", ""):
                        node_timeout[n] += 1
                        break

    # 构建节点统计列表
    node_stats = []
    for node_name in _KNOWN_NODES:
        calls = node_calls.get(node_name, 0)
        latencies = node_latencies.get(node_name, [])
        degraded = node_degraded.get(node_name, 0)
        timeout = node_timeout.get(node_name, 0)

        avg_lat = round(sum(latencies) / len(latencies), 1) if latencies else None
        p95_lat = None
        if len(latencies) >= 5:
            sorted_lat = sorted(latencies)
            p95_idx = int(len(sorted_lat) * 0.95)
            p95_lat = round(sorted_lat[min(p95_idx, len(sorted_lat) - 1)], 1)

        node_stats.append(ChainNodeStatItem(
            node_name=node_name,
            total_calls=calls,
            degraded_count=degraded,
            degraded_rate=round(degraded / calls * 100, 1) if calls > 0 else 0.0,
            avg_latency_ms=avg_lat,
            p95_latency_ms=p95_lat,
            timeout_count=timeout,
        ))

    governor_block_rate = round(governor_block_count / total_requests * 100, 1) if total_requests > 0 else 0.0

    return ChainStatsResponse(
        window_minutes=window_minutes,
        total_requests=total_requests,
        governor_block_count=governor_block_count,
        governor_block_rate=governor_block_rate,
        recall_degraded_count=recall_degraded_count,
        episodic_written_count=episodic_written_count,
        qa_rewrite_count=qa_rewrite_count,
        canon_proposal_count=canon_proposal_count,
        node_stats=node_stats,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/agent-loop/history — TODO-86: Agent Loop 历史执行记录
# ---------------------------------------------------------------------------

class AgentLoopHistoryItem(BaseModel):
    request_id: str
    goal: Optional[str]
    final_status: Optional[str]
    goal_achieved: Optional[bool]
    total_rounds: Optional[int]
    total_latency_ms: Optional[int]
    ts_ms: Optional[int]
    client_mode: Optional[str]


class AgentLoopHistoryResponse(BaseModel):
    items: List[AgentLoopHistoryItem]
    total: int
    window_minutes: int
    timestamp: int


@router.get("/agent-loop/history", response_model=AgentLoopHistoryResponse)
async def get_agent_loop_history(
    window_minutes: int = Query(1440, ge=1, le=10080, description="统计窗口（分钟）"),
    limit: int = Query(10, ge=1, le=50, description="最大返回条数"),
):
    """TODO-86: Agent Loop 历史执行记录（最近 N 次执行结果）。

    从 audit_log 中拉取 LOOP_DONE 阶段的记录，返回最近 limit 条。
    """
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("audit_log", start_ms=since_ms, end_ms=now)
        loop_rows = [r for r in rows if str(r.get("stage") or "").upper() == "LOOP_DONE"]
    except Exception:
        loop_rows = []

    # 按时间倒序，取最近 limit 条
    loop_rows.sort(key=lambda r: r.get("ts_ms") or 0, reverse=True)
    loop_rows = loop_rows[:limit]

    items = []
    for row in loop_rows:
        detail_raw = row.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}

        items.append(AgentLoopHistoryItem(
            request_id=str(row.get("request_id") or ""),
            goal=detail.get("goal") or row.get("goal"),
            final_status=detail.get("final_status") or row.get("final_status"),
            goal_achieved=detail.get("goal_achieved"),
            total_rounds=detail.get("total_rounds"),
            total_latency_ms=detail.get("total_latency_ms"),
            ts_ms=row.get("ts_ms"),
            client_mode=detail.get("client_mode"),
        ))

    return AgentLoopHistoryResponse(
        items=items,
        total=len(items),
        window_minutes=window_minutes,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/error-heatmap — TODO-88: 错误热力图端点（按小时/节点分布）
# ---------------------------------------------------------------------------

class ErrorHeatmapCell(BaseModel):
    hour: int                    # 0-23
    node_name: str
    error_count: int
    pct: float                   # 占该小时总错误的百分比


class ErrorHeatmapResponse(BaseModel):
    window_hours: int
    total_errors: int
    hours: List[int]             # 出现过错误的小时列表（0-23）
    nodes: List[str]             # 出现过错误的节点列表
    cells: List[ErrorHeatmapCell]
    hourly_totals: List[Dict[str, Any]]   # [{ hour, total }]
    node_totals: List[Dict[str, Any]]     # [{ node_name, total }]
    timestamp: int


@router.get("/error-heatmap", response_model=ErrorHeatmapResponse)
async def get_error_heatmap(
    window_hours: int = Query(24, ge=1, le=168, description="统计窗口（小时），最大 7 天"),
):
    """TODO-88: 错误热力图端点 — 按小时 × 节点维度统计错误分布。

    从 error_log 中拉取近 window_hours 小时的错误记录，
    按 (hour, node_name) 聚合，返回热力图数据。
    """
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_hours * 3600 * 1000

    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 按 (hour, node_name) 聚合
    from collections import defaultdict
    import datetime as _dt

    cell_counts: dict[tuple[int, str], int] = defaultdict(int)
    hourly_counts: dict[int, int] = defaultdict(int)
    node_counts: dict[str, int] = defaultdict(int)

    for row in rows:
        ts = row.get("ts_ms") or 0
        if ts <= 0:
            continue
        hour = _dt.datetime.fromtimestamp(ts / 1000).hour
        node_name = str(
            row.get("node_id") or row.get("stage") or row.get("source") or "unknown"
        )
        cell_counts[(hour, node_name)] += 1
        hourly_counts[hour] += 1
        node_counts[node_name] += 1

    total_errors = sum(hourly_counts.values())
    hours_list = sorted(hourly_counts.keys())
    nodes_list = sorted(node_counts.keys(), key=lambda n: node_counts[n], reverse=True)[:20]

    cells = []
    for (hour, node_name), cnt in cell_counts.items():
        if node_name not in nodes_list:
            continue
        hour_total = hourly_counts.get(hour, 1)
        cells.append(ErrorHeatmapCell(
            hour=hour,
            node_name=node_name,
            error_count=cnt,
            pct=round(cnt / hour_total * 100, 1),
        ))

    hourly_totals = [{"hour": h, "total": hourly_counts[h]} for h in range(24)]
    node_totals = [{"node_name": n, "total": node_counts[n]} for n in nodes_list]

    return ErrorHeatmapResponse(
        window_hours=window_hours,
        total_errors=total_errors,
        hours=hours_list,
        nodes=nodes_list,
        cells=cells,
        hourly_totals=hourly_totals,
        node_totals=node_totals,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-103: GET /obs/semantic-recall/stats — 语义召回器统计
# ---------------------------------------------------------------------------

@dataclass
class SemanticRecallStatsResponse:
    """语义召回器统计响应。"""
    use_mock: bool
    chroma_host: str
    chroma_port: int
    top_k: int
    similarity_threshold: float
    collection_name: str
    mock_doc_count: int
    """Mock 后端中的文档数量（use_mock=True 时有效）"""
    status: str
    """OK / DEGRADED"""
    timestamp: int


@router.get("/semantic-recall/stats")
async def get_semantic_recall_stats():
    """TODO-103: 语义召回器统计端点 — 返回 SemanticRecaller 的配置和状态信息。"""
    now = _now_ms()
    try:
        from host_brain.app.dependencies import get_semantic_recaller  # noqa: PLC0415
        recaller = get_semantic_recaller()

        mock_doc_count = len(getattr(recaller, "_mock_store", []))
        return SemanticRecallStatsResponse(
            use_mock=recaller._use_mock,
            chroma_host=recaller._chroma_host,
            chroma_port=recaller._chroma_port,
            top_k=recaller._top_k,
            similarity_threshold=recaller._threshold,
            collection_name=recaller._collection_name,
            mock_doc_count=mock_doc_count,
            status="OK",
            timestamp=now,
        )
    except Exception as exc:
        return SemanticRecallStatsResponse(
            use_mock=True,
            chroma_host="localhost",
            chroma_port=8000,
            top_k=5,
            similarity_threshold=0.70,
            collection_name="episodic_memory",
            mock_doc_count=0,
            status=f"DEGRADED: {exc}",
            timestamp=now,
        )


# ─────────────────────────────────────────────────────────────────────────────
# TODO-115: GET /obs/failure-events — 失败事件统计（按事件类型分组）
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/failure-events")
async def get_failure_events(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计时间窗口（分钟，最大 7 天）"),
    event_type: Optional[str] = Query(None, description="按事件类型过滤（如 PROMPT_INJECTION / GOVERNOR_BLOCK）"),
    limit: int = Query(50, ge=1, le=200, description="返回条数上限"),
    ledger: "SQLiteLedger" = Depends(_get_ledger),
):
    """
    TODO-115: 失败事件统计端点。

    从 audit_log 中按 audit_tag 聚合失败事件，返回：
    - 各事件类型的发生次数
    - 最近 N 条失败事件详情
    - 时间窗口内的总失败次数
    """
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # 所有已知失败事件类型（对应 FailureEvent 枚举）
    KNOWN_FAILURE_EVENTS = [
        "GOVERNOR_BLOCK", "EVIDENCE_EMPTY", "MODEL_FALLBACK",
        "QA_REWRITE", "EDGE_UNREACHABLE", "VISION_LOW_CONFIDENCE",
        "MODULE_TIMEOUT", "PROMPT_INJECTION",
    ]

    try:
        # 使用 SQLiteLedger 实际存在的 fetch_by_time_range 方法查询 error_log
        # error_log 表的 reason_code 字段对应 FailureEvent 枚举值
        all_rows = ledger.fetch_by_time_range(
            "error_log",
            start_ms=since_ms,
            end_ms=now,
            limit=limit * 3,  # 多取一些，过滤后再截断
        )

        # 按 reason_code 过滤（对应 FailureEvent 枚举值）
        target_codes = set([event_type] if event_type else KNOWN_FAILURE_EVENTS)
        filtered_rows = [
            row for row in all_rows
            if row.get("reason_code", "") in target_codes
        ]

        # 按事件类型聚合
        event_counts: dict = {}
        recent_events = []
        for row in filtered_rows:
            tag = row.get("reason_code", "UNKNOWN")
            event_counts[tag] = event_counts.get(tag, 0) + 1
            if len(recent_events) < limit:
                recent_events.append({
                    "ts_ms": row.get("ts_ms", 0),
                    "event_type": tag,
                    "request_id": row.get("request_id", ""),
                    "stage": row.get("stage", ""),
                    "detail": str(row.get("detail", ""))[:200],
                })

        total_failures = sum(event_counts.values())

        return {
            "window_minutes": window_minutes,
            "total_failures": total_failures,
            "event_counts": event_counts,
            "recent_events": recent_events,
            "timestamp": now,
            "status": "OK",
        }

    except Exception as exc:
        logger.warning("get_failure_events: 查询失败: %s", exc)
        return {
            "window_minutes": window_minutes,
            "total_failures": 0,
            "event_counts": {},
            "recent_events": [],
            "timestamp": now,
            "status": f"DEGRADED: {exc}",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Gateway 六平台消息统计 — GET /obs/gateway/stats
# ─────────────────────────────────────────────────────────────────────────────

class GatewayPlatformStats(BaseModel):
    """单个平台的统计数据。"""
    platform: str
    status: str = "UNKNOWN"  # UP / DEGRADED / DOWN / UNKNOWN
    status_detail: Optional[str] = None
    inbound_count: int = 0
    outbound_count: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    last_event_ts_ms: Optional[int] = None
    bridge_status: Optional[str] = None  # wxautox4 bridge 特有
    call_active: int = 0  # 当前活跃通话数（仅微信）

class GatewayStatsResponse(BaseModel):
    platforms: List[GatewayPlatformStats] = []
    total_inbound: int = 0
    total_outbound: int = 0
    total_errors: int = 0
    humanize_enabled: bool = True
    humanize_typing_speed_cps: int = 12
    window_minutes: int = 60
    timestamp: int = 0


_GATEWAY_REMOTE_HEALTH_CACHE: Dict[str, Any] = {
    "expires_at_ms": 0,
    "value": {},
}


def _normalize_gateway_platform_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"ok", "up", "healthy", "ready", "online", "active"}:
        return "UP"
    if normalized in {"degraded", "warn", "warning", "partial"}:
        return "DEGRADED"
    if normalized in {"error", "down", "failed", "fail", "offline"}:
        return "DOWN"
    return "UNKNOWN"


def _merge_gateway_platform_status(local_status: str, remote_status: str) -> str:
    priority = {
        "UNKNOWN": 0,
        "UP": 1,
        "DEGRADED": 2,
        "DOWN": 3,
    }
    left = str(local_status or "UNKNOWN").strip().upper() or "UNKNOWN"
    right = str(remote_status or "UNKNOWN").strip().upper() or "UNKNOWN"
    return left if priority.get(left, 0) >= priority.get(right, 0) else right


def _coerce_dict_payload(value: Any) -> Dict[str, Any]:
    payload = _safe_json(value)
    return dict(payload) if isinstance(payload, dict) else {}


def _infer_gateway_platform_from_row(*, session_id: Any, payload: Any, detail: Any) -> str | None:
    payload_dict = _coerce_dict_payload(payload)
    platform = str(payload_dict.get("platform") or "").strip().lower()
    if platform in {"wechat", "qq", "telegram", "feishu", "wecom", "line"}:
        return platform

    normalized_session = str(session_id or "").strip().lower()
    if normalized_session.startswith("qq_"):
        return "qq"
    if normalized_session.startswith("wechat_") or normalized_session.startswith("wx_"):
        return "wechat"
    if normalized_session.startswith("telegram_") or normalized_session.startswith("tg_"):
        return "telegram"
    if normalized_session.startswith("feishu_"):
        return "feishu"
    if normalized_session.startswith("wecom_"):
        return "wecom"
    if normalized_session.startswith("line_"):
        return "line"

    detail_text = str(detail or "").strip().lower()
    for candidate in ("wechat", "qq", "telegram", "feishu", "wecom", "line"):
        if f"platform={candidate}" in detail_text:
            return candidate
    return None


async def _fetch_remote_gateway_health() -> Dict[str, Any]:
    cached = _GATEWAY_REMOTE_HEALTH_CACHE.get("value")
    expires_at_ms = int(_GATEWAY_REMOTE_HEALTH_CACHE.get("expires_at_ms") or 0)
    now = _now_ms()
    if expires_at_ms > now and isinstance(cached, dict) and cached:
        return dict(cached)

    stale = dict(cached) if isinstance(cached, dict) else {}
    try:
        import httpx
        from host_brain.channels.edge_rpc_client import _resolve_gateway_base_urls
    except Exception:
        return stale

    timeout_ms_raw = str(os.environ.get("HOST_EDGE_RPC_TIMEOUT_MS") or "3000").strip()
    try:
        timeout_ms = int(timeout_ms_raw)
    except ValueError:
        timeout_ms = 3000
    timeout_s = max(0.5, min(float(timeout_ms) / 1000.0, 5.0))

    for base_url in _resolve_gateway_base_urls():
        url = f"{base_url}/v1/gateway/health"
        try:
            async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                continue
            body = resp.json()
            if not isinstance(body, dict):
                continue
            snapshot = {
                "base_url": base_url,
                "body": body,
            }
            _GATEWAY_REMOTE_HEALTH_CACHE["value"] = dict(snapshot)
            _GATEWAY_REMOTE_HEALTH_CACHE["expires_at_ms"] = now + 3_000
            return snapshot
        except Exception as exc:
            logger.debug("get_gateway_stats: gateway health probe failed url=%s error=%s", url, exc)

    return stale

@router.get("/gateway/stats", response_model=GatewayStatsResponse)
async def get_gateway_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    ledger: "SQLiteLedger" = Depends(_get_ledger),
):
    """
    Gateway 六平台消息统计端点。

    聚合各平台的消息收发量、错误数、延迟、在线状态、Bridge 健康和通话状态。
    """
    import os
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    PLATFORMS = ["wechat", "qq", "telegram", "feishu", "wecom", "line"]
    platform_stats: List[GatewayPlatformStats] = []

    total_in = 0
    total_out = 0
    total_err = 0
    remote_gateway_health = await _fetch_remote_gateway_health()
    remote_gateway_body = (
        remote_gateway_health.get("body")
        if isinstance(remote_gateway_health, dict)
        else {}
    )
    remote_adapters = (
        remote_gateway_body.get("adapters")
        if isinstance(remote_gateway_body, dict) and isinstance(remote_gateway_body.get("adapters"), dict)
        else {}
    )
    remote_health_source = str(remote_gateway_health.get("base_url") or "").strip()
    try:
        channel_rows = ledger.fetch_by_time_range(
            "channel_io_log",
            start_ms=since_ms,
            end_ms=now,
            limit=5000,
        )
    except Exception:
        channel_rows = []
    try:
        voice_rows = ledger.fetch_by_time_range(
            "voice_event_log",
            start_ms=since_ms,
            end_ms=now,
            limit=5000,
        )
    except Exception:
        voice_rows = []

    for plat in PLATFORMS:
        try:
            plat_rows = [
                row for row in channel_rows
                if str(row.get("channel") or "").strip().lower() == plat
            ]
            gateway_input_rows = []
            plat_voice_rows = []
            for row in voice_rows:
                payload_dict = _coerce_dict_payload(row.get("payload"))
                inferred_platform = _infer_gateway_platform_from_row(
                    session_id=row.get("session_id"),
                    payload=payload_dict,
                    detail=row.get("detail"),
                )
                if inferred_platform == plat:
                    plat_voice_rows.append(row)
                if inferred_platform != plat:
                    continue
                source_upper = str(payload_dict.get("source") or "").strip().upper()
                if source_upper == "GATEWAY":
                    gateway_input_rows.append((row, payload_dict))

            combined_rows = list(plat_rows) + list(plat_voice_rows)
            channel_inbound = sum(
                1
                for row in plat_rows
                if str(row.get("direction") or "").upper() in ("INBOUND", "INGRESS", "RECEIVE", "RECV")
            )
            channel_outbound = sum(
                1
                for row in plat_rows
                if str(row.get("direction") or "").upper() in ("OUTBOUND", "EGRESS", "SEND", "REPLY")
            )
            channel_errors = sum(
                1
                for row in plat_rows
                if str(row.get("status") or "").upper() in ("ERROR", "FAILED", "REJECTED")
                or "FAIL" in str(row.get("reason_code") or "").upper()
                or "ERROR" in str(row.get("reason_code") or "").upper()
            )
            gateway_inbound_ids = {
                str(row.get("request_id") or "").strip()
                for row, _payload in gateway_input_rows
                if str(row.get("event_type") or "").strip().upper() == "INPUT_ACCEPTED"
                and str(row.get("request_id") or "").strip()
            }
            gateway_completed_ids = {
                str(row.get("request_id") or "").strip()
                for row, _payload in gateway_input_rows
                if str(row.get("event_type") or "").strip().upper() == "INPUT_COMPLETED"
                and str(row.get("status") or "").strip().upper() in {"OK", "COMPLETED", "SENT"}
                and str(row.get("request_id") or "").strip()
            }
            gateway_error_ids = {
                str(row.get("request_id") or "").strip()
                for row, _payload in gateway_input_rows
                if (
                    str(row.get("event_type") or "").strip().upper() == "INPUT_FAILED"
                    or str(row.get("status") or "").strip().upper() in {"ERROR", "FAILED", "REJECTED"}
                    or "FAIL" in str(row.get("reason_code") or "").upper()
                    or "ERROR" in str(row.get("reason_code") or "").upper()
                )
                and str(row.get("request_id") or "").strip()
            }

            inbound = channel_inbound if channel_inbound > 0 else len(gateway_inbound_ids)
            outbound = channel_outbound if channel_outbound > 0 else len(gateway_completed_ids)
            errors = channel_errors if channel_errors > 0 else len(gateway_error_ids)

            last_ts = max((row.get("ts_ms", 0) for row in combined_rows), default=None) if combined_rows else None

            latencies = []
            for row in combined_rows:
                payload = _coerce_dict_payload(row.get("payload"))
                if payload.get("latency_ms") is not None:
                    latencies.append(payload.get("latency_ms"))
            avg_lat = sum(latencies) / len(latencies) if latencies else 0.0

            if not combined_rows:
                status_str = "UNKNOWN"
            elif errors > inbound * 0.5 and inbound > 0:
                status_str = "DOWN"
            elif errors > 0:
                status_str = "DEGRADED"
            else:
                status_str = "UP"

            bridge_status = None
            call_active = 0
            status_detail = None
            if plat == "wechat":
                bridge_backend = os.environ.get("EDGE_WECHAT_BRIDGE_BACKEND", "uia")
                if combined_rows:
                    bridge_status = f"{bridge_backend}:active"
                recent_call_window = now - 5 * 60 * 1000
                call_rows = [
                    row for row in plat_voice_rows
                    if row.get("ts_ms", 0) > recent_call_window and "CALL" in str(row.get("event_type") or "").upper()
                ]
                call_starts = sum(1 for row in call_rows if "VOICE_CALL" in str(row.get("event_type") or "").upper())
                call_ends = sum(1 for row in call_rows if "CANCEL_CALL" in str(row.get("event_type") or "").upper())
                call_active = max(0, call_starts - call_ends)
            remote_adapter = remote_adapters.get(plat) if isinstance(remote_adapters, dict) else None
            if isinstance(remote_adapter, dict):
                remote_status = _normalize_gateway_platform_status(remote_adapter.get("status"))
                status_str = _merge_gateway_platform_status(status_str, remote_status)
                if not combined_rows and remote_status != "UNKNOWN":
                    if remote_health_source:
                        status_detail = f"adapter health via {remote_health_source}"
                    else:
                        status_detail = "adapter health via remote gateway"
                if plat == "wechat" and not bridge_status:
                    remote_bridge_backend = (
                        str(remote_adapter.get("bridge_backend") or os.environ.get("EDGE_WECHAT_BRIDGE_BACKEND", "uia")).strip()
                        or "uia"
                    )
                    remote_bridge_status = str(remote_adapter.get("status") or "unknown").strip().lower() or "unknown"
                    bridge_status = f"{remote_bridge_backend}:{remote_bridge_status}"
                remote_last_outbound = remote_adapter.get("last_outbound")
                if last_ts is None and isinstance(remote_last_outbound, dict):
                    try:
                        last_ts = int(remote_last_outbound.get("ts_ms")) if remote_last_outbound.get("ts_ms") is not None else None
                    except (TypeError, ValueError):
                        last_ts = None
            ps = GatewayPlatformStats(
                platform=plat,
                status=status_str,
                status_detail=status_detail,
                inbound_count=inbound,
                outbound_count=outbound,
                error_count=errors,
                avg_latency_ms=round(avg_lat, 1),
                last_event_ts_ms=last_ts,
                bridge_status=bridge_status,
                call_active=call_active,
            )
            platform_stats.append(ps)
            total_in += inbound
            total_out += outbound
            total_err += errors

        except Exception as exc:
            logger.warning("get_gateway_stats: platform %s error: %s", plat, exc)
            platform_stats.append(GatewayPlatformStats(platform=plat, status=f"ERROR: {exc}"))

    humanize_enabled = os.environ.get("EDGE_HUMANIZE_ENABLED", "true").lower() == "true"
    humanize_cps = int(os.environ.get("EDGE_HUMANIZE_TYPING_SPEED_CPS", "12"))

    return GatewayStatsResponse(
        platforms=platform_stats,
        total_inbound=total_in,
        total_outbound=total_out,
        total_errors=total_err,
        humanize_enabled=humanize_enabled,
        humanize_typing_speed_cps=humanize_cps,
        window_minutes=window_minutes,
        timestamp=now,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Skills Hub 统计 — GET /obs/skills-hub/stats
# ─────────────────────────────────────────────────────────────────────────────

class SkillHubEntry(BaseModel):
    name: str
    version: str = ""
    author: str = ""
    clearance: str = "LOW"
    risk_level: int = 1
    source: str = "local"  # local / github / external
    status: str = "loaded"

class SkillHubStatsResponse(BaseModel):
    hub_enabled: bool = True
    total_skills: int = 0
    loaded_skills: int = 0
    failed_skills: int = 0
    skills: List[SkillHubEntry] = []
    external_dirs_scanned: List[str] = []
    timestamp: int = 0

@router.get("/skills-hub/stats", response_model=SkillHubStatsResponse)
async def get_skills_hub_stats():
    """
    Skills Hub 统计端点。

    返回已加载的外部 Skill 列表、Hub 开关状态、扫描目录等信息。
    """
    import os
    now = _now_ms()

    hub_enabled = os.environ.get("HOST_SKILL_HUB_ENABLED", "true").lower() == "true"
    skills_list: List[SkillHubEntry] = []
    failed_count = 0
    external_dirs: List[str] = []

    if hub_enabled:
        try:
            # 尝试从 SkillRegistry 获取已注册技能
            from host_brain.skills.registry import SkillRegistry
            registry = SkillRegistry.get_instance()
            all_skills = registry.list_all() if hasattr(registry, "list_all") else []
            for sk in all_skills:
                if isinstance(sk, dict):
                    skills_list.append(SkillHubEntry(
                        name=sk.get("name", sk.get("skill_id", "unknown")),
                        version=sk.get("version", ""),
                        author=sk.get("author", ""),
                        clearance=sk.get("clearance", "LOW"),
                        risk_level=sk.get("risk_level", 1),
                        source=sk.get("source", "builtin"),
                        status="loaded",
                    ))
        except Exception as exc:
            logger.warning("get_skills_hub_stats: registry load failed: %s", exc)
            failed_count += 1

        # 扫描外部目录
        scan_external = os.environ.get("HOST_SKILL_SCAN_EXTERNAL", "true").lower() == "true"
        if scan_external:
            home = os.path.expanduser("~")
            for d in [
                os.path.join(home, ".skillhub", "skills"),
                os.path.join(home, ".openclaw", "skills"),
                os.path.join(home, ".codebuddy", "skills"),
            ]:
                if os.path.isdir(d):
                    external_dirs.append(d)
        extra_dirs = os.environ.get("HOST_SKILL_EXTERNAL_DIRS", "").strip()
        if extra_dirs:
            for d in extra_dirs.split(","):
                d = d.strip()
                if d and os.path.isdir(d):
                    external_dirs.append(d)

    return SkillHubStatsResponse(
        hub_enabled=hub_enabled,
        total_skills=len(skills_list) + failed_count,
        loaded_skills=len(skills_list),
        failed_skills=failed_count,
        skills=skills_list,
        external_dirs_scanned=external_dirs,
        timestamp=now,
    )


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket 实时推送 — WS /obs/ws/health
# ─────────────────────────────────────────────────────────────────────────────

from fastapi import WebSocket, WebSocketDisconnect
import asyncio

_ws_clients: List[WebSocket] = []

@router.websocket("/ws/health")
async def ws_health(websocket: WebSocket):
    """
    WebSocket 实时健康推送端点。

    连接后每 10s 推送一次全服务健康快照（与 GET /obs/services/health 格式一致）。
    告警事件（服务 DOWN/DEGRADED 变化）立即推送。
    """
    await websocket.accept()
    _ws_clients.append(websocket)
    logger.info("WebSocket health client connected (total: %d)", len(_ws_clients))

    prev_statuses: Dict[str, str] = {}

    try:
        while True:
            try:
                # 获取最新健康数据
                aggregator = ServiceHealthAggregator()
                ledger = _get_ledger_instance()
                health_data = aggregator.aggregate(ledger=ledger) if ledger else {"services": [], "status": "DEGRADED"}

                services = health_data.get("services", [])
                current_statuses: Dict[str, str] = {}
                alerts = []

                for svc in services:
                    sid = svc.get("service_id", "")
                    st = svc.get("status", "UNKNOWN")
                    current_statuses[sid] = st

                    # 检测状态变化（告警）
                    prev_st = prev_statuses.get(sid)
                    if prev_st and prev_st != st:
                        if st in ("DOWN", "DEGRADED"):
                            alerts.append({
                                "type": "alert",
                                "service_id": sid,
                                "prev_status": prev_st,
                                "new_status": st,
                                "ts_ms": _now_ms(),
                            })

                prev_statuses = current_statuses

                # 推送健康快照
                payload = {
                    "type": "health_snapshot",
                    "data": health_data,
                    "alerts": alerts,
                    "ts_ms": _now_ms(),
                }
                await websocket.send_json(payload)

            except Exception as exc:
                logger.warning("WebSocket health push error: %s", exc)
                try:
                    await websocket.send_json({
                        "type": "error",
                        "detail": str(exc),
                        "ts_ms": _now_ms(),
                    })
                except Exception:
                    break

            await asyncio.sleep(10)  # 每 10 秒推送一次

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)
        logger.info("WebSocket health client disconnected (remaining: %d)", len(_ws_clients))


def _get_ledger_instance():
    """尝试获取 ledger 实例（辅助函数）。"""
    try:
        if _ledger_provider and callable(_ledger_provider):
            return _ledger_provider()
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 系统运行模式管理 — GET/POST /obs/sys-mode
# ─────────────────────────────────────────────────────────────────────────────

import threading  # noqa: E402 — sys-mode 区域需要 threading.Lock

# 全局 ModeTransitionService 实例（延迟初始化）
_mode_transition_service = None
_mode_transition_lock = threading.Lock()


def _get_mode_transition_service():
    """获取或初始化 ModeTransitionService 单例。"""
    global _mode_transition_service
    if _mode_transition_service is not None:
        return _mode_transition_service

    with _mode_transition_lock:
        if _mode_transition_service is not None:
            return _mode_transition_service

        try:
            from host_brain.core.mode_transition import ModeTransitionService
            from host_brain.app.dependencies import (
                get_hard_state as _dep_get_hard_state,
                set_sys_mode as _dep_set_sys_mode,
            )
            ledger = _get_ledger_instance()
            _mode_transition_service = ModeTransitionService(
                get_hard_state=_dep_get_hard_state,
                set_sys_mode=_dep_set_sys_mode,
                ledger=ledger,
            )
        except Exception as exc:
            logger.warning("ModeTransitionService init failed, using fallback: %s", exc)
            # 降级：返回一个最小化的 fallback 服务
            _mode_transition_service = _FallbackModeService()

    return _mode_transition_service


class _FallbackModeService:
    """ModeTransitionService 降级替代——当依赖不可用时提供基本功能。"""

    def get_current_status(self):
        import os
        mode = os.environ.get("HOST_SYS_MODE", "normal").strip().lower()
        return {
            "sys_mode": mode,
            "auto_rules": {
                "gaming_detect": True,
                "gaming_exit": True,
                "idle_to_sleep": True,
                "wakeup_on_msg": True,
                "morning_wakeup": True,
            },
            "idle_ms": 0,
            "idle_minutes": 0,
            "last_interaction_ts_ms": _now_ms(),
            "is_night_time": False,
            "valid_transitions": [],
            "ts_ms": _now_ms(),
            "degraded": True,
            "degraded_reason": "ModeTransitionService not available",
        }

    def switch_mode(self, target_mode, *, source=None, reason=""):
        import os
        from host_brain.core.metabolism import ALLOWED_SYS_MODES
        target = str(target_mode).strip().lower()
        if target not in ALLOWED_SYS_MODES:
            return {"success": False, "error": f"invalid mode: {target}"}
        # 优先通过 set_sys_mode 切换（保持 _hard_state 一致性）
        try:
            from host_brain.app.dependencies import set_sys_mode as _dep_set  # noqa: PLC0415
            result = _dep_set(target)
            return {
                "success": True,
                "from_mode": result.get("from_mode", "unknown"),
                "to_mode": result.get("to_mode", target),
                "source": "manual",
                "reason": reason,
                "ts_ms": _now_ms(),
                "degraded": True,
            }
        except Exception:
            pass
        # 降级：直接写环境变量（_hard_state 可能不一致）
        os.environ["HOST_SYS_MODE"] = target
        return {
            "success": True,
            "from_mode": "unknown",
            "to_mode": target,
            "source": "manual",
            "reason": reason,
            "ts_ms": _now_ms(),
            "degraded": True,
        }

    def get_history(self, limit=20):
        return []

    def update_auto_rules(self, rules):
        return rules

    def on_event(self, event_type, payload=None):
        return None


class SysModeStatusResponse(BaseModel):
    sys_mode: str
    auto_rules: Dict[str, bool]
    idle_ms: int
    idle_minutes: float
    last_interaction_ts_ms: int
    is_night_time: bool
    valid_transitions: List[str]
    ts_ms: int
    degraded: bool = False
    degraded_reason: Optional[str] = None


class SysModeSwitchRequest(BaseModel):
    target_mode: Literal["normal", "focus", "gaming", "sleep", "maintenance", "locked"] = Field(
        ..., description="目标模式: normal/focus/gaming/sleep/maintenance/locked"
    )
    reason: str = Field("", description="切换原因说明")
    source: str = Field("manual", description="切换来源: manual/auto_gaming/auto_idle/auto_wakeup/skill")


class SysModeSwitchResponse(BaseModel):
    success: bool
    from_mode: Optional[str] = None
    to_mode: Optional[str] = None
    source: Optional[str] = None
    reason: Optional[str] = None
    event_id: Optional[str] = None
    error: Optional[str] = None
    ts_ms: int


class SysModeHistoryItem(BaseModel):
    event_id: str
    from_mode: str
    to_mode: str
    source: str
    reason: str
    ts_ms: int
    success: bool
    error: Optional[str] = None


class SysModeHistoryResponse(BaseModel):
    items: List[SysModeHistoryItem]
    total: int
    timestamp: int


class AutoRulesUpdateRequest(BaseModel):
    rules: Dict[str, bool] = Field(..., description="自动切换规则开关")


@router.get("/sys-mode", response_model=SysModeStatusResponse)
async def get_sys_mode():
    """获取当前系统运行模式 + 自动切换规则状态 + 空闲信息。"""
    service = _get_mode_transition_service()
    status = service.get_current_status()
    return SysModeStatusResponse(**status)


@router.post("/sys-mode", response_model=SysModeSwitchResponse)
async def switch_sys_mode(body: SysModeSwitchRequest):
    """切换系统运行模式（手动 / API 触发）。"""
    service = _get_mode_transition_service()

    # 映射 source 字符串到枚举
    try:
        from host_brain.core.mode_transition import TransitionSource
        source_enum = TransitionSource(body.source)
    except (ValueError, ImportError):
        source_enum = None

    if source_enum:
        result = service.switch_mode(
            body.target_mode,
            source=source_enum,
            reason=body.reason,
        )
    else:
        result = service.switch_mode(
            body.target_mode,
            reason=body.reason,
        )

    return SysModeSwitchResponse(
        success=result.get("success", False),
        from_mode=result.get("from_mode"),
        to_mode=result.get("to_mode"),
        source=result.get("source"),
        reason=result.get("reason"),
        event_id=result.get("event_id"),
        error=result.get("error"),
        ts_ms=result.get("ts_ms", _now_ms()),
    )


@router.get("/sys-mode/history", response_model=SysModeHistoryResponse)
async def get_sys_mode_history(
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
):
    """获取模式切换历史记录。"""
    service = _get_mode_transition_service()
    history = service.get_history(limit=limit)
    items = [SysModeHistoryItem(**h) for h in history]
    return SysModeHistoryResponse(
        items=items,
        total=len(items),
        timestamp=_now_ms(),
    )


@router.put("/sys-mode/auto-rules")
async def update_auto_rules(body: AutoRulesUpdateRequest):
    """更新自动切换规则开关。"""
    service = _get_mode_transition_service()
    updated = service.update_auto_rules(body.rules)
    return {
        "auto_rules": updated,
        "timestamp": _now_ms(),
    }


@router.post("/sys-mode/event")
async def post_sys_mode_event(
    event_type: str = Query(..., description="事件类型"),
):
    """发送模式切换事件（供 Edge 端调用）。

    支持的 event_type：
    - gaming_process_detected: 检测到游戏进程
    - gaming_process_exited: 游戏进程退出
    - user_message_received: 收到用户消息
    - idle_check: 空闲检查
    - morning_check: 早间唤醒检查
    """
    service = _get_mode_transition_service()
    result = service.on_event(event_type)
    if result is None:
        return {
            "triggered": False,
            "event_type": event_type,
            "timestamp": _now_ms(),
        }
    return {
        "triggered": True,
        "event_type": event_type,
        "result": result,
        "timestamp": _now_ms(),
    }
