from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

from host_brain.agent_runtime.agent_registry import AGENT_ID_RETRIEVAL
from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import get_tools_for_agent
from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_contract_json(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text


def _resolve_retrieval_agent_runtime_texts() -> dict[str, Any]:
    return _load_template_mapping("retrieval_agent_handler.runtime_texts")


def _resolve_retrieval_agent_system_prompt() -> str:
    return _mapping_text(
        _resolve_retrieval_agent_runtime_texts(),
        "system_prompt_extra",
    )


def _resolve_retrieval_agent_error_summary() -> str:
    return _mapping_text(
        _resolve_retrieval_agent_runtime_texts(),
        "handler_error_summary",
    )


def retrieval_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()

    out = dict(payload)

    try:
        user_input = str(payload.get("user_input") or payload.get("query") or "").strip()
        tools = get_tools_for_agent(AGENT_ID_RETRIEVAL, allow_side_effects=False)
        gateway = ctx.get("gateway") or payload.get("_gateway")

        react_result = run_react_loop(
            agent_id=AGENT_ID_RETRIEVAL,
            user_input=user_input,
            tools=tools,
            gateway=gateway,
            system_prompt_extra=_resolve_retrieval_agent_system_prompt(),
            max_steps=int(os.environ.get("RETRIEVAL_AGENT_REACT_MAX_STEPS") or "3"),
            allow_side_effects=False,
            context=ctx,
        )

        out["react_scratchpad"] = react_result.to_scratchpad_dict()
        out["agent_output"] = react_result.to_public_output_dict()

        retrieval_payload = dict((payload.get("workbench_retrieval") or {}).get("payload") or {})
        retrieval_source = str(retrieval_payload.get("source") or "knowledge_base").strip()
        result_items = retrieval_payload.get("results") or retrieval_payload.get("items") or []
        result_count = len(result_items) if isinstance(result_items, list) else 0

        artifact_ref = f"artifact://{request_id}/{AGENT_ID_RETRIEVAL}_{int(time.time() * 1000)}"
        write_agent_artifact_to_state(
            out,
            agent_id=AGENT_ID_RETRIEVAL,
            artifact_ref=artifact_ref,
            summary=react_result.final_answer[:200],
            confidence=react_result.confidence,
            risk_flags=react_result.risk_flags,
        )

        out["retrieval_agent_trace"] = {
            "agent_id": AGENT_ID_RETRIEVAL,
            "mode": "react_loop",
            "artifact_ref": artifact_ref,
            "retrieval_source": retrieval_source,
            "result_count": result_count,
            "total_steps": react_result.total_steps,
            "stopped_reason": react_result.stopped_reason,
            "ts_ms": int(time.time() * 1000),
        }
    except Exception as exc:
        artifacts = out.setdefault("agent_artifacts", {})
        artifacts[AGENT_ID_RETRIEVAL] = {
            "artifact_ref": f"artifact://{request_id}/{AGENT_ID_RETRIEVAL}_error",
            "summary": _resolve_retrieval_agent_error_summary(),
            "confidence": 0.0,
            "risk_flags": [f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out
