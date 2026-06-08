from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from typing import Any

from host_brain.app.reply_envelope import ReplyEnvelope
from host_brain.app.surface_reply_preprocessor import _build_deterministic_sub_agent_reply
from host_brain.app.surface_main_chain_adapter import (
    surface_main_chain_enabled,
    surface_main_chain_required,
    surface_main_chain_sse_generator,
)


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(dict(data), ensure_ascii=False)}\n\n"


def _no_reply_event(*, request_id: str, reason_code: str, started_ms: int, detail: str = "") -> str:
    envelope = ReplyEnvelope.no_reply(
        reason_code=reason_code,
        source="surface_reply_runtime",
        audit={"request_id": request_id, "detail": detail},
    )
    return _sse(
        {
            "phase": "COMMIT",
            "done": True,
            "decision": "NO_REPLY",
            "reason_code": envelope.reason_code,
            "narrative": "",
            "request_id": request_id,
            "reply_envelope": envelope.as_dict(),
            "audit_refs": {
                "request_id": request_id,
                "path": "surface_reply:runtime",
                "latency_ms": max(0, int(time.time() * 1000) - int(started_ms or 0)),
                "reason_code": envelope.reason_code,
                "detail": detail,
            },
        }
    )


def _has_user_visible_reply_context(agent_input: Any) -> bool:
    """Return true for user-directed chat inputs that must not be dropped by ownership gates."""
    user_message = str(getattr(agent_input, "user_message", "") or "").strip()
    if user_message:
        return True
    if getattr(agent_input, "attachments", None):
        return True
    observations = getattr(agent_input, "multimodal_observations", None)
    if isinstance(observations, dict) and observations:
        return True
    if str(getattr(agent_input, "attachment_summary", "") or "").strip():
        return True
    return False


def _should_force_surface_main_chain(agent_input: Any) -> bool:
    """Ownership gates may route, but user-facing chat should still have a model owner."""
    if not _has_user_visible_reply_context(agent_input):
        return False
    source = str(getattr(agent_input, "input_source", "") or getattr(agent_input, "source", "") or "").strip().upper()
    reply_target = str(getattr(agent_input, "reply_target", "") or "").strip()
    reply_channel = str(getattr(agent_input, "reply_channel", "") or "").strip()
    session_id = str(getattr(agent_input, "session_id", "") or "").strip()
    if source == "GATEWAY" or reply_target or reply_channel:
        return True
    return bool(session_id)


async def surface_reply_sse_generator(agent_input: Any) -> AsyncGenerator[str, None]:
    """Current user-visible text reply entry.

    The retired SurfaceReply body has been removed. Normal text replies are produced
    by SurfaceMainChain; deterministic sub-agent outputs are passed through only
    when SurfaceMainChain explicitly does not own that context.
    """
    started_ms = int(time.time() * 1000)
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    sub_agent_ctx = getattr(agent_input, "sub_agent_context", None)

    try:
        if surface_main_chain_enabled(agent_input, sub_agent_ctx=sub_agent_ctx):
            async for event in surface_main_chain_sse_generator(agent_input):
                yield event
            return

        deterministic_text = _build_deterministic_sub_agent_reply(
            sub_agent_ctx,
            render_target_lang=str(getattr(agent_input, "render_target_lang", "zh") or "zh"),
            user_message=str(getattr(agent_input, "user_message", "") or ""),
        )
        if deterministic_text:
            envelope = ReplyEnvelope.final_reply(
                text=deterministic_text,
                response_form="text",
                source="surface_reply_deterministic_sub_agent",
                cacheable=False,
                audit={"request_id": request_id, "sub_agent_id": str(getattr(sub_agent_ctx, "agent_id", "") or "")},
            )
            yield _sse({"phase": "RENDER", "chunk": deterministic_text})
            yield _sse({"phase": "SENTENCE", "sentence": deterministic_text, "sentence_index": 0})
            yield _sse(
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": deterministic_text,
                    "request_id": request_id,
                    "reply_envelope": envelope.as_dict(),
                    "audit_refs": {
                        "request_id": request_id,
                        "path": "surface_reply:deterministic_sub_agent",
                        "latency_ms": max(0, int(time.time() * 1000) - started_ms),
                        "reply_envelope": envelope.as_dict(),
                    },
                }
            )
            return

        if _should_force_surface_main_chain(agent_input):
            async for event in surface_main_chain_sse_generator(agent_input):
                yield event
            return

        if surface_main_chain_required(agent_input, sub_agent_ctx=sub_agent_ctx):
            yield _no_reply_event(
                request_id=request_id,
                reason_code="SURFACE_MAIN_CHAIN_DISABLED",
                started_ms=started_ms,
                detail="SurfaceMainChain is required but not enabled.",
            )
            return

        yield _no_reply_event(
            request_id=request_id,
            reason_code="SURFACE_REPLY_UNSUPPORTED_CONTEXT",
            started_ms=started_ms,
            detail="No SurfaceMainChain or deterministic Surface reply path owns this context.",
        )
    except Exception as exc:
        yield _no_reply_event(
            request_id=request_id,
            reason_code="SURFACE_REPLY_RUNTIME_FAILED",
            started_ms=started_ms,
            detail=type(exc).__name__,
        )


__all__ = ["_sse", "surface_reply_sse_generator"]
