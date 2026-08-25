"""Canonical Digital Life projection for the OBS runtime snapshot.

This module contains the canonical reader branch.  Keeping the authority
boundary here makes it auditable: a Life reader can never accidentally fall
through to a Bond, timeline, Limbic, or Prefrontal fallback.
"""

from collections.abc import Mapping

from ._shared import *  # noqa: F403
from ._shared import (
    _as_plain_dict,
    _coerce_float,
    _coerce_int,
    _record_snapshot_status,
)
from .background_projection import _build_background_runtime_snapshot
from .ports import LifecycleRuntimeApplicationPorts


_CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY = "CANONICAL_LIFE_JOURNAL"


def resolve_runtime_relationship_subject_id() -> tuple[str, str, str]:
    """Resolve the aggregate subject without treating it as Natsume's self id."""

    try:
        from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415

        subject_id = str(get_canonical_bond_user_id() or "").strip() or "default"
        return subject_id, "live", ""
    except Exception as exc:
        return "default", "fallback", str(exc)[:120]


def _canonical_runtime_self_affect(
    state: Mapping[str, Any],
    *,
    relationship_subject_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str, float]:
    """Return bounded self-affect and the display-safe user observation.

    The observation is deliberately a separate result.  In particular, a
    user's sadness must never become Natsume's own ``emotion_tag`` merely
    because both need to be visible in an operator projection.
    """

    identity = dict(state.get("identity") or {})
    affective = dict(state.get("affective") or {})
    declared_self_id = str(
        affective.get("self_id") or identity.get("self_id") or "natsume_ai"
    ).strip()
    if declared_self_id != "natsume_ai":
        raise RuntimeError("CANONICAL_LIFE_SELF_ID_MISMATCH")
    declared_subject = str(
        affective.get("relationship_subject_id")
        or identity.get("relationship_subject_id")
        or relationship_subject_id
    ).strip()
    if declared_subject != relationship_subject_id:
        raise RuntimeError("CANONICAL_LIFE_RELATIONSHIP_SUBJECT_MISMATCH")

    raw_components = list(affective.get("components") or [])
    components: list[dict[str, Any]] = []
    for raw in raw_components:
        if not isinstance(raw, Mapping):
            continue
        emotion = str(raw.get("emotion") or "").strip().upper()
        if not emotion:
            continue
        intensity = max(0.0, min(1.0, _coerce_float(raw.get("intensity"), 0.0)))
        components.append(
            {
                "emotion": emotion[:48],
                "intensity": round(intensity, 4),
                "valence": round(
                    max(-1.0, min(1.0, _coerce_float(raw.get("valence"), 0.0))),
                    4,
                ),
                "arousal": round(
                    max(0.0, min(1.0, _coerce_float(raw.get("arousal"), 0.0))),
                    4,
                ),
                "action_tendency": str(raw.get("action_tendency") or "observe")
                .strip()
                .lower()[:64],
            }
        )
    components.sort(key=lambda item: (-float(item["intensity"]), str(item["emotion"])))
    mood = dict(affective.get("mood") or {})
    needs = dict(affective.get("needs") or {})
    regulation = dict(affective.get("last_regulation") or {})
    self_affect = {
        "schema_version": "self_affect_state.v2",
        "authority": _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY,
        "self_id": "natsume_ai",
        "relationship_subject_id": relationship_subject_id,
        "revision": max(0, _coerce_int(affective.get("revision"), 0)),
        "mood": {
            "label": str(mood.get("label") or "calm").strip().lower()[:48]
            or "calm",
            "valence": round(
                max(-1.0, min(1.0, _coerce_float(mood.get("valence"), 0.0))), 4
            ),
            "arousal": round(
                max(0.0, min(1.0, _coerce_float(mood.get("arousal"), 0.0))), 4
            ),
            "energy": round(
                max(0.0, min(1.0, _coerce_float(mood.get("energy"), 0.0))), 4
            ),
        },
        "components": components[:4],
        "active_episode_count": min(4, len(list(affective.get("active_episodes") or []))),
        "needs": {
            key: round(max(0.0, min(1.0, _coerce_float(needs.get(key), 0.0))), 4)
            for key in ("connection", "rest", "autonomy", "play", "repair", "safety")
        },
        "regulation_mode": str(regulation.get("mode") or "NONE").strip().upper()[:48]
        or "NONE",
    }
    dominant = components[0] if components else {}
    emotion_tag = str(dominant.get("emotion") or "NEUTRAL")
    emotion_intensity = _coerce_float(dominant.get("intensity"), 0.0)
    observation = dict(dict(state.get("observations") or {}).get("last_user_affect") or {})
    user_observation = {
        "source": str(observation.get("source") or "").strip()[:64],
        "modality": str(observation.get("modality") or "").strip()[:32],
        "emotion_tag": str(observation.get("emotion_tag") or "").strip().upper()[:48],
        "intensity": round(
            max(0.0, min(1.0, _coerce_float(observation.get("intensity"), 0.0))), 4
        ),
        "confidence": round(
            max(0.0, min(1.0, _coerce_float(observation.get("confidence"), 0.0))), 4
        ),
        "observed_at_ms": max(0, _coerce_int(observation.get("observed_at_ms"), 0)),
    }
    return self_affect, user_observation, emotion_tag, emotion_intensity


