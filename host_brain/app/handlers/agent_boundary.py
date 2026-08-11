"""Shared helpers for stateless application-agent boundaries.

Handlers in this package may consume already-governed model/read results or
prepare a canonical handoff.  They do not resolve process services and do not
own capability execution.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from host_brain.core.trace_redaction import build_public_agent_output, public_output_text
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def request_id_for(
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    prefix: str,
) -> str:
    request_id = str(context.get("request_id") or payload.get("request_id") or "").strip()
    return request_id or f"{prefix}_{time.time_ns()}"


def nested_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    raw = value.get("payload")
    return dict(raw) if isinstance(raw, Mapping) else dict(value)


def first_public_text(value: Any, *, fallback: str = "") -> str:
    mapping = nested_payload(value)
    for key in (
        "response_text",
        "translated_text",
        "translation",
        "result_text",
        "final_answer",
        "plan_summary",
        "summary",
        "text",
        "result",
        "output",
    ):
        candidate = mapping.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return public_output_text(candidate, fallback=fallback)
    return public_output_text(fallback)


def finalize_agent_boundary(
    out: dict[str, Any],
    *,
    agent_id: str,
    trace_key: str,
    request_id: str,
    summary: str,
    mode: str,
    reason_code: str,
    confidence: float,
    risk_flags: list[str] | None = None,
    trace_fields: Mapping[str, Any] | None = None,
    artifact_suffix: str = "result",
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    safe_summary = public_output_text(summary)
    resolved_flags = list(dict.fromkeys(str(item) for item in (risk_flags or []) if str(item)))
    artifact_ref = f"artifact://{request_id}/{agent_id}_{artifact_suffix}"

    out["agent_output"] = build_public_agent_output(safe_summary)
    write_agent_artifact_to_state(
        out,
        agent_id=agent_id,
        artifact_ref=artifact_ref,
        summary=safe_summary[:200],
        confidence=max(0.0, min(1.0, float(confidence))),
        risk_flags=resolved_flags,
    )
    out[trace_key] = {
        "agent_id": agent_id,
        "mode": mode,
        "reason_code": reason_code,
        "artifact_ref": artifact_ref,
        "side_effects_executed": False,
        "ts_ms": now_ms,
        **dict(trace_fields or {}),
    }
    return out


__all__ = [
    "finalize_agent_boundary",
    "first_public_text",
    "nested_payload",
    "request_id_for",
]
