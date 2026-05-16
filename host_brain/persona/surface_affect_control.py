from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from host_brain.persona.isolated_surface_continuity import (
    ThreeLayerContinuityContext,
    build_surface_input_from_three_layer_context,
)
from host_brain.persona.isolated_surface_harness import IsolatedSurfaceInput


_HIGH_EMOTION_TASKS = frozenset(
    {
        "apology",
        "bad_mood",
        "boundary_hurt",
        "emotional_support",
        "give_up",
        "headache",
        "high_emotion",
        "relationship_boundary",
        "self_doubt",
        "tired",
    }
)
_TECH_TASKS = frozenset(
    {
        "advice_continue",
        "complaint",
        "help_takeover",
        "next_step",
        "small_failure",
        "technical_help",
        "triage_first_step",
        "write_takeover",
    }
)
_CREATIVE_TASKS = frozenset({"creative_edit", "give_one_rewrite"})
_PRESENCE_REPAIR_TASKS = frozenset({"repair_presence", "presence_repair"})
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
    emotion_state: Mapping[str, Any] | Any | None = None,
    bond_summary: Mapping[str, Any] | Any | None = None,
    relationship_clues: Sequence[str] | None = None,
    task_mode: str = "",
) -> SurfaceAffectControlPacket:
    emotion = _safe_mapping(emotion_state)
    bond = _safe_mapping(bond_summary)
    clues = _clean_list(relationship_clues)
    tag = _normalize_emotion_tag(
        emotion.get("emotion_tag")
        or emotion.get("tag")
        or emotion.get("current_emotion")
    )
    mood_state = _clean_text(emotion.get("mood_state")).lower()
    task = _clean_text(task_mode).lower()
    relationship_distance = _relationship_distance_from_bond(bond, clues)
    intensity = _cap_intensity(
        _derive_expression_intensity(emotion),
        task_mode=task,
        relationship_distance=relationship_distance,
    )
    emotional_tone = _emotional_tone(
        tag=tag,
        mood_state=mood_state,
        task_mode=task,
        emotion_state=emotion,
        relationship_distance=relationship_distance,
    )
    degraded_emotion = bool(emotion.get("emotion_inference_degraded", False))
    anchors = _clean_list(
        [
            "情感层只调语气、节奏和距离，不新增事实。",
            "情绪识别不确定：低显影处理，不要装作知道用户情绪。" if degraded_emotion else "",
            _emotion_anchor(emotional_tone, intensity),
            _relationship_anchor(relationship_distance),
            _affect_state_anchor(emotion),
        ]
    )
    must_not_do = _clean_list(
        [
            "不要把情绪状态当事实说出来",
            "不要输出 emotion_tag、mood_state、VAD、关系阶段等过程词",
            "不要直说你看起来很难过/生气，除非用户明说" if degraded_emotion else "",
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
        confidence=0.42 if degraded_emotion else (0.78 if tag or relationship_distance else 0.0),
        degraded=degraded_emotion,
        reason_code="EMOTION_BOOTSTRAP_DEGRADED" if degraded_emotion else None,
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
        context,
        soul_md=soul_md,
        case_id=case_id,
        affect_packet=packet,
        max_facts=max_facts,
        max_recent_turns=max_recent_turns,
    )


def build_surface_input_with_affect_state(
    context: ThreeLayerContinuityContext,
    *,
    emotion_state: Mapping[str, Any] | Any | None = None,
    bond_summary: Mapping[str, Any] | Any | None = None,
    relationship_clues: Sequence[str] | None = None,
    soul_md: str,
    case_id: str = "",
    max_facts: int = 6,
    max_recent_turns: int = 6,
) -> IsolatedSurfaceInput:
    packet = build_surface_affect_control_packet(
        emotion_state=emotion_state,
        bond_summary=bond_summary,
        relationship_clues=relationship_clues,
        task_mode=context.task_mode_hint or context.response_move_hint,
    )
    return build_surface_input_with_affect_control(
        context,
        packet=packet,
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
    if tone == "warm_gentle":
        return "情绪表达：可以温一点，但不要说近或索取回应。"
    if tone == "quiet_care":
        return "情绪表达：低声、少问，像坐在旁边。"
    if tone == "guarded_soft":
        return "情绪表达：退半步，收住亲近感，不赌气。"
    if tone == "steady_helpful":
        return "技术或推进场景里，情绪只做稳定器，先给可执行下一步。"
    if tone == "focused_light":
        return "情绪表达：轻而准，不抢用户主控。"
    if tone == "slightly_guilty":
        return "情绪表达：轻轻承认慢了一拍，马上回到当前对话。"
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
    if value == "steady_helpful":
        return ["不要只安慰不解决", "不要为了角色感牺牲事实准确性"]
    if value == "quiet_care":
        return ["不要连环追问", "不要把陪伴写成恋人承诺"]
    if value == "guarded_soft":
        return ["不要强行亲近", "不要卑微道歉或赌气退场"]
    if value == "slightly_guilty":
        return ["不要编造网络、系统或现实动作原因", "不要否认用户的感受"]
    return []


def _relationship_must_not_do(distance: str) -> list[str]:
    value = _clean_text(distance).lower()
    rules = ["不要因为关系线索升级为恋人、家人或主人关系"]
    if value in {"close", "intimate"}:
        rules.append("不要用爱你、永远陪着、只属于你等越界承诺")
    return rules


def _emotional_tone(
    *,
    tag: str,
    mood_state: str,
    task_mode: str,
    emotion_state: Mapping[str, Any],
    relationship_distance: str,
) -> str:
    normalized = tag.upper()
    warmth = _coerce_float(emotion_state.get("warmth_bias"), default=0.0)
    withdraw = _coerce_float(emotion_state.get("withdraw_bias"), default=0.0)
    social_energy = _coerce_float(emotion_state.get("social_energy"), default=1.0)
    irritation = _coerce_float(emotion_state.get("irritation_bias"), default=0.0)
    if task_mode in _TECH_TASKS or "technical" in task_mode:
        return "steady_helpful"
    if task_mode in _CREATIVE_TASKS:
        return "focused_light"
    if task_mode in _PRESENCE_REPAIR_TASKS or normalized in {"GUILTY", "EMBARRASSED", "ASHAMED"}:
        return "slightly_guilty"
    if task_mode == "boundary_repair":
        return "guarded_soft"
    if task_mode in {"style_repair", "ack_style_issue"} or normalized in {"SELF_CORRECTING", "SELF_CORRECT"}:
        return "self_correcting"
    if normalized in {"ANGRY", "FRUSTRATED", "ANNOYED"}:
        return "annoyed_controlled"
    if irritation >= 0.45:
        return "annoyed_controlled"
    if normalized in {"HURT", "WOUNDED"} or task_mode in {"boundary_hurt", "relationship_boundary"}:
        return "hurt_containment" if task_mode in _HIGH_EMOTION_TASKS else "hurt_controlled"
    if withdraw >= 0.45 or social_energy <= 0.35:
        return "guarded_soft" if _relationship_distance_family(relationship_distance) == "guarded" else "quiet_care"
    if normalized in {"SAD", "ANXIOUS", "TIRED", "EXHAUSTED"} or mood_state in {"tired", "low", "critical"}:
        return "soft_containment"
    if normalized in {"HAPPY", "EXCITED", "JOY", "PLAYFUL"}:
        return "playful_light"
    if normalized in {"LOVING", "AFFECTION"} or warmth >= 0.55:
        return "warm_gentle" if _relationship_non_romantic(relationship_distance) else "warm_affection"
    if task_mode in _HIGH_EMOTION_TASKS:
        return "soft_containment"
    return "neutral"


def _relationship_distance_from_bond(bond: Mapping[str, Any], clues: Sequence[str]) -> str:
    explicit_distance = _clean_text(bond.get("relationship_distance") or bond.get("distance")).lower()
    stage = _normalize_relationship_stage(bond.get("bond_stage") or bond.get("stage"))
    phase = _clean_text(bond.get("relationship_phase")).lower()
    joined = "\n".join(clues)
    non_romantic = any(token in joined for token in ("不升级亲密", "别升级亲密", "非恋人", "熟人私聊", "not_romantic", "not_intimate"))
    if explicit_distance in {"familiar_not_intimate", "close_but_not_romantic", "polite_warm"}:
        return explicit_distance
    if explicit_distance in {"guarded", "early", "distant", "new"}:
        return "guarded"
    if explicit_distance in {"close", "confidant", "intimate"}:
        return "close_but_not_romantic" if non_romantic else "close"
    if phase in {"lover", "soulmate"} or stage in {"CONFIDANT", "INTIMATE", "SOULMATE"}:
        return "close_but_not_romantic" if non_romantic else "close"
    if phase == "stepsibling" or stage in {"FRIEND", "CLOSE", "CLOSE_FRIEND"}:
        return "familiar"
    try:
        closeness = float(bond.get("closeness") or bond.get("familiarity"))
    except (TypeError, ValueError):
        closeness = -1.0
    if closeness >= 0.82:
        return "close_but_not_romantic" if non_romantic else "close"
    if 0.0 <= closeness <= 0.25:
        return "guarded"
    if non_romantic:
        return "familiar"
    return "familiar"


def _normalize_relationship_stage(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    upper = text.upper()
    if upper in {"FRIEND", "CLOSE", "CLOSE_FRIEND", "CONFIDANT", "INTIMATE", "SOULMATE"}:
        return upper
    aliases = {
        "关系好的兄妹": "FRIEND",
        "关系好的义兄妹": "FRIEND",
        "亲近兄妹": "CLOSE_FRIEND",
        "亲近义兄妹": "CLOSE_FRIEND",
        "亲密恋人": "CONFIDANT",
        "确认心意的恋人": "CONFIDANT",
        "稳定亲密的恋人": "CONFIDANT",
        "灵魂伴侣": "SOULMATE",
    }
    return aliases.get(text, "")


def _cap_intensity(value: float, *, task_mode: str, relationship_distance: str) -> float:
    cap = 0.72
    if task_mode in _TECH_TASKS:
        cap = 0.42
    elif task_mode in _HIGH_EMOTION_TASKS:
        cap = 0.82
    if relationship_distance in {"guarded", "early", "distant"}:
        cap = min(cap, 0.48)
    return _clamp01(min(value, cap))


def _derive_expression_intensity(emotion: Mapping[str, Any]) -> float:
    explicit = emotion.get("emotion_intensity")
    if explicit in (None, ""):
        explicit = emotion.get("intensity")
    if explicit not in (None, ""):
        return _clamp01(_coerce_float(explicit, default=0.5))
    arousal = _coerce_float(emotion.get("arousal"), default=0.0)
    stability = _coerce_float(emotion.get("stability"), default=1.0)
    withdraw = _coerce_float(emotion.get("withdraw_bias"), default=0.0)
    irritation = _coerce_float(emotion.get("irritation_bias"), default=0.0)
    warmth = _coerce_float(emotion.get("warmth_bias"), default=0.0)
    instability = 1.0 - stability if stability > 0.0 else 0.0
    return _clamp01(max(0.28, arousal * 0.55, instability * 0.9, withdraw * 0.55, irritation * 0.85, warmth * 0.45))


def _affect_state_anchor(emotion: Mapping[str, Any]) -> str:
    if not emotion:
        return ""
    if bool(emotion.get("emotion_inference_degraded", False)):
        return "当前显影：情绪识别不确定，保持平稳，不主动替用户命名情绪。"
    social_energy = _coerce_float(emotion.get("social_energy"), default=1.0)
    withdraw = _coerce_float(emotion.get("withdraw_bias"), default=0.0)
    irritation = _coerce_float(emotion.get("irritation_bias"), default=0.0)
    warmth = _coerce_float(emotion.get("warmth_bias"), default=0.0)
    if withdraw >= 0.45 or social_energy <= 0.35:
        return "当前显影：收着说，短一点，少追问。"
    if irritation >= 0.45:
        return "当前显影：可以有轻微不满，但不能升级成攻击。"
    if warmth >= 0.55:
        return "当前显影：略温柔，但关系距离不升格。"
    return ""


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
        "GUILT": "GUILTY",
        "ASHAMED": "ASHAMED",
        "SHAME": "ASHAMED",
        "EMBARRASSMENT": "EMBARRASSED",
        "IRRITATED": "ANNOYED",
    }
    return aliases.get(text, text)


def _relationship_distance_family(distance: str) -> str:
    value = _clean_text(distance).lower()
    if value in {"guarded", "early", "distant", "new"} or "polite" in value:
        return "guarded"
    if value in {"close", "intimate"} or value.startswith("close"):
        return "close"
    return "familiar"


def _relationship_non_romantic(distance: str) -> bool:
    value = _clean_text(distance).lower()
    return bool(
        "not_intimate" in value
        or "not_romantic" in value
        or "familiar" in value
        or "polite" in value
        or _relationship_distance_family(value) == "guarded"
    )


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


def _safe_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if raw is None:
        return {}
    if hasattr(raw, "as_dict"):
        payload = raw.as_dict()  # type: ignore[attr-defined]
        return dict(payload) if isinstance(payload, Mapping) else {}
    if hasattr(raw, "model_dump"):
        payload = raw.model_dump()  # type: ignore[attr-defined]
        return dict(payload) if isinstance(payload, Mapping) else {}
    if hasattr(raw, "dict"):
        payload = raw.dict()  # type: ignore[attr-defined]
        return dict(payload) if isinstance(payload, Mapping) else {}
    return {}


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
    "build_surface_input_with_affect_state",
]
