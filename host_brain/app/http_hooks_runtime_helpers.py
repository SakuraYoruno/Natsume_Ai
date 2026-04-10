"""Helper functions used by http lifecycle hooks."""

from __future__ import annotations

import datetime as _dt
import glob as _glob
import json as _json
import os as _os
import sqlite3 as _sqlite3
import time as _time
import urllib.request as _urlreq
from pathlib import Path
from typing import Any, Callable, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged

def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text


def read_world_canon_fact(world_store: Any, fact_key: str) -> str | None:
    if str(fact_key or "").strip() != "world_canon_facts":
        return None
    try:
        docs = world_store.list_documents()
    except Exception:
        return None
    chunks: list[str] = []
    for doc in docs[:3]:
        title = str(getattr(doc, "title", "") or "").strip()
        body = str(getattr(doc, "body", "") or "").strip()
        line = f"{title}: {body}" if title and body else (body or title)
        if line:
            chunks.append(line)
    merged = "\n".join(chunks).strip()
    return merged or None


def read_style_canon_fact(style_store: Any, fact_key: str) -> str | None:
    if str(fact_key or "").strip() != "style_canon_facts":
        return None
    try:
        entries = style_store.list_entries()
    except Exception:
        return None
    chunks: list[str] = []
    for entry in entries[:3]:
        content = str(getattr(entry, "content", "") or "").strip()
        if content:
            chunks.append(content)
    merged = "\n".join(chunks).strip()
    return merged or None


def read_episodic_latest_fact(episodic_store: Any, fact_key: str) -> str | None:
    if str(fact_key or "").strip() != "episodic_latest":
        return None
    records_map = getattr(episodic_store, "_records", None)
    if not isinstance(records_map, dict) or not records_map:
        return None
    try:
        latest = max(records_map.values(), key=lambda row: int(getattr(row, "ts_ms", 0)))
    except Exception:
        return None
    text = str(getattr(latest, "text", "") or "").strip()
    return text or None


def _as_plain_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    for attr_name in ("as_dict", "model_dump", "dict", "to_summary"):
        method = getattr(value, attr_name, None)
        if not callable(method):
            continue
        try:
            payload = method()
        except Exception:
            continue
        if isinstance(payload, dict):
            return dict(payload)
    return {}


def _record_snapshot_status(
    slot_status: dict[str, str],
    degraded_reasons: list[str],
    *,
    slot: str,
    status: str,
    detail: str = "",
) -> None:
    normalized_status = str(status or "unknown").strip() or "unknown"
    slot_status[slot] = normalized_status
    if normalized_status != "live":
        reason = f"{slot}:{normalized_status}"
        if detail:
            reason = f"{reason} ({detail})"
        degraded_reasons.append(reason)


def _record_snapshot_slot_from_payload(
    slot_status: dict[str, str],
    degraded_reasons: list[str],
    *,
    slot: str,
    payload: Mapping[str, Any] | None,
    allow_empty_live: bool = False,
) -> None:
    normalized = dict(payload or {})
    if normalized or allow_empty_live:
        slot_status[slot] = "live"
        return
    _record_snapshot_status(
        slot_status,
        degraded_reasons,
        slot=slot,
        status="unavailable",
    )


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truncate_text(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}…"


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _seconds_since_timestamp(*, ts_ms: Any, now_ms: int) -> int | None:
    value = _coerce_int(ts_ms, 0)
    if value <= 0:
        return None
    delta_ms = max(0, int(now_ms) - value)
    return int(delta_ms / 1000)


def _extract_interval_seconds(task_health: dict[str, Any]) -> int | None:
    trigger_config = task_health.get("trigger_config")
    if not isinstance(trigger_config, dict):
        return None
    seconds = _coerce_int(trigger_config.get("seconds"), 0)
    return seconds if seconds > 0 else None


def _classify_tick_health(
    *,
    scheduler_status: str,
    core_ticks_registered: bool,
    seconds_since_last_tick: int | None,
    expected_interval_s: int,
) -> str:
    normalized_scheduler = str(scheduler_status or "").strip().upper()
    if normalized_scheduler in {"UNAVAILABLE", "ERROR", "STOPPED", "DOWN", "OFFLINE"}:
        return "inactive"
    if not core_ticks_registered:
        return "inactive"
    if seconds_since_last_tick is None:
        return "inactive"

    base_interval = max(int(expected_interval_s or 60), 30)
    if seconds_since_last_tick <= int(base_interval * 2.5):
        return "fresh"
    if seconds_since_last_tick <= int(base_interval * 6):
        return "delayed"
    return "stalled"


