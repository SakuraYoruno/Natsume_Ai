from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from host_brain.persona.isolated_surface_continuity import (
    ThreeLayerContinuityContext,
    build_surface_input_from_three_layer_context,
)
from host_brain.persona.isolated_surface_harness import IsolatedSurfaceInput


_HIGH_EMOTION_TASKS = frozenset({"apology", "bad_mood", "boundary_hurt", "give_up", "headache", "relationship_boundary", "tired"})
_TECH_TASKS = frozenset({"advice_continue", "complaint", "help_takeover", "next_step", "small_failure", "write_takeover"})
_PROCESS_WORDS = ("emotion_tag", "mood_state", "vad", "情绪值", "关系阶段", "bond", "affect")


@dataclass(frozen=True)
class SurfaceAffectControlPacket:
    """Compressed affect controls allowed to cross into ReplyControlPacket."""

    emotional_tone: str = ""
    relationship_distance: str = ""
    expression_intensity: float = 0.0
    style_anchor: tuple[str, ...] = ()
    must_not_do: tuple[str, ...] = ()
    confidence: float = 0.0
    degraded: bool = False
    reason_code: str | None = None
    trace: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "emotional_tone": self.emotional_tone,
            "relationship_distance": self.relationship_distance,
            "expression_intensity": round(float(self.expression_intensity or 0.0), 6),
            "style_anchor": list(self.style_anchor),
            "must_not_do": list(self.must_not_do),
            "confidence": round(float(self.confidence or 0.0), 6),
            "degraded": bool(self.degraded),
            "reason_code": self.reason_code,
            "trace": dict(self.trace or {}),
        }


def build_surface_affect_control_packet(
    *,
    emotion_state: Mapping[str, Any] | None = None,
    bond_summary: Mapping[str, Any] | None = None,
    relationship_clues: Sequence[str] | None = None,
    task_mode: str = "",
) -> SurfaceAffectControlPacket:
    emotion = dict(emotion_state or {})
    bond = dict(bond_summary or {})
    clues = _clean_list(relationship_clues)
    tag = _normalize_emotion_tag(
        emotion.get("emotion_tag")
        or emotion.get("tag")
        or emotion.get("current_emotion")
    )
    mood_state = _clean_text(emotion.get("mood_state")).lower()
    task = _clean_text(task_mode).lower()
    intensity = _cap_intensity(
        _coerce_float(emotion.get("emotion_intensity"), default=_coerce_float(emotion.get("intensity"), default=0.5)),
        task_mode=task,
        relationship_distance=_relationship_distance_from_bond(bond, clues),
    )
    relationship_distance = _relationship_distance_from_bond(bond, clues)
    emotional_tone = _emotional_tone(tag=tag, mood_state=mood_state, task_mode=task)
    anchors = _clean_list(
        [
            "情感层只调语气、节奏和距离，不新增事实。",
            _emotion_anchor(emotional_tone, intensity),
            _relationship_anchor(relationship_distance),
        ]
    )
    must_not_do = _clean_list(
        [
            "不要把情绪状态当事实说出来",
            "不要输出 emotion_tag、mood_state、VAD、关系阶段等过程词",
            *_tone_must_not_do(emotional_tone),
            *_relationship_must_not_do(relationship_distance),
        ]
    )
    return SurfaceAffectControlPacket(
        emotional_tone=emotional_tone,
        relationship_distance=relationship_distance,
        expression_intensity=intensity,
        style_anchor=tuple(anchors),
        must_not_do=tuple(must_not_do),
        confidence=0.78 if tag or relationship_distance else 0.0,
        trace={
            "emotion_tag": tag or "NEUTRAL",
            "task_mode": task,
            "relationship_distance": relationship_distance,
            "clue_count": len(clues),
        },
    )


def apply_surface_affect_control_packet(
    context: ThreeLayerContinuityContext,
    packet: SurfaceAffectControlPacket,
) -> ThreeLayerContinuityContext:
    emotional_tone = packet.emotional_tone or context.emotional_tone_hint
    relationship_distance = packet.relationship_distance or context.relationship_distance
    style_anchor = _clean_list([*context.style_anchor, *packet.style_anchor, "情感表达压缩" if packet.style_anchor else ""])
    must_not_do = _clean_list([*context.must_not_do, *packet.must_not_do])
    return replace(
        context,
        emotional_tone_hint=emotional_tone,
        relationship_distance=relationship_distance,
        style_anchor=tuple(style_anchor),
        must_not_do=tuple(must_not_do),
    )


def build_surface_input_with_affect_control(
    context: ThreeLayerContinuityContext,
    *,
    packet: SurfaceAffectControlPacket,
    soul_md: str,
    case_id: str = "",
    max_facts: int = 6,
    max_recent_turns: int = 6,
) -> IsolatedSurfaceInput:
    return build_surface_input_from_three_layer_context(
        apply_surface_affect_control_packet(context, packet),
        soul_md=soul_md,
        case_id=case_id,
        max_facts=max_facts,
        max_recent_turns=max_recent_turns,
    )


def affect_control_packet_from_mapping(raw: Mapping[str, Any] | SurfaceAffectControlPacket) -> SurfaceAffectControlPacket:
    if isinstance(raw, SurfaceAffectControlPacket):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError("affect control packet must be a mapping")
    return SurfaceAffectControlPacket(
        emotional_tone=_clean_text(raw.get("emotional_tone")),
        relationship_distance=_clean_text(raw.get("relationship_distance")),
        expression_intensity=_clamp01(_coerce_float(raw.get("expression_intensity"))),
        style_anchor=tuple(_clean_list(_coerce_sequence(raw.get("style_anchor")))),
        must_not_do=tuple(_clean_list(_coerce_sequence(raw.get("must_not_do")))),
        confidence=_clamp01(_coerce_float(raw.get("confidence"))),
        degraded=bool(raw.get("degraded", False)),
        reason_code=_clean_text(raw.get("reason_code")) or None,
        trace=_safe_trace(raw.get("trace")),
    )


