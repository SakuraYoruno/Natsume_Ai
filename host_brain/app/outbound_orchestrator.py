from __future__ import annotations

import logging
from typing import Any, Mapping

from host_brain.app.reply_envelope import FINAL_REPLY, ReplyEnvelope, can_send, coerce_reply_envelope
from host_brain.app.reply_performance import ReplyPerformance, coerce_reply_performance


_logger = logging.getLogger("Natsume.outbound_orchestrator")


async def trigger_reply_performance(
    *,
    request_id: str,
    reply_envelope: ReplyEnvelope | Mapping[str, Any],
    enable_vts: bool = True,
) -> dict[str, Any]:
    """Drive non-text performance channels from the same final reply envelope.

    The orchestrator never authors text. It only consumes the already-approved
    `ReplyEnvelope.performance` and fans it out to Edge/VTS.
    """

    envelope = coerce_reply_envelope(reply_envelope)
    performance = coerce_reply_performance(envelope.performance)
    if not enable_vts or envelope.kind != FINAL_REPLY or not can_send(envelope):
        return {
            "invoked": False,
            "affected_output": False,
            "reason_code": "PERFORMANCE_SKIPPED_NON_FINAL",
            "performance": performance.as_dict(),
        }
    if not performance.emotion_tag:
        return {
            "invoked": False,
            "affected_output": False,
            "reason_code": "PERFORMANCE_EMOTION_EMPTY",
            "performance": performance.as_dict(),
        }

    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.channels.vts import TRIGGER_LLM, resolve_vts_expression  # noqa: PLC0415

        result = resolve_vts_expression(
            emotion_tag=performance.emotion_tag,
            ledger=get_ledger(),
            request_id=request_id,
            trigger=TRIGGER_LLM,
        )
        return {
            "invoked": True,
            "affected_output": True,
            "source": performance.source or "reply_envelope",
            "status": result.status,
            "success": bool(result.success),
            "reason_code": result.reason_code,
            "emotion_tag": result.emotion_tag,
            "expression_id": result.expression_id,
            "performance": performance.as_dict(),
        }
    except Exception as exc:
        _logger.debug("reply performance trigger failed request_id=%s err=%s", request_id, exc)
        return {
            "invoked": True,
            "affected_output": False,
            "status": "FAILED",
            "reason_code": "PERFORMANCE_TRIGGER_EXCEPTION",
            "detail": f"{type(exc).__name__}: {str(exc)[:180]}",
            "performance": performance.as_dict(),
        }


__all__ = ["ReplyPerformance", "trigger_reply_performance"]
