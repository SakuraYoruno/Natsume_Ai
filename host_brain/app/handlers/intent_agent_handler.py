"""Intent plan projection and governed-execution handoff.

The application Agent may describe an execution request, but it cannot run a
skill, invoke a model, or dispatch another Agent.  A caller must re-admit the
proposal through ``HostCommand -> immutable TaskGraph`` before a governed
worker can consume it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.agent_runtime.agent_registry import AGENT_ID_INTENT

from .agent_boundary import finalize_agent_boundary, first_public_text, nested_payload, request_id_for


INTENT_HANDOFF_SCHEMA = "Natsume.intent.execution-handoff.v1"
INTENT_GOVERNED_EXECUTION_REQUIRED = "INTENT_GOVERNED_EXECUTION_REQUIRED"


def intent_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    out = dict(payload or {})
    request_id = request_id_for(out, ctx, prefix="intent")

    planner_raw = out.get("tool_planner")
    reasoner_raw = out.get("core_reasoner_primary")
    planner = nested_payload(planner_raw)
    reasoner = nested_payload(reasoner_raw)
    summary = (
        first_public_text(planner_raw)
        or first_public_text(reasoner_raw)
        or "执行意图已形成受治理交接；尚未执行任何工具或子 Agent。"
    )
    gateway_status = str(
        (planner_raw.get("gateway_status") if isinstance(planner_raw, Mapping) else "")
        or (reasoner_raw.get("gateway_status") if isinstance(reasoner_raw, Mapping) else "")
        or "NOT_EXECUTED"
    ).strip().upper()

    safety_raw = out.get("safety_verifier")
    safety = nested_payload(safety_raw)
    safety_risk = str(safety.get("risk_level") or "NONE").strip().upper()
    risk_flags = ["EXEC_INTENT_SCENE", INTENT_GOVERNED_EXECUTION_REQUIRED]
    confidence = 0.7 if planner or reasoner else 0.4
    if gateway_status not in {"OK", "NOT_EXECUTED"}:
        risk_flags.append(f"GATEWAY_STATUS:{gateway_status}")
        confidence = min(confidence, 0.3)
    if safety_risk in {"HIGH", "CRITICAL"}:
        risk_flags.append(f"SAFETY_RISK:{safety_risk}")
        confidence = min(confidence, 0.2)

    out["intent_execution_handoff"] = _build_execution_handoff(
        request_id=request_id,
        plan_summary=summary,
        requested_tools=_requested_tools(planner),
        safety_risk=safety_risk,
    )
    return finalize_agent_boundary(
        out,
        agent_id=AGENT_ID_INTENT,
        trace_key="intent_agent_trace",
        request_id=request_id,
        summary=summary,
        mode="governed_execution_handoff",
        reason_code=INTENT_GOVERNED_EXECUTION_REQUIRED,
        confidence=confidence,
        risk_flags=risk_flags,
        trace_fields={
            "gateway_status": gateway_status,
            "safety_risk": safety_risk,
            "execution_surface": "governed_runtime_admission_required",
            "model_attempt": {},
        },
        artifact_suffix="handoff",
    )


def _requested_tools(planner: Mapping[str, Any]) -> list[str]:
    raw = planner.get("tool_ids") or planner.get("tools") or []
    if isinstance(raw, str):
        values: Sequence[Any] = [raw]
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        values = raw
    else:
        values = []
    resolved: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            tool_id = str(value.get("tool_id") or value.get("tool") or value.get("name") or "").strip()
        else:
            tool_id = str(value or "").strip()
        if tool_id and tool_id not in resolved:
            resolved.append(tool_id)
    return resolved[:64]


def _build_execution_handoff(
    *,
    request_id: str,
    plan_summary: str,
    requested_tools: list[str],
    safety_risk: str,
) -> dict[str, Any]:
    return {
        "schema_version": INTENT_HANDOFF_SCHEMA,
        "request_id": request_id,
        "execution_surface": "governed_runtime_admission_required",
        "authority_path": "HostCommand -> immutable TaskGraph -> Governor -> CapabilityBroker -> Receipt",
        "request_material": {
            "plan_summary": plan_summary,
            "requested_tools": list(requested_tools),
            "safety_risk": safety_risk,
            "trusted_authority": False,
            "authority_source": "UNTRUSTED_APPLICATION_REQUEST",
        },
        "requirements": {
            "durable_principal": True,
            "governor_grant": True,
            "capability_broker": True,
            "durable_receipt": True,
        },
        "side_effects_executed": False,
    }


__all__ = [
    "INTENT_GOVERNED_EXECUTION_REQUIRED",
    "INTENT_HANDOFF_SCHEMA",
    "intent_agent_handler",
]
