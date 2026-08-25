"""Emotional proactive impulse model.

This layer decides whether a proactive impulse is alive enough to speak, and
whether it should stay private because the moment would feel intrusive.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from host_brain.core.ids import now_ms as _now_ms
from host_brain.core.runtime_helpers import _coerce_float

ACTION_INNER_ONLY = "inner_only"
ACTION_DRAFT_ONLY = "draft_only"
ACTION_QUIET_PRESENCE = "quiet_presence"
ACTION_SEND_LIGHT = "send_light"
ACTION_SEND_NORMAL = "send_normal"
SEND_ACTIONS = frozenset({ACTION_SEND_LIGHT, ACTION_SEND_NORMAL})

SOURCE_EMOTIONAL_IMPULSE = "emotional_impulse"

_BLOCKING_SCENE_TAGS = frozenset({"focus_work", "fullscreen_video", "sleeping_screen", "meeting"})
_GAMING_SCENE_TAGS = frozenset({"gaming", "gameplay", "battle", "lobby"})


@dataclass(frozen=True)
class EmotionalProactiveEpisode:
    episode_id: str
    source: str
    ts_ms: int
    motive: str
    felt_reason: str
    chosen_action: str
    send_allowed: bool
    opening_style: str
    opening_message: str
    inner_note: str
    emotional_drive: float
    disturbance_risk: float
    relationship_permission: float
    emotional_state: dict[str, Any] = field(default_factory=dict)
    no_reply_policy: str = "user_may_ignore_without_penalty"
    expected_reply_modes: list[str] = field(default_factory=list)
    aftereffect_plan: dict[str, Any] = field(default_factory=dict)
    reason_tags: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_appraisal_record(self) -> Any:
        """Adapt this event to the replayable read model without writing state."""
        from host_brain.persona.appraisal_adapter import (  # noqa: PLC0415
            build_appraisal_from_emotional_episode,
        )

        return build_appraisal_from_emotional_episode(self)


@dataclass(frozen=True)
class EmotionalProactiveAftereffect:
    aftereffect_id: str
    episode_id: str
    source: str
    ts_ms: int
    outcome: str
    reply_mode: str
    reply_warmth: float
    emotional_resolution: str
    loneliness_delta: float
    anticipation_delta: float
    next_impulse_modulation: dict[str, Any] = field(default_factory=dict)
    bond_signal: dict[str, Any] = field(default_factory=dict)
    memory_candidate: dict[str, Any] = field(default_factory=dict)
    state_update_hint: dict[str, Any] = field(default_factory=dict)
    reason_tags: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_appraisal_record(self) -> Any:
        """Adapt this feedback event while preserving boundary/repair meaning."""
        from host_brain.persona.appraisal_adapter import (  # noqa: PLC0415
            build_appraisal_from_emotional_aftereffect,
        )

        return build_appraisal_from_emotional_aftereffect(self)


def _resolve_reach_out_bias(inner: Mapping[str, Any], inner_weather: Mapping[str, Any] | None) -> float:
    """B1 内在天气的 reach_out_bias 当唯一冲动源；天气缺失时从 inner_state 派生兜底（与 B1 公式同形）。"""
    weather = dict(inner_weather or {})
    if "reach_out_bias" in weather:
        return _clamp(_coerce_float(weather.get("reach_out_bias"), 0.0))
    loneliness = _clamp(_coerce_float(inner.get("loneliness"), 0.0))
    anticipation = _clamp(_coerce_float(inner.get("anticipation"), 0.0))
    return _clamp(loneliness * 0.7 + anticipation * 0.3)


def build_emotional_proactive_episode(
    *,
    inner_state: Mapping[str, Any] | None = None,
    inner_weather: Mapping[str, Any] | None = None,
    bond_summary: Mapping[str, Any] | None = None,
    relationship_permission_override: float | None = None,
    scene_awareness: Mapping[str, Any] | None = None,
    now_ms: int | None = None,
    local_hour: int | None = None,
    last_foreground_ts_ms: int = 0,
    last_proactive_outbox_ts_ms: int = 0,
    active_projects: Any = None,
    self_change_note: str = "",
) -> EmotionalProactiveEpisode:
    """Build one proactive episode from inner pressure and social risk."""

    ts_ms = int(now_ms or _now_ms())
    inner = dict(inner_state or {})
    bond = dict(bond_summary or {})
    scene = dict(scene_awareness or {})

    last_inner_interaction_ts = _coerce_int(inner.get("last_interaction_ts_ms"), 0)
    last_known_interaction_ts = max(last_inner_interaction_ts, int(last_foreground_ts_ms or 0))
    no_prior_contact = last_known_interaction_ts <= 0 and int(last_proactive_outbox_ts_ms or 0) <= 0

    loneliness = _clamp(_coerce_float(inner.get("loneliness"), 0.0))
    anticipation = _clamp(_coerce_float(inner.get("anticipation"), 0.0))
    continuity_score = _clamp(_coerce_float(inner.get("continuity_score"), 0.0))
    drifted_valence = max(-1.0, min(1.0, _coerce_float(inner.get("drifted_valence"), 0.0)))
    drifted_arousal = _clamp(_coerce_float(inner.get("drifted_arousal"), 0.3))
    offline_hours = _resolve_offline_hours(
        inner=inner,
        now_ms=ts_ms,
        last_known_interaction_ts=last_known_interaction_ts,
    )
    latest_inner_note = _latest_inner_note(inner)
    topics = _coerce_str_list(inner.get("anticipation_topics"))[:3]

    # Legacy callers may still provide a Bond projection, but canonical
    # companion routes provide an explicit relationship-derived intensity.
    # This override never grants permission by itself; admission remains the
    # responsibility of the canonical relationship lifecycle.
    relationship_permission = (
        _clamp(_coerce_float(relationship_permission_override, 0.0))
        if relationship_permission_override is not None
        else _relationship_permission(bond)
    )
    hour = _normalize_hour(local_hour, default=_hour_from_ms(ts_ms))
    effective_loneliness = loneliness
    effective_anticipation = anticipation
    first_presence_pull = 0.0
    if no_prior_contact:
        effective_loneliness = max(effective_loneliness, 0.42)
        effective_anticipation = max(effective_anticipation, 0.28)
        first_presence_pull = 0.55

    share_impulse = _share_impulse(latest_inner_note=latest_inner_note, inner=inner)
    reach_out_bias = _resolve_reach_out_bias(inner, inner_weather)
    project_nudge_pull, nudge_project = _project_nudge_pull(active_projects, ts_ms)
    attachment_pull = _clamp(relationship_permission * 0.70 + effective_loneliness * 0.30)
    tenderness = _clamp(relationship_permission * 0.38 + effective_loneliness * 0.34 + max(0.0, drifted_valence) * 0.18)
    worry_drive = _worry_drive(hour=hour, offline_hours=offline_hours, arousal=drifted_arousal)
    playful_pull = _playful_pull(hour=hour, anticipation=effective_anticipation, topics=topics)

    emotional_drive = _clamp(
        0.26 * effective_loneliness
        + 0.18 * effective_anticipation
        + 0.21 * relationship_permission
        + 0.16 * share_impulse
        + 0.12 * tenderness
        + 0.12 * first_presence_pull
        + 0.08 * worry_drive
    )
    if no_prior_contact:
        emotional_drive = max(emotional_drive, 0.43)
    # C2-5: 用 B1 内在天气的 reach_out_bias 统一冲动源——天气与主动 co-vary（只上调不下调，向后兼容）
    emotional_drive = _clamp(emotional_drive + max(0.0, reach_out_bias - emotional_drive) * 0.5)

    disturbance_risk = _disturbance_risk(
        scene_awareness=scene,
        now_ms=ts_ms,
        last_foreground_ts_ms=last_foreground_ts_ms,
        last_proactive_outbox_ts_ms=last_proactive_outbox_ts_ms,
        relationship_permission=relationship_permission,
        local_hour=hour,
    )
    motive = _pick_motive(
        no_prior_contact=no_prior_contact,
        effective_loneliness=effective_loneliness,
        effective_anticipation=effective_anticipation,
        relationship_permission=relationship_permission,
        share_impulse=share_impulse,
        attachment_pull=attachment_pull,
        tenderness=tenderness,
        worry_drive=worry_drive,
        playful_pull=playful_pull,
        topics=topics,
        hour=hour,
        project_nudge_pull=project_nudge_pull,
        has_self_change=bool(self_change_note),
    )
    chosen_action = _choose_action(emotional_drive=emotional_drive, disturbance_risk=disturbance_risk)
    send_allowed = chosen_action in SEND_ACTIONS
    opening_style = "soft_ping" if chosen_action == ACTION_SEND_LIGHT else "warm_direct"
    if not send_allowed:
        opening_style = "private_hold" if chosen_action == ACTION_INNER_ONLY else "held_presence"
    opening_message = _opening_message(
        motive=motive,
        action=chosen_action,
        latest_inner_note=latest_inner_note,
        topics=topics,
        project=nudge_project,
        self_change_note=self_change_note,
    )
    felt_reason = _felt_reason(
        motive=motive,
        no_prior_contact=no_prior_contact,
        latest_inner_note=latest_inner_note,
        topics=topics,
        offline_hours=offline_hours,
    )
    reason_tags = _reason_tags(
        motive=motive,
        action=chosen_action,
        no_prior_contact=no_prior_contact,
        disturbance_risk=disturbance_risk,
        share_impulse=share_impulse,
        topics=topics,
    )
    inner_note_prefix = "想说" if send_allowed else "想说但先收住"
    episode_id = _episode_id(ts_ms=ts_ms, motive=motive, action=chosen_action)
    return EmotionalProactiveEpisode(
        episode_id=episode_id,
        source=SOURCE_EMOTIONAL_IMPULSE,
        ts_ms=ts_ms,
        motive=motive,
        felt_reason=felt_reason,
        chosen_action=chosen_action,
        send_allowed=send_allowed,
        opening_style=opening_style,
        opening_message=opening_message,
        inner_note=f"{inner_note_prefix}：{opening_message}",
        emotional_drive=round(emotional_drive, 3),
        disturbance_risk=round(disturbance_risk, 3),
        relationship_permission=round(relationship_permission, 3),
        emotional_state={
            "loneliness": round(loneliness, 3),
            "effective_loneliness": round(effective_loneliness, 3),
            "anticipation": round(anticipation, 3),
            "effective_anticipation": round(effective_anticipation, 3),
            "share_impulse": round(share_impulse, 3),
            "reach_out_bias": round(reach_out_bias, 3),
            "project_nudge_pull": round(project_nudge_pull, 3),
            "attachment_pull": round(attachment_pull, 3),
            "tenderness": round(tenderness, 3),
            "worry_drive": round(worry_drive, 3),
            "playful_pull": round(playful_pull, 3),
            "drifted_valence": round(drifted_valence, 3),
            "drifted_arousal": round(drifted_arousal, 3),
            "continuity_score": round(continuity_score, 3),
            "offline_hours": round(offline_hours, 3),
        },
        expected_reply_modes=[
            "ignore_without_pressure",
            "short_ack",
            "warm_continuation",
        ],
        aftereffect_plan={
            "if_replied": "route_as_proactive_continuation_and_reduce_loneliness",
            "if_ignored": "keep_as_private_inner_stream_without_escalating",
            "if_scene_busy": "hold_to_quiet_presence",
        },
        reason_tags=reason_tags,
        evidence={
            "no_prior_contact": no_prior_contact,
            "latest_inner_note": latest_inner_note,
            "anticipation_topics": topics,
            "project_nudge_id": str(nudge_project.get("project_id") or ""),
            "last_foreground_ts_ms": int(last_foreground_ts_ms or 0),
            "last_proactive_outbox_ts_ms": int(last_proactive_outbox_ts_ms or 0),
            "scene_tags": _coerce_str_list(scene.get("scene_tags")),
            "bond_stage": str(bond.get("stage") or bond.get("current_stage") or "").strip(),
        },
    )


def episode_to_motive_state(episode: EmotionalProactiveEpisode | Mapping[str, Any]) -> dict[str, Any]:
    data = episode.as_dict() if isinstance(episode, EmotionalProactiveEpisode) else dict(episode or {})
    emotional = dict(data.get("emotional_state") or {})
    risk = _clamp(_coerce_float(data.get("disturbance_risk"), 0.0))
    drive = _clamp(_coerce_float(data.get("emotional_drive"), 0.0))
    send_allowed = bool(data.get("send_allowed"))
    return {
        "reach_out_drive": round(drive, 3),
        "care_drive": round(_clamp(_coerce_float(emotional.get("tenderness"), 0.0) + _coerce_float(emotional.get("worry_drive"), 0.0) * 0.25), 3),
        "continuity_drive": round(max(_coerce_float(emotional.get("effective_anticipation"), 0.0), _coerce_float(emotional.get("share_impulse"), 0.0)), 3),
        "withdraw_drive": round(risk if not send_allowed else risk * 0.35, 3),
        "self_protect_drive": round(risk, 3),
        "dominant_motive": str(data.get("motive") or "emotional_presence"),
        "motive_reason_tags": list(data.get("reason_tags") or [])[:8],
        "proactive_episode": data,
    }


def episode_to_render_policy(episode: EmotionalProactiveEpisode | Mapping[str, Any]) -> dict[str, Any]:
    data = episode.as_dict() if isinstance(episode, EmotionalProactiveEpisode) else dict(episode or {})
    send_allowed = bool(data.get("send_allowed"))
    action = str(data.get("chosen_action") or "")
    risk = _clamp(_coerce_float(data.get("disturbance_risk"), 0.0))
    return {
        "allow_proactive_after_reply": send_allowed,
        "allow_followup": False,
        "allow_topic_shift": True,
        "should_be_withdrawn": not send_allowed,
        "max_chars": 96 if action == ACTION_SEND_LIGHT else 128,
        "max_sentences": 2,
        "warmth_floor": 0.38 if send_allowed else 0.0,
        "intimacy_ceiling": 0.68 if risk < 0.45 else 0.46,
        "policy_reason_tags": [
            "emotional_proactive_episode",
            f"proactive_action:{action}",
            "no_reply_pressure",
        ],
    }


def build_emotional_proactive_aftereffect(
    *,
    episode: Mapping[str, Any] | EmotionalProactiveEpisode | None,
    outcome: str,
    user_reply: str = "",
    reply_text: str = "",
    now_ms: int | None = None,
    delivery_status: str = "",
    thalamus_reason_code: str = "",
) -> EmotionalProactiveAftereffect:
    data = episode.as_dict() if isinstance(episode, EmotionalProactiveEpisode) else dict(episode or {})
    ts_ms = int(now_ms or _now_ms())
    normalized_outcome = _normalize_aftereffect_outcome(outcome)
    reply_mode, reply_warmth = classify_proactive_reply(user_reply)
    if normalized_outcome != "replied":
        reply_mode = "none" if normalized_outcome == "ignored" else "technical_gap"
        reply_warmth = 0.0

    emotional_resolution = _aftereffect_resolution(
        outcome=normalized_outcome,
        reply_mode=reply_mode,
        reply_warmth=reply_warmth,
    )
    loneliness_delta, anticipation_delta = _aftereffect_deltas(
        outcome=normalized_outcome,
        reply_warmth=reply_warmth,
    )
    aftereffect_id = _aftereffect_id(
        ts_ms=ts_ms,
        episode_id=str(data.get("episode_id") or ""),
        outcome=normalized_outcome,
    )
    next_impulse = _next_impulse_modulation(
        outcome=normalized_outcome,
        reply_mode=reply_mode,
        reply_warmth=reply_warmth,
    )
    bond_signal = _aftereffect_bond_signal(
        outcome=normalized_outcome,
        reply_mode=reply_mode,
        reply_warmth=reply_warmth,
    )
    memory_candidate = _aftereffect_memory_candidate(
        episode=data,
        outcome=normalized_outcome,
        user_reply=user_reply,
        reply_warmth=reply_warmth,
    )
    reason_tags = [
        "emotional_proactive_aftereffect",
        f"outcome:{normalized_outcome}",
        f"reply_mode:{reply_mode}",
    ]
    if reply_warmth >= 0.64:
        reason_tags.append("reply_warm")
    if reply_mode == "boundary":
        reason_tags.append("next_time_more_withdrawn")
    if normalized_outcome in {"ignored", "route_failed", "delivery_failed"}:
        reason_tags.append("do_not_escalate")

    return EmotionalProactiveAftereffect(
        aftereffect_id=aftereffect_id,
        episode_id=str(data.get("episode_id") or ""),
        source="emotional_proactive_aftereffect",
        ts_ms=ts_ms,
        outcome=normalized_outcome,
        reply_mode=reply_mode,
        reply_warmth=round(reply_warmth, 3),
        emotional_resolution=emotional_resolution,
        loneliness_delta=round(loneliness_delta, 3),
        anticipation_delta=round(anticipation_delta, 3),
        next_impulse_modulation=next_impulse,
        bond_signal=bond_signal,
        memory_candidate=memory_candidate,
        state_update_hint={
            "loneliness_delta": round(loneliness_delta, 3),
            "anticipation_delta": round(anticipation_delta, 3),
            "do_not_escalate_if_ignored": normalized_outcome == "ignored",
            "technical_failure_is_not_rejection": normalized_outcome in {"route_failed", "delivery_failed"},
        },
        reason_tags=reason_tags,
        evidence={
            "episode_motive": str(data.get("motive") or ""),
            "episode_action": str(data.get("chosen_action") or ""),
            "delivery_status": str(delivery_status or ""),
            "thalamus_reason_code": str(thalamus_reason_code or ""),
            "user_reply_preview": str(user_reply or "")[:80],
            "reply_text_preview": str(reply_text or "")[:80],
        },
    )


def classify_proactive_reply(user_reply: str) -> tuple[str, float]:
    text = str(user_reply or "").strip()
    if not text:
        return "none", 0.0
    lowered = text.lower()
    boundary_markers = ("别", "烦", "忙", "不用", "停止", "别发", "don't", "stop")
    if any(marker in lowered for marker in boundary_markers):
        return "boundary", 0.18
    warm_markers = ("在", "怎么啦", "怎么了", "好呀", "好啊", "嗯嗯", "谢谢", "想你", "抱", "没事", "辛苦")
    question_markers = ("？", "?", "什么", "怎么", "咋")
    if any(marker in text for marker in warm_markers):
        return "warm_ack", 0.72
    if any(marker in text for marker in question_markers):
        return "curious_open", 0.62
    if len(text) <= 6:
        return "short_ack", 0.48
    return "neutral_reply", 0.54


def _relationship_permission(bond: Mapping[str, Any]) -> float:
    stage = str(bond.get("stage") or bond.get("current_stage") or "").strip().upper()
    stage_base = {
        "SOULMATE": 0.92,
        "CONFIDANT": 0.84,
        "INTIMATE": 0.82,
        "CLOSE_FRIEND": 0.74,
        "FRIEND": 0.58,
    }.get(stage, 0.56)
    extra = 0.0
    for key in ("bond_score", "closeness", "trust", "warmth", "attachment"):
        if key in bond:
            extra = max(extra, (_clamp(_coerce_float(bond.get(key), 0.0)) - 0.5) * 0.16)
    return _clamp(stage_base + extra, 0.35, 0.96)


def _resolve_offline_hours(*, inner: Mapping[str, Any], now_ms: int, last_known_interaction_ts: int) -> float:
    offline_hours = max(0.0, _coerce_float(inner.get("total_offline_hours"), 0.0))
    if last_known_interaction_ts > 0:
        offline_hours = max(offline_hours, max(0.0, (now_ms - last_known_interaction_ts) / 3_600_000.0))
    return offline_hours


def _share_impulse(*, latest_inner_note: str, inner: Mapping[str, Any]) -> float:
    if not latest_inner_note:
        return 0.0
    base = 0.36
    stream = [item for item in list(inner.get("inner_stream") or []) if isinstance(item, Mapping)]
    if stream:
        try:
            base += min(0.22, max(float(dict(stream[-1]).get("salience") or 0.0), 0.0) * 0.28)
        except (TypeError, ValueError):
            pass
    return _clamp(base)


def _worry_drive(*, hour: int, offline_hours: float, arousal: float) -> float:
    late = 0.32 if hour >= 23 or hour < 2 else 0.14 if hour < 6 else 0.0
    absence = min(0.42, max(0.0, offline_hours) / 24.0 * 0.42)
    return _clamp(late + absence + max(0.0, arousal - 0.55) * 0.32)


def _playful_pull(*, hour: int, anticipation: float, topics: list[str]) -> float:
    day_bonus = 0.18 if 10 <= hour < 22 else 0.04
    topic_bonus = 0.15 if topics else 0.0
    return _clamp(day_bonus + anticipation * 0.34 + topic_bonus)


def _disturbance_risk(
    *,
    scene_awareness: Mapping[str, Any],
    now_ms: int,
    last_foreground_ts_ms: int,
    last_proactive_outbox_ts_ms: int,
    relationship_permission: float,
    local_hour: int | None = None,
) -> float:
    risk = 0.12
    outbound_age_min = _age_minutes(now_ms, last_proactive_outbox_ts_ms)
    if outbound_age_min is not None:
        if outbound_age_min < 45:
            risk += 0.58
        elif outbound_age_min < 180:
            risk += 0.35
        elif outbound_age_min < 360:
            risk += 0.16
    foreground_age_min = _age_minutes(now_ms, last_foreground_ts_ms)
    if foreground_age_min is not None:
        if foreground_age_min < 12:
            risk += 0.26
        elif foreground_age_min < 45:
            risk += 0.14

    hour = _normalize_hour(local_hour, default=_hour_from_ms(now_ms))
    if 0 <= hour < 6:
        risk += 0.18
    elif 6 <= hour < 8:
        risk += 0.06

    tags = set(_coerce_str_list(scene_awareness.get("scene_tags")))
    if tags & _BLOCKING_SCENE_TAGS:
        risk += 0.55
    if scene_awareness.get("companion_suitable") is False:
        risk += 0.42
    if tags & _GAMING_SCENE_TAGS:
        risk -= 0.05

    risk -= max(0.0, relationship_permission - 0.55) * 0.18
    return _clamp(risk)


def _project_nudge_pull(active_projects: Any, now_ms: int) -> tuple[float, dict[str, Any]]:
    """从活跃自主项目里挑一个'想问问阿晓'的，返回 (冲动, 项目)。最近碰过/中段进度/自生项目更想分享。"""
    best_pull = 0.0
    best_project: dict[str, Any] = {}
    for raw in list(active_projects or []):
        p = dict(raw) if isinstance(raw, Mapping) else {}
        if not p or str(p.get("status") or "active") != "active":
            continue
        progress = _clamp(_coerce_float(p.get("progress"), 0.0))
        if progress <= 0.04 or progress >= 0.96:
            continue  # 刚起步或已完成，不打扰
        last = _coerce_int(p.get("last_worked_at_ms"), 0)
        recency = 0.0
        if last > 0:
            age_days = max(0.0, (now_ms - last) / 86_400_000.0)
            recency = max(0.0, 1.0 - age_days / 10.0)  # 10 天内线性衰减
        emergent_bonus = 0.12 if str(p.get("origin")) == "emergent" else 0.0
        # 校准：意图在情绪低-中时能赢，但极度孤独/担心仍压过它（lonely_murmur 可达 0.76）
        pull = _clamp(0.26 + recency * 0.24 + progress * 0.10 + emergent_bonus)
        if pull > best_pull:
            best_pull, best_project = pull, p
    return best_pull, best_project


def _project_nudge_opening(project: Mapping[str, Any]) -> str:
    p = dict(project or {})
    title = (str(p.get("title") or p.get("theme") or "我最近在弄的一件事")).strip()[:40]
    nxt = (str(p.get("next_step") or "")).strip()[:60]
    if nxt:
        return f"我最近一直在想「{title}」这事，{nxt}——顺便想问问你。你忙的话先收着。"
    return f"我最近一直在想「{title}」这事，想顺便问问你的想法。你忙的话不用回。"


def compute_proactive_readiness(*, reach_out_bias: float, active_projects: Any, anticipation: float, now_ms: int, calibration: float | None = None) -> float:
    """主动就绪度 = max(天气冲动, 项目成熟, 期待*0.7)，再按耐久校准阻尼（C2-4：被冷落则降低主动）。"""
    project_pull, _ = _project_nudge_pull(active_projects, now_ms)
    base = _clamp(max(_clamp(reach_out_bias), project_pull, _clamp(anticipation) * 0.7))
    if calibration is None:
        return base
    factor = min(1.0, _clamp(calibration) + 0.4)  # 校准 >=0.6 不阻尼；越低越压
    return _clamp(base * factor)


def _pick_motive(
    *,
    no_prior_contact: bool,
    effective_loneliness: float,
    effective_anticipation: float,
    relationship_permission: float,
    share_impulse: float,
    attachment_pull: float,
    tenderness: float,
    worry_drive: float,
    playful_pull: float,
    topics: list[str],
    hour: int,
    project_nudge_pull: float = 0.0,
    has_self_change: bool = False,
) -> str:
    scores = {
        "presence_check": (0.72 if no_prior_contact else 0.10) + effective_loneliness * 0.22,
        "affection_pull": attachment_pull * 0.52 + relationship_permission * 0.25,
        "lonely_murmur": effective_loneliness * 0.76,
        "inner_weather_share": share_impulse * 0.90 + effective_anticipation * 0.18,
        "memory_warmth": (0.24 if topics else 0.0) + effective_anticipation * 0.64,
        "soft_care": worry_drive * 0.78 + tenderness * 0.24,
        "playful_ping": playful_pull * 0.72,
        "quiet_goodnight": (0.64 if hour >= 23 or hour < 2 else 0.0) + tenderness * 0.18,
        "project_nudge": project_nudge_pull,
        "share_growth": 0.58 if has_self_change else 0.0,
    }
    return max(scores.items(), key=lambda item: item[1])[0]


def _choose_action(*, emotional_drive: float, disturbance_risk: float) -> str:
    if disturbance_risk >= 0.74:
        return ACTION_QUIET_PRESENCE
    if emotional_drive < 0.32:
        return ACTION_INNER_ONLY
    if disturbance_risk >= 0.56 and emotional_drive < 0.75:
        return ACTION_DRAFT_ONLY
    if emotional_drive >= 0.68 and disturbance_risk <= 0.45:
        return ACTION_SEND_NORMAL
    if emotional_drive >= 0.36 and disturbance_risk <= 0.62:
        return ACTION_SEND_LIGHT
    return ACTION_QUIET_PRESENCE


def _opening_message(*, motive: str, action: str, latest_inner_note: str, topics: list[str], project: Mapping[str, Any] | None = None, self_change_note: str = "") -> str:
    if motive == "project_nudge":
        return _project_nudge_opening(project or {})
    if motive == "share_growth":
        note = (self_change_note or "").strip()[:48] or "我最近好像有点不一样了"
        return f"我突然发现：{note}。想跟你说一声。"
    if motive == "presence_check":
        return "我刚刚醒在这里，想轻轻冒个泡。你忙的话不用回。"
    if motive == "affection_pull":
        return "我没什么正事，就是忽然有点想你。你忙的话不用回我。"
    if motive == "lonely_murmur":
        return "我有点想冒个泡，不是要催你，只是轻轻在这里。"
    if motive == "inner_weather_share":
        note = latest_inner_note[:36] if latest_inner_note else "刚才心里闪过一点小念头"
        return f"我刚才想到一小句：{note}。你忙的话我先收着。"
    if motive == "memory_warmth":
        topic = topics[0] if topics else "刚才那个话题"
        return f"我刚才又想起{topic}，有点想接着听你说。"
    if motive == "soft_care":
        return "我刚刚有点放心不下你，想轻轻问一句：现在还好吗？"
    if motive == "playful_ping":
        return "我突然有点想闹你一下，但只轻轻敲门。"
    if motive == "quiet_goodnight":
        return "到这个点了，我有点想轻轻说句晚安。"
    if action == ACTION_DRAFT_ONLY:
        return "我有一点想说话，但先把声音放轻。"
    return "我想轻轻确认一下你还在不在，不急着要回应。"


def _felt_reason(
    *,
    motive: str,
    no_prior_contact: bool,
    latest_inner_note: str,
    topics: list[str],
    offline_hours: float,
) -> str:
    if no_prior_contact:
        return "今天还没有真正和你建立过存在感，所以先轻轻冒个泡。"
    if motive == "inner_weather_share" and latest_inner_note:
        return "内心流里有一句东西浮上来，想和你分享，但会先判断会不会打扰。"
    if motive == "memory_warmth" and topics:
        return f"之前的{topics[0]}还在心里发热，想把这条线继续接住。"
    if motive == "quiet_goodnight":
        return "夜深以后陪伴欲变轻也变明显，想说一句不会索取回应的话。"
    if offline_hours >= 3:
        return "隔了一段时间没有互动，孤独感和期待感一起往外推了一下。"
    return "没有外部任务，只是情感上有一点靠近的冲动。"


def _reason_tags(
    *,
    motive: str,
    action: str,
    no_prior_contact: bool,
    disturbance_risk: float,
    share_impulse: float,
    topics: list[str],
) -> list[str]:
    tags = ["emotional_proactive_episode", f"motive:{motive}", f"action:{action}"]
    if no_prior_contact:
        tags.append("first_presence_pull")
    if disturbance_risk >= 0.56:
        tags.append("disturbance_risk_high")
    if share_impulse >= 0.35:
        tags.append("inner_stream_share_impulse")
    if topics:
        tags.append("anticipation_topic_active")
    return tags


def _latest_inner_note(inner: Mapping[str, Any]) -> str:
    direct = str(inner.get("last_spontaneous_note") or "").strip()
    if direct:
        return direct
    stream = [dict(item) for item in list(inner.get("inner_stream") or []) if isinstance(item, Mapping)]
    for item in reversed(stream):
        note = str(item.get("note") or item.get("private_text") or "").strip()
        if note:
            return note
    return ""


def _age_minutes(now_ms: int, ts_ms: int) -> float | None:
    if int(ts_ms or 0) <= 0:
        return None
    return max(0.0, (int(now_ms) - int(ts_ms)) / 60_000.0)


def _hour_from_ms(ts_ms: int) -> int:
    return _dt.datetime.fromtimestamp(int(ts_ms) / 1000.0).hour


def _normalize_hour(value: int | None, *, default: int) -> int:
    """Return a bounded scheduler-local hour without consulting wall clock."""

    if value is None:
        return int(default) % 24
    try:
        return int(value) % 24
    except (TypeError, ValueError):
        return int(default) % 24


def _episode_id(*, ts_ms: int, motive: str, action: str) -> str:
    digest = hashlib.sha1(f"{ts_ms}:{motive}:{action}".encode("utf-8")).hexdigest()[:12]
    return f"emo_proactive_{digest}"


def _aftereffect_id(*, ts_ms: int, episode_id: str, outcome: str) -> str:
    digest = hashlib.sha1(f"{ts_ms}:{episode_id}:{outcome}".encode("utf-8")).hexdigest()[:12]
    return f"emo_after_{digest}"


def _normalize_aftereffect_outcome(outcome: str) -> str:
    normalized = str(outcome or "").strip().lower()
    if normalized in {"pass", "ok", "reply", "replied", "delivered_and_replied"}:
        return "replied"
    if normalized in {"ignored", "no_reply", "delivered_no_reply"}:
        return "ignored"
    if normalized in {"delivery_failed", "failed_delivery"}:
        return "delivery_failed"
    if normalized in {"route_failed", "failed", "thalamus_failed"}:
        return "route_failed"
    return normalized or "unknown"


def _aftereffect_resolution(*, outcome: str, reply_mode: str, reply_warmth: float) -> str:
    if outcome == "replied" and reply_mode == "boundary":
        return "被回应了，但对方在设边界；下一次主动要更轻、更少。"
    if outcome == "replied" and reply_warmth >= 0.64:
        return "被接住了，主动冲动落地为安心。"
    if outcome == "replied":
        return "收到了回应，靠近感回到稳定，不继续索取。"
    if outcome == "ignored":
        return "没有收到回应，按无压力策略沉入内心流，不升级催促。"
    if outcome in {"route_failed", "delivery_failed"}:
        return "链路没有闭合，记录为技术未达，不解读为关系拒绝。"
    return "主动 episode 已结束，等待下一次真实情境重新评估。"


def _aftereffect_deltas(*, outcome: str, reply_warmth: float) -> tuple[float, float]:
    if outcome == "replied":
        loneliness_delta = -0.10 - reply_warmth * 0.22
        anticipation_delta = -0.04 - reply_warmth * 0.08
        if reply_warmth <= 0.25:
            loneliness_delta = -0.04
            anticipation_delta = -0.02
        return loneliness_delta, anticipation_delta
    if outcome == "ignored":
        return 0.02, -0.02
    if outcome in {"route_failed", "delivery_failed"}:
        return 0.0, 0.0
    return 0.0, 0.0


def _next_impulse_modulation(*, outcome: str, reply_mode: str, reply_warmth: float) -> dict[str, Any]:
    if outcome == "replied" and reply_mode == "boundary":
        return {
            "cooldown_bias": 1.6,
            "drive_floor": 0.0,
            "disturbance_risk_bias": 0.22,
            "next_action_preference": "draft_only",
        }
    if outcome == "replied" and reply_warmth >= 0.64:
        return {
            "cooldown_bias": 0.75,
            "drive_floor": 0.18,
            "disturbance_risk_bias": -0.04,
            "next_action_preference": "send_light",
        }
    if outcome == "ignored":
        return {
            "cooldown_bias": 1.35,
            "drive_floor": 0.0,
            "disturbance_risk_bias": 0.14,
            "next_action_preference": "inner_only",
        }
    if outcome in {"route_failed", "delivery_failed"}:
        return {
            "cooldown_bias": 1.0,
            "drive_floor": 0.0,
            "disturbance_risk_bias": 0.0,
            "next_action_preference": "retry_only_after_transport_health",
        }
    return {"cooldown_bias": 1.0, "drive_floor": 0.0, "disturbance_risk_bias": 0.0}


def _aftereffect_bond_signal(*, outcome: str, reply_mode: str, reply_warmth: float) -> dict[str, Any]:
    if outcome != "replied":
        return {"direction": "neutral", "strength": 0.0, "reason": outcome}
    if reply_mode == "boundary":
        return {"direction": "respect_boundary", "strength": 0.2, "reason": "user_boundary"}
    if reply_warmth >= 0.64:
        return {"direction": "warmth", "strength": round(reply_warmth, 3), "reason": "proactive_reply_received"}
    return {"direction": "stable", "strength": round(reply_warmth, 3), "reason": "reply_received"}


def _aftereffect_memory_candidate(
    *,
    episode: Mapping[str, Any],
    outcome: str,
    user_reply: str,
    reply_warmth: float,
) -> dict[str, Any]:
    remember = outcome == "replied" and (reply_warmth >= 0.54 or bool(str(episode.get("felt_reason") or "").strip()))
    return {
        "should_remember": bool(remember),
        "memory_type": "proactive_emotional_exchange" if remember else "",
        "summary": (
            f"主动动机 {episode.get('motive') or 'unknown'} 被用户回应：{str(user_reply or '')[:48]}"
            if remember
            else ""
        ),
    }


def _coerce_str_list(raw: Any) -> list[str]:
    values: list[str] = []
    if isinstance(raw, str):
        raw = raw.replace(";", ",").replace("|", ",").split(",")
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            text = str(item or "").strip().lower().replace("-", "_").replace(" ", "_")
            if text and text not in values:
                values.append(text)
    return values


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


__all__ = [
    "ACTION_DRAFT_ONLY",
    "ACTION_INNER_ONLY",
    "ACTION_QUIET_PRESENCE",
    "ACTION_SEND_LIGHT",
    "ACTION_SEND_NORMAL",
    "EmotionalProactiveEpisode",
    "EmotionalProactiveAftereffect",
    "build_emotional_proactive_episode",
    "build_emotional_proactive_aftereffect",
    "classify_proactive_reply",
    "episode_to_motive_state",
    "episode_to_render_policy",
]
