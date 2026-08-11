"""Translation application adapter for the governed graph model plane.

The handler owns no model provider, tool registry, ReAct loop, or capability
broker.  Production graph composition supplies ``invoke_model_node``; tests
and other callers may instead provide an already-governed
``workbench_translate`` result.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.agent_runtime.agent_registry import AGENT_ID_TRANSLATE

from .agent_boundary import (
    finalize_agent_boundary,
    first_public_text,
    nested_payload,
    request_id_for,
)


TRANSLATE_MODEL_NODE_ID = "workbench_translate"
TRANSLATE_RESULT_READY = "TRANSLATE_RESULT_READY"
TRANSLATE_MODEL_NODE_UNAVAILABLE = "TRANSLATE_MODEL_NODE_UNAVAILABLE"
TRANSLATE_MODEL_NODE_FAILED = "TRANSLATE_MODEL_NODE_FAILED"


def translate_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    out = dict(payload or {})
    request_id = request_id_for(out, ctx, prefix="translate")

    model_result = out.get(TRANSLATE_MODEL_NODE_ID)
    invoke_model_node = ctx.get("invoke_model_node")
    if model_result is None and callable(invoke_model_node):
        try:
            invoked = invoke_model_node(TRANSLATE_MODEL_NODE_ID, out)
            if isinstance(invoked, Mapping):
                out.update(dict(invoked))
                model_result = out.get(TRANSLATE_MODEL_NODE_ID, invoked)
        except Exception as exc:
            return finalize_agent_boundary(
                out,
                agent_id=AGENT_ID_TRANSLATE,
                trace_key="translate_agent_trace",
                request_id=request_id,
                summary="翻译模型节点暂不可用；本次没有调用兼容工具或执行任何副作用。",
                mode="governed_model_node",
                reason_code=TRANSLATE_MODEL_NODE_FAILED,
                confidence=0.0,
                risk_flags=[f"MODEL_NODE_ERROR:{type(exc).__name__}"],
                trace_fields={
                    "model_node_id": TRANSLATE_MODEL_NODE_ID,
                    "gateway_status": "DEGRADED",
                },
                artifact_suffix="degraded",
            )

    result_payload = nested_payload(model_result)
    translated_text = first_public_text(model_result)
    gateway_status = str(
        (model_result.get("gateway_status") if isinstance(model_result, Mapping) else "")
        or result_payload.get("gateway_status")
        or ("OK" if translated_text else "DEGRADED")
    ).strip().upper()

    if not translated_text:
        return finalize_agent_boundary(
            out,
            agent_id=AGENT_ID_TRANSLATE,
            trace_key="translate_agent_trace",
            request_id=request_id,
            summary="尚无受治理翻译模型结果；本次没有调用兼容工具或执行任何副作用。",
            mode="governed_model_node",
            reason_code=TRANSLATE_MODEL_NODE_UNAVAILABLE,
            confidence=0.0,
            risk_flags=[TRANSLATE_MODEL_NODE_UNAVAILABLE],
            trace_fields={
                "model_node_id": TRANSLATE_MODEL_NODE_ID,
                "gateway_status": gateway_status,
            },
            artifact_suffix="degraded",
        )

    confidence_raw = result_payload.get("confidence", 0.85 if gateway_status == "OK" else 0.4)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    risk_flags = [] if gateway_status == "OK" else [f"GATEWAY_STATUS:{gateway_status}"]
    return finalize_agent_boundary(
        out,
        agent_id=AGENT_ID_TRANSLATE,
        trace_key="translate_agent_trace",
        request_id=request_id,
        summary=translated_text,
        mode="governed_model_node",
        reason_code=TRANSLATE_RESULT_READY,
        confidence=confidence,
        risk_flags=risk_flags,
        trace_fields={
            "model_node_id": TRANSLATE_MODEL_NODE_ID,
            "gateway_status": gateway_status,
        },
    )


__all__ = [
    "TRANSLATE_MODEL_NODE_FAILED",
    "TRANSLATE_MODEL_NODE_ID",
    "TRANSLATE_MODEL_NODE_UNAVAILABLE",
    "TRANSLATE_RESULT_READY",
    "translate_agent_handler",
]
