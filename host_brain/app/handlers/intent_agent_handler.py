from __future__ import annotations

import os
import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_INTENT
from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import get_tools_for_agent
from host_brain.app.prompt_template_store import resolve_prompt_template_text
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def _parse_bool_env(key: str, default: bool = False) -> bool:
    raw = str(os.environ.get(key, "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _resolve_intent_agent_system_prompt() -> str:
    return resolve_prompt_template_text("intent_agent_handler.system_prompt_extra")


def _resolve_intent_agent_error_summary() -> str:
    return resolve_prompt_template_text("intent_agent_handler.error_summary")


def intent_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()
    intent_subgraph_enabled = _parse_bool_env("INTENT_SUBGRAPH_ENABLED", default=False)

    out = dict(payload)

    try:
        if intent_subgraph_enabled:
            user_input = str(payload.get("user_input") or payload.get("query") or "").strip()
            tools = get_tools_for_agent(AGENT_ID_INTENT, allow_side_effects=True)
            gateway = ctx.get("gateway") or payload.get("_gateway")

            react_result = run_react_loop(
                agent_id=AGENT_ID_INTENT,
                user_input=user_input,
                tools=tools,
                gateway=gateway,
                system_prompt_extra=_resolve_intent_agent_system_prompt(),
                max_steps=int(os.environ.get("INTENT_REACT_MAX_STEPS") or "5"),
                allow_side_effects=True,
                context=ctx,
            )

            out["react_scratchpad"] = react_result.to_scratchpad_dict()

            artifact_ref = f"artifact://{request_id}/{AGENT_ID_INTENT}_{int(time.time() * 1000)}"
            write_agent_artifact_to_state(
                out,
                agent_id=AGENT_ID_INTENT,
                artifact_ref=artifact_ref,
                summary=react_result.final_answer[:200],
                confidence=react_result.confidence,
                risk_flags=react_result.risk_flags + ["EXEC_INTENT_SCENE"],
            )

            out["intent_agent_trace"] = {
                "agent_id": AGENT_ID_INTENT,
                "mode": "react_loop",
                "artifact_ref": artifact_ref,
                "total_steps": react_result.total_steps,
                "stopped_reason": react_result.stopped_reason,
                "intent_subgraph_enabled": True,
                "ts_ms": int(time.time() * 1000),
            }
        else:
            tool_planner_output = dict(payload.get("tool_planner") or {})
            core_reasoner_output = dict(payload.get("core_reasoner_primary") or {})

            draft_summary = str(
                tool_planner_output.get("payload", {}).get("plan_summary")
                or tool_planner_output.get("payload", {}).get("response_text")
                or core_reasoner_output.get("payload", {}).get("response_text")
                or "执行意图计划已生成"
            ).strip()[:200]

            gateway_status = str(
                tool_planner_output.get("gateway_status") or core_reasoner_output.get("gateway_status") or "OK"
            ).strip().upper()

            confidence = 0.75 if gateway_status == "OK" else 0.15
            risk_flags = ["EXEC_INTENT_SCENE"]
            if gateway_status != "OK":
                risk_flags.append(f"GATEWAY_STATUS:{gateway_status}")

            safety_output = dict(payload.get("safety_verifier") or {})
            safety_risk = str(safety_output.get("payload", {}).get("risk_level") or "").strip().upper()
            if safety_risk in {"HIGH", "CRITICAL"}:
                risk_flags.append(f"SAFETY_RISK:{safety_risk}")
                confidence = min(confidence, 0.4)

            artifact_ref = f"artifact://{request_id}/{AGENT_ID_INTENT}_{int(time.time() * 1000)}"
            write_agent_artifact_to_state(
                out,
                agent_id=AGENT_ID_INTENT,
                artifact_ref=artifact_ref,
                summary=draft_summary,
                confidence=confidence,
                risk_flags=risk_flags,
            )

            out["intent_agent_trace"] = {
                "agent_id": AGENT_ID_INTENT,
                "mode": "legacy_chain",
                "artifact_ref": artifact_ref,
                "gateway_status": gateway_status,
                "safety_risk": safety_risk or "NONE",
                "intent_subgraph_enabled": False,
                "ts_ms": int(time.time() * 1000),
            }
    except Exception as exc:
        artifacts = out.setdefault("agent_artifacts", {})
        artifacts[AGENT_ID_INTENT] = {
            "artifact_ref": f"artifact://{request_id}/{AGENT_ID_INTENT}_error",
            "summary": _resolve_intent_agent_error_summary(),
            "confidence": 0.0,
            "risk_flags": ["EXEC_INTENT_SCENE", f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out
