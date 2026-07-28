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
_AFFECTIVE_PATCH_PROTECTED_KEYS = frozenset(
    {"identity", "affective", "relationship_v2", "companion_proactive_target"}
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

    if event_type in {"tick", "advance"}:
        _apply_tick(state, prior=prior, payload=body, now_ms=now_ms)
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


def _apply_proactive_attempt(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    proactive = _mapping(state.get("proactive"))
    has_delivery = bool(list(payload.get("deliveries") or []))
    proactive.update(
        {
            "last_attempt_ms": now_ms,
            "last_status": "QUEUED" if has_delivery else "SKIPPED",
            "last_reason_code": "LIFE_DELIVERY_OUTBOX" if has_delivery else "NO_DELIVERY_INTENT",
            "last_motive": str(payload.get("motive") or "manual_proactive")[:120],
        }
    )
    state["proactive"] = proactive


def _canonical_identity(person_id: str) -> dict[str, str]:
    """Return the non-negotiable self/relationship-subject separation."""

    return {
        "self_id": "natsume_ai",
        "relationship_subject_id": str(person_id),
    }


def _resolve_canonical_relationship_subject(
    state: Mapping[str, Any],
    *,
    aggregate_person_id: str,
    event_type: str,
    payload: Mapping[str, Any],
) -> str:
    """Keep the Life aggregate key separate from the relationship subject.

    DL-02 scopes affect aggregates by workspace/session and DL-03 scopes
    relationship aggregates by workspace. The aggregate key is opaque, while
    canonical affect and relationship contracts retain the actual subject.
    Existing non-canonical Life commands keep their historical identity.
    """

    identity = _mapping(state.get("identity"))
    existing = str(identity.get("relationship_subject_id") or "").strip()
    candidate = ""
    if event_type == "affective_observation_recorded":
        candidate = str(
            _mapping(payload.get("observation")).get(
                "relationship_subject_id"
            )
            or ""
        ).strip()
    elif event_type == "affective_event_appraised":
        candidate = str(
            _mapping(payload.get("appraisal")).get(
                "relationship_subject_id"
            )
            or ""
        ).strip()
    elif event_type == "affective_regulation_decided":
        candidate = str(
            _mapping(payload.get("regulation")).get(
                "relationship_subject_id"
            )
            or ""
        ).strip()
    elif event_type in {
        "relationship_response_accepted",
        "relationship_proactive_delivered",
    }:
        candidate = str(
            _mapping(payload.get("episode")).get(
                "relationship_subject_id"
            )
            or ""
        ).strip()
    elif event_type == "relational_aftereffect_recorded":
        candidate = str(
            _mapping(payload.get("aftereffect")).get(
                "relationship_subject_id"
            )
            or ""
        ).strip()
    elif event_type == "relationship_proactive_permission_changed":
        candidate = str(payload.get("relationship_subject_id") or "").strip()

    if not candidate:
        return existing or str(aggregate_person_id)
    # An untouched seed uses the aggregate key as its placeholder.  Replacing
    # that placeholder is reducer-owned and deterministic; state_patch still
    # cannot write either identity or affective domains.
    if existing and existing not in {str(aggregate_person_id), candidate}:
        raise ValueError("affective relationship subject drift")
    return candidate


def _relationship_episode_from_mapping(raw: Mapping[str, Any]) -> RelationshipEpisodeV1:
    if not raw:
        raise ValueError("relationship episode is required")
    return RelationshipEpisodeV1(
        episode_id=raw.get("episode_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        source_kind=raw.get("source_kind"),
        response_receipt_ref=raw.get("response_receipt_ref"),
        response_digest=raw.get("response_digest"),
        opened_at_ms=raw.get("opened_at_ms"),
        evidence_refs=raw.get("evidence_refs") or (),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _companion_proactive_target_from_mapping(
    raw: Mapping[str, Any],
) -> CompanionProactiveTargetV1:
    if not raw:
        raise ValueError("companion proactive target is required")
    return CompanionProactiveTargetV1(
        target_id=raw.get("target_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        platform=raw.get("platform"),
        conversation_id=raw.get("conversation_id"),
        source_receipt_ref=raw.get("source_receipt_ref"),
        registered_at_ms=raw.get("registered_at_ms"),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _normalize_companion_proactive_target(
    raw: Any,
    *,
    relationship_state: Mapping[str, Any],
) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("companion proactive target must be an object")
    target = _companion_proactive_target_from_mapping(raw)
    relationship = _mapping(relationship_state)
    if (
        target.workspace_id != str(relationship.get("workspace_id") or "")
        or target.relationship_subject_id
        != str(relationship.get("relationship_subject_id") or "")
    ):
        raise ValueError("companion proactive target scope drift")
    return target.as_dict()


def _relational_aftereffect_from_mapping(
    raw: Mapping[str, Any],
) -> RelationalAftereffectV1:
    if not raw:
        raise ValueError("relational aftereffect is required")
    return RelationalAftereffectV1(
        aftereffect_id=raw.get("aftereffect_id"),
        episode_id=raw.get("episode_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        source_receipt_ref=raw.get("source_receipt_ref"),
        user_input_ref=raw.get("user_input_ref"),
        outcome=raw.get("outcome"),
        helpfulness=raw.get("helpfulness"),
        boundary_respected=raw.get("boundary_respected"),
        repair_acknowledged=raw.get("repair_acknowledged"),
        observed_at_ms=raw.get("observed_at_ms"),
        evidence_refs=raw.get("evidence_refs") or (),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _normalize_relationship_head(
    raw: Any,
    *,
    relationship_subject_id: str,
    aggregate_person_id: str,
) -> dict[str, Any]:
    source = _mapping(raw)
    workspace_id = str(source.get("workspace_id") or "workspace::unbound").strip()
    if not workspace_id:
        workspace_id = "workspace::unbound"
    subject = str(source.get("relationship_subject_id") or "").strip()
    # A just-seeded aggregate initially uses its opaque aggregate id as a
    # placeholder.  The first typed relationship command is allowed to bind
    # the real user subject exactly once; an existing non-empty head may never
    # silently switch subjects.
    if (
        not subject
        or (
            subject == str(aggregate_person_id)
            and _integer(source.get("revision")) == 0
            and not list(source.get("active_episodes") or [])
            and source.get("last_aftereffect") is None
        )
    ):
        subject = str(relationship_subject_id)
    if subject != str(relationship_subject_id):
        raise ValueError("relationship_v2 relationship subject drift")
    raw_episodes = source.get("active_episodes") or []
    if not isinstance(raw_episodes, list):
        raise ValueError("relationship_v2.active_episodes must be a list")
    episodes = tuple(
        _relationship_episode_from_mapping(_mapping(item))
        for item in raw_episodes
        if isinstance(item, Mapping)
    )
    if len(episodes) != len(raw_episodes):
        raise ValueError("relationship_v2.active_episodes must contain objects")
    last_aftereffect_raw = source.get("last_aftereffect")
    last_aftereffect = (
        _relational_aftereffect_from_mapping(_mapping(last_aftereffect_raw))
        if isinstance(last_aftereffect_raw, Mapping)
        else None
    )
    return RelationshipStateV2(
        workspace_id=workspace_id,
        relationship_subject_id=subject,
        revision=max(0, _integer(source.get("revision"))),
        trust=_clamp(source.get("trust"), 0.5),
        safety=_clamp(source.get("safety"), 0.58),
        attunement=_clamp(source.get("attunement"), 0.42),
        rupture_load=_clamp(source.get("rupture_load"), 0.0),
        repair_required=bool(source.get("repair_required", False)),
        proactive_permission=str(source.get("proactive_permission") or "UNKNOWN"),
        romantic_consent=str(source.get("romantic_consent") or "NOT_GRANTED"),
        exclusive_consent=str(source.get("exclusive_consent") or "NOT_GRANTED"),
        dependency_consent=str(source.get("dependency_consent") or "NOT_GRANTED"),
        active_episodes=episodes,
        last_aftereffect=last_aftereffect,
    ).as_dict()


def _relationship_state_from_state(state: Mapping[str, Any]) -> RelationshipStateV2:
    raw = _mapping(state.get("relationship_v2"))
    return RelationshipStateV2(
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        revision=raw.get("revision"),
        trust=raw.get("trust"),
        safety=raw.get("safety"),
        attunement=raw.get("attunement"),
        rupture_load=raw.get("rupture_load"),
        repair_required=raw.get("repair_required"),
        proactive_permission=raw.get("proactive_permission"),
        romantic_consent=raw.get("romantic_consent"),
        exclusive_consent=raw.get("exclusive_consent"),
        dependency_consent=raw.get("dependency_consent"),
        active_episodes=tuple(
            _relationship_episode_from_mapping(_mapping(item))
            for item in list(raw.get("active_episodes") or [])
        ),
        last_aftereffect=(
            _relational_aftereffect_from_mapping(_mapping(raw.get("last_aftereffect")))
            if isinstance(raw.get("last_aftereffect"), Mapping)
            else None
        ),
    )


def _relationship_scope_matches(
    *,
    state: RelationshipStateV2,
    workspace_id: str,
    relationship_subject_id: str,
) -> RelationshipStateV2:
    if state.relationship_subject_id != relationship_subject_id:
        raise ValueError("relationship subject drift")
    if state.workspace_id == "workspace::unbound":
        return RelationshipStateV2(
            workspace_id=workspace_id,
            relationship_subject_id=state.relationship_subject_id,
            revision=state.revision,
            trust=state.trust,
            safety=state.safety,
            attunement=state.attunement,
            rupture_load=state.rupture_load,
            repair_required=state.repair_required,
            proactive_permission=state.proactive_permission,
            romantic_consent=state.romantic_consent,
            exclusive_consent=state.exclusive_consent,
            dependency_consent=state.dependency_consent,
            active_episodes=state.active_episodes,
            last_aftereffect=state.last_aftereffect,
        )
    if state.workspace_id != workspace_id:
        raise ValueError("relationship workspace drift")
    return state


def _apply_relationship_response_accepted(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    _reject_raw_affective_material(payload)
    episode = _relationship_episode_from_mapping(_mapping(payload.get("episode")))
    raw_target = payload.get("companion_proactive_target")
    target = (
        _companion_proactive_target_from_mapping(_mapping(raw_target))
        if raw_target is not None
        else None
    )
    if episode.opened_at_ms > now_ms:
        raise ValueError("relationship episode cannot open after command time")
    if target is not None and (
        target.workspace_id != episode.workspace_id
        or target.relationship_subject_id != episode.relationship_subject_id
        or target.session_id != episode.session_id
        or target.source_receipt_ref != episode.response_receipt_ref
        or target.registered_at_ms != episode.opened_at_ms
    ):
        raise ValueError("companion proactive target response receipt drift")
    current = _relationship_scope_matches(
        state=_relationship_state_from_state(state),
        workspace_id=episode.workspace_id,
        relationship_subject_id=episode.relationship_subject_id,
    )
    if any(item.episode_id == episode.episode_id for item in current.active_episodes):
        raise ValueError("relationship episode already active")
    episodes = (*current.active_episodes, episode)
    if len(episodes) > 4:
        raise ValueError("relationship active episode limit exceeded")
    state["relationship_v2"] = RelationshipStateV2(
        workspace_id=current.workspace_id,
        relationship_subject_id=current.relationship_subject_id,
        revision=current.revision + 1,
        trust=current.trust,
        safety=current.safety,
        attunement=current.attunement,
        rupture_load=current.rupture_load,
        repair_required=current.repair_required,
        proactive_permission=current.proactive_permission,
        romantic_consent=current.romantic_consent,
        exclusive_consent=current.exclusive_consent,
        dependency_consent=current.dependency_consent,
        active_episodes=episodes,
        last_aftereffect=current.last_aftereffect,
    ).as_dict()
    if target is not None:
        state["companion_proactive_target"] = target.as_dict()
    if episode.source_kind == "CHAT_RESPONSE":
        _record_relationship_user_presence(state, observed_at_ms=episode.opened_at_ms)


def _apply_relational_aftereffect(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    _reject_raw_affective_material(payload)
    aftereffect = _relational_aftereffect_from_mapping(
        _mapping(payload.get("aftereffect"))
    )
    if aftereffect.observed_at_ms > now_ms:
        raise ValueError("relational aftereffect cannot be after command time")
    current = _relationship_scope_matches(
        state=_relationship_state_from_state(state),
        workspace_id=aftereffect.workspace_id,
        relationship_subject_id=aftereffect.relationship_subject_id,
    )
    episode = next(
        (item for item in current.active_episodes if item.episode_id == aftereffect.episode_id),
        None,
    )
    if episode is None:
        raise ValueError("relational aftereffect requires an active episode")
    if episode.response_receipt_ref != aftereffect.source_receipt_ref:
        raise ValueError("relational aftereffect response receipt drift")

    trust = current.trust
    safety = current.safety
    attunement = current.attunement
    rupture = current.rupture_load
    repair_required = current.repair_required
    permission = current.proactive_permission
    outcome = aftereffect.outcome
    if outcome == "HELPFUL":
        trust = _clamp(trust + 0.012 * aftereffect.helpfulness)
        safety = _clamp(safety + 0.009 * aftereffect.helpfulness)
        attunement = _clamp(attunement + 0.02 * aftereffect.helpfulness)
    elif outcome == "MISUNDERSTOOD":
        trust = _clamp(trust - 0.035)
        safety = _clamp(safety - 0.05)
        rupture = _clamp(rupture + 0.11)
        repair_required = True
    elif outcome == "BOUNDARY":
        trust = _clamp(trust - 0.06)
        safety = _clamp(safety - 0.11)
        rupture = _clamp(rupture + 0.2)
        repair_required = True
        permission = "DENIED"
    elif outcome == "REPAIR_UNRESOLVED":
        safety = _clamp(safety - 0.025)
        rupture = _clamp(rupture + 0.05)
        repair_required = True
    elif outcome == "REPAIR_ACCEPTED":
        if not current.repair_required or not aftereffect.repair_acknowledged:
            raise ValueError("repair acceptance requires a pending rupture and user acknowledgement")
        safety = _clamp(safety + 0.035)
        attunement = _clamp(attunement + 0.015)
        rupture = _clamp(rupture - 0.18)
        repair_required = rupture > 0.05
    # NEUTRAL, SILENCE and DELIVERY_FAILED intentionally do not alter trust,
    # safety, permission or consent. Lack of interaction is not rejection.
    remaining = tuple(
        item for item in current.active_episodes if item.episode_id != episode.episode_id
    )
    state["relationship_v2"] = RelationshipStateV2(
        workspace_id=current.workspace_id,
        relationship_subject_id=current.relationship_subject_id,
        revision=current.revision + 1,
        trust=trust,
        safety=safety,
        attunement=attunement,
        rupture_load=rupture,
        repair_required=repair_required,
        proactive_permission=permission,
        romantic_consent=current.romantic_consent,
        exclusive_consent=current.exclusive_consent,
        dependency_consent=current.dependency_consent,
        active_episodes=remaining,
        last_aftereffect=aftereffect,
    ).as_dict()
    _record_relationship_user_presence(
        state,
        observed_at_ms=aftereffect.observed_at_ms,
    )


def _apply_relationship_proactive_permission(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    _reject_raw_affective_material(payload)
    workspace_id = _required_text(
        payload.get("workspace_id"), "relationship permission workspace_id", limit=240
    )
    subject_id = _required_text(
        payload.get("relationship_subject_id"), "relationship permission relationship_subject_id", limit=240
    )
    permission = _required_text(
        payload.get("proactive_permission"), "relationship proactive_permission", limit=64
    ).upper()
    source_ref = _required_text(
        payload.get("source_ref"), "relationship permission source_ref", limit=512
    )
    if permission not in {"ALLOWED", "DENIED"}:
        raise ValueError("relationship proactive permission must be ALLOWED or DENIED")
    source_kind = _required_text(
        payload.get("source_kind"), "relationship permission source_kind", limit=64
    ).upper()
    if source_kind != "USER_EXPLICIT":
        raise ValueError("relationship proactive permission requires USER_EXPLICIT evidence")
    current = _relationship_scope_matches(
        state=_relationship_state_from_state(state),
        workspace_id=workspace_id,
        relationship_subject_id=subject_id,
    )
    state["relationship_v2"] = RelationshipStateV2(
        workspace_id=current.workspace_id,
        relationship_subject_id=current.relationship_subject_id,
        revision=current.revision + 1,
        trust=current.trust,
        safety=current.safety,
        attunement=current.attunement,
        rupture_load=current.rupture_load,
        repair_required=current.repair_required,
        proactive_permission=permission,
        romantic_consent=current.romantic_consent,
        exclusive_consent=current.exclusive_consent,
        dependency_consent=current.dependency_consent,
        active_episodes=current.active_episodes,
        last_aftereffect=current.last_aftereffect,
    ).as_dict()
    _record_relationship_user_presence(state, observed_at_ms=now_ms)


def _record_relationship_user_presence(
    state: dict[str, Any],
    *,
    observed_at_ms: int,
) -> None:
    """Advance the relationship aggregate clock only from typed user events."""

    clock = _mapping(state.get("clock"))
    timestamp = max(0, int(observed_at_ms))
    clock["last_user_seen_ms"] = max(
        _integer(clock.get("last_user_seen_ms")),
        timestamp,
    )
    clock["idle_minutes"] = 0
    state["clock"] = clock


def _apply_affective_observation(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    _reject_raw_affective_material(payload)
    observation = _observation_from_mapping(_mapping(payload.get("observation")))
    _assert_affective_subject(
        observation.relationship_subject_id,
        state=state,
        field_name="observation.relationship_subject_id",
    )
    if observation.observed_at_ms > now_ms:
        raise ValueError("observation.observed_at_ms cannot be after command time")

    # The observation has its own bounded head. It does not change
    # affective components, mood, needs or the self-affect revision.
    # Only a later appraised event command may cross that boundary.
    observation_head = observation.as_dict()
    observations = _mapping(state.get("observations"))
    observations.update(
        {
            "last_user_affect": observation_head,
            "last_user_affect_at_ms": observation.observed_at_ms,
        }
    )
    state["observations"] = observations
    affective = _mapping(state.get("affective"))
    affective["last_observation"] = observation_head
    state["affective"] = affective


def _apply_affective_appraisal(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    _reject_raw_affective_material(payload)
    appraisal = _appraisal_from_mapping(_mapping(payload.get("appraisal")))
    episode = _episode_from_mapping(_mapping(payload.get("episode")))
    _assert_affective_subject(
        appraisal.relationship_subject_id,
        state=state,
        field_name="appraisal.relationship_subject_id",
    )
    _assert_affective_subject(
        episode.relationship_subject_id,
        state=state,
        field_name="episode.relationship_subject_id",
    )
    if episode.appraisal_id != appraisal.appraisal_id:
        raise ValueError("episode.appraisal_id must match appraisal.appraisal_id")
    if episode.opened_at_ms > now_ms:
        raise ValueError("episode.opened_at_ms cannot be after command time")
    if not episode.unresolved:
        raise ValueError("a newly appraised episode must be unresolved")

    affective = _mapping(state.get("affective"))
    _decay_affective_episodes(affective, now_ms=now_ms)
    active_rows = list(affective.get("active_episodes") or [])
    if any(str(row.get("episode_id") or "") == episode.episode_id for row in active_rows):
        raise ValueError("episode_id is already active")
    active_rows.append(_episode_state_from_contract(episode, now_ms=now_ms))
    affective["active_episodes"] = _bounded_episode_rows(active_rows)
    affective["last_appraisal"] = appraisal.as_dict()
    affective["last_integrated_ms"] = now_ms
    affective["revision"] = _integer(affective.get("revision")) + 1
    _apply_appraisal_needs(affective, appraisal=appraisal, now_ms=now_ms)
    _refresh_affective_head(affective, now_ms=now_ms, mood_weight=0.34)
    state["affective"] = affective

    # This is a read-compatible legacy appraisal projection, not a second
    # self-affect writer. The canonical mixed components remain exclusively
    # under state.affective.
    legacy_appraisal = _mapping(state.get("appraisal"))
    legacy_appraisal.update(
        {
            "event_type": appraisal.event_kind,
            "salience": round(
                max(
                    appraisal.relationship_significance,
                    appraisal.boundary_relevance,
                    appraisal.risk,
                ),
                4,
            ),
            "valence": appraisal.goal_congruence,
            "uncertainty": appraisal.uncertainty,
            "appraisal_id": appraisal.appraisal_id,
            "appraised_at_ms": now_ms,
        }
    )
    state["appraisal"] = legacy_appraisal


def _apply_affective_time_advance(
    state: dict[str, Any],
    *,
    now_ms: int,
) -> None:
    affective = _mapping(state.get("affective"))
    changed = _decay_affective_episodes(affective, now_ms=now_ms)
    previous_integrated = _integer(affective.get("last_integrated_ms"))
    elapsed_ms = max(0, now_ms - previous_integrated) if previous_integrated else 0
    if elapsed_ms:
        affective["last_integrated_ms"] = now_ms
        _advance_affective_needs(affective, elapsed_ms=elapsed_ms, now_ms=now_ms)
        _refresh_affective_head(
            affective,
            now_ms=now_ms,
            mood_weight=min(0.45, elapsed_ms / 7_200_000.0),
        )
        changed = True
    if changed:
        affective["revision"] = _integer(affective.get("revision")) + 1
    state["affective"] = affective


def _apply_affective_episode_resolution(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    _reject_raw_affective_material(payload)
    episode_id = _required_text(payload.get("episode_id"), "episode_id", limit=240)
    resolution_ref = _required_text(
        payload.get("resolution_ref"),
        "resolution_ref",
        limit=512,
    )
    affective = _mapping(state.get("affective"))
    _decay_affective_episodes(affective, now_ms=now_ms)
    active_rows = list(affective.get("active_episodes") or [])
    remaining = [
        row
        for row in active_rows
        if str(row.get("episode_id") or "") != episode_id
    ]
    if len(remaining) == len(active_rows):
        raise ValueError("episode_id is not active")
    affective["active_episodes"] = remaining
    affective["last_resolution"] = {
        "episode_id": episode_id,
        "resolution_ref": resolution_ref,
        "resolved_at_ms": now_ms,
    }
    affective["last_integrated_ms"] = now_ms
    affective["revision"] = _integer(affective.get("revision")) + 1
    _refresh_affective_head(affective, now_ms=now_ms, mood_weight=0.28)
    state["affective"] = affective


def _apply_affective_regulation(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    _reject_raw_affective_material(payload)
    decision = _regulation_from_mapping(_mapping(payload.get("regulation")))
    _assert_affective_subject(
        decision.relationship_subject_id,
        state=state,
        field_name="regulation.relationship_subject_id",
    )
    if decision.decided_at_ms > now_ms:
        raise ValueError("regulation.decided_at_ms cannot be after command time")
    affective = _mapping(state.get("affective"))
    active_ids = {
        str(row.get("episode_id") or "")
        for row in list(affective.get("active_episodes") or [])
    }
    if decision.episode_id is not None and decision.episode_id not in active_ids:
        raise ValueError("regulation.episode_id must reference an active episode")
    affective["last_regulation"] = decision.as_dict()
    affective["last_integrated_ms"] = now_ms
    affective["revision"] = _integer(affective.get("revision")) + 1
    state["affective"] = affective


def _normalize_affective_head(
    raw: Any,
    *,
    person_id: str,
    now_ms: int,
) -> dict[str, Any]:
    source = _mapping(raw)
    default_mood = MoodStateV1(
        label="calm",
        valence=0.18,
        arousal=0.24,
        energy=0.62,
        updated_at_ms=0,
    )
    default_needs = NeedStateV1(
        connection=0.12,
        rest=0.12,
        autonomy=0.25,
        play=0.18,
        repair=0.0,
        safety=0.08,
        updated_at_ms=0,
    )
    mood = _mood_from_mapping(_mapping(source.get("mood")), default=default_mood)
    needs = _needs_from_mapping(_mapping(source.get("needs")), default=default_needs)
    active_rows: list[dict[str, Any]] = []
    raw_episodes = source.get("active_episodes") or []
    if not isinstance(raw_episodes, list):
        raise ValueError("affective.active_episodes must be a list")
    for raw_episode in raw_episodes:
        if not isinstance(raw_episode, Mapping):
            raise ValueError("affective.active_episodes must contain objects")
        episode = _episode_from_mapping(_mapping(raw_episode))
        if episode.relationship_subject_id != str(person_id):
            raise ValueError("affective episode relationship subject drift")
        if episode.unresolved:
            active_rows.append(
                _episode_state_from_contract(
                    episode,
                    now_ms=max(
                        episode.opened_at_ms,
                        _integer(_mapping(raw_episode).get("last_decay_at_ms")),
                    ),
                )
            )
    active_rows = _bounded_episode_rows(active_rows)
    last_regulation = _regulation_from_mapping_or_none(
        _mapping(source.get("last_regulation"))
    )
    if (
        last_regulation is not None
        and last_regulation.relationship_subject_id != str(person_id)
    ):
        raise ValueError("affective regulation relationship subject drift")
    components = _components_from_episode_rows(active_rows)
    validated = SelfAffectStateV2(
        relationship_subject_id=str(person_id),
        revision=max(0, _integer(source.get("revision"))),
        components=tuple(_component_from_mapping(row) for row in components),
        active_episodes=tuple(
            _episode_from_mapping(row) for row in active_rows
        ),
        mood=mood,
        needs=needs,
        last_integrated_ms=max(0, _integer(source.get("last_integrated_ms"))),
        last_regulation=last_regulation,
    )
    normalized = validated.as_dict()
    normalized["active_episodes"] = active_rows
    normalized["last_observation"] = _safe_observation_head(
        _mapping(source.get("last_observation"))
    )
    normalized["last_appraisal"] = _safe_appraisal_head(
        _mapping(source.get("last_appraisal"))
    )
    normalized["last_resolution"] = _safe_resolution_head(
        _mapping(source.get("last_resolution"))
    )
    # now_ms is intentionally not written into a fresh empty head. That
    # preserves the fact that no affective event has yet been integrated.
    return normalized


def _observation_from_mapping(raw: Mapping[str, Any]) -> UserAffectObservationV1:
    if not raw:
        raise ValueError("observation is required")
    return UserAffectObservationV1(
        observation_id=raw.get("observation_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        modality=raw.get("modality"),
        emotion_distribution=_mapping(raw.get("emotion_distribution")),
        valence=raw.get("valence"),
        arousal=raw.get("arousal"),
        confidence=raw.get("confidence"),
        observed_at_ms=raw.get("observed_at_ms"),
        expires_at_ms=raw.get("expires_at_ms"),
        evidence_refs=raw.get("evidence_refs") or (),
        conflict_refs=raw.get("conflict_refs") or (),
    )


def _appraisal_from_mapping(raw: Mapping[str, Any]) -> EventAppraisalV1:
    if not raw:
        raise ValueError("appraisal is required")
    return EventAppraisalV1(
        appraisal_id=raw.get("appraisal_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        event_kind=raw.get("event_kind"),
        target_ref=raw.get("target_ref"),
        goal_congruence=raw.get("goal_congruence"),
        responsibility=raw.get("responsibility"),
        controllability=raw.get("controllability"),
        relationship_significance=raw.get("relationship_significance"),
        boundary_relevance=raw.get("boundary_relevance"),
        risk=raw.get("risk"),
        uncertainty=raw.get("uncertainty"),
        evidence_refs=raw.get("evidence_refs") or (),
        observation_refs=raw.get("observation_refs") or (),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _component_from_mapping(raw: Mapping[str, Any]) -> EmotionComponentV1:
    return EmotionComponentV1(
        emotion=raw.get("emotion"),
        intensity=raw.get("intensity"),
        valence=raw.get("valence"),
        arousal=raw.get("arousal"),
        action_tendency=raw.get("action_tendency") or "observe",
    )


def _episode_from_mapping(raw: Mapping[str, Any]) -> EmotionEpisodeV1:
    if not raw:
        raise ValueError("episode is required")
    components = raw.get("components")
    if not isinstance(components, list):
        raise ValueError("episode.components must be a list")
    return EmotionEpisodeV1(
        episode_id=raw.get("episode_id"),
        appraisal_id=raw.get("appraisal_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        trigger_ref=raw.get("trigger_ref"),
        components=tuple(
            _component_from_mapping(_mapping(component))
            for component in components
        ),
        opened_at_ms=raw.get("opened_at_ms"),
        decay_half_life_ms=raw.get("decay_half_life_ms"),
        evidence_refs=raw.get("evidence_refs") or (),
        unresolved=bool(raw.get("unresolved", True)),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _regulation_from_mapping(raw: Mapping[str, Any]) -> RegulationDecisionV1:
    if not raw:
        raise ValueError("regulation is required")
    return RegulationDecisionV1(
        regulation_id=raw.get("regulation_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        mode=raw.get("mode"),
        reason_refs=raw.get("reason_refs") or (),
        decided_at_ms=raw.get("decided_at_ms"),
        episode_id=raw.get("episode_id"),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _regulation_from_mapping_or_none(
    raw: Mapping[str, Any],
) -> RegulationDecisionV1 | None:
    return None if not raw else _regulation_from_mapping(raw)


def _mood_from_mapping(
    raw: Mapping[str, Any],
    *,
    default: MoodStateV1,
) -> MoodStateV1:
    if not raw:
        return default
    return MoodStateV1(
        label=raw.get("label") or default.label,
        valence=raw.get("valence", default.valence),
        arousal=raw.get("arousal", default.arousal),
        energy=raw.get("energy", default.energy),
        updated_at_ms=raw.get("updated_at_ms", default.updated_at_ms),
    )


def _needs_from_mapping(
    raw: Mapping[str, Any],
    *,
    default: NeedStateV1,
) -> NeedStateV1:
    if not raw:
        return default
    return NeedStateV1(
        connection=raw.get("connection", default.connection),
        rest=raw.get("rest", default.rest),
        autonomy=raw.get("autonomy", default.autonomy),
        play=raw.get("play", default.play),
        repair=raw.get("repair", default.repair),
        safety=raw.get("safety", default.safety),
        updated_at_ms=raw.get("updated_at_ms", default.updated_at_ms),
    )


def _episode_state_from_contract(
    episode: EmotionEpisodeV1,
    *,
    now_ms: int,
) -> dict[str, Any]:
    return {
        **episode.as_dict(),
        "last_decay_at_ms": max(episode.opened_at_ms, int(now_ms)),
    }


def _bounded_episode_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized = [dict(row) for row in rows]
    normalized.sort(
        key=lambda row: (
            -_episode_peak_intensity(row),
            -_integer(row.get("opened_at_ms")),
            str(row.get("episode_id") or ""),
        )
    )
    return normalized[:_AFFECTIVE_EPISODE_LIMIT]


def _episode_peak_intensity(row: Mapping[str, Any]) -> float:
    components = row.get("components")
    if not isinstance(components, list):
        return 0.0
    return max(
        [_clamp(_mapping(component).get("intensity")) for component in components]
        or [0.0]
    )


def _components_from_episode_rows(
    rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_emotion: dict[str, EmotionComponentV1] = {}
    for row in rows:
        components = row.get("components")
        if not isinstance(components, list):
            raise ValueError("affective episode components must be a list")
        for raw_component in components:
            component = _component_from_mapping(_mapping(raw_component))
            current = by_emotion.get(component.emotion)
            if current is None or (
                component.intensity,
                component.action_tendency,
            ) > (
                current.intensity,
                current.action_tendency,
            ):
                by_emotion[component.emotion] = component
    ordered = sorted(
        by_emotion.values(),
        key=lambda component: (-component.intensity, component.emotion),
    )[:_AFFECTIVE_COMPONENT_LIMIT]
    return [component.as_dict() for component in ordered]


def _decay_affective_episodes(
    affective: dict[str, Any],
    *,
    now_ms: int,
) -> bool:
    changed = False
    active_rows: list[dict[str, Any]] = []
    for raw_row in list(affective.get("active_episodes") or []):
        row = dict(raw_row)
        last_decay_at_ms = max(
            _integer(row.get("opened_at_ms")),
            _integer(row.get("last_decay_at_ms")),
        )
        elapsed_ms = max(0, int(now_ms) - last_decay_at_ms)
        if elapsed_ms:
            half_life_ms = max(1_000, _integer(row.get("decay_half_life_ms"), 60_000))
            factor = 0.5 ** (elapsed_ms / half_life_ms)
            components: list[dict[str, Any]] = []
            for raw_component in list(row.get("components") or []):
                component = _component_from_mapping(_mapping(raw_component))
                components.append(
                    {
                        **component.as_dict(),
                        "intensity": round(component.intensity * factor, 6),
                    }
                )
            row["components"] = components
            row["last_decay_at_ms"] = int(now_ms)
            changed = True
        if _episode_peak_intensity(row) >= 0.015:
            active_rows.append(row)
        else:
            changed = True
    bounded = _bounded_episode_rows(active_rows)
    if bounded != list(affective.get("active_episodes") or []):
        changed = True
    affective["active_episodes"] = bounded
    return changed


def _apply_appraisal_needs(
    affective: dict[str, Any],
    *,
    appraisal: EventAppraisalV1,
    now_ms: int,
) -> None:
    needs = _mapping(affective.get("needs"))
    needs["repair"] = round(
        _clamp(
            max(
                _float(needs.get("repair")) * 0.82,
                appraisal.boundary_relevance * 0.68 + appraisal.risk * 0.22,
            )
        ),
        6,
    )
    needs["safety"] = round(
        _clamp(
            max(
                _float(needs.get("safety")) * 0.88,
                appraisal.risk * 0.72 + appraisal.uncertainty * 0.16,
            )
        ),
        6,
    )
    needs["connection"] = round(
        _clamp(
            _float(needs.get("connection"), 0.12) * 0.92
            + appraisal.relationship_significance * 0.08
        ),
        6,
    )
    needs["autonomy"] = round(
        _clamp(
            _float(needs.get("autonomy"), 0.25) * 0.94
            + appraisal.boundary_relevance * 0.08
        ),
        6,
    )
    needs["updated_at_ms"] = now_ms
    affective["needs"] = needs


def _advance_affective_needs(
    affective: dict[str, Any],
    *,
    elapsed_ms: int,
    now_ms: int,
) -> None:
    hours = min(72.0, max(0, elapsed_ms) / 3_600_000.0)
    needs = _mapping(affective.get("needs"))
    needs["rest"] = round(
        _clamp(_float(needs.get("rest"), 0.12) + hours * 0.012),
        6,
    )
    needs["connection"] = round(
        _clamp(_float(needs.get("connection"), 0.12) + hours * 0.006),
        6,
    )
    needs["repair"] = round(
        _clamp(_float(needs.get("repair")) * (0.985 ** hours)),
        6,
    )
    needs["safety"] = round(
        _clamp(_float(needs.get("safety")) * (0.99 ** hours)),
        6,
    )
    needs["updated_at_ms"] = now_ms
    affective["needs"] = needs


def _refresh_affective_head(
    affective: dict[str, Any],
    *,
    now_ms: int,
    mood_weight: float,
) -> None:
    components = _components_from_episode_rows(
        list(affective.get("active_episodes") or [])
    )
    affective["components"] = components
    mood = _mapping(affective.get("mood"))
    if components:
        total = sum(_float(component.get("intensity")) for component in components)
        target_valence = (
            sum(
                _float(component.get("intensity"))
                * _float(component.get("valence"))
                for component in components
            )
            / total
            if total
            else 0.18
        )
        target_arousal = (
            sum(
                _float(component.get("intensity"))
                * _float(component.get("arousal"))
                for component in components
            )
            / total
            if total
            else 0.24
        )
        activation = min(1.0, total / max(1, len(components)))
        label = str(components[0].get("emotion") or "calm").lower()
    else:
        target_valence = 0.18
        target_arousal = 0.24
        activation = 0.0
        label = "calm"
    weight = _clamp(mood_weight)
    mood["label"] = label
    mood["valence"] = _approach_signed(
        _float(mood.get("valence"), 0.18),
        target_valence,
        weight,
    )
    mood["arousal"] = _approach(
        _float(mood.get("arousal"), 0.24),
        target_arousal,
        weight,
    )
    mood["energy"] = round(
        _clamp(
            _approach(
                _float(mood.get("energy"), 0.62),
                max(0.15, 0.62 - activation * 0.18),
                min(0.5, weight * 0.75),
            )
        ),
        6,
    )
    mood["updated_at_ms"] = now_ms
    affective["mood"] = mood


def _safe_observation_head(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {} if not raw else _observation_from_mapping(raw).as_dict()


def _safe_appraisal_head(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {} if not raw else _appraisal_from_mapping(raw).as_dict()


def _safe_resolution_head(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not raw:
        return {}
    return {
        "episode_id": _required_text(raw.get("episode_id"), "last_resolution.episode_id", limit=240),
        "resolution_ref": _required_text(
            raw.get("resolution_ref"),
            "last_resolution.resolution_ref",
            limit=512,
        ),
        "resolved_at_ms": max(0, _integer(raw.get("resolved_at_ms"))),
    }


def _assert_affective_subject(
    relationship_subject_id: str,
    *,
    state: Mapping[str, Any],
    field_name: str,
) -> None:
    identity = _mapping(state.get("identity"))
    expected = str(identity.get("relationship_subject_id") or "")
    if str(relationship_subject_id) != expected:
        raise ValueError(f"{field_name} must match the aggregate relationship subject")


def _reject_raw_affective_material(value: Any) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().lower()
            if key in _AFFECTIVE_RAW_MATERIAL_KEYS:
                raise ValueError(f"affective command cannot contain {key}")
            _reject_raw_affective_material(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_raw_affective_material(nested)


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


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _merge_patch(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key in sorted(str(item) for item in patch):
        incoming = patch.get(key)
        if incoming is None:
            result.pop(key, None)
        elif isinstance(incoming, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge_patch(dict(result[key]), incoming)
        elif isinstance(incoming, Mapping):
            result[key] = _merge_patch({}, incoming)
        else:
            result[key] = deepcopy(incoming)
    return result


def _clamp(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return float(default)


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return int(default)


def _required_text(value: Any, name: str, *, limit: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    if len(normalized) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return normalized


def _approach(current: float, target: float, weight: float) -> float:
    ratio = _clamp(weight)
    return round(max(-1.0, min(1.0, current + (target - current) * ratio)), 4)


def _approach_signed(current: float, target: float, weight: float) -> float:
    ratio = _clamp(weight)
    return round(max(-1.0, min(1.0, current + (target - current) * ratio)), 6)


def _first_float(
    values: Mapping[str, Any],
    keys: tuple[str, ...],
    default: float,
) -> float:
    for key in keys:
        if values.get(key) not in (None, ""):
            return _clamp(values.get(key), default)
    return _clamp(default)


__all__ = ["reduce_life_domains"]
