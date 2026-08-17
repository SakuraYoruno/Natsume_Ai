"""将冻结的情感计划编译为可回放的 Host 投递协议。

这不是 Edge 客户端：它在文本可见前明确 Host 侧 intent 与回执，后续通道适配器
可独立执行、拒绝或降级 Voice 和 Avatar。该分层确保 TTS/VTS 不可用不会抹去
Natsume 的 canonical 自我情感，也不会使已批准的文本回复失效。
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
from host_brain.digital_life.companion_voice import normalize_japanese_speech_text


_EDGE_EMOTION_BY_EXPRESSION = {
    "SOFT_COMFORT": "CALM",
    "CONTAINED_HURT": "SAD",
    "CONTAINED_JEALOUSY": "THINKING",
    "GENTLE_BOUNDARY": "THINKING",
    "REPAIR_OPEN": "CALM",
    "GIVE_SPACE": "CALM",
    "QUIET_PRESENCE": "CALM",
    "SHARED_JOY": "HAPPY",
    "CALM_ATTENTIVE": "CALM",
}


def previous_durable_avatar_emotion_tag(
    emotion_context: Mapping[str, Any] | None,
) -> str | None:
    """只从冻结的 Life 计划读取上一个 Avatar 表情。

    旧 ``_prev_emotion_tag`` 可能源自用户观察，绝不能替 Natsume 选择 Avatar
    迁移；因此不在精确 ``expression_plan.v1`` 信封内的兼容数据被有意视为无效。
    """

    raw = _mapping(emotion_context).get("_affective_expression_internal")
    if not isinstance(raw, Mapping):
        return None
    if str(raw.get("schema_version") or "").strip() != "expression_plan.v1":
        return None
    expression = str(raw.get("previous_avatar_expression") or "").strip().upper()
    return _EDGE_EMOTION_BY_EXPRESSION.get(expression)


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
    """验证情感计划的身份与 revision，并提取后续命令共同使用的绑定作用域。"""
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
        "companion_profile_id": str(raw.get("companion_profile_id") or "").strip(),
        "companion_profile_digest": str(
            raw.get("companion_profile_digest") or ""
        ).strip(),
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
    if bool(scope["companion_profile_id"]) != bool(
        scope["companion_profile_digest"]
    ):
        raise ValueError("AFFECTIVE_EXPRESSION_COMPANION_PROFILE_INVALID")
    return scope


def build_sentence_expression_delivery(
    *,
    affective_turn_plan: Mapping[str, Any],
    sentence: str,
    speech_sentence: str | None = None,
    speech_language: str | None = None,
    sentence_index: int,
    created_at_ms: int,
) -> tuple[tuple[ExpressionDeliveryIntentV1, ...], tuple[ExpressionDeliveryReceiptV1, ...]]:
    """为一条已通过治理的句子构造 Voice 与 Avatar 协议。

    两个 intent 带有完全相同的同步标识和自我情感 revision。回执仅表示 Host
    已持久化暂存协议，绝不表示远端 Edge 已渲染；可见文本与物理表现由此可独立
    恢复和降级。
    """

    scope = _plan_scope(affective_turn_plan)
    text = _text(sentence, field_name="sentence", limit=2_000)
    speech_text = (
        _text(speech_sentence, field_name="speech_sentence", limit=4_000)
        if speech_sentence is not None
        else text
    )
    language = str(speech_language or "zh").strip().lower().replace("_", "-")
    language = "ja" if language.startswith("ja") else "zh"
    if language == "ja" and normalize_japanese_speech_text(speech_text) != speech_text:
        raise ValueError("speech_sentence must be bounded Japanese text")
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
    voice_payload: dict[str, Any] = {
        # This is the exact payload shape consumed by the existing Edge voice
        # protocol.  Extra affect controls are additive and may be ignored by
        # an older Edge, never used to reinterpret text.
        "edge_action": "speak",
        "session_id": scope["session_id"],
        "utterance_id": utterance_id,
        "text": speech_text,
        "lang_hint": language,
        "emotion_tag": scope["self_emotion_tag"],
        "emotion_intensity": scope["companion_intensity"],
        "speed": _float(expression.get("voice_rate"), default=1.0, minimum=0.7, maximum=1.3),
        "energy": _float(expression.get("voice_energy"), default=0.5, minimum=0.0, maximum=1.0),
        "pitch": _float(expression.get("voice_pitch"), default=0.0, minimum=-1.0, maximum=1.0),
        "pause_ms": _integer(expression.get("voice_pause_ms"), field_name="voice_pause_ms"),
        # ``voice_profile`` is the machine-readable reference-bank key.
        # It deliberately stays separate from ``emotion_tag``: a shy or
        # longing delivery does not manufacture a new canonical emotion.
        "voice_profile": _text(
            expression.get("voice_style"), field_name="voice_style", limit=64
        ).upper(),
        "style_note": _text(expression.get("voice_style"), field_name="voice_style", limit=64),
        "companion_profile_id": scope["companion_profile_id"],
        "companion_profile_digest": scope["companion_profile_digest"],
        "interruptible": True,
    }
    if speech_text != text or language != "zh":
        # Keep the already-visible Chinese sentence separate from what the
        # Japanese voice reads.  Acceptance later binds both exact strings.
        voice_payload["display_text"] = text
    voice = ExpressionDeliveryIntentV1(
        delivery_id=voice_id,
        channel="VOICE",
        action="SPEAK",
        payload=voice_payload,
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
            "motion_intensity": _float(
                expression.get("avatar_motion_intensity"),
                default=0.5,
                minimum=0.0,
                maximum=1.0,
            ),
            "companion_profile_id": scope["companion_profile_id"],
            "companion_profile_digest": scope["companion_profile_digest"],
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
    """为所有已暂存句子构造取消或释放控制。

    控制只覆盖已完成前缀，不能声称取消尚未产生的内容；稳定 ID 使流中断后
    重试重新命中同一控制命令，而不会多次打断或释放设备通道。
    """

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


def bounded_expression_segments(
    narrative: str,
    *,
    maximum: int = 2_000,
) -> tuple[str, ...]:
    """拆分一条已批准的缓冲回复，且不丢失任何可见文本。

    原生流已有受治理句界；Governed Chat 则以完整叙述抵达 Acceptance，故 Surface
    适配器和 Acceptance 编译器共享这个确定性切分器。这样 worker 协议与 canonical
    的后 Acceptance 协议不会悄悄采用不同句界，导致身份或回执漂移。
    """

    remaining = str(narrative or "").strip()
    if not remaining:
        return ()
    maximum = max(64, int(maximum))
    segments: list[str] = []
    separators = "。！？!?；;，,、\n"
    while remaining:
        if len(remaining) <= maximum:
            segments.append(remaining)
            break
        window = remaining[:maximum]
        boundary = max(window.rfind(marker) for marker in separators)
        cut = boundary + 1 if boundary >= maximum // 2 else maximum
        segment = remaining[:cut].strip()
        if not segment:  # Defensive only; non-empty input must advance.
            segment = remaining[:maximum]
            cut = len(segment)
        segments.append(segment)
        remaining = remaining[cut:].lstrip()
    return tuple(segments)


def build_buffered_expression_delivery(
    *,
    affective_turn_plan: Mapping[str, Any],
    narrative: str,
    created_at_ms: int,
) -> tuple[
    tuple[ExpressionDeliveryIntentV1, ...],
    tuple[ExpressionDeliveryIntentV1, ...],
    tuple[ExpressionDeliveryReceiptV1, ...],
]:
    """为一条已批准的缓冲回复编译完整 Host 协议。

    返回顺序为 ``(sentence_intents, terminal_controls, receipts)``。它刻意只
    描述 Host 协议暂存；物理通道执行仍由拥有独立回执的 outbox 处理。
    """

    text = _text(narrative, field_name="narrative", limit=32_000)
    created = _integer(created_at_ms, field_name="created_at_ms")
    sentence_intents: list[ExpressionDeliveryIntentV1] = []
    receipts: list[ExpressionDeliveryReceiptV1] = []
    segments = bounded_expression_segments(text)
    for sentence_index, sentence in enumerate(segments):
        intents, staged = build_sentence_expression_delivery(
            affective_turn_plan=affective_turn_plan,
            sentence=sentence,
            sentence_index=sentence_index,
            created_at_ms=created,
        )
        sentence_intents.extend(intents)
        receipts.extend(staged)
    controls, terminal_receipts = build_expression_delivery_controls(
        affective_turn_plan=affective_turn_plan,
        action="RELEASE",
        sent_sentence_count=len(segments),
        created_at_ms=created,
    )
    receipts.extend(terminal_receipts)
    return tuple(sentence_intents), controls, tuple(receipts)


def parse_expression_delivery_intent(
    raw: Mapping[str, Any] | Any,
) -> ExpressionDeliveryIntentV1:
    """重开一条不可变 intent，并拒绝任意字段投影漂移。"""

    if not isinstance(raw, Mapping):
        raise ValueError("AFFECTIVE_EXPRESSION_INTENT_INVALID")
    value = dict(raw)
    required = {
        "schema_version",
        "delivery_id",
        "plan_id",
        "synchronization_id",
        "self_id",
        "self_affect_revision",
        "workspace_id",
        "relationship_subject_id",
        "session_id",
        "channel",
        "action",
        "sentence_index",
        "payload",
        "payload_digest",
        "created_at_ms",
    }
    if set(value) != required or value.get("schema_version") != "expression_delivery_intent.v1":
        raise ValueError("AFFECTIVE_EXPRESSION_INTENT_INVALID")
    try:
        intent = ExpressionDeliveryIntentV1(
            delivery_id=str(value.get("delivery_id") or ""),
            plan_id=str(value.get("plan_id") or ""),
            synchronization_id=str(value.get("synchronization_id") or ""),
            self_id=str(value.get("self_id") or ""),
            self_affect_revision=value.get("self_affect_revision"),
            workspace_id=str(value.get("workspace_id") or ""),
            relationship_subject_id=str(value.get("relationship_subject_id") or ""),
            session_id=str(value.get("session_id") or ""),
            channel=str(value.get("channel") or ""),
            action=str(value.get("action") or ""),
            sentence_index=value.get("sentence_index"),
            payload=value.get("payload"),
            created_at_ms=value.get("created_at_ms"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("AFFECTIVE_EXPRESSION_INTENT_INVALID") from exc
    if value != intent.as_dict():
        raise ValueError("AFFECTIVE_EXPRESSION_INTENT_DRIFT")
    return intent


def parse_expression_delivery_receipt(
    raw: Mapping[str, Any] | Any,
) -> ExpressionDeliveryReceiptV1:
    """重开一条不可变 Host 协议回执，不把暂存升级为物理成功。"""

    if not isinstance(raw, Mapping):
        raise ValueError("AFFECTIVE_EXPRESSION_RECEIPT_INVALID")
    value = dict(raw)
    required = {
        "schema_version",
        "receipt_id",
        "delivery_id",
        "synchronization_id",
        "channel",
        "action",
        "status",
        "reason_code",
        "physical_receipt_ref",
        "created_at_ms",
    }
    if set(value) != required or value.get("schema_version") != "expression_delivery_receipt.v1":
        raise ValueError("AFFECTIVE_EXPRESSION_RECEIPT_INVALID")
    try:
        receipt = ExpressionDeliveryReceiptV1(
            receipt_id=str(value.get("receipt_id") or ""),
            delivery_id=str(value.get("delivery_id") or ""),
            synchronization_id=str(value.get("synchronization_id") or ""),
            channel=str(value.get("channel") or ""),
            action=str(value.get("action") or ""),
            status=str(value.get("status") or ""),
            reason_code=value.get("reason_code"),
            physical_receipt_ref=value.get("physical_receipt_ref"),
            created_at_ms=value.get("created_at_ms"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("AFFECTIVE_EXPRESSION_RECEIPT_INVALID") from exc
    if value != receipt.as_dict():
        raise ValueError("AFFECTIVE_EXPRESSION_RECEIPT_DRIFT")
    return receipt


def expression_delivery_trace(
    intents: Sequence[ExpressionDeliveryIntentV1],
    receipts: Sequence[ExpressionDeliveryReceiptV1],
) -> dict[str, Any]:
    """生成适用于 SSE 的追踪摘要，永不暴露句子 payload 或朗读文本。"""

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
    "bounded_expression_segments",
    "build_buffered_expression_delivery",
    "build_expression_delivery_controls",
    "build_sentence_expression_delivery",
    "expression_delivery_trace",
    "previous_durable_avatar_emotion_tag",
    "parse_expression_delivery_intent",
    "parse_expression_delivery_receipt",
]
