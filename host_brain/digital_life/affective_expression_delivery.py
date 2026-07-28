"""Compile one frozen affective plan into replayable Host delivery protocol.

This is deliberately not an Edge client.  It makes the Host-side intent and
receipt explicit before text becomes visible, while a later channel adapter
may independently apply, reject, or degrade voice and avatar.  That separation
is what keeps an unavailable TTS/VTS connection from neutralising Natsume's
canonical self-affect or invalidating the text reply.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any

from natsume_contracts.life import (
    ExpressionDeliveryIntentV1,
    ExpressionDeliveryReceiptV1,
)


_EDGE_EMOTION_BY_EXPRESSION = {
    "SOFT_COMFORT": "CALM",
    "CONTAINED_HURT": "SAD",
    "GENTLE_BOUNDARY": "THINKING",
    "REPAIR_OPEN": "CALM",
    "GIVE_SPACE": "CALM",
    "QUIET_PRESENCE": "CALM",
    "SHARED_JOY": "HAPPY",
    "CALM_ATTENTIVE": "CALM",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any, *, field_name: str, limit: int = 512) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field_name} is required")
    if len(result) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return result


def _integer(value: Any, *, field_name: str, default: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if result < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return result


def _float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return round(max(minimum, min(maximum, result)), 6)


def _stable_id(*, kind: str, identity: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"kind": kind, **dict(identity)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"expression-delivery::{kind.lower()}::{hashlib.sha256(encoded).hexdigest()[:40]}"


def _plan_scope(plan: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(plan)
    expression = _mapping(raw.get("expression_plan"))
    if raw.get("schema_version") != "affective_turn_plan.v1":
        raise ValueError("AFFECTIVE_EXPRESSION_PLAN_INVALID")
    if str(raw.get("self_id") or "natsume_ai").strip() != "natsume_ai":
        raise ValueError("AFFECTIVE_EXPRESSION_SELF_ID_INVALID")
    scope = {
        "plan_id": _text(raw.get("plan_id"), field_name="plan_id"),
        "synchronization_id": _text(
            expression.get("synchronization_id"), field_name="synchronization_id"
        ),
        "self_affect_revision": _integer(
            raw.get("self_affect_revision"), field_name="self_affect_revision"
        ),
        "workspace_id": _text(raw.get("workspace_id"), field_name="workspace_id"),
        "relationship_subject_id": _text(
            raw.get("relationship_subject_id"), field_name="relationship_subject_id"
        ),
        "session_id": _text(raw.get("session_id"), field_name="session_id"),
        "self_emotion_tag": _text(
            raw.get("self_emotion_tag"), field_name="self_emotion_tag", limit=64
        ).upper(),
        "companion_intensity": _float(
            _mapping(raw.get("companion_act")).get("intensity"),
            default=0.2,
            minimum=0.0,
            maximum=1.0,
        ),
        "expression": expression,
    }
    if _integer(
        expression.get("self_affect_revision"),
        field_name="expression.self_affect_revision",
    ) != scope["self_affect_revision"]:
        raise ValueError("AFFECTIVE_EXPRESSION_REVISION_MISMATCH")
    return scope


def build_sentence_expression_delivery(
    *,
    affective_turn_plan: Mapping[str, Any],
    sentence: str,
    sentence_index: int,
    created_at_ms: int,
) -> tuple[tuple[ExpressionDeliveryIntentV1, ...], tuple[ExpressionDeliveryReceiptV1, ...]]:
    """Build the voice and avatar protocol for one already-gated sentence.

    Both intents carry the exact same synchronization and self-affect revision.
    A receipt only says the Host durably staged the protocol; it never says a
    remote Edge rendered it.
    """

    scope = _plan_scope(affective_turn_plan)
    text = _text(sentence, field_name="sentence", limit=2_000)
    index = _integer(sentence_index, field_name="sentence_index")
    created = _integer(created_at_ms, field_name="created_at_ms")
    expression = _mapping(scope["expression"])
    base = {
        "plan_id": scope["plan_id"],
        "synchronization_id": scope["synchronization_id"],
        "self_affect_revision": scope["self_affect_revision"],
        "workspace_id": scope["workspace_id"],
        "relationship_subject_id": scope["relationship_subject_id"],
        "session_id": scope["session_id"],
        "sentence_index": index,
    }
    utterance_id = f"{scope['synchronization_id']}::sentence::{index}"
    voice_id = _stable_id(kind="voice-speak", identity=base)
    avatar_id = _stable_id(kind="avatar-transition", identity=base)
    voice = ExpressionDeliveryIntentV1(
        delivery_id=voice_id,
        channel="VOICE",
        action="SPEAK",
        payload={
            # This is the exact payload shape consumed by the existing Edge
            # voice protocol.  Extra affect controls are additive and may be
            # ignored by an older Edge, never used to reinterpret text.
            "edge_action": "speak",
            "session_id": scope["session_id"],
            "utterance_id": utterance_id,
            "text": text,
            "lang_hint": "zh",
            "emotion_tag": scope["self_emotion_tag"],
            "emotion_intensity": scope["companion_intensity"],
            "speed": _float(expression.get("voice_rate"), default=1.0, minimum=0.7, maximum=1.3),
            "energy": _float(expression.get("voice_energy"), default=0.5, minimum=0.0, maximum=1.0),
            "pitch": _float(expression.get("voice_pitch"), default=0.0, minimum=-1.0, maximum=1.0),
            "pause_ms": _integer(expression.get("voice_pause_ms"), field_name="voice_pause_ms"),
            "style_note": _text(expression.get("voice_style"), field_name="voice_style", limit=64),
            "interruptible": True,
        },
        created_at_ms=created,
        **base,
    )
    avatar_expression = _text(
        expression.get("avatar_expression"), field_name="avatar_expression", limit=64
    ).upper()
    avatar = ExpressionDeliveryIntentV1(
        delivery_id=avatar_id,
        channel="AVATAR",
        action="TRANSITION",
        payload={
            # ``emotion_tag`` remains the actual VTS `set_expression` input;
            # the remaining fields describe the complete planned behaviour and
            # are carried to a newer Edge without breaking the deployed shape.
            "edge_action": "set_expression",
            "emotion_tag": _EDGE_EMOTION_BY_EXPRESSION.get(
                avatar_expression, scope["self_emotion_tag"]
            ),
            "emotion_intensity": scope["companion_intensity"],
            "expression": avatar_expression,
            "previous_expression": _text(
                expression.get("previous_avatar_expression"),
                field_name="previous_avatar_expression",
                limit=64,
            ).upper(),
            "previous_expression_source": _text(
                expression.get("previous_expression_source"),
                field_name="previous_expression_source",
                limit=64,
            ).upper(),
            "gaze": _text(expression.get("avatar_gaze"), field_name="avatar_gaze", limit=64),
            "posture": _text(expression.get("avatar_posture"), field_name="avatar_posture", limit=64),
            "transition_ms": _integer(
                expression.get("avatar_transition_ms"), field_name="avatar_transition_ms"
            ),
            "behavior_id": _text(
                expression.get("avatar_behavior"), field_name="avatar_behavior", limit=64
            ),
        },
        created_at_ms=created,
        **base,
    )
    intents = (voice, avatar)
    receipts = tuple(
        ExpressionDeliveryReceiptV1(
            receipt_id=_stable_id(
                kind="host-staged",
                identity={"delivery_id": intent.delivery_id, "status": "HOST_PROTOCOL_STAGED"},
            ),
            delivery_id=intent.delivery_id,
            synchronization_id=intent.synchronization_id,
            channel=intent.channel,
            action=intent.action,
            status="HOST_PROTOCOL_STAGED",
            created_at_ms=created,
        )
        for intent in intents
    )
    return intents, receipts


def build_expression_delivery_controls(
    *,
    affective_turn_plan: Mapping[str, Any],
    action: str,
    sent_sentence_count: int,
    created_at_ms: int,
    reason_code: str | None = None,
) -> tuple[tuple[ExpressionDeliveryIntentV1, ...], tuple[ExpressionDeliveryReceiptV1, ...]]:
    """Build cancel/release controls for all already-staged sentence output."""

    scope = _plan_scope(affective_turn_plan)
    normalized_action = _text(action, field_name="action", limit=32).upper()
    if normalized_action not in {"CANCEL", "RELEASE"}:
        raise ValueError("AFFECTIVE_EXPRESSION_CONTROL_INVALID")
    count = _integer(sent_sentence_count, field_name="sent_sentence_count")
    created = _integer(created_at_ms, field_name="created_at_ms")
    expression = _mapping(scope["expression"])
    base = {
        "plan_id": scope["plan_id"],
        "synchronization_id": scope["synchronization_id"],
        "self_affect_revision": scope["self_affect_revision"],
        "workspace_id": scope["workspace_id"],
        "relationship_subject_id": scope["relationship_subject_id"],
        "session_id": scope["session_id"],
        # Controls cover a completed prefix, not unsent content.
        "sentence_index": count,
    }
    voice = ExpressionDeliveryIntentV1(
        delivery_id=_stable_id(kind=f"voice-{normalized_action.lower()}", identity=base),
        channel="VOICE",
        action=normalized_action,
        payload={
            "edge_action": "barge_in" if normalized_action == "CANCEL" else "release",
            "session_id": scope["session_id"],
            "active_utterance_prefix": f"{scope['synchronization_id']}::sentence::",
            "source": "surface_stream_cancelled" if normalized_action == "CANCEL" else "surface_stream_completed",
            "reason_code": str(reason_code or "")[:160] or None,
        },
        created_at_ms=created,
        **base,
    )
    avatar = ExpressionDeliveryIntentV1(
        delivery_id=_stable_id(kind=f"avatar-{normalized_action.lower()}", identity=base),
        channel="AVATAR",
        action=normalized_action,
        payload={
            "edge_action": "behavior" if normalized_action == "CANCEL" else "release",
            "behavior_id": "IDLE" if normalized_action == "CANCEL" else _text(
                expression.get("avatar_behavior"), field_name="avatar_behavior", limit=64
            ),
            "synchronization_id": scope["synchronization_id"],
            "source": "surface_stream_cancelled" if normalized_action == "CANCEL" else "surface_stream_completed",
            "reason_code": str(reason_code or "")[:160] or None,
        },
        created_at_ms=created,
        **base,
    )
    status = "HOST_CANCEL_STAGED" if normalized_action == "CANCEL" else "HOST_RELEASE_STAGED"
    intents = (voice, avatar)
    receipts = tuple(
        ExpressionDeliveryReceiptV1(
            receipt_id=_stable_id(
                kind="host-control-staged",
                identity={"delivery_id": intent.delivery_id, "status": status},
            ),
            delivery_id=intent.delivery_id,
            synchronization_id=intent.synchronization_id,
            channel=intent.channel,
            action=intent.action,
            status=status,
            reason_code=reason_code,
            created_at_ms=created,
        )
        for intent in intents
    )
    return intents, receipts


def expression_delivery_trace(
    intents: Sequence[ExpressionDeliveryIntentV1],
    receipts: Sequence[ExpressionDeliveryReceiptV1],
) -> dict[str, Any]:
    """Produce an SSE-safe trace that never exposes the sentence payload."""

    return {
        "schema_version": "affective_expression_delivery_trace.v1",
        "intent_count": len(intents),
        "receipt_count": len(receipts),
        "delivery_ids": [intent.delivery_id for intent in intents],
        "channels": [intent.channel for intent in intents],
        "statuses": [receipt.status for receipt in receipts],
        "synchronization_id": intents[0].synchronization_id if intents else "",
        "self_affect_revision": intents[0].self_affect_revision if intents else 0,
    }


__all__ = [
    "build_expression_delivery_controls",
    "build_sentence_expression_delivery",
    "expression_delivery_trace",
]
