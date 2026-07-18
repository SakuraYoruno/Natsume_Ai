from __future__ import annotations

import logging
from typing import Any, Mapping

from host_brain.input_runtime.reply_envelope import FINAL_REPLY, ReplyEnvelope, can_send, coerce_reply_envelope
from host_brain.input_runtime.reply_performance import ReplyPerformance, coerce_reply_performance


_logger = logging.getLogger("Natsume.outbound_orchestrator")


async def trigger_reply_performance(
    *,
    request_id: str,
    reply_envelope: ReplyEnvelope | Mapping[str, Any],
    enable_vts: bool = True,
    embodiment_runtime: Any | None = None,
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
        from host_brain.channels.embodiment import (  # noqa: PLC0415
            build_host_embodiment_command,
            queue_embodiment_command,
        )
        from natsume_contracts.edge.embodiment_v1 import (  # noqa: PLC0415
            EMBODIMENT_KIND_AVATAR,
        )

        ack = queue_embodiment_command(
            build_host_embodiment_command(
                kind=EMBODIMENT_KIND_AVATAR,
                action="set_expression",
                payload={
                    "emotion_tag": performance.emotion_tag,
                    "emotion_intensity": performance.emotion_intensity,
                },
                request_id=request_id,
                idempotency_key=f"{request_id}:reply-performance:vts",
            ),
            runtime=embodiment_runtime,
        )
        return {
            "invoked": True,
            # Durable admission is not evidence that Edge has applied the
            # expression.  The embodiment dispatcher and receipt projection
            # own that later physical-effect transition.
            "affected_output": False,
            "delivery_state": "DURABLY_QUEUED",
            "source": performance.source or "reply_envelope",
            "status": ack.status,
            "success": bool(ack.success),
            "reason_code": ack.reason_code,
            "emotion_tag": performance.emotion_tag,
            "expression_id": None,
            "command_id": ack.command_id,
            "outbox_id": ack.outbox_id,
            "performance": performance.as_dict(),
        }
    except Exception as exc:
        _logger.debug("reply performance trigger failed request_id=%s err=%s", request_id, exc)
        failure_reason = str(exc).strip().split(":", 1)[0]
        if not failure_reason.startswith("ACTION_PREVIEW_"):
            failure_reason = "PERFORMANCE_TRIGGER_EXCEPTION"
        return {
            "invoked": True,
            "affected_output": False,
            "status": "FAILED",
            "reason_code": failure_reason,
            "detail": f"{type(exc).__name__}: {str(exc)[:180]}",
            "performance": performance.as_dict(),
        }


__all__ = ["ReplyPerformance", "trigger_reply_performance"]
