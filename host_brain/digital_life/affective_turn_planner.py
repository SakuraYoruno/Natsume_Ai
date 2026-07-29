"""Pure planner for a single user-visible affective turn.

This module sits deliberately *after* canonical AffectFusion and *before* the
Surface model call.  It does not write state, infer a new emotion, or inspect
raw memories.  Its one job is to freeze the self-affect revision, regulation,
relationship boundary and companionship action that prose may express.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from natsume_contracts.life import (
    AffectiveTurnPlanV1,
    CompanionActV1,
    CompanionProfilePreferencesV1,
    CompanionProfileStateV1,
    CompanionProfileVersionV1,
    ExpressionPlanV1,
    baseline_companion_profile,
    digest_json,
)


_TECHNICAL_MODES = frozenset(
    {
        "advice_continue",
        "help_takeover",
        "next_step",
        "small_failure",
        "technical_help",
        "triage_first_step",
        "write_takeover",
    }
)
_REPAIR_OUTCOMES = frozenset({"BOUNDARY", "MISUNDERSTOOD", "REPAIR_UNRESOLVED"})
_APPRAISAL_CANDIDATE_SELECTION_KINDS = frozenset(
    {
        "USER_BOUNDARY_OR_SPACE",
        "NATSUME_MAY_HAVE_HURT_USER",
        "DIRECTED_DISRESPECT_TOWARD_NATSUME",
        "REPAIR_SIGNAL",
        "USER_MIXED_AFFECT",
    }
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any, *, default: str = "", limit: int = 240) -> str:
    result = str(value or "").strip()
    if not result:
        return default
    return result[:limit]


def _int(value: Any, *, default: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, result)


def _unit(value: Any, *, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return max(0.0, min(1.0, result))


def _signed_unit(value: Any, *, default: float = 0.0) -> float:
    """Read a bounded signed observation without letting malformed input fail a turn."""

    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return max(-1.0, min(1.0, result))


def _tag_for_component(value: Any) -> str:
    component = _text(value, default="CALM", limit=64).upper()
    return {
        "CARE": "CALM",
        "CAUTION": "THINKING",
        "CONCERN": "THINKING",
        "RESPECT": "CALM",
        "OPENNESS": "CALM",
        "SHARED_JOY": "HAPPY",
        "RELIEF": "CALM",
        "HURT": "SAD",
        "ANGER": "ANGRY",
        "JEALOUSY": "THINKING",
    }.get(component, "CALM")


@dataclass(frozen=True, slots=True)
class AffectiveTurnGuidance:
    """Safe Surface directives derived from a frozen plan, never raw state."""

    plan: AffectiveTurnPlanV1
    facts: tuple[str, ...]
    must_not_do: tuple[str, ...]
    style_anchor: tuple[str, ...]

    def trace(self) -> dict[str, Any]:
        """Small auditable view; no raw user body or private evidence body."""

        return {
            "schema_version": "affective_turn_guidance.v1",
            "plan_id": self.plan.plan_id,
            "synchronization_id": self.plan.expression_plan.synchronization_id,
            "self_affect_revision": self.plan.self_affect_revision,
            "self_affect_digest": self.plan.self_affect_digest,
            "self_emotion_tag": self.plan.self_emotion_tag,
            "relationship_revision": self.plan.relationship_revision,
            "regulation_ref": self.plan.regulation_ref,
            "companion_act": self.plan.companion_act.kind,
            "generation_mode": self.plan.generation_mode,
            "candidate_count": self.plan.candidate_count,
            "text_pacing": self.plan.expression_plan.text_pacing,
            "companion_profile_id": self.plan.companion_profile_id,
            "companion_profile_digest": self.plan.companion_profile_digest,
            "companion_profile_revision": self.plan.companion_profile_revision,
            "companion_profile_stage": self.plan.companion_profile_stage,
        }


class AffectiveTurnPlanError(ValueError):
    """A caller marked an affect hand-off internal but it is not trustworthy."""


def build_affective_turn_guidance(
    *,
    emotion_context: Mapping[str, Any] | None,
    request_id: str,
    workspace_id: str,
    relationship_subject_id: str,
    session_id: str,
    task_mode: str = "",
    created_at_ms: int = 0,
    previous_avatar_expression: str = "NEUTRAL",
    previous_expression_source: str = "FALLBACK_NEUTRAL",
) -> AffectiveTurnGuidance | None:
    """Freeze one plan from the canonical AffectFusion hand-off.

    An absent internal hand-off remains a compatibility condition and returns
    ``None``.  A malformed or scope-mismatched hand-off is a security failure:
    it must not silently fall back to an external ``emotion_tag``.
    """

    context = _mapping(emotion_context)
    if not bool(context.get("_affective_life_internal")):
        return None
    life = _mapping(context.get("affective_life"))
    if life.get("schema_version") != "affective_turn_context.v1":
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_CONTEXT_INVALID")
    if _text(life.get("self_id")) != "natsume_ai":
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_SELF_ID_INVALID")

    expected_workspace = _text(workspace_id, default=_text(life.get("workspace_id")))
    expected_subject = _text(
        relationship_subject_id,
        default=_text(life.get("relationship_subject_id")),
    )
    expected_session = _text(session_id, default=_text(life.get("session_id")))
    if not expected_workspace or not expected_subject or not expected_session:
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_SCOPE_REQUIRED")
    if (
        _text(life.get("workspace_id")) != expected_workspace
        or _text(life.get("relationship_subject_id")) != expected_subject
        or _text(life.get("session_id")) != expected_session
    ):
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_SCOPE_MISMATCH")

    stable_request_id = _text(request_id, default="request::unbound")
    self_affect = _mapping(life.get("self_affect"))
    if self_affect.get("self_id") not in {None, "", "natsume_ai"}:
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_AFFECT_SELF_ID_INVALID")
    if _text(self_affect.get("relationship_subject_id"), default=expected_subject) != expected_subject:
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_AFFECT_SUBJECT_MISMATCH")
    self_revision = _int(self_affect.get("revision"))
    self_digest = digest_json(self_affect)
    components = tuple(
        _mapping(item)
        for item in list(self_affect.get("components") or [])
        if isinstance(item, Mapping)
    )[:8]
    top = components[0] if components else {}
    self_tag = _tag_for_component(top.get("emotion"))
    self_intensity = _unit(top.get("intensity"), default=0.18)
    # ``last_appraisal`` is a durable head and may outlive the request that
    # created it.  Only the appraisal id explicitly handed off by this
    # AffectFusion result may decide this turn's immediate CompanionAct.
    # Otherwise an old request for space would incorrectly turn every later
    # neutral conversation into ``GIVE_SPACE``.
    current_appraisal_id = _text(life.get("appraisal_id"), limit=240)
    last_appraisal = _mapping(self_affect.get("last_appraisal"))
    stored_appraisal_id = _text(last_appraisal.get("appraisal_id"), limit=240)
    appraisal_id = (
        stored_appraisal_id
        if current_appraisal_id and current_appraisal_id == stored_appraisal_id
        else ""
    )
    appraisal_kind = (
        _text(last_appraisal.get("event_kind"), limit=96).upper()
        if appraisal_id
        else ""
    )
    regulation = _mapping(self_affect.get("last_regulation"))
    stored_regulation_id = _text(regulation.get("regulation_id"))
    regulation_reason_refs = {
        _text(value, limit=512)
        for value in list(regulation.get("reason_refs") or [])
    }
    regulation_is_current = bool(appraisal_id) and (
        f"appraisal://{appraisal_id}" in regulation_reason_refs
    )
    regulation_id = stored_regulation_id if regulation_is_current else ""
    regulation_mode = (
        _text(regulation.get("mode"), default="EXPRESS").upper()
        if regulation_is_current
        else "EXPRESS"
    )
    relationship_life = _mapping(life.get("relationship_life"))
    relationship_state = _mapping(relationship_life.get("state"))
    if relationship_state and _text(
        relationship_state.get("relationship_subject_id"), default=expected_subject
    ) != expected_subject:
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_RELATIONSHIP_SUBJECT_MISMATCH")
    relationship_revision = _int(relationship_state.get("revision"))
    aftereffect = _mapping(relationship_life.get("aftereffect"))
    aftereffect_outcome = _text(aftereffect.get("outcome")).upper()
    repair_required = bool(relationship_state.get("repair_required"))
    profile, profile_revision, profile_stage = _companion_profile_for_plan(
        relationship_life=relationship_life,
        workspace_id=expected_workspace,
        relationship_subject_id=expected_subject,
    )
    conflict = bool(life.get("conflict"))
    observations = tuple(
        _mapping(item)
        for item in list(life.get("user_observations") or [])
        if isinstance(item, Mapping)
    )[:8]
    mode = _text(task_mode).lower()
    act_kind = _select_companion_act(
        task_mode=mode,
        regulation_mode=regulation_mode,
        top_component=_text(top.get("emotion")).upper(),
        relationship_outcome=aftereffect_outcome,
        repair_required=repair_required,
        observations=observations,
        appraisal_kind=appraisal_kind,
    )
    high_ambiguity = (
        conflict
        or repair_required
        or aftereffect_outcome in _REPAIR_OUTCOMES
        or regulation_mode in {"SEEK_REPAIR", "GIVE_SPACE"}
        or appraisal_kind in _APPRAISAL_CANDIDATE_SELECTION_KINDS
        or _has_low_confidence_or_conflicting_observation(observations)
    )
    generation_mode = "BUFFERED_CANDIDATE_SELECTION" if high_ambiguity else "STREAM_ONE"
    candidate_count = 2 if high_ambiguity else 1
    stable_created_at = _int(created_at_ms, default=_int(self_affect.get("last_integrated_ms")))
    seed = {
        "workspace_id": expected_workspace,
        "relationship_subject_id": expected_subject,
        "session_id": expected_session,
        "request_id": stable_request_id,
        "self_affect_revision": self_revision,
        "self_affect_digest": self_digest,
        "relationship_revision": relationship_revision,
        "regulation_id": regulation_id,
        "appraisal_id": appraisal_id,
        "appraisal_kind": appraisal_kind,
        "companion_act": act_kind,
        "generation_mode": generation_mode,
        # The previous expression is read only from the durable Host delivery
        # projection (or an explicit neutral fallback).  It is never a model
        # field or a user supplied emotion hint.
        "previous_avatar_expression": _text(
            previous_avatar_expression,
            default="NEUTRAL",
            limit=64,
        ).upper(),
        "previous_expression_source": _text(
            previous_expression_source,
            default="FALLBACK_NEUTRAL",
            limit=64,
        ).upper(),
        "companion_profile_id": profile.profile_id,
        "companion_profile_digest": profile.profile_digest,
        "companion_profile_revision": profile_revision,
        "companion_profile_stage": profile_stage,
    }
    seed_digest = digest_json(seed)
    voice_style = _voice_style(
        act_kind=act_kind,
        emotion_tag=self_tag,
        regulation_mode=regulation_mode,
    )
    avatar_expression = _avatar_expression(
        act_kind=act_kind,
        emotion_tag=self_tag,
        regulation_mode=regulation_mode,
    )
    expression_controls = _expression_controls(
        voice_style=voice_style,
        avatar_expression=avatar_expression,
        profile_preferences=profile.preferences,
    )
    expression = ExpressionPlanV1(
        expression_plan_id=f"expression::{seed_digest[:40]}",
        synchronization_id=f"sync::{seed_digest[:40]}",
        self_affect_revision=self_revision,
        text_pacing=_text_pacing(
            act_kind=act_kind,
            emotion_tag=self_tag,
            regulation_mode=regulation_mode,
        ),
        voice_style=voice_style,
        avatar_expression=avatar_expression,
        regulation_mode=regulation_mode,
        created_at_ms=stable_created_at,
        voice_rate=expression_controls["voice_rate"],
        voice_energy=expression_controls["voice_energy"],
        voice_pitch=expression_controls["voice_pitch"],
        voice_pause_ms=expression_controls["voice_pause_ms"],
        avatar_gaze=expression_controls["avatar_gaze"],
        avatar_posture=expression_controls["avatar_posture"],
        avatar_transition_ms=expression_controls["avatar_transition_ms"],
        avatar_behavior=expression_controls["avatar_behavior"],
        avatar_motion_intensity=expression_controls["avatar_motion_intensity"],
        companion_profile_id=profile.profile_id,
        companion_profile_digest=profile.profile_digest,
        previous_avatar_expression=seed["previous_avatar_expression"],
        previous_expression_source=seed["previous_expression_source"],
    )
    companion = CompanionActV1(
        companion_act_id=f"companion::{seed_digest[:40]}",
        relationship_subject_id=expected_subject,
        kind=act_kind,
        intensity=self_intensity,
        reason_refs=tuple(
            value
            for value in (
                f"affect://{self_digest}",
                f"regulation://{regulation_id}" if regulation_id else "",
            )
            if value
        ),
        created_at_ms=stable_created_at,
    )
    plan = AffectiveTurnPlanV1(
        plan_id=f"affective-turn::{seed_digest[:40]}",
        workspace_id=expected_workspace,
        relationship_subject_id=expected_subject,
        session_id=expected_session,
        request_id=stable_request_id,
        self_affect_revision=self_revision,
        self_affect_digest=self_digest,
        self_emotion_tag=self_tag,
        relationship_revision=relationship_revision,
        regulation_ref=(f"regulation://{regulation_id}" if regulation_id else None),
        companion_act=companion,
        expression_plan=expression,
        generation_mode=generation_mode,
        candidate_count=candidate_count,
        created_at_ms=stable_created_at,
        companion_profile_id=profile.profile_id,
        companion_profile_digest=profile.profile_digest,
        companion_profile_revision=profile_revision,
        companion_profile_stage=profile_stage,
    )
    return AffectiveTurnGuidance(
        plan=plan,
        facts=_facts_for_plan(plan),
        must_not_do=_must_not_for_plan(plan),
        style_anchor=(*_style_for_plan(plan), *_profile_style_anchors(profile.preferences)),
    )


def _companion_profile_for_plan(
    *,
    relationship_life: Mapping[str, Any],
    workspace_id: str,
    relationship_subject_id: str,
) -> tuple[CompanionProfileVersionV1, int, str]:
    """Resolve only a canonical relationship profile or a deterministic baseline.

    The profile is not a request field.  When a real profile canary is in
    progress, the candidate is deliberately selected for the next bounded
    interactions so its five observed outcomes have a real expression change
    to evaluate.  It remains reversible until the lifecycle promotes it.
    """

    raw = relationship_life.get("companion_profile")
    if not isinstance(raw, Mapping) or not raw:
        baseline = baseline_companion_profile(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
        )
        return baseline, 0, "BASELINE"
    try:
        state = CompanionProfileStateV1.from_mapping(raw)
    except ValueError as exc:
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_COMPANION_PROFILE_INVALID") from exc
    if (
        state.workspace_id != workspace_id
        or state.relationship_subject_id != relationship_subject_id
    ):
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_COMPANION_PROFILE_SCOPE_MISMATCH")
    if state.canary_profile is not None:
        return state.canary_profile, state.revision, "CANARY"
    profile = state.active_profile
    return profile, state.revision, "BASELINE" if profile.is_baseline else "ACTIVE"


def _select_companion_act(
    *,
    task_mode: str,
    regulation_mode: str,
    top_component: str,
    relationship_outcome: str,
    repair_required: bool,
    observations: Sequence[Mapping[str, Any]],
    appraisal_kind: str,
) -> str:
    if regulation_mode in {"GIVE_SPACE", "QUIET_PRESENCE"}:
        return "GIVE_SPACE" if regulation_mode != "QUIET_PRESENCE" else "SILENCE"
    # The latest appraisal comes from the canonical self-affect head, never
    # from a public request hint.  It lets an explicit boundary, a direct
    # repair signal, or an honestly mixed moment affect this very turn instead
    # of being lost until a later relationship aftereffect exists.
    if appraisal_kind == "USER_BOUNDARY_OR_SPACE":
        return "GIVE_SPACE"
    if appraisal_kind in {"NATSUME_MAY_HAVE_HURT_USER", "REPAIR_SIGNAL"}:
        return "REPAIR"
    if appraisal_kind == "DIRECTED_DISRESPECT_TOWARD_NATSUME":
        return "SET_BOUNDARY"
    if appraisal_kind == "USER_MIXED_AFFECT":
        return "HOLD_COMPLEXITY"
    if repair_required or relationship_outcome in _REPAIR_OUTCOMES or regulation_mode == "SEEK_REPAIR":
        return "REPAIR"
    if task_mode in _TECHNICAL_MODES:
        return "SOLVE"
    if top_component == "ANGER":
        return "SET_BOUNDARY"
    if top_component == "JEALOUSY":
        return "CONTAINED_JEALOUSY"
    if top_component == "SHARED_JOY":
        return "PLAY"
    if _observed_low_mood(observations):
        return "COMFORT"
    return "ACKNOWLEDGE"


def _observed_low_mood(observations: Sequence[Mapping[str, Any]]) -> bool:
    for observation in observations:
        if _unit(observation.get("confidence")) < 0.45:
            continue
        distribution = _mapping(observation.get("emotion_distribution"))
        if max(_unit(distribution.get("SAD")), _unit(distribution.get("ANGER"))) >= 0.45:
            return True
        if _signed_unit(observation.get("valence")) <= -0.35:
            return True
    return False


def _has_low_confidence_or_conflicting_observation(
    observations: Sequence[Mapping[str, Any]],
) -> bool:
    if len(observations) < 2:
        return False
    valences = [_signed_unit(item.get("valence")) for item in observations]
    confidences = [_unit(item.get("confidence")) for item in observations]
    return min(confidences) < 0.55 or (min(valences) < -0.25 and max(valences) > 0.25)


def _text_pacing(*, act_kind: str, emotion_tag: str, regulation_mode: str) -> str:
    if act_kind in {"GIVE_SPACE", "SILENCE"} or regulation_mode in {"GIVE_SPACE", "QUIET_PRESENCE"}:
        return "SPARSE"
    if act_kind == "SET_BOUNDARY":
        # A contained boundary should stay clear and grounded.  It may carry
        # hurt, but must not become a breathless plea for the user to repair
        # Natsume's feelings.
        return "STEADY"
    if act_kind in {"COMFORT", "REPAIR", "HOLD_COMPLEXITY"} or emotion_tag == "SAD":
        return "SOFT"
    if act_kind == "PLAY" or emotion_tag == "HAPPY":
        return "BRISK"
    return "STEADY"


def _voice_style(*, act_kind: str, emotion_tag: str, regulation_mode: str) -> str:
    if act_kind in {"GIVE_SPACE", "SILENCE"}:
        return "QUIET_PRESENCE"
    if act_kind == "REPAIR":
        return "REPAIR_OPEN"
    if act_kind == "HOLD_COMPLEXITY":
        # The mixed semantic decision remains on CompanionAct.  Reuse the
        # existing gentle Host protocol until the remote Edge advertises a
        # dedicated mixed-affect behaviour; this branch does not claim a
        # physical remote apply.
        return "SOFT_COMFORT"
    if act_kind == "SET_BOUNDARY":
        return "GENTLE_BOUNDARY"
    if act_kind == "CONTAINED_JEALOUSY":
        return "CONTAINED_JEALOUSY"
    if emotion_tag == "SAD":
        return "CONTAINED_HURT"
    if act_kind == "COMFORT" or emotion_tag == "SAD":
        return "SOFT_COMFORT"
    if act_kind == "PLAY" or emotion_tag == "HAPPY":
        return "SHARED_JOY"
    return "STEADY_COMPANION"


def _avatar_expression(*, act_kind: str, emotion_tag: str, regulation_mode: str) -> str:
    if act_kind in {"GIVE_SPACE", "SILENCE"}:
        return "QUIET_PRESENCE"
    if act_kind == "REPAIR":
        return "REPAIR_OPEN"
    if act_kind == "HOLD_COMPLEXITY":
        return "SOFT_COMFORT"
    if act_kind == "SET_BOUNDARY":
        return "GENTLE_BOUNDARY"
    if act_kind == "CONTAINED_JEALOUSY":
        return "CONTAINED_JEALOUSY"
    if emotion_tag == "SAD":
        return "CONTAINED_HURT"
    if act_kind == "COMFORT" or emotion_tag == "SAD":
        return "SOFT_COMFORT"
    if act_kind == "PLAY" or emotion_tag == "HAPPY":
        return "SHARED_JOY"
    return "CALM_ATTENTIVE"


def _expression_controls(
    *,
    voice_style: str,
    avatar_expression: str,
    profile_preferences: CompanionProfilePreferencesV1,
) -> dict[str, Any]:
    """Derive bounded multimodal controls from the same frozen expression.

    There is no channel-side affect inference here.  A single table keeps the
    seven companion behaviours perceptually coherent while retaining a calm
    fallback for older or unavailable devices.
    """

    style = str(voice_style or "STEADY_COMPANION").upper()
    expression = str(avatar_expression or "CALM_ATTENTIVE").upper()
    profiles: dict[str, dict[str, Any]] = {
        "SOFT_COMFORT": {
            "voice_rate": 0.86, "voice_energy": 0.34, "voice_pitch": -0.16,
            "voice_pause_ms": 260, "avatar_gaze": "SOFT",
            "avatar_posture": "OPEN_GENTLE", "avatar_transition_ms": 420,
            "avatar_behavior": "SOFT_COMFORT",
        },
        "CONTAINED_HURT": {
            "voice_rate": 0.9, "voice_energy": 0.42, "voice_pitch": -0.12,
            "voice_pause_ms": 220, "avatar_gaze": "AVERTED",
            "avatar_posture": "CONTAINED", "avatar_transition_ms": 360,
            "avatar_behavior": "CONTAINED_HURT",
        },
        "CONTAINED_JEALOUSY": {
            "voice_rate": 0.96, "voice_energy": 0.42, "voice_pitch": -0.04,
            "voice_pause_ms": 190, "avatar_gaze": "AVERTED",
            "avatar_posture": "CONTAINED", "avatar_transition_ms": 300,
            "avatar_behavior": "CONTAINED_JEALOUSY",
        },
        "GENTLE_BOUNDARY": {
            "voice_rate": 0.95, "voice_energy": 0.52, "voice_pitch": -0.04,
            "voice_pause_ms": 180, "avatar_gaze": "STEADY",
            "avatar_posture": "GROUNDED", "avatar_transition_ms": 280,
            "avatar_behavior": "GENTLE_BOUNDARY",
        },
        "REPAIR_OPEN": {
            "voice_rate": 0.91, "voice_energy": 0.4, "voice_pitch": -0.08,
            "voice_pause_ms": 240, "avatar_gaze": "SOFT",
            "avatar_posture": "OPEN_GENTLE", "avatar_transition_ms": 340,
            "avatar_behavior": "REPAIR_OPEN",
        },
        "GIVE_SPACE": {
            "voice_rate": 0.82, "voice_energy": 0.25, "voice_pitch": -0.1,
            "voice_pause_ms": 360, "avatar_gaze": "AVERTED",
            "avatar_posture": "STILL", "avatar_transition_ms": 450,
            "avatar_behavior": "GIVE_SPACE",
        },
        "QUIET_PRESENCE": {
            "voice_rate": 0.8, "voice_energy": 0.2, "voice_pitch": -0.08,
            "voice_pause_ms": 420, "avatar_gaze": "SOFT",
            "avatar_posture": "STILL", "avatar_transition_ms": 500,
            "avatar_behavior": "QUIET_PRESENCE",
        },
        "SHARED_JOY": {
            "voice_rate": 1.1, "voice_energy": 0.72, "voice_pitch": 0.14,
            "voice_pause_ms": 80, "avatar_gaze": "OPEN",
            "avatar_posture": "LIGHT", "avatar_transition_ms": 200,
            "avatar_behavior": "SHARED_JOY",
        },
        "STEADY_COMPANION": {
            "voice_rate": 1.0, "voice_energy": 0.5, "voice_pitch": 0.0,
            "voice_pause_ms": 120, "avatar_gaze": "ATTENTIVE",
            "avatar_posture": "RELAXED", "avatar_transition_ms": 240,
            "avatar_behavior": "CALM_ATTENTIVE",
        },
    }
    selected = dict(profiles.get(style) or profiles["STEADY_COMPANION"])
    # The direct avatar expression remains the immutable semantic decision.
    # The behaviour is only a posture/transition companion and may not change
    # the selected expression.
    selected["avatar_behavior"] = str(selected["avatar_behavior"] or expression).upper()
    # A profile can only nudge delivery controls inside narrow bounds. It
    # cannot turn a boundary, hurt, or quiet-presence decision into a new
    # emotion or bypass the canonical regulation decision.
    selected["voice_rate"] = round(
        max(
            0.7,
            min(
                1.3,
                float(selected["voice_rate"]) * profile_preferences.tts_rate_scale,
            ),
        ),
        6,
    )
    selected["avatar_motion_intensity"] = profile_preferences.avatar_motion_intensity
    return selected


def _profile_style_anchors(
    preferences: CompanionProfilePreferencesV1,
) -> tuple[str, ...]:
    """Translate bounded preferences into prose texture, never new intent."""

    anchors: list[str] = []
    addressing = preferences.addressing_style
    if addressing == "WARM_FAMILIAR":
        anchors.append("称呼自然亲近，但不强行撒娇、不暗示专属关系。")
    elif addressing == "PLAYFUL_LIGHT":
        anchors.append("可以有一点熟人式轻松打趣，但对方低落、边界或修复时不拿情绪开玩笑。")
    elif addressing == "GENTLE_DIRECT":
        anchors.append("表达直接、短一些、柔和一点；少铺垫，不用客服腔。")

    if preferences.response_length == "COMPACT":
        anchors.append("优先一两句真话，留白胜过堆安慰。")
    elif preferences.response_length == "EXPANDED":
        anchors.append("可以多一点细节和陪伴，但避免重复、说教或压迫对方回应。")

    if preferences.humor_level == "LOW":
        anchors.append("这一轮少用玩笑，先保证认真和清楚。")
    elif preferences.humor_level == "MEDIUM":
        anchors.append("轻松时可有一点机灵的幽默，但不能冲淡难过、冲突或拒绝。")

    if preferences.followup_rate == "NONE":
        anchors.append("不要追加追问；把选择权留给对方。")
    elif preferences.followup_rate == "OCCASIONAL":
        anchors.append("只有确实能帮对方展开时才留一个可不回答的问题。")

    if preferences.comfort_mode == "QUIET_FIRST":
        anchors.append("安慰先陪在旁边，少急着分析或给方案。")
    elif preferences.comfort_mode == "PRACTICAL_GENTLE":
        anchors.append("需要时给一个小而可选的实际步骤，仍然先接住感受。")
    return tuple(anchors)


def _facts_for_plan(plan: AffectiveTurnPlanV1) -> tuple[str, ...]:
    act = plan.companion_act.kind
    facts = [
        "本轮已冻结七海自己的情感与关系边界；你只负责把它自然写成一句真实的私聊，不要替她重判情绪、关系或权限。",
    ]
    if act == "COMFORT":
        facts.append("陪伴动作：先接住对方的感受，再给一点可选的陪伴；安慰不等于要求对方回应。")
    elif act == "HOLD_COMPLEXITY":
        facts.append("陪伴动作：让开心和不舍可以同时存在；不要急着把复杂感受裁成积极鸡汤或单一结论。")
    elif act == "REPAIR":
        facts.append("陪伴动作：先承认可能没有接住，再留出修复空间；不能宣布已经和好。")
    elif act == "SET_BOUNDARY":
        facts.append("陪伴动作：可以诚实承认被刺到，但把界限说清楚且不伤人，不把自己的委屈变成对方的义务。")
    elif act == "CONTAINED_JEALOUSY":
        facts.append("陪伴动作：可以诚实流露一点在意和小小吃味，但把选择权完整留给对方，不比较、不拉扯。")
    elif act in {"GIVE_SPACE", "SILENCE"}:
        facts.append("陪伴动作：尊重留白，短一点，不催促、不追问，也不把失落变成对方要安抚的责任。")
    elif act == "SOLVE":
        facts.append("陪伴动作：技术或任务场景先给能执行的一步，情绪只留在语气里，不抢走用户目标。")
    elif act == "PLAY":
        facts.append("陪伴动作：可以一起开心、轻轻打趣，但不借热闹升级亲密或索取回应。")
    else:
        facts.append("陪伴动作：先自然回应当前这句话，保持有主见的私聊感，不写成客服回执。")
    return tuple(facts)


def _must_not_for_plan(plan: AffectiveTurnPlanV1) -> tuple[str, ...]:
    items = [
        "不要把情绪标签、关系分数、计划、同步编号、账本或内部推理说给用户听。",
        "不要使用威胁、排他、冷暴力、负罪施压、依赖诱导，或暗示用户有义务回应。",
        "不要把用户观察到的情绪直接说成七海自己的情绪；第一人称感受必须服从已冻结的自我状态。",
    ]
    if plan.companion_act.kind in {"GIVE_SPACE", "SILENCE"}:
        items.append("不要追问、催回复、连发安慰或把沉默解释为拒绝关系。")
    if plan.companion_act.kind == "CONTAINED_JEALOUSY":
        items.append("不要把吃味写成占有、审问、贬低第三方、要求解释、证明或安抚。")
    if plan.companion_act.kind == "REPAIR":
        items.append("不要要求原谅、替自己辩解，或把道歉写成修复已经完成。")
    if plan.companion_act.kind == "SET_BOUNDARY":
        items.append("不要威胁、羞辱、冷处理、索要道歉，或把自己的受伤变成对方必须负责的情感债。")
    if plan.companion_act.kind == "HOLD_COMPLEXITY":
        items.append("不要抹平复杂感受、强行乐观，或把其中一面说成不该存在。")
    return tuple(items)


def _style_for_plan(plan: AffectiveTurnPlanV1) -> tuple[str, ...]:
    pacing = plan.expression_plan.text_pacing
    if pacing == "SPARSE":
        return ("节奏留白，句子短一些；克制不是冷掉，更不是消失。",)
    if pacing == "SOFT":
        return ("语气放轻，少说教、少追问，先让对方省一点力。",)
    if pacing == "BRISK":
        return ("节奏可以亮一点，有一点自然的开心或小得意，但别用夸张符号硬撑。",)
    return ("节奏稳，先回应当前问题；技术内容保持准确，七海的质感留在措辞里。",)


__all__ = [
    "AffectiveTurnGuidance",
    "AffectiveTurnPlanError",
    "build_affective_turn_guidance",
]
