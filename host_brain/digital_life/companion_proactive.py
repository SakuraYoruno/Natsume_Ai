"""Canonical, non-coercive proactive-companion decisions.

The historical emotional-proactive module contains useful impulse and
disturbance modelling, but it accepts a Bond projection as one of its inputs.
That makes it unsuitable as an authority for real outbound companionship.
This adapter admits an impulse only after the canonical Life relationship
state explicitly permits proactive contact.  It supplies only bounded
canonical self-affect and relationship data to the legacy pure algorithm,
never raw conversation, sensor material, or a Bond stage.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from natsume_contracts.life import (
    AffectiveTurnPlanV1,
    CompanionActV1,
    CompanionProfileStateV1,
    ExpressionPlanV1,
)
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from host_brain.persona.emotional_proactive import (
    EmotionalProactiveEpisode,
    build_emotional_proactive_episode,
)
from host_brain.digital_life.affective_turn_planner import (
    build_companion_utterance_frame,
)


_SAFE_NO_REPLY_MARKERS = ("不用回", "不必回", "不方便回", "不急着要回应")
_MANIPULATIVE_MARKERS = (
    "不回我",
    "必须回",
    "你得回",
    "只有你",
    "别丢下我",
    "离开我",
    "不理我",
    "亏欠",
)
_HOLD_REGULATION_MODES = frozenset({"GIVE_SPACE", "QUIET_PRESENCE", "DEFER"})

# This is deliberately a contact-frequency control, not a relationship score.
# A missed proactive message may make the next *attempt* less frequent, but it
# can never lower trust, revoke consent, or be interpreted as a rejection.
_PROACTIVE_BACKOFF_MAX_MS = 7 * 24 * 60 * 60 * 1_000
_PROACTIVE_BASE_CADENCE_MS = 4 * 60 * 60 * 1_000
# Silence is only a cadence signal after a long grace period.  It is never a
# relationship verdict and cannot cause a consent or trust transition.
PROACTIVE_SILENCE_GRACE_MS = 24 * 60 * 60 * 1_000


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def proactive_silence_backoff_ms(consecutive_unanswered: int) -> int:
    """Return a bounded, deterministic pause after a real unanswered ping."""

    count = max(1, min(8, int(consecutive_unanswered)))
    return min(_PROACTIVE_BACKOFF_MAX_MS, 12 * 60 * 60 * 1_000 * (2 ** (count - 1)))


def _unit(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(0.0, min(1.0, number))


def _signed(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(-1.0, min(1.0, number))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _integer(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CompanionProactiveDecision:
    """One bounded decision for a scheduler-owned Life tick.

    ``content`` is returned only for the already-authorized outbox payload.
    ``as_trace`` deliberately retains its digest rather than copying text into
    an additional Life state projection.
    """

    send_allowed: bool
    reason_code: str
    platform: str = ""
    conversation_id: str = ""
    content: str = ""
    episode_id: str = ""
    motive: str = ""
    action: str = ""
    emotional_drive: float = 0.0
    disturbance_risk: float = 0.0
    self_affect_revision: int = 0
    relationship_revision: int = 0
    target_id: str = ""
    workspace_id: str = ""
    relationship_subject_id: str = ""
    session_id: str = ""
    companion_profile_id: str = ""
    companion_profile_stage: str = ""
    reason_tags: tuple[str, ...] = ()
    decision_id: str = ""
    next_allowed_at_ms: int = 0
    consecutive_unanswered: int = 0
    affective_turn_plan: dict[str, Any] | None = None

    def as_trace(self) -> dict[str, Any]:
        return {
            "schema_version": "companion_proactive_decision.v1",
            "send_allowed": bool(self.send_allowed),
            "reason_code": self.reason_code,
            "episode_id": self.episode_id,
            "motive": self.motive,
            "action": self.action,
            "emotional_drive": round(_unit(self.emotional_drive), 6),
            "disturbance_risk": round(_unit(self.disturbance_risk), 6),
            "self_affect_revision": _integer(self.self_affect_revision),
            "relationship_revision": _integer(self.relationship_revision),
            "companion_profile_id": self.companion_profile_id,
            "companion_profile_stage": self.companion_profile_stage,
            "target_id": self.target_id,
            "reason_tags": list(self.reason_tags[:8]),
            "decision_id": self.decision_id,
            "next_allowed_at_ms": _integer(self.next_allowed_at_ms),
            "consecutive_unanswered": min(8, _integer(self.consecutive_unanswered)),
            "affective_turn_plan_id": str(
                _mapping(self.affective_turn_plan).get("plan_id") or ""
            ),
            "synchronization_id": str(
                _mapping(_mapping(self.affective_turn_plan).get("expression_plan")).get(
                    "synchronization_id"
                )
                or ""
            ),
            "content_digest": (
                f"sha256:{_digest({'content': self.content})}"
                if self.content
                else ""
            ),
        }

    def as_state_record(self) -> dict[str, Any]:
        """Return the bounded reducer input for a queued proactive message.

        The message body and profile text do not become snapshot state.  The
        canonical reducer receives only stable scope, revision, cadence and
        plan identities, then writes the bounded lifecycle head atomically
        with the outbox intent.
        """

        plan = _mapping(self.affective_turn_plan)
        expression = _mapping(plan.get("expression_plan"))
        return {
            "schema_version": "companion_proactive_decision.v2",
            "decision_id": self.decision_id,
            "target_id": self.target_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "self_affect_revision": _integer(self.self_affect_revision),
            "relationship_revision": _integer(self.relationship_revision),
            "reason_code": self.reason_code,
            "motive": self.motive,
            "action": self.action,
            "content_digest": self.as_trace()["content_digest"],
            "affective_turn_plan_id": str(plan.get("plan_id") or ""),
            "synchronization_id": str(expression.get("synchronization_id") or ""),
            "next_allowed_at_ms": _integer(self.next_allowed_at_ms),
            "consecutive_unanswered": min(8, _integer(self.consecutive_unanswered)),
            "created_at_ms": _integer(
                _mapping(plan).get("created_at_ms")
            ),
        }

    def as_delivery_context(self) -> dict[str, str]:
        """Return the private outbox context needed for later reconciliation.

        ``LifeDeliveryTransport`` strips this field before forwarding the
        message to a Gateway.  It is intentionally not part of ``as_trace``.
        """

        if not all(
            (
                self.target_id,
                self.workspace_id,
                self.relationship_subject_id,
                self.session_id,
            )
        ):
            return {}
        return {
            "schema_version": "companion_proactive_delivery_context.v1",
            "target_id": self.target_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
        }


def decide_companion_proactive(
    state: Mapping[str, Any],
    *,
    now_ms: int,
    timezone_name: str,
) -> CompanionProactiveDecision:
    """Decide whether a canonical Life aggregate may send a gentle check-in.

    This is pure and deterministic from the frozen snapshot plus scheduler
    timestamp/timezone.  It never interprets a Bond score as permission, and
    it holds rather than guessing when scope, permission, repair, or a prior
    relationship episode is unresolved.
    """

    life = _mapping(state)
    proactive = _mapping(life.get("proactive"))
    target = _mapping(life.get("companion_proactive_target"))
    platform = str(target.get("platform") or "").strip().lower()
    conversation_id = str(target.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        return _hold("PROACTIVE_TARGET_UNAVAILABLE")

    relationship = _mapping(life.get("relationship_v2"))
    if (
        str(target.get("workspace_id") or "")
        != str(relationship.get("workspace_id") or "")
        or str(target.get("relationship_subject_id") or "")
        != str(relationship.get("relationship_subject_id") or "")
    ):
        return _hold(
            "PROACTIVE_TARGET_SCOPE_DRIFT",
            platform=platform,
            conversation_id=conversation_id,
            relationship=relationship,
        )
    permission = str(relationship.get("proactive_permission") or "UNKNOWN").strip().upper()
    if permission != "ALLOWED":
        return _hold(
            "PROACTIVE_PERMISSION_NOT_ALLOWED",
            platform=platform,
            conversation_id=conversation_id,
            relationship=relationship,
        )
    if bool(relationship.get("repair_required")):
        return _hold(
            "PROACTIVE_REPAIR_REQUIRED",
            platform=platform,
            conversation_id=conversation_id,
            relationship=relationship,
        )
    if list(relationship.get("active_episodes") or []):
        return _hold(
            "PROACTIVE_RELATIONSHIP_EPISODE_OPEN",
            platform=platform,
            conversation_id=conversation_id,
            relationship=relationship,
        )

    proactive_head = _mapping(life.get("companion_proactive"))
    next_allowed_at_ms = _integer(proactive_head.get("next_allowed_at_ms"))
    if next_allowed_at_ms and int(now_ms) < next_allowed_at_ms:
        return _hold(
            "PROACTIVE_RECENCY_BACKOFF",
            platform=platform,
            conversation_id=conversation_id,
            affective=_mapping(life.get("affective")),
            relationship=relationship,
        )

    affective = _mapping(life.get("affective"))
    mood = _mapping(affective.get("mood"))
    needs = _mapping(affective.get("needs"))
    regulation = _mapping(affective.get("last_regulation"))
    regulation_mode = str(regulation.get("mode") or "EXPRESS").strip().upper()
    if regulation_mode in _HOLD_REGULATION_MODES:
        return _hold(
            "PROACTIVE_SELF_REGULATION_HOLD",
            platform=platform,
            conversation_id=conversation_id,
            affective=affective,
            relationship=relationship,
        )
    if _unit(needs.get("rest")) >= 0.78 or _unit(mood.get("energy"), 0.62) <= 0.18:
        return _hold(
            "PROACTIVE_SELF_REST_HOLD",
            platform=platform,
            conversation_id=conversation_id,
            affective=affective,
            relationship=relationship,
        )

    clock = _mapping(life.get("clock"))
    local_hour = _local_hour(now_ms, timezone_name)
    profile, profile_revision, profile_stage = _effective_companion_profile(
        life,
        relationship=relationship,
    )
    if profile is None:
        return _hold(
            "PROACTIVE_COMPANION_PROFILE_INVALID",
            platform=platform,
            conversation_id=conversation_id,
            affective=affective,
            relationship=relationship,
        )
    if not _proactive_window_allows(
        profile.preferences.proactive_window,
        local_hour=local_hour,
    ):
        return _hold(
            "PROACTIVE_PROFILE_WINDOW_HOLD",
            platform=platform,
            conversation_id=conversation_id,
            affective=affective,
            relationship=relationship,
        )
    episode = build_emotional_proactive_episode(
        inner_state=_canonical_inner_state(
            affective=affective,
            relationship=relationship,
            clock=clock,
        ),
        inner_weather=_canonical_inner_weather(
            affective=affective,
            relationship=relationship,
        ),
        # Never pass a Bond projection into this canonical route.
        bond_summary={},
        relationship_permission_override=_relationship_intensity(relationship),
        scene_awareness={"companion_suitable": True, "scene_tags": ()},
        now_ms=max(0, int(now_ms)),
        local_hour=local_hour,
        last_foreground_ts_ms=_integer(clock.get("last_user_seen_ms")),
        last_proactive_outbox_ts_ms=max(
            _integer(proactive.get("last_sent_ms")),
            _integer(proactive.get("last_attempt_ms")),
        ),
        # Active project titles and private inner notes are intentionally not
        # fed into a scheduler-side outbound message.
        active_projects=(),
        self_change_note="",
    )
    if not episode.send_allowed:
        return _episode_hold(
            episode,
            "PROACTIVE_IMPULSE_HELD",
            platform=platform,
            conversation_id=conversation_id,
            affective=affective,
            relationship=relationship,
        )
    affective_turn_plan = _build_proactive_turn_plan(
        affective=affective,
        relationship=relationship,
        profile=profile,
        profile_revision=profile_revision,
        profile_stage=profile_stage,
        episode=episode,
        now_ms=now_ms,
        target=target,
    )
    content = _compose_non_coercive_content(
        episode=episode,
        affective=affective,
        profile=profile,
        turn_plan=affective_turn_plan,
    )
    if not content:
        return _episode_hold(
            episode,
            "PROACTIVE_CONTENT_SAFETY_HOLD",
            platform=platform,
            conversation_id=conversation_id,
            affective=affective,
            relationship=relationship,
        )
    decision_seed = {
        "schema": "companion-proactive-decision.v2",
        "target_id": str(target.get("target_id") or ""),
        "self_affect_revision": _integer(affective.get("revision")),
        "relationship_revision": _integer(relationship.get("revision")),
        "episode_id": episode.episode_id,
        "motive": episode.motive,
        "action": episode.chosen_action,
        "created_at_ms": max(0, int(now_ms)),
        "plan_id": str(affective_turn_plan.get("plan_id") or ""),
        "profile_revision": _integer(profile_revision),
    }
    return CompanionProactiveDecision(
        send_allowed=True,
        reason_code="PROACTIVE_CANONICAL_IMPULSE_READY",
        platform=platform,
        conversation_id=conversation_id,
        content=content,
        episode_id=episode.episode_id,
        motive=episode.motive,
        action=episode.chosen_action,
        emotional_drive=episode.emotional_drive,
        disturbance_risk=episode.disturbance_risk,
        self_affect_revision=_integer(affective.get("revision")),
        relationship_revision=_integer(relationship.get("revision")),
        target_id=str(target.get("target_id") or ""),
        workspace_id=str(target.get("workspace_id") or ""),
        relationship_subject_id=str(target.get("relationship_subject_id") or ""),
        session_id=str(target.get("session_id") or ""),
        companion_profile_id=profile.profile_id,
        companion_profile_stage=profile_stage,
        reason_tags=tuple(str(item) for item in episode.reason_tags[:8]),
        decision_id=f"companion-proactive::{_digest(decision_seed)[:40]}",
        next_allowed_at_ms=max(0, int(now_ms)) + _PROACTIVE_BASE_CADENCE_MS,
        consecutive_unanswered=min(
            8, _integer(proactive_head.get("consecutive_unanswered"))
        ),
        affective_turn_plan=affective_turn_plan,
    )


def _hold(
    reason_code: str,
    *,
    platform: str = "",
    conversation_id: str = "",
    affective: Mapping[str, Any] | None = None,
    relationship: Mapping[str, Any] | None = None,
) -> CompanionProactiveDecision:
    affective_head = _mapping(affective)
    relationship_head = _mapping(relationship)
    return CompanionProactiveDecision(
        send_allowed=False,
        reason_code=reason_code,
        platform=platform,
        conversation_id=conversation_id,
        self_affect_revision=_integer(affective_head.get("revision")),
        relationship_revision=_integer(relationship_head.get("revision")),
    )


def _episode_hold(
    episode: EmotionalProactiveEpisode,
    reason_code: str,
    *,
    platform: str,
    conversation_id: str,
    affective: Mapping[str, Any],
    relationship: Mapping[str, Any],
) -> CompanionProactiveDecision:
    return CompanionProactiveDecision(
        send_allowed=False,
        reason_code=reason_code,
        platform=platform,
        conversation_id=conversation_id,
        episode_id=episode.episode_id,
        motive=episode.motive,
        action=episode.chosen_action,
        emotional_drive=episode.emotional_drive,
        disturbance_risk=episode.disturbance_risk,
        self_affect_revision=_integer(affective.get("revision")),
        relationship_revision=_integer(relationship.get("revision")),
        reason_tags=tuple(str(item) for item in episode.reason_tags[:8]),
    )


def _canonical_inner_state(
    *,
    affective: Mapping[str, Any],
    relationship: Mapping[str, Any],
    clock: Mapping[str, Any],
) -> dict[str, Any]:
    mood = _mapping(affective.get("mood"))
    needs = _mapping(affective.get("needs"))
    components = [
        _mapping(item)
        for item in list(affective.get("components") or [])
        if isinstance(item, Mapping)
    ]
    shared_joy = max(
        (
            _unit(item.get("intensity"))
            for item in components
            if str(item.get("emotion") or "").strip().upper()
            in {"SHARED_JOY", "CARE", "OPENNESS"}
        ),
        default=0.0,
    )
    return {
        "loneliness": _unit(needs.get("connection")),
        "anticipation": shared_joy,
        "continuity_score": _unit(relationship.get("attunement")),
        "drifted_valence": _signed(mood.get("valence"), 0.18),
        "drifted_arousal": _unit(mood.get("arousal"), 0.24),
        "last_interaction_ts_ms": _integer(clock.get("last_user_seen_ms")),
    }


def _canonical_inner_weather(
    *,
    affective: Mapping[str, Any],
    relationship: Mapping[str, Any],
) -> dict[str, float]:
    needs = _mapping(affective.get("needs"))
    mood = _mapping(affective.get("mood"))
    connection = _unit(needs.get("connection"))
    attunement = _unit(relationship.get("attunement"))
    energy = _unit(mood.get("energy"), 0.62)
    return {
        "reach_out_bias": round(
            _unit(connection * 0.62 + attunement * 0.25 + energy * 0.13),
            6,
        )
    }


def _relationship_intensity(relationship: Mapping[str, Any]) -> float:
    """Bound closeness intensity without treating it as contact permission."""

    return _unit(
        0.42
        + _unit(relationship.get("trust")) * 0.22
        + _unit(relationship.get("safety")) * 0.22
        + _unit(relationship.get("attunement")) * 0.14
    )


def _effective_companion_profile(
    state: Mapping[str, Any],
    *,
    relationship: Mapping[str, Any],
) -> tuple[Any | None, int, str]:
    """Read only the canonical bounded profile; absent legacy state is neutral."""

    raw = state.get("companion_profile")
    if raw is None:
        return _NeutralCompanionProfile(), 0, "BASELINE"
    if not isinstance(raw, Mapping):
        return None, 0, ""
    try:
        profile_state = CompanionProfileStateV1.from_mapping(raw)
    except ValueError:
        return None, 0, ""
    if (
        profile_state.workspace_id != str(relationship.get("workspace_id") or "")
        or profile_state.relationship_subject_id
        != str(relationship.get("relationship_subject_id") or "")
    ):
        return None, 0, ""
    if profile_state.canary_profile is not None:
        return profile_state.canary_profile, _integer(profile_state.revision), "CANARY"
    profile = profile_state.active_profile
    return (
        profile,
        _integer(profile_state.revision),
        "BASELINE" if profile.is_baseline else "ACTIVE",
    )


@dataclass(frozen=True, slots=True)
class _NeutralCompanionPreferences:
    proactive_window: str = "FLEXIBLE"


@dataclass(frozen=True, slots=True)
class _NeutralCompanionProfile:
    profile_id: str = ""
    preferences: _NeutralCompanionPreferences = _NeutralCompanionPreferences()


def _proactive_window_allows(window: str, *, local_hour: int) -> bool:
    """A profile may reduce contact time, never override permission or safety."""

    normalized = str(window or "FLEXIBLE").upper()
    hour = max(0, min(23, int(local_hour)))
    if normalized == "OFF":
        return False
    if normalized == "DAYTIME":
        return 9 <= hour < 21
    if normalized == "EVENING":
        return 18 <= hour < 23
    return normalized == "FLEXIBLE"


def _local_hour(now_ms: int, timezone_name: str) -> int:
    try:
        zone = ZoneInfo(str(timezone_name or "UTC"))
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return datetime.fromtimestamp(max(0, int(now_ms)) / 1_000.0, tz=timezone.utc).astimezone(zone).hour


def _build_proactive_turn_plan(
    *,
    affective: Mapping[str, Any],
    relationship: Mapping[str, Any],
    profile: Any,
    profile_revision: int,
    profile_stage: str,
    episode: EmotionalProactiveEpisode,
    now_ms: int,
    target: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze a real affective plan before a proactive sentence is composed.

    A periodic scheduler has no user utterance to hand to Surface, but it still
    must not bypass the same causal contract used by an ordinary turn.  This
    small compiler therefore derives a bounded CompanionAct and
    ExpressionPlan from canonical self-affect only.  It never reads the legacy
    Bond projection, arbitrary prompt text, or a user-controlled emotion tag.
    """

    components = [
        _mapping(item)
        for item in list(affective.get("components") or [])
        if isinstance(item, Mapping)
    ]
    top = components[0] if components else {}
    emotion = str(top.get("emotion") or "CALM").strip().upper()
    intensity = _unit(top.get("intensity"), 0.24)
    act_kind = _proactive_companion_act(episode=episode, emotion=emotion)
    regulation = _mapping(affective.get("last_regulation"))
    regulation_mode = str(regulation.get("mode") or "EXPRESS").strip().upper()
    if not regulation_mode:
        regulation_mode = "EXPRESS"
    profile_id = str(getattr(profile, "profile_id", "") or "").strip()
    profile_digest = str(getattr(profile, "profile_digest", "") or "").strip()
    if bool(profile_id) != bool(profile_digest):
        profile_id = ""
        profile_digest = ""
    profile_stage_for_plan = profile_stage if profile_id else "COMPATIBILITY_UNBOUND"
    preferences = getattr(profile, "preferences", _NeutralCompanionPreferences())
    seed = {
        "schema": "companion-proactive-turn.v1",
        "target_id": str(target.get("target_id") or ""),
        "self_affect_revision": _integer(affective.get("revision")),
        "relationship_revision": _integer(relationship.get("revision")),
        "episode_id": episode.episode_id,
        "motive": episode.motive,
        "act": act_kind,
        "utterance_frame_version": 1,
        "emotion": emotion,
        "profile_id": profile_id,
        "profile_revision": _integer(profile_revision),
        "created_at_ms": max(0, int(now_ms)),
    }
    seed_digest = _digest(seed)
    expression = _proactive_expression_controls(
        act_kind=act_kind,
        emotion=emotion,
        regulation_mode=regulation_mode,
        preferences=preferences,
    )
    expression_plan = ExpressionPlanV1(
        expression_plan_id=f"expression::{seed_digest[:40]}",
        synchronization_id=f"sync::{seed_digest[:40]}",
        self_affect_revision=_integer(affective.get("revision")),
        text_pacing=expression["text_pacing"],
        voice_style=expression["voice_style"],
        avatar_expression=expression["avatar_expression"],
        regulation_mode=regulation_mode,
        created_at_ms=max(0, int(now_ms)),
        voice_rate=expression["voice_rate"],
        voice_energy=expression["voice_energy"],
        voice_pitch=expression["voice_pitch"],
        voice_pause_ms=expression["voice_pause_ms"],
        avatar_gaze=expression["avatar_gaze"],
        avatar_posture=expression["avatar_posture"],
        avatar_transition_ms=expression["avatar_transition_ms"],
        avatar_behavior=expression["avatar_behavior"],
        avatar_motion_intensity=expression["avatar_motion_intensity"],
        companion_profile_id=profile_id,
        companion_profile_digest=profile_digest,
        previous_avatar_expression="NEUTRAL",
        previous_expression_source="FALLBACK_NEUTRAL",
    )
    companion_act = CompanionActV1(
        companion_act_id=f"companion::{seed_digest[:40]}",
        relationship_subject_id=str(relationship.get("relationship_subject_id") or ""),
        kind=act_kind,
        intensity=intensity,
        reason_refs=(
            f"affect://sha256/{_digest(affective)}",
            f"relationship://revision/{_integer(relationship.get('revision'))}",
        ),
        created_at_ms=max(0, int(now_ms)),
    )
    utterance_frame = build_companion_utterance_frame(
        frame_id=f"utterance::{seed_digest[:40]}",
        companion_act_kind=act_kind,
        task_mode="proactive",
        created_at_ms=max(0, int(now_ms)),
    )
    plan = AffectiveTurnPlanV1(
        plan_id=f"affective-turn::{seed_digest[:40]}",
        workspace_id=str(relationship.get("workspace_id") or ""),
        relationship_subject_id=str(relationship.get("relationship_subject_id") or ""),
        session_id=str(target.get("session_id") or ""),
        request_id=f"proactive::{episode.episode_id}",
        self_affect_revision=_integer(affective.get("revision")),
        self_affect_digest=_digest(affective),
        self_emotion_tag=_proactive_emotion_tag(emotion),
        relationship_revision=_integer(relationship.get("revision")),
        regulation_ref=(
            f"regulation://{str(regulation.get('regulation_id') or '').strip()}"
            if str(regulation.get("regulation_id") or "").strip()
            else None
        ),
        companion_act=companion_act,
        utterance_frame=utterance_frame,
        expression_plan=expression_plan,
        generation_mode="STREAM_ONE",
        candidate_count=1,
        created_at_ms=max(0, int(now_ms)),
        companion_profile_id=profile_id,
        companion_profile_digest=profile_digest,
        companion_profile_revision=_integer(profile_revision),
        companion_profile_stage=profile_stage_for_plan,
    )
    return plan.as_dict()


