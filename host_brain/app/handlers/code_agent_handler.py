from __future__ import annotations

import os
import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_CODE
from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import get_tools_for_agent
from host_brain.app.prompt_template_store import resolve_prompt_template_text
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def _resolve_code_agent_system_prompt() -> str:
    return resolve_prompt_template_text("code_agent_handler.system_prompt_extra")


def _resolve_code_agent_error_summary() -> str:
    return resolve_prompt_template_text("code_agent_handler.error_summary")


def code_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()

    out = dict(payload)

    try:
        user_input = str(payload.get("user_input") or payload.get("query") or "").strip()
        tools = get_tools_for_agent(AGENT_ID_CODE, allow_side_effects=False)
        gateway = ctx.get("gateway") or payload.get("gateway") or payload.get("_gateway")

        react_result = run_react_loop(
            agent_id=AGENT_ID_CODE,
            user_input=user_input,
            tools=tools,
            gateway=gateway,
            system_prompt_extra=_resolve_code_agent_system_prompt(),
            max_steps=int(os.environ.get("CODE_AGENT_REACT_MAX_STEPS") or "3"),
            allow_side_effects=False,
            context=ctx,
        )

        out["react_scratchpad"] = react_result.to_scratchpad_dict()

        artifact_ref = f"artifact://{request_id}/{AGENT_ID_CODE}_{int(time.time() * 1000)}"
        write_agent_artifact_to_state(
            out,
            agent_id=AGENT_ID_CODE,
            artifact_ref=artifact_ref,
            summary=react_result.final_answer[:200],
            confidence=react_result.confidence,
            risk_flags=react_result.risk_flags,
        )

        out["code_agent_trace"] = {
            "agent_id": AGENT_ID_CODE,
            "mode": "react_loop",
            "artifact_ref": artifact_ref,
            "total_steps": react_result.total_steps,
            "stopped_reason": react_result.stopped_reason,
            "ts_ms": int(time.time() * 1000),
        }
    except Exception as exc:
        artifacts = out.setdefault("agent_artifacts", {})
        artifacts[AGENT_ID_CODE] = {
            "artifact_ref": f"artifact://{request_id}/{AGENT_ID_CODE}_error",
            "summary": _resolve_code_agent_error_summary(),
            "confidence": 0.0,
            "risk_flags": [f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out
