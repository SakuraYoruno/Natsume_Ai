"""Lifecycle runtime responsibility extracted from the HTTP adapter."""

from ._shared import *  # noqa: F403

def _build_background_runtime_snapshot(
    *,
    now_ms: int,
    persona_user_id: str,
    inner_world_state: dict[str, Any],
    scheduler_snapshot_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from host_brain.persona.identity import resolve_persona_self_id  # noqa: PLC0415

        persona_user_id = resolve_persona_self_id(persona_user_id)
    except Exception:
        persona_user_id = str(persona_user_id or "").strip() or "natsume_ai"

    scheduler_snapshot: dict[str, Any] = {}
    scheduler_error = ""
    if scheduler_snapshot_override is not None:
        scheduler_snapshot = dict(scheduler_snapshot_override or {})
    else:
        scheduler_error = "SCHEDULER_SERVICE_SNAPSHOT_REQUIRED"
        scheduler_snapshot = {
            "scheduler_status": "UNAVAILABLE",
            "total_tasks": 0,
            "enabled_tasks": 0,
            "suspended_tasks": 0,
            "error_tasks": 0,
            "tasks": [],
            "ts_ms": now_ms,
        }

    scheduler_status = str(scheduler_snapshot.get("scheduler_status") or "UNAVAILABLE").strip().upper()
    scheduler_active = scheduler_status in {"HEALTHY", "DEGRADED", "RUNNING", "UP", "OK"}
    # AW-04 retired the split Life timers.  One governed ``life_tick`` owns
    # numeric/emotion/proactive policy evaluation and is therefore also the
    # only scheduler health identity exposed by this read model.
    core_tick_ids = ["life_tick"]
    proactive_task_id = "life_tick"
    tasks_by_id: dict[str, dict[str, Any]] = {}
    for item in list(scheduler_snapshot.get("tasks") or []):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "").strip()
        if task_id:
            tasks_by_id[task_id] = dict(item)

    core_ticks_missing = [task_id for task_id in core_tick_ids if task_id not in tasks_by_id]
    core_ticks_registered = len(core_ticks_missing) == 0

    core_tick_tasks: dict[str, Any] = {}
    for task_id in core_tick_ids:
        task_health = tasks_by_id.get(task_id, {})
        status_value = str(task_health.get("status") or "MISSING").strip().upper() or "MISSING"
        enabled = bool(task_health.get("enabled", False)) if task_health else False
        expected_interval_s = _extract_interval_seconds(task_health) or 60
        last_run_ts = _coerce_int(task_health.get("last_run_ts"), 0)
        if last_run_ts <= 0:
            last_run_ts = None
        core_tick_tasks[task_id] = {
            "status": status_value,
            "enabled": enabled,
            "last_run_ts": last_run_ts,
            "expected_interval_s": expected_interval_s,
            "seconds_since_last_run": _seconds_since_timestamp(ts_ms=last_run_ts, now_ms=now_ms),
            "last_reason_code": str(task_health.get("last_reason_code") or "").strip() or None,
            "last_gate_state": str(task_health.get("last_gate_state") or "").strip().lower() or None,
            "last_mood_state": str(task_health.get("last_mood_state") or "").strip().lower() or None,
            "last_endocrine_cortisol": _coerce_float(task_health.get("last_endocrine_cortisol"), 0.0),
            "last_endocrine_energy": _coerce_float(task_health.get("last_endocrine_energy"), 0.0),
        }

    proactive_task_health = tasks_by_id.get(proactive_task_id, {})
    proactive_last_run_ts = _coerce_int(proactive_task_health.get("last_run_ts"), 0)
    if proactive_last_run_ts <= 0:
        proactive_last_run_ts = None
    proactive_task_snapshot = {
        "task_id": proactive_task_id,
        "status": str(proactive_task_health.get("status") or "MISSING").strip().upper() or "MISSING",
        "enabled": bool(proactive_task_health.get("enabled", False)) if proactive_task_health else False,
        "last_run_ts": proactive_last_run_ts,
        "seconds_since_last_run": _seconds_since_timestamp(ts_ms=proactive_last_run_ts, now_ms=now_ms),
        "last_reason_code": str(proactive_task_health.get("last_reason_code") or "").strip() or None,
        "last_gate_state": str(proactive_task_health.get("last_gate_state") or "").strip().lower() or None,
        "last_mood_state": str(proactive_task_health.get("last_mood_state") or "").strip().lower() or None,
        "last_endocrine_cortisol": _coerce_float(proactive_task_health.get("last_endocrine_cortisol"), 0.0),
        "last_endocrine_energy": _coerce_float(proactive_task_health.get("last_endocrine_energy"), 0.0),
    }

    gate_block_reason_codes = {
        "EMOTION_GATE_BLOCKED",
        "ENDOCRINE_GATE_BLOCKED",
        "ENDOCRINE_PROACTIVE_BLOCKED",
    }
    gate_blocked_tasks: list[str] = []
    for task_id, task_health in tasks_by_id.items():
        reason_code = str(task_health.get("last_reason_code") or "").strip().upper()
        if reason_code in gate_block_reason_codes:
            gate_blocked_tasks.append(task_id)

    last_inner_world_tick_ts_ms = _coerce_int(inner_world_state.get("last_tick_ts_ms"), 0)
    if last_inner_world_tick_ts_ms <= 0:
        last_inner_world_tick_ts_ms = None
    numeric_tick_last_run_ts = _coerce_int(
        core_tick_tasks.get("life_tick", {}).get("last_run_ts"),
        0,
    )
    if numeric_tick_last_run_ts > 0 and (
        last_inner_world_tick_ts_ms is None
        or numeric_tick_last_run_ts > last_inner_world_tick_ts_ms
    ):
        last_inner_world_tick_ts_ms = numeric_tick_last_run_ts
    seconds_since_inner_world_tick = _seconds_since_timestamp(
        ts_ms=last_inner_world_tick_ts_ms,
        now_ms=now_ms,
    )

    inner_tick_expected_interval_s = int(
        core_tick_tasks.get("inner_world_numeric_tick", {}).get("expected_interval_s") or 60
    )
    tick_health = _classify_tick_health(
        scheduler_status=scheduler_status,
        core_ticks_registered=core_ticks_registered,
        seconds_since_last_tick=seconds_since_inner_world_tick,
        expected_interval_s=inner_tick_expected_interval_s,
    )

    inner_world_user_id = str(inner_world_state.get("user_id") or "").strip()
    persona_user_alignment_ok = (not inner_world_user_id) or inner_world_user_id == persona_user_id
    is_advancing = bool(
        scheduler_active
        and core_ticks_registered
        and tick_health in {"fresh", "delayed"}
        and core_tick_tasks.get("inner_world_numeric_tick", {}).get("enabled", False)
        and persona_user_alignment_ok
    )

    return {
        "scheduler_status": scheduler_status,
        "scheduler_active": scheduler_active,
        "scheduler_total_tasks": _coerce_int(scheduler_snapshot.get("total_tasks"), 0),
        "scheduler_enabled_tasks": _coerce_int(scheduler_snapshot.get("enabled_tasks"), 0),
        "core_tick_ids": core_tick_ids,
        "core_ticks_registered": core_ticks_registered,
        "core_ticks_missing": core_ticks_missing,
        "core_tick_tasks": core_tick_tasks,
        "proactive_task": proactive_task_snapshot,
        "gate_blocked_tasks": gate_blocked_tasks,
        "last_inner_world_tick_ts_ms": last_inner_world_tick_ts_ms,
        "seconds_since_inner_world_tick": seconds_since_inner_world_tick,
        "tick_health": tick_health,
        "persona_user_alignment_ok": persona_user_alignment_ok,
        "inner_world_user_id": inner_world_user_id or None,
        "is_advancing": is_advancing,
        "scheduler_error": scheduler_error or None,
    }