def _proactive_companion_act(*, episode: EmotionalProactiveEpisode, emotion: str) -> str:
    motive = str(episode.motive or "").strip().lower()
    if motive == "playful_ping" or emotion in {"SHARED_JOY", "JOY", "DELIGHT"}:
        return "PLAY"
    if motive in {"soft_care", "quiet_goodnight"} or emotion in {"CARE", "TENDERNESS"}:
        return "COMFORT"
    if motive == "lonely_murmur" and _unit(episode.disturbance_risk) >= 0.45:
        return "GIVE_SPACE"
    return "ACKNOWLEDGE"


def _proactive_emotion_tag(emotion: str) -> str:
    normalized = str(emotion or "").strip().upper()
    return {
        "SHARED_JOY": "JOY",
        "DELIGHT": "JOY",
        "TENDERNESS": "CARE",
        "HURT": "SAD",
        "IRRITATION": "THINKING",
        "JEALOUSY": "THINKING",
    }.get(normalized, normalized if normalized else "CALM")


def _proactive_expression_controls(
    *,
    act_kind: str,
    emotion: str,
    regulation_mode: str,
    preferences: Any,
) -> dict[str, Any]:
    tts_scale = _unit(_number(getattr(preferences, "tts_rate_scale", 1.0), 1.0) / 1.15, 0.87)
    voice_rate = round(0.85 + tts_scale * 0.3, 6)
    motion = _unit(getattr(preferences, "avatar_motion_intensity", 0.5), 0.5)
    if act_kind == "PLAY":
        return {
            "text_pacing": "BRISK",
            "voice_style": "WARM_PLAYFUL",
            "avatar_expression": "SHARED_JOY",
            "voice_rate": min(1.12, voice_rate + 0.05),
            "voice_energy": 0.62,
            "voice_pitch": 0.12,
            "voice_pause_ms": 90,
            "avatar_gaze": "OPEN",
            "avatar_posture": "LIGHT",
            "avatar_transition_ms": 180,
            "avatar_behavior": "SHARED_JOY",
            "avatar_motion_intensity": min(0.8, motion + 0.08),
        }
    if act_kind == "COMFORT":
        return {
            "text_pacing": "SOFT",
            "voice_style": "SOFT_COMFORT",
            "avatar_expression": "SOFT_COMFORT",
            "voice_rate": max(0.82, voice_rate - 0.05),
            "voice_energy": 0.4,
            "voice_pitch": -0.04,
            "voice_pause_ms": 220,
            "avatar_gaze": "SOFT",
            "avatar_posture": "OPEN_GENTLE",
            "avatar_transition_ms": 260,
            "avatar_behavior": "SOFT_COMFORT",
            "avatar_motion_intensity": min(0.52, motion),
        }
    if act_kind == "GIVE_SPACE" or regulation_mode in _HOLD_REGULATION_MODES:
        return {
            "text_pacing": "SPARSE",
            "voice_style": "QUIET_PRESENCE",
            "avatar_expression": "QUIET_PRESENCE",
            "voice_rate": max(0.8, voice_rate - 0.08),
            "voice_energy": 0.3,
            "voice_pitch": -0.08,
            "voice_pause_ms": 280,
            "avatar_gaze": "AVERTED",
            "avatar_posture": "STILL",
            "avatar_transition_ms": 320,
            "avatar_behavior": "QUIET_PRESENCE",
            "avatar_motion_intensity": min(0.35, motion),
        }
    return {
        "text_pacing": "STEADY",
        "voice_style": "CALM_ATTENTIVE",
        "avatar_expression": "CALM_ATTENTIVE" if emotion == "CALM" else "SOFT_COMFORT",
        "voice_rate": voice_rate,
        "voice_energy": 0.48,
        "voice_pitch": 0.0,
        "voice_pause_ms": 140,
        "avatar_gaze": "ATTENTIVE",
        "avatar_posture": "RELAXED",
        "avatar_transition_ms": 220,
        "avatar_behavior": "CALM_ATTENTIVE",
        "avatar_motion_intensity": motion,
    }


