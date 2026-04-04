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
from typing import Any, Callable


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


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_absence_band(offline_state: dict[str, Any]) -> str:
    raw_band = str(offline_state.get("absence_band") or "").strip()
    if raw_band in {"recent", "6h_plus", "24h_plus", "72h_plus", "7d_plus"}:
        return raw_band
    offline_hours = _coerce_float(offline_state.get("offline_hours"), 0.0)
    if offline_hours >= 7 * 24:
        return "7d_plus"
    if offline_hours >= 72:
        return "72h_plus"
    if offline_hours >= 24:
        return "24h_plus"
    if offline_hours >= 6:
        return "6h_plus"
    return "recent"


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
    absence_band = _resolve_absence_band(offline_state)
    bond_stage = str(
        bond_state.get("current_stage")
        or bond_summary.get("stage")
        or "UNKNOWN"
    ).strip() or "UNKNOWN"
    willingness_level = str(silence_state.get("willingness_level") or "NORMAL").strip().upper() or "NORMAL"
    fatigue_level = str(body_sense.get("fatigue_level") or "UNKNOWN").strip().upper() or "UNKNOWN"
    dominant_motive = str(motive_state.get("dominant_motive") or "unknown").strip() or "unknown"
    reply_posture = _derive_reply_posture(response_policy)

    headline_parts: list[str] = [f"mode={sys_mode}"]
    if bond_stage and bond_stage != "UNKNOWN":
        headline_parts.append(f"bond={bond_stage}")
    if absence_band != "recent":
        headline_parts.append(f"offline={absence_band}")
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

    return {
        "headline": " | ".join(part for part in headline_parts if part),
        "current_state": {
            "sys_mode": sys_mode,
            "bond_stage": bond_stage,
            "absence_band": absence_band,
            "willingness_level": willingness_level,
            "fatigue_level": fatigue_level,
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

        monitor = getattr(_deps, "_activity_monitor_ref", None)
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

    bond_state: dict[str, Any] = {}
    bond_summary: dict[str, Any] = {}
    bond_summary_human: dict[str, Any] = {}
    bond_state_obj: Any = None
    try:
        from host_brain.app.dependencies import get_bond_stage_engine  # noqa: PLC0415
        from host_brain.bond.summarizer import derive_bond_summary  # noqa: PLC0415

        bond_engine = get_bond_stage_engine()
        if bond_engine is not None:
            bond_state_obj = bond_engine.get_state(persona_user_id)
            bond_summary = derive_bond_summary(None, bond_score_state=bond_state_obj)
            bond_summary_human = _as_plain_dict(bond_state_obj.to_summary() if hasattr(bond_state_obj, "to_summary") else bond_state_obj)
            bond_state = {
                "user_id": persona_user_id,
                "current_stage": str(getattr(bond_state_obj, "current_stage", "") or "").strip() or "STRANGER",
                "trend": (
                    bond_state_obj.trend()
                    if callable(getattr(bond_state_obj, "trend", None))
                    else str((bond_summary or {}).get("trend") or "")
                ),
                "trend_intensity": (
                    bond_state_obj.trend_intensity()
                    if callable(getattr(bond_state_obj, "trend_intensity", None))
                    else ""
                ),
                "capability_hint": (
                    bond_state_obj.capability_hint()
                    if callable(getattr(bond_state_obj, "capability_hint", None))
                    else str((bond_summary or {}).get("capability_hint") or "")
                ),
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
        from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415
        from host_brain.persona.motive_runtime import build_offline_state_from_inner_state  # noqa: PLC0415

        inner_world_state = dict(get_inner_world().get_state(persona_user_id) or {})
        offline_state = dict(build_offline_state_from_inner_state(inner_world_state) or {})
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
        from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415

        silence_state = _as_plain_dict(
            get_silence_engine().get_silence_state(current_topic=normalized_topic)
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
        from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

        body_sense = _as_plain_dict(get_body_sense_model().get_state())
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
        from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415
        from host_brain.persona.self_reflection import build_self_reflection_pulse  # noqa: PLC0415

        persona_engine = PersonaEngine.get_instance()
        snapshot_obj = persona_engine.get_self_model_snapshot(
            persona_user_id,
            timeline_summary=timeline_summary,
            timeline_diary=timeline_diary,
            bond_state=bond_state_obj,
            bond_summary=bond_summary,
            last_deep_talk_ts_ms=int(getattr(bond_state_obj, "last_event_ts_ms", 0) or 0),
            current_ts_ms=now_ms,
        )
        self_model_snapshot = _as_plain_dict(snapshot_obj)
        if self_model_snapshot:
            self_reflection_pulse = dict(
                build_self_reflection_pulse(
                    self_model_snapshot,
                    trigger_reason="runtime_snapshot",
                )
                or {}
            )
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
        from host_brain.persona.motive_engine import MotiveInput, compute_motive_state  # noqa: PLC0415

        motive_state = compute_motive_state(
            MotiveInput(
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
        ).as_dict()
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
        from host_brain.persona.response_policy import ResponsePolicyInput, decide_response_policy  # noqa: PLC0415

        response_policy = decide_response_policy(
            ResponsePolicyInput(
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
        ).as_dict()
        slot_status["response_policy"] = "live"
    except Exception as exc:
        _record_snapshot_status(
            slot_status,
            degraded_reasons,
            slot="response_policy",
            status="degraded",
            detail=str(exc)[:120],
        )

    runtime_summary = _build_runtime_summary(
        current_topic=normalized_topic,
        sys_mode=sys_mode,
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

    return {
        "persona_user_id": persona_user_id,
        "current_topic": normalized_topic,
        "sys_mode": sys_mode,
        "hard_state": hard_state,
        "activity_monitor": activity_monitor,
        "emotion_context": emotion_context,
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
        "runtime_summary": runtime_summary,
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

    skill_name = system_intent.get("skill_name", "")
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

    if skill_name != "proactive_talk":
        if skill_name == "evolution_cycle":
            return _run_evolution_cycle(system_intent=system_intent, logger=logger)
        return {"status": "SKIPPED", "reason_code": "UNKNOWN_SKILL", "skill_name": skill_name}

    args = dict(system_intent.get("args") or {})

    last_interaction_ts = None
    try:
        ledger = get_ledger()
        db_path = getattr(ledger, "_db_path", None) or getattr(ledger, "db_path", None)
        if db_path:
            conn = _sqlite3.connect(db_path)
            row = conn.execute("SELECT ts_ms FROM intent_log ORDER BY ts_ms DESC LIMIT 1").fetchone()
            conn.close()
            if row:
                last_interaction_ts = int(row[0])
    except Exception:
        pass

    bond_summary: dict[str, Any] = {}
    try:
        from host_brain.app.dependencies import get_bond_stage_engine as _get_bse  # noqa: PLC0415

        bse = _get_bse()
        if bse is not None:
            bond_state = bse.get_bond_state(session_id="default")
            if bond_state is not None:
                bond_summary = {
                    "stage": str(getattr(bond_state, "stage", "") or "").strip(),
                    "trend": str(getattr(bond_state, "trend", "") or "").strip(),
                    "capability_hint": str(getattr(bond_state, "capability_hint", "") or "").strip(),
                }
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
        or system_intent.get("mood_state")
        or "normal"
    ).strip().lower() or "normal"

    motive_state: dict[str, Any] = {}
    try:
        from host_brain.persona.motive_runtime import compute_runtime_motive_state  # noqa: PLC0415

        current_topic = recent_topics[0] if recent_topics else ""
        pending_topics = list(recent_topics[:3])
        motive_state = compute_runtime_motive_state(
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
        from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415
        from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415
        from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415
        from host_brain.persona.motive_runtime import build_offline_state_from_inner_state  # noqa: PLC0415
        from host_brain.persona.response_policy import ResponsePolicyInput, decide_response_policy  # noqa: PLC0415

        current_topic = recent_topics[0] if recent_topics else ""
        inner_state = get_inner_world().get_state(persona_user_id or "user_1")
        offline_state = build_offline_state_from_inner_state(inner_state)
        silence_state = get_silence_engine().get_silence_state(current_topic=current_topic).as_dict()
        body_sense = get_body_sense_model().get_state().as_dict()
        response_policy = decide_response_policy(
            ResponsePolicyInput(
                silence_state=silence_state,
                body_sense=body_sense,
                offline_state=offline_state,
                bond_summary=bond_summary,
                emotion_context=emotion_context,
                motive_state=motive_state,
                user_message=current_topic,
                runtime_flags={"source": "proactive_talk", "sys_mode": resolved_sys_mode},
            )
        ).as_dict()
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
        from host_brain.app.dependencies import get_bond_stage_engine as _get_bse_evo  # noqa: PLC0415
        from host_brain.app.emotion_context import _global_emotion_timeline  # noqa: PLC0415
        from host_brain.persona.evolution_orchestrator import EvolutionOrchestrator  # noqa: PLC0415

        orchestrator = EvolutionOrchestrator()
        args = dict(system_intent.get("args") or {})
        user_id = str(args.get("user_id") or "user_1")
        date = str(args.get("date") or _dt.date.today().isoformat())

        timeline_summary: dict[str, Any] = {}
        timeline_diary: dict[str, Any] = {}
        try:
            timeline_summary = _global_emotion_timeline.rebuild_summary(window_ms=24 * 3600 * 1000)
            timeline_diary = _global_emotion_timeline.build_daily_diary(window_ms=24 * 3600 * 1000)
        except Exception:
            pass

        bond_state = None
        bond_summary: dict[str, Any] = {}
        try:
            bse = _get_bse_evo()
            if bse is not None:
                bond_state = bse.get_bond_state(session_id="default")
                if bond_state is not None:
                    bond_summary = {
                        "stage": str(getattr(bond_state, "stage", "") or "").strip(),
                        "trend": str(getattr(bond_state, "trend", "") or "").strip(),
                        "capability_hint": str(getattr(bond_state, "capability_hint", "") or "").strip(),
                    }
        except Exception:
            pass

        result = orchestrator.run_evolution_cycle(
            user_id=user_id,
            date=date,
            timeline_summary=timeline_summary,
            timeline_diary=timeline_diary,
            bond_state=bond_state,
            bond_summary=bond_summary,
        )
        logger.info(
            "进化周期完成: date=%s traits_changed=%s errors=%d",
            date,
            result.traits_changed,
            len(result.errors),
        )
        return {
            "status": "OK",
            "cycle_id": result.cycle_id,
            "traits_changed": result.traits_changed,
            "growth_awareness": result.growth_awareness,
            "errors": result.errors,
        }
    except Exception as exc:
        logger.warning("evolution_cycle 执行失败（降级）: %s", exc)
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
            f"你是夏目藍，现在是{time_period}时段。",
            f"请主动发起一句话问候{user_name}。",
        ]
        if stage:
            prompt_parts.append(f"当前关系阶段：{stage}，{hint}。")
        if emotion_hint:
            prompt_parts.append(f"你的情感状态：{emotion_hint}")
        if motive_hint:
            prompt_parts.append(f"你当前的主动动机：{motive_hint}")
        if dream_snippet:
            prompt_parts.append(f"你昨晚做了一个梦，可以自然地提起：{dream_snippet[:60]}。")
        elif diary_snippet:
            prompt_parts.append(f"你写了日记，可以自然地提起今天的感受：{diary_snippet[:60]}。")
        elif topics_str:
            prompt_parts.append(f"可以围绕最近话题展开：{topics_str}。")
        if topic_hint:
            prompt_parts.append(f"话题提示：{topic_hint}。")
        prompt_parts.append("要求：自然、温暖、20字以内，不要重复打招呼模板。")

        prompt = "".join(prompt_parts)
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
            return {}

    return _llm_generator
