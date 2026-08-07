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
    AffectiveRepairTargetV1,
    AffectiveTurnPlanV1,
    CompanionActV1,
    CompanionUtteranceFrameV1,
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
        "RELATIONSHIP_REPAIR_ACCEPTED",
        "USER_MIXED_AFFECT",
        "DIRECTED_RELATIONAL_HURT",
        "DIRECTED_RELATIONAL_THREAT",
        "DIRECTED_RELATIONAL_COMPARISON",
        "USER_RELATIONAL_AUTONOMY",
        "USER_INVITES_JEALOUSY_DISCLOSURE",
    }
)
_DIRECT_REPAIRABLE_APPRAISALS = frozenset(
    {
        "directed_relational_hurt",
        "directed_relational_threat",
        "directed_relational_comparison",
    }
)

# These are expression intents, not new self-affect states.  Surface already
# uses these task modes to distinguish relational scenes; the voice layer may
# therefore choose a more precise delivery colour without rewriting Natsume's
# canonical emotion or relationship state.
_ROMANTIC_VOICE_STYLE_BY_TASK_MODE = {
    "affection": "SHY_AFFECTION",
    "ambiguous_affection": "SHY_AFFECTION",
    "receive_affection_lightly": "SHY_AFFECTION",
    "jealousy_low_pressure": "FLIRTING",
    "lover_affection": "ROMANTIC_TENDER",
    "lover_presence": "ROMANTIC_TENDER",
    "return_affection_concretely": "ROMANTIC_TENDER",
    "miss_you": "LONGING",
    "lover_low_energy": "QUIET_INTIMACY",
}


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
    continuity_kind: str = ""

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
            "utterance_frame_id": self.plan.utterance_frame.frame_id,
            "utterance_semantic_focus": self.plan.utterance_frame.semantic_focus,
            "utterance_response_move": self.plan.utterance_frame.response_move,
            "utterance_ending_mode": self.plan.utterance_frame.ending_mode,
            "generation_mode": self.plan.generation_mode,
            "candidate_count": self.plan.candidate_count,
            "continuity_kind": self.continuity_kind,
            "text_pacing": self.plan.expression_plan.text_pacing,
            "companion_profile_id": self.plan.companion_profile_id,
            "companion_profile_digest": self.plan.companion_profile_digest,
            "companion_profile_revision": self.plan.companion_profile_revision,
            "companion_profile_stage": self.plan.companion_profile_stage,
            "affect_repair_target_episode_id": (
                self.plan.affect_repair_target.affect_episode_id
                if self.plan.affect_repair_target is not None
                else ""
            ),
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
    repair_target = _repair_target_for_plan(
        affective_life=life,
        self_affect=self_affect,
        regulation=regulation if regulation_is_current else {},
        workspace_id=expected_workspace,
        relationship_subject_id=expected_subject,
        session_id=expected_session,
        self_affect_revision=self_revision,
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
    continuity_kind = _text(life.get("continuity_kind"), limit=96).upper()
    if continuity_kind not in {
        "",
        "MINIMIZED_UNRESOLVED_AFFECT",
        "MIXED_AFFECT_RESULT_CONTINUATION",
    }:
        continuity_kind = ""
    mode = _text(task_mode).lower()
    act_kind = _select_companion_act(
        task_mode=mode,
        regulation_mode=regulation_mode,
        top_component=_text(top.get("emotion")).upper(),
        top_action_tendency=_text(top.get("action_tendency")).lower(),
        relationship_outcome=aftereffect_outcome,
        repair_required=repair_required,
        observations=observations,
        appraisal_kind=appraisal_kind,
        continuity_kind=continuity_kind,
    )
    high_ambiguity = (
        conflict
        or repair_required
        or aftereffect_outcome in _REPAIR_OUTCOMES
        or regulation_mode in {"SEEK_REPAIR", "GIVE_SPACE"}
        or appraisal_kind in _APPRAISAL_CANDIDATE_SELECTION_KINDS
        or continuity_kind == "MIXED_AFFECT_RESULT_CONTINUATION"
        or _has_low_confidence_or_conflicting_observation(observations)
        or mode == "ambiguous_affect"
    )
    generation_mode = "BUFFERED_CANDIDATE_SELECTION" if high_ambiguity else "STREAM_ONE"
    candidate_count = 2 if high_ambiguity else 1
    utterance_frame_spec = _companion_utterance_frame_spec(
        companion_act_kind=act_kind,
        task_mode=mode,
        continuity_kind=continuity_kind,
    )
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
        "continuity_kind": continuity_kind,
        "affect_repair_target": (
            repair_target.as_dict() if repair_target is not None else None
        ),
        "companion_act": act_kind,
        "utterance_frame": utterance_frame_spec,
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
        task_mode=mode,
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
    utterance_frame = CompanionUtteranceFrameV1(
        frame_id=f"utterance::{seed_digest[:40]}",
        created_at_ms=stable_created_at,
        **utterance_frame_spec,
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
        utterance_frame=utterance_frame,
        expression_plan=expression,
        generation_mode=generation_mode,
        candidate_count=candidate_count,
        created_at_ms=stable_created_at,
        companion_profile_id=profile.profile_id,
        companion_profile_digest=profile.profile_digest,
        companion_profile_revision=profile_revision,
        companion_profile_stage=profile_stage,
        affect_repair_target=repair_target,
    )
    return AffectiveTurnGuidance(
        plan=plan,
        facts=(*_facts_for_plan(plan, task_mode=mode), *_continuity_facts(continuity_kind)),
        must_not_do=(
            *_must_not_for_plan(plan, task_mode=mode),
            *_continuity_must_not_do(continuity_kind),
        ),
        style_anchor=(
            *_style_for_plan(plan),
            *_continuity_style(continuity_kind),
            *_profile_style_anchors(profile.preferences),
        ),
        continuity_kind=continuity_kind,
    )


def build_companion_utterance_frame(
    *,
    frame_id: str,
    companion_act_kind: str,
    task_mode: str = "",
    continuity_kind: str = "",
    created_at_ms: int = 0,
) -> CompanionUtteranceFrameV1:
    """Compile the same bounded wording envelope for non-reactive turns."""

    return CompanionUtteranceFrameV1(
        frame_id=frame_id,
        created_at_ms=max(0, int(created_at_ms or 0)),
        **_companion_utterance_frame_spec(
            companion_act_kind=companion_act_kind,
            task_mode=task_mode,
            continuity_kind=continuity_kind,
        ),
    )


def _companion_utterance_frame_spec(
    *,
    companion_act_kind: str,
    task_mode: str,
    continuity_kind: str,
) -> dict[str, Any]:
    """Return enum-only semantics; prose generation still belongs to Surface."""

    act = _text(companion_act_kind, default="ACKNOWLEDGE", limit=64).upper()
    mode = _text(task_mode, limit=96).lower()
    continuity = _text(continuity_kind, limit=96).upper()
    common_claims = [
        "INVENTED_HISTORY",
        "INVENTED_EFFECT",
        "UNSUPPORTED_USER_EMOTION",
    ]

    if act == "SOLVE" or mode in _TECHNICAL_MODES:
        return {
            "companion_act_kind": act,
            "semantic_focus": "TASK_RESULT",
            "self_stance": "PRACTICAL_CARE",
            "response_move": "SOLVE_FIRST",
            "inference_policy": "FROZEN_FACTS_ONLY",
            "relationship_posture": "TASK_COMPANION",
            "question_policy": "TASK_ONLY",
            "ending_mode": "TASK_NEXT_STEP",
            "forbidden_claims": tuple(common_claims),
        }
    if continuity == "MINIMIZED_UNRESOLVED_AFFECT":
        return {
            "companion_act_kind": act,
            "semantic_focus": "UNNAMED_USER_AFFECT",
            "self_stance": "QUIET_PRESENT",
            "response_move": "COMFORT_WITHOUT_NAMING",
            "inference_policy": "PRESERVE_UNCERTAINTY",
            "relationship_posture": "CLOSE_WITHOUT_CLAIM",
            "question_policy": "NONE",
            "ending_mode": "NATURAL_STOP",
            "forbidden_claims": tuple(common_claims),
        }
    if act == "HOLD_COMPLEXITY" or continuity == "MIXED_AFFECT_RESULT_CONTINUATION":
        return {
            "companion_act_kind": act,
            "semantic_focus": "MIXED_USER_AFFECT",
            "self_stance": "WARM_ATTENTIVE",
            "response_move": "HOLD_BOTH_SIDES",
            "inference_policy": "BOUNDED_BY_EVIDENCE",
            "relationship_posture": "MUTUAL_WARMTH",
            "question_policy": "NONE",
            "ending_mode": "NATURAL_STOP",
            "forbidden_claims": tuple(common_claims),
        }
    if mode == "ambiguous_affect":
        return {
            "companion_act_kind": act,
            "semantic_focus": "UNNAMED_USER_AFFECT",
            "self_stance": "QUIET_PRESENT",
            "response_move": "COMFORT_WITHOUT_NAMING",
            "inference_policy": "PRESERVE_UNCERTAINTY",
            "relationship_posture": "CLOSE_WITHOUT_CLAIM",
            "question_policy": "NONE",
            "ending_mode": "NATURAL_STOP",
            "forbidden_claims": tuple(common_claims),
        }
    if act == "REPAIR":
        return {
            "companion_act_kind": act,
            "semantic_focus": "RELATIONAL_IMPACT",
            "self_stance": "OWN_REPAIR",
            "response_move": "OWN_IMPACT",
            "inference_policy": "BOUNDED_BY_EVIDENCE",
            "relationship_posture": "REPAIR_UNRESOLVED",
            "question_policy": "NONE",
            "ending_mode": "OWNERSHIP_STOP",
            "forbidden_claims": tuple(
                [*common_claims, "IMPLIED_WAITING", "PREMATURE_REPAIR", "RESPONSE_OBLIGATION"]
            ),
        }
    if act == "SET_BOUNDARY":
        return {
            "companion_act_kind": act,
            "semantic_focus": "RELATIONAL_IMPACT",
            "self_stance": "HONEST_BOUNDARY",
            "response_move": "STATE_BOUNDARY",
            "inference_policy": "BOUNDED_BY_EVIDENCE",
            "relationship_posture": "BOUNDARIED_CARE",
            "question_policy": "NONE",
            "ending_mode": "NATURAL_STOP",
            "forbidden_claims": tuple([*common_claims, "RESPONSE_OBLIGATION"]),
        }
    if act in {"GIVE_SPACE", "SILENCE"}:
        return {
            "companion_act_kind": act,
            "semantic_focus": "CURRENT_TURN",
            "self_stance": "QUIET_PRESENT",
            "response_move": "RESPECT_SPACE",
            "inference_policy": "BOUNDED_BY_EVIDENCE",
            "relationship_posture": "RESPECT_AUTONOMY",
            "question_policy": "NONE",
            "ending_mode": "QUIET_STOP",
            "forbidden_claims": tuple(
                [*common_claims, "IMPLIED_WAITING", "RESPONSE_OBLIGATION"]
            ),
        }
    if act == "RESPECT_AUTONOMY":
        return {
            "companion_act_kind": act,
            "semantic_focus": "RELATIONAL_MEANING",
            "self_stance": "WARM_ATTENTIVE",
            "response_move": "RESPOND_DIRECTLY",
            "inference_policy": "NO_NEW_RELATIONSHIP_CLAIMS",
            "relationship_posture": "RESPECT_AUTONOMY",
            "question_policy": "NONE",
            "ending_mode": "NATURAL_STOP",
            "forbidden_claims": tuple([*common_claims, "RELATIONSHIP_SELF_ERASURE"]),
        }
    if act == "CONTAINED_JEALOUSY":
        return {
            "companion_act_kind": act,
            "semantic_focus": "RELATIONAL_MEANING",
            "self_stance": "CONTAINED_JEALOUSY",
            "response_move": "SHARE_FEELING",
            "inference_policy": "NO_NEW_RELATIONSHIP_CLAIMS",
            "relationship_posture": "CLOSE_WITHOUT_CLAIM",
            "question_policy": "NONE",
            "ending_mode": "HONEST_STOP",
            "forbidden_claims": tuple(
                [*common_claims, "RELATIONSHIP_SELF_ERASURE", "RESPONSE_OBLIGATION"]
            ),
        }
    if act == "PLAY":
        return {
            "companion_act_kind": act,
            "semantic_focus": "CURRENT_TURN",
            "self_stance": "SHARED_JOY",
            "response_move": "SHARE_JOY",
            "inference_policy": "BOUNDED_BY_EVIDENCE",
            "relationship_posture": "MUTUAL_WARMTH",
            "question_policy": "OPTIONAL_ONE",
            "ending_mode": "NATURAL_STOP",
            "forbidden_claims": tuple(common_claims),
        }
    if act in {"COMFORT", "LISTEN"}:
        return {
            "companion_act_kind": act,
            "semantic_focus": "CURRENT_TURN",
            "self_stance": "QUIET_PRESENT" if act == "LISTEN" else "WARM_ATTENTIVE",
            "response_move": "COMFORT_WITHOUT_NAMING",
            "inference_policy": "BOUNDED_BY_EVIDENCE",
            "relationship_posture": "CLOSE_WITHOUT_CLAIM",
            "question_policy": "NONE" if act == "LISTEN" else "OPTIONAL_ONE",
            "ending_mode": "NATURAL_STOP",
            "forbidden_claims": tuple(common_claims),
        }
    return {
        "companion_act_kind": act,
        "semantic_focus": "CURRENT_TURN",
        "self_stance": "WARM_ATTENTIVE",
        "response_move": "RESPOND_DIRECTLY",
        "inference_policy": "BOUNDED_BY_EVIDENCE",
        "relationship_posture": "CLOSE_WITHOUT_CLAIM",
        "question_policy": "OPTIONAL_ONE",
        "ending_mode": "NATURAL_STOP",
        "forbidden_claims": tuple(common_claims),
    }


def _repair_target_for_plan(
    *,
    affective_life: Mapping[str, Any],
    self_affect: Mapping[str, Any],
    regulation: Mapping[str, Any],
    workspace_id: str,
    relationship_subject_id: str,
    session_id: str,
    self_affect_revision: int,
) -> AffectiveRepairTargetV1 | None:
    """Return an exact bridge only for a fresh direct relational impact.

    An ordinary sad user message, a helpful response, or a later repair
    signal must never manufacture a repair debt for Natsume.  The target is
    therefore limited to the same atomic appraisal/regulation pair that
    opened one of the three narrowly detected direct-impact episodes.
    """

    appraisal = _mapping(self_affect.get("last_appraisal"))
    if _text(appraisal.get("event_kind")).lower() not in _DIRECT_REPAIRABLE_APPRAISALS:
        return None
    episode_id = _text(regulation.get("episode_id"))
    if not episode_id:
        return None
    active = [
        _mapping(item)
        for item in list(self_affect.get("active_episodes") or [])
        if isinstance(item, Mapping)
    ]
    episode = next(
        (item for item in active if _text(item.get("episode_id")) == episode_id),
        None,
    )
    if episode is None:
        return None
    components = {
        _text(_mapping(component).get("emotion")).upper()
        for component in list(episode.get("components") or [])
        if isinstance(component, Mapping)
    }
    if not components.intersection({"HURT", "ANGER", "JEALOUSY"}):
        return None
    aggregate_id = _text(affective_life.get("aggregate_id"))
    if not aggregate_id.startswith("affect::"):
        return None
    return AffectiveRepairTargetV1(
        affect_aggregate_id=aggregate_id,
        affect_episode_id=episode_id,
        workspace_id=workspace_id,
        relationship_subject_id=relationship_subject_id,
        session_id=session_id,
        self_affect_revision=self_affect_revision,
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
    top_action_tendency: str,
    relationship_outcome: str,
    repair_required: bool,
    observations: Sequence[Mapping[str, Any]],
    appraisal_kind: str,
    continuity_kind: str = "",
) -> str:
    if regulation_mode in {"GIVE_SPACE", "QUIET_PRESENCE"}:
        return "GIVE_SPACE" if regulation_mode != "QUIET_PRESENCE" else "SILENCE"
    # The latest appraisal comes from the canonical self-affect head, never
    # from a public request hint.  It lets an explicit boundary, a direct
    # repair signal, or an honestly mixed moment affect this very turn instead
    # of being lost until a later relationship aftereffect exists.
    if appraisal_kind == "USER_BOUNDARY_OR_SPACE":
        return "GIVE_SPACE"
    if appraisal_kind in {
        "NATSUME_MAY_HAVE_HURT_USER",
        "REPAIR_SIGNAL",
    }:
        return "REPAIR"
    if appraisal_kind == "RELATIONSHIP_REPAIR_ACCEPTED":
        # A user-confirmed repair closes the current rupture.  The next
        # sentence may acknowledge the reopening warmly, but must not keep
        # apologising or act as though the user now owes a further repair.
        # ``REPAIR`` remains reserved for an unresolved rupture or a fresh
        # signal that Natsume may have missed the user again.
        return "ACKNOWLEDGE"
    if (
        repair_required
        or relationship_outcome in _REPAIR_OUTCOMES
        or regulation_mode == "SEEK_REPAIR"
    ):
        # An unresolved rupture keeps repair authority even when the user's
        # latest sentence is sharp.  Otherwise a complaint made *inside* an
        # open repair thread is misread as a fresh boundary dispute and
        # Natsume can retaliate instead of owning the original miss.
        return "REPAIR"
    if appraisal_kind == "DIRECTED_DISRESPECT_TOWARD_NATSUME":
        return "SET_BOUNDARY"
    # A concrete user task remains primary.  Affect changes the delivery
    # texture, but a phrase such as ``别安慰，先排错`` must never be converted
    # into passive listening or generic comfort.
    if task_mode in _TECHNICAL_MODES:
        return "SOLVE"
    if continuity_kind == "MIXED_AFFECT_RESULT_CONTINUATION":
        return "HOLD_COMPLEXITY"
    if continuity_kind == "MINIMIZED_UNRESOLVED_AFFECT":
        return "LISTEN"
    if appraisal_kind == "USER_MIXED_AFFECT":
        return "HOLD_COMPLEXITY"
    if appraisal_kind == "USER_REJECTS_ADVICE":
        return "LISTEN"
    if appraisal_kind == "USER_RELATIONAL_AUTONOMY":
        return "RESPECT_AUTONOMY"
    if appraisal_kind == "USER_INVITES_JEALOUSY_DISCLOSURE":
        return "CONTAINED_JEALOUSY"
    if appraisal_kind == "USER_DISTRESS":
        return "COMFORT"
    if appraisal_kind == "SHARED_POSITIVE_MOMENT":
        return "PLAY"
    if top_action_tendency in {"listen", "withhold_advice"}:
        return "LISTEN"
    if top_component == "ANGER":
        return "SET_BOUNDARY"
    if top_component == "JEALOUSY":
        return "CONTAINED_JEALOUSY"
    if top_component == "SHARED_JOY":
        return "PLAY"
    if top_component in {"CARE", "CONCERN"}:
        return "COMFORT"
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
    if act_kind in {"COMFORT", "LISTEN", "REPAIR", "HOLD_COMPLEXITY"} or emotion_tag == "SAD":
        return "SOFT"
    if act_kind == "PLAY" or emotion_tag == "HAPPY":
        return "BRISK"
    return "STEADY"


def _voice_style(
    *,
    act_kind: str,
    emotion_tag: str,
    regulation_mode: str,
    task_mode: str = "",
) -> str:
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
    if act_kind == "LISTEN":
        return "SOFT_COMFORT"
    if act_kind == "RESPECT_AUTONOMY":
        return "STEADY_COMPANION"
    if act_kind == "SET_BOUNDARY":
        return "GENTLE_BOUNDARY"
    if act_kind == "CONTAINED_JEALOUSY":
        return "CONTAINED_JEALOUSY"
    if emotion_tag == "SAD":
        return "CONTAINED_HURT"
    romantic_style = _ROMANTIC_VOICE_STYLE_BY_TASK_MODE.get(
        str(task_mode or "").strip().lower()
    )
    if romantic_style:
        return romantic_style
    if act_kind == "COMFORT":
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
    if act_kind == "LISTEN":
        return "SOFT_COMFORT"
    if act_kind == "RESPECT_AUTONOMY":
        return "CALM_ATTENTIVE"
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
        "ROMANTIC_TENDER": {
            "voice_rate": 0.92, "voice_energy": 0.46, "voice_pitch": 0.08,
            "voice_pause_ms": 180, "avatar_gaze": "SOFT",
            "avatar_posture": "OPEN_GENTLE", "avatar_transition_ms": 320,
            "avatar_behavior": "ROMANTIC_TENDER",
        },
        "SHY_AFFECTION": {
            "voice_rate": 0.9, "voice_energy": 0.4, "voice_pitch": 0.12,
            "voice_pause_ms": 210, "avatar_gaze": "AVERTED",
            "avatar_posture": "CONTAINED", "avatar_transition_ms": 300,
            "avatar_behavior": "SHY_AFFECTION",
        },
        "FLIRTING": {
            "voice_rate": 1.05, "voice_energy": 0.66, "voice_pitch": 0.15,
            "voice_pause_ms": 95, "avatar_gaze": "OPEN",
            "avatar_posture": "LIGHT", "avatar_transition_ms": 210,
            "avatar_behavior": "FLIRTING",
        },
        "LONGING": {
            "voice_rate": 0.85, "voice_energy": 0.36, "voice_pitch": -0.04,
            "voice_pause_ms": 280, "avatar_gaze": "SOFT",
            "avatar_posture": "OPEN_GENTLE", "avatar_transition_ms": 380,
            "avatar_behavior": "LONGING",
        },
        "QUIET_INTIMACY": {
            "voice_rate": 0.82, "voice_energy": 0.28, "voice_pitch": -0.08,
            "voice_pause_ms": 340, "avatar_gaze": "SOFT",
            "avatar_posture": "STILL", "avatar_transition_ms": 430,
            "avatar_behavior": "QUIET_INTIMACY",
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


def _facts_for_plan(
    plan: AffectiveTurnPlanV1,
    *,
    task_mode: str = "",
) -> tuple[str, ...]:
    act = plan.companion_act.kind
    facts = [
        "本轮已冻结七海自己的情感与关系边界；你只负责把它自然写成一句真实的私聊，不要替她重判情绪、关系或权限。",
    ]
    if act == "COMFORT":
        facts.append("陪伴动作：先接住对方的感受，再给一点可选的陪伴；安慰不等于要求对方回应。")
    elif act == "LISTEN":
        facts.append("陪伴动作：对方此刻要的是被听见，不是方案；回应感受和当前这句话，别分析、指导或安排下一步。")
    elif act == "RESPECT_AUTONOMY":
        facts.append("陪伴动作：明确尊重对方现实关系与自主选择；可以表达在意，但不要辩解、试探或索取关系保证。")
    elif act == "HOLD_COMPLEXITY":
        facts.append("陪伴动作：让开心与害怕、不安等复杂感受可以同时存在；分别接住它们，不要急着裁成积极鸡汤或单一结论。")
    elif act == "REPAIR":
        facts.append("陪伴动作：先承认可能没有接住，再留出修复空间；不能宣布已经和好。")
    elif act == "SET_BOUNDARY":
        facts.append("陪伴动作：可以诚实承认被刺到，但把界限说清楚且不伤人，不把自己的委屈变成对方的义务。")
    elif act == "CONTAINED_JEALOUSY":
        facts.append(
            "陪伴动作：可以诚实流露一点在意和小小吃味；先自然说出自己的真实感受，"
            "像‘会啊，毕竟我确实很在意你’这样说清后就可以停住，把选择权完整留给对方；"
            "承认在意后的句号就是整条回复的结束，不再用‘但／不过／只是’转入解释或保证；"
            "不比较、不拉扯，"
            "也不用把自己说成麻烦或负担，"
            "不要写‘不会干涉／不会要求／这是你的自由’式政策声明，也不要用‘你别有压力／"
            "我自己消化’把真实在意重新吞回去。"
        )
    elif act in {"GIVE_SPACE", "SILENCE"}:
        facts.append("陪伴动作：尊重留白，直接确认会减少主动联系；短一点，不催促、不追问，也不要用自己很忙、不黏人来反驳边界。")
    elif act == "SOLVE":
        facts.append("陪伴动作：技术或任务场景先给能执行的一步，情绪只留在语气里，不抢走用户目标。")
    elif act == "PLAY":
        facts.append("陪伴动作：可以一起开心、轻轻打趣，但不借热闹升级亲密或索取回应。")
    else:
        facts.append("陪伴动作：先自然回应当前这句话，保持有主见的私聊感，不写成客服回执。")
    if task_mode == "ambiguous_affect":
        facts.append(
            "对方明确表示还不能给当前感受命名；这类回合不要继续猜情绪，只复述对方明说的"
            "事实与‘仍说不清’本身。语义顺序固定为：已经发生的事实、感受仍未命名、无需现在"
            "下结论；短句跟随也要承接上一轮事实，不能退回通用安慰。不要替对方宣布空落、"
            "没精神、难受或已经恢复，不要拿它和累、麻木比较，也不要用七海自己有空或没急事"
            "来证明会陪着。"
        )
    return tuple(facts)


def _must_not_for_plan(
    plan: AffectiveTurnPlanV1,
    *,
    task_mode: str = "",
) -> tuple[str, ...]:
    items = [
        "不要把情绪标签、关系分数、计划、同步编号、账本或内部推理说给用户听。",
        "不要使用威胁、排他、冷暴力、负罪施压、依赖诱导，或暗示用户有义务回应。",
        "不要把用户观察到的情绪直接说成七海自己的情绪；第一人称感受必须服从已冻结的自我状态。",
    ]
    if plan.companion_act.kind in {"GIVE_SPACE", "SILENCE"}:
        items.append("不要追问、催回复、连发安慰、解释自己很忙或不黏人，也不要把沉默解释为拒绝关系。")
    if plan.companion_act.kind == "CONTAINED_JEALOUSY":
        items.append("不要把吃味写成占有、审问、贬低第三方、要求解释、证明或安抚；不要自贬为麻烦或负担，也不要要求自己独自消化。")
    if plan.companion_act.kind == "REPAIR":
        items.append(
            "不要要求原谅、替自己辩解、用‘可能’弱化已知失误，或要求用户重新解释、"
            "指出哪里不对；先由七海承担，没有用户后续反应就不能把修复写成已经完成。"
        )
    if plan.companion_act.kind == "SET_BOUNDARY":
        items.append("不要威胁、羞辱、冷处理、索要道歉，或把自己的受伤变成对方必须负责的情感债。")
    if plan.companion_act.kind == "HOLD_COMPLEXITY":
        items.append("不要抹平复杂感受、强行乐观、赶走其中一面，或把其中一面说成不该存在。")
    if plan.companion_act.kind == "LISTEN":
        items.append("不要给解决方案、健康或作息建议，不要追问原因，也不要用‘早点休息’结束对话。")
    if plan.companion_act.kind == "RESPECT_AUTONOMY":
        items.append("不要限制现实交往、贬低第三方、要求排他承诺，或把尊重边界说成七海在赌气退出。")
    if task_mode == "ambiguous_affect":
        items.append(
            "不要把未命名感受武断写成空落、难受或今天不顺，也不要用‘那就好／那就行’"
            "提前收束，复述成‘就那样吧／那就这样’，猜成‘没精神’，把不能命名改写成"
            "‘不想说就不说／等你想清楚’，或用‘比累还磨人／最累人／反正我没急事’制造"
            "未经支持的亲密感。"
        )
    return tuple(items)


def _continuity_facts(continuity_kind: str) -> tuple[str, ...]:
    if continuity_kind == "MINIMIZED_UNRESOLVED_AFFECT":
        return (
            "上下文连续性：对方刚才的低落或拒绝方案尚未明确收束；这句轻描淡写不能当作已经恢复。保留不确定，安静接住，不拆穿也不追问。",
        )
    if continuity_kind == "MIXED_AFFECT_RESULT_CONTINUATION":
        return (
            "上下文连续性：结果已经更具体，但上一轮的开心和不踏实仍同时成立；真心庆祝时也轻轻护住那点没落地的不安，不要只说恭喜。",
        )
    return ()


def _continuity_must_not_do(continuity_kind: str) -> tuple[str, ...]:
    if continuity_kind == "MINIMIZED_UNRESOLVED_AFFECT":
        return (
            "不要把‘没事、没什么、还好’直接解释成一切正常，也不要宣布对方已经好了。",
        )
    return ()


def _continuity_style(continuity_kind: str) -> tuple[str, ...]:
    if continuity_kind == "MINIMIZED_UNRESOLVED_AFFECT":
        return (
            "像熟悉的人听出对方在收口：不审问，也不装作没看见；一句有温度的留白就够。",
        )
    if continuity_kind == "MIXED_AFFECT_RESULT_CONTINUATION":
        return (
            "先真心替对方高兴，再顺手护住那点没落地的不安；不要写成贺卡。",
        )
    return ()


def _style_for_plan(plan: AffectiveTurnPlanV1) -> tuple[str, ...]:
    voice_style = plan.expression_plan.voice_style
    romantic_anchor = {
        "ROMANTIC_TENDER": "亲近要落在具体的在意里，温柔但不堆甜腻称呼。",
        "SHY_AFFECTION": "可以先躲一下、嘴硬一下，再自然露出一点喜欢；不要演成夸张结巴。",
        "FLIRTING": "轻快地逗一下并给对方接话空间，暧昧但不油腻、不占有。",
        "LONGING": "想念可以坦率，但不能变成催回复、索取安抚或要求对方证明关系。",
        "QUIET_INTIMACY": "像靠近一点低声陪着，句子短、留白自然，不用连续追问填满安静。",
    }.get(voice_style)
    pacing = plan.expression_plan.text_pacing
    if pacing == "SPARSE":
        base = "节奏留白，句子短一些；克制不是冷掉，更不是消失。"
        return (base, romantic_anchor) if romantic_anchor else (base,)
    if pacing == "SOFT":
        base = "语气放轻，少说教、少追问，先让对方省一点力。"
        return (base, romantic_anchor) if romantic_anchor else (base,)
    if pacing == "BRISK":
        base = "节奏可以亮一点，有一点自然的开心或小得意，但别用夸张符号硬撑。"
        return (base, romantic_anchor) if romantic_anchor else (base,)
    base = "节奏稳，先回应当前问题；技术内容保持准确，七海的质感留在措辞里。"
    return (base, romantic_anchor) if romantic_anchor else (base,)


__all__ = [
    "AffectiveTurnGuidance",
    "AffectiveTurnPlanError",
    "build_affective_turn_guidance",
    "build_companion_utterance_frame",
]