def _canonical_runtime_relationship(
    state: Mapping[str, Any],
    *,
    relationship_subject_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build an OBS-safe relationship texture without reviving Bond scores."""

    relationship = dict(state.get("relationship_v2") or {})
    declared_self_id = str(relationship.get("self_id") or "natsume_ai").strip()
    declared_subject = str(
        relationship.get("relationship_subject_id") or relationship_subject_id
    ).strip()
    if declared_self_id != "natsume_ai":
        raise RuntimeError("CANONICAL_RELATIONSHIP_SELF_ID_MISMATCH")
    if declared_subject != relationship_subject_id:
        raise RuntimeError("CANONICAL_RELATIONSHIP_SUBJECT_MISMATCH")
    repair_required = bool(relationship.get("repair_required"))
    safety = max(0.0, min(1.0, _coerce_float(relationship.get("safety"), 0.0)))
    texture = "guarded" if repair_required or safety < 0.35 else "familiar"
    bounded = {
        "schema_version": "relationship_state.v2",
        "authority": _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY,
        "self_id": "natsume_ai",
        "relationship_subject_id": relationship_subject_id,
        "workspace_id": str(relationship.get("workspace_id") or "workspace::unbound")
        .strip()[:160],
        "revision": max(0, _coerce_int(relationship.get("revision"), 0)),
        "trust": round(
            max(0.0, min(1.0, _coerce_float(relationship.get("trust"), 0.0))), 4
        ),
        "safety": round(safety, 4),
        "attunement": round(
            max(0.0, min(1.0, _coerce_float(relationship.get("attunement"), 0.0))),
            4,
        ),
        "rupture_load": round(
            max(0.0, min(1.0, _coerce_float(relationship.get("rupture_load"), 0.0))),
            4,
        ),
        "repair_required": repair_required,
        "proactive_permission": str(
            relationship.get("proactive_permission") or "UNKNOWN"
        ).strip().upper()[:64]
        or "UNKNOWN",
        "active_episode_count": min(4, len(list(relationship.get("active_episodes") or []))),
        "texture": texture,
    }
    # Keep legacy presentation fields bounded and non-escalating.  No score,
    # consent or stage may be used to infer romance, exclusivity or outreach.
    bond_state = {
        "user_id": relationship_subject_id,
        "current_stage": "FRIEND",
        "relationship_texture": texture,
        "repair_required": repair_required,
        "authority": _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY,
    }
    bond_summary = {
        "stage": "FRIEND",
        "relationship_texture": texture,
        "repair_required": repair_required,
        "proactive_permission": bounded["proactive_permission"],
        "authority": _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY,
    }
    return bounded, bond_state, bond_summary


def _canonical_runtime_motive(
    *,
    self_affect: Mapping[str, Any],
    relationship: Mapping[str, Any],
    body_sense: Mapping[str, Any],
) -> dict[str, Any]:
    """Project companion posture from self state, never a legacy Bond stage."""

    needs = dict(self_affect.get("needs") or {})
    repair = max(0.0, min(1.0, _coerce_float(needs.get("repair"), 0.0)))
    safety = max(0.0, min(1.0, _coerce_float(needs.get("safety"), 0.0)))
    play = max(0.0, min(1.0, _coerce_float(needs.get("play"), 0.0)))
    connection = max(0.0, min(1.0, _coerce_float(needs.get("connection"), 0.0)))
    fatigue = max(0.0, min(1.0, _coerce_float(body_sense.get("fatigue"), 0.0)))
    rupture = max(0.0, min(1.0, _coerce_float(relationship.get("rupture_load"), 0.0)))
    repair_drive = max(repair, rupture)
    self_protect_drive = max(safety, fatigue)
    choices = (
        ("repair", repair_drive),
        ("rest", self_protect_drive),
        ("play", play),
        ("quiet_presence", connection),
    )
    dominant_motive, dominant_score = max(choices, key=lambda item: (item[1], item[0]))
    reason_tags = [
        f"canonical:{dominant_motive}",
        "canonical:repair_required"
        if bool(relationship.get("repair_required"))
        else "canonical:relationship_bounded",
    ]
    return {
        "authority": _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY,
        "reach_out_drive": 0.0,
        "care_drive": round(repair_drive, 4),
        "withdraw_drive": round(max(0.0, fatigue - 0.25), 4),
        "continuity_drive": round(connection, 4),
        "self_protect_drive": round(self_protect_drive, 4),
        "dominant_motive": dominant_motive if dominant_score > 0.0 else "quiet_presence",
        "motive_reason_tags": reason_tags,
    }


def _canonical_runtime_render_policy(
    *,
    self_affect: Mapping[str, Any],
    relationship: Mapping[str, Any],
    body_sense: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose a bounded posture for OBS without a second reply planner."""

    regulation_mode = str(self_affect.get("regulation_mode") or "NONE").upper()
    repair_required = bool(relationship.get("repair_required"))
    fatigue = max(0.0, min(1.0, _coerce_float(body_sense.get("fatigue"), 0.0)))
    give_space = regulation_mode in {"GIVE_SPACE", "PAUSE", "SILENCE"}
    gentle_boundary = regulation_mode in {"SET_BOUNDARY", "BOUNDARY"}
    max_chars = 120 if fatigue >= 0.6 else 0
    return {
        "authority": _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY,
        "should_be_withdrawn": give_space,
        "allow_followup": not give_space,
        "allow_topic_shift": not gentle_boundary,
        "allow_proactive_after_reply": False,
        "max_chars": max_chars,
        "max_sentences": 2 if max_chars else 0,
        "warmth_floor": 0.0 if gentle_boundary else 0.12,
        "intimacy_ceiling": 0.0,
        "policy_reason_tags": [
            f"canonical:regulation={regulation_mode}",
            "canonical:repair_context" if repair_required else "canonical:bounded_relationship",
        ],
    }


def _canonical_runtime_summary(
    *,
    sys_mode: str,
    relationship: Mapping[str, Any],
    offline_state: Mapping[str, Any],
    body_sense: Mapping[str, Any],
    motive_state: Mapping[str, Any],
    render_policy: Mapping[str, Any],
    slot_status: Mapping[str, Any],
    degraded_reasons: list[str],
) -> dict[str, Any]:
    """A pure OBS summary; never ask legacy agents to explain Life state."""

    dominant = str(motive_state.get("dominant_motive") or "quiet_presence")
    texture = str(relationship.get("texture") or "familiar")
    posture = "withdrawn" if bool(render_policy.get("should_be_withdrawn")) else "balanced"
    return {
        "headline": f"mode={sys_mode} | relationship={texture} | motive={dominant} | reply={posture}",
        "current_state": {
            "sys_mode": sys_mode,
            "bond_stage": "FRIEND",
            "absence_band": str(offline_state.get("absence_band") or "recent"),
            "offline_reconnect_style": "soft_resume",
            "willingness_level": "NORMAL",
            "fatigue_level": str(body_sense.get("fatigue_level") or "FRESH"),
            "drowsiness_level": str(body_sense.get("drowsiness_level") or "ALERT"),
            "activity_event": "",
            "topic_resistance_active": False,
            "self_reflection_risk_level": "",
            "dominant_motive": dominant,
        },
        "reply_posture": {
            "style": posture,
            "max_chars": max(0, _coerce_int(render_policy.get("max_chars"), 0)),
            "max_sentences": max(0, _coerce_int(render_policy.get("max_sentences"), 0)),
            "allow_followup": bool(render_policy.get("allow_followup", True)),
            "allow_topic_shift": bool(render_policy.get("allow_topic_shift", True)),
            "allow_proactive_after_reply": bool(
                render_policy.get("allow_proactive_after_reply", False)
            ),
            "should_be_withdrawn": bool(render_policy.get("should_be_withdrawn")),
            "warmth_floor": _coerce_float(render_policy.get("warmth_floor"), 0.0),
            "intimacy_ceiling": 0.0,
        },
        "decision_chain": list(render_policy.get("policy_reason_tags") or [])[:4]
        + list(motive_state.get("motive_reason_tags") or [])[:4],
        "core_slots": {
            "canonical_life": {
                "status": str(slot_status.get("canonical_life") or "unknown"),
                "authority": _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY,
                "relationship_texture": texture,
            }
        },
        "why_reply": list(render_policy.get("policy_reason_tags") or [])[:4],
        "notable_signals": [],
        "degraded_slots": [
            f"{slot}:{status}"
            for slot, status in dict(slot_status).items()
            if str(status) not in {"live", "retired"}
        ],
        "degraded_reasons": list(degraded_reasons),
    }


def build_canonical_life_runtime_snapshot(
    *,
    ports: LifecycleRuntimeApplicationPorts,
    current_topic: str,
    now_ms: int,
    relationship_subject_id: str,
    subject_status: str,
    subject_detail: str,
) -> dict[str, Any]:
    """Build the production runtime projection from one canonical Life read."""

    slot_status: dict[str, str] = {
        "legacy_emotion_timeline": "retired",
        "legacy_bond": "retired",
    }
    degraded_reasons: list[str] = []
    if subject_status == "live":
        slot_status["relationship_subject_id"] = "live"
    else:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="relationship_subject_id",
            status=subject_status,
            detail=subject_detail,
        )

    hard_state: dict[str, Any] = {}
    try:
        hard_state = _as_plain_dict(ports.get_hard_state())
        slot_status["hard_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="hard_state",
            status="degraded",
            detail=str(exc)[:120],
        )
    sys_mode = str(hard_state.get("sys_mode") or "maintenance").strip().lower()
    if sys_mode not in {"normal", "focus", "gaming", "sleep", "maintenance", "locked"}:
        sys_mode = "maintenance"
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="hard_state",
            status="degraded",
            detail="invalid sys_mode",
        )

    activity_monitor: dict[str, Any] = {}
    try:
        monitor = ports.get_activity_monitor()
        if monitor is None:
            slot_status["activity_monitor"] = "unavailable"
        elif hasattr(monitor, "get_status"):
            activity_monitor = _as_plain_dict(monitor.get_status())
            slot_status["activity_monitor"] = "live"
        else:
            activity_monitor = {"running": bool(getattr(monitor, "_running", False))}
            slot_status["activity_monitor"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="activity_monitor",
            status="degraded",
            detail=str(exc)[:120],
        )

    life_snapshot: dict[str, Any] = {}
    inner_world_state: dict[str, Any] = {}
    self_affect: dict[str, Any] = {}
    relationship_state: dict[str, Any] = {}
    bond_state: dict[str, Any] = {}
    bond_summary: dict[str, Any] = {}
    user_observation: dict[str, Any] = {}
    emotion_tag = "NEUTRAL"
    emotion_intensity = 0.0
    try:
        reader_provider = ports.get_life_snapshot_reader
        if not callable(reader_provider):
            raise RuntimeError("LIFE_SNAPSHOT_READER_NOT_COMPOSED")
        reader = reader_provider()
        if reader is None:
            raise RuntimeError("LIFE_SNAPSHOT_READER_NOT_COMPOSED")
        from host_brain.digital_life.application_boundary import (  # noqa: PLC0415
            project_inner_world_state,
            read_life_snapshot,
        )

        life_snapshot = read_life_snapshot(relationship_subject_id, reader=reader)
        state = dict(life_snapshot.get("state") or {})
        self_affect, user_observation, emotion_tag, emotion_intensity = (
            _canonical_runtime_self_affect(
                state,
                relationship_subject_id=relationship_subject_id,
            )
        )
        relationship_state, bond_state, bond_summary = _canonical_runtime_relationship(
            state,
            relationship_subject_id=relationship_subject_id,
        )
        inner_world_state = dict(project_inner_world_state(life_snapshot) or {})
        # Make the exact same bounded projections explicit for all OBS
        # consumers; they must not reinterpret user affect as self affect.
        inner_world_state["self_affect"] = dict(self_affect)
        inner_world_state["relationship_state"] = dict(relationship_state)
        slot_status["canonical_life"] = "live"
        slot_status["emotion_context"] = "live"
        slot_status["bond_state"] = "live"
        slot_status["inner_world_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="canonical_life",
            status="degraded",
            detail=str(exc)[:120],
        )
        slot_status.setdefault("emotion_context", "unavailable")
        slot_status.setdefault("bond_state", "unavailable")
        slot_status.setdefault("inner_world_state", "unavailable")

    emotion_context = {
        "authority": _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY,
        "self_id": "natsume_ai",
        "relationship_subject_id": relationship_subject_id,
        "life_version": max(0, _coerce_int(life_snapshot.get("version"), 0)),
        "life_state_digest": str(life_snapshot.get("state_digest") or ""),
        "mood_state": str(dict(self_affect.get("mood") or {}).get("label") or "calm"),
        "emotion_tag": emotion_tag,
        "emotion_intensity": round(max(0.0, min(1.0, emotion_intensity)), 4),
        "self_affect": dict(self_affect),
        "user_affect_observation": user_observation,
    }

    body_sense: dict[str, Any] = {}
    try:
        from host_brain.persona.body_sense import project_body_sense_from_life_snapshot  # noqa: PLC0415

        body_sense = project_body_sense_from_life_snapshot(life_snapshot).as_dict()
        body_sense.update(
            {
                "authority": _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY,
                "relationship_subject_id": relationship_subject_id,
                "life_version": emotion_context["life_version"],
            }
        )
        slot_status["body_sense"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="body_sense",
            status="degraded",
            detail=str(exc)[:120],
        )

    offline_state: dict[str, Any] = {}
    try:
        from host_brain.persona.motive_runtime import build_offline_state_from_inner_state  # noqa: PLC0415

        offline_state = dict(
            build_offline_state_from_inner_state(inner_world_state, now_ms=now_ms) or {}
        )
        offline_state["authority"] = _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY
        slot_status["offline_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="offline_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    regulation_mode = str(self_affect.get("regulation_mode") or "NONE").upper()
    silence_state = {
        "authority": _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY,
        "response_willingness": round(
            max(0.0, 1.0 - _coerce_float(body_sense.get("willingness_penalty"), 0.0)),
            4,
        ),
        "willingness_level": (
            "SUBDUED"
            if regulation_mode in {"GIVE_SPACE", "PAUSE", "SILENCE"}
            else "NORMAL"
        ),
        "topic_resistance_active": regulation_mode in {"SET_BOUNDARY", "BOUNDARY"},
        "resistant_topic": "",
        "trigger_reason": f"canonical_regulation:{regulation_mode}",
    }
    slot_status["silence_state"] = "live"

    motive_state = _canonical_runtime_motive(
        self_affect=self_affect,
        relationship=relationship_state,
        body_sense=body_sense,
    )
    render_policy = _canonical_runtime_render_policy(
        self_affect=self_affect,
        relationship=relationship_state,
        body_sense=body_sense,
    )
    slot_status["motive_state"] = "live"
    slot_status["render_policy"] = "live"

    endocrine_state: dict[str, Any] = {}
    try:
        endocrine_state = dict(
            ports.get_endocrine_state(
                emotion_tag=emotion_tag,
                emotion_intensity=emotion_intensity,
            )
            or {}
        )
        endocrine_state["authority"] = "HOST_HARD_STATE_PROJECTION"
        slot_status["endocrine_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="endocrine_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    slot_status.update(
        {
            "bond_narrative": "retired",
            "self_reflection_pulse": "retired",
            "legacy_limbic_runtime": "retired",
            "legacy_prefrontal_runtime": "retired",
        }
    )
    runtime_summary = _canonical_runtime_summary(
        sys_mode=sys_mode,
        relationship=relationship_state,
        offline_state=offline_state,
        body_sense=body_sense,
        motive_state=motive_state,
        render_policy=render_policy,
        slot_status=slot_status,
        degraded_reasons=degraded_reasons,
    )
    background_runtime = _build_background_runtime_snapshot(
        now_ms=now_ms,
        persona_user_id=relationship_subject_id,
        inner_world_state=inner_world_state,
    )
    return {
        "self_id": "natsume_ai",
        "relationship_subject_id": relationship_subject_id,
        "persona_user_id": relationship_subject_id,
        "current_topic": current_topic,
        "sys_mode": sys_mode,
        "hard_state": hard_state,
        "activity_monitor": activity_monitor,
        "emotion_context": emotion_context,
        "endocrine_state": endocrine_state,
        "bond_state": bond_state,
        "bond_summary": bond_summary,
        "bond_summary_human": {},
        "inner_world_state": inner_world_state,
        "offline_state": offline_state,
        "silence_state": silence_state,
        "body_sense": body_sense,
        "self_model_snapshot": {
            "authority": _CANONICAL_LIFE_RUNTIME_SNAPSHOT_AUTHORITY,
            "life_version": emotion_context["life_version"],
        },
        "self_reflection_pulse": {},
        "bond_narrative": {},
        "motive_state": motive_state,
        "render_policy": render_policy,
        "runtime_summary": runtime_summary,
        "background_runtime": background_runtime,
        "slot_status": slot_status,
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "timestamp": now_ms,
    }


__all__ = [
    "build_canonical_life_runtime_snapshot",
    "resolve_runtime_relationship_subject_id",
]