def _build_background_runtime_snapshot(
    *,
    now_ms: int,
    persona_user_id: str,
    inner_world_state: dict[str, Any],
) -> dict[str, Any]:
    scheduler_snapshot: dict[str, Any] = {}
    scheduler_error = ""
    try:
        from host_brain.scheduler.scheduler_routes import get_scheduler_health_snapshot_safe  # noqa: PLC0415

        scheduler_snapshot = dict(get_scheduler_health_snapshot_safe() or {})
    except Exception as exc:
        scheduler_error = str(exc)[:160]
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
    core_tick_ids = ["inner_world_tick", "silence_tick", "body_sense_tick"]
    proactive_task_id = "proactive_talk_periodic"
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
    seconds_since_inner_world_tick = _seconds_since_timestamp(
        ts_ms=last_inner_world_tick_ts_ms,
        now_ms=now_ms,
    )

    inner_tick_expected_interval_s = int(
        core_tick_tasks.get("inner_world_tick", {}).get("expected_interval_s") or 60
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
        and core_tick_tasks.get("inner_world_tick", {}).get("enabled", False)
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


def _derive_reply_posture(response_policy: dict[str, Any]) -> str:
    should_be_withdrawn = bool(response_policy.get("should_be_withdrawn") or False)
    allow_followup = bool(response_policy.get("allow_followup", True))
    allow_topic_shift = bool(response_policy.get("allow_topic_shift", True))
    max_chars = int(response_policy.get("max_chars") or 0)
    warmth_floor = _coerce_float(response_policy.get("warmth_floor"), 0.0)

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
    response_policy: dict[str, Any],
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
    reply_posture = _derive_reply_posture(response_policy)
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
        list(response_policy.get("policy_reason_tags") or []),
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
        "policy:withdrawn" if bool(response_policy.get("should_be_withdrawn") or False) else "",
        "policy:no_followup" if response_policy.get("allow_followup") is False else "",
        "policy:no_topic_shift" if response_policy.get("allow_topic_shift") is False else "",
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
        f"response_policy:{reply_posture}",
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
        "response_policy": {
            "status": str(slot_status.get("response_policy") or "unknown"),
            "reply_posture": reply_posture,
            "max_chars": int(response_policy.get("max_chars") or 0),
            "max_sentences": int(response_policy.get("max_sentences") or 0),
            "allow_followup": bool(response_policy.get("allow_followup", True)),
            "allow_topic_shift": bool(response_policy.get("allow_topic_shift", True)),
            "allow_proactive_after_reply": bool(response_policy.get("allow_proactive_after_reply", True)),
            "reason_tags": [
                str(item).strip()
                for item in (response_policy.get("policy_reason_tags") or [])
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
            "max_chars": int(response_policy.get("max_chars") or 0),
            "max_sentences": int(response_policy.get("max_sentences") or 0),
            "allow_followup": bool(response_policy.get("allow_followup", True)),
            "allow_topic_shift": bool(response_policy.get("allow_topic_shift", True)),
            "allow_proactive_after_reply": bool(response_policy.get("allow_proactive_after_reply", True)),
            "should_be_withdrawn": bool(response_policy.get("should_be_withdrawn") or False),
            "warmth_floor": _coerce_float(response_policy.get("warmth_floor"), 0.0),
            "intimacy_ceiling": _coerce_float(response_policy.get("intimacy_ceiling"), 0.0),
        },
        "decision_chain": decision_chain,
        "core_slots": core_slots,
        "why_reply": why_reply,
        "notable_signals": notable_signals,
        "degraded_slots": degraded_slots,
        "degraded_reasons": list(degraded_reasons or []),
    }


def build_digital_life_runtime_snapshot(*, current_topic: str = "") -> dict[str, Any]:
    now_ms = int(_time.time() * 1000)
    normalized_topic = str(current_topic or "").strip()
    slot_status: dict[str, str] = {}
    degraded_reasons: list[str] = []

    persona_user_id = "default"
    try:
        from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415

        persona_user_id = str(get_canonical_bond_user_id() or "").strip() or "default"
        slot_status["persona_user_id"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="persona_user_id",
            status="fallback",
            detail=str(exc)[:120],
        )

    hard_state: dict[str, Any] = {}
    try:
        from host_brain.app.dependencies import get_hard_state  # noqa: PLC0415

        hard_state = _as_plain_dict(get_hard_state())
        slot_status["hard_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="hard_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    sys_mode = str(
        hard_state.get("sys_mode")
        or _os.environ.get("HOST_SYS_MODE")
        or "normal"
    ).strip().lower() or "normal"

    activity_monitor: dict[str, Any] = {}
    try:
        from host_brain.app import dependencies as _deps  # noqa: PLC0415

        monitor = _deps.get_activity_monitor(create_if_missing=True)
        if monitor is not None:
            if hasattr(monitor, "get_status"):
                activity_monitor = _as_plain_dict(monitor.get_status())
            else:
                activity_monitor = {
                    "running": bool(getattr(monitor, "_running", False)),
                    "gaming_check_interval_s": getattr(monitor, "_gaming_interval", None),
                    "idle_check_interval_s": getattr(monitor, "_idle_interval", None),
                }
            slot_status["activity_monitor"] = "live"
        else:
            slot_status["activity_monitor"] = "unavailable"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="activity_monitor",
            status="degraded",
            detail=str(exc)[:120],
        )

    emotion_context: dict[str, Any] = {
        "mood_state": str(hard_state.get("mood_state") or "steady").strip().lower() or "steady",
        "emotion_tag": "NEUTRAL",
        "emotion_intensity": 0.0,
    }
    timeline_summary: dict[str, Any] = {}
    timeline_diary: dict[str, Any] = {}
    try:
        from host_brain.app.emotion_context import _global_emotion_timeline, get_user_emotion_profile  # noqa: PLC0415

        profile = get_user_emotion_profile()
        profile_summary = _as_plain_dict(profile.to_summary() if hasattr(profile, "to_summary") else profile)
        if profile_summary:
            emotion_context["user_emotion_profile"] = profile_summary
            top_emotions = list(profile_summary.get("top_emotions") or [])
            if top_emotions and isinstance(top_emotions[0], dict):
                emotion_context["emotion_tag"] = str(top_emotions[0].get("tag") or emotion_context["emotion_tag"]).upper()
            try:
                emotion_context["emotion_intensity"] = float(profile_summary.get("volatility") or 0.0)
            except (TypeError, ValueError):
                pass

        timeline_summary = dict(_global_emotion_timeline.rebuild_summary(window_ms=6 * 3600 * 1000) or {})
        if timeline_summary:
            emotion_context["emotion_timeline_summary"] = timeline_summary
            dominant_emotion = str(timeline_summary.get("dominant_emotion") or "").strip().upper()
            if dominant_emotion:
                emotion_context["emotion_tag"] = dominant_emotion
            try:
                emotion_context["emotion_intensity"] = float(
                    timeline_summary.get("emotion_intensity")
                    or timeline_summary.get("avg_intensity")
                    or emotion_context.get("emotion_intensity")
                    or 0.0
                )
            except (TypeError, ValueError):
                pass

        timeline_diary = dict(_global_emotion_timeline.build_daily_diary(window_ms=24 * 3600 * 1000) or {})
        if timeline_diary:
            emotion_context["emotion_diary"] = {
                "dominant_emotion": timeline_diary.get("dominant_emotion"),
                "diary_text": str(timeline_diary.get("diary_text") or "")[:160],
            }

        slot_status["emotion_context"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="emotion_context",
            status="degraded",
            detail=str(exc)[:120],
        )

    endocrine_state: dict[str, Any] = {}
    try:
        from host_brain.app.dependencies import get_endocrine_state  # noqa: PLC0415

        endocrine_state = dict(
            get_endocrine_state(
                emotion_tag=str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                emotion_intensity=float(emotion_context.get("emotion_intensity") or 0.0),
            )
            or {}
        )
        emotion_context["endocrine_state"] = endocrine_state
        slot_status["endocrine_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="endocrine_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    immune_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415

        immune_agent = get_immune_agent()
        immune_state = dict(immune_agent.export_runtime_state() or {})
        if normalized_topic:
            preview = immune_agent.inspect_text(
                text=normalized_topic,
                source="obs.runtime_snapshot.preview",
                metadata={"persona_user_id": persona_user_id},
                record=False,
            )
            immune_state["preview"] = {
                "decision": preview.get("decision"),
                "risk_level": preview.get("risk_level"),
                "score": preview.get("score"),
                "reason_codes": list(preview.get("reason_codes") or []),
            }
        slot_status["immune_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="immune_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    bond_state: dict[str, Any] = {}
    bond_summary: dict[str, Any] = {}
    bond_summary_human: dict[str, Any] = {}
    bond_state_obj: Any = None
    try:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        social_snapshot = get_social_brain_agent().resolve_bond_snapshot(person_id=persona_user_id)
        bond_state_obj = social_snapshot.get("bond_state_obj")
        bond_summary = dict(social_snapshot.get("bond_summary") or {})
        bond_summary_human = _as_plain_dict(
            bond_state_obj.to_summary() if (bond_state_obj is not None and hasattr(bond_state_obj, "to_summary")) else {}
        )
        if bond_state_obj is not None:
            bond_state = {
                "user_id": persona_user_id,
                "current_stage": str(social_snapshot.get("bond_stage") or "STRANGER").strip() or "STRANGER",
                "trend": str(social_snapshot.get("bond_trend") or "").strip(),
                "trend_intensity": str(social_snapshot.get("bond_trend_intensity") or "").strip(),
                "capability_hint": str(social_snapshot.get("capability_hint") or "").strip(),
                "event_count": int(getattr(bond_state_obj, "event_count", 0) or 0),
                "last_event_ts_ms": int(getattr(bond_state_obj, "last_event_ts_ms", 0) or 0),
            }
            slot_status["bond_state"] = "live"
        else:
            slot_status["bond_state"] = "unavailable"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="bond_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    inner_world_state: dict[str, Any] = {}
    offline_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        limbic_agent = get_limbic_agent()
        inner_world_state = dict(limbic_agent.resolve_inner_world_state(person_id=persona_user_id) or {})
        offline_state = dict(limbic_agent.resolve_offline_state(person_id=persona_user_id) or {})
        slot_status["inner_world_state"] = "live"
        slot_status["offline_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="inner_world_state",
            status="degraded",
            detail=str(exc)[:120],
        )
        slot_status.setdefault("offline_state", "degraded")

    silence_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        silence_state = _as_plain_dict(
            get_limbic_agent().resolve_silence_state(current_topic=normalized_topic)
        )
        slot_status["silence_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="silence_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    body_sense: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        body_sense = _as_plain_dict(get_limbic_agent().resolve_body_sense_state())
        slot_status["body_sense"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="body_sense",
            status="degraded",
            detail=str(exc)[:120],
        )

    self_model_snapshot: dict[str, Any] = {}
    self_reflection_pulse: dict[str, Any] = {}
    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

        reflection_payload = get_prefrontal_agent().resolve_self_reflection_context(
            persona_user_id=persona_user_id,
            timeline_summary=timeline_summary,
            timeline_diary=timeline_diary,
            bond_state_obj=bond_state_obj,
            bond_summary=bond_summary,
            now_ms=now_ms,
            trigger_reason="runtime_snapshot",
        )
        self_model_snapshot = _as_plain_dict(reflection_payload.get("self_model_snapshot"))
        self_reflection_pulse = _as_plain_dict(reflection_payload.get("self_reflection_pulse"))
        slot_status["self_model_snapshot"] = "live"
        slot_status["self_reflection_pulse"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="self_reflection_pulse",
            status="degraded",
            detail=str(exc)[:120],
        )
        slot_status.setdefault("self_model_snapshot", "degraded")

    bond_narrative: dict[str, Any] = {}
    try:
        from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415
        from host_brain.bond.bond_narrative import BondNarrativeLayer  # noqa: PLC0415
        from host_brain.bond.milestone_tracker import MilestoneTracker  # noqa: PLC0415
        from host_brain.bond.shared_memory_index import SharedMemoryIndex  # noqa: PLC0415

        narrative_layer = BondNarrativeLayer(
            memory_index=SharedMemoryIndex(store=get_episodic_store()),
            milestone_tracker=MilestoneTracker(),
        )
        narrative_result = narrative_layer.build_narrative(
            user_id=persona_user_id,
            bond_stage=str(bond_state.get("current_stage") or "STRANGER").upper(),
            now_ts_ms=now_ms,
        )
        bond_narrative = _as_plain_dict(
            narrative_result.to_prompt_slot() if hasattr(narrative_result, "to_prompt_slot") else narrative_result
        )
        slot_status["bond_narrative"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="bond_narrative",
            status="degraded",
            detail=str(exc)[:120],
        )

    shared_memory_count = 0
    for candidate in (
        bond_narrative.get("shared_memory_count"),
        self_model_snapshot.get("shared_memory_count"),
        bond_summary_human.get("shared_memory_count"),
    ):
        try:
            shared_memory_count = int(candidate)
            break
        except (TypeError, ValueError):
            continue

    motive_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        motive_state = get_limbic_agent().compute_motive_state(
            silence_state=silence_state or None,
            body_sense=body_sense or None,
            offline_state=offline_state or None,
            bond_summary=bond_summary or None,
            self_reflection_pulse=self_reflection_pulse or None,
            emotion_context=emotion_context or None,
            pending_followup_topics=list(offline_state.get("anticipation_topics") or []),
            shared_memory_count=shared_memory_count,
            user_message=normalized_topic,
            runtime_flags={
                "source": "obs_runtime_snapshot",
                "sys_mode": sys_mode,
                "activity_monitor": activity_monitor,
            },
        )
        slot_status["motive_state"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="motive_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    response_policy: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        response_policy = get_limbic_agent().decide_response_policy(
            silence_state=silence_state or None,
            body_sense=body_sense or None,
            offline_state=offline_state or None,
            bond_summary=bond_summary or None,
            self_reflection_pulse=self_reflection_pulse or None,
            emotion_context=emotion_context or None,
            motive_state=motive_state or None,
            user_message=normalized_topic,
            runtime_flags={
                "source": "obs_runtime_snapshot",
                "sys_mode": sys_mode,
                "activity_monitor": activity_monitor,
            },
        )
        slot_status["response_policy"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="response_policy",
            status="degraded",
            detail=str(exc)[:120],
        )

    somatic_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.somatic_agent import get_somatic_agent  # noqa: PLC0415

        somatic_state = dict(get_somatic_agent().export_runtime_state() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="somatic_state",
            payload=somatic_state,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="somatic_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    peripheral_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.peripheral_agent import get_peripheral_agent  # noqa: PLC0415

        peripheral_state = dict(get_peripheral_agent().export_runtime_state() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="peripheral_state",
            payload=peripheral_state,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="peripheral_state",
            status="degraded",
            detail=str(exc)[:120],
        )

    circadian_state: dict[str, Any] = {}
    proprioception_state: dict[str, Any] = {}
    initiative_decision: dict[str, Any] = {}
    try:
        from host_brain.tribe.circadian_agent import get_circadian_agent  # noqa: PLC0415
        from host_brain.tribe.proprioception_agent import get_proprioception_agent  # noqa: PLC0415
        from host_brain.tribe.initiative_governor_agent import get_initiative_governor_agent  # noqa: PLC0415

        circadian_state = dict(get_circadian_agent().export_runtime_state() or {})
        proprioception_state = dict(get_proprioception_agent().export_runtime_state() or {})
        initiative_decision = dict(get_initiative_governor_agent().get_last_decision() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="circadian_state",
            payload=circadian_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="proprioception_state",
            payload=proprioception_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="initiative_decision",
            payload=initiative_decision,
            allow_empty_live=True,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="circadian_state",
            status="degraded",
            detail=str(exc)[:120],
        )
        slot_status.setdefault("proprioception_state", "degraded")
        slot_status.setdefault("initiative_decision", "degraded")

    default_mode_state: dict[str, Any] = {}
    mirror_neuron_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: PLC0415
        from host_brain.tribe.mirror_neuron_agent import get_mirror_neuron_agent  # noqa: PLC0415

        default_mode_state = dict(get_default_mode_agent().export_runtime_state() or {})
        mirror_neuron_state = dict(get_mirror_neuron_agent().export_runtime_state() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="default_mode_state",
            payload=default_mode_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="mirror_neuron_state",
            payload=mirror_neuron_state,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="default_mode_state",
            status="degraded",
            detail=str(exc)[:120],
        )
        slot_status.setdefault("mirror_neuron_state", "degraded")

    memory_layers: dict[str, Any] = {}
    try:
        from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: PLC0415

        memory_layers = dict(get_hippocampus_agent().resolve_memory_layer_snapshot() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="memory_layers",
            payload=memory_layers,
            allow_empty_live=True,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="memory_layers",
            status="degraded",
            detail=str(exc)[:120],
        )

    language_dual_system: dict[str, Any] = {}
    try:
        from host_brain.tribe.language_system_agent import get_language_system_agent  # noqa: PLC0415

        language_agent = get_language_system_agent()
        language_row = language_agent.state_registry.get_state("language_system_agent") or {}
        language_state = dict(language_row.get("state") or {})
        language_dual_system = dict(language_state.get("last_dual_system") or {})
        if not language_dual_system and normalized_topic:
            bond_stage = str(
                bond_state.get("current_stage")
                or bond_summary.get("stage")
                or "stranger"
            ).strip().lower() or "stranger"
            emotion_tag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            loneliness_level = _coerce_float(
                body_sense.get("loneliness"),
                _coerce_float(offline_state.get("loneliness_level"), 0.0),
            )
            language_dual_system = dict(
                language_agent.resolve_dual_system(
                    user_text=normalized_topic,
                    bond_stage=bond_stage,
                    emotion_tag=emotion_tag,
                    loneliness_level=loneliness_level,
                    high_loneliness_flag=loneliness_level >= 0.7,
                    response_goal="obs_runtime_snapshot",
                )
                or {}
            )
        if not language_dual_system:
            fallback_style = dict(language_state.get("last_language_style") or {})
            if fallback_style:
                language_dual_system = {"style": fallback_style}
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="language_dual_system",
            payload=language_dual_system,
            allow_empty_live=True,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="language_dual_system",
            status="degraded",
            detail=str(exc)[:120],
        )

    homeostasis_report: dict[str, Any] = {}
    try:
        from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415

        homeostasis_report = dict(
            get_global_registry().get_homeostasis_report(now_ts_ms=now_ms)
            or {}
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="homeostasis_report",
            payload=homeostasis_report,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="homeostasis_report",
            status="degraded",
            detail=str(exc)[:120],
        )

    interoception_state: dict[str, Any] = {}
    motor_cortex_state: dict[str, Any] = {}
    cerebellum_state: dict[str, Any] = {}
    glymphatic_state: dict[str, Any] = {}
    vestibular_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
        from host_brain.tribe.motor_cortex_agent import get_motor_cortex_agent  # noqa: PLC0415
        from host_brain.tribe.cerebellum_agent import get_cerebellum_agent  # noqa: PLC0415
        from host_brain.tribe.glymphatic_agent import get_glymphatic_agent  # noqa: PLC0415
        from host_brain.tribe.vestibular_agent import get_vestibular_agent  # noqa: PLC0415

        interoception_state = dict(get_interoception_agent().export_runtime_state() or {})
        motor_cortex_state = dict(get_motor_cortex_agent().export_runtime_state() or {})
        cerebellum_state = dict(get_cerebellum_agent().export_runtime_state() or {})
        glymphatic_state = dict(get_glymphatic_agent().export_runtime_state() or {})
        vestibular_state = dict(get_vestibular_agent().export_runtime_state() or {})
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="interoception_state",
            payload=interoception_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="motor_cortex_state",
            payload=motor_cortex_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="cerebellum_state",
            payload=cerebellum_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="glymphatic_state",
            payload=glymphatic_state,
        )
        _record_snapshot_slot_from_payload(
            slot_status,
            degraded_reasons,
            slot="vestibular_state",
            payload=vestibular_state,
        )
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="interoception_state",
            status="degraded",
            detail=str(exc)[:120],
        )
        slot_status.setdefault("motor_cortex_state", "degraded")
        slot_status.setdefault("cerebellum_state", "degraded")
        slot_status.setdefault("glymphatic_state", "degraded")
        slot_status.setdefault("vestibular_state", "degraded")

    runtime_summary = _build_runtime_summary(
        current_topic=normalized_topic,
        sys_mode=sys_mode,
        activity_monitor=activity_monitor,
        bond_state=bond_state,
        bond_summary=bond_summary,
        offline_state=offline_state,
        silence_state=silence_state,
        body_sense=body_sense,
        self_reflection_pulse=self_reflection_pulse,
        bond_narrative=bond_narrative,
        motive_state=motive_state,
        response_policy=response_policy,
        slot_status=slot_status,
        degraded_reasons=degraded_reasons,
    )
    background_runtime = _build_background_runtime_snapshot(
        now_ms=now_ms,
        persona_user_id=persona_user_id,
        inner_world_state=inner_world_state,
    )

    return {
        "persona_user_id": persona_user_id,
        "current_topic": normalized_topic,
        "sys_mode": sys_mode,
        "hard_state": hard_state,
        "activity_monitor": activity_monitor,
        "emotion_context": emotion_context,
        "endocrine_state": endocrine_state,
        "immune_state": immune_state,
        "bond_state": bond_state,
        "bond_summary": bond_summary,
        "bond_summary_human": bond_summary_human,
        "inner_world_state": inner_world_state,
        "offline_state": offline_state,
        "silence_state": silence_state,
        "body_sense": body_sense,
        "self_model_snapshot": self_model_snapshot,
        "self_reflection_pulse": self_reflection_pulse,
        "bond_narrative": bond_narrative,
        "motive_state": motive_state,
        "response_policy": response_policy,
        "somatic_state": somatic_state,
        "peripheral_state": peripheral_state,
        "circadian_state": circadian_state,
        "proprioception_state": proprioception_state,
        "initiative_decision": initiative_decision,
        "default_mode_state": default_mode_state,
        "mirror_neuron_state": mirror_neuron_state,
        "memory_layers": memory_layers,
        "language_dual_system": language_dual_system,
        "homeostasis_report": homeostasis_report,
        "interoception_state": interoception_state,
        "motor_cortex_state": motor_cortex_state,
        "cerebellum_state": cerebellum_state,
        "glymphatic_state": glymphatic_state,
        "vestibular_state": vestibular_state,
        "runtime_summary": runtime_summary,
        "background_runtime": background_runtime,
        "slot_status": slot_status,
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "timestamp": now_ms,
    }


def run_proactive_intent_dispatch(
    system_intent: dict[str, Any],
    *,
    logger: Any,
    get_ledger: Callable[[], Any],
    retention_policy: Any,
    ui_pusher: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    from host_brain.skills.impl.proactive_talk import execute_proactive_talk  # noqa: PLC0415

    skill_name = str(system_intent.get("skill_name", "") or "").strip()
    args = dict(system_intent.get("args") or {})
    if skill_name == "audit_retention":
        try:
            from host_brain.audit.retention import RetentionPolicy, run_retention_policy  # noqa: PLC0415
            from host_brain.core.ids import new_request_id as _new_id  # noqa: PLC0415

            policy = retention_policy if retention_policy is not None else RetentionPolicy()
            request_id = str(system_intent.get("request_id") or _new_id())
            result = run_retention_policy(
                ledger=get_ledger(),
                policy=policy,
                request_id=request_id,
                now_ts_ms=int(_time.time() * 1000),
            )
            payload = result.as_dict()
            payload["status"] = "OK" if result.success else "FAILED"
            return payload
        except Exception as retention_exc:
            logger.warning("audit_retention 调度执行失败: %s", retention_exc)
            return {
                "status": "FAILED",
                "reason_code": "AUDIT_RETENTION_FAILED",
                "detail": str(retention_exc),
            }

    if skill_name == "long_term_memory_distillation":
        return run_long_term_distillation_handler(args, {})

    if skill_name == "evolution_cycle":
        return _run_evolution_cycle(system_intent=system_intent, logger=logger)

    if skill_name != "proactive_talk":
        personality_result = _run_personality_scheduler_skill(
            system_intent=system_intent,
            logger=logger,
            get_ledger=get_ledger,
            ui_pusher=ui_pusher,
        )
        if personality_result is not None:
            return personality_result
        return {"status": "SKIPPED", "reason_code": "UNKNOWN_SKILL", "skill_name": skill_name}

    last_interaction_ts = _load_last_interaction_ts(get_ledger=get_ledger)

    bond_summary: dict[str, Any] = {}
    try:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        bond_summary = get_social_brain_agent().resolve_scheduler_bond_summary(session_id="default")
    except Exception:
        pass

    recent_topics = _load_recent_topics(get_ledger=get_ledger)
    diary_snippet = _load_recent_diary_snippet()
    dream_snippet = _load_recent_dream_snippet(get_ledger=get_ledger)
    llm_generator = _build_llm_generator(
        logger=logger,
        recent_topics=recent_topics,
        diary_snippet=diary_snippet,
        dream_snippet=dream_snippet,
    )

    emotion_context: dict[str, Any] = {}
    try:
        from host_brain.app.emotion_context import (  # noqa: PLC0415
            _global_emotion_timeline,
            get_user_emotion_profile,
        )

        emotion_context["emotion_tag"] = system_intent.get("emotion_tag", "NEUTRAL")
        emotion_context["mood_state"] = system_intent.get("mood_state", "steady")
        profile = get_user_emotion_profile()
        emotion_context["user_emotion_profile"] = profile.to_summary()
        try:
            emotion_context["emotion_timeline_summary"] = _global_emotion_timeline.rebuild_summary(
                window_ms=6 * 3600 * 1000
            )
        except Exception:
            pass
        try:
            diary = _global_emotion_timeline.build_daily_diary(window_ms=24 * 3600 * 1000)
            if diary.get("diary_text"):
                emotion_context["emotion_diary"] = {
                    "diary_text": str(diary["diary_text"])[:120],
                    "dominant_emotion": diary.get("dominant_emotion"),
                }
        except Exception:
            pass
    except Exception as emotion_exc:
        logger.debug("方向B: 情感上下文读取异常（忽略）: %s", emotion_exc)

    persona_user_id = ""
    try:
        from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415

        persona_user_id = str(get_canonical_bond_user_id() or "").strip()
    except Exception:
        persona_user_id = ""

    resolved_sys_mode = str(
        system_intent.get("sys_mode")
        or "normal"
    ).strip().lower() or "normal"

    motive_state: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        current_topic = recent_topics[0] if recent_topics else ""
        pending_topics = list(recent_topics[:3])
        motive_state = get_limbic_agent().compute_runtime_motive_state(
            persona_user_id=persona_user_id,
            current_topic=current_topic,
            bond_summary=bond_summary,
            emotion_context=emotion_context,
            pending_topics=pending_topics,
            runtime_flags={"source": "proactive_talk", "sys_mode": resolved_sys_mode},
        )
    except Exception as motive_exc:
        logger.debug("proactive motive_state compute failed（忽略）: %s", motive_exc)

    response_policy: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        current_topic = recent_topics[0] if recent_topics else ""
        limbic_agent = get_limbic_agent()
        offline_state = limbic_agent.resolve_offline_state(person_id=persona_user_id or "user_1")
        silence_state = limbic_agent.resolve_silence_state(current_topic=current_topic)
        body_sense = limbic_agent.resolve_body_sense_state()
        response_policy = limbic_agent.decide_response_policy(
            silence_state=silence_state,
            body_sense=body_sense,
            offline_state=offline_state,
            bond_summary=bond_summary,
            emotion_context=emotion_context,
            motive_state=motive_state,
            user_message=current_topic,
            runtime_flags={"source": "proactive_talk", "sys_mode": resolved_sys_mode},
        )
    except Exception as policy_exc:
        logger.debug("proactive response_policy compute failed（忽略）: %s", policy_exc)

    context = {
        "sys_mode": resolved_sys_mode,
        "request_id": system_intent.get("request_id"),
        "intent_id": system_intent.get("intent_id"),
        "ui_pusher": ui_pusher,
        "last_interaction_ts": last_interaction_ts,
        "bond_summary": bond_summary,
        "llm_generator": llm_generator,
        "user_name": "主人",
        "emotion_context": emotion_context,
        "motive_state": motive_state,
        "response_policy": response_policy,
        "scene_awareness": system_intent.get("scene_awareness") or args.get("scene_awareness") or {},
    }
    return execute_proactive_talk(args=args, context=context)


def _run_personality_scheduler_skill(
    *,
    system_intent: dict[str, Any],
    logger: Any,
    get_ledger: Callable[[], Any],
    ui_pusher: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any] | None:
    try:
        from host_brain.skills.personality_registry import build_personality_skill_registry  # noqa: PLC0415
    except Exception as exc:
        logger.warning("Personality skill registry unavailable: %s", exc)
        return None

    skill_name = str(system_intent.get("skill_name") or "").strip()
    if not skill_name:
        return None

    try:
        registry = build_personality_skill_registry()
        meta = registry.get(skill_name)
    except Exception as exc:
        logger.warning("Personality skill registry build failed: %s", exc)
        return {
            "status": "FAILED",
            "reason_code": "PERSONALITY_REGISTRY_FAILED",
            "skill_name": skill_name,
            "detail": str(exc),
        }

    if meta is None:
        return None

    if not bool(getattr(meta, "scheduler_allowed", False)):
        return {
            "status": "REJECTED",
            "reason_code": "SKILL_NOT_SCHEDULER_ALLOWED",
            "skill_name": skill_name,
        }

    emotion_context = {
        "emotion_tag": str(system_intent.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL",
        "mood_state": str(system_intent.get("mood_state") or "steady").strip().lower() or "steady",
        "triggered_emotions": list(system_intent.get("triggered_emotions") or []),
    }

    bond_summary: dict[str, Any] = {}
    try:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        bond_summary = get_social_brain_agent().resolve_scheduler_bond_summary(session_id="default")
    except Exception as exc:
        logger.debug("personality skill bond summary unavailable: %s", exc)

    context = {
        "request_id": system_intent.get("request_id"),
        "intent_id": system_intent.get("intent_id"),
        "task_id": system_intent.get("task_id"),
        "skill_name": skill_name,
        "logger": logger,
        "get_ledger": get_ledger,
        "ui_pusher": ui_pusher,
        "system_intent": dict(system_intent),
        "bond_summary": bond_summary,
        "emotion_context": emotion_context,
        "scene_awareness": system_intent.get("scene_awareness") or {},
    }

    args = dict(system_intent.get("args") or {})
    try:
        result = meta.handler(args, context)
    except Exception as exc:
        logger.warning("Personality scheduler skill failed: skill=%s err=%s", skill_name, exc)
        return {
            "status": "FAILED",
            "reason_code": "PERSONALITY_SKILL_FAILED",
            "skill_name": skill_name,
            "detail": str(exc),
        }

    if not isinstance(result, dict):
        return {
            "status": "FAILED",
            "reason_code": "PERSONALITY_SKILL_INVALID_RESULT",
            "skill_name": skill_name,
            "detail": f"expected dict result, got {type(result).__name__}",
        }

    payload = dict(result)
    payload.setdefault("status", "OK")
    payload.setdefault("skill_name", skill_name)
    return payload


def run_long_term_distillation_handler(args: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    try:
        from host_brain.app.dependencies import get_episodic_store, get_ledger as _get_ledger  # noqa: PLC0415
        from host_brain.memory.long_term_distiller import LongTermDistiller  # noqa: PLC0415

        episodic_store = get_episodic_store()
        ledger = _get_ledger()
        top_k = int((args or {}).get("top_k") or 5)
        max_props = int((args or {}).get("max_proposals") or 5)
        distiller = LongTermDistiller(
            episodic_store=episodic_store,
            ledger=ledger,
            max_proposals_per_cycle=max_props,
        )
        result = distiller.run_distillation_cycle(top_k=top_k)
        return {"status": result.get("status", "OK"), "result": result}
    except Exception as exc:
        return {"status": "DEGRADED", "error": str(exc)[:200]}


def _run_evolution_cycle(*, system_intent: dict[str, Any], logger: Any) -> dict[str, Any]:
    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

        return get_prefrontal_agent().run_evolution_cycle(
            system_intent=system_intent,
            logger=logger,
        )
    except Exception as exc:
        logger.warning("evolution_cycle execution failed (degraded): %s", exc)
        return {
            "status": "DEGRADED",
            "reason_code": "EVOLUTION_CYCLE_FAILED",
            "detail": str(exc),
        }


def _load_recent_topics(*, get_ledger: Callable[[], Any]) -> list[str]:
    topics: list[str] = []
    try:
        ledger = get_ledger()
        db_path = getattr(ledger, "_db_path", None) or getattr(ledger, "db_path", None)
        if db_path:
            now_ms = int(_time.time() * 1000)
            since_ms = now_ms - 24 * 3600 * 1000
            conn = _sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT detail FROM memory_ops_log WHERE ts_ms >= ? AND op_kind='WRITE' "
                "AND status='SUCCESS' ORDER BY ts_ms DESC LIMIT 5",
                (since_ms,),
            ).fetchall()
            conn.close()
            for row in rows:
                detail = str(row[0] or "")[:80]
                if detail:
                    topics.append(detail)
    except Exception:
        pass
    return topics


def _load_last_interaction_ts(
    *,
    get_ledger: Callable[[], Any],
    now_ts_ms: int | None = None,
    max_future_skew_ms: int = 60_000,
) -> int | None:
    try:
        ledger = get_ledger()
        db_path = getattr(ledger, "_db_path", None) or getattr(ledger, "db_path", None)
        if not db_path:
            return None
        current_ts = int(now_ts_ms if now_ts_ms is not None else _time.time() * 1000)
        upper_bound = current_ts + max(0, int(max_future_skew_ms))
        conn = _sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT ts_ms FROM intent_log WHERE ts_ms > 0 AND ts_ms <= ? ORDER BY ts_ms DESC LIMIT 1",
                (upper_bound,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return int(row[0])
    except Exception:
        return None


def _load_recent_diary_snippet() -> str:
    try:
        today = _dt.date.today().isoformat()
        yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
        diary_base = Path(__file__).resolve().parents[2] / "runtime" / "diary" / "diary"
        for date_str in (today, yesterday):
            diary_path = diary_base / f"{date_str}.md"
            if diary_path.is_file():
                content = diary_path.read_text(encoding="utf-8", errors="replace")[:300].strip()
                if content:
                    return content[:120]
    except Exception:
        pass
    return ""


def _load_recent_dream_snippet(*, get_ledger: Callable[[], Any]) -> str:
    try:
        ledger = get_ledger()
        db_path = getattr(ledger, "_db_path", None) or getattr(ledger, "db_path", None)
        if db_path:
            conn = _sqlite3.connect(db_path)
            dream_rows = conn.execute(
                "SELECT detail FROM memory_ops_log WHERE op_kind='WRITE' "
                "AND status='SUCCESS' AND detail LIKE '%dream%' "
                "ORDER BY ts_ms DESC LIMIT 1"
            ).fetchall()
            conn.close()
            if dream_rows:
                return str(dream_rows[0][0] or "")[:100]
    except Exception:
        pass
    return ""


def _build_llm_generator(
    *,
    logger: Any,
    recent_topics: list[str],
    diary_snippet: str,
    dream_snippet: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _llm_generator(llm_input: dict[str, Any]) -> dict[str, Any]:
        templates = _load_template_mapping("http_hooks_runtime_helpers.proactive_talk_llm_prompt")
        time_period = llm_input.get("time_period", "evening")
        user_name = llm_input.get("user_name", "主人")
        topic_hint = llm_input.get("topic_hint") or ""
        bond = llm_input.get("bond_summary") or {}
        stage = bond.get("stage", "")
        hint = bond.get("capability_hint", "")
        topics_str = "、".join(recent_topics[:3]) if recent_topics else ""
        emotion_hint = str(llm_input.get("emotion_hint") or "").strip()
        motive_hint = str(llm_input.get("motive_hint") or "").strip()

        prompt_parts = [
            _mapping_text(
                templates,
                "time_period_line",
                time_period=time_period,
            ),
            _mapping_text(
                templates,
                "greeting_line",
                user_name=user_name,
            ),
        ]
        if stage:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "bond_stage_line",
                    stage=stage,
                    hint=hint,
                )
            )
        if emotion_hint:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "emotion_hint_line",
                    emotion_hint=emotion_hint,
                )
            )
        if motive_hint:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "motive_hint_line",
                    motive_hint=motive_hint,
                )
            )
        if dream_snippet:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "dream_snippet_line",
                    dream_snippet=dream_snippet[:60],
                )
            )
        elif diary_snippet:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "diary_snippet_line",
                    diary_snippet=diary_snippet[:60],
                )
            )
        elif topics_str:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "recent_topics_line",
                    topics_str=topics_str,
                )
            )
        if topic_hint:
            prompt_parts.append(
                _mapping_text(
                    templates,
                    "topic_hint_line",
                    topic_hint=topic_hint,
                )
            )
        prompt_parts.append(
            _mapping_text(
                templates,
                "requirements_line",
            )
        )

        prompt = _mapping_text(templates, "prompt_separator").join(prompt_parts)
        try:
            data = _json.dumps(
                {
                    "message": prompt,
                    "session_id": "proactive_talk_scheduler",
                    "user_id": "scheduler_internal",
                },
                ensure_ascii=False,
            ).encode()
            req = _urlreq.Request(
                "http://127.0.0.1:18080/v1/chat",
                data=data,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            events = []
            with _urlreq.urlopen(req, timeout=30) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    try:
                        events.append(_json.loads(line[6:]))
                    except Exception:
                        pass
            text = "".join(
                event.get("chunk", "")
                for event in events
                if event.get("phase") == "RENDER" and event.get("chunk")
            )
            return {"greeting": text, "content": text}
        except Exception as exc:
            logger.warning("proactive_talk LLM 生成失败: %s", exc)
            return {
                "greeting": "",
                "content": "",
                "fallback_to_template": True,
                "fallback_reason": "LLM_UNAVAILABLE",
            }

    return _llm_generator
