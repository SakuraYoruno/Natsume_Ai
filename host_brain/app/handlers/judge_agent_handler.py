from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Any

from host_brain.agent_runtime.agent_registry import AGENT_ID_JUDGE
from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import get_tools_for_agent
from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json
from host_brain.core.trace_redaction import public_decision_summary


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


def _resolve_judge_system_prompt() -> str:
    templates = _load_template_mapping("judge_agent_handler.prompt_templates")
    return _mapping_text(
        templates,
        "system_prompt_extra",
    )


def _build_judge_user_input(agent_artifacts: dict[str, Any]) -> str:
    templates = _load_template_mapping("judge_agent_handler.prompt_templates")
    artifacts_summary: list[str] = []
    for agent_id, artifact in agent_artifacts.items():
        confidence = float(artifact.get("confidence") or 0.0)
        summary = str(artifact.get("summary") or "")[:100]
        risk_flags = list(artifact.get("risk_flags") or [])
        has_error = bool(artifact.get("error"))
        artifacts_summary.append(
            f"- {agent_id}: confidence={confidence:.2f}, summary='{summary}', risk_flags={risk_flags}, error={has_error}"
        )

    return _mapping_text(
        templates,
        "user_input_template",
        artifact_count=len(agent_artifacts),
        artifacts_desc="\n".join(artifacts_summary),
        artifacts_json=json.dumps(agent_artifacts, ensure_ascii=False, default=str)[:800],
    )


