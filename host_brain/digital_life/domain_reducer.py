"""Pure semantic state evolution for the Digital Life aggregate.

This module owns the durable World/Appraisal/Affect/Homeostasis/Relationship/
Scene/Intent projection.  It deliberately has no clock, random, repository,
model, transport or process-global dependency: the command timestamp and
payload are the complete inputs, so journal replay produces byte-identical
state.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from natsume_contracts.life import (
    AffectiveRepairTargetV1,
    CompanionProfileCanaryInteractionV1,
    CompanionProfileStateV1,
    CompanionProfileVersionV1,
    CompanionProactiveTargetV1,
    EmotionComponentV1,
    EmotionEpisodeV1,
    EventAppraisalV1,
    MoodStateV1,
    NeedStateV1,
    RegulationDecisionV1,
    SelfAffectStateV2,
    UserAffectObservationV1,
    RelationshipEpisodeV1,
    RelationshipStateV2,
    RelationalAftereffectV1,
    baseline_companion_profile,
    digest_json,
)
from host_brain.digital_life.companion_proactive import (
    PROACTIVE_SILENCE_GRACE_MS,
    proactive_silence_backoff_ms,
)


_AFFECTIVE_COMMAND_TYPES = frozenset(
    {
        "affective_observation_recorded",
        "affective_event_appraised",
        "affective_time_advanced",
        "affective_episode_resolved",
        "affective_regulation_decided",
    }
)
_RELATIONSHIP_COMMAND_TYPES = frozenset(
    {
        "relationship_response_accepted",
        "relationship_proactive_delivered",
        "relational_aftereffect_recorded",
        "relationship_proactive_permission_changed",
    }
)
_COMPANION_PROFILE_COMMAND_TYPES = frozenset(
    {
        "companion_profile_canary_started",
        "companion_profile_canary_interaction_recorded",
        "companion_profile_activated",
        "companion_profile_rolled_back",
    }
)
_AFFECTIVE_PATCH_PROTECTED_KEYS = frozenset(
    {
        "identity",
        "affective",
        "relationship_v2",
        "companion_proactive_target",
        "companion_profile",
        "companion_proactive",
    }
)
_AFFECTIVE_RAW_MATERIAL_KEYS = frozenset(
    {
        "assistant_reply",
        "audio",
        "image",
        "model_chain",
        "raw_audio",
        "raw_image",
        "raw_text",
        "transcript",
        "user_message",
        "video",
    }
)
_AFFECTIVE_COMPONENT_LIMIT = 8
_AFFECTIVE_EPISODE_LIMIT = 4
_AFFECTIVE_EVIDENCE_LIMIT = 16
_PROACTIVE_COMPANION_BASE_CADENCE_MS = 4 * 60 * 60 * 1_000


def reduce_life_domains(
    before: Mapping[str, Any],
    *,
    person_id: str,
    command_id: str,
    command_type: str,
    payload: Mapping[str, Any],
    created_at_ms: int,
) -> dict[str, Any]:
    """Return the next semantic state without assigning aggregate version."""

    before_clock = _mapping(before.get("clock"))
    before_runtime = _mapping(before.get("runtime"))
    now_ms = max(
        0,
        int(created_at_ms),
        _integer(before_clock.get("last_integrated_ms")),
        _integer(before_runtime.get("last_integrated_ms")),
    )
    event_type = str(command_type or "").strip().lower()
    body = dict(payload or {})
    # A policy compiler sees the persisted snapshot before this reducer seeds
    # missing compatibility defaults.  Keep that exact digest so a legacy
    # sparse snapshot can still be strictly bound to the plan it produced.
    source_affective_digest = digest_json(_mapping(before.get("affective")))
    state = _seed_state(before, person_id=person_id, now_ms=now_ms)
    prior = deepcopy(state)
    raw_patch = body.get("state_patch") or {}
    if not isinstance(raw_patch, Mapping):
        raise ValueError("payload.state_patch must be an object")
    protected = sorted(
        str(key)
        for key in raw_patch
        if str(key) in _AFFECTIVE_PATCH_PROTECTED_KEYS
    )
    if protected:
        raise ValueError(
            "payload.state_patch cannot write canonical affective domains: "
            + ", ".join(protected)
        )
    state = _merge_patch(state, raw_patch)
    relationship_subject_id = _resolve_canonical_relationship_subject(
        state,
        aggregate_person_id=person_id,
        event_type=event_type,
        payload=body,
    )
    state["identity"] = _canonical_identity(relationship_subject_id)
    state["affective"] = _normalize_affective_head(
        state.get("affective"),
        person_id=relationship_subject_id,
        now_ms=now_ms,
    )
    state["relationship_v2"] = _normalize_relationship_head(
        state.get("relationship_v2"),
        relationship_subject_id=relationship_subject_id,
        aggregate_person_id=person_id,
    )
    state["companion_proactive_target"] = _normalize_companion_proactive_target(
        state.get("companion_proactive_target"),
        relationship_state=state["relationship_v2"],
    )
    state["companion_profile"] = _normalize_companion_profile_state(
        state.get("companion_profile"),
        relationship_state=state["relationship_v2"],
    )

    if event_type in {"tick", "advance"}:
        _apply_tick(
            state,
            prior=prior,
            payload=body,
            now_ms=now_ms,
            source_affective_digest=source_affective_digest,
        )
    elif event_type == "foreground_completed":
        _apply_foreground(state, payload=body, now_ms=now_ms)
    elif event_type in {
        "conversation_turn_recorded",
        "experience_ingest",
        "life_experience",
    }:
        _apply_experience(state, payload=body, now_ms=now_ms)
    elif event_type == "work_event":
        _apply_work_event(state, payload=body, now_ms=now_ms)
    elif event_type == "proactive_attempt":
        _apply_proactive_attempt(state, payload=body, now_ms=now_ms)
    elif event_type == "affective_observation_recorded":
        _apply_affective_observation(state, payload=body, now_ms=now_ms)
    elif event_type == "affective_event_appraised":
        _apply_affective_appraisal(state, payload=body, now_ms=now_ms)
    elif event_type == "affective_time_advanced":
        _apply_affective_time_advance(state, now_ms=now_ms)
    elif event_type == "affective_episode_resolved":
        _apply_affective_episode_resolution(state, payload=body, now_ms=now_ms)
    elif event_type == "affective_regulation_decided":
        _apply_affective_regulation(state, payload=body, now_ms=now_ms)
    elif event_type in {
        "relationship_response_accepted",
        "relationship_proactive_delivered",
    }:
        _apply_relationship_response_accepted(state, payload=body, now_ms=now_ms)
    elif event_type == "relational_aftereffect_recorded":
        _apply_relational_aftereffect(state, payload=body, now_ms=now_ms)
    elif event_type == "relationship_proactive_permission_changed":
        _apply_relationship_proactive_permission(state, payload=body, now_ms=now_ms)
    elif event_type == "companion_profile_canary_started":
        _apply_companion_profile_canary_started(state, payload=body)
    elif event_type == "companion_profile_canary_interaction_recorded":
        _apply_companion_profile_canary_interaction(state, payload=body)
    elif event_type == "companion_profile_activated":
        _apply_companion_profile_activation(state, payload=body)
    elif event_type == "companion_profile_rolled_back":
        _apply_companion_profile_rollback(state, payload=body)

    # A first relationship event can bind an aggregate that was seeded with
    # ``workspace::unbound``. Re-normalize after the domain action so its
    # deterministic baseline profile follows that one allowed binding; any
    # learned/canary profile still fails closed on scope drift.
    state["companion_profile"] = _normalize_companion_profile_state(
        state.get("companion_profile"),
        relationship_state=state["relationship_v2"],
    )

    _derive_semantic_projection(
        state,
        command_id=command_id,
        command_type=event_type,
        payload=body,
        now_ms=now_ms,
    )
    return state


def _seed_state(
    before: Mapping[str, Any],
    *,
    person_id: str,
    now_ms: int,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "person_id": person_id,
        # ``person_id`` is the aggregate key for the relationship subject.
        # It is intentionally not interchangeable with Natsume's stable
        # identity.  Workspace/session binding is added by the ingress and
        # fusion waves; the kernel already makes the self/subject split
        # explicit in every durable snapshot.
        "identity": {
            "self_id": "natsume_ai",
            "relationship_subject_id": person_id,
        },
        "runtime": {
            "alive_status": "alive",
            "last_integrated_ms": 0,
            "last_heartbeat_ms": 0,
            "missed_tick_count": 0,
            "catchup_count": 0,
        },
        "clock": {
            "last_tick_ms": 0,
            "last_integrated_ms": 0,
            "last_user_seen_ms": 0,
            "idle_minutes": 0,
            "day_phase": _day_phase(now_ms, "Asia/Shanghai"),
        },
        "environment": {},
        "world": {},
        "appraisal": {
            "event_type": "",
            "salience": 0.0,
            "valence": 0.0,
            "uncertainty": 0.0,
        },
        # User affect is an observation about the relationship subject, never a
        # writable alias for Natsume's own ``affect`` state.  DL-00 keeps only a
        # bounded, privacy-safe compatibility projection here; DL-02 replaces it
        # with the full append-only UserAffectObservation journal.
        "observations": {
            "last_user_affect": {},
            "last_user_affect_at_ms": 0,
        },
        # DL-01's only canonical self-affect head.  The generic Life event
        # journal retains the detailed immutable command/event evidence;
        # this snapshot stays deliberately bounded for deterministic replay
        # and safe prompt consumption.
        "affective": {
            "schema_version": "self_affect_state.v2",
            "self_id": "natsume_ai",
            "relationship_subject_id": person_id,
            "revision": 0,
            "components": [],
            "active_episodes": [],
            "mood": {
                "label": "calm",
                "valence": 0.18,
                "arousal": 0.24,
                "energy": 0.62,
                "updated_at_ms": 0,
            },
            "needs": {
                "connection": 0.12,
                "rest": 0.12,
                "autonomy": 0.25,
                "play": 0.18,
                "repair": 0.0,
                "safety": 0.08,
                "updated_at_ms": 0,
            },
            "last_observation": {},
            "last_appraisal": {},
            "last_regulation": {},
            "last_resolution": {},
            "last_integrated_ms": 0,
        },
        "affect": {
            "mood": "calm",
            "valence": 0.18,
            "arousal": 0.24,
            "energy": 0.62,
            "loneliness": 0.0,
            "attachment_warmth": 0.42,
            "irritation": 0.0,
            "fatigue": 0.12,
            "tension": 0.0,
            "pride": 0.0,
            "relief": 0.0,
        },
        "needs": {
            "contact": 0.12,
            "rest": 0.12,
            "play": 0.18,
            "autonomy": 0.25,
            "memory_consolidation": 0.2,
        },
        "homeostasis": {},
        "relationship": {
            "warmth": 0.42,
            "trust": 0.5,
            "closeness": 0.4,
            "distance": "familiar",
        },
        # DL-03 is the canonical relationship projection.  It is separate
        # from the historical scalar ``relationship`` compatibility view and
        # from all Bond score/stage machinery.
        "relationship_v2": {
            "schema_version": "relationship_state.v2",
            "self_id": "natsume_ai",
            "workspace_id": "workspace::unbound",
            "relationship_subject_id": person_id,
            "revision": 0,
            "trust": 0.5,
            "safety": 0.58,
            "attunement": 0.42,
            "rupture_load": 0.0,
            "repair_required": False,
            "proactive_permission": "UNKNOWN",
            "romantic_consent": "NOT_GRANTED",
            "exclusive_consent": "NOT_GRANTED",
            "dependency_consent": "NOT_GRANTED",
            "active_episodes": [],
            "last_aftereffect": None,
        },
        # A target is registered atomically with an accepted foreground
        # response.  It is neither a relationship score nor a permission to
        # contact; only relationship_v2.proactive_permission can grant that.
        "companion_proactive_target": None,
        # This is a bounded scheduler-contact head, not a relationship score.
        # It records cadence only after the canonical reducer validates a
        # queue decision or a receipt-backed aftereffect.
        "companion_proactive": {
            "schema_version": "companion_proactive_state.v1",
            "last_decision_id": "",
            "last_episode_id": "",
            "last_status": "",
            "last_reason_code": "",
            "last_motive": "",
            "last_content_digest": "",
            "last_plan_id": "",
            "last_synchronization_id": "",
            "last_attempt_ms": 0,
            "next_allowed_at_ms": 0,
            "consecutive_unanswered": 0,
            "updated_at_ms": 0,
        },
        # The profile is only an expression preference head.  It does not
        # replace Soul, relationship consent, self-affect or the user-facing
        # identity.  The deterministic baseline makes first rollback exact
        # without inventing an in-memory default authority.
        "companion_profile": None,
        "scene": {"kind": "idle", "since_ms": now_ms},
        "intent": {"kind": "quiet_presence", "confidence": 0.5},
        "inner_world": {
            "current_preoccupation": "",
            "attention_target": "",
            "unresolved_loop": "",
            "felt_tendency": "",
            "dialogue_bias": "",
            "attention_kind": "idle",
        },
        "life_pressure": {
            "absence": 0.0,
            "dream": 0.0,
            "work": 0.0,
            "fatigue": 0.12,
            "relationship": 0.28,
        },
        "work": {
            "active": False,
            "last_event_type": "",
            "work_id": "",
            "summary": "",
            "urgency": "medium",
            "updated_at_ms": 0,
        },
        "motives": [],
        "continuity": {
            "last_interaction_digest": "",
            "last_visible_event_id": "",
            "last_private_event_id": "",
            "open_loop_count": 0,
        },
        "embodiment": {
            "expression": "calm_idle",
            "idle_animation": "quiet_breath",
            "motion_bias": "low_random",
            "voice_tone": "soft_neutral",
            "mateengine_state": "idle",
            "emotion_tag": "NEUTRAL",
        },
        "life_action": {},
        "proactive": {
            "last_target": {},
            "last_attempt_ms": 0,
            "last_sent_ms": 0,
            "last_status": "",
            "last_reason_code": "",
            "last_motive": "",
        },
        "organ_runtime": {},
        "autonomy": {
            "alive_status": "alive",
            "current_attention": "body_presence",
            "user_input_required_for_progress": False,
        },
        "self_growth": {
            "active_projects": [],
            "adaptive_traits": {},
            "recent_growth_signals": [],
        },
        "learning_state": {
            "recent_clusters": [],
            "learning_signals": [],
            "self_reward": {},
        },
    }
    return _merge_patch(defaults, dict(before or {}))


def _apply_tick(
    state: dict[str, Any],
    *,
    prior: Mapping[str, Any],
    payload: Mapping[str, Any],
    now_ms: int,
    source_affective_digest: str,
) -> None:
    prior_clock = _mapping(prior.get("clock"))
    last_integrated = max(
        0,
        _integer(
            prior_clock.get("last_integrated_ms")
            or _mapping(prior.get("runtime")).get("last_integrated_ms")
        ),
    )
    elapsed_ms = max(0, now_ms - last_integrated) if last_integrated else 0
    scheduled_at_ms = max(
        0,
        _integer(
            payload.get("scheduled_at_ms")
            or _mapping(payload.get("occurrence_payload")).get("scheduled_at_ms")
        ),
    )
    if scheduled_at_ms:
        elapsed_ms = (
            max(0, scheduled_at_ms - last_integrated)
            if last_integrated
            else 0
        )
    elapsed_hours = min(24.0, elapsed_ms / 3_600_000.0)
    clock = _mapping(state.get("clock"))
    last_user_seen = max(0, _integer(clock.get("last_user_seen_ms")))
    idle_ms = max(0, now_ms - last_user_seen) if last_user_seen else 0
    interval_end_ms = scheduled_at_ms or now_ms
    unattended_elapsed_ms = _unattended_elapsed_ms(
        start_ms=last_integrated,
        end_ms=interval_end_ms,
        last_user_seen_ms=last_user_seen,
    )
    unattended_ratio = min(1.0, unattended_elapsed_ms / 86_400_000.0)
    timezone_name = str(
        _mapping(payload.get("occurrence_payload")).get("timezone")
        or payload.get("timezone")
        or "Asia/Shanghai"
    ).strip()
    clock.update(
        {
            "last_tick_ms": now_ms,
            "last_integrated_ms": now_ms,
            "idle_minutes": idle_ms // 60_000,
            "day_phase": _day_phase(now_ms, timezone_name),
        }
    )
    state["clock"] = clock
    runtime = _mapping(state.get("runtime"))
    runtime.update(
        {
            "alive_status": "alive",
            "last_integrated_ms": now_ms,
            "last_heartbeat_ms": now_ms,
            "tick_source": str(payload.get("source") or "life_command"),
        }
    )
    state["runtime"] = runtime
    affect = _mapping(state.get("affect"))
    needs = _mapping(state.get("needs"))
    affect["fatigue"] = _clamp(_float(affect.get("fatigue"), 0.12) + elapsed_hours * 0.018)
    affect["energy"] = _clamp(_float(affect.get("energy"), 0.62) - elapsed_hours * 0.014)
    affect["loneliness"] = _clamp(
        _float(affect.get("loneliness")) + unattended_ratio * 0.18
    )
    affect["arousal"] = _approach(_float(affect.get("arousal"), 0.24), 0.22, min(1.0, elapsed_hours * 0.08))
    affect["tension"] = _approach(_float(affect.get("tension")), 0.08, min(1.0, elapsed_hours * 0.06))
    needs["contact"] = _clamp(
        _float(needs.get("contact"), 0.12) + unattended_ratio * 0.2
    )
    needs["rest"] = _clamp(_float(needs.get("rest"), 0.12) + elapsed_hours * 0.012)
    needs["memory_consolidation"] = _clamp(_float(needs.get("memory_consolidation"), 0.2) + elapsed_hours * 0.006)
    state["affect"] = affect
    state["needs"] = needs
    _apply_companion_proactive_silence_aftereffect(
        state,
        payload=payload,
        now_ms=now_ms,
    )
    _apply_companion_proactive_decision(
        state,
        payload=payload,
        now_ms=now_ms,
        source_affective_digest=source_affective_digest,
    )


def _apply_foreground(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    clock = _mapping(state.get("clock"))
    clock.update({"last_user_seen_ms": now_ms, "idle_minutes": 0})
    state["clock"] = clock
    emotion = _mapping(payload.get("emotion_context"))
    user_affect_observation = _user_affect_observation(emotion, observed_at_ms=now_ms)
    if user_affect_observation:
        observations = _mapping(state.get("observations"))
        observations.update(
            {
                "last_user_affect": user_affect_observation,
                "last_user_affect_at_ms": now_ms,
            }
        )
        state["observations"] = observations

    # Foreground presence can relieve contact pressure, but a user's affect
    # observation must not directly become Natsume's self affect.  A later
    # EventAppraisal command is the sole path that may create a causal
    # self-affect transition.
    affect = _mapping(state.get("affect"))
    affect["loneliness"] = _clamp(_float(affect.get("loneliness")) - 0.16)
    affect["attachment_warmth"] = _clamp(_float(affect.get("attachment_warmth"), 0.42) + 0.025)
    affect["tension"] = _clamp(_float(affect.get("tension")) * 0.82)
    state["affect"] = affect
    needs = _mapping(state.get("needs"))
    needs["contact"] = _clamp(_float(needs.get("contact"), 0.12) * 0.45)
    needs["memory_consolidation"] = _clamp(_float(needs.get("memory_consolidation"), 0.2) + 0.04)
    state["needs"] = needs
    relationship = _mapping(state.get("relationship"))
    bond = _mapping(payload.get("bond_summary"))
    relationship["warmth"] = _approach(
        _float(relationship.get("warmth"), 0.42),
        _first_float(bond, ("warmth", "bond_score", "closeness"), 0.55),
        0.12,
    )
    relationship["trust"] = _approach(
        _float(relationship.get("trust"), 0.5),
        _first_float(bond, ("trust", "trust_score"), 0.55),
        0.08,
    )
    state["relationship"] = relationship
    continuity = _mapping(state.get("continuity"))
    continuity["last_interaction_digest"] = str(
        payload.get("user_message") or payload.get("assistant_reply") or ""
    ).strip()[:160]
    state["continuity"] = continuity


def _apply_experience(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    salience = _clamp(payload.get("salience"), 0.3)
    valence = max(-1.0, min(1.0, _float(payload.get("valence"))))
    uncertainty = _clamp(payload.get("uncertainty"), 0.1)
    evidence = _mapping(payload.get("evidence"))
    event_kind = str(payload.get("event_kind") or "experience").strip().lower()
    appraisal = _mapping(state.get("appraisal"))
    appraisal.update(
        {
            "event_type": event_kind,
            "salience": round(salience, 4),
            "valence": round(valence, 4),
            "uncertainty": round(uncertainty, 4),
            "appraised_at_ms": now_ms,
        }
    )
    state["appraisal"] = appraisal
    affect = _mapping(state.get("affect"))
    weight = 0.08 + salience * 0.24
    affect["valence"] = _approach(_float(affect.get("valence"), 0.18), valence, weight)
    affect["arousal"] = _approach(_float(affect.get("arousal"), 0.24), salience, weight * 0.7)
    affect["tension"] = _clamp(
        _float(affect.get("tension")) * 0.9
        + max(0.0, -valence) * salience * 0.18
        + uncertainty * 0.05
    )
    affect["irritation"] = _clamp(
        _float(affect.get("irritation")) * 0.92
        + (max(0.0, -valence) * 0.1 if salience >= 0.4 else 0.0)
    )
    role = str(evidence.get("role") or "").strip().lower()
    if role == "user" or event_kind in {"user_message", "dialogue_user"}:
        affect["loneliness"] = _clamp(_float(affect.get("loneliness")) - 0.12)
        needs = _mapping(state.get("needs"))
        needs["contact"] = _clamp(_float(needs.get("contact"), 0.12) * 0.55)
        state["needs"] = needs
        clock = _mapping(state.get("clock"))
        clock.update({"last_user_seen_ms": now_ms, "idle_minutes": 0})
        state["clock"] = clock
    state["affect"] = affect
    continuity = _mapping(state.get("continuity"))
    event_ref = str(
        evidence.get("memory_event_id")
        or evidence.get("request_id")
        or evidence.get("signature")
        or ""
    ).strip()
    if role in {"user", "assistant"}:
        continuity["last_visible_event_id"] = event_ref
    else:
        continuity["last_private_event_id"] = event_ref
    continuity["last_experience_kind"] = event_kind
    continuity["last_experience_at_ms"] = now_ms
    state["continuity"] = continuity


def _apply_work_event(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    event_type = str(payload.get("event_type") or "task_progress").strip().lower()
    terminal = event_type in {"task_done", "task_failed", "task_cancelled"}
    work = _mapping(state.get("work"))
    work.update(
        {
            "active": not terminal,
            "last_event_type": event_type,
            "work_id": str(payload.get("work_id") or work.get("work_id") or "")[:240],
            "summary": str(payload.get("summary") or "")[:500],
            "urgency": str(payload.get("urgency") or "medium")[:40],
            "updated_at_ms": now_ms,
        }
    )
    state["work"] = work
    environment = _mapping(state.get("environment"))
    environment["work"] = deepcopy(work)
    environment["last_observed_at_ms"] = now_ms
    state["environment"] = environment
    affect = _mapping(state.get("affect"))
    if event_type == "task_done":
        affect["pride"] = _clamp(_float(affect.get("pride")) + 0.14)
        affect["relief"] = _clamp(_float(affect.get("relief")) + 0.18)
        affect["tension"] = _clamp(_float(affect.get("tension")) * 0.65)
    elif event_type == "task_failed":
        affect["tension"] = _clamp(_float(affect.get("tension")) + 0.2)
        affect["valence"] = max(-1.0, _float(affect.get("valence"), 0.18) - 0.12)
    else:
        urgency = {"low": 0.15, "medium": 0.35, "high": 0.6, "critical": 0.8}.get(
            str(work.get("urgency") or "medium").lower(), 0.35
        )
        affect["tension"] = _clamp(_float(affect.get("tension")) + urgency * 0.08)
    state["affect"] = affect


from host_brain.digital_life.domain_reducer_support import (
    _approach,
    _approach_signed,
    _clamp,
    _first_float,
    _float,
    _integer,
    _mapping,
    _merge_patch,
    _required_text,
)
from host_brain.digital_life.affective_domain_reducer import (
    _advance_affective_needs,
    _appraisal_from_mapping,
    _apply_affective_appraisal,
    _apply_affective_episode_resolution,
    _apply_affective_observation,
    _apply_affective_regulation,
    _apply_affective_time_advance,
    _apply_appraisal_needs,
    _assert_affective_subject,
    _bounded_episode_rows,
    _component_from_mapping,
    _components_from_episode_rows,
    _decay_affective_episodes,
    _episode_from_mapping,
    _episode_peak_intensity,
    _episode_state_from_contract,
    _mood_from_mapping,
    _needs_from_mapping,
    _normalize_affective_head,
    _observation_from_mapping,
    _refresh_affective_head,
    _regulation_from_mapping,
    _regulation_from_mapping_or_none,
    _reject_raw_affective_material,
    _safe_appraisal_head,
    _safe_observation_head,
    _safe_resolution_head,
)
from host_brain.digital_life.relationship_domain_reducer import (
    _apply_companion_profile_activation,
    _apply_companion_profile_canary_interaction,
    _apply_companion_profile_canary_started,
    _apply_companion_profile_rollback,
    _apply_companion_proactive_aftereffect_head,
    _apply_companion_proactive_decision,
    _apply_companion_proactive_silence_aftereffect,
    _apply_proactive_attempt,
    _apply_relational_aftereffect,
    _apply_relationship_proactive_permission,
    _apply_relationship_response_accepted,
    _assert_companion_profile_scope,
    _canonical_identity,
    _companion_profile_from_mapping,
    _companion_profile_state_from_mapping,
    _companion_profile_state_from_state,
    _companion_proactive_target_from_mapping,
    _normalize_companion_profile_state,
    _normalize_companion_proactive_head,
    _normalize_companion_proactive_target,
    _normalize_relationship_head,
    _profile_known_with,
    _record_relationship_user_presence,
    _relational_aftereffect_from_mapping,
    _relationship_episode_from_mapping,
    _relationship_scope_matches,
    _relationship_state_from_state,
    _resolve_canonical_relationship_subject,
)

def _derive_semantic_projection(
    state: dict[str, Any],
    *,
    command_id: str,
    command_type: str,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    runtime = _mapping(state.get("runtime"))
    runtime.update(
        {
            "alive_status": "alive",
            "last_integrated_ms": now_ms,
            "last_heartbeat_ms": now_ms,
        }
    )
    state["runtime"] = runtime
    clock = _mapping(state.get("clock"))
    clock["last_integrated_ms"] = now_ms
    state["clock"] = clock
    affect = _mapping(state.get("affect"))
    needs = _mapping(state.get("needs"))
    relationship = _mapping(state.get("relationship"))
    work = _mapping(state.get("work"))
    pressure = _mapping(state.get("life_pressure"))
    pressure.update(
        {
            "absence": round(max(_float(needs.get("contact")), _float(affect.get("loneliness"))), 4),
            "work": round(0.58 if bool(work.get("active")) else _float(pressure.get("work")) * 0.45, 4),
            "fatigue": round(max(_float(needs.get("rest")), _float(affect.get("fatigue"))), 4),
            "relationship": round(max(_float(relationship.get("warmth")), _float(needs.get("contact"))), 4),
        }
    )
    state["life_pressure"] = pressure
    balance_cost = (
        _float(affect.get("tension"))
        + (1.0 - _float(affect.get("energy"), 0.62))
        + _float(needs.get("rest"))
        + _float(needs.get("contact"))
    ) / 4.0
    homeostasis = {
        "balance": round(_clamp(1.0 - balance_cost), 4),
        "energy_deficit": round(_clamp(1.0 - _float(affect.get("energy"), 0.62)), 4),
        "rest_deficit": round(_clamp(needs.get("rest")), 4),
        "contact_deficit": round(_clamp(needs.get("contact")), 4),
        "tension_load": round(_clamp(affect.get("tension")), 4),
        "updated_at_ms": now_ms,
    }
    state["homeostasis"] = homeostasis
    relationship["closeness"] = round(
        _clamp((_float(relationship.get("warmth"), 0.42) + _float(relationship.get("trust"), 0.5)) / 2.0),
        4,
    )
    relationship["distance"] = (
        "close" if relationship["closeness"] >= 0.72 else "familiar" if relationship["closeness"] >= 0.4 else "guarded"
    )
    relationship["updated_at_ms"] = now_ms
    state["relationship"] = relationship
    scene_kind = _scene_kind(state, command_type=command_type)
    previous_scene = _mapping(state.get("scene"))
    state["scene"] = {
        "kind": scene_kind,
        "since_ms": (
            _integer(previous_scene.get("since_ms"))
            if str(previous_scene.get("kind") or "") == scene_kind
            else now_ms
        ),
        "source_command_id": command_id,
        "source_command_type": command_type,
    }
    intent_kind = _intent_kind(state, command_type=command_type, scene_kind=scene_kind)
    state["intent"] = {
        "kind": intent_kind,
        "confidence": round(_intent_confidence(state, intent_kind), 4),
        "source_command_id": command_id,
        "updated_at_ms": now_ms,
    }
    emotion_tag, embodiment = _embodiment(affect, intent_kind=intent_kind)
    existing_embodiment = _mapping(state.get("embodiment"))
    existing_embodiment.update(embodiment)
    existing_embodiment["emotion_tag"] = emotion_tag
    state["embodiment"] = existing_embodiment
    state["life_action"] = {
        "action_kind": intent_kind,
        "motive": intent_kind,
        "scene": scene_kind,
        "channels": ["status_panel", "dialogue"],
        "embodiment_target": deepcopy(existing_embodiment),
        "generated_at_ms": now_ms,
        "command_id": command_id,
        "source_command_id": command_id,
    }
    state["motives"] = [
        {
            "motive": intent_kind,
            "pressure": round(
                max([_clamp(value) for value in pressure.values()] or [0.0]),
                4,
            ),
            "source_command_id": command_id,
        }
    ]
    state["world"] = {
        "time": {
            "now_ms": now_ms,
            "day_phase": _mapping(state.get("clock")).get("day_phase"),
        },
        "environment": deepcopy(_mapping(state.get("environment"))),
        "work_active": bool(work.get("active")),
        "last_event_type": command_type,
        "source_command_id": command_id,
    }
    state["organ_runtime"] = {
        "organ_count": 7,
        "updated_at_ms": now_ms,
        "top_organs": [
            {"organ_id": "appraisal", "activation": round(_clamp(_mapping(state.get("appraisal")).get("salience")), 4)},
            {"organ_id": "affect", "activation": round(max(_clamp(affect.get("arousal")), _clamp(affect.get("tension"))), 4)},
            {"organ_id": "homeostasis", "activation": round(1.0 - homeostasis["balance"], 4)},
            {"organ_id": "relationship", "activation": round(_clamp(pressure.get("relationship")), 4)},
        ],
        "synthesis": {
            "scene": scene_kind,
            "intent": intent_kind,
            "emotion_tag": emotion_tag,
        },
    }
    appraisal = _mapping(state.get("appraisal"))
    if not str(appraisal.get("event_type") or "").strip():
        appraisal["event_type"] = command_type
    if command_type == "foreground_completed":
        appraisal["salience"] = round(max(_clamp(appraisal.get("salience")), 0.55), 4)
        appraisal["valence"] = round(_float(affect.get("valence"), 0.18), 4)
        appraisal["uncertainty"] = round(_clamp(appraisal.get("uncertainty"), 0.05), 4)
    elif command_type in {"tick", "advance"}:
        appraisal["salience"] = round(_clamp(appraisal.get("salience"), 0.08), 4)
        appraisal["valence"] = round(_float(affect.get("valence"), 0.18), 4)
        appraisal["uncertainty"] = round(_clamp(appraisal.get("uncertainty"), 0.02), 4)
    elif command_type == "work_event":
        appraisal["salience"] = round(max(_clamp(appraisal.get("salience")), 0.45), 4)
        appraisal["valence"] = round(_float(affect.get("valence"), 0.18), 4)
        appraisal["uncertainty"] = round(_clamp(appraisal.get("uncertainty"), 0.08), 4)
    appraisal["source_command_id"] = command_id
    appraisal["appraised_at_ms"] = now_ms
    state["appraisal"] = appraisal


def _scene_kind(state: Mapping[str, Any], *, command_type: str) -> str:
    affect = _mapping(state.get("affect"))
    needs = _mapping(state.get("needs"))
    work = _mapping(state.get("work"))
    if bool(work.get("active")):
        return "work_waiting"
    if _float(affect.get("tension")) >= 0.62 or _float(affect.get("valence")) <= -0.35:
        return "repair"
    if max(_float(affect.get("fatigue")), _float(needs.get("rest"))) >= 0.62:
        return "rest"
    if max(_float(affect.get("loneliness")), _float(needs.get("contact"))) >= 0.52:
        return "reconnect"
    if command_type in {"foreground_completed", "conversation_turn_recorded"}:
        return "dialogue"
    return "idle"


def _intent_kind(state: Mapping[str, Any], *, command_type: str, scene_kind: str) -> str:
    if command_type == "foreground_completed":
        return "respond"
    if scene_kind == "work_waiting":
        return "work_presence"
    if scene_kind == "repair":
        return "careful_repair"
    if scene_kind == "rest":
        return "rest_recovery"
    if scene_kind == "reconnect":
        return "soft_reconnect"
    if command_type in {"conversation_turn_recorded", "experience_ingest"}:
        return "integrate_experience"
    return "quiet_presence"


def _intent_confidence(state: Mapping[str, Any], intent_kind: str) -> float:
    appraisal = _mapping(state.get("appraisal"))
    uncertainty = _clamp(appraisal.get("uncertainty"))
    base = 0.72 if intent_kind in {"respond", "work_presence"} else 0.62
    return _clamp(base - uncertainty * 0.25)


def _embodiment(affect: Mapping[str, Any], *, intent_kind: str) -> tuple[str, dict[str, Any]]:
    valence = _float(affect.get("valence"), 0.18)
    tension = _clamp(affect.get("tension"))
    fatigue = _clamp(affect.get("fatigue"))
    if fatigue >= 0.68:
        return "TIRED", {"expression": "tired_soft", "voice_tone": "low_soft", "mateengine_state": "tired"}
    if tension >= 0.65:
        return "TENSE", {"expression": "concerned", "voice_tone": "careful", "mateengine_state": "tense"}
    if valence >= 0.35:
        return "HAPPY", {"expression": "soft_smile", "voice_tone": "warm", "mateengine_state": "happy"}
    if valence <= -0.3:
        return "SAD", {"expression": "subdued", "voice_tone": "quiet", "mateengine_state": "sad"}
    if intent_kind == "soft_reconnect":
        return "WARM", {"expression": "gentle", "voice_tone": "warm_soft", "mateengine_state": "warm"}
    return "NEUTRAL", {"expression": "calm_idle", "voice_tone": "soft_neutral", "mateengine_state": "idle"}


def _unattended_elapsed_ms(
    *,
    start_ms: int,
    end_ms: int,
    last_user_seen_ms: int,
) -> int:
    """Return only the unattended portion of a reducer integration interval."""

    start = max(0, int(start_ms))
    end = max(start, int(end_ms))
    if not start or end <= start:
        return 0
    if last_user_seen_ms <= start:
        return end - start
    if last_user_seen_ms >= end:
        return 0
    return end - int(last_user_seen_ms)


def _user_affect_observation(
    emotion: Mapping[str, Any],
    *,
    observed_at_ms: int,
) -> dict[str, Any]:
    """Create the bounded compatibility projection for an observed user state.

    This function intentionally copies no message text, raw audio, image data,
    inner monologue or profile content.  It is an observation with uncertainty,
    not a claim about Natsume's own emotion.
    """

    if not emotion:
        return {}
    vector = _mapping(emotion.get("vad_vector"))
    tag = str(emotion.get("emotion_tag") or "NEUTRAL").strip().upper()[:48]
    confidence = _clamp(
        emotion.get("emotion_inference_confidence", emotion.get("confidence", 0.0))
    )
    return {
        "version": "user_affect_observation.v0",
        "emotion_tag": tag or "NEUTRAL",
        "emotion_intensity": round(_clamp(emotion.get("emotion_intensity"), 0.0), 4),
        "valence": round(
            max(
                -1.0,
                min(
                    1.0,
                    _float(
                        vector.get(
                            "valence",
                            emotion.get("emotion_valence", emotion.get("valence")),
                        )
                    ),
                ),
            ),
            4,
        ),
        "arousal": round(
            _clamp(vector.get("arousal", emotion.get("emotion_arousal", emotion.get("arousal", 0.35))), 0.35),
            4,
        ),
        "confidence": round(confidence, 4),
        "source": str(emotion.get("emotion_inference_source") or "compatibility_projection")[:120],
        "observed_at_ms": max(0, int(observed_at_ms)),
    }


def _day_phase(now_ms: int, timezone_name: str) -> str:
    try:
        zone = ZoneInfo(str(timezone_name or "UTC"))
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    hour = datetime.fromtimestamp(max(0, now_ms) / 1000.0, tz=timezone.utc).astimezone(zone).hour
    if 5 <= hour < 10:
        return "morning"
    if 10 <= hour < 17:
        return "day"
    if 17 <= hour < 22:
        return "evening"
    return "night"
