from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from host_brain.app.emotion_context import map_emotion_tag_to_vad
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.bond.bond_stage_engine import compute_affinity_delta
from host_brain.bond.user_identity import resolve_bond_user_id
from host_brain.core.ids import now_ms


_NEGATIVE_SUPPORT_TAGS = frozenset(
    {"SAD", "SADNESS", "ANXIOUS", "TIRED", "EXHAUSTED", "HURT", "LONELY", "FEAR", "FEARFUL", "OVERWHELMED"}
)
_CONFLICT_TAGS = frozenset({"ANGRY", "ANGER", "ANNOYED", "FRUSTRATED", "IRRITATED", "DISGUST", "HURT"})
_POSITIVE_TAGS = frozenset({"HAPPY", "JOY", "EXCITED", "GRATEFUL", "CALM", "LOVE", "LOVING", "AFFECTION"})
_APOLOGY_MARKERS = ("对不起", "抱歉", "不好意思", "我错了", "sorry")
_CONFLICT_MARKERS = (
    "别管",
    "别烦",
    "闭嘴",
    "滚",
    "烦死",
    "讨厌你",
    "你很烦",
    "你又",
    "你怎么",
    "不想理你",
    "伤到我",
    "不舒服",
)
_SUPPORT_REPLY_MARKERS = (
    "我在",
    "别逞强",
    "慢慢来",
    "不急",
    "先别",
    "先",
    "难受",
    "陪你",
    "发我",
    "我帮你",
)
_SHARED_MEMORY_MARKERS = ("上次", "之前", "记得", "还记得", "那天", "以前")


@dataclass(frozen=True)
class SurfaceEmotionBondPlan:
    applied: bool
    update_kind: str = "INTERACTION_MINIMAL"
    emotion_tag: str = "NEUTRAL"
    user_emotion_tag: str = "NEUTRAL"
    assistant_emotion_tag: str = "NEUTRAL"
    emotion_intensity: float = 0.5
    emotion_valence: float = 0.0
    reason_codes: tuple[str, ...] = ()
    topic_id: str = "surface_main_chain"
    affinity_delta_hint: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": bool(self.applied),
            "update_kind": self.update_kind,
            "emotion_tag": self.emotion_tag,
            "user_emotion_tag": self.user_emotion_tag,
            "assistant_emotion_tag": self.assistant_emotion_tag,
            "emotion_intensity": round(float(self.emotion_intensity), 4),
            "emotion_valence": round(float(self.emotion_valence), 4),
            "reason_codes": list(self.reason_codes),
            "topic_id": self.topic_id,
            "affinity_delta_hint": round(float(self.affinity_delta_hint), 4),
        }