def _resolve_offline_reconnect_profile(offline_state: dict[str, Any]) -> tuple[str, str]:
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        payload = get_limbic_agent().resolve_offline_reconnect_profile(
            offline_state=dict(offline_state or {}),
        )
        if isinstance(payload, Mapping):
            absence_band = str(payload.get("absence_band") or "recent").strip() or "recent"
            reconnect_style = str(payload.get("reconnect_style") or "soft_resume").strip() or "soft_resume"
            return absence_band, reconnect_style
    except Exception:
        pass
    return "recent", "soft_resume"


def _resolve_absence_band(offline_state: dict[str, Any]) -> str:
    absence_band, _ = _resolve_offline_reconnect_profile(offline_state)
    return absence_band


def _derive_reply_posture(render_policy: dict[str, Any]) -> str:
    should_be_withdrawn = bool(render_policy.get("should_be_withdrawn") or False)
    allow_followup = bool(render_policy.get("allow_followup", True))
    allow_topic_shift = bool(render_policy.get("allow_topic_shift", True))
    max_chars = int(render_policy.get("max_chars") or 0)
    warmth_floor = _coerce_float(render_policy.get("warmth_floor"), 0.0)

    if should_be_withdrawn:
        return "withdrawn"
    if 0 < max_chars <= 80 and not allow_followup:
        return "brief_guarded"
    if warmth_floor >= 0.18:
        return "warm_reconnect"
    if not allow_topic_shift:
        return "focused"
    return "balanced"


