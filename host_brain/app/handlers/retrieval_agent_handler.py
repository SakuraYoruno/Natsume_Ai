"""Readonly retrieval result adapter and Research Domain Pack handoff.

This boundary never executes ``search_knowledge`` or resolves a process-local
tool registry.  It projects an already-governed retrieval result, or prepares
request material for the canonical Research/Browser Domain Pack.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.agent_runtime.agent_registry import AGENT_ID_RETRIEVAL
from host_brain.domain_packs.research_browser import RESEARCH_BROWSER_PACK_ID

from .agent_boundary import (
    finalize_agent_boundary,
    first_public_text,
    nested_payload,
    request_id_for,
)


RETRIEVAL_HANDOFF_SCHEMA = "Natsume.retrieval.domain-handoff.v1"
RETRIEVAL_CAPABILITY_ID = "browser_research"
RETRIEVAL_TASK_ID = "research.browser.worker"
RETRIEVAL_RESULT_READY = "RETRIEVAL_RESULT_READY"
RETRIEVAL_DOMAIN_HANDOFF_REQUIRED = "RETRIEVAL_DOMAIN_HANDOFF_REQUIRED"


def retrieval_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    out = dict(payload or {})
    request_id = request_id_for(out, ctx, prefix="retrieval")
    query = _request_text(out, ctx)

    raw_result = out.get("workbench_retrieval")
    if isinstance(raw_result, Mapping):
        result_payload = nested_payload(raw_result)
        items_raw = result_payload.get("results") or result_payload.get("items") or []
        items = list(items_raw) if isinstance(items_raw, list) else []
        source = str(result_payload.get("source") or "knowledge_base").strip() or "knowledge_base"
        summary = first_public_text(raw_result)
        if not summary:
            summary = f"只读检索结果已就绪，共 {len(items)} 条；结果由 {source} 提供。"
        gateway_status = str(raw_result.get("gateway_status") or "OK").strip().upper()
        confidence_raw = result_payload.get("confidence", 0.8 if items else 0.5)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0.0
        risk_flags = [] if gateway_status == "OK" else [f"GATEWAY_STATUS:{gateway_status}"]
        return finalize_agent_boundary(
            out,
            agent_id=AGENT_ID_RETRIEVAL,
            trace_key="retrieval_agent_trace",
            request_id=request_id,
            summary=summary,
            mode="readonly_result",
            reason_code=RETRIEVAL_RESULT_READY,
            confidence=confidence,
            risk_flags=risk_flags,
            trace_fields={
                "retrieval_source": source,
                "result_count": len(items),
                "gateway_status": gateway_status,
            },
        )

    handoff = _build_retrieval_handoff(request_id=request_id, query=query)
    out["retrieval_domain_handoff"] = handoff
    return finalize_agent_boundary(
        out,
        agent_id=AGENT_ID_RETRIEVAL,
        trace_key="retrieval_agent_trace",
        request_id=request_id,
        summary=(
            "已准备 Research/Browser Domain Pack 的只读检索交接；"
            "应用 Agent 没有直接调用工具、网络或 capability。"
        ),
        mode="domain_handoff",
        reason_code=RETRIEVAL_DOMAIN_HANDOFF_REQUIRED,
        confidence=0.5 if query else 0.0,
        risk_flags=[RETRIEVAL_DOMAIN_HANDOFF_REQUIRED],
        trace_fields={
            "retrieval_source": "domain_pack",
            "result_count": 0,
            "gateway_status": "NOT_EXECUTED",
        },
        artifact_suffix="handoff",
    )


def _request_text(payload: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    for value in (
        payload.get("user_input"),
        payload.get("query"),
        context.get("task"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _build_retrieval_handoff(*, request_id: str, query: str) -> dict[str, Any]:
    return {
        "schema_version": RETRIEVAL_HANDOFF_SCHEMA,
        "request_id": request_id,
        "domain_pack": {
            "pack_id": RESEARCH_BROWSER_PACK_ID,
            "capability_id": RETRIEVAL_CAPABILITY_ID,
            "task_id": RETRIEVAL_TASK_ID,
            "request_contract": "host_brain.domain_packs.contracts.DomainPackRequest",
            "authority_path": "CapabilityBroker -> DomainPackRegistry -> TaskPort",
        },
        "request_material": {
            "query": query,
            "trusted_authority": False,
            "authority_source": "UNTRUSTED_APPLICATION_REQUEST",
        },
        "side_effects_executed": False,
    }


__all__ = [
    "RETRIEVAL_CAPABILITY_ID",
    "RETRIEVAL_DOMAIN_HANDOFF_REQUIRED",
    "RETRIEVAL_HANDOFF_SCHEMA",
    "RETRIEVAL_RESULT_READY",
    "retrieval_agent_handler",
]