def plan_surface_emotion_bond_update(
    *,
    user_message: str,
    assistant_reply: str,
    emotion_context: Mapping[str, Any] | None,
    reply_plan: Mapping[str, Any] | None = None,
) -> SurfaceEmotionBondPlan:
    user_text = str(user_message or "").strip()
    reply_text = str(assistant_reply or "").strip()
    if not user_text and not reply_text:
        return SurfaceEmotionBondPlan(applied=False, reason_codes=("empty_turn",))

    emotion = dict(emotion_context or {})
    assistant_tag = _normalize_emotion_tag(
        emotion.get("surface_natsume_emotion_tag")
        or emotion.get("natsume_emotion_tag")
        or emotion.get("assistant_emotion_tag")
        or emotion.get("emotion_tag")
        or emotion.get("tag")
        or "NEUTRAL"
    )
    user_tag_raw = (
        emotion.get("surface_user_emotion_tag")
        or emotion.get("user_emotion_tag")
        or emotion.get("perceived_user_emotion_tag")
    )
    user_tag = _normalize_emotion_tag(user_tag_raw or emotion.get("emotion_tag") or emotion.get("tag") or "NEUTRAL")
    tag = user_tag
    valence = _extract_emotion_valence(emotion, tag=tag)
    intensity = _extract_emotion_intensity(emotion, valence=valence)
    joined = f"{user_text}\n{reply_text}".strip()
    reasons: list[str] = []
    if user_tag_raw not in (None, ""):
        reasons.append("surface_user_emotion_used")

    if _contains_any(user_text, _APOLOGY_MARKERS):
        update_kind = "APOLOGY"
        reasons.append("user_apology")
    elif _is_direct_conflict(user_text=user_text, tag=tag, intensity=intensity):
        update_kind = "CONFLICT"
        reasons.append("direct_conflict")
    elif tag in _NEGATIVE_SUPPORT_TAGS or valence <= -0.35:
        if _looks_supportive(reply_text):
            update_kind = "EMOTIONAL_SUPPORT"
            reasons.append("negative_affect_supported")
        else:
            update_kind = "COMFORT"
            reasons.append("negative_affect_contained")
    elif _contains_any(joined, _SHARED_MEMORY_MARKERS):
        update_kind = "SHARED_MEMORY"
        reasons.append("shared_memory_touch")
    elif tag in _POSITIVE_TAGS or valence >= 0.35:
        update_kind = BondDynamicsWriter.classify_interaction(
            text=joined,
            emotion_intensity=max(0.55, intensity),
        )
        reasons.append("positive_affect_interaction")
    else:
        update_kind = BondDynamicsWriter.classify_interaction(
            text=joined,
            emotion_intensity=intensity,
        )
        reasons.append("neutral_interaction")

    task_topic = _clean_topic(reply_plan)
    return SurfaceEmotionBondPlan(
        applied=True,
        update_kind=update_kind,
        emotion_tag=tag,
        user_emotion_tag=user_tag,
        assistant_emotion_tag=assistant_tag,
        emotion_intensity=intensity,
        emotion_valence=valence,
        reason_codes=tuple(reasons),
        topic_id=task_topic or _preview_topic(user_text),
        affinity_delta_hint=_affinity_delta_hint(update_kind, intensity=intensity, valence=valence),
    )


def apply_surface_emotion_bond_flow(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    assistant_reply: str,
    emotion_context: Mapping[str, Any] | None,
    bond_summary: Mapping[str, Any] | None = None,
    reply_plan: Mapping[str, Any] | None = None,
    retrieval_evidence_items: Sequence[Mapping[str, Any]] | None = None,
    ts_ms: int = 0,
    queue: Any | None = None,
) -> dict[str, Any]:
    plan = plan_surface_emotion_bond_update(
        user_message=user_message,
        assistant_reply=assistant_reply,
        emotion_context=emotion_context,
        reply_plan=reply_plan,
    )
    if not plan.applied:
        return {"status": "SKIPPED", **plan.as_dict()}

    evidence_refs = _evidence_refs(
        request_id=request_id,
        retrieval_evidence_items=retrieval_evidence_items,
    )
    payload = {
        "request_id": str(request_id or ""),
        "intent_id": str(_safe_get(reply_plan, "intent_id") or request_id or ""),
        "session_id": str(session_id or ""),
        "seq": 1,
        "user_id": resolve_bond_user_id(person_id or None, session_id=session_id or None),
        "topic_id": plan.topic_id,
        "capability_hint": str(_safe_get(bond_summary, "capability_hint") or "surface_emotion_bond"),
        "evidence_refs": evidence_refs,
        "payload": {
            "source": "surface_main_chain",
            "reason_codes": list(plan.reason_codes),
            "affinity_delta_hint": plan.affinity_delta_hint,
            "user_message_preview": str(user_message or "")[:96],
            "assistant_reply_preview": str(assistant_reply or "")[:160],
            "user_emotion_tag": plan.user_emotion_tag,
            "assistant_emotion_tag": plan.assistant_emotion_tag,
        },
        "update_kind": plan.update_kind,
        "ts_ms": int(ts_ms or now_ms()),
        "emotion_tag": plan.emotion_tag,
        "emotion_intensity": plan.emotion_intensity,
        "emotion_valence": plan.emotion_valence,
    }

    if queue is None:
        from host_brain.app.dependencies import get_bond_update_queue  # noqa: PLC0415

        queue = get_bond_update_queue()
    queue_result = dict(queue.enqueue(payload) or {})
    return {
        "status": str(queue_result.get("status") or "UNKNOWN"),
        "queue_reason_code": queue_result.get("reason_code"),
        "queue_size": queue_result.get("queue_size"),
        **plan.as_dict(),
    }