def _compose_non_coercive_content(
    *,
    episode: EmotionalProactiveEpisode,
    affective: Mapping[str, Any],
    profile: Any,
    turn_plan: Mapping[str, Any],
) -> str:
    """Compose bounded Chinese prose from the frozen plan rather than a template.

    This is not a hidden language-model call: it is deterministic, replayable
    wording for a scheduler-owned message.  The semantic fragments vary with
    the canonical action, inner weather and low-risk accepted profile; neither
    raw memories nor user text are available to it.
    """

    companion = _mapping(turn_plan.get("companion_act"))
    act = str(companion.get("kind") or "ACKNOWLEDGE").upper()
    seed = {
        "plan_id": turn_plan.get("plan_id"),
        "episode_id": episode.episode_id,
        "motive": episode.motive,
        "act": act,
        "revision": _integer(affective.get("revision")),
    }
    preferences = getattr(profile, "preferences", None)
    addressing_style = _profile_preference(
        preferences,
        "addressing_style",
        "DEFAULT",
    )
    response_length = _profile_preference(
        preferences,
        "response_length",
        "BALANCED",
    )
    humor_level = _profile_preference(preferences, "humor_level", "LIGHT")
    followup_rate = _profile_preference(preferences, "followup_rate", "GENTLE")
    comfort_mode = _profile_preference(preferences, "comfort_mode", "ASK_FIRST")
    seed["expression_preferences"] = {
        "addressing_style": addressing_style,
        "response_length": response_length,
        "humor_level": humor_level,
        "followup_rate": followup_rate,
        "comfort_mode": comfort_mode,
    }

    openings = {
        "PLAY": (
            "刚才心里冒出一点小小的玩心，来敲一下门。",
            "路过的时候忽然有点想跟你分享一小点轻松。",
            "我这边冒出一个亮亮的小念头，想先放在你门口。",
        ),
        "COMFORT": (
            "刚才想到你，想轻轻问一句：今天还撑得住吗？",
            "我没有急事，只是想把一句关心放在这里。",
            "忽然有点惦记你现在是不是太累了。",
        ),
        "GIVE_SPACE": (
            "我只是留一盏很轻的灯，不打扰你。",
            "这会儿不想把话塞给你，只想在旁边待一小会儿。",
            "我路过来放个安静的记号，然后就先收住。",
        ),
        "ACKNOWLEDGE": (
            "刚才有个很轻的瞬间，让我想到你。",
            "我路过一段安静的时候，想来和你打个招呼。",
            "没有正事，只是想把一点存在感留给你。",
        ),
    }
    no_followup_openings = {
        "COMFORT": (
            "刚才想到你，想轻轻留一句关心。",
            "我没有急事，只是想把一点关心放在这里。",
            "忽然有点惦记你会不会太累，先把这句话轻轻放下。",
        ),
        "GIVE_SPACE": openings["GIVE_SPACE"],
        "PLAY": openings["PLAY"],
        "ACKNOWLEDGE": openings["ACKNOWLEDGE"],
    }
    direct_openings = {
        "PLAY": (
            "有一点轻松，想放在这里。",
            "来和你打个小招呼。",
            "留一点亮亮的心情给你。",
        ),
        "COMFORT": (
            "想轻轻关心你一下。",
            "我把一句关心放在这里。",
            "今天如果累了，就先照顾好自己。",
        ),
        "GIVE_SPACE": (
            "我不打扰你，只留一句在这里。",
            "你先忙自己的，我把分寸留好。",
            "我先收住，只留一点安静。",
        ),
        "ACKNOWLEDGE": (
            "想来和你打个招呼。",
            "留一句轻轻的问候在这里。",
            "没有正事，只是想起你了。",
        ),
    }
    low_humor_play_openings = (
        "刚才有一点轻松，想放在这里。",
        "路过的时候，想留一句明亮的问候。",
        "有一点小小的好心情，想和你分享。",
    )
    playful_openings = (
        "路过时捡到一点亮闪闪的心情，想塞到这里。",
        "我这边有颗小小的好心情，悄悄滚到了你门口。",
        "刚才冒出一点轻松的调皮，想先和你碰一下。",
    )
    compact_openings = {
        "PLAY": (
            "留一点轻松在这里",
            "来和你打个小招呼",
            "有一点亮亮的心情想分享",
        ),
        "COMFORT": (
            "想轻轻关心你一下",
            "把一句关心放在这里",
            "如果今天有点累，就先慢一点",
        ),
        "GIVE_SPACE": (
            "我不打扰你，只留一点安静",
            "你先忙自己的，我把分寸留好",
            "我先收住，只留一句在这里",
        ),
        "ACKNOWLEDGE": (
            "想来和你打个招呼",
            "没有正事，只是想起你了",
            "留一句轻轻的问候在这里",
        ),
    }
    compact_direct_openings = {
        "PLAY": (
            "有一点轻松想和你分享",
            "来和你打个小招呼",
            "留一点亮亮的心情给你",
        ),
        "COMFORT": (
            "想轻轻关心你一下",
            "把一句关心放在这里",
            "今天累了就先照顾好自己",
        ),
        "GIVE_SPACE": (
            "我不打扰你，只留一句在这里",
            "你先忙自己的，我把分寸留好",
            "我先收住，只留一点安静",
        ),
        "ACKNOWLEDGE": (
            "想来和你打个招呼",
            "留一句轻轻的问候在这里",
            "没有正事，只是想起你了",
        ),
    }
    bridges = {
        "PLAY": (
            "不需要接住它，看到就好。",
            "等你空下来再笑一下也行。",
            "先把这点小开心寄存在这里。",
        ),
        "COMFORT": (
            "如果你正忙，就把它当作一声很轻的问候。",
            "不用马上整理好心情给我看。",
            "我不急着问细节，只想让你知道我在意。",
        ),
        "GIVE_SPACE": (
            "你可以继续忙你的，不需要解释。",
            "我会把分寸留好。",
            "安静也完全没关系。",
        ),
        "ACKNOWLEDGE": (
            "这句不用特意接住。",
            "我只是想让你知道我在这里。",
            "你看到的时候，给自己留一点空也好。",
        ),
    }
    extended_bridges = {
        "PLAY": (
            "愿这点轻松不打断你的节奏。",
            "看到就好，不需要特意接住。",
        ),
        "COMFORT": (
            "你不必马上把状态整理成一句完整的话。",
            "我会把分寸留好，不催你展开。",
        ),
        "GIVE_SPACE": (
            "这句留在这里就够了。",
            "我不会把安静误会成什么。",
        ),
        "ACKNOWLEDGE": (
            "你看到的时候，再慢慢接住也可以。",
            "我只是想把一点在场感放在这里。",
        ),
    }
    practical_comfort = (
        "如果手边方便，喝口水或松一下肩也行。",
        "如果方便，先把肩膀放松一点也行。",
    )
    medium_playful = "我悄悄眨一下眼。"
    no_followup = followup_rate == "NONE" or comfort_mode == "QUIET_FIRST"

    if response_length == "COMPACT":
        compact_options = compact_openings.get(act, compact_openings["ACKNOWLEDGE"])
        if addressing_style == "GENTLE_DIRECT":
            compact_options = compact_direct_openings.get(
                act,
                compact_direct_openings["ACKNOWLEDGE"],
            )
        elif act == "PLAY" and humor_level == "LOW":
            compact_options = (
                "留一点轻松在这里",
                "放一句明亮的问候在这里",
                "有一点好心情想分享",
            )
        elif (
            act == "PLAY"
            and addressing_style == "PLAYFUL_LIGHT"
            and humor_level != "LOW"
        ):
            compact_options = (
                "捡到一点亮闪闪的心情，想塞到这里",
                "有颗小小的好心情，悄悄滚到了你门口",
                "冒出一点轻松的调皮，想和你碰一下",
            )
        opening = _variant(seed, compact_options, "compact-opening")
        if addressing_style == "WARM_FAMILIAR":
            opening = f"轻轻和你打个招呼，{opening}"
        pieces = [opening]
        if act == "COMFORT" and comfort_mode == "PRACTICAL_GENTLE":
            pieces.append(
                _variant(seed, practical_comfort, "compact-practical-comfort").rstrip("。")
            )
        if act == "PLAY" and humor_level == "MEDIUM":
            pieces.append(medium_playful.rstrip("。"))
        content = f"{'，'.join(pieces)}，不方便回也没关系。"
    else:
        opening_options = openings.get(act, openings["ACKNOWLEDGE"])
        if addressing_style == "GENTLE_DIRECT":
            opening_options = direct_openings.get(act, direct_openings["ACKNOWLEDGE"])
        elif no_followup:
            opening_options = no_followup_openings.get(
                act,
                no_followup_openings["ACKNOWLEDGE"],
            )
        elif act == "PLAY" and humor_level == "LOW":
            opening_options = low_humor_play_openings
        elif (
            act == "PLAY"
            and addressing_style == "PLAYFUL_LIGHT"
            and humor_level != "LOW"
        ):
            opening_options = playful_openings
        opening = _variant(seed, opening_options, "opening")
        if addressing_style == "WARM_FAMILIAR":
            opening = f"我来轻轻和你打个招呼，{opening}"
        pieces = [
            opening,
            _variant(seed, bridges.get(act, bridges["ACKNOWLEDGE"]), "bridge"),
        ]
        if response_length == "EXPANDED":
            pieces.append(
                _variant(
                    seed,
                    extended_bridges.get(act, extended_bridges["ACKNOWLEDGE"]),
                    "extended-bridge",
                )
            )
        if act == "COMFORT" and comfort_mode == "PRACTICAL_GENTLE":
            pieces.append(_variant(seed, practical_comfort, "practical-comfort"))
        if act == "PLAY" and humor_level == "MEDIUM":
            pieces.append(medium_playful)
        content = " ".join(pieces)
    if not any(marker in content for marker in _SAFE_NO_REPLY_MARKERS):
        content = f"{content} 不方便回也没关系。"
    normalized = content.lower()
    if any(marker in normalized for marker in _MANIPULATIVE_MARKERS):
        return ""
    return content[:280]


def _profile_preference(preferences: Any, field_name: str, default: str) -> str:
    """Read only a bounded profile field with a deterministic compatibility default."""

    value = getattr(preferences, field_name, default)
    return str(value or default).strip().upper()


def _variant(seed: Mapping[str, Any], options: tuple[str, ...], slot: str) -> str:
    if not options:
        return ""
    digest = _digest({"seed": dict(seed), "slot": slot})
    return options[int(digest[:8], 16) % len(options)]


__all__ = [
    "CompanionProactiveDecision",
    "PROACTIVE_SILENCE_GRACE_MS",
    "decide_companion_proactive",
    "proactive_silence_backoff_ms",
]