def _emotion_anchor(emotional_tone: str, intensity: float) -> str:
    tone = _clean_text(emotional_tone)
    if tone in {"hurt_controlled", "annoyed_controlled"}:
        return "情绪表达：可以有一点被刺到或不满，但要收住，不攻击用户。"
    if tone == "soft_containment":
        return "情绪表达：先接住低能量，不追问、不说教。"
    if tone == "playful_light":
        return "情绪表达：轻快一点，但不要靠波浪号、叠词或卖萌撑角色感。"
    if tone == "warm_affection":
        return "情绪表达：亲近要短而克制，关系距离优先。"
    if intensity <= 0.25:
        return "情绪表达：低显影，语气平稳。"
    return "情绪表达：中等显影，藏在措辞和节奏里。"


def _relationship_anchor(distance: str) -> str:
    value = _clean_text(distance).lower()
    if value in {"guarded", "early", "distant"}:
        return "关系距离：偏克制，先尊重边界，不主动升温。"
    if value in {"close", "intimate"}:
        return "关系距离：可亲近，但不许越界承诺或强行亲密。"
    return "关系距离：熟人私聊，不升级亲密。"


def _tone_must_not_do(tone: str) -> list[str]:
    value = _clean_text(tone)
    if value in {"hurt_controlled", "annoyed_controlled"}:
        return ["不要攻击用户或冷暴力", "不要把受伤写成客服式原谅"]
    if value == "soft_containment":
        return ["不要连环追问", "不要把安慰写成鸡汤"]
    if value == "playful_light":
        return ["不要使用 ~ 或 ～ 撑可爱语气"]
    return []


def _relationship_must_not_do(distance: str) -> list[str]:
    value = _clean_text(distance).lower()
    rules = ["不要因为关系线索升级为恋人、家人或主人关系"]
    if value in {"close", "intimate"}:
        rules.append("不要用爱你、永远陪着、只属于你等越界承诺")
    return rules


def _emotional_tone(*, tag: str, mood_state: str, task_mode: str) -> str:
    normalized = tag.upper()
    if normalized in {"ANGRY", "FRUSTRATED", "ANNOYED"}:
        return "annoyed_controlled"
    if normalized in {"HURT", "WOUNDED"} or task_mode in {"boundary_hurt", "relationship_boundary"}:
        return "hurt_controlled"
    if normalized in {"SAD", "ANXIOUS", "TIRED", "EXHAUSTED"} or mood_state in {"tired", "low", "critical"}:
        return "soft_containment"
    if normalized in {"HAPPY", "EXCITED", "JOY", "PLAYFUL"}:
        return "playful_light"
    if normalized in {"LOVING", "AFFECTION"}:
        return "warm_affection"
    if task_mode in _HIGH_EMOTION_TASKS:
        return "soft_containment"
    return "neutral"


def _relationship_distance_from_bond(bond: Mapping[str, Any], clues: Sequence[str]) -> str:
    raw = _clean_text(
        bond.get("relationship_distance")
        or bond.get("distance")
        or bond.get("stage")
        or bond.get("bond_stage")
    ).lower()
    if raw in {"guarded", "early", "stranger", "distant", "new"}:
        return "guarded"
    if raw in {"close", "confidant", "intimate"}:
        return "close"
    try:
        closeness = float(bond.get("closeness") or bond.get("familiarity"))
    except (TypeError, ValueError):
        closeness = -1.0
    if closeness >= 0.82:
        return "close"
    if 0.0 <= closeness <= 0.25:
        return "guarded"
    joined = "\n".join(clues)
    if any(token in joined for token in ("不升级亲密", "别升级亲密", "熟人私聊")):
        return "familiar"
    return "familiar"


def _cap_intensity(value: float, *, task_mode: str, relationship_distance: str) -> float:
    cap = 0.72
    if task_mode in _TECH_TASKS:
        cap = 0.42
    elif task_mode in _HIGH_EMOTION_TASKS:
        cap = 0.82
    if relationship_distance in {"guarded", "early", "distant"}:
        cap = min(cap, 0.48)
    return _clamp01(min(value, cap))


def _normalize_emotion_tag(value: Any) -> str:
    text = _clean_text(value).upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "ANGER": "ANGRY",
        "ANNOYANCE": "ANNOYED",
        "COMFORT": "CALM",
        "EXHAUSTION": "EXHAUSTED",
        "HAPPINESS": "HAPPY",
        "JOYFUL": "JOY",
        "LOVE": "LOVING",
        "SADNESS": "SAD",
    }
    return aliases.get(text, text)


def _safe_trace(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key in ("emotion_tag", "task_mode", "relationship_distance", "clue_count"):
        if key not in raw:
            continue
        value = raw.get(key)
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)):
            out[key] = value
        else:
            text = _clean_text(value)
            if text and not any(word in text for word in _PROCESS_WORDS):
                out[key] = text[:64]
    return out


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _clean_list(values: Sequence[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = _clean_text(value)
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value or 0.0)))


__all__ = [
    "SurfaceAffectControlPacket",
    "affect_control_packet_from_mapping",
    "apply_surface_affect_control_packet",
    "build_surface_affect_control_packet",
    "build_surface_input_with_affect_control",
]
