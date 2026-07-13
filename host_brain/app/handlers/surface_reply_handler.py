from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from host_brain.agent_runtime.agent_registry import AGENT_ID_CHAT
from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    candidates = [str(template_id or "").strip()]
    for candidate in candidates:
        raw = resolve_prompt_contract_json(candidate)
        if isinstance(raw, Mapping) and raw:
            return dict(raw)
    return {}


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


def _resolve_surface_reply_runtime_texts() -> dict[str, Any]:
    return _load_template_mapping("surface_reply_handler.runtime_texts")


def _resolve_surface_reply_draft_summary_default() -> str:
    return _mapping_text(
        _resolve_surface_reply_runtime_texts(),
        "draft_summary_default",
    )


def _resolve_surface_reply_error_summary() -> str:
    return _mapping_text(
        _resolve_surface_reply_runtime_texts(),
        "handler_error_summary",
    )


def surface_reply_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()

    out = dict(payload)

    try:
        chat_node_output = dict(payload.get("chat_direct_ja") or {})
        draft_summary = str(
            chat_node_output.get("payload", {}).get("response_text")
            or chat_node_output.get("payload", {}).get("answer")
            or _resolve_surface_reply_draft_summary_default()
        ).strip()[:200]

        gateway_status = str(chat_node_output.get("gateway_status") or "OK").strip().upper()
        confidence = 0.9 if gateway_status == "OK" else 0.3
        risk_flags: list[str] = []
        if gateway_status != "OK":
            risk_flags.append(f"GATEWAY_STATUS:{gateway_status}")

        artifact_ref = f"artifact://{request_id}/{AGENT_ID_CHAT}_{int(time.time() * 1000)}"
        write_agent_artifact_to_state(
            out,
            agent_id=AGENT_ID_CHAT,
            artifact_ref=artifact_ref,
            summary=draft_summary,
            confidence=confidence,
            risk_flags=risk_flags,
        )

        out["surface_reply_trace"] = {
            "agent_id": AGENT_ID_CHAT,
            "artifact_ref": artifact_ref,
            "gateway_status": gateway_status,
            "ts_ms": int(time.time() * 1000),
        }
    except Exception as exc:
        artifacts = out.setdefault("agent_artifacts", {})
        artifacts[AGENT_ID_CHAT] = {
            "artifact_ref": f"artifact://{request_id}/{AGENT_ID_CHAT}_error",
            "summary": _resolve_surface_reply_error_summary(),
            "confidence": 0.0,
            "risk_flags": [f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out
