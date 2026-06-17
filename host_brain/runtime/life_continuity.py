from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.runtime.life_organs import (
    ORGAN_SCHEMA_SQL,
    append_life_experience,
    close_pending_proactive_episode_for_user_reply,
    get_recent_life_experience,
    get_recent_proactive_episodes,
    get_organ_runtime_state,
    open_proactive_episode,
    organ_votes_from_runtime,
    synthesize_life_state_from_organs,
)
from host_brain.runtime.autonomous_self import (
    apply_autonomous_self_cycle,
    build_adaptive_self_pack,
)

LOGGER = logging.getLogger("Natsume.life_continuity")
DEFAULT_PERSON_ID = "default"
_SCHEMA_READY = False
_SCHEMA_READY_CONNECTIONS: set[int] = set()
_LOCK = threading.RLock()
_LIFE_LOOP_TASK: asyncio.Task[Any] | None = None
_LIFE_LOOP_STOP: asyncio.Event | None = None
_LAST_TICK_RESULT: dict[str, Any] = {}

SCHEMA_SQL: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS digital_life_state (person_id TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 1, state_json TEXT NOT NULL, updated_at_ms INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS digital_life_state_event (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, person_id TEXT NOT NULL, source_kind TEXT NOT NULL, event_type TEXT NOT NULL, delta_json TEXT NOT NULL DEFAULT '{}', state_before_json TEXT NOT NULL DEFAULT '{}', state_after_json TEXT NOT NULL DEFAULT '{}', visible_channels_json TEXT NOT NULL DEFAULT '[]', created_at_ms INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS dialogue_life_influence_log (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, request_id TEXT, person_id TEXT NOT NULL, session_id TEXT, user_message TEXT NOT NULL DEFAULT '', reply_text TEXT NOT NULL DEFAULT '', life_director_json TEXT NOT NULL DEFAULT '{}', life_causality_json TEXT NOT NULL DEFAULT '{}', created_at_ms INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS embodiment_dispatch_log (id INTEGER PRIMARY KEY AUTOINCREMENT, dispatch_id TEXT NOT NULL UNIQUE, person_id TEXT NOT NULL, source_kind TEXT NOT NULL, target_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'UNKNOWN', reason_code TEXT, result_json TEXT NOT NULL DEFAULT '{}', created_at_ms INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS work_environment_event (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, person_id TEXT NOT NULL, session_id TEXT, request_id TEXT, event_type TEXT NOT NULL, work_id TEXT, summary TEXT NOT NULL DEFAULT '', urgency TEXT NOT NULL DEFAULT 'medium', emotional_effect_json TEXT NOT NULL DEFAULT '{}', created_at_ms INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1)",
    "CREATE TABLE IF NOT EXISTS proactive_life_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, outbox_id TEXT NOT NULL UNIQUE, person_id TEXT NOT NULL, source_kind TEXT NOT NULL, motive TEXT NOT NULL DEFAULT '', pressure REAL NOT NULL DEFAULT 0.0, target_json TEXT NOT NULL DEFAULT '{}', message_json TEXT NOT NULL DEFAULT '{}', gate_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'UNKNOWN', reason_code TEXT, result_json TEXT NOT NULL DEFAULT '{}', created_at_ms INTEGER NOT NULL)",
    *ORGAN_SCHEMA_SQL,
    "CREATE INDEX IF NOT EXISTS idx_digital_life_state_event_person_ts ON digital_life_state_event(person_id, created_at_ms DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dialogue_life_influence_request ON dialogue_life_influence_log(request_id)",
    "CREATE INDEX IF NOT EXISTS idx_dialogue_life_influence_person_ts ON dialogue_life_influence_log(person_id, created_at_ms DESC)",
    "CREATE INDEX IF NOT EXISTS idx_embodiment_dispatch_person_ts ON embodiment_dispatch_log(person_id, created_at_ms DESC)",
    "CREATE INDEX IF NOT EXISTS idx_work_environment_person_ts ON work_environment_event(person_id, created_at_ms DESC)",
    "CREATE INDEX IF NOT EXISTS idx_proactive_life_outbox_person_ts ON proactive_life_outbox(person_id, created_at_ms DESC)",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _event_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return copy.deepcopy(default)
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    try:
        return json.loads(str(value))
    except Exception:
        return copy.deepcopy(default)


def _map(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except Exception:
        return float(default)


def _clamp(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _bool_env(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _int_env(name: str, default: int, minimum: int = 0, maximum: int = 86400) -> int:
    try:
        value = int(str(os.environ.get(name) or default).strip())
    except Exception:
        value = int(default)
    return max(minimum, min(maximum, value))


def _conn(ledger: Any) -> Any:
    conn = getattr(ledger, "_conn", None)
    if conn is None:
        raise RuntimeError("ledger sqlite connection unavailable")
    return conn


def ensure_life_schema(ledger: Any) -> None:
    global _SCHEMA_READY
    conn = _conn(ledger)
    conn_id = id(conn)
    if conn_id in _SCHEMA_READY_CONNECTIONS:
        return
    with _LOCK:
        if conn_id in _SCHEMA_READY_CONNECTIONS:
            return
        for sql in SCHEMA_SQL:
            conn.execute(sql)
        conn.commit()
        _SCHEMA_READY_CONNECTIONS.add(conn_id)
        _SCHEMA_READY = True


def _day_phase(ts_ms: int) -> str:
    hour = time.localtime(int(ts_ms) / 1000.0).tm_hour
    if 5 <= hour < 10:
        return "morning"
    if 10 <= hour < 18:
        return "day"
    if 18 <= hour < 23:
        return "night"
    return "late_night"


def _default_state(person_id: str, now_ms: int | None = None) -> dict[str, Any]:
    now = int(now_ms or _now_ms())
    return {
        "person_id": person_id or DEFAULT_PERSON_ID,
        "version": 1,
        "runtime": {"alive_status": "alive", "last_integrated_ms": now, "last_heartbeat_ms": now, "missed_tick_count": 0, "catchup_count": 0},
        "clock": {"last_tick_ms": now, "last_user_seen_ms": now, "idle_minutes": 0, "day_phase": _day_phase(now)},
        "environment": {},
        "affect": {"mood": "calm", "valence": 0.18, "arousal": 0.24, "energy": 0.62, "loneliness": 0.0, "attachment_warmth": 0.42, "irritation": 0.0, "fatigue": 0.12, "tension": 0.0, "pride": 0.0, "relief": 0.0},
        "needs": {"contact": 0.12, "rest": 0.12, "play": 0.18, "autonomy": 0.25, "memory_consolidation": 0.2},
        "inner_world": {"current_preoccupation": "", "attention_target": "", "unresolved_loop": "", "felt_tendency": "", "dialogue_bias": "", "expires_at_ms": 0, "preoccupation_expires_at_ms": 0, "attention_kind": "idle", "monologue_due": False, "monologue_suppression_reason": "", "dream_residue": "", "diary_residue": "", "thought_seed": "", "private_note": "", "numeric_state": {}},
        "life_pressure": {"absence": 0.0, "dream": 0.0, "work": 0.0, "fatigue": 0.12, "relationship": 0.28},
        "work": {"active": False, "last_event_type": "", "work_id": "", "summary": "", "urgency": "medium", "updated_at_ms": 0},
        "motives": [],
        "continuity": {"last_interaction_digest": "", "last_visible_event_id": "", "last_private_event_id": "", "last_body_state_id": "", "last_episode_id": "", "open_loop_count": 0, "last_dream_id": "", "last_diary_id": ""},
        "embodiment": {"expression": "calm_idle", "idle_animation": "quiet_breath", "motion_bias": "low_random", "voice_tone": "soft_neutral", "mateengine_state": "idle", "emotion_tag": "NEUTRAL", "last_dispatch_ms": 0, "last_dispatch_id": ""},
        "life_action": {},
        "proactive": {"last_target": {}, "last_attempt_ms": 0, "last_sent_ms": 0, "last_outbox_id": "", "last_status": "", "last_reason_code": "", "last_motive": ""},
        "autonomy": {"alive_status": "alive", "last_autonomous_cycle_ms": now, "last_drive_tick_ms": now, "last_attention_tick_ms": now, "last_learning_cycle_ms": now, "current_attention": "body_presence", "active_agenda_count": 0, "body_loop_status": "state_driven", "user_input_required_for_progress": False},
        "self_growth": {"active_projects": [], "adaptive_traits": {}, "recent_growth_signals": [], "current_learning_focus": "", "last_learning_cycle_ms": 0},
        "self_projects": [],
        "learning_state": {"last_learning_cycle_ms": 0, "recent_clusters": [], "learning_signals": [], "self_reward": {}},
    }


def _normalize_state(state: Mapping[str, Any], person_id: str) -> dict[str, Any]:
    merged = _default_state(person_id)
    for key, value in dict(state).items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key].update(dict(value))
        else:
            merged[key] = copy.deepcopy(value)
    merged["person_id"] = person_id or DEFAULT_PERSON_ID
    merged["version"] = int(merged.get("version") or 1)
    return _normalize_inner_world_continuity(_suppress_stale_work_pressure(merged))


def _save_state(ledger: Any, state: Mapping[str, Any]) -> None:
    payload = dict(state or {})
    person_id = str(payload.get("person_id") or DEFAULT_PERSON_ID)
    _conn(ledger).execute(
        "INSERT INTO digital_life_state(person_id, version, state_json, updated_at_ms) VALUES(?, ?, ?, ?) ON CONFLICT(person_id) DO UPDATE SET version=excluded.version, state_json=excluded.state_json, updated_at_ms=excluded.updated_at_ms",
        (person_id, int(payload.get("version") or 1), _dumps(payload), _now_ms()),
    )
    _conn(ledger).commit()


def _append_state_event(ledger: Any, person_id: str, source_kind: str, event_type: str, before: Mapping[str, Any], after: Mapping[str, Any], delta: Mapping[str, Any], visible: Sequence[str]) -> str:
    event_id = _event_id("lifeevt")
    _conn(ledger).execute(
        "INSERT INTO digital_life_state_event(event_id, person_id, source_kind, event_type, delta_json, state_before_json, state_after_json, visible_channels_json, created_at_ms) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, person_id, source_kind, event_type, _dumps(dict(delta or {})), _dumps(dict(before or {})), _dumps(dict(after or {})), _dumps(list(visible or [])), _now_ms()),
    )
    _conn(ledger).commit()
    return event_id


def get_life_state(ledger: Any, person_id: str = DEFAULT_PERSON_ID) -> dict[str, Any]:
    ensure_life_schema(ledger)
    resolved = str(person_id or DEFAULT_PERSON_ID)
    row = _conn(ledger).execute("SELECT state_json FROM digital_life_state WHERE person_id=?", (resolved,)).fetchone()
    if row:
        state = _loads(row[0], {})
        if isinstance(state, dict) and state:
            return _normalize_state(state, resolved)
    state = _default_state(resolved)
    _save_state(ledger, state)
    _append_state_event(ledger, resolved, "init", "state_initialized", {}, state, {"created": True}, ["status_panel"])
    return copy.deepcopy(state)


def _pressures(state: Mapping[str, Any]) -> dict[str, float]:
    raw = _map(state.get("life_pressure"))
    work_pressure = _clamp(raw.get("work"))
    work = _map(state.get("work"))
    if not _work_state_can_drive_life(work):
        work_pressure = 0.0
    return {"absence": _clamp(raw.get("absence")), "dream": _clamp(raw.get("dream")), "work": work_pressure, "fatigue": _clamp(raw.get("fatigue")), "relationship": _clamp(raw.get("relationship"))}


def _work_state_is_stale(work: Mapping[str, Any]) -> bool:
    event_type = str(_map(work).get("last_event_type") or "").strip()
    return event_type == "task_stale" or bool(_map(work).get("expired_at_ms"))


def _work_state_can_drive_life(work: Mapping[str, Any]) -> bool:
    value = _map(work)
    event_type = str(value.get("last_event_type") or "").strip()
    if _work_state_is_stale(value):
        return False
    if event_type in {"task_done", "task_failed", "task_blocked"}:
        return True
    return bool(value.get("active"))


def _life_action_is_stale_work(action: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    kind = str(_map(action).get("action_kind") or "").strip()
    if not kind.startswith("work"):
        return False
    return not _work_state_can_drive_life(_map(state.get("work")))


def _suppress_stale_work_pressure(state: Mapping[str, Any]) -> dict[str, Any]:
    after = copy.deepcopy(dict(state or {}))
    if _work_state_can_drive_life(_map(after.get("work"))):
        return after
    pressure = _map(after.get("life_pressure"))
    pressure["work"] = 0.0
    after["life_pressure"] = pressure
    after["motives"] = [
        item
        for item in list(after.get("motives") or [])
        if not (isinstance(item, Mapping) and str(item.get("motive") or "").strip() == "work_progress_nudge")
    ]
    if _life_action_is_stale_work(_map(after.get("life_action")), after):
        after["life_action"] = {}
    return after


def _normalize_inner_world_continuity(state: Mapping[str, Any]) -> dict[str, Any]:
    after = copy.deepcopy(dict(state or {}))
    inner = _map(after.get("inner_world"))
    now = _now_ms()
    expires_at = int(_float(inner.get("preoccupation_expires_at_ms") or inner.get("expires_at_ms")))
    numeric = _map(inner.get("numeric_state"))
    numeric_anchor = _text(numeric.get("current_preoccupation") or numeric.get("attention_target"), 180)
    if numeric:
        inner["attention_kind"] = _text(numeric.get("attention_kind") or numeric.get("attention") or inner.get("attention_kind"), 80)
        inner["monologue_due"] = bool(numeric.get("monologue_due"))
        if numeric.get("monologue_suppression_reason"):
            inner["monologue_suppression_reason"] = _text(numeric.get("monologue_suppression_reason"), 160)
        if numeric.get("preoccupation_expires_at_ms"):
            expires_at = int(_float(numeric.get("preoccupation_expires_at_ms")))
            inner["preoccupation_expires_at_ms"] = expires_at
    if numeric_anchor:
        inner["current_preoccupation"] = numeric_anchor
        inner["attention_target"] = _text(numeric.get("attention_target") or numeric_anchor, 120)
    elif expires_at and now >= expires_at:
        inner["current_preoccupation"] = ""
        inner["attention_target"] = ""
        inner["unresolved_loop"] = ""
    if not _text(inner.get("dream_residue"), 240):
        pressure = _map(after.get("life_pressure"))
        pressure["dream"] = min(_clamp(pressure.get("dream")), 0.18)
        after["life_pressure"] = pressure
        if str(_map(after.get("life_action")).get("action_kind") or "") == "dream_afterglow":
            after["life_action"] = {}
    after["inner_world"] = inner
    return after


def _changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    fields: list[str] = []
    for root in ("clock", "affect", "needs", "inner_world", "life_pressure", "work", "embodiment"):
        b = _map(before.get(root)); a = _map(after.get(root))
        for key, value in a.items():
            if b.get(key) != value:
                fields.append(f"{root}.{key}")
    return fields[:18]


def _looks_work(text: str, task_mode: str = "") -> bool:
    joined = f"{text} {task_mode}".lower()
    if any(t in joined for t in ("修复", "重启", "验证", "测试", "提交", "推送", "拉取", "代码", "仓库", "项目", "开始吧", "继续", "做吧", "fix", "restart", "commit", "push", "pull", "test", "repo", "codex")):
        return True
    return False


def _asks_dream(text: str) -> bool:
    return any(t in str(text or "") for t in ("梦", "做梦", "梦境", "梦见", "昨晚"))


def _returns(text: str) -> bool:
    return any(t in str(text or "") for t in ("我回来了", "回来了", "在吗", "早", "晚安", "睡了吗", "醒了吗"))


def _scene(state: Mapping[str, Any], user_message: str = "", task_mode: str = "", response_move: str = "", reason: str = "dialogue") -> tuple[str, str, str]:
    p = _pressures(state); work = _map(state.get("work")); inner = _map(state.get("inner_world")); last = str(work.get("last_event_type") or "")
    can_drive_work = _work_state_can_drive_life(work)
    has_dream_residue = bool(_text(inner.get("dream_residue"), 240))
    if can_drive_work and last == "task_done" and p["work"] >= 0.25:
        return "work_done", "proud", "strong"
    if can_drive_work and last in {"task_failed", "task_blocked"} and p["work"] >= 0.25:
        return "work_blocked", "guarded", "strong"
    if (can_drive_work and (bool(work.get("active")) or p["work"] >= 0.35)) or _looks_work(user_message, task_mode):
        return "work_waiting", "curious_guarded", "strong"
    if _asks_dream(user_message) or (has_dream_residue and p["dream"] >= 0.55):
        return "dream_residue", "curious", "dominant" if _asks_dream(user_message) else "strong"
    if _returns(user_message) or p["absence"] >= 0.32:
        return "reconnect", "approach", "dominant"
    if p["fatigue"] >= 0.55:
        return "night_companion", "rest", "dominant"
    if reason in {"periodic_tick", "manual_tick", "startup_tick", "life_integrated", "body_dispatch"}:
        return ("idle", "quiet_presence", "medium") if max(p.values() or [0.0]) >= 0.18 else ("idle", "rest", "weak")
    return "daily_chat", "tease", "dominant"


def _embodiment(scene: str, motive: str, state: Mapping[str, Any]) -> dict[str, Any]:
    mood = str(_map(state.get("affect")).get("mood") or "calm")
    if scene == "work_done" or motive == "proud":
        return {"expression": "smug_smile", "idle_animation": "small_bounce", "motion_bias": "lively", "voice_tone": "bright_teasing", "mateengine_state": "happy", "emotion_tag": "HAPPY"}
    if scene in {"work_waiting", "work_progress"}:
        return {"expression": "thinking", "idle_animation": "quiet_focus", "motion_bias": "low_attention", "voice_tone": "focused_soft", "mateengine_state": "thinking", "emotion_tag": "THINKING"}
    if scene == "work_blocked" or motive == "guarded":
        return {"expression": "confused_guarded", "idle_animation": "small_head_tilt", "motion_bias": "low_attention", "voice_tone": "careful_low", "mateengine_state": "anxious", "emotion_tag": "CONFUSED"}
    if scene == "dream_residue":
        return {"expression": "soft_dreamy", "idle_animation": "slow_breath", "motion_bias": "low_slow", "voice_tone": "low_soft", "mateengine_state": "cozy", "emotion_tag": "LOVE"}
    if scene == "reconnect" or mood == "lonely":
        return {"expression": "soft_lonely", "idle_animation": "quiet_waiting", "motion_bias": "low_random", "voice_tone": "low_soft", "mateengine_state": "waiting", "emotion_tag": "SAD"}
    if scene == "night_companion" or mood == "tired":
        return {"expression": "sleepy", "idle_animation": "slow_breath", "motion_bias": "low_slow", "voice_tone": "soft_tired", "mateengine_state": "idle", "emotion_tag": "SAD"}
    if motive == "quiet_presence":
        return {"expression": "calm_idle", "idle_animation": "quiet_breath", "motion_bias": "low_slow", "voice_tone": "soft_neutral", "mateengine_state": "idle", "emotion_tag": "NEUTRAL"}
    if motive == "tease" or mood == "playful":
        return {"expression": "smug_smile", "idle_animation": "small_bounce", "motion_bias": "medium_lively", "voice_tone": "bright_teasing", "mateengine_state": "happy", "emotion_tag": "HAPPY"}
    return {"expression": "calm_idle", "idle_animation": "quiet_breath", "motion_bias": "low_random", "voice_tone": "soft_neutral", "mateengine_state": "idle", "emotion_tag": "NEUTRAL"}


def _effect(value: float) -> str:
    if value >= 0.66:
        return "strong"
    if value >= 0.35:
        return "medium"
    if value >= 0.12:
        return "weak"
    return "none"


def _organ_votes(state: Mapping[str, Any], scene: str, motive: str) -> dict[str, Any]:
    p = _pressures(state); votes: list[dict[str, Any]] = []
    def add(organ: str, vote: str, strength: float, field: str) -> None:
        if strength > 0.05:
            votes.append({"organ": organ, "vote": vote, "strength": round(_clamp(strength), 3), "field": field})
    add("prefrontal", "work_focus", p["work"], "work.active")
    add("cortex", "frontstage_backend_split", max(p["work"], 0.32 if scene.startswith("work") else 0.0), "scene")
    add("limbic", "warm_reconnect", max(p["absence"], p["relationship"] * 0.8), "affect.attachment_warmth")
    add("social_brain", "approach", p["absence"] * 0.9 + p["relationship"] * 0.25, "needs.contact")
    add("default_mode", "dream_residue", p["dream"], "inner_world.dream_residue")
    add("hippocampus", "continuity_memory", max(p["dream"] * 0.8, p["relationship"] * 0.45), "continuity")
    add("circadian", "tired_soft", p["fatigue"], "affect.fatigue")
    add("somatic", "low_motion", p["fatigue"] * 0.75, "embodiment.motion_bias")
    add("motor_cortex", "embodiment_target", max(p.values()) if p else 0.1, "embodiment")
    votes.sort(key=lambda x: float(x.get("strength") or 0.0), reverse=True)
    peak = max([float(v.get("strength") or 0.0) for v in votes] or [0.0])
    return {"mapped_organ_count": 23, "active_organs": [str(v["organ"]) for v in votes[:8]], "organ_votes": votes[:8], "top_influences": votes[:5], "winning_scene": scene, "winning_motive": motive, "dialogue_effect": _effect(peak), "embodiment_effect": _effect(peak)}


def _visible(scene: str, influence: str, p: Mapping[str, float]) -> list[str]:
    channels = ["status_panel"]
    if influence in {"strong", "dominant"} or scene != "idle":
        channels.append("dialogue")
    if _bool_env("HOST_LIFE_EMBODIMENT_ENABLED", True) and (max([float(v) for v in p.values()] or [0.0]) >= 0.18 or scene != "daily_chat"):
        channels.append("embodiment")
    if influence in {"strong", "dominant"}:
        channels.append("voice")
    proactive_pressure = max(float(p.get("absence") or 0.0), float(p.get("dream") or 0.0), float(p.get("work") or 0.0), float(p.get("fatigue") or 0.0))
    if proactive_pressure >= 0.32:
        channels.append("proactive")
    return list(dict.fromkeys(channels))


def _style_goal(scene: str) -> str:
    if scene.startswith("work"):
        return "像她正在前台盯着后台 Codex 的进度；可以有等待、担心、得意或松口气，但不要像助手汇报。"
    if scene == "reconnect":
        return "像她确实等了一会儿后重新接上用户；自然带一点靠近感，不要解释系统。"
    if scene == "dream_residue":
        return "像梦境留下了轻微余温；可以有一点飘忽或柔软，但不要把梦说成现实事实。"
    if scene == "night_companion":
        return "像深夜还在但有点困；语气软一点、动作慢一点。"
    return "像前台数字生命顺口回应；生命状态要能被感觉到，不要像助手总结。"


def build_life_director_pack(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, session_id: str = "", user_message: str = "", task_mode: str = "", response_move: str = "", reason: str = "dialogue", trace_stage_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    state = get_life_state(ledger, person_id or DEFAULT_PERSON_ID)
    scene, motive, influence = _scene(state, user_message, task_mode, response_move, reason)
    p = _pressures(state); emb = _embodiment(scene, motive, state)
    fallback_organ = _organ_votes(state, scene, motive)
    organ = organ_votes_from_runtime(ledger=ledger, person_id=person_id or DEFAULT_PERSON_ID, fallback=fallback_organ)
    organ["winning_scene"] = scene
    organ["winning_motive"] = motive
    work = _map(state.get("work")); summary = _text(work.get("summary"), 120)
    backend = f"Codex backend {work.get('last_event_type') or 'task_started'}: {summary}" if scene.startswith("work") and summary else ("Codex backend active" if scene.startswith("work") else "no active Codex backend work event")
    environment = _map(state.get("environment"))
    try:
        from host_brain.runtime.environment_state import private_mind_summary  # noqa: PLC0415
        private_mind = private_mind_summary(_map(state.get("inner_world")))
    except Exception:
        private_mind = {}
    inner_life_snapshot = _build_inner_life_snapshot_from_state(
        ledger=ledger,
        person_id=person_id or DEFAULT_PERSON_ID,
        state=state,
    )
    adaptive_self_pack = build_adaptive_self_pack(state)
    private_mind.update(
        {
            "attention_target": _text(_map(inner_life_snapshot.get("attention")).get("target"), 120),
            "current_preoccupation": _text(_map(inner_life_snapshot.get("preoccupation")).get("object"), 160),
            "dialogue_bias": _text(_map(inner_life_snapshot.get("expression_bias")).get("dialogue"), 240),
            "must_not_expose_raw_inner_text": True,
        }
    )
    return {
        "frontstage_role": "Natsume is the frontstage digital life; Codex is the work backend.",
        "codex_backend_status": backend,
        "state_version": int(state.get("version") or 1),
        "runtime": _map(state.get("runtime")),
        "environment": environment,
        "private_mind_summary": private_mind,
        "inner_life_snapshot": inner_life_snapshot,
        "adaptive_self_pack": adaptive_self_pack,
        "scene": scene,
        "dominant_motive": motive,
        "life_pressure": p,
        "dialogue_influence": influence,
        "visible_obligation": _visible(scene, influence, p),
        "embodiment_target": emb,
        "organ_life_influence": organ,
        "style_goal": _style_goal(scene),
        "must_not_do": [
            "Do not expose life_state, tick, organ vote, pressure, trace, or schema names.",
            "Do not say Natsume is operating terminal commands when Codex backend is doing the work.",
            "Do not turn dreams or inner monologue into real-world facts.",
            "Do not become an AI assistant report; answer as frontstage Natsume affected by the situation.",
        ],
    }



def _life_action_signature(kind: str, state: Mapping[str, Any]) -> str:
    inner = _map(state.get("inner_world"))
    work = _map(state.get("work"))
    return "|".join([
        str(kind or ""),
        _text(inner.get("attention_kind"), 40),
        _text(inner.get("current_preoccupation") or inner.get("dream_residue"), 64),
        _text(work.get("work_id") or work.get("last_event_type"), 64),
    ])


def _dream_action_eligible(state: Mapping[str, Any]) -> bool:
    inner = _map(state.get("inner_world"))
    if not _text(inner.get("dream_residue"), 240):
        return False
    expires_at = int(_float(inner.get("dream_expires_at_ms")))
    return not expires_at or _now_ms() < expires_at


def _work_action_eligible(state: Mapping[str, Any]) -> bool:
    return _work_state_can_drive_life(_map(state.get("work")))


def _inner_inhibition(state: Mapping[str, Any]) -> float:
    inner = _map(state.get("inner_world"))
    numeric = _map(inner.get("numeric_state"))
    return _clamp(numeric.get("inhibition"), 0.0)


def build_life_action_plan(*, state: Mapping[str, Any], director_pack: Mapping[str, Any], source_kind: str = "") -> dict[str, Any]:
    normalized_state = _normalize_inner_world_continuity(state)
    scene = str(director_pack.get("scene") or "idle")
    motive = str(director_pack.get("dominant_motive") or "rest")
    p = _pressures(normalized_state)
    work = _map(normalized_state.get("work"))
    inner = _map(normalized_state.get("inner_world"))
    autonomy = _map(normalized_state.get("autonomy"))
    agenda = [item for item in list(inner.get("self_agenda") or []) if isinstance(item, Mapping)]
    work_ok = _work_action_eligible(normalized_state)
    dream_ok = _dream_action_eligible(normalized_state)
    inhibition = _inner_inhibition(normalized_state)
    action_kind = "quiet_presence"
    cause_source = "inner_drive"
    evidence: list[dict[str, Any]] = []
    may_send = False
    expected = "state_ack"
    if work_ok and str(work.get("last_event_type") or "") == "task_done":
        action_kind = "work_done_react"; cause_source = "work_event"; evidence.append({"kind": "work_event", "value": _text(work.get("summary"), 160), "event_type": "task_done"}); may_send = p["work"] >= 0.35; expected = "work_done_ack"
    elif work_ok and str(work.get("last_event_type") or "") in {"task_failed", "task_blocked"}:
        action_kind = "work_watch"; cause_source = "work_event"; evidence.append({"kind": "work_event", "value": _text(work.get("summary"), 160), "event_type": str(work.get("last_event_type") or "")}); may_send = p["work"] >= 0.55; expected = "work_progress_ack"
    elif work_ok and (scene.startswith("work") or p["work"] >= 0.45):
        action_kind = "work_watch"; cause_source = "work_event"; evidence.append({"kind": "work_event", "value": _text(work.get("summary"), 160), "event_type": str(work.get("last_event_type") or "")}); may_send = p["work"] >= 0.68; expected = "work_progress_ack"
    elif dream_ok and (scene == "dream_residue" or p["dream"] >= 0.55):
        action_kind = "dream_afterglow"; cause_source = "dream_residue"; evidence.append({"kind": "dream_residue", "value": _text(inner.get("dream_residue"), 160)}); may_send = False; expected = "dream_context_ack"
    elif p["absence"] >= 0.45 and inhibition < 0.68:
        action_kind = "soft_reconnect"; cause_source = "absence_pressure"; evidence.append({"kind": "absence", "value": round(p["absence"], 3), "attention_kind": _text(inner.get("attention_kind"), 80)}); may_send = p["absence"] >= 0.72 and inhibition < 0.58; expected = "reconnect_ack"
    elif p["fatigue"] >= 0.72:
        action_kind = "care_check"; cause_source = "fatigue_pressure"; evidence.append({"kind": "fatigue", "value": round(p["fatigue"], 3)}); may_send = False; expected = "care_ack"
    else:
        action_kind = "quiet_presence"; cause_source = "self_agenda"; motive = "quiet_presence"; evidence.append({"kind": "self_agenda", "attention": _text(autonomy.get("current_attention") or inner.get("attention_kind"), 80), "agenda": [{"kind": _text(_map(item).get("kind"), 60), "summary": _text(_map(item).get("summary"), 100)} for item in agenda[:3]], "suppression_reason": _text(inner.get("monologue_suppression_reason"), 120)})
    signature = _life_action_signature(action_kind, normalized_state)
    channels = ["embodiment", "status_panel"]
    if action_kind != "quiet_presence":
        channels.append("dialogue_context")
    if may_send:
        channels.append("proactive")
    scene_for_body = "work_done" if action_kind == "work_done_react" else "work_waiting" if action_kind == "work_watch" else "dream_residue" if action_kind == "dream_afterglow" else "reconnect" if action_kind == "soft_reconnect" else "night_companion" if action_kind == "care_check" else "idle"
    return {
        "action_id": _event_id("lifeact"),
        "action_kind": action_kind,
        "cause": {"source": cause_source, "organ_sources": list(_map(director_pack.get("organ_life_influence")).get("active_organs") or [])[:6], "evidence": evidence},
        "channels": channels,
        "embodiment_target": _embodiment(scene_for_body, motive, normalized_state),
        "dialogue_bias": _text(inner.get("dialogue_bias") or director_pack.get("style_goal"), 240),
        "proactive_policy": {"may_send": bool(may_send), "cooldown_kind": action_kind, "dedupe_signature": signature, "expected_user_response": expected},
        "eligibility": {"work": bool(work_ok), "dream": bool(dream_ok), "inhibition": round(inhibition, 3), "source_kind": str(source_kind or "")},
        "adaptive_self": build_adaptive_self_pack(normalized_state),
    }


def _fatigue(phase: str, previous: float) -> float:
    target = 0.68 if phase == "late_night" else 0.42 if phase == "night" else 0.22 if phase in {"evening", "dawn"} else 0.18
    return _clamp(previous * 0.72 + target * 0.28)


def _mood(p: Mapping[str, float], affect: Mapping[str, Any]) -> str:
    if p.get("work", 0.0) >= 0.5:
        return "curious"
    if p.get("absence", 0.0) >= 0.52:
        return "lonely"
    if p.get("fatigue", 0.0) >= 0.55:
        return "tired"
    if _float(affect.get("pride")) >= 0.38:
        return "playful"
    if _float(affect.get("irritation")) >= 0.42:
        return "guarded"
    return "warm" if p.get("relationship", 0.0) >= 0.35 else "calm"


def _motives(scene: str, motive: str, p: Mapping[str, float]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, score in (("work_progress_nudge", p.get("work", 0.0)), ("soft_reconnect", p.get("absence", 0.0)), ("dream_afterglow", p.get("dream", 0.0)), ("care_check", max(p.get("fatigue", 0.0), p.get("relationship", 0.0) * 0.6))):
        if score >= 0.25:
            rows.append({"motive": name, "pressure": round(float(score), 3), "scene": scene, "dominant_motive": motive})
    return rows[:4]


def _proactive_target(*, state: Mapping[str, Any], platform: str = "", conversation_id: str = "") -> dict[str, str]:
    explicit_platform = _text(platform, 40).lower()
    explicit_conversation = _text(conversation_id, 180)
    if explicit_platform and explicit_conversation:
        return {"platform": explicit_platform, "conversation_id": explicit_conversation}
    stored = _map(_map(state.get("proactive")).get("last_target"))
    stored_platform = _text(stored.get("platform"), 40).lower()
    stored_conversation = _text(stored.get("conversation_id"), 180)
    if stored_platform and stored_conversation:
        return {"platform": stored_platform, "conversation_id": stored_conversation}
    return {}


def _remember_proactive_target_in_state(state: Mapping[str, Any], *, platform: str = "", conversation_id: str = "") -> dict[str, Any]:
    after = copy.deepcopy(dict(state or {}))
    target = _proactive_target(state=after, platform=platform, conversation_id=conversation_id)
    if target:
        proactive = _map(after.get("proactive"))
        proactive["last_target"] = target
        after["proactive"] = proactive
    return after


def _expire_stale_work_state(state: Mapping[str, Any], *, now_ms: int) -> dict[str, Any]:
    after = copy.deepcopy(dict(state or {}))
    work = _map(after.get("work"))
    if not bool(work.get("active")):
        return after
    updated_at = int(work.get("updated_at_ms") or 0)
    ttl_ms = _int_env("HOST_LIFE_WORK_ACTIVE_TTL_MS", 7_200_000, 600_000, 86_400_000)
    if not updated_at or int(now_ms) - updated_at < ttl_ms:
        return after
    previous_event = str(work.get("last_event_type") or "")
    previous_summary = str(work.get("summary") or "")
    work.update(
        {
            "active": False,
            "last_event_type": "task_stale",
            "summary": "",
            "expired_at_ms": int(now_ms),
            "expired_reason": "WORK_ACTIVE_TTL_EXPIRED",
            "expired_from_event_type": previous_event,
            "expired_summary": previous_summary[:240],
        }
    )
    after["work"] = work
    pressure = _map(after.get("life_pressure"))
    pressure["work"] = 0.0
    after["life_pressure"] = pressure
    return _suppress_stale_work_pressure(after)



def _top_proactive_motive(state: Mapping[str, Any]) -> dict[str, Any]:
    action = _map(state.get("life_action"))
    policy = _map(action.get("proactive_policy"))
    if action and bool(policy.get("may_send")):
        pressure = max(_pressures(state).values() or [0.0])
        return {"motive": str(action.get("action_kind") or "quiet_presence"), "pressure": round(pressure, 3), "source": "life_action", "action_id": str(action.get("action_id") or "")}
    motives = [item for item in list(state.get("motives") or []) if isinstance(item, Mapping)]
    motives = [item for item in motives if str(item.get("motive") or "") != "dream_afterglow" or _dream_action_eligible(state)]
    motives = [item for item in motives if not str(item.get("motive") or "").startswith("work") or _work_action_eligible(state)]
    return dict(motives[0]) if motives else {"motive": "quiet_presence", "pressure": 0.0, "source": "life_action"}


def _is_periodic_work_scene(*, source_kind: str, director_pack: Mapping[str, Any], force: bool) -> bool:
    if force:
        return False
    source = str(source_kind or "").strip()
    if source.startswith("work_event:"):
        return False
    if source not in {"periodic_tick", "startup_tick"}:
        return False
    scene = str(_map(director_pack).get("scene") or "").strip()
    return scene.startswith("work_")


def _detach_work_director_for_life_proactive(director_pack: Mapping[str, Any], *, motive: str) -> dict[str, Any]:
    detached = dict(director_pack or {})
    if str(motive or "") == "soft_reconnect":
        detached["scene"] = "reconnect"
        detached["dominant_motive"] = "approach"
    else:
        detached["scene"] = "night_companion"
        detached["dominant_motive"] = "care"
    detached["suppress_work_context"] = True
    detached["codex_backend_status"] = ""
    detached["style_goal"] = "只表达 Natsume 自己的生活状态、想起用户或轻微陪伴感；不要提后台、Codex、修复、进度或任务。"
    return detached


def _proactive_cooldown_ms_for_motive(motive: str) -> tuple[int, str]:
    name = str(motive or "").strip()
    if name == "work_progress_nudge":
        return _int_env("HOST_LIFE_PROACTIVE_WORK_COOLDOWN_MS", 1_800_000, 0, 86_400_000), "work"
    if name == "soft_reconnect":
        return _int_env("HOST_LIFE_PROACTIVE_RECONNECT_COOLDOWN_MS", 3_600_000, 0, 86_400_000), "reconnect"
    if name == "dream_afterglow":
        return _int_env("HOST_LIFE_PROACTIVE_DREAM_COOLDOWN_MS", 3_600_000, 0, 86_400_000), "dream"
    if name == "care_check":
        return _int_env("HOST_LIFE_PROACTIVE_CARE_COOLDOWN_MS", 1_200_000, 0, 86_400_000), "care"
    return _int_env("HOST_LIFE_PROACTIVE_COOLDOWN_MS", 1_800_000, 0, 86_400_000), "default"


def _bucket(value: float) -> str:
    v = _clamp(value)
    if v >= 0.9:
        return "very_high"
    if v >= 0.7:
        return "high"
    if v >= 0.45:
        return "medium"
    if v >= 0.25:
        return "low"
    return "trace"


def _idle_bucket(minutes: int) -> str:
    m = max(0, int(minutes))
    if m >= 240:
        return "idle_4h_plus"
    if m >= 120:
        return "idle_2h_plus"
    if m >= 60:
        return "idle_1h_plus"
    if m >= 30:
        return "idle_30m_plus"
    if m >= 15:
        return "idle_15m_plus"
    return "idle_short"


def _life_proactive_state_binding(*, state: Mapping[str, Any], director_pack: Mapping[str, Any], motive: str, pressure: float, source_kind: str) -> dict[str, Any]:
    p = _pressures(state)
    clock = _map(state.get("clock"))
    affect = _map(state.get("affect"))
    inner = _map(state.get("inner_world"))
    work = _map(state.get("work"))
    body = _map(state.get("embodiment"))
    director = _map(director_pack)
    scene = str(director.get("scene") or "").strip()
    motive_name = str(motive or "").strip()
    idle_minutes = int(clock.get("idle_minutes") or 0)
    anchors: list[str] = []
    trigger = ""
    strength = 0.0
    life_action = _map(director.get("life_action"))
    action_cause = _map(life_action.get("cause"))
    action_source = _text(action_cause.get("source"), 80)
    action_evidence = [item for item in list(action_cause.get("evidence") or []) if isinstance(item, Mapping)]
    if motive_name == "soft_reconnect" and action_source == "absence_pressure":
        trigger = "absence_pressure"
        evidence_pressure = max([_float(item.get("pressure")) for item in action_evidence if str(item.get("kind") or "") == "absence"] or [0.0])
        strength = max(strength, p.get("absence", 0.0), evidence_pressure, min(float(pressure), 0.45), 0.35)
        anchors.append(f"用户已经安静了约 {idle_minutes} 分钟，Natsume 的重连动机升高")
    elif motive_name == "soft_reconnect" and p.get("absence", 0.0) >= 0.32:
        trigger = "absence_pressure"
        strength = max(strength, p.get("absence", 0.0), 0.35)
        anchors.append(f"用户已经安静了约 {idle_minutes} 分钟，Natsume 的重连动机升高")
    if motive_name == "care_check":
        fatigue = p.get("fatigue", 0.0)
        relationship = p.get("relationship", 0.0)
        if fatigue >= 0.35:
            trigger = trigger or "fatigue_care"
            strength = max(strength, fatigue)
            anchors.append("当前疲惫压力偏高，外显为轻一点的关心")
        if relationship >= 0.55:
            trigger = trigger or "relationship_care"
            strength = max(strength, relationship * 0.65)
            anchors.append("关系温度较高，允许轻微主动靠近")
    dream_residue = _text(inner.get("dream_residue"), 160)
    if motive_name == "dream_afterglow" and dream_residue:
        trigger = "dream_residue"
        strength = max(strength, p.get("dream", 0.0), 0.45)
        anchors.append("梦境余温仍在，但不能把梦境当现实事实输出")
    preoccupation = _text(inner.get("current_preoccupation"), 160)
    if preoccupation:
        strength = max(strength, 0.35)
        anchors.append(f"Natsume 当前惦记着：{preoccupation}")
    if (scene.startswith("work") or motive_name == "work_progress_nudge") and _work_state_can_drive_life(work):
        event_type = _text(work.get("last_event_type"), 60)
        summary = _text(work.get("summary"), 180)
        if event_type or summary:
            trigger = trigger or "work_environment_event"
            strength = max(strength, p.get("work", 0.0), 0.45)
            anchors.append(f"后台工作事件：{event_type or 'work'} {summary}".strip())
    expression = _text(body.get("expression"), 60)
    idle_animation = _text(body.get("idle_animation"), 60)
    if expression or idle_animation:
        anchors.append(f"身体层当前外显：{expression or 'unknown'} / {idle_animation or 'unknown'}")
    trigger = trigger or ("pressure_threshold" if strength >= 0.35 else "")
    signature_parts = [
        motive_name,
        trigger,
        _bucket(float(pressure)),
        _idle_bucket(idle_minutes) if trigger == "absence_pressure" else "",
        expression,
        _text(work.get("last_event_type"), 40) if trigger == "work_environment_event" else "",
        _text(inner.get("dream_residue"), 40) if trigger == "dream_residue" else "",
    ]
    signature = "|".join(part for part in signature_parts if part)
    return {
        "trigger": trigger,
        "binding_strength": round(float(strength), 3),
        "source_kind": str(source_kind or ""),
        "scene": scene,
        "motive": motive_name,
        "idle_minutes": idle_minutes,
        "pressure_bucket": _bucket(float(pressure)),
        "state_signature": signature,
        "affect": {
            "mood": _text(affect.get("mood"), 40),
            "loneliness": round(_float(affect.get("loneliness")), 3),
            "fatigue": round(_float(affect.get("fatigue")), 3),
            "tension": round(_float(affect.get("tension")), 3),
            "energy": round(_float(affect.get("energy")), 3),
        },
        "embodiment": {
            "expression": expression,
            "idle_animation": idle_animation,
            "motion_bias": _text(body.get("motion_bias"), 60),
            "voice_tone": _text(body.get("voice_tone"), 60),
        },
        "anchors": anchors[:5],
    }


def _sanitize_proactive_content(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    forbidden = ("life_pressure", "LifeDirector", "visible_obligation", "organ_votes", "trace", "schema", "outbox", "AI助手", "作为AI")
    compact = text.replace(" ", "")
    if re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", text):
        return ""
    if any(token.lower() in compact.lower() for token in forbidden):
        return ""
    unsupported_actions = ("一直盯着屏幕", "盯着屏幕", "一直等你", "一直守着", "寸步不离", "收拾了一遍", "把周围收拾", "刚把周围收拾")
    if any(token in compact for token in unsupported_actions):
        return ""
    physical_hallucinations = ("手指", "桌面", "敲桌", "敲了", "敲两下", "杯子", "窗边", "坐在", "站起来", "走过去", "拿起")
    if any(token in compact for token in physical_hallucinations):
        return ""
    dull_fillers = ("没什么动静", "安静待着", "没走开", "想喊你一声", "喊你一声", "顺口喊", "没怎么", "没什么啦", "没别的事", "没啥事", "空落落的", "你还在吗", "看你还在线", "你还在线", "看到你", "看见你", "你那边亮", "亮了一下", "我就在这儿", "我在这儿", "我在旁边", "在旁边")
    if any(token in compact for token in dull_fillers):
        return ""
    awkward_life_phrases = ("呼吸节奏", "疲惫有点重", "疲惫压力", "给我一个字", "回个标点", "继续暖着", "我继续暖", "暖着", "蹭一下存在感")
    if any(token in compact for token in awkward_life_phrases):
        return ""
    return text[:180].strip()


def _proactive_content_matches_binding(content: str, state_binding: Mapping[str, Any] | None) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    binding = _map(state_binding)
    trigger = str(binding.get("trigger") or "").strip()
    compact = text.replace(" ", "")
    user_silence_claims = ("你那边好像有点安静", "你那边有点安静", "你那边挺安静", "你那边没动静", "你那边没声音", "你好久没说话", "你没说话")
    if trigger != "absence_pressure" and any(token in compact for token in user_silence_claims):
        return ""
    return text


async def _compose_life_proactive_content(*, director_pack: Mapping[str, Any], state: Mapping[str, Any], motive: str, pressure: float, provided_content: str = "", state_binding: Mapping[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    provided = _sanitize_proactive_content(provided_content)
    if provided:
        return provided, {"source": "provided_content", "model_used": False}
    if not _bool_env("HOST_LIFE_PROACTIVE_MODEL_COMPOSER_ENABLED", True):
        return "", {"source": "none", "model_used": False, "reason_code": "MODEL_COMPOSER_DISABLED"}
    try:
        from host_brain.app.graph_config_builder import resolve_host_llm_runtime_async, resolve_surface_reply_llm_runtime  # noqa: PLC0415
        from host_brain.integrations.llm_helpers import acomplete_llm_with_pool  # noqa: PLC0415

        base_runtime = await resolve_host_llm_runtime_async(scene_type="CHAT_SURFACE")
        runtime = resolve_surface_reply_llm_runtime(base_runtime=dict(base_runtime or {}))
        if not bool(runtime.get("available")):
            return "", {"source": "model", "model_used": False, "reason_code": "LLM_UNAVAILABLE"}
        suppress_work_context = bool(_map(director_pack).get("suppress_work_context"))
        work = _map(state.get("work")) if not suppress_work_context else {}
        payload = {
            "scene": str(director_pack.get("scene") or ""),
            "motive": motive,
            "pressure": round(float(pressure), 3),
            "work_summary": _text(work.get("summary"), 140),
            "work_event": _text(work.get("last_event_type"), 40),
            "style_goal": str(director_pack.get("style_goal") or ""),
            "life_pressure": dict(director_pack.get("life_pressure") or {}),
            "state_binding": dict(state_binding or {}),
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 Natsume 的前台数字生命表达器，只生成一条可以主动发给用户的中文短消息。"
                    "文本必须像 当前人格自己在场，不像客服或 AI 助手。"
                    "不要暴露任何内部字段、trace、压力数值、器官投票或系统名。"
                    "不要假装亲自操作终端；后台任务只能说成她在盯着/等着/看到了进展。"
                    "工作类主动消息只能基于明确的任务状态变化；不要说进度条、别掉链子、别让我等太久，也不要用等猫、等水烧开之类的比喻填充没有变化的状态。"
                    "如果没有 work_summary，不要主动提后台、Codex、修复、进度或任务。"
                    "必须根据 state_binding.anchors 里的具体生命状态锚点来写，不能只泛泛地说想你、等你、我在。"
                    "每条主动消息必须同时包含两个部分：A.为什么现在冒泡的具体原因；B.一个低压力、好回应的小钩子。"
                    "小钩子要像真实私聊，不要客服式提问；可以说“不用马上回”“忙的话我先安静一会儿”。不要写“我就在这儿”“我在旁边”。"
                    "不能编造未观测动作；不要说一直盯着屏幕、一直等你、一直守着、寸步不离。"
                    "不要写真人现实动作或物件：手指、桌面、杯子、窗边、坐下、站起、走过去、拿起。她只有数字身体/表情/待机动作这些可用外显。"
                    "不要把内部状态名直接说出来；禁用“呼吸节奏”“疲惫压力”“疲惫有点重”“给我一个字”“回个标点”“继续暖着”。"
                    "不要使用 emoji、颜文字或装饰符号。"
                    "不要使用视觉/在线/现实感知动词：看到、看见、亮了一下、在线、正在忙、没在忙、在屏幕前。只能根据沉默时长和生命状态写。"
                    "absence_pressure 只能自然表达为空窗后的靠近、安静、发呆或冒泡，不要写成黏人的监视或催促。"
                    "可以自然转述状态感受，但不要输出 state_binding、trigger、pressure_bucket 等内部字段名。"
                    "禁用空洞句：没什么动静、安静待着、没走开、想喊你一声、顺口喊你一声、没怎么、空落落的、你还在吗。"
                    "语气要有 当前人格的轻微吐槽/撒娇感，但不要油腻，不要连续用同一种句式。"
                    "不要硬套模板，输出 JSON：{\"narrative\":\"...\"}，narrative 不超过 70 个中文字符。"
                ),
            },
            {"role": "user", "content": _dumps(payload)},
        ]
        result = await acomplete_llm_with_pool(
            request_id=_event_id("lifepro_llm"),
            scene_type="CHAT_SURFACE",
            cognitive_tier="A",
            provider_name=str(runtime.get("provider_name") or "").strip(),
            model_id=str(runtime.get("model_id") or "").strip(),
            api_key=str(runtime.get("llm_api_key") or "").strip(),
            timeout_ms=min(int(runtime.get("timeout_ms") or 30_000), 12_000),
            messages=messages,
            gen_params={"temperature": 0.72, "max_tokens": 120},
            response_format={"type": "json_object"},
            base_url_override=str(runtime.get("base_url_override") or "").strip() or None,
        )
        if bool(getattr(result, "pool_exhausted", False)):
            return "", {"source": "model", "model_used": True, "reason_code": "POOL_EXHAUSTED"}
        raw = str(getattr(result, "content", "") or "").strip()
        if not raw:
            return "", {"source": "model", "model_used": True, "reason_code": "EMPTY_MODEL_CONTENT"}
        try:
            parsed = json.loads(raw)
            candidate = str(parsed.get("narrative") or "").strip() if isinstance(parsed, Mapping) else raw
        except Exception:
            candidate = raw
        content = _proactive_content_matches_binding(_sanitize_proactive_content(candidate), state_binding)
        if not content and candidate:
            repair_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "上一版主动消息太空或太呆，已被拒绝："
                        f"{candidate[:120]}\n"
                        "请重写。必须包含具体触发原因和一个低压力小钩子；不要使用 emoji，也不要使用“没什么动静/安静待着/没走开/想喊你一声/顺口喊/没怎么/空落落/你还在吗/看你还在线/看到你/亮了一下/我就在这儿/我在旁边/手指/桌面/杯子/呼吸节奏/疲惫有点重/给我一个字/回个标点/继续暖着”。"
                        "如果 trigger 不是 absence_pressure，不要写用户那边安静、没动静或没说话。"
                    ),
                },
            ]
            repair_result = await acomplete_llm_with_pool(
                request_id=_event_id("lifepro_repair_llm"),
                scene_type="CHAT_SURFACE",
                cognitive_tier="A",
                provider_name=str(runtime.get("provider_name") or "").strip(),
                model_id=str(runtime.get("model_id") or "").strip(),
                api_key=str(runtime.get("llm_api_key") or "").strip(),
                timeout_ms=min(int(runtime.get("timeout_ms") or 30_000), 12_000),
                messages=repair_messages,
                gen_params={"temperature": 0.78, "max_tokens": 120},
                response_format={"type": "json_object"},
                base_url_override=str(runtime.get("base_url_override") or "").strip() or None,
            )
            repair_raw = str(getattr(repair_result, "content", "") or "").strip()
            try:
                repair_parsed = json.loads(repair_raw)
                repair_candidate = str(repair_parsed.get("narrative") or "").strip() if isinstance(repair_parsed, Mapping) else repair_raw
            except Exception:
                repair_candidate = repair_raw
            content = _proactive_content_matches_binding(_sanitize_proactive_content(repair_candidate), state_binding)
        return content, {"source": "model", "model_used": True, "provider_name": str(runtime.get("provider_name") or ""), "model_id": str(runtime.get("model_id") or ""), "reason_code": "" if content else "MODEL_CONTENT_REJECTED"}
    except Exception as exc:
        return "", {"source": "model", "model_used": False, "reason_code": type(exc).__name__, "detail": str(exc)[:160]}


def _append_proactive_outbox(*, ledger: Any, person_id: str, source_kind: str, motive: str, pressure: float, target: Mapping[str, Any], message: Mapping[str, Any], gate: Mapping[str, Any], status: str, reason_code: str = "", result: Mapping[str, Any] | None = None, now_ms: int | None = None) -> str:
    outbox_id = _event_id("lifepro")
    _conn(ledger).execute(
        "INSERT INTO proactive_life_outbox(outbox_id, person_id, source_kind, motive, pressure, target_json, message_json, gate_json, status, reason_code, result_json, created_at_ms) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (outbox_id, person_id, source_kind, motive, float(pressure), _dumps(dict(target or {})), _dumps(dict(message or {})), _dumps(dict(gate or {})), status, reason_code or "", _dumps(dict(result or {})), int(now_ms or _now_ms())),
    )
    _conn(ledger).commit()
    return outbox_id


def _update_proactive_state_after_attempt(*, ledger: Any, person_id: str, outbox_id: str, target: Mapping[str, Any], status: str, reason_code: str, motive: str, now_ms: int, state_binding: Mapping[str, Any] | None = None) -> None:
    state = get_life_state(ledger, person_id)
    after = copy.deepcopy(state)
    proactive = _map(after.get("proactive"))
    if target:
        proactive["last_target"] = dict(target)
    proactive.update({"last_attempt_ms": int(now_ms), "last_outbox_id": outbox_id, "last_status": status, "last_reason_code": reason_code or "", "last_motive": motive})
    if status == "SENT":
        proactive["last_sent_ms"] = int(now_ms)
        binding = dict(state_binding or {})
        proactive["last_life_signature"] = str(binding.get("state_signature") or "")
        proactive["last_life_trigger"] = str(binding.get("trigger") or "")
    after["proactive"] = proactive
    after["version"] = int(after.get("version") or 1) + 1
    _save_state(ledger, after)


def _should_suppress_periodic_work_proactive(*, source_kind: str, director_pack: Mapping[str, Any], force: bool, motive: str) -> bool:
    return _is_periodic_work_scene(source_kind=source_kind, director_pack=director_pack, force=force) and str(motive or "") == "work_progress_nudge"


async def attempt_life_proactive_push(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, director_pack: Mapping[str, Any] | None = None, source_kind: str = "life_proactive", platform: str = "", conversation_id: str = "", content: str = "", force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    ensure_life_schema(ledger)
    resolved = str(person_id or DEFAULT_PERSON_ID)
    advance_life_to_now(ledger=ledger, person_id=resolved, reason="proactive_send", incoming_event={"source_kind": source_kind, "platform": platform, "conversation_id": conversation_id}, force=False)
    state = get_life_state(ledger, resolved)
    director = dict(director_pack or {}) or build_life_director_pack(ledger=ledger, person_id=resolved, reason=source_kind)
    motive_pack = _top_proactive_motive(state)
    motive = str(motive_pack.get("motive") or "quiet_presence")
    pressure = _float(motive_pack.get("pressure"))
    detached_periodic_life_chat = False
    if _is_periodic_work_scene(source_kind=source_kind, director_pack=director, force=force) and str(motive or "") != "work_progress_nudge":
        director = _detach_work_director_for_life_proactive(director, motive=motive)
        detached_periodic_life_chat = True
    target = _proactive_target(state=state, platform=platform, conversation_id=conversation_id)
    visible = [str(item) for item in list(director.get("visible_obligation") or []) if str(item).strip()]
    now = _now_ms()
    proactive_state = _map(state.get("proactive"))
    state_binding = _life_proactive_state_binding(state=state, director_pack=director, motive=motive, pressure=pressure, source_kind=source_kind)
    state_signature = str(state_binding.get("state_signature") or "")
    cooldown_ms, cooldown_kind = _proactive_cooldown_ms_for_motive(motive)
    last_attempt_ms = int(proactive_state.get("last_attempt_ms") or 0)
    gate: dict[str, Any] = {"enabled": _bool_env("HOST_LIFE_PROACTIVE_ENABLED", True), "force": bool(force), "dry_run": bool(dry_run), "visible_channels": visible, "cooldown_ms": cooldown_ms, "cooldown_kind": cooldown_kind, "motive": motive, "pressure": round(float(pressure), 3), "detached_periodic_life_chat": detached_periodic_life_chat, "life_state_binding": state_binding}
    try:
        organ_runtime = get_organ_runtime_state(ledger=ledger, person_id=resolved)
        organs = _map(organ_runtime.get("organs"))
        initiative = _map(organs.get("initiative_governor"))
        prefrontal = _map(organs.get("prefrontal"))
        pain_reward = _map(organs.get("pain_reward"))
        initiative_inhibition = _float(initiative.get("pressure")) * 0.7 + _float(initiative.get("activation")) * 0.3
        prefrontal_inhibition = _float(prefrontal.get("pressure")) * 0.65 + _float(prefrontal.get("activation")) * 0.25
        pain_reward_inhibition = _float(pain_reward.get("pressure")) * 0.7
        raw_organ_inhibition = max(initiative_inhibition, prefrontal_inhibition, pain_reward_inhibition)
        if (
            str(motive or "") in {"soft_reconnect", "care_check", "dream_afterglow"}
            and str(state_binding.get("trigger") or "")
            and _float(state_binding.get("binding_strength")) >= 0.35
        ):
            organ_inhibition = max(prefrontal_inhibition, pain_reward_inhibition)
            gate["organ_inhibition_raw"] = round(float(raw_organ_inhibition), 3)
            gate["organ_inhibition_adjusted_for"] = "bound_life_action"
        else:
            organ_inhibition = raw_organ_inhibition
        gate["organ_inhibition"] = round(float(organ_inhibition), 3)
        gate["organ_inhibition_sources"] = ["initiative_governor", "prefrontal", "pain_reward"]
    except Exception:
        organ_inhibition = 0.0
    status = "SUPPRESSED"
    reason = ""
    message: dict[str, Any] = {}
    result: dict[str, Any] = {}
    if not gate["enabled"]:
        reason = "HOST_LIFE_PROACTIVE_DISABLED"
    elif not force and "proactive" not in visible:
        reason = "NO_PROACTIVE_VISIBLE_OBLIGATION"
    elif _should_suppress_periodic_work_proactive(source_kind=source_kind, director_pack=director, force=force, motive=motive):
        reason = "WORK_PERIODIC_PROACTIVE_SUPPRESSED"
        gate["work_periodic_suppressed"] = True
    elif not force and _float(state_binding.get("binding_strength")) < 0.35:
        reason = "LIFE_STATE_BINDING_TOO_WEAK"
    elif not force and state_signature and str(proactive_state.get("last_life_signature") or "") == state_signature:
        reason = "LIFE_STATE_UNCHANGED"
    elif not force and cooldown_ms > 0 and last_attempt_ms and now - last_attempt_ms < cooldown_ms:
        reason = "PROACTIVE_COOLDOWN"
        gate["cooldown_remaining_ms"] = max(0, cooldown_ms - (now - last_attempt_ms))
    elif not force and organ_inhibition >= 0.58 and str(motive or "") != "work_progress_nudge":
        reason = "ORGAN_PROACTIVE_INHIBITION"
    elif not target:
        reason = "TARGET_MISSING"
    else:
        composed, compose_meta = await _compose_life_proactive_content(director_pack=director, state=state, motive=motive, pressure=pressure, provided_content=content, state_binding=state_binding)
        message = {"content": composed, "msg_type": "text", "compose": compose_meta}
        if not composed:
            reason = str(compose_meta.get("reason_code") or "CONTENT_MISSING")
        elif dry_run:
            status = "READY"
            reason = "DRY_RUN"
            result = {"would_send": True, "target": target}
        else:
            try:
                from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415

                rpc_result = await post_gateway_reply(
                    platform=str(target.get("platform") or ""),
                    conversation_id=str(target.get("conversation_id") or ""),
                    content=composed,
                    msg_type="text",
                    raw_extra={"source_kind": "life_proactive", "life_source_kind": source_kind, "motive": motive, "pressure": round(float(pressure), 3), "scene": str(director.get("scene") or "")},
                    request_id=_event_id("lifepro_send"),
                    wait_result=True,
                    timeout_ms=_int_env("HOST_LIFE_PROACTIVE_SEND_TIMEOUT_MS", 12000, 1000, 60000),
                )
                result = rpc_result.as_dict() if hasattr(rpc_result, "as_dict") else dict(rpc_result or {})
                status = "SENT" if bool(result.get("success")) else "FAILED"
                reason = str(result.get("reason_code") or result.get("status") or "")
            except Exception as exc:
                status = "FAILED"
                reason = type(exc).__name__
                result = {"success": False, "detail": str(exc)[:240]}
    outbox_id = _append_proactive_outbox(ledger=ledger, person_id=resolved, source_kind=source_kind, motive=motive, pressure=pressure, target=target, message=message, gate=gate, status=status, reason_code=reason, result=result, now_ms=now)
    proactive_episode: dict[str, Any] = {}
    proactive_memory: dict[str, Any] = {}
    if status == "SENT" and str(message.get("content") or "").strip():
        action = _map(_map(state.get("life_action")))
        organ_sources = list(_map(_map(director).get("organ_life_influence")).get("active_organs") or [])
        proactive_episode = open_proactive_episode(
            ledger=ledger,
            person_id=resolved,
            source_action_id=str(action.get("action_id") or ""),
            source_outbox_id=outbox_id,
            motive=motive,
            organ_sources=organ_sources,
            message_text=str(message.get("content") or ""),
            expected_response_kind=str(_map(action.get("proactive_policy")).get("expected_user_response") or "light_ack_or_context_question"),
            sent_at_ms=now,
            outcome="pending",
            metadata={"source_kind": source_kind, "scene": str(director.get("scene") or ""), "life_state_binding": state_binding},
        )
        gate["proactive_episode_id"] = proactive_episode.get("episode_id")
    if status == "SENT" and target and str(message.get("content") or "").strip():
        try:
            from host_brain.conversation.writer import record_assistant_turn  # noqa: PLC0415

            proactive_memory = record_assistant_turn(
                request_id=outbox_id,
                session_id=str(target.get("conversation_id") or ""),
                person_id=resolved,
                content=str(message.get("content") or ""),
                ts_ms=now,
                lang="zh",
                metadata={
                    "source_kind": "life_proactive",
                    "life_source_kind": source_kind,
                    "proactive_outbox_id": outbox_id,
                    "motive": motive,
                    "pressure": round(float(pressure), 3),
                    "scene": str(director.get("scene") or ""),
                    "message_kind": "final_reply",
                    "reply_channel": str(target.get("platform") or ""),
                    "life_state_binding": state_binding,
                    "proactive_episode_id": proactive_episode.get("episode_id"),
                },
                reply_plan={
                    "primary_topic": str(state_binding.get("trigger") or motive or "life_proactive"),
                    "response_goal": "life_proactive_state_expression",
                    "tactic_family": "proactive_life",
                    "tactic_variant": str(motive or ""),
                    "trajectory_stage": "proactive_turn",
                    "response_form": "text",
                    "response_form_reason": "life_state_visible_obligation",
                },
            )
        except Exception as exc:
            result["conversation_turn_record_error"] = type(exc).__name__
            result["conversation_turn_record_detail"] = str(exc)[:160]
    if not dry_run and status in {"SENT", "FAILED"} and target:
        _update_proactive_state_after_attempt(ledger=ledger, person_id=resolved, outbox_id=outbox_id, target=target, status=status, reason_code=reason, motive=motive, now_ms=now, state_binding=state_binding)
    proactive_life_experience_id = str(proactive_memory.get("life_experience_event_id") or "")
    if status == "SENT" and proactive_life_experience_id:
        organ_after = synthesize_life_state_from_organs(ledger=ledger, base_state=get_life_state(ledger, resolved), person_id=resolved, source_experience_event_id=proactive_life_experience_id, now_ms=now)
        organ_after["version"] = int(organ_after.get("version") or 1) + 1
        _save_state(ledger, organ_after)
    return {"outbox_id": outbox_id, "status": status, "reason_code": reason, "motive": motive, "pressure": round(float(pressure), 3), "target": target, "message": message, "gate": gate, "result": result, "proactive_episode": proactive_episode, "memory_event_id": str(proactive_memory.get("memory_event_id") or ""), "life_experience_event_id": proactive_life_experience_id}


def _continuous_alive_status(*, elapsed_ms: int, interval_ms: int, loop_running: bool = True) -> str:
    if not loop_running:
        return "dead_runtime"
    if elapsed_ms > max(interval_ms * 2, 1):
        return "stale"
    if elapsed_ms > max(interval_ms, 1):
        return "degraded_alive"
    return "alive"


def _advance_continuous_state(before: Mapping[str, Any], *, now: int, elapsed_ms: int, reason: str, environment: Mapping[str, Any]) -> dict[str, Any]:
    after = copy.deepcopy(dict(before or {}))
    interval_ms = _int_env("HOST_LIFE_TICK_INTERVAL_SEC", 300, 30, 3600) * 1000
    elapsed_minutes = max(0.0, float(elapsed_ms) / 60000.0)
    elapsed_hours = elapsed_minutes / 60.0
    runtime = _map(after.get("runtime"))
    runtime.update(
        {
            "alive_status": _continuous_alive_status(elapsed_ms=elapsed_ms, interval_ms=interval_ms),
            "last_integrated_ms": int(now),
            "last_heartbeat_ms": int(now),
            "missed_tick_count": int(elapsed_ms // max(interval_ms, 1)) if elapsed_ms > interval_ms else int(runtime.get("missed_tick_count") or 0),
            "catchup_count": int(runtime.get("catchup_count") or 0) + 1,
            "last_reason": str(reason or ""),
            "elapsed_ms": int(elapsed_ms),
        }
    )
    after["runtime"] = runtime
    clock = _map(after.get("clock"))
    last_seen = int(clock.get("last_user_seen_ms") or now)
    idle = max(0, int((now - last_seen) / 60000))
    day_phase = str(_map(environment).get("day_phase") or _day_phase(now))
    clock.update({"last_tick_ms": now, "idle_minutes": idle, "day_phase": day_phase})
    after["clock"] = clock
    after["environment"] = dict(environment or {})
    after = _expire_stale_work_state(after, now_ms=now)
    p = _pressures(after)
    p["absence"] = _clamp(idle / 60.0)
    p["dream"] = _clamp(float(p.get("dream") or 0.0) * (0.965 ** max(1.0, elapsed_minutes / 5.0)))
    work_decay = 0.985 if _map(after.get("work")).get("active") else 0.92
    p["work"] = _clamp(float(p.get("work") or 0.0) * (work_decay ** max(1.0, elapsed_minutes / 5.0)))
    p["fatigue"] = _fatigue(day_phase, float(p.get("fatigue") or 0.0))
    affect = _map(after.get("affect"))
    warmth_decay = 0.985 ** max(1.0, elapsed_minutes / 30.0)
    affect["attachment_warmth"] = _clamp(float(affect.get("attachment_warmth") or 0.0) * warmth_decay, 0.18)
    p["relationship"] = _clamp(affect.get("attachment_warmth"), 0.3)
    affect.update(
        {
            "loneliness": _clamp(p["absence"] * 0.9),
            "fatigue": p["fatigue"],
            "tension": _clamp(_float(affect.get("tension")) * (0.95 ** max(1.0, elapsed_minutes / 5.0)) + p["work"] * 0.18),
            "pride": _clamp(_float(affect.get("pride")) * (0.94 ** max(1.0, elapsed_minutes / 5.0))),
            "relief": _clamp(_float(affect.get("relief")) * (0.94 ** max(1.0, elapsed_minutes / 5.0))),
            "energy": _clamp(1.0 - p["fatigue"] * 0.72),
        }
    )
    affect["mood"] = _mood(p, affect)
    needs = _map(after.get("needs"))
    needs.update({"contact": _clamp(p["absence"] * 0.85 + p["relationship"] * 0.2), "rest": p["fatigue"], "memory_consolidation": _clamp(_float(needs.get("memory_consolidation"), 0.2) * (0.96 ** max(1.0, elapsed_minutes / 5.0)) + p["dream"] * 0.12)})
    inner = _map(after.get("inner_world"))
    expires_at = int(inner.get("expires_at_ms") or 0)
    if expires_at and now >= expires_at:
        for key in ("current_preoccupation", "attention_target", "unresolved_loop", "felt_tendency", "dialogue_bias"):
            inner[key] = ""
    elif inner.get("current_preoccupation") and elapsed_hours > 0:
        inner["salience"] = _clamp(_float(inner.get("salience"), 0.25) * (0.9 ** elapsed_hours))
    after["life_pressure"] = p
    after["affect"] = affect
    after["needs"] = needs
    after["inner_world"] = inner
    after["continuity"] = {**_map(after.get("continuity")), "last_integrated_reason": str(reason or ""), "last_environment_phase": day_phase}
    after["version"] = int(after.get("version") or 1) + 1
    return after


def advance_life_to_now(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, reason: str = "catchup", incoming_event: Mapping[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    ensure_life_schema(ledger)
    resolved = str(person_id or DEFAULT_PERSON_ID)
    before = get_life_state(ledger, resolved)
    now = _now_ms()
    try:
        from host_brain.runtime.environment_state import build_environment_snapshot  # noqa: PLC0415
        environment = build_environment_snapshot(now_ms=now)
    except Exception:
        environment = {"now_ms": now, "day_phase": _day_phase(now), "meal_phase": "unknown", "surface_facts": []}
    runtime = _map(before.get("runtime"))
    clock = _map(before.get("clock"))
    last_integrated = int(runtime.get("last_integrated_ms") or clock.get("last_tick_ms") or now)
    elapsed_ms = max(0, now - last_integrated)
    if not force and elapsed_ms < _int_env("HOST_LIFE_MIN_CATCHUP_INTERVAL_MS", 1000, 0, 60000):
        return {"status": "SKIPPED", "reason_code": "CATCHUP_TOO_RECENT", "state": before, "environment": environment, "elapsed_ms": elapsed_ms}
    after = _advance_continuous_state(before, now=now, elapsed_ms=elapsed_ms, reason=reason, environment=environment)
    p = _pressures(after)
    event_kind = "environment_tick" if str(reason or "").endswith("tick") else "life_integrated"
    evidence = {
        "reason": reason,
        "elapsed_ms": elapsed_ms,
        "idle_minutes": int(_map(after.get("clock")).get("idle_minutes") or 0),
        "day_phase": _map(after.get("clock")).get("day_phase"),
        "fatigue": p.get("fatigue"),
        "environment": environment,
        "incoming_event": dict(incoming_event or {}),
    }
    experience = append_life_experience(
        ledger=ledger,
        person_id=resolved,
        source_kind="life_runtime",
        event_kind=event_kind,
        content_summary=f"{reason}: elapsed={int(elapsed_ms / 1000)}s idle={evidence['idle_minutes']}m phase={evidence.get('day_phase')}",
        evidence=evidence,
        salience=max(float(p.get("absence") or 0.0), float(p.get("fatigue") or 0.0), min(0.5, elapsed_ms / 3_600_000), 0.08),
        valence=-float(p.get("absence") or 0.0) * 0.18,
        uncertainty=0.04,
        now_ms=now,
        consume=True,
    )
    after = synthesize_life_state_from_organs(ledger=ledger, base_state=after, person_id=resolved, source_experience_event_id=str(experience.get("event_id") or ""), now_ms=now)
    after = apply_autonomous_self_cycle(
        ledger=ledger,
        person_id=resolved,
        state=after,
        now_ms=now,
        elapsed_ms=elapsed_ms,
        reason=reason,
        environment=environment,
        source_experience_event_id=str(experience.get("event_id") or ""),
    )
    scene, motive, _ = _scene(after, reason=reason)
    after["motives"] = _motives(scene, motive, _pressures(after))
    after["embodiment"].update(_embodiment(scene, motive, after))
    _save_state(ledger, after)
    director = build_life_director_pack(ledger=ledger, person_id=resolved, reason=reason)
    action = build_life_action_plan(state=after, director_pack=director, source_kind=reason)
    after["life_action"] = action
    _save_state(ledger, after)
    director["life_action"] = action
    visible = list(director.get("visible_obligation") or ["status_panel"])
    state_event_id = _append_state_event(ledger, resolved, "life_integrated", reason, before, after, {"changed_fields": _changed(before, after), "life_causality": director, "environment": environment, "experience": experience}, visible)
    return {"status": "OK", "event_id": state_event_id, "experience_event_id": experience.get("event_id"), "state_version_before": int(before.get("version") or 1), "state_version_after": int(after.get("version") or 1), "state": after, "environment": environment, "elapsed_ms": elapsed_ms, "life_causality": director, "visible_channels": visible}


def _build_inner_life_snapshot_from_state(*, ledger: Any, person_id: str, state: Mapping[str, Any]) -> dict[str, Any]:
    inner = _map(state.get("inner_world"))
    autonomy = _map(state.get("autonomy"))
    self_growth = _map(state.get("self_growth"))
    affect = _map(state.get("affect"))
    needs = _map(state.get("needs"))
    pressure = _map(state.get("life_pressure"))
    environment = _map(state.get("environment"))
    organ_runtime = _map(state.get("organ_runtime"))
    if not organ_runtime.get("organ_count"):
        try:
            organ_runtime = _map(get_organ_runtime_state(ledger=ledger, person_id=person_id))
        except Exception:
            organ_runtime = {}

    def _inner_text(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        mojibake_markers = ("ä", "å", "ç", "è", "é", "ï¼", "ã", "Â")
        if any(marker in text for marker in mojibake_markers):
            return ""
        if "_" in text and " " not in text and len(text) < 80:
            return ""
        return text

    env_time = _map(environment.get("time"))
    env_context = _map(environment.get("context"))
    day_phase = str(
        environment.get("day_phase")
        or env_time.get("day_phase")
        or env_context.get("day_phase")
        or ""
    ).strip()
    meal_phase = str(
        environment.get("meal_phase")
        or env_time.get("meal_phase")
        or env_context.get("meal_phase")
        or ""
    ).strip()
    expires_at_ms = int(inner.get("expires_at_ms") or 0)
    inner_expired = bool(not expires_at_ms or expires_at_ms <= _now_ms())

    attention_target = "" if inner_expired else _inner_text(inner.get("attention_target"))
    if not attention_target:
        if _clamp(pressure.get("work")) >= 0.35:
            attention_target = "work_environment"
        elif _clamp(pressure.get("relationship")) >= 0.35 or _clamp(needs.get("contact")) >= 0.35:
            attention_target = "user_contact"
        elif _clamp(pressure.get("dream")) >= 0.25:
            attention_target = "dream_residue"
        elif _clamp(pressure.get("fatigue")) >= 0.45 or _clamp(needs.get("rest")) >= 0.45:
            attention_target = "body_rhythm"
        elif day_phase or meal_phase:
            attention_target = "current_environment"
        else:
            attention_target = "ambient_presence"

    preoccupation = "" if inner_expired else _inner_text(inner.get("current_preoccupation"))
    if not preoccupation:
        if _clamp(pressure.get("work")) >= 0.35:
            preoccupation = "后台环境是否有未完成变化"
        elif _clamp(pressure.get("relationship")) >= 0.35 or _clamp(needs.get("contact")) >= 0.35:
            preoccupation = "最近一次可见互动是否需要承接"
        elif _clamp(pressure.get("dream")) >= 0.25:
            preoccupation = "梦境余韵是否仍在影响情绪底色"
        elif day_phase or meal_phase:
            preoccupation = "当前时段和身体节律"
        else:
            preoccupation = "维持在场感"

    dialogue_bias = "" if inner_expired else _inner_text(inner.get("dialogue_bias"))
    if not dialogue_bias:
        bias_parts = ["先依据真实时段、最近可见事件和用户当下话语理解语境"]
        if day_phase:
            bias_parts.append(f"当前时段={day_phase}")
        if meal_phase:
            bias_parts.append(f"生活节律={meal_phase}")
        bias_parts.append("内心状态只影响语气和注意力，不替代事实上下文")
        dialogue_bias = "；".join(bias_parts)

    return {
        "being": {
            "alive_status": _map(state.get("runtime")).get("alive_status") or "alive",
            "last_integrated_ms": _map(state.get("runtime")).get("last_integrated_ms"),
            "continuity": _clamp(needs.get("memory_consolidation"), 0.2),
        },
        "drive": {
            "contact": _clamp(needs.get("contact")),
            "rest": _clamp(needs.get("rest")),
            "repair": _clamp(affect.get("tension")),
            "play": _clamp(needs.get("play"), 0.0),
            "curiosity": _clamp(pressure.get("work")),
            "inhibition": _clamp(max(float(pressure.get("fatigue") or 0.0), float(affect.get("irritation") or 0.0))),
        },
        "attention": {
            "mode": "anchored" if attention_target != "ambient_presence" else "diffuse",
            "target": attention_target,
            "stability": _clamp(1.0 - float(affect.get("arousal") or 0.0) * 0.5, 0.5),
        },
        "preoccupation": {
            "object": preoccupation,
            "pressure": _clamp(max(float(pressure.get("dream") or 0.0), float(pressure.get("relationship") or 0.0) * 0.5)),
            "expires_at_ms": int(inner.get("expires_at_ms") or 0),
        },
        "expression_bias": {
            "dialogue": dialogue_bias,
            "voice": str(_map(state.get("embodiment")).get("voice_tone") or ""),
            "body": str(_map(state.get("embodiment")).get("idle_animation") or ""),
        },
        "inner_world": inner,
        "organ_runtime": {
            "organ_count": int(organ_runtime.get("organ_count") or 0),
            "top_organs": list(organ_runtime.get("top_organs") or [])[:8],
            "synthesis": _map(organ_runtime.get("synthesis")),
        },
    }


def advance_inner_life_to_now(
    *,
    ledger: Any,
    person_id: str = DEFAULT_PERSON_ID,
    reason: str = "inner_snapshot",
    incoming_event: Mapping[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Advance 23-organ runtime to now and return the current inner-life snapshot.

    This is the semantic entrypoint for the digital-life inner state. External events are
    perturbations; ticks are sampling/landing points. The returned snapshot is a view over
    the current organ-synthesized state, not a generated monologue.
    """
    result = advance_life_to_now(
        ledger=ledger,
        person_id=person_id,
        reason=reason,
        incoming_event=incoming_event,
        force=force,
    )
    state = _map(result.get("state"))
    snapshot = _build_inner_life_snapshot_from_state(ledger=ledger, person_id=person_id, state=state)
    result["inner_life_snapshot"] = snapshot
    return result



def get_life_runtime_health(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID) -> dict[str, Any]:
    ensure_life_schema(ledger)
    state = get_life_state(ledger, person_id)
    now = _now_ms()
    runtime = _map(state.get("runtime"))
    last_integrated = int(runtime.get("last_integrated_ms") or 0)
    tick_interval = _int_env("HOST_LIFE_TICK_INTERVAL_SEC", 60, 30, 3600)
    age_ms = max(0, now - last_integrated) if last_integrated else 0
    alive_status = "alive"
    if not last_integrated:
        alive_status = "stale"
    elif age_ms > tick_interval * 2000:
        alive_status = "stale"
    elif age_ms > tick_interval * 1000:
        alive_status = "degraded_alive"
    inner = _map(state.get("inner_world"))
    expires_at = int(_float(inner.get("preoccupation_expires_at_ms") or inner.get("expires_at_ms")))
    stale_preoccupation_count = 1 if _text(inner.get("current_preoccupation"), 240) and expires_at and now >= expires_at else 0
    organ_state = get_organ_runtime_state(ledger=ledger, person_id=person_id)
    organs = _map(organ_state.get("organs"))
    organ_saturation_count = 0
    for value in organs.values():
        item = _map(value)
        if _float(item.get("activation")) >= 0.985 and _float(item.get("pressure")) >= 0.985:
            organ_saturation_count += 1
    rows = _conn(ledger).execute(
        "SELECT event_kind, content_summary, evidence_json, created_at_ms FROM life_experience_event WHERE person_id=? AND source_kind='inner_world' ORDER BY created_at_ms DESC LIMIT 20",
        (str(person_id or DEFAULT_PERSON_ID),),
    ).fetchall()
    last_monologue_ms = 0
    signatures: list[str] = []
    for row in rows:
        if str(row[0] or "") == "inner_stream_tick" and not last_monologue_ms:
            last_monologue_ms = int(row[3] or 0)
        if str(row[0] or "") == "inner_numeric_tick":
            evidence = _loads(row[2], {})
            signatures.append(str(_map(evidence).get("signature") or row[1] or ""))
    duplicate_inner_numeric_streak = 0
    if signatures:
        first = signatures[0]
        for sig in signatures:
            if sig == first:
                duplicate_inner_numeric_streak += 1
            else:
                break
    action = _map(state.get("life_action"))
    ineligible = 0
    kind = str(action.get("action_kind") or "")
    if kind == "dream_afterglow" and not _dream_action_eligible(state):
        ineligible = 1
    if kind in {"work_watch", "work_done_react"} and not _work_action_eligible(state):
        ineligible = 1
    last_autonomous_cycle_ms = int(_float(autonomy.get("last_autonomous_cycle_ms")))
    last_learning_cycle_ms = int(_float(self_growth.get("last_learning_cycle_ms") or autonomy.get("last_learning_cycle_ms")))
    active_agenda = [item for item in list(inner.get("self_agenda") or []) if isinstance(item, Mapping)]
    autonomous_cycle_stalled_minutes = round(max(0, now - last_autonomous_cycle_ms) / 60000.0, 2) if last_autonomous_cycle_ms else None
    learning_loop_stalled_minutes = round(max(0, now - last_learning_cycle_ms) / 60000.0, 2) if last_learning_cycle_ms else None
    empty_attention_minutes = autonomous_cycle_stalled_minutes if not _text(autonomy.get("current_attention") or inner.get("attention_kind"), 80) else 0
    self_agenda_empty_minutes = autonomous_cycle_stalled_minutes if not active_agenda else 0
    return {
        "person_id": str(person_id or DEFAULT_PERSON_ID),
        "alive_status": alive_status,
        "last_integrated_ms": last_integrated,
        "age_ms": age_ms,
        "tick_interval_s": tick_interval,
        "stale_preoccupation_count": stale_preoccupation_count,
        "organ_saturation_count": organ_saturation_count,
        "monologue_stalled_minutes": round(max(0, now - last_monologue_ms) / 60000.0, 2) if last_monologue_ms else None,
        "ineligible_action_selected_count": ineligible,
        "duplicate_inner_numeric_streak": duplicate_inner_numeric_streak,
        "autonomous_cycle_stalled_minutes": autonomous_cycle_stalled_minutes,
        "empty_attention_minutes": empty_attention_minutes,
        "self_agenda_empty_minutes": self_agenda_empty_minutes,
        "body_loop_stalled_minutes": autonomous_cycle_stalled_minutes if str(autonomy.get("body_loop_status") or "") != "state_driven" else 0,
        "user_input_dependency_detected": bool(autonomy.get("user_input_required_for_progress", False) or not active_agenda),
        "learning_loop_stalled_minutes": learning_loop_stalled_minutes,
        "growth_state_stale_minutes": learning_loop_stalled_minutes,
        "inner_world": {"attention_kind": _text(inner.get("attention_kind"), 80), "current_preoccupation": _text(inner.get("current_preoccupation"), 160), "monologue_due": bool(inner.get("monologue_due")), "monologue_suppression_reason": _text(inner.get("monologue_suppression_reason"), 160)},
        "autonomy": {"current_attention": _text(autonomy.get("current_attention"), 80), "active_agenda_count": len(active_agenda), "user_input_required_for_progress": bool(autonomy.get("user_input_required_for_progress", False))},
        "self_growth": {"current_learning_focus": _text(self_growth.get("current_learning_focus"), 80), "recent_growth_signal_count": len(list(self_growth.get("recent_growth_signals") or []))},
    }


def get_current_environment(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID) -> dict[str, Any]:
    state = get_life_state(ledger, person_id)
    env = _map(state.get("environment"))
    if env:
        return {"person_id": str(person_id or DEFAULT_PERSON_ID), "environment": env, "source": "digital_life_state"}
    try:
        from host_brain.runtime.environment_state import build_environment_snapshot  # noqa: PLC0415
        env = build_environment_snapshot()
    except Exception:
        env = {"now_ms": _now_ms(), "day_phase": _day_phase(_now_ms()), "meal_phase": "unknown", "surface_facts": []}
    return {"person_id": str(person_id or DEFAULT_PERSON_ID), "environment": env, "source": "computed"}


async def run_life_tick(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, reason: str = "periodic_tick", force_dispatch: bool = False) -> dict[str, Any]:
    integrated = advance_inner_life_to_now(ledger=ledger, person_id=person_id, reason=reason, force=True)
    after = _map(integrated.get("state")) or get_life_state(ledger, person_id)
    pack = _map(integrated.get("life_causality")) or build_life_director_pack(ledger=ledger, person_id=person_id, reason=reason)
    action = _map(pack.get("life_action")) or build_life_action_plan(state=after, director_pack=pack, source_kind=reason)
    visible = list(pack.get("visible_obligation") or integrated.get("visible_channels") or ["status_panel"])
    dispatch = await dispatch_life_embodiment(ledger=ledger, person_id=person_id, director_pack=pack, source_kind=reason, force=force_dispatch) if (force_dispatch or "embodiment" in visible) else {}
    proactive = await attempt_life_proactive_push(ledger=ledger, person_id=person_id, director_pack=pack, source_kind=reason, force=False, dry_run=False) if (_bool_env("HOST_LIFE_PROACTIVE_AUTO_ATTEMPT_ENABLED", True) and "proactive" in visible) else {}
    result = {"event_id": integrated.get("event_id"), "experience_event_id": integrated.get("experience_event_id"), "state_version_before": integrated.get("state_version_before"), "state_version_after": integrated.get("state_version_after"), "life_causality": pack, "life_action": action, "life_visibility": "ok" if visible else "failed", "visible_channels": visible, "embodiment_dispatch": dispatch, "proactive_attempt": proactive, "environment": integrated.get("environment"), "elapsed_ms": integrated.get("elapsed_ms")}
    global _LAST_TICK_RESULT; _LAST_TICK_RESULT = dict(result); return result


def _work_event(value: str) -> str:
    raw = str(value or "task_progress").strip().lower(); aliases = {"started": "task_started", "start": "task_started", "done": "task_done", "complete": "task_done", "completed": "task_done", "failed": "task_failed", "fail": "task_failed", "blocked": "task_blocked", "stuck": "task_blocked", "progress": "task_progress", "running": "task_progress"}
    return aliases.get(raw, raw if raw in {"task_started", "task_progress", "task_blocked", "task_done", "task_failed"} else "task_progress")


def _work_effect(event_type: str, urgency: str) -> dict[str, float]:
    b = 0.14 if urgency == "high" else 0.04 if urgency == "low" else 0.08
    if event_type == "task_started": return {"curiosity": 0.24 + b, "tension": 0.2 + b, "relief": 0.0, "pride": 0.0}
    if event_type == "task_blocked": return {"curiosity": 0.1, "tension": 0.34 + b, "relief": 0.0, "pride": 0.0, "irritation": 0.08}
    if event_type == "task_failed": return {"curiosity": 0.04, "tension": 0.3 + b, "relief": 0.0, "pride": 0.0, "irritation": 0.12}
    if event_type == "task_done": return {"curiosity": 0.02, "tension": -0.2, "relief": 0.45, "pride": 0.42}
    return {"curiosity": 0.12, "tension": 0.12 + b, "relief": 0.0, "pride": 0.0}


def _work_pressure(event_type: str, previous: float, urgency: str) -> float:
    b = 0.14 if urgency == "high" else 0.0 if urgency == "low" else 0.07
    if event_type == "task_started": return _clamp(max(previous, 0.58 + b))
    if event_type == "task_progress": return _clamp(max(previous * 0.92, 0.46 + b))
    if event_type == "task_blocked": return _clamp(max(previous, 0.74 + b))
    if event_type == "task_failed": return _clamp(max(previous, 0.62 + b))
    if event_type == "task_done": return _clamp(max(previous * 0.35, 0.28))
    return _clamp(previous)


async def record_work_environment_event(*, ledger: Any, event_type: str, person_id: str = DEFAULT_PERSON_ID, session_id: str = "", request_id: str = "", work_id: str = "", summary: str = "", urgency: str = "medium", emotional_effect: Mapping[str, Any] | None = None, platform: str = "", conversation_id: str = "", proactive_content: str = "") -> dict[str, Any]:
    ensure_life_schema(ledger); resolved = str(person_id or DEFAULT_PERSON_ID); event_type = _work_event(event_type)
    advance_life_to_now(ledger=ledger, person_id=resolved, reason="work_event", incoming_event={"event_type": event_type, "work_id": work_id, "summary": _text(summary, 160)}, force=False)
    before = get_life_state(ledger, resolved); after = copy.deepcopy(before); now = _now_ms(); effect = _map(emotional_effect) or _work_effect(event_type, urgency)
    workenv_id = _event_id("workenv")
    resolved_work_id = work_id or request_id or _event_id("work")
    memory_event_id = ""
    try:
        from host_brain.app.memory_event_store import MemoryEventStore  # noqa: PLC0415

        memory_event = MemoryEventStore(ledger).append_event(
            person_id=resolved,
            session_id=session_id,
            conversation_id=conversation_id or session_id,
            source_kind="work_environment_event",
            event_kind="work_event",
            visibility="system",
            content_summary=_text(summary or event_type, 500),
            refs={"work_environment_event_id": workenv_id, "work_id": resolved_work_id, "request_id": request_id, "event_type": event_type},
            causality={"urgency": urgency, "emotional_effect": effect},
            salience=0.5,
            valence=0.45 if event_type == "task_done" else -0.25 if event_type in {"task_failed", "task_blocked"} else -0.05,
            uncertainty=0.08,
            confidence=1.0,
            created_at_ms=now,
        )
        memory_event_id = str(memory_event.get("event_id") or "")
    except Exception:
        memory_event_id = ""
    _conn(ledger).execute("INSERT INTO work_environment_event(event_id, person_id, session_id, request_id, event_type, work_id, summary, urgency, emotional_effect_json, created_at_ms, active) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)", (workenv_id, resolved, session_id, request_id, event_type, resolved_work_id, _text(summary, 320), urgency, _dumps(effect), now)); _conn(ledger).commit()
    if memory_event_id:
        try:
            from host_brain.app.memory_event_store import MemoryEventStore  # noqa: PLC0415

            MemoryEventStore(ledger).update_projection_status(memory_event_id, {"work_environment_event": {"status": "OK", "event_id": workenv_id}})
        except Exception:
            pass
    work = _map(after.get("work")); work.update({"active": event_type not in {"task_done", "task_failed"}, "last_event_type": event_type, "work_id": work_id or request_id or work.get("work_id") or "", "summary": _text(summary or work.get("summary"), 200), "urgency": urgency, "updated_at_ms": now}); after["work"] = work
    p = _pressures(after); p["work"] = _work_pressure(event_type, p.get("work", 0.0), urgency); after["life_pressure"] = p
    affect = _map(after.get("affect")); affect["tension"] = _clamp(_float(affect.get("tension")) + _float(effect.get("tension"))); affect["pride"] = _clamp(_float(affect.get("pride")) + _float(effect.get("pride"))); affect["relief"] = _clamp(_float(affect.get("relief")) + _float(effect.get("relief"))); affect["irritation"] = _clamp(_float(affect.get("irritation")) + _float(effect.get("irritation"))); affect["mood"] = _mood(p, affect); after["affect"] = affect; after["version"] = int(after.get("version") or 1) + 1
    experience = append_life_experience(
        ledger=ledger,
        person_id=resolved,
        session_id=session_id,
        source_kind="work_environment_event",
        event_kind="work_event",
        content_summary=_text(summary or event_type, 500),
        evidence={"memory_event_id": memory_event_id, "event_type": event_type, "work_id": resolved_work_id, "urgency": urgency, "emotional_effect": effect},
        salience=max(float(p.get("work") or 0.0), 0.35),
        valence=0.45 if event_type == "task_done" else -0.25 if event_type in {"task_failed", "task_blocked"} else -0.05,
        uncertainty=0.08,
        now_ms=now,
        consume=True,
    )
    after = synthesize_life_state_from_organs(ledger=ledger, base_state=after, person_id=resolved, source_experience_event_id=str(experience.get("event_id") or ""), now_ms=now)
    scene, motive, _ = _scene(after, reason="work_event"); after["embodiment"].update(_embodiment(scene, motive, after)); after = _remember_proactive_target_in_state(after, platform=platform, conversation_id=conversation_id); _save_state(ledger, after)
    pack = build_life_director_pack(ledger=ledger, person_id=resolved, reason="work_event"); action = build_life_action_plan(state=after, director_pack=pack, source_kind=f"work_event:{event_type}"); after["life_action"] = action; _save_state(ledger, after); pack["life_action"] = action; visible = list(pack.get("visible_obligation") or ["status_panel"]); event_id = _append_state_event(ledger, resolved, "work_environment_event", event_type, before, after, {"emotional_effect": effect, "summary": _text(summary), "life_action": action}, visible)
    dispatch = await dispatch_life_embodiment(ledger=ledger, person_id=resolved, director_pack=pack, source_kind=f"work_event:{event_type}", force=event_type in {"task_started", "task_done", "task_failed", "task_blocked"})
    proactive = await attempt_life_proactive_push(ledger=ledger, person_id=resolved, director_pack=pack, source_kind=f"work_event:{event_type}", platform=platform, conversation_id=conversation_id, content=proactive_content, force=event_type in {"task_started", "task_done", "task_failed", "task_blocked"}, dry_run=False) if _bool_env("HOST_LIFE_WORK_EVENT_PROACTIVE_ENABLED", True) else {}
    return {"event_id": event_id, "state": after, "life_director": pack, "embodiment_dispatch": dispatch, "proactive_attempt": proactive}


def _dialogue_sources(director: Mapping[str, Any], user_message: str) -> list[str]:
    sources = ["life_director"]
    if director.get("scene"):
        sources.append(str(director.get("scene")))
    organ = _map(director.get("organ_life_influence"))
    sources.extend(str(x) for x in list(organ.get("active_organs") or [])[:4])
    if _looks_work(user_message):
        sources.append("work_environment")
    return list(dict.fromkeys([s for s in sources if s]))[:8]


async def record_dialogue_aftereffect(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, session_id: str = "", request_id: str = "", user_message: str = "", reply_text: str = "", life_director: Mapping[str, Any] | None = None, platform: str = "", conversation_id: str = "") -> dict[str, Any]:
    ensure_life_schema(ledger); resolved = str(person_id or DEFAULT_PERSON_ID); text = str(user_message or "")
    advance_life_to_now(ledger=ledger, person_id=resolved, reason="assistant_reply", incoming_event={"session_id": session_id, "request_id": request_id, "user_message": _text(text, 240)}, force=False)
    before = get_life_state(ledger, resolved); after = copy.deepcopy(before); now = _now_ms()
    proactive_episode = close_pending_proactive_episode_for_user_reply(ledger=ledger, person_id=resolved, session_id=session_id or conversation_id, request_id=request_id, user_message=text, now_ms=now)
    clock = _map(after.get("clock")); clock["last_user_seen_ms"] = now; clock["idle_minutes"] = 0; after["clock"] = clock
    p = _pressures(after); p["absence"] = 0.0; affect = _map(after.get("affect")); needs = _map(after.get("needs"))
    if any(t in text for t in ("慢", "半天", "怎么还", "不对", "没效果", "牛头不对马嘴")):
        affect["irritation"] = _clamp(_float(affect.get("irritation")) + 0.18); affect["tension"] = _clamp(_float(affect.get("tension")) + 0.2)
    if any(t in text for t in ("可以了", "不错", "挺好", "喜欢", "好了", "对了")):
        affect["attachment_warmth"] = _clamp(_float(affect.get("attachment_warmth")) + 0.1); affect["pride"] = _clamp(_float(affect.get("pride")) + 0.16); affect["irritation"] = _clamp(_float(affect.get("irritation")) - 0.1)
    if _looks_work(text):
        p["work"] = max(p["work"], 0.52); work = _map(after.get("work")); work.update({"active": True, "last_event_type": work.get("last_event_type") or "task_started", "summary": _text(text, 160), "updated_at_ms": now}); after["work"] = work
    needs["contact"] = _clamp(_float(needs.get("contact")) * 0.35); affect["loneliness"] = _clamp(_float(affect.get("loneliness")) * 0.25); p["relationship"] = _clamp(affect.get("attachment_warmth"), 0.4)
    after["life_pressure"] = p; affect["mood"] = _mood(p, affect); after["affect"] = affect; after["needs"] = needs; after["continuity"] = {**_map(after.get("continuity")), "last_interaction_digest": _text(text or reply_text, 160)}; after["version"] = int(after.get("version") or 1) + 1
    source_experience_event_id = str(_map(_map(proactive_episode).get("organ_delta_after")).get("event_id") or "")
    after = synthesize_life_state_from_organs(ledger=ledger, base_state=after, person_id=resolved, source_experience_event_id=source_experience_event_id, now_ms=now)
    scene, motive, _ = _scene(after, user_message=text, reason="dialogue_aftereffect"); after["embodiment"].update(_embodiment(scene, motive, after)); after = _remember_proactive_target_in_state(after, platform=platform, conversation_id=conversation_id); _save_state(ledger, after)
    director = dict(life_director or {}) or build_life_director_pack(ledger=ledger, person_id=resolved, session_id=session_id, user_message=text, reason="dialogue_aftereffect")
    action = build_life_action_plan(state=after, director_pack=director, source_kind="dialogue_aftereffect"); after["life_action"] = action; _save_state(ledger, after); director["life_action"] = action
    visible = list(director.get("visible_obligation") or ["status_panel", "dialogue"]); causality = {"state_version_before": int(before.get("version") or 1), "state_version_after": int(after.get("version") or 1), "applied_sources": _dialogue_sources(director, text), "changed_fields": _changed(before, after), "dialogue_effect": str(director.get("dialogue_influence") or "weak"), "embodiment_effect": str(_map(director.get("organ_life_influence")).get("embodiment_effect") or "weak"), "life_visibility": "ok" if visible else "failed", "visible_channels": visible, "life_action": action, "proactive_episode_binding": proactive_episode, "source_experience_event_id": source_experience_event_id, "private_leak_blocked": True}
    _append_state_event(ledger, resolved, "dialogue_aftereffect", "foreground_turn", before, after, causality, visible)
    _conn(ledger).execute("INSERT INTO dialogue_life_influence_log(event_id, request_id, person_id, session_id, user_message, reply_text, life_director_json, life_causality_json, created_at_ms) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)", (_event_id("lifedlg"), request_id, resolved, session_id, _text(text, 1000), _text(reply_text, 1000), _dumps(director), _dumps(causality), now)); _conn(ledger).commit()
    if _bool_env("HOST_LIFE_DIALOGUE_AFTEREFFECT_EMBODIMENT_ENABLED", True):
        causality["embodiment_dispatch"] = await dispatch_life_embodiment(ledger=ledger, person_id=resolved, director_pack=director, source_kind="dialogue_aftereffect", force=False)
    return causality


async def dispatch_life_embodiment(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, director_pack: Mapping[str, Any], source_kind: str = "life_tick", force: bool = False) -> dict[str, Any]:
    if not _bool_env("HOST_LIFE_EMBODIMENT_ENABLED", True):
        return {"status": "SKIPPED", "reason_code": "HOST_LIFE_EMBODIMENT_DISABLED"}
    advance_life_to_now(ledger=ledger, person_id=person_id, reason="body_dispatch", incoming_event={"source_kind": source_kind}, force=False)
    state = get_life_state(ledger, person_id); target = _map(director_pack.get("embodiment_target")) or _map(state.get("embodiment")); now = _now_ms(); cooldown = _int_env("HOST_LIFE_EMBODIMENT_COOLDOWN_MS", 30000, 0, 600000); last = int(_map(state.get("embodiment")).get("last_dispatch_ms") or 0)
    if not force and cooldown > 0 and now - last < cooldown:
        return {"status": "SKIPPED", "reason_code": "EMBODIMENT_COOLDOWN", "cooldown_ms": cooldown, "target": target}
    result: dict[str, Any] = {"vts": {}, "mateengine": {}}; status = "APPLIED"; reason = ""; emotion = str(target.get("emotion_tag") or "NEUTRAL").upper(); req_id = _event_id("lifebody")
    if _bool_env("HOST_LIFE_VTS_EMBODIMENT_ENABLED", True):
        try:
            from host_brain.app.vts_emotion_driver import _drive_stream_vts_emotion  # noqa: PLC0415
            result["vts"] = await _drive_stream_vts_emotion(request_id=req_id, emotion_context={"emotion_tag": emotion}, ts_ms=now)
        except Exception as exc:
            status = "DEGRADED"; reason = "VTS_DISPATCH_FAILED"; result["vts"] = {"status": "DEGRADED", "detail": str(exc)[:240]}
    if _bool_env("HOST_LIFE_MATEENGINE_EMBODIMENT_ENABLED", True):
        try:
            from host_brain.channels.mateengine import send_mateengine_action  # noqa: PLC0415
            mate_raw = send_mateengine_action(action="state", state=str(target.get("mateengine_state") or "idle"), emotion_tag=emotion, expression=str(target.get("expression") or ""))
            if hasattr(mate_raw, "as_dict"):
                result["mateengine"] = mate_raw.as_dict()
            elif hasattr(mate_raw, "__dict__"):
                result["mateengine"] = dict(mate_raw.__dict__)
            else:
                result["mateengine"] = {"value": str(mate_raw)}
        except Exception as exc:
            if status == "APPLIED": status = "DEGRADED"; reason = "MATEENGINE_DISPATCH_FAILED"
            result["mateengine"] = {"status": "DEGRADED", "detail": str(exc)[:240]}
    after = copy.deepcopy(state); emb = _map(after.get("embodiment")); emb.update(target); emb.update({"last_dispatch_ms": now, "last_dispatch_id": req_id}); after["embodiment"] = emb; after["version"] = int(after.get("version") or 1) + 1; _save_state(ledger, after)
    dispatch_id = _event_id("lifebody"); _conn(ledger).execute("INSERT INTO embodiment_dispatch_log(dispatch_id, person_id, source_kind, target_json, status, reason_code, result_json, created_at_ms) VALUES(?, ?, ?, ?, ?, ?, ?, ?)", (dispatch_id, person_id, source_kind, _dumps(target), status, reason, _dumps(result), now)); _conn(ledger).commit()
    try:
        experience = append_life_experience(
            ledger=ledger,
            person_id=person_id,
            source_kind="embodiment_dispatch",
            event_kind="embodiment_dispatch",
            content_summary=f"{source_kind}: {status}",
            evidence={"dispatch_id": dispatch_id, "target": target, "status": status, "reason_code": reason},
            salience=0.34 if status == "APPLIED" else 0.22,
            valence=0.08 if status == "APPLIED" else -0.12,
            uncertainty=0.08,
            now_ms=now,
            consume=True,
        )
        organ_after = synthesize_life_state_from_organs(ledger=ledger, base_state=after, person_id=person_id, source_experience_event_id=str(experience.get("event_id") or ""), now_ms=now)
        organ_after["version"] = int(organ_after.get("version") or 1) + 1
        _save_state(ledger, organ_after)
        result["life_experience_event_id"] = str(experience.get("event_id") or "")
    except Exception as exc:
        result["life_experience_error"] = type(exc).__name__
    return {"status": status, "reason_code": reason, "dispatch_id": dispatch_id, "target": target, "result": result}


def get_current_embodiment(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID) -> dict[str, Any]:
    state = get_life_state(ledger, person_id); row = _conn(ledger).execute("SELECT target_json, status, reason_code, result_json, created_at_ms FROM embodiment_dispatch_log WHERE person_id=? ORDER BY created_at_ms DESC LIMIT 1", (person_id,)).fetchone(); latest = {}
    if row:
        latest = {"target": _loads(row[0], {}), "status": row[1], "reason_code": row[2], "result": _loads(row[3], {}), "created_at_ms": row[4]}
    return {"person_id": person_id, "embodiment": _map(state.get("embodiment")), "latest_dispatch": latest}


def get_current_life_action(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID) -> dict[str, Any]:
    state = get_life_state(ledger, person_id)
    action = _map(state.get("life_action"))
    if not action or _life_action_is_stale_work(action, state):
        director = build_life_director_pack(ledger=ledger, person_id=person_id, reason="action_current")
        action = build_life_action_plan(state=state, director_pack=director, source_kind="action_current")
    return {"person_id": person_id, "state_version": int(state.get("version") or 1), "life_action": action}


def record_life_experience(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, session_id: str = "", source_kind: str = "manual_ingest", event_kind: str = "inner_stream_tick", content_summary: str = "", evidence: Mapping[str, Any] | None = None, salience: float = 0.3, valence: float = 0.0, uncertainty: float = 0.1, consume: bool = True) -> dict[str, Any]:
    ensure_life_schema(ledger)
    resolved = str(person_id or DEFAULT_PERSON_ID)
    before = get_life_state(ledger, resolved)
    now = _now_ms()
    experience = append_life_experience(
        ledger=ledger,
        person_id=resolved,
        session_id=session_id,
        source_kind=source_kind,
        event_kind=event_kind,
        content_summary=content_summary,
        evidence=evidence or {},
        salience=salience,
        valence=valence,
        uncertainty=uncertainty,
        now_ms=now,
        consume=consume,
    )
    after = synthesize_life_state_from_organs(ledger=ledger, base_state=before, person_id=resolved, source_experience_event_id=str(experience.get("event_id") or ""), now_ms=now) if consume else copy.deepcopy(before)
    if consume:
        after["version"] = int(after.get("version") or 1) + 1
        _save_state(ledger, after)
    director = build_life_director_pack(ledger=ledger, person_id=resolved, reason=event_kind)
    action = build_life_action_plan(state=after, director_pack=director, source_kind=event_kind)
    after["life_action"] = action
    _save_state(ledger, after)
    visible = list(director.get("visible_obligation") or ["status_panel"])
    state_event_id = _append_state_event(ledger, resolved, "life_experience", event_kind, before, after, {"experience": experience, "life_action": action}, visible)
    return {"event_id": experience.get("event_id"), "memory_event_id": experience.get("memory_event_id"), "state_event_id": state_event_id, "state": after, "life_director": director, "life_action": action, "visible_channels": visible, "experience": experience}


def get_recent_proactive_outbox(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, limit: int = 20) -> dict[str, Any]:
    ensure_life_schema(ledger); person_id = str(person_id or DEFAULT_PERSON_ID); limit = max(1, min(int(limit or 20), 100))
    rows = _conn(ledger).execute("SELECT outbox_id, source_kind, motive, pressure, target_json, message_json, gate_json, status, reason_code, result_json, created_at_ms FROM proactive_life_outbox WHERE person_id=? ORDER BY created_at_ms DESC LIMIT ?", (person_id, limit)).fetchall()
    items = [{"outbox_id": r[0], "source_kind": r[1], "motive": r[2], "pressure": float(r[3] or 0.0), "target": _loads(r[4], {}), "message": _loads(r[5], {}), "gate": _loads(r[6], {}), "status": r[7], "reason_code": r[8], "result": _loads(r[9], {}), "created_at_ms": r[10]} for r in rows]
    return {"person_id": person_id, "items": items}


def get_recent_causality(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, limit: int = 20) -> dict[str, Any]:
    ensure_life_schema(ledger); person_id = str(person_id or DEFAULT_PERSON_ID); limit = max(1, min(int(limit or 20), 100))
    events = [{"event_id": r[0], "source_kind": r[1], "event_type": r[2], "delta": _loads(r[3], {}), "visible_channels": _loads(r[4], []), "created_at_ms": r[5]} for r in _conn(ledger).execute("SELECT event_id, source_kind, event_type, delta_json, visible_channels_json, created_at_ms FROM digital_life_state_event WHERE person_id=? ORDER BY created_at_ms DESC LIMIT ?", (person_id, limit)).fetchall()]
    dialogs = [{"event_id": r[0], "request_id": r[1], "life_director": _loads(r[2], {}), "life_causality": _loads(r[3], {}), "created_at_ms": r[4]} for r in _conn(ledger).execute("SELECT event_id, request_id, life_director_json, life_causality_json, created_at_ms FROM dialogue_life_influence_log WHERE person_id=? ORDER BY created_at_ms DESC LIMIT ?", (person_id, limit)).fetchall()]
    dispatches = [{"dispatch_id": r[0], "source_kind": r[1], "target": _loads(r[2], {}), "status": r[3], "reason_code": r[4], "created_at_ms": r[5]} for r in _conn(ledger).execute("SELECT dispatch_id, source_kind, target_json, status, reason_code, created_at_ms FROM embodiment_dispatch_log WHERE person_id=? ORDER BY created_at_ms DESC LIMIT ?", (person_id, limit)).fetchall()]
    works = [{"event_id": r[0], "event_type": r[1], "work_id": r[2], "summary": r[3], "urgency": r[4], "emotional_effect": _loads(r[5], {}), "created_at_ms": r[6]} for r in _conn(ledger).execute("SELECT event_id, event_type, work_id, summary, urgency, emotional_effect_json, created_at_ms FROM work_environment_event WHERE person_id=? ORDER BY created_at_ms DESC LIMIT ?", (person_id, limit)).fetchall()]
    proactive = list(get_recent_proactive_outbox(ledger=ledger, person_id=person_id, limit=limit).get("items") or [])
    organ_state = get_organ_runtime_state(ledger=ledger, person_id=person_id)
    experience = list(get_recent_life_experience(ledger=ledger, person_id=person_id, limit=limit).get("items") or [])
    proactive_episodes = list(get_recent_proactive_episodes(ledger=ledger, person_id=person_id, limit=limit).get("items") or [])
    latest_state = get_life_state(ledger, person_id)
    learning_state = _map(latest_state.get("learning_state"))
    self_growth = _map(latest_state.get("self_growth"))
    inner = _map(latest_state.get("inner_world"))
    return {"person_id": person_id, "latest_state": latest_state, "runtime_health": get_life_runtime_health(ledger=ledger, person_id=person_id), "environment": _map(latest_state.get("environment")), "last_tick_result": dict(_LAST_TICK_RESULT), "state_events": events, "dialogue_influence": dialogs, "embodiment_dispatches": dispatches, "work_events": works, "proactive_outbox": proactive, "life_experience": experience, "organ_runtime": {"organ_count": organ_state.get("organ_count"), "top_organs": organ_state.get("top_organs", [])}, "proactive_episodes": proactive_episodes, "autonomous_chain": {"self_agenda": list(inner.get("self_agenda") or [])[:8], "learning_signals": list(learning_state.get("learning_signals") or [])[:8], "growth_signals": list(self_growth.get("recent_growth_signals") or [])[:8], "active_projects": list(self_growth.get("active_projects") or [])[:8]}}



def recalibrate_inner_life_state(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID) -> dict[str, Any]:
    ensure_life_schema(ledger)
    resolved = str(person_id or DEFAULT_PERSON_ID)
    before = get_life_state(ledger, resolved)
    now = _now_ms()
    after = copy.deepcopy(before)
    inner = _map(after.get("inner_world"))
    numeric = _map(inner.get("numeric_state"))
    anchor = _text(numeric.get("current_preoccupation") or numeric.get("attention_target"), 180)
    if not anchor:
        inner.update({"current_preoccupation": "", "attention_target": "", "unresolved_loop": "", "preoccupation_expires_at_ms": 0, "monologue_due": False, "monologue_suppression_reason": _text(inner.get("monologue_suppression_reason") or "RECALIBRATED_NO_CONCRETE_ANCHOR", 160)})
    if not _text(inner.get("dream_residue"), 240):
        pressure = _map(after.get("life_pressure"))
        pressure["dream"] = min(_clamp(pressure.get("dream")), 0.12)
        after["life_pressure"] = pressure
        if str(_map(after.get("life_action")).get("action_kind") or "") == "dream_afterglow":
            after["life_action"] = {}
    after["inner_world"] = inner
    after["version"] = int(after.get("version") or 1) + 1
    _save_state(ledger, after)
    conn = _conn(ledger)
    adjusted = 0
    for row in conn.execute("SELECT organ_id, state_json FROM organ_runtime_state WHERE person_id=?", (resolved,)).fetchall():
        organ_id = str(row[0] or "")
        state = _loads(row[1], {})
        if not isinstance(state, dict):
            continue
        changed = False
        for key, cap in (("activation", 0.78), ("pressure", 0.76), ("fatigue", 0.88)):
            value = _float(state.get(key))
            if value > 0.94:
                state[key] = cap
                changed = True
        if changed:
            state["updated_at_ms"] = now
            conn.execute("UPDATE organ_runtime_state SET state_json=?, updated_at_ms=? WHERE person_id=? AND organ_id=?", (_dumps(state), now, resolved, organ_id))
            adjusted += 1
    conn.commit()
    event = append_life_experience(ledger=ledger, person_id=resolved, session_id="inner_life_recalibration", source_kind="system", event_kind="inner_life_recalibrated", content_summary="inner life state recalibrated", evidence={"cleared_stale_preoccupation": not bool(anchor), "adjusted_organs": adjusted}, salience=0.12, valence=0.0, uncertainty=0.02, now_ms=now, consume=False)
    return {"status": "OK", "person_id": resolved, "adjusted_organs": adjusted, "event_id": event.get("event_id"), "state_version": int(after.get("version") or 1)}

def start_life_continuity_loop(*, ledger: Any, logger: Any | None = None) -> dict[str, Any]:
    global _LIFE_LOOP_TASK, _LIFE_LOOP_STOP
    ensure_life_schema(ledger)
    if not _bool_env("HOST_LIFE_CONTINUITY_ENABLED", True):
        return {"running": False, "status": "SKIPPED", "reason_code": "HOST_LIFE_CONTINUITY_DISABLED"}
    if _LIFE_LOOP_TASK is not None and not _LIFE_LOOP_TASK.done():
        return {"running": True, "status": "OK", "reason_code": "ALREADY_RUNNING"}
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {"running": False, "status": "DEGRADED", "reason_code": "NO_RUNNING_EVENT_LOOP"}
    interval = _int_env("HOST_LIFE_TICK_INTERVAL_SEC", 60, 30, 3600); _LIFE_LOOP_STOP = asyncio.Event(); _LIFE_LOOP_TASK = loop.create_task(_life_loop_runner(ledger=ledger, stop_event=_LIFE_LOOP_STOP, interval_s=interval, logger=logger or LOGGER))
    return {"running": True, "status": "OK", "interval_s": interval}


async def _life_loop_runner(*, ledger: Any, stop_event: asyncio.Event, interval_s: int, logger: Any) -> None:
    try:
        if _bool_env("HOST_LIFE_TICK_ON_STARTUP", True):
            await run_life_tick(ledger=ledger, reason="startup_tick", force_dispatch=True)
    except Exception as exc:
        logger.warning("Life continuity startup tick degraded: %s", exc)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(interval_s)); break
        except asyncio.TimeoutError:
            pass
        try:
            await run_life_tick(ledger=ledger, reason="periodic_tick", force_dispatch=False)
        except Exception as exc:
            logger.warning("Life continuity periodic tick degraded: %s", exc)


async def stop_life_continuity_loop() -> dict[str, Any]:
    global _LIFE_LOOP_TASK, _LIFE_LOOP_STOP
    task = _LIFE_LOOP_TASK
    if _LIFE_LOOP_STOP is not None:
        _LIFE_LOOP_STOP.set()
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()
        except Exception:
            pass
    _LIFE_LOOP_TASK = None; _LIFE_LOOP_STOP = None
    return {"running": False, "status": "STOPPED"}


__all__ = ["advance_inner_life_to_now", "advance_life_to_now", "attempt_life_proactive_push", "build_life_action_plan", "build_life_director_pack", "dispatch_life_embodiment", "ensure_life_schema", "get_current_embodiment", "get_current_environment", "get_current_life_action", "get_life_runtime_health", "get_life_state", "get_recent_causality", "get_recent_proactive_outbox", "record_dialogue_aftereffect", "record_life_experience", "record_work_environment_event", "run_life_tick", "recalibrate_inner_life_state", "start_life_continuity_loop", "stop_life_continuity_loop"]