def _normalize_emotion_tag(value: Any) -> str:
    from host_brain.core.emotion_constants import normalize_emotion_tag  # noqa: PLC0415
    return normalize_emotion_tag(value)


def _extract_emotion_valence(emotion: Mapping[str, Any], *, tag: str) -> float:
    vad = emotion.get("vad_vector")
    raw = vad.get("valence") if isinstance(vad, Mapping) else None
    if raw in (None, ""):
        raw = emotion.get("valence")
    if raw not in (None, ""):
        value = _coerce_float(raw, default=0.0)
        if 0.0 <= value <= 1.0:
            return _clamp(value * 2.0 - 1.0, low=-1.0, high=1.0)
        return _clamp(value, low=-1.0, high=1.0)
    mapped, _arousal, _dominance = map_emotion_tag_to_vad(tag)
    return _clamp(mapped, low=-1.0, high=1.0)


def _extract_emotion_intensity(emotion: Mapping[str, Any], *, valence: float) -> float:
    for key in ("emotion_intensity", "intensity"):
        raw = emotion.get(key)
        if raw not in (None, ""):
            return _clamp(_coerce_float(raw, default=0.5))
    arousal = emotion.get("arousal")
    if arousal not in (None, ""):
        return _clamp(max(abs(valence), _coerce_float(arousal, default=0.35)))
    return _clamp(max(0.35, abs(valence)))


def _is_direct_conflict(*, user_text: str, tag: str, intensity: float) -> bool:
    if _contains_any(user_text, _CONFLICT_MARKERS):
        return True
    return tag in _CONFLICT_TAGS and intensity >= 0.55 and _contains_any(user_text, ("你", "别", "不想"))


def _looks_supportive(reply_text: str) -> bool:
    return _contains_any(reply_text, _SUPPORT_REPLY_MARKERS)


def _contains_any(text: str, markers: Sequence[str]) -> bool:
    lowered = str(text or "").lower()
    return any(marker.lower() in lowered for marker in markers)


def _clean_topic(reply_plan: Mapping[str, Any] | None) -> str:
    plan = dict(reply_plan or {})
    for key in ("primary_topic", "task_mode", "interaction_move", "response_goal"):
        text = str(plan.get(key) or "").strip()
        if text:
            return text[:64]
    return ""


def _preview_topic(user_text: str) -> str:
    text = str(user_text or "").strip().replace("\n", " ")
    return text[:64] or "surface_main_chain"


def _affinity_delta_hint(update_kind: str, *, intensity: float, valence: float) -> float:
    return float(
        compute_affinity_delta(
            update_kind=str(update_kind or "").strip().upper(),
            emotion_intensity=float(intensity),
            emotion_valence=float(valence),
        )
    )


def _evidence_refs(
    *,
    request_id: str,
    retrieval_evidence_items: Sequence[Mapping[str, Any]] | None,
) -> list[str]:
    refs = [f"surface_turn:{str(request_id or '').strip() or 'unknown'}"]
    for item in retrieval_evidence_items or ():
        if not isinstance(item, Mapping):
            continue
        ref = str(item.get("evidence_ref") or item.get("ref") or item.get("id") or "").strip()
        if ref:
            refs.append(ref)
    return refs[:8]


def _safe_get(mapping: Mapping[str, Any] | None, key: str) -> Any:
    return dict(mapping or {}).get(key) if isinstance(mapping, Mapping) else None


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


__all__ = [
    "SurfaceEmotionBondPlan",
    "apply_surface_emotion_bond_flow",
    "plan_surface_emotion_bond_update",
]