def judge_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()

    out = dict(payload)

    try:
        agent_artifacts = dict(out.get("agent_artifacts") or {})

        if not agent_artifacts:
            out["judge_result"] = {
                "status": "DEGRADED",
                "reason": "NO_ARTIFACTS",
                "selected_agent": None,
                "selected_artifact": None,
                "confidence": 0.0,
                "risk_flags": ["NO_ARTIFACTS"],
                "mode": "react_loop",
                "ts_ms": int(time.time() * 1000),
            }
            out["judge_agent_trace"] = {
                "agent_id": AGENT_ID_JUDGE,
                "mode": "react_loop",
                "artifact_ref": f"artifact://{request_id}/{AGENT_ID_JUDGE}_degraded",
                "total_steps": 0,
                "stopped_reason": "NO_ARTIFACTS",
                "ts_ms": int(time.time() * 1000),
            }
            return out

        user_input = _build_judge_user_input(agent_artifacts)
        tools = get_tools_for_agent(AGENT_ID_JUDGE, allow_side_effects=False)
        gateway = ctx.get("gateway") or payload.get("_gateway")

        react_result = run_react_loop(
            agent_id=AGENT_ID_JUDGE,
            user_input=user_input,
            tools=tools,
            gateway=gateway,
            system_prompt_extra=_resolve_judge_system_prompt(),
            max_steps=int(os.environ.get("JUDGE_AGENT_REACT_MAX_STEPS") or "3"),
            allow_side_effects=False,
            context=ctx,
        )

        out["react_scratchpad"] = react_result.to_scratchpad_dict()
        out["agent_output"] = react_result.to_public_output_dict()

        if react_result.stopped_reason == "ERROR":
            out["judge_result"] = {
                "status": "DEGRADED",
                "reason": "REACT_LOOP_ERROR",
                "selected_agent": None,
                "selected_artifact": None,
                "all_agents_evaluated": list(agent_artifacts.keys()),
                "confidence": 0.0,
                "risk_flags": ["REACT_LOOP_ERROR"],
                "mode": "react_loop",
                "ts_ms": int(time.time() * 1000),
            }
            artifact_ref = f"artifact://{request_id}/{AGENT_ID_JUDGE}_{int(time.time() * 1000)}"
            out["judge_agent_trace"] = {
                "agent_id": AGENT_ID_JUDGE,
                "mode": "react_loop",
                "artifact_ref": artifact_ref,
                "artifacts_evaluated": len(agent_artifacts),
                "selected_agent": None,
                "judge_status": "DEGRADED",
                "total_steps": react_result.total_steps,
                "stopped_reason": react_result.stopped_reason,
                "ts_ms": int(time.time() * 1000),
            }
            return out

        final_answer = react_result.final_answer or ""
        selected_agent, judge_confidence, judge_reason = _parse_judge_final_answer(final_answer, agent_artifacts)

        all_risk_flags: list[str] = []
        for artifact in agent_artifacts.values():
            all_risk_flags.extend(list(artifact.get("risk_flags") or []))
        all_risk_flags = list(dict.fromkeys(all_risk_flags))

        high_risk = any(flag in all_risk_flags for flag in {"SAFETY_RISK:HIGH", "SAFETY_RISK:CRITICAL"})
        judge_status = "DEGRADED" if high_risk or judge_confidence < 0.3 else "OK"

        selected_artifact = None
        if selected_agent and selected_agent in agent_artifacts:
            art = agent_artifacts[selected_agent]
            selected_artifact = {
                "artifact_ref": str(art.get("artifact_ref") or ""),
                "summary": str(art.get("summary") or ""),
                "confidence": float(art.get("confidence") or 0.0),
            }

        out["judge_result"] = {
            "status": judge_status,
            "reason": judge_reason,
            "selected_agent": selected_agent,
            "selected_artifact": selected_artifact,
            "all_agents_evaluated": list(agent_artifacts.keys()),
            "confidence": judge_confidence,
            "risk_flags": all_risk_flags,
            "mode": "react_loop",
            "ts_ms": int(time.time() * 1000),
        }

        artifact_ref = f"artifact://{request_id}/{AGENT_ID_JUDGE}_{int(time.time() * 1000)}"
        out["judge_agent_trace"] = {
            "agent_id": AGENT_ID_JUDGE,
            "mode": "react_loop",
            "artifact_ref": artifact_ref,
            "artifacts_evaluated": len(agent_artifacts),
            "selected_agent": selected_agent,
            "judge_status": judge_status,
            "total_steps": react_result.total_steps,
            "stopped_reason": react_result.stopped_reason,
            "ts_ms": int(time.time() * 1000),
        }
    except Exception as exc:
        out["judge_result"] = {
            "status": "DEGRADED",
            "reason": f"JUDGE_ERROR:{type(exc).__name__}",
            "selected_agent": None,
            "selected_artifact": None,
            "confidence": 0.0,
            "risk_flags": [f"JUDGE_ERROR:{type(exc).__name__}"],
            "mode": "react_loop",
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out


def _parse_judge_final_answer(
    final_answer: str,
    agent_artifacts: dict[str, Any],
) -> tuple[str | None, float, str]:
    selected_agent: str | None = None
    confidence = 0.0
    reason = "OK"

    if not agent_artifacts:
        return selected_agent, confidence, "NO_ANSWER"

    import re  # noqa: PLC0415

    match = re.search(r"selected_agent\s*=\s*(\w+)", final_answer)
    if match:
        candidate = match.group(1).strip()
        if candidate in agent_artifacts:
            selected_agent = candidate

    conf_match = re.search(r"confidence\s*=\s*([0-9.]+)", final_answer)
    if conf_match:
        try:
            confidence = float(conf_match.group(1))
            confidence = max(0.0, min(1.0, confidence))
        except ValueError:
            pass

    reason_match = re.search(r"reason\s*=\s*([^\n,]+)", final_answer)
    if reason_match:
        reason = public_decision_summary(reason_match.group(1), max_chars=100)

    if selected_agent is None:
        valid = {aid: art for aid, art in agent_artifacts.items() if not art.get("error")}
        if valid:
            selected_agent = max(valid, key=lambda aid: float(valid[aid].get("confidence") or 0.0))
            confidence = float(agent_artifacts[selected_agent].get("confidence") or 0.0)
            reason = "FALLBACK_MAX_CONFIDENCE"

    if confidence == 0.0 and selected_agent and selected_agent in agent_artifacts:
        confidence = float(agent_artifacts[selected_agent].get("confidence") or 0.0)

    return selected_agent, confidence, reason
