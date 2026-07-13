"""Deterministic artifact-policy judge.

Judging is a pure application decision over committed artifact metadata.  The
handler does not call an LLM, a tool registry, or a process service locator.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from host_brain.agent_runtime.agent_registry import AGENT_ID_JUDGE
from host_brain.core.trace_redaction import build_public_agent_output, public_decision_summary

from .agent_boundary import request_id_for


JUDGE_ARTIFACT_POLICY_READY = "JUDGE_ARTIFACT_POLICY_READY"
JUDGE_NO_ARTIFACTS = "NO_ARTIFACTS"
JUDGE_ALL_ARTIFACTS_REJECTED = "ALL_ARTIFACTS_REJECTED"


def judge_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    out = dict(payload or {})
    request_id = request_id_for(out, ctx, prefix="judge")
    raw_artifacts = out.get("agent_artifacts")
    agent_artifacts = {
        str(agent_id): dict(artifact)
        for agent_id, artifact in dict(raw_artifacts or {}).items()
        if isinstance(artifact, Mapping)
    }
    now_ms = int(time.time() * 1000)

    if not agent_artifacts:
        return _finalize_judge(
            out,
            request_id=request_id,
            status="DEGRADED",
            reason=JUDGE_NO_ARTIFACTS,
            selected_agent=None,
            selected_artifact=None,
            confidence=0.0,
            risk_flags=[JUDGE_NO_ARTIFACTS],
            all_agents_evaluated=[],
            now_ms=now_ms,
        )

    selected_agent, judge_confidence, fallback_reason = _parse_judge_final_answer(
        "",
        agent_artifacts,
    )
    all_risk_flags = list(
        dict.fromkeys(
            str(flag)
            for artifact in agent_artifacts.values()
            for flag in list(artifact.get("risk_flags") or [])
            if str(flag)
        )
    )
    high_risk = any(
        flag in {"SAFETY_RISK:HIGH", "SAFETY_RISK:CRITICAL"}
        for flag in all_risk_flags
    )

    selected_artifact: dict[str, Any] | None = None
    if selected_agent is not None:
        artifact = agent_artifacts[selected_agent]
        selected_artifact = {
            "artifact_ref": str(artifact.get("artifact_ref") or ""),
            "summary": str(artifact.get("summary") or ""),
            "confidence": float(artifact.get("confidence") or 0.0),
        }

    if selected_agent is None:
        status = "DEGRADED"
        reason = JUDGE_ALL_ARTIFACTS_REJECTED
    elif high_risk or judge_confidence < 0.3:
        status = "DEGRADED"
        reason = "ARTIFACT_POLICY_RISK"
    else:
        status = "OK"
        reason = JUDGE_ARTIFACT_POLICY_READY if fallback_reason == "FALLBACK_MAX_CONFIDENCE" else fallback_reason

    return _finalize_judge(
        out,
        request_id=request_id,
        status=status,
        reason=reason,
        selected_agent=selected_agent,
        selected_artifact=selected_artifact,
        confidence=judge_confidence,
        risk_flags=all_risk_flags,
        all_agents_evaluated=list(agent_artifacts),
        now_ms=now_ms,
    )


def _finalize_judge(
    out: dict[str, Any],
    *,
    request_id: str,
    status: str,
    reason: str,
    selected_agent: str | None,
    selected_artifact: dict[str, Any] | None,
    confidence: float,
    risk_flags: list[str],
    all_agents_evaluated: list[str],
    now_ms: int,
) -> dict[str, Any]:
    summary = (
        str((selected_artifact or {}).get("summary") or "").strip()
        or "没有可发布的 Agent 工件。"
    )
    out["agent_output"] = build_public_agent_output(summary)
    out["judge_result"] = {
        "status": status,
        "reason": reason,
        "selected_agent": selected_agent,
        "selected_artifact": selected_artifact,
        "all_agents_evaluated": list(all_agents_evaluated),
        "confidence": max(0.0, min(1.0, float(confidence))),
        "risk_flags": list(risk_flags),
        "mode": "artifact_policy",
        "ts_ms": now_ms,
    }
    out["judge_agent_trace"] = {
        "agent_id": AGENT_ID_JUDGE,
        "mode": "artifact_policy",
        "reason_code": reason,
        "artifact_ref": f"artifact://{request_id}/{AGENT_ID_JUDGE}_decision",
        "artifacts_evaluated": len(all_agents_evaluated),
        "selected_agent": selected_agent,
        "judge_status": status,
        "side_effects_executed": False,
        "ts_ms": now_ms,
    }
    return out


def _parse_judge_final_answer(
    final_answer: str,
    agent_artifacts: dict[str, Any],
) -> tuple[str | None, float, str]:
    """Compatibility parser kept for historical serialized judge decisions."""

    selected_agent: str | None = None
    confidence = 0.0
    reason = "OK"

    if not agent_artifacts:
        return selected_agent, confidence, "NO_ANSWER"

    match = re.search(r"selected_agent\s*=\s*(\w+)", final_answer)
    if match:
        candidate = match.group(1).strip()
        if candidate in agent_artifacts:
            selected_agent = candidate

    conf_match = re.search(r"confidence\s*=\s*([0-9.]+)", final_answer)
    if conf_match:
        try:
            confidence = max(0.0, min(1.0, float(conf_match.group(1))))
        except ValueError:
            pass

    reason_match = re.search(r"reason\s*=\s*([^\n,]+)", final_answer)
    if reason_match:
        reason = public_decision_summary(reason_match.group(1), max_chars=100)

    if selected_agent is None:
        valid = {
            agent_id: artifact
            for agent_id, artifact in agent_artifacts.items()
            if isinstance(artifact, Mapping) and not artifact.get("error")
        }
        if valid:
            selected_agent = max(
                valid,
                key=lambda agent_id: float(valid[agent_id].get("confidence") or 0.0),
            )
            confidence = float(valid[selected_agent].get("confidence") or 0.0)
            reason = "FALLBACK_MAX_CONFIDENCE"

    if confidence == 0.0 and selected_agent and selected_agent in agent_artifacts:
        confidence = float(agent_artifacts[selected_agent].get("confidence") or 0.0)

    return selected_agent, confidence, reason


__all__ = [
    "JUDGE_ARTIFACT_POLICY_READY",
    "JUDGE_NO_ARTIFACTS",
    "_parse_judge_final_answer",
    "judge_agent_handler",
]
