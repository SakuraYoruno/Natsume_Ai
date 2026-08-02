from __future__ import annotations

from typing import Any, Mapping

from host_brain.input_runtime.reply_envelope import FINAL_REPLY, ReplyEnvelope, can_send, coerce_reply_envelope
from host_brain.input_runtime.reply_performance import ReplyPerformance, coerce_reply_performance


_LEGACY_REPLY_PERFORMANCE_RETIRED = "LEGACY_REPLY_PERFORMANCE_RETIRED"
_CANONICAL_EMBODIMENT_AUTHORITY = "accepted_expression_plan_life_delivery_transport"


async def trigger_reply_performance(
    *,
    request_id: str,
    reply_envelope: ReplyEnvelope | Mapping[str, Any],
    enable_vts: bool = True,
    embodiment_runtime: Any | None = None,
) -> dict[str, Any]:
    """Report retirement of the legacy reply-envelope embodiment shortcut.

    A final reply's ``performance`` metadata is no longer authority to create
    an Avatar command.  Only an accepted ``ExpressionPlanV1`` may cross the
    embodiment boundary through ``LifeDeliveryTransport`` with its terminal
    Task preview and receipt lineage.  Keep this adapter as an explicit,
    side-effect-free compatibility result while callers migrate.
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

    # ``request_id`` and ``embodiment_runtime`` deliberately remain accepted
    # for wire compatibility, but no legacy direct command is built or queued.
    return {
        "invoked": False,
        "affected_output": False,
        "delivery_state": "RETIRED",
        "source": performance.source or "reply_envelope",
        "status": "RETIRED",
        "success": False,
        "reason_code": _LEGACY_REPLY_PERFORMANCE_RETIRED,
        "detail": "legacy ReplyEnvelope performance delivery is retired; accepted ExpressionPlan is required",
        "replacement_authority": _CANONICAL_EMBODIMENT_AUTHORITY,
        "emotion_tag": performance.emotion_tag,
        "expression_id": None,
        "command_id": None,
        "outbox_id": None,
        "performance": performance.as_dict(),
    }


__all__ = ["ReplyPerformance", "trigger_reply_performance"]
