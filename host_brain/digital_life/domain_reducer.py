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
    state = _merge_patch(state, raw_patch)

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
        _float(affect.get("loneliness")) + min(idle_ms / 86_400_000.0, 1.0) * 0.18
    )
    affect["arousal"] = _approach(_float(affect.get("arousal"), 0.24), 0.22, min(1.0, elapsed_hours * 0.08))
    affect["tension"] = _approach(_float(affect.get("tension")), 0.08, min(1.0, elapsed_hours * 0.06))
    needs["contact"] = _clamp(_float(needs.get("contact"), 0.12) + min(idle_ms / 86_400_000.0, 1.0) * 0.2)
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
    affect = _mapping(state.get("affect"))
    emotion = _mapping(payload.get("emotion_context"))
    observed_valence, observed_arousal = _emotion_signal(emotion)
    affect["valence"] = _approach(_float(affect.get("valence"), 0.18), observed_valence, 0.28)
    affect["arousal"] = _approach(_float(affect.get("arousal"), 0.24), observed_arousal, 0.25)
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


def _emotion_signal(emotion: Mapping[str, Any]) -> tuple[float, float]:
    if "emotion_valence" in emotion or "valence" in emotion:
        valence = max(-1.0, min(1.0, _float(emotion.get("emotion_valence", emotion.get("valence")))))
    else:
        tag = str(emotion.get("emotion_tag") or "NEUTRAL").strip().upper()
        valence = {
            "HAPPY": 0.65,
            "WARM": 0.5,
            "EXCITED": 0.55,
            "SAD": -0.55,
            "ANGRY": -0.7,
            "TENSE": -0.35,
            "TIRED": -0.15,
        }.get(tag, 0.08)
    arousal = _clamp(emotion.get("emotion_arousal", emotion.get("arousal", 0.35)), 0.35)
    return valence, arousal


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


def _approach(current: float, target: float, weight: float) -> float:
    ratio = _clamp(weight)
    return round(max(-1.0, min(1.0, current + (target - current) * ratio)), 4)


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