def _build_runtime_summary(
    *,
    current_topic: str,
    sys_mode: str,
    activity_monitor: dict[str, Any],
    bond_state: dict[str, Any],
    bond_summary: dict[str, Any],
    offline_state: dict[str, Any],
    silence_state: dict[str, Any],
    body_sense: dict[str, Any],
    self_reflection_pulse: dict[str, Any],
    bond_narrative: dict[str, Any],
    motive_state: dict[str, Any],
    render_policy: dict[str, Any],
    slot_status: dict[str, str],
    degraded_reasons: list[str],
) -> dict[str, Any]:
    absence_band, offline_reconnect_style = _resolve_offline_reconnect_profile(offline_state)
    bond_stage = str(
        bond_state.get("current_stage")
        or bond_summary.get("stage")
        or "UNKNOWN"
    ).strip() or "UNKNOWN"
    willingness_level = str(silence_state.get("willingness_level") or "NORMAL").strip().upper() or "NORMAL"
    fatigue_level = str(body_sense.get("fatigue_level") or "UNKNOWN").strip().upper() or "UNKNOWN"
    drowsiness_level = str(body_sense.get("drowsiness_level") or "UNKNOWN").strip().upper() or "UNKNOWN"
    dominant_motive = str(motive_state.get("dominant_motive") or "unknown").strip() or "unknown"
    reply_posture = _derive_reply_posture(render_policy)
    activity_event = str(activity_monitor.get("recent_event_type") or "").strip()
    topic_resistance_active = bool(silence_state.get("topic_resistance_active") or False)
    guardrails = dict(self_reflection_pulse.get("narrative_guardrails") or {})
    self_reflection_risk_level = str(guardrails.get("risk_level") or "").strip().lower()

    headline_parts: list[str] = [f"mode={sys_mode}"]
    if bond_stage and bond_stage != "UNKNOWN":
        headline_parts.append(f"bond={bond_stage}")
    if absence_band != "recent":
        headline_parts.append(f"offline={absence_band}")
    if activity_event:
        headline_parts.append(f"activity={activity_event}")
    if dominant_motive and dominant_motive != "unknown":
        headline_parts.append(f"motive={dominant_motive}")
    headline_parts.append(f"reply={reply_posture}")

    why_reply: list[str] = []
    for source in (
        list(render_policy.get("policy_reason_tags") or []),
        list(motive_state.get("motive_reason_tags") or []),
    ):
        for item in source:
            label = str(item or "").strip()
            if label and label not in why_reply:
                why_reply.append(label)
    fallback_reasons = [
        f"mode:{sys_mode}",
        f"offline:{absence_band}" if absence_band != "recent" else "",
        f"motive:{dominant_motive}" if dominant_motive and dominant_motive != "unknown" else "",
        "policy:withdrawn" if bool(render_policy.get("should_be_withdrawn") or False) else "",
        "policy:no_followup" if render_policy.get("allow_followup") is False else "",
        "policy:no_topic_shift" if render_policy.get("allow_topic_shift") is False else "",
    ]
    for item in fallback_reasons:
        label = str(item or "").strip()
        if label and label not in why_reply:
            why_reply.append(label)

    notable_signals: list[str] = []
    if current_topic:
        notable_signals.append(f"current_topic:{current_topic}")
    awakening_hint = str(offline_state.get("awakening_hint") or "").strip()
    if awakening_hint:
        notable_signals.append(f"awakening_hint:{awakening_hint[:80]}")
    growth_note = str(self_reflection_pulse.get("growth_note") or "").strip()
    if growth_note:
        notable_signals.append(f"growth_note:{growth_note[:80]}")
    narratives = list(bond_narrative.get("narratives") or [])
    if narratives:
        first_narrative = str(narratives[0] or "").strip()
        if first_narrative:
            notable_signals.append(f"bond_narrative:{first_narrative[:80]}")

    degraded_slots = [
        f"{slot}:{status}"
        for slot, status in dict(slot_status or {}).items()
        if str(status or "").strip() != "live"
    ]

    anticipation_topics = [
        str(item).strip()
        for item in (offline_state.get("anticipation_topics") or [])
        if str(item).strip()
    ][:3]
    first_narrative = ""
    narratives = list(bond_narrative.get("narratives") or [])
    if narratives:
        first_narrative = str(narratives[0] or "").strip()
    guardrail_tags = [
        str(item).strip()
        for item in (guardrails.get("risk_tags") or [])
        if str(item).strip()
    ]
    try:
        shared_memory_count = int(bond_narrative.get("shared_memory_count") or 0)
    except (TypeError, ValueError):
        shared_memory_count = 0
    motive_active_drives = {
        key: round(_coerce_float(motive_state.get(key), 0.0), 3)
        for key in (
            "reach_out_drive",
            "care_drive",
            "continuity_drive",
            "withdraw_drive",
            "self_protect_drive",
        )
        if _coerce_float(motive_state.get(key), 0.0) > 0.0
    }
    decision_chain: list[str] = []
    for item in (
        f"mode:{sys_mode}",
        f"activity:{activity_event}" if activity_event else "",
        f"offline:{absence_band}->{offline_reconnect_style}" if absence_band != "recent" else "",
        f"silence:{willingness_level}" if willingness_level != "NORMAL" else "",
        "silence:topic_resistance" if topic_resistance_active else "",
        f"body:{fatigue_level}/{drowsiness_level}"
        if fatigue_level != "FRESH" or drowsiness_level not in {"UNKNOWN", "ALERT", "AWAKE"}
        else "",
        f"self_reflection_guard:{self_reflection_risk_level}" if self_reflection_risk_level else "",
        f"motive:{dominant_motive}" if dominant_motive != "unknown" else "",
        f"render_policy:{reply_posture}",
    ):
        label = str(item or "").strip()
        if label and label not in decision_chain:
            decision_chain.append(label)

    core_slots = {
        "sys_mode": {
            "status": str(slot_status.get("hard_state") or "unknown"),
            "value": sys_mode,
            "activity_event": activity_event,
            "idle_minutes": round(_coerce_float(activity_monitor.get("idle_minutes"), 0.0), 1),
        },
        "offline_state": {
            "status": str(slot_status.get("offline_state") or "unknown"),
            "absence_band": absence_band,
            "offline_hours": round(_coerce_float(offline_state.get("offline_hours"), 0.0), 1),
            "offline_reconnect_style": offline_reconnect_style,
            "loneliness_level": round(_coerce_float(offline_state.get("loneliness_level"), 0.0), 3),
            "anticipation_topics": anticipation_topics,
            "should_express_waiting": bool(offline_state.get("should_express_waiting") or False),
            "awakening_hint": _truncate_text(offline_state.get("awakening_hint"), limit=100),
        },
        "silence_state": {
            "status": str(slot_status.get("silence_state") or "unknown"),
            "response_willingness": round(_coerce_float(silence_state.get("response_willingness"), 1.0), 3),
            "willingness_level": willingness_level,
            "topic_resistance_active": topic_resistance_active,
            "resistant_topic": str(silence_state.get("resistant_topic") or "").strip(),
            "topic_resistance_hint": _truncate_text(silence_state.get("topic_resistance_hint"), limit=100),
        },
        "body_sense": {
            "status": str(slot_status.get("body_sense") or "unknown"),
            "fatigue_level": fatigue_level,
            "drowsiness_level": drowsiness_level,
            "loneliness_level": str(body_sense.get("loneliness_level") or "UNKNOWN").strip().upper() or "UNKNOWN",
            "willingness_penalty": round(_coerce_float(body_sense.get("willingness_penalty"), 0.0), 3),
            "body_sense_hint": _truncate_text(body_sense.get("body_sense_hint"), limit=100),
        },
        "bond_narrative": {
            "status": str(slot_status.get("bond_narrative") or "unknown"),
            "bond_stage": bond_stage,
            "relationship_duration": str(bond_narrative.get("relationship_duration") or "").strip(),
            "shared_memory_count": shared_memory_count,
            "narrative_preview": _truncate_text(first_narrative, limit=100),
        },
        "self_reflection_pulse": {
            "status": str(slot_status.get("self_reflection_pulse") or "unknown"),
            "self_awareness": _truncate_text(self_reflection_pulse.get("self_awareness"), limit=100),
            "growth_note": _truncate_text(growth_note, limit=100),
            "time_context": str(self_reflection_pulse.get("time_context") or "").strip(),
            "guardrail_risk_level": self_reflection_risk_level,
            "guardrail_tags": guardrail_tags,
        },
        "motive_state": {
            "status": str(slot_status.get("motive_state") or "unknown"),
            "dominant_motive": dominant_motive,
            "reason_tags": [
                str(item).strip()
                for item in (motive_state.get("motive_reason_tags") or [])
                if str(item).strip()
            ][:5],
            "active_drives": motive_active_drives,
        },
        "render_policy": {
            "status": str(slot_status.get("render_policy") or "unknown"),
            "reply_posture": reply_posture,
            "max_chars": int(render_policy.get("max_chars") or 0),
            "max_sentences": int(render_policy.get("max_sentences") or 0),
            "allow_followup": bool(render_policy.get("allow_followup", True)),
            "allow_topic_shift": bool(render_policy.get("allow_topic_shift", True)),
            "allow_proactive_after_reply": bool(render_policy.get("allow_proactive_after_reply", True)),
            "reason_tags": [
                str(item).strip()
                for item in (render_policy.get("policy_reason_tags") or [])
                if str(item).strip()
            ][:5],
        },
    }

    return {
        "headline": " | ".join(part for part in headline_parts if part),
        "current_state": {
            "sys_mode": sys_mode,
            "bond_stage": bond_stage,
            "absence_band": absence_band,
            "offline_reconnect_style": offline_reconnect_style,
            "willingness_level": willingness_level,
            "fatigue_level": fatigue_level,
            "drowsiness_level": drowsiness_level,
            "activity_event": activity_event,
            "topic_resistance_active": topic_resistance_active,
            "self_reflection_risk_level": self_reflection_risk_level,
            "dominant_motive": dominant_motive,
        },
        "reply_posture": {
            "style": reply_posture,
            "max_chars": int(render_policy.get("max_chars") or 0),
            "max_sentences": int(render_policy.get("max_sentences") or 0),
            "allow_followup": bool(render_policy.get("allow_followup", True)),
            "allow_topic_shift": bool(render_policy.get("allow_topic_shift", True)),
            "allow_proactive_after_reply": bool(render_policy.get("allow_proactive_after_reply", True)),
            "should_be_withdrawn": bool(render_policy.get("should_be_withdrawn") or False),
            "warmth_floor": _coerce_float(render_policy.get("warmth_floor"), 0.0),
            "intimacy_ceiling": _coerce_float(render_policy.get("intimacy_ceiling"), 0.0),
        },
        "decision_chain": decision_chain,
        "core_slots": core_slots,
        "why_reply": why_reply,
        "notable_signals": notable_signals,
        "degraded_slots": degraded_slots,
        "degraded_reasons": list(degraded_reasons or []),
    }
