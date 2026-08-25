"""将一次对话运行链压缩为可查询、可脱敏的诊断审计投影。

这里记录的是解释运行结果所需的摘要与引用，而不是另一条业务执行路径；
审计写入失败不得反向阻塞用户可见的回复。
"""

from __future__ import annotations

from typing import Any, Callable, Sequence
from collections.abc import Mapping
from host_brain.core.runtime_helpers import (
    _coerce_trace_mapping,
    _normalize_trace_stage_meta,
    _resolve_envelope_confidence,
    _build_trace_output_digest,
    _detect_trace_fallback_used,
    _summarize_motive_state_for_trace,
    _summarize_conversation_interpretation_for_trace,
    _summarize_reply_plan_for_trace,
    _summarize_tactic_ledger_for_trace,
    _summarize_topic_ledger_for_trace,
    _summarize_affect_state_for_trace,
    _summarize_relationship_state_for_trace,
    _summarize_drive_state_for_trace,
    _summarize_emotion_context_for_trace,
    _summarize_persona_resident_memory_for_trace,
)
import asyncio
import json
import time

def _normalize_retrieval_summary_for_audit(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    # 检索实现可返回不同形状的运行时字典。先在审计边界归一为有限状态，
    # 查询端才无需猜测“没有候选”究竟是跳过、降级还是正常空结果。
    raw = dict(summary or {})
    surface_memory_trace = _surface_memory_trace_for_audit(raw)
    route_rows = list(raw.get("route_rows") or []) if isinstance(raw.get("route_rows"), list) else []
    route_count = raw.get("route_count")
    if route_count in (None, ""):
        route_count = len(route_rows)
    candidate_count = int(raw.get("candidate_count") or 0)
    topk_count = int(raw.get("topk_count") or 0)
    reason_code = str(raw.get("reason_code") or "").strip() or None
    error_text = str(raw.get("error") or "").strip() or None
    explicit_status = str(raw.get("status") or "").strip().upper()

    if explicit_status in {"OK", "SKIPPED", "DEGRADED"}:
        status = explicit_status
    elif reason_code or error_text or bool(raw.get("degraded", False)):
        status = "DEGRADED"
    elif candidate_count > 0 or int(route_count) > 0 or topk_count > 0 or route_rows:
        status = "OK"
    else:
        status = "SKIPPED"

    degraded = status == "DEGRADED" or bool(raw.get("degraded", False))

    raw["status"] = status
    raw["route_count"] = int(route_count)
    raw["candidate_count"] = candidate_count
    raw["topk_count"] = topk_count
    raw["degraded"] = degraded
    raw["reason_code"] = reason_code
    raw["error"] = error_text
    if surface_memory_trace:
        raw["surface_memory_trace"] = surface_memory_trace
    return raw


def _surface_memory_trace_for_audit(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    # 仅挑选可解释召回决策的字段并限制列表长度；不要把原始记忆包直接写入
    # 诊断账本，以免持久化过多用户内容或让单次请求制造无界审计记录。
    packet = _coerce_trace_mapping(dict(summary or {}).get("surface_memory_recall_packet"))
    trace = _coerce_trace_mapping(packet.get("trace"))
    if not trace:
        return {}

    out: dict[str, Any] = {}
    plan = _coerce_trace_mapping(trace.get("plan"))
    if plan:
        out["plan"] = {
            "reason": str(plan.get("reason") or ""),
            "task_mode": str(plan.get("task_mode") or ""),
            "recall_policy": str(plan.get("recall_policy") or ""),
            "max_facts": _safe_int(plan.get("max_facts"), default=0),
            "target_store_ids": [str(item) for item in list(plan.get("target_store_ids") or [])[:8]],
        }
    bool_trace_keys = {
        "skipped",
        "short_term_explicit_memory",
        "answer_ready",
        "coverage_failed_resolved_by_deep_review",
        "coverage_failed_resolved_by_generic_evidence_review",
    }
    for key in ("skipped", "route_count", "short_term_explicit_memory", "answer_ready", "coverage_failed_resolved_by_deep_review", "coverage_failed_resolved_by_generic_evidence_review"):
        if key in trace:
            out[key] = (
                bool(trace.get(key))
                if key in bool_trace_keys
                else _safe_int(trace.get(key), default=0)
            )

    decision = _coerce_trace_mapping(trace.get("recall_decision"))
    if decision:
        out["recall_decision"] = {
            "schema": str(decision.get("schema") or ""),
            "decision": str(decision.get("decision") or ""),
            "invoked": bool(decision.get("invoked")),
            "skip_reason": str(decision.get("skip_reason") or ""),
            "recall_policy": str(decision.get("recall_policy") or ""),
            "reason": str(decision.get("reason") or ""),
            "task_mode": str(decision.get("task_mode") or ""),
            "query_kind": str(decision.get("query_kind") or ""),
            "temporal_scope": str(decision.get("temporal_scope") or ""),
            "recall_contract_id": str(decision.get("recall_contract_id") or ""),
            "need_type": str(decision.get("need_type") or ""),
            "recall_grain": str(decision.get("recall_grain") or ""),
            "retrieval_unit": str(decision.get("retrieval_unit") or ""),
            "answer_scope": str(decision.get("answer_scope") or ""),
            "verification_gate": str(decision.get("verification_gate") or ""),
            "primary_memory_layer": str(decision.get("primary_memory_layer") or ""),
            "expected_memory_layers": [str(item) for item in list(decision.get("expected_memory_layers") or [])[:8]],
            "target_store_ids": [str(item) for item in list(decision.get("target_store_ids") or [])[:8]],
            "target_domains": [str(item) for item in list(decision.get("target_domains") or [])[:8]],
            "validation_requirements": [str(item) for item in list(decision.get("validation_requirements") or [])[:8]],
            "answer_obligations": [str(item) for item in list(decision.get("answer_obligations") or [])[:8]],
            "evidence_depth": str(decision.get("evidence_depth") or ""),
            "min_collection_items": _safe_int(decision.get("min_collection_items"), default=0),
            "time_lookback_days": decision.get("time_lookback_days"),
            "max_facts": _safe_int(decision.get("max_facts"), default=0),
            "selection_basis": [str(item) for item in list(decision.get("selection_basis") or [])[:12]],
        }

    need = _coerce_trace_mapping(trace.get("recall_need"))
    if need:
        out["recall_need"] = {
            "schema": str(need.get("schema") or ""),
            "query_kind": str(need.get("query_kind") or ""),
            "temporal_scope": str(need.get("temporal_scope") or ""),
            "recall_contract_id": str(need.get("recall_contract_id") or ""),
            "need_type": str(need.get("need_type") or ""),
            "recall_grain": str(need.get("recall_grain") or ""),
            "retrieval_unit": str(need.get("retrieval_unit") or ""),
            "answer_scope": str(need.get("answer_scope") or ""),
            "verification_gate": str(need.get("verification_gate") or ""),
            "primary_memory_layer": str(need.get("primary_memory_layer") or ""),
            "expected_memory_layers": [str(item) for item in list(need.get("expected_memory_layers") or [])[:8]],
            "target_domains": [str(item) for item in list(need.get("target_domains") or [])[:8]],
            "min_collection_items": _safe_int(need.get("min_collection_items"), default=0),
            "evidence_depth": str(need.get("evidence_depth") or ""),
        }
    coverage = _coerce_trace_mapping(trace.get("recall_coverage"))
    if coverage:
        dimensions = _coerce_trace_mapping(coverage.get("dimensions"))
        out["recall_coverage"] = {
            "score": _safe_float(coverage.get("score"), default=0.0),
            "passed": bool(coverage.get("passed")),
            "missing": [str(item) for item in list(coverage.get("missing") or [])[:8]],
            "dimensions": {str(key): _safe_float(value, default=0.0) for key, value in dimensions.items()},
            "required_count": _safe_int(coverage.get("required_count"), default=0),
            "passed_required_count": _safe_int(coverage.get("passed_required_count"), default=0),
            "selected_item_count": _safe_int(coverage.get("selected_item_count"), default=0),
            "min_collection_items": _safe_int(coverage.get("min_collection_items"), default=0),
            "evidence_ref_count": _safe_int(coverage.get("evidence_ref_count"), default=0),
            "source_ref_count": _safe_int(coverage.get("source_ref_count"), default=0),
        }
        detail = _coerce_trace_mapping(coverage.get("detail_evidence"))
        if detail:
            out["recall_coverage"]["detail_evidence"] = {
                "required": bool(detail.get("required")),
                "ready_for_deep_review": bool(detail.get("ready_for_deep_review")),
                "basis": str(detail.get("basis") or ""),
                "body_item_count": _safe_int(detail.get("body_item_count"), default=0),
                "anchor_item_count": _safe_int(detail.get("anchor_item_count"), default=0),
                "structured_item_count": _safe_int(detail.get("structured_item_count"), default=0),
            }
        out["coverage_score"] = out["recall_coverage"]["score"]

    readiness = _coerce_trace_mapping(trace.get("recall_answer_readiness"))
    if readiness:
        detail = _coerce_trace_mapping(readiness.get("detail_evidence"))
        out["recall_answer_readiness"] = {
            "schema": str(readiness.get("schema") or ""),
            "status": str(readiness.get("status") or ""),
            "ready_to_answer": bool(readiness.get("ready_to_answer")),
            "response_mode": str(readiness.get("response_mode") or ""),
            "surface_action": str(readiness.get("surface_action") or ""),
            "reasons": [str(item) for item in list(readiness.get("reasons") or [])[:8]],
            "need_type": str(readiness.get("need_type") or ""),
            "recall_contract_id": str(readiness.get("recall_contract_id") or ""),
            "query_kind": str(readiness.get("query_kind") or ""),
            "temporal_scope": str(readiness.get("temporal_scope") or ""),
            "evidence_depth": str(readiness.get("evidence_depth") or ""),
            "coverage_score": _safe_float(readiness.get("coverage_score"), default=0.0),
            "coverage_passed": bool(readiness.get("coverage_passed")),
            "validation_passed": bool(readiness.get("validation_passed")),
            "validation_failed": bool(readiness.get("validation_failed")),
            "coverage_failed": bool(readiness.get("coverage_failed")),
            "missing": [str(item) for item in list(readiness.get("missing") or [])[:8]],
            "selected_fact_count": _safe_int(readiness.get("selected_fact_count"), default=0),
            "selected_user_fact_count": _safe_int(readiness.get("selected_user_fact_count"), default=0),
            "selected_bond_count": _safe_int(readiness.get("selected_bond_count"), default=0),
            "selected_item_count": _safe_int(readiness.get("selected_item_count"), default=0),
            "min_collection_items": _safe_int(readiness.get("min_collection_items"), default=0),
            "required_count": _safe_int(readiness.get("required_count"), default=0),
            "passed_required_count": _safe_int(readiness.get("passed_required_count"), default=0),
        }
        if detail:
            out["recall_answer_readiness"]["detail_evidence"] = {
                "required": bool(detail.get("required")),
                "ready_for_deep_review": bool(detail.get("ready_for_deep_review")),
                "basis": str(detail.get("basis") or ""),
                "body_item_count": _safe_int(detail.get("body_item_count"), default=0),
                "anchor_item_count": _safe_int(detail.get("anchor_item_count"), default=0),
                "structured_item_count": _safe_int(detail.get("structured_item_count"), default=0),
            }

    deep = _coerce_trace_mapping(trace.get("lightnovel_deep_review"))
    if deep:
        selected = _coerce_trace_mapping(deep.get("selected"))
        safe_deep = {
            "status": str(deep.get("status") or ""),
            "reason_code": str(deep.get("reason_code") or ""),
            "item_count": _safe_int(deep.get("item_count"), default=0),
            "latency_ms": _safe_int(deep.get("latency_ms"), default=0),
            "cached": bool(deep.get("cached")),
            "source": str(deep.get("source") or ""),
            "recommendation_signature": str(deep.get("recommendation_signature") or ""),
            "source_episode_id": str(deep.get("source_episode_id") or ""),
            "persisted_episode_id": str(deep.get("persisted_episode_id") or ""),
            "persist_status": str(deep.get("persist_status") or ""),
            "evidence_ref_count": _safe_int(deep.get("evidence_ref_count"), default=0),
        }
        if selected:
            safe_deep["selected"] = {
                "index": _safe_int(selected.get("index"), default=0),
                "title": str(selected.get("title") or "")[:120],
            }
        out["lightnovel_deep_review"] = safe_deep

    generic = _coerce_trace_mapping(trace.get("generic_evidence_review"))
    if generic:
        out["generic_evidence_review"] = {
            "status": str(generic.get("status") or ""),
            "reason_code": str(generic.get("reason_code") or ""),
            "item_count": _safe_int(generic.get("item_count"), default=0),
            "latency_ms": _safe_int(generic.get("latency_ms"), default=0),
            "cached": bool(generic.get("cached")),
            "source": str(generic.get("source") or ""),
            "persisted_episode_id": str(generic.get("persisted_episode_id") or ""),
            "persist_status": str(generic.get("persist_status") or ""),
            "evidence_ref_count": _safe_int(generic.get("evidence_ref_count"), default=0),
        }
    collection_fastpath = _coerce_trace_mapping(trace.get("collection_fastpath"))
    if collection_fastpath:
        out["collection_fastpath"] = {
            "attempted": bool(collection_fastpath.get("attempted")),
            "status": str(collection_fastpath.get("status") or ""),
            "latency_ms": _safe_int(collection_fastpath.get("latency_ms"), default=0),
            "candidate_count": _safe_int(collection_fastpath.get("candidate_count"), default=0),
        }
    return out


def _surface_memory_debug_trace_for_audit(
    *,
    retrieval_summary: Mapping[str, Any] | None,
    retrieval_evidence_items: Sequence[Mapping[str, Any]] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
    delegated_agents: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    summary = dict(retrieval_summary or {})
    packet = _coerce_trace_mapping(summary.get("surface_memory_recall_packet"))
    trace_stage = _coerce_trace_mapping(trace_stage_meta)
    out: dict[str, Any] = {}

    history = _coerce_trace_mapping(trace_stage.get("conversation_history"))
    if history:
        out["history_window"] = {
            "turns_injected": _safe_int(history.get("turns_injected"), default=0),
            "fetched_turns": _safe_int(history.get("fetched_turns"), default=0),
            "older_turn_count": _safe_int(history.get("older_turn_count"), default=0),
            "summary_present": bool(history.get("summary_present")),
            "oldest_ts_ms": history.get("oldest_ts_ms"),
            "newest_ts_ms": history.get("newest_ts_ms"),
        }

    if packet:
        rows = [
            _surface_memory_debug_row(row)
            for row in list(packet.get("rows") or [])[:8]
            if isinstance(row, Mapping)
        ]
        episode_rows = [row for row in rows if row.get("store_id") == "life_episode"]
        out["episode_recall_packet"] = {
            "candidate_count": _safe_int(packet.get("candidate_count"), default=0),
            "route_ids": [str(item) for item in list(packet.get("route_ids") or [])[:8]],
            "source_refs": [str(item) for item in list(packet.get("source_refs") or [])[:8]],
            "evidence_refs": [str(item) for item in list(packet.get("evidence_refs") or [])[:8]],
            "fact_count": len(list(packet.get("facts") or [])),
            "life_episode_row_count": len(episode_rows),
            "rows": rows,
        }

    evidence_items = [
        item
        for item in list(retrieval_evidence_items or [])
        if isinstance(item, Mapping)
    ]
    if evidence_items:
        out["evidence_pack"] = {
            "item_count": len(evidence_items),
            "items": [
                {
                    "store_id": str(item.get("store_id") or ""),
                    "evidence_ref": str(item.get("evidence_ref") or ""),
                    "source_ref": str(item.get("source_ref") or ""),
                    "score": item.get("score"),
                    "snippet": str(item.get("snippet") or "")[:220],
                }
                for item in evidence_items[:8]
            ],
        }
    elif packet:
        evidence_refs = [str(item) for item in list(packet.get("evidence_refs") or [])[:8] if str(item or "").strip()]
        source_refs = [str(item) for item in list(packet.get("source_refs") or [])[:8] if str(item or "").strip()]
        if evidence_refs or source_refs:
            out["evidence_pack"] = {
                "item_count": len(evidence_refs or source_refs),
                "items": [
                    {"evidence_ref": ref}
                    for ref in evidence_refs[:8]
                ] or [
                    {"source_ref": ref}
                    for ref in source_refs[:8]
                ],
            }

    sub_agent_rows: list[dict[str, Any]] = []
    for item in list(delegated_agents or [])[:8]:
        if not isinstance(item, Mapping):
            continue
        audit_refs = _coerce_trace_mapping(item.get("audit_refs"))
        agent_context = _coerce_trace_mapping(audit_refs.get("agent_context"))
        agent_context_trace = _coerce_trace_mapping(agent_context.get("trace"))
        sub_agent_rows.append(
            {
                "agent_id": str(item.get("agent_id") or ""),
                "success": bool(item.get("success")),
                "latency_ms": _safe_int(item.get("latency_ms"), default=0),
                "context_profile": str(agent_context_trace.get("context_profile") or ""),
                "conversation_turns": _safe_int(agent_context.get("conversation_turns"), default=0),
                "memory_fact_count": _safe_int(agent_context.get("memory_fact_count"), default=0),
                "evidence_item_count": _safe_int(agent_context.get("evidence_item_count"), default=0),
            }
        )
    if sub_agent_rows:
        out["sub_agent_envelope"] = {
            "agent_count": len(sub_agent_rows),
            "agents": sub_agent_rows,
        }
    return {
        key: value
        for key, value in out.items()
        if value not in (None, "", [], {})
    }


def _surface_memory_debug_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "store_id": str(row.get("store_id") or ""),
        "source_ref": str(row.get("source_ref") or ""),
        "evidence_ref": str(row.get("evidence_ref") or ""),
        "score": row.get("score"),
        "fact": str(row.get("fact") or "")[:260],
    }


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return round(float(default), 6)


def _build_trace_agent_envelope(
    *,
    request_id: str,
    node_id: str,
    parent_node_id: str | None,
    node_type: str,
    status: str,
    reason_code: str | None,
    output_data: Any,
    fallback_used: bool,
    degrade_code: str | None,
    ts_ms: int,
) -> dict[str, Any]:
    status_text = str(status or "OK").strip().upper() or "OK"
    reason_code_text = str(reason_code or "").strip() or None
    degrade_code_text = str(degrade_code or "").strip() or None
    reason_text = reason_code_text or ""
    output_digest = _build_trace_output_digest(output_data)

    fallback_marked = bool(fallback_used) or _detect_trace_fallback_used(
        reason_code=reason_code_text,
        degrade_code=degrade_code_text,
        output_data=output_data,
    )
    if degrade_code_text is None and status_text in {"DEGRADED", "FAILED", "ERROR"}:
        degrade_code_text = reason_code_text

    return {
        "agent_id": node_id,
        "agent_family": str(node_type or "system").strip().lower() or "system",
        "node_id": node_id,
        "parent_node_id": (str(parent_node_id).strip() if parent_node_id else None),
        "request_id": str(request_id or "").strip(),
        "status": status_text,
        "confidence": _resolve_envelope_confidence(status_text),
        "reason": reason_text,
        "reason_code": reason_code_text,
        "fallback_used": fallback_marked,
        "degrade_code": degrade_code_text,
        "output_digest": output_digest,
        "schema_version": "1.0",
        "ts_ms": int(ts_ms or 0),
    }


def _build_trace_stage_node(
    *,
    node_id: str,
    node_type: str,
    display_name: str,
    ts_ms: int,
    status: str = "OK",
    side: str = "host",
    request_id: str = "",
    parent_node_id: str | None = None,
    reason_code: str | None = None,
    fallback_used: bool = False,
    degrade_code: str | None = None,
    latency_ms: int | None = None,
    provider: str | None = None,
    model_id: str | None = None,
    input_data: Any = None,
    output_data: Any = None,
    error: str | None = None,
    invoked: bool | None = None,
    skipped_reason: str | None = None,
    affected_output: bool | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    # 节点既供时间线展示，也会被下游 operator 查询消费。因此把 SKIPPED、
    # DEGRADED 与 FAILED 显式编码，避免把“未调用”误读成一次成功但无输出的执行。
    normalized_status = str(status or "OK").strip().upper() or "OK"
    node: dict[str, Any] = {
        "node_id": node_id,
        "node_type": node_type,
        "display_name": display_name,
        "side": side,
        "status": status,
        "ts_ms": ts_ms,
        "invoked": bool(invoked) if invoked is not None else normalized_status != "SKIPPED",
        "affected_output": bool(affected_output) if affected_output is not None else normalized_status not in {"SKIPPED", "FAILED"},
    }
    if skipped_reason:
        node["skipped_reason"] = skipped_reason
    elif normalized_status == "SKIPPED":
        node["skipped_reason"] = reason_code or "not_invoked"
    if source:
        node["source"] = source
    if reason_code:
        node["reason_code"] = reason_code
    if latency_ms is not None:
        node["latency_ms"] = latency_ms
    if provider:
        node["provider"] = provider
    if model_id:
        node["model_id"] = model_id
    if input_data not in (None, {}, []):
        node["input_data"] = input_data
    if output_data not in (None, {}, []):
        node["output_data"] = output_data
    if error:
        node["error"] = error
    node["agent_envelope"] = _build_trace_agent_envelope(
        request_id=request_id,
        node_id=node_id,
        parent_node_id=parent_node_id,
        node_type=node_type,
        status=status,
        reason_code=reason_code,
        output_data=output_data,
        fallback_used=fallback_used,
        degrade_code=degrade_code,
        ts_ms=ts_ms,
    )
    return node


def _load_request_aux_llm_trace_nodes(
    *,
    ledger: Any,
    request_id: str,
) -> list[dict[str, Any]]:
    try:
        rows = list(ledger.fetch_by_request_id("llm_pool_call_log", request_id) or [])
    except Exception:
        return []

    rows.sort(
        key=lambda row: (
            int(row.get("ts_ms") or 0),
            str(row.get("scene_type") or "").strip(),
        )
    )

    nodes: list[dict[str, Any]] = []
    for row in rows:
        scene_name = str(row.get("scene_type") or "CHAT").strip().upper() or "CHAT"
        node_id = f"llm_pool:{scene_name.lower()}"
        provider = str(row.get("final_provider") or "").strip().lower() or None
        model_id = str(row.get("final_model") or "").strip() or None
        pool_exhausted = bool(row.get("pool_exhausted"))
        status = "DEGRADED" if pool_exhausted else "OK"
        reason_code = "LLM_POOL_EXHAUSTED" if pool_exhausted else None
        latency_ms = row.get("total_latency_ms")
        try:
            latency_value = int(latency_ms) if latency_ms is not None else None
        except (TypeError, ValueError):
            latency_value = None
        nodes.append(
            _build_trace_stage_node(
                node_id=node_id,
                node_type="llm",
                display_name=f"LLM Pool ({scene_name})",
                ts_ms=int(row.get("ts_ms") or 0),
                status=status,
                reason_code=reason_code,
                latency_ms=latency_value,
                provider=provider,
                model_id=model_id,
                input_data={
                    "scene_type": scene_name,
                    "cognitive_tier": str(row.get("cognitive_tier") or "").strip().upper() or None,
                },
                output_data={
                    "attempt_count": int(row.get("attempt_count") or 0),
                    "cached": bool(row.get("cached")),
                    "pool_exhausted": pool_exhausted,
                },
            )
        )
    return nodes


def _trace_node_sort_key(node: Mapping[str, Any]) -> tuple[int, int, str]:
    node_id = str(node.get("node_id") or "").strip()
    ts_ms = int(node.get("ts_ms") or 0)
    priority = {
        "airlock_ingress": 0,
        "governor": 1,
        "emotion_context": 10,
        "persona_resident_memory": 11,
        "motive_state": 12,
        "conversation_interpretation": 13,
        "topic_ledger": 14,
        "tactic_ledger": 15,
        "affect_state": 16,
        "relationship_state": 17,
        "drive_state": 18,
        "reply_plan": 19,
        "cortex_orchestrator": 20,
        "query_planner": 30,
        "agentic_rag": 31,
        "recall_fusion": 32,
        "executive_router": 40,
        "surface_reply": 50,
        "identity_surface_render": 51,
        "surface_render": 51,
        "surface_renderer": 51,
        "prompt_builder": 60,
        "llm_stream": 70,
        "pii_filter": 80,
        "airlock_egress": 90,
    }.get(node_id)
    if priority is None:
        priority = 55
    return (priority, ts_ms, node_id)


def _persist_post_process_audit_sync(
    *,
    audit_ledger: Callable[[], Any],
    request_id: str,
    session_id: str,
    narrative: str,
    emotion_context: Mapping[str, Any] | None = None,
    bond_summary: dict[str, Any] | None,
    ts_ms: int,
    total_latency_ms: int = 0,
    llm_latency_ms: int = 0,
    first_token_ms: int | None = None,
    provider_name: str = "",
    model_id: str = "",
    buffer_stream: bool = False,
    delegated_agents: list[dict[str, Any]] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    gov_decision: dict[str, Any] | None = None,
    pii_ingress_stats: dict[str, Any] | None = None,
    pii_egress_stats: dict[str, Any] | None = None,
    llm_input_digest: str | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    motive_state: Mapping[str, Any] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> None:
    # 该函数只投影已经发生的运行事实；不能据此驱动策略或补做业务动作。
    # 先统一检索/追踪数据再落库，保证同一次请求中的多张审计视图能相互对照。
    ledger = audit_ledger()
    _now = int(time.time() * 1000)

    if gov_decision:
        ledger.log_governor(
            {
                "request_id": request_id,
                "intent_id": request_id,
                "session_id": session_id,
                "decision": gov_decision.get("decision", "APPROVE"),
                "reason_code": gov_decision.get("reason_code"),
                "rule_trace": json.dumps(gov_decision.get("rule_trace", []), ensure_ascii=False, default=str),
                "latency_ms": gov_decision.get("latency_ms", 0),
                "ts_ms": ts_ms,
            }
        )

    _trace_stage_meta = _normalize_trace_stage_meta(trace_stage_meta)
    _normalized_retrieval_summary = _normalize_retrieval_summary_for_audit(retrieval_summary)
    _evidence_items = list(retrieval_evidence_items or [])
    _rs = _normalized_retrieval_summary
    _surface_memory_debug_trace = _surface_memory_debug_trace_for_audit(
        retrieval_summary=_rs,
        retrieval_evidence_items=_evidence_items,
        trace_stage_meta=_trace_stage_meta,
        delegated_agents=delegated_agents,
    )
    if _surface_memory_debug_trace:
        _rs["surface_memory_debug_trace"] = _surface_memory_debug_trace
    if _evidence_items or _rs.get("status") == "OK":
        _query_plan = _rs.get("query_plan") or {}
        _topk_snippets = [
            {
                "evidence_ref": str(item.get("evidence_ref") or ""),
                "store_id": str(item.get("store_id") or ""),
                "snippet": str(item.get("snippet") or "")[:200],
                "score": item.get("score"),
            }
            for item in _evidence_items[:8]
        ]
        ledger.log_retrieval_event(
            {
                "request_id": request_id,
                "intent_id": request_id,
                "op_kind": "PIPELINE",
                "status": _rs.get("status", "OK"),
                "reason_code": _rs.get("reason_code"),
                "stage": "cortex_orchestrator._async_post_process",
                "payload": {
                    "candidate_count": _rs.get("candidate_count", 0),
                    "route_count": _rs.get("route_count", 0),
                    "topk_count": _rs.get("topk_count", 0),
                    "degraded": bool(_rs.get("degraded", False)),
                    "query_plan": _query_plan,
                    "topk": _topk_snippets,
                    "agentic_meta": _rs.get("agentic_meta"),
                    "embedding_model_id": _rs.get("embedding_model_id"),
                    "embedding_provider": _rs.get("embedding_provider"),
                    "effective_embedding_provider": _rs.get("effective_embedding_provider"),
                    "fallback_embedding_used": bool(_rs.get("fallback_embedding_used", False)),
                    "fallback_embedding_reason_code": _rs.get("fallback_embedding_reason_code"),
                    "surface_memory_trace": _rs.get("surface_memory_trace") or {},
                    "surface_memory_debug_trace": _rs.get("surface_memory_debug_trace") or {},
                },
                "detail": (
                    f"candidate={_rs.get('candidate_count', 0)} "
                    f"topk={len(_topk_snippets)} "
                    f"intent={_query_plan.get('intent', 'unknown')}"
                ),
                "ts_ms": _now,
            }
        )

    _retrieval_status = _normalized_retrieval_summary.get("status", "SKIPPED")
    _retrieval_count = _normalized_retrieval_summary.get("candidate_count", 0)
    _retrieval_reason = str(_normalized_retrieval_summary.get("reason_code") or "").strip() or None
    _retrieval_output = {
        "candidate_count": _retrieval_count,
        "status": _retrieval_status,
        "reason_code": _retrieval_reason,
        "error": _normalized_retrieval_summary.get("error"),
        "route_count": _normalized_retrieval_summary.get("route_count"),
        "topk_count": _normalized_retrieval_summary.get("topk_count"),
        "degraded": _normalized_retrieval_summary.get("degraded"),
        "target_stores": _normalized_retrieval_summary.get("target_stores"),
        "query_plan": _normalized_retrieval_summary.get("query_plan"),
        "agentic_meta": _normalized_retrieval_summary.get("agentic_meta"),
        "embedding_model_id": _normalized_retrieval_summary.get("embedding_model_id"),
        "embedding_provider": _normalized_retrieval_summary.get("embedding_provider"),
        "effective_embedding_provider": _normalized_retrieval_summary.get("effective_embedding_provider"),
        "fallback_embedding_used": bool(_normalized_retrieval_summary.get("fallback_embedding_used", False)),
        "fallback_embedding_reason_code": _normalized_retrieval_summary.get("fallback_embedding_reason_code"),
        "surface_memory_trace": _normalized_retrieval_summary.get("surface_memory_trace") or {},
        "surface_memory_debug_trace": _normalized_retrieval_summary.get("surface_memory_debug_trace") or {},
    }
    _query_plan_output = _coerce_trace_mapping(_normalized_retrieval_summary.get("query_plan"))
    _agentic_rag_output = _coerce_trace_mapping(_normalized_retrieval_summary.get("agentic_meta"))
    _emotion_trace = _summarize_emotion_context_for_trace(emotion_context)
    _persona_memory_trace = _summarize_persona_resident_memory_for_trace(
        bond_summary=bond_summary,
        retrieval_summary=_normalized_retrieval_summary,
        retrieval_evidence_items=_evidence_items,
    )
    _motive_trace = _summarize_motive_state_for_trace(motive_state)
    _conversation_interpretation_trace = _summarize_conversation_interpretation_for_trace(
        _trace_stage_meta.get("conversation_interpretation")
    )
    _topic_ledger_trace = _summarize_topic_ledger_for_trace(_trace_stage_meta.get("topic_ledger"))
    _tactic_ledger_trace = _summarize_tactic_ledger_for_trace(_trace_stage_meta.get("tactic_ledger"))
    _affect_state_trace = _summarize_affect_state_for_trace(_trace_stage_meta.get("affect_state"))
    _relationship_state_trace = _summarize_relationship_state_for_trace(_trace_stage_meta.get("relationship_state"))
    _drive_state_trace = _summarize_drive_state_for_trace(_trace_stage_meta.get("drive_state"))
    _reply_plan_trace = _summarize_reply_plan_for_trace(_trace_stage_meta.get("reply_plan"))
    _conversation_history_trace = _coerce_trace_mapping(_trace_stage_meta.get("conversation_history"))
    _prompt_builder_output: dict[str, Any] = {}
    if _motive_trace:
        _prompt_builder_output["motive_state"] = _motive_trace
    if _conversation_interpretation_trace:
        _prompt_builder_output["conversation_interpretation"] = _conversation_interpretation_trace
    if _topic_ledger_trace:
        _prompt_builder_output["topic_ledger"] = _topic_ledger_trace
    if _tactic_ledger_trace:
        _prompt_builder_output["tactic_ledger"] = _tactic_ledger_trace
    if _affect_state_trace:
        _prompt_builder_output["affect_state"] = _affect_state_trace
    if _relationship_state_trace:
        _prompt_builder_output["relationship_state"] = _relationship_state_trace
    if _drive_state_trace:
        _prompt_builder_output["drive_state"] = _drive_state_trace
    if _reply_plan_trace:
        _prompt_builder_output["reply_plan"] = _reply_plan_trace
    if _conversation_history_trace:
        _prompt_builder_output["conversation_history"] = _conversation_history_trace
    _prompt_builder_status = "OK" if (llm_input_digest or _prompt_builder_output) else "SKIPPED"
    _prompt_builder_node = _build_trace_stage_node(
        node_id="prompt_builder",
        node_type="system",
        display_name="Prompt Builder",
        status=_prompt_builder_status,
        ts_ms=ts_ms,
        output_data=_prompt_builder_output,
    )

    _dispatch_used = bool(_trace_stage_meta.get("executive_router_used"))
    _chat_used = bool(_trace_stage_meta.get("surface_reply_used"))
    _orchestrator_used = bool(_trace_stage_meta.get("cortex_orchestrator_used"))
    _path = str(
        _trace_stage_meta.get("dispatch_route")
        or ("delegate" if delegated_agents else "chat_direct")
    ).strip() or ("delegate" if delegated_agents else "chat_direct")
    _dispatch_reason = str(_trace_stage_meta.get("dispatch_reason") or "").strip() or None
    _dispatch_target = str(_trace_stage_meta.get("dispatch_target") or "").strip() or None
    _chat_mode = str(
        _trace_stage_meta.get("chat_mode")
        or ("sub_agent" if delegated_agents else "direct")
    ).strip() or ("sub_agent" if delegated_agents else "direct")
    _chat_sub_agent_id = str(_trace_stage_meta.get("chat_sub_agent_id") or "").strip() or None
    _dispatch_output = {
        "route": _path,
        "reason": _dispatch_reason,
        "target": _dispatch_target,
    }
    _chat_output = {
        "mode": _chat_mode,
        "sub_agent_id": _chat_sub_agent_id,
        "delegated_agent_count": len(list(delegated_agents or [])),
    }
    _cortex_output = {
        "entrypoint": "cortex_orchestrator",
        "path": _path,
        "retrieval_status": _retrieval_status,
        "retrieval_candidate_count": _retrieval_count,
        "delegated_agent_count": len(list(delegated_agents or [])),
        "orchestrator_used": _orchestrator_used,
        "executive_router_used": bool(_trace_stage_meta.get("executive_router_used")),
        "trace_stage_meta": _trace_stage_meta,
    }

    _trace_nodes_v2: list[dict[str, Any]] = [
        _build_trace_stage_node(
            node_id="airlock_ingress",
            node_type="governor",
            display_name="Airlock Ingress",
            ts_ms=ts_ms,
            status="OK",
        ),
        _build_trace_stage_node(
            node_id="governor",
            node_type="governor",
            display_name="Governor",
            ts_ms=ts_ms,
            status=gov_decision.get("decision", "APPROVE") if gov_decision else "OK",
            latency_ms=gov_decision.get("latency_ms", 0) if gov_decision else 0,
            reason_code=gov_decision.get("reason_code") if gov_decision else None,
            output_data={"rule_trace": gov_decision.get("rule_trace")} if gov_decision else None,
        ),
        _build_trace_stage_node(
            node_id="emotion_context",
            node_type="system",
            display_name="Emotion Context",
            ts_ms=ts_ms,
            status="OK" if _emotion_trace else "SKIPPED",
            output_data=_emotion_trace,
        ),
        _build_trace_stage_node(
            node_id="persona_resident_memory",
            node_type="memory",
            display_name="Persona Resident Memory",
            ts_ms=ts_ms,
            status="OK" if _persona_memory_trace else "SKIPPED",
            output_data=_persona_memory_trace,
        ),
        _build_trace_stage_node(
            node_id="motive_state",
            node_type="system",
            display_name="Motive State",
            ts_ms=ts_ms,
            status="OK" if _motive_trace else "SKIPPED",
            output_data=_motive_trace,
        ),
        _build_trace_stage_node(
            node_id="conversation_interpretation",
            node_type="system",
            display_name="Conversation Interpretation",
            ts_ms=ts_ms,
            status="OK" if _conversation_interpretation_trace else "SKIPPED",
            output_data=_conversation_interpretation_trace,
        ),
        _build_trace_stage_node(
            node_id="topic_ledger",
            node_type="system",
            display_name="Topic Ledger",
            ts_ms=ts_ms,
            status="OK" if _topic_ledger_trace else "SKIPPED",
            output_data=_topic_ledger_trace,
        ),
        _build_trace_stage_node(
            node_id="tactic_ledger",
            node_type="system",
            display_name="Tactic Ledger",
            ts_ms=ts_ms,
            status="OK" if _tactic_ledger_trace else "SKIPPED",
            output_data=_tactic_ledger_trace,
        ),
        _build_trace_stage_node(
            node_id="affect_state",
            node_type="system",
            display_name="Affect State",
            ts_ms=ts_ms,
            status="OK" if _affect_state_trace else "SKIPPED",
            output_data=_affect_state_trace,
        ),
        _build_trace_stage_node(
            node_id="relationship_state",
            node_type="system",
            display_name="Relationship State",
            ts_ms=ts_ms,
            status="OK" if _relationship_state_trace else "SKIPPED",
            output_data=_relationship_state_trace,
        ),
        _build_trace_stage_node(
            node_id="drive_state",
            node_type="system",
            display_name="Drive State",
            ts_ms=ts_ms,
            status="OK" if _drive_state_trace else "SKIPPED",
            output_data=_drive_state_trace,
        ),
        _build_trace_stage_node(
            node_id="reply_plan",
            node_type="system",
            display_name="Reply Plan",
            ts_ms=ts_ms,
            status="OK" if _reply_plan_trace else "SKIPPED",
            output_data=_reply_plan_trace,
        ),
        _build_trace_stage_node(
            node_id="cortex_orchestrator",
            node_type="system",
            display_name="Cortex Orchestrator",
            ts_ms=ts_ms,
            status="OK" if narrative.strip() else "DEGRADED",
            output_data=_cortex_output,
        ),
    ]
    if _retrieval_status != "SKIPPED" and _query_plan_output:
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="query_planner",
                node_type="retrieval",
                display_name="Query Planner",
                ts_ms=ts_ms,
                status=_retrieval_status,
                reason_code=_retrieval_reason,
                output_data=_query_plan_output,
            )
        )
    if _retrieval_status != "SKIPPED" and _agentic_rag_output:
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="agentic_rag",
                node_type="retrieval",
                display_name="Agentic RAG",
                ts_ms=ts_ms,
                status=_retrieval_status,
                reason_code=_retrieval_reason,
                output_data=_agentic_rag_output,
            )
        )
    if _retrieval_status != "SKIPPED" or _retrieval_count or _retrieval_output.get("degraded"):
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="recall_fusion",
                node_type="recall",
                display_name="Recall Fusion",
                ts_ms=ts_ms,
                status=_retrieval_status,
                reason_code=_retrieval_reason,
                output_data=_retrieval_output,
            )
        )
    if _dispatch_used:
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="executive_router",
                node_type="agent",
                display_name="Executive Router",
                ts_ms=ts_ms,
                status="OK",
                reason_code=_dispatch_reason,
                output_data=_dispatch_output,
            )
        )
    for da in (delegated_agents or []):
        _agent_id = str(da.get("agent_id") or "?")
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id=f"sub_agent:{_agent_id}",
                node_type="agent",
                display_name=f"Sub Agent: {_agent_id}",
                ts_ms=ts_ms,
                status="OK" if da.get("success") else "FAILED",
                latency_ms=da.get("latency_ms", 0),
                error=da.get("error"),
                output_data={
                    "agent_id": _agent_id,
                    "audit_refs": da.get("audit_refs"),
                },
            )
        )
    if _chat_used:
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="surface_reply",
                node_type="agent",
                display_name="Surface Reply",
                ts_ms=ts_ms,
                status="OK" if narrative.strip() else "DEGRADED",
                output_data=_chat_output,
            )
        )
    for _surface_key, _surface_name in (
        ("identity_surface_render", "Identity Surface"),
        ("surface_render", "Surface Renderer"),
        ("surface_renderer", "Surface Renderer"),
    ):
        _surface_meta = _coerce_trace_mapping(_trace_stage_meta.get(_surface_key))
        if not _surface_meta:
            continue
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id=_surface_key,
                node_type="surface",
                display_name=_surface_name,
                ts_ms=ts_ms,
                status=str(_surface_meta.get("status") or "OK").strip().upper() or "OK",
                reason_code=str(_surface_meta.get("reason_code") or "").strip() or None,
                latency_ms=int(_surface_meta.get("latency_ms") or 0) if _surface_meta.get("latency_ms") is not None else None,
                provider=str(_surface_meta.get("provider_name") or "").strip() or None,
                model_id=str(_surface_meta.get("model_id") or "").strip() or None,
                output_data=_surface_meta,
                invoked=bool(_surface_meta.get("invoked")),
                affected_output=bool(_surface_meta.get("affected_output")),
                source=str(_surface_meta.get("source") or "").strip() or None,
            )
        )
    _trace_nodes_v2.extend(
        _load_request_aux_llm_trace_nodes(
            ledger=ledger,
            request_id=request_id,
        )
    )
    _trace_nodes_v2.append(_prompt_builder_node)
    _trace_nodes_v2.append(
        _build_trace_stage_node(
            node_id="llm_stream",
            node_type="llm",
            display_name="LLM Stream",
            ts_ms=_now,
            status="OK" if narrative.strip() else "DEGRADED",
            latency_ms=llm_latency_ms,
            provider=provider_name,
            model_id=model_id,
            input_data={
                "first_token_ms": first_token_ms,
                "buffer_stream": buffer_stream,
                "input_digest": (llm_input_digest or "")[:300] or None,
            },
            output_data={
                "final_response": (narrative or "")[:800] or None,
            },
        )
    )
    _trace_nodes_v2.append(
        _build_trace_stage_node(
            node_id="airlock_egress",
            node_type="governor",
            display_name="Airlock Egress",
            ts_ms=_now,
            status="OK",
        )
    )

    trace_nodes = list(_trace_nodes_v2)
    _pii_detail: dict[str, Any] = {}
    if pii_ingress_stats:
        _pii_detail["ingress"] = pii_ingress_stats
    if pii_egress_stats:
        _pii_detail["egress"] = pii_egress_stats
    if _pii_detail:
        trace_nodes.append(
            {
                "node_id": "pii_filter",
                "node_type": "governor",
                "display_name": "PII 过滤",
                "side": "host",
                "status": "OK",
                "output_data": _pii_detail,
                "ts_ms": _now,
            }
        )
    trace_nodes = sorted(trace_nodes, key=_trace_node_sort_key)

    _prev_node_id: str | None = None
    for _node in trace_nodes:
        _node_id = str(_node.get("node_id") or "").strip()
        _node_type = str(_node.get("node_type") or "system").strip().lower() or "system"
        _node_status = str(_node.get("status") or "OK").strip().upper() or "OK"
        _node_reason = str(_node.get("reason_code") or "").strip() or None
        _node_output = _node.get("output_data")
        _node_ts = int(_node.get("ts_ms") or ts_ms)
        _node["agent_envelope"] = _build_trace_agent_envelope(
            request_id=request_id,
            node_id=_node_id,
            parent_node_id=_prev_node_id,
            node_type=_node_type,
            status=_node_status,
            reason_code=_node_reason,
            output_data=_node_output,
            fallback_used=False,
            degrade_code=None,
            ts_ms=_node_ts,
        )
        if _node_id:
            _prev_node_id = _node_id

    # 边按稳定排序后的节点顺序连接，形成“本次实际可见主链”；这不是 DAG
    # 调度依赖图，不能被当作重放或授权执行的依据。
    trace_edges = [
        {"source": trace_nodes[i]["node_id"], "target": trace_nodes[i + 1]["node_id"]}
        for i in range(len(trace_nodes) - 1)
    ]

    ledger.log_graph_run_trace(
        {
            "request_id": request_id,
            "session_id": session_id,
            "total_latency_ms": total_latency_ms,
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None,
            "node_count": len(trace_nodes),
            "nodes_json": trace_nodes,
            "edges_json": trace_edges,
            "ts_ms": _now,
        }
    )


async def _persist_post_process_audit(**kwargs: Any) -> None:
    """在线程中写入诊断投影，避免同步 SQLite I/O 占用 Host 事件循环。

    它不属于用户可见回复的提交条件：诊断缺失可被后续排查，而聊天主链不能
    因审计写入较慢而被拖住。
    """

    await asyncio.to_thread(_persist_post_process_audit_sync, **kwargs)
