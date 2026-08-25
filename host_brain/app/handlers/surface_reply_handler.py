from __future__ import annotations

import time
from typing import Any

from host_brain.agent_runtime.agent_registry import AGENT_ID_CHAT
from host_brain.model_runtime.prompt_builder_utils import (
    _load_template_mapping,
    _mapping_text,
)
from host_brain.app.handlers.agent_boundary import write_agent_artifact_to_state


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
            or _mapping_text(
                _load_template_mapping("surface_reply_handler.runtime_texts"),
                "draft_summary_default",
            )
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
            "summary": _mapping_text(
                _load_template_mapping("surface_reply_handler.runtime_texts"),
                "handler_error_summary",
            ),
            "confidence": 0.0,
            "risk_flags": [f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out
