from __future__ import annotations

import copy
import json
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any

DEFAULT_PERSON_ID = "default"

ORGAN_IDS: tuple[str, ...] = (
    "limbic",
    "social_brain",
    "hippocampus",
    "default_mode",
    "circadian",
    "glymphatic",
    "endocrine",
    "immune",
    "prefrontal",
    "initiative_governor",
    "motor_cortex",
    "cerebellum",
    "vestibular",
    "somatic",
    "interoception",
    "thalamus",
    "language_system",
    "mirror_neuron",
    "pain_reward",
    "ans",
    "peripheral",
    "proprioception",
    "cortex",
)

ORGAN_PROFILES: dict[str, dict[str, str]] = {
    "limbic": {"drive": "emotional_attachment", "label": "情绪靠近"},
    "social_brain": {"drive": "social_contact", "label": "关系回应"},
    "hippocampus": {"drive": "continuity_binding", "label": "连续性"},
    "default_mode": {"drive": "inner_drift", "label": "内心流"},
    "circadian": {"drive": "rhythm_rest", "label": "昼夜节律"},
    "glymphatic": {"drive": "recovery_dream", "label": "恢复清理"},
    "endocrine": {"drive": "slow_pressure", "label": "长周期压力"},
    "immune": {"drive": "risk_guard", "label": "风险警觉"},
    "prefrontal": {"drive": "inhibit_or_focus", "label": "前额叶抑制"},
    "initiative_governor": {"drive": "proactive_threshold", "label": "主动阈值"},
    "motor_cortex": {"drive": "body_action", "label": "身体动作"},
    "cerebellum": {"drive": "motion_smoothing", "label": "动作平滑"},
    "vestibular": {"drive": "body_stability", "label": "身体稳定"},
    "somatic": {"drive": "embodied_presence", "label": "身体存在"},
    "interoception": {"drive": "internal_sense", "label": "内部感受"},
    "thalamus": {"drive": "attention_route", "label": "注意路由"},
    "language_system": {"drive": "expression_bias", "label": "表达调制"},
    "mirror_neuron": {"drive": "user_affect_mirroring", "label": "情绪映射"},
    "pain_reward": {"drive": "reward_learning", "label": "奖惩学习"},
    "ans": {"drive": "tension_relaxation", "label": "自主神经"},
    "peripheral": {"drive": "channel_presence", "label": "外部通道"},
    "proprioception": {"drive": "posture_memory", "label": "姿态记忆"},
    "cortex": {"drive": "scene_integration", "label": "场景整合"},
}

ORGAN_SCHEMA_SQL: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS life_experience_event (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, person_id TEXT NOT NULL, session_id TEXT, source_kind TEXT NOT NULL, event_kind TEXT NOT NULL, content_summary TEXT NOT NULL DEFAULT '', evidence_json TEXT NOT NULL DEFAULT '{}', salience REAL NOT NULL DEFAULT 0.0, valence REAL NOT NULL DEFAULT 0.0, uncertainty REAL NOT NULL DEFAULT 0.0, created_at_ms INTEGER NOT NULL, consumed_by_organs INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS organ_runtime_state (person_id TEXT NOT NULL, organ_id TEXT NOT NULL, state_json TEXT NOT NULL DEFAULT '{}', updated_at_ms INTEGER NOT NULL, PRIMARY KEY(person_id, organ_id))",
    "CREATE TABLE IF NOT EXISTS organ_runtime_event (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, experience_event_id TEXT NOT NULL, person_id TEXT NOT NULL, organ_id TEXT NOT NULL, delta_json TEXT NOT NULL DEFAULT '{}', state_before_json TEXT NOT NULL DEFAULT '{}', state_after_json TEXT NOT NULL DEFAULT '{}', created_at_ms INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS organ_coupling_event (id INTEGER PRIMARY KEY AUTOINCREMENT, coupling_id TEXT NOT NULL UNIQUE, source_experience_event_id TEXT, person_id TEXT NOT NULL, source_organs_json TEXT NOT NULL DEFAULT '[]', target_organ_id TEXT NOT NULL, effect_json TEXT NOT NULL DEFAULT '{}', created_at_ms INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS life_state_synthesis_log (id INTEGER PRIMARY KEY AUTOINCREMENT, synthesis_id TEXT NOT NULL UNIQUE, person_id TEXT NOT NULL, source_experience_event_id TEXT, organ_summary_json TEXT NOT NULL DEFAULT '{}', state_patch_json TEXT NOT NULL DEFAULT '{}', created_at_ms INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS proactive_interaction_episode (id INTEGER PRIMARY KEY AUTOINCREMENT, episode_id TEXT NOT NULL UNIQUE, person_id TEXT NOT NULL, source_action_id TEXT, source_outbox_id TEXT, motive TEXT NOT NULL DEFAULT '', organ_sources_json TEXT NOT NULL DEFAULT '[]', message_text TEXT NOT NULL DEFAULT '', expected_response_kind TEXT NOT NULL DEFAULT '', sent_at_ms INTEGER NOT NULL DEFAULT 0, user_response_turn_id TEXT, outcome TEXT NOT NULL DEFAULT 'pending', organ_delta_after_json TEXT NOT NULL DEFAULT '{}', closed_at_ms INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}')",
    "CREATE INDEX IF NOT EXISTS idx_life_experience_person_ts ON life_experience_event(person_id, created_at_ms DESC)",
    "CREATE INDEX IF NOT EXISTS idx_life_experience_unconsumed ON life_experience_event(person_id, consumed_by_organs, created_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_organ_runtime_event_person_ts ON organ_runtime_event(person_id, created_at_ms DESC)",
    "CREATE INDEX IF NOT EXISTS idx_organ_coupling_person_ts ON organ_coupling_event(person_id, created_at_ms DESC)",
    "CREATE INDEX IF NOT EXISTS idx_life_state_synthesis_person_ts ON life_state_synthesis_log(person_id, created_at_ms DESC)",
    "CREATE INDEX IF NOT EXISTS idx_proactive_episode_person_ts ON proactive_interaction_episode(person_id, sent_at_ms DESC)",
    "CREATE INDEX IF NOT EXISTS idx_proactive_episode_pending ON proactive_interaction_episode(person_id, outcome, sent_at_ms DESC)",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _event_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _conn(ledger: Any) -> Any:
    conn = getattr(ledger, "_conn", None)
    if conn is None:
        raise RuntimeError("ledger sqlite connection unavailable")
    return conn


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


def ensure_organ_schema(ledger: Any) -> None:
    conn = _conn(ledger)
    for sql in ORGAN_SCHEMA_SQL:
        conn.execute(sql)
    conn.commit()


def _default_organ_state(organ_id: str, now_ms: int | None = None) -> dict[str, Any]:
    profile = ORGAN_PROFILES.get(organ_id, {})
    return {
        "organ_id": organ_id,
        "activation": 0.0,
        "valence": 0.5,
        "fatigue": 0.0,
        "pressure": 0.0,
        "confidence": 0.2,
        "dominant_drive": profile.get("drive") or organ_id,
        "label": profile.get("label") or organ_id,
        "recent_traces": [],
        "cooldowns": {},
        "memory_trace": {},
        "updated_at_ms": int(now_ms or _now_ms()),
    }


def _get_organ_state(conn: Any, person_id: str, organ_id: str, now_ms: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT state_json FROM organ_runtime_state WHERE person_id=? AND organ_id=?",
        (person_id, organ_id),
    ).fetchone()
    if row:
        state = _loads(row[0], {})
        if isinstance(state, dict) and state:
            base = _default_organ_state(organ_id, now_ms)
            base.update(state)
            base["organ_id"] = organ_id
            return base
    return _default_organ_state(organ_id, now_ms)


def _save_organ_state(conn: Any, person_id: str, state: Mapping[str, Any], now_ms: int) -> None:
    organ_id = str(state.get("organ_id") or "")
    if not organ_id:
        return
    payload = dict(state)
    payload["updated_at_ms"] = now_ms
    conn.execute(
        "INSERT INTO organ_runtime_state(person_id, organ_id, state_json, updated_at_ms) VALUES(?, ?, ?, ?) ON CONFLICT(person_id, organ_id) DO UPDATE SET state_json=excluded.state_json, updated_at_ms=excluded.updated_at_ms",
        (person_id, organ_id, _dumps(payload), now_ms),
    )


def _decay_state(state: Mapping[str, Any], elapsed_min: float) -> dict[str, Any]:
    after = copy.deepcopy(dict(state))
    minutes = max(0.0, min(float(elapsed_min), 720.0))
    pressure_decay = 0.985 ** minutes
    activation_decay = 0.975 ** minutes
    fatigue_decay = 0.992 ** minutes
    after["activation"] = _clamp(_float(after.get("activation")) * activation_decay)
    after["pressure"] = _clamp(_float(after.get("pressure")) * pressure_decay)
    after["fatigue"] = _clamp(_float(after.get("fatigue")) * fatigue_decay)
    return after


def _apply_delta(state: Mapping[str, Any], delta: Mapping[str, Any], *, trace: Mapping[str, Any], now_ms: int) -> dict[str, Any]:
    after = copy.deepcopy(dict(state))
    for key in ("activation", "pressure", "fatigue", "confidence"):
        if key in delta:
            after[key] = _clamp(_float(after.get(key)) + _float(delta.get(key)))
    if "valence" in delta:
        after["valence"] = _clamp(_float(after.get("valence"), 0.5) + _float(delta.get("valence")))
    if delta.get("dominant_drive"):
        after["dominant_drive"] = _text(delta.get("dominant_drive"), 80)
    memory = _map(after.get("memory_trace"))
    for key, value in _map(delta.get("memory_trace")).items():
        memory[key] = value
    after["memory_trace"] = memory
    cooldowns = _map(after.get("cooldowns"))
    for key, value in _map(delta.get("cooldowns")).items():
        cooldowns[key] = value
    after["cooldowns"] = cooldowns
    traces = list(after.get("recent_traces") or [])
    traces.insert(0, {"ts_ms": now_ms, **dict(trace or {}), "delta": {k: v for k, v in dict(delta).items() if k not in {"memory_trace", "cooldowns"}}})
    after["recent_traces"] = traces[:8]
    after["updated_at_ms"] = now_ms
    return after


def _base_event_deltas(event_kind: str, evidence: Mapping[str, Any], content: str, salience: float, valence: float, now_ms: int) -> dict[str, dict[str, Any]]:
    kind = str(event_kind or "").strip()
    text = str(content or "")
    deltas: dict[str, dict[str, Any]] = {}

    def add(organ: str, **delta: Any) -> None:
        current = deltas.setdefault(organ, {})
        for key, value in delta.items():
            if key in {"activation", "pressure", "fatigue", "confidence", "valence"}:
                current[key] = _float(current.get(key)) + _float(value)
            elif key in {"memory_trace", "cooldowns"}:
                merged = _map(current.get(key))
                merged.update(_map(value))
                current[key] = merged
            else:
                current[key] = value

    if kind in {"periodic_tick", "startup_tick", "manual_tick", "silence_tick", "life_integrated", "environment_tick", "heartbeat_tick", "numeric_inner_tick"}:
        idle_minutes = int(_float(evidence.get("idle_minutes")))
        absence = _clamp(idle_minutes / 90.0)
        fatigue = _clamp(_float(evidence.get("fatigue")))
        elapsed_minutes = max(0.0, _float(evidence.get("elapsed_ms")) / 60000.0)
        env = _map(evidence.get("environment"))
        day_phase = str(env.get("day_phase") or evidence.get("day_phase") or "")
        meal_phase = str(env.get("meal_phase") or "")
        add("social_brain", activation=0.05 + absence * 0.18, pressure=absence * 0.22, confidence=0.04, dominant_drive="awaiting_contact", memory_trace={"idle_minutes": idle_minutes})
        add("limbic", activation=absence * 0.14, pressure=absence * 0.16, valence=-absence * 0.05, dominant_drive="warm_reconnect")
        add("interoception", activation=absence * 0.12, pressure=absence * 0.11, dominant_drive="felt_absence")
        add("prefrontal", activation=0.04, pressure=absence * 0.04, dominant_drive="suppress_or_allow_ping")
        add("initiative_governor", activation=0.04, pressure=absence * 0.08, dominant_drive="proactive_threshold")
        add("circadian", activation=0.05, pressure=fatigue * 0.1, fatigue=fatigue * 0.08, dominant_drive="rhythm_rest")
        if elapsed_minutes > 0.0:
            add("cortex", activation=min(0.16, elapsed_minutes / 600.0), dominant_drive="continuous_integration", memory_trace={"elapsed_minutes": round(elapsed_minutes, 2)})
        if day_phase:
            phase_pressure = 0.16 if day_phase in {"night", "late_night"} else 0.08 if day_phase in {"dawn", "morning"} else 0.04
            phase_fatigue = 0.16 if day_phase == "late_night" else 0.1 if day_phase == "night" else 0.03
            add("circadian", activation=0.16, pressure=phase_pressure, fatigue=phase_fatigue, dominant_drive=f"rhythm_{day_phase}", memory_trace={"day_phase": day_phase})
            add("language_system", activation=0.1, dominant_drive="temporal_expression_boundary", memory_trace={"day_phase": day_phase, "meal_phase": meal_phase})
            add("prefrontal", activation=0.08, pressure=0.08 if day_phase not in {"dawn", "morning"} else 0.03, dominant_drive="time_consistency_guard")
        if meal_phase and meal_phase != "none":
            add("interoception", activation=0.12, pressure=0.08, dominant_drive="routine_body_window", memory_trace={"meal_phase": meal_phase})
    elif kind == "user_message":
        annoyance = any(t in text for t in ("烦", "别发", "别一直", "莫名其妙", "鬼扯", "很呆", "烂", "不对", "没效果", "牛头不对马嘴"))
        positive = any(t in text for t in ("可以了", "不错", "挺好", "喜欢", "好了", "对了", "继续", "开始"))
        add("social_brain", activation=0.28, pressure=-0.35, valence=0.16 if positive else 0.05, confidence=0.1, dominant_drive="contact_received")
        add("limbic", activation=0.22, pressure=-0.26, valence=0.12 if positive else 0.02, dominant_drive="reconnected")
        add("hippocampus", activation=0.18, confidence=0.08, dominant_drive="bind_current_turn", memory_trace={"last_user_message": _text(text, 180)})
        add("mirror_neuron", activation=0.16, valence=-0.18 if annoyance else 0.08, dominant_drive="mirror_user_tone")
        add("thalamus", activation=0.14, dominant_drive="route_current_message")
        add("language_system", activation=0.12, dominant_drive="expression_bias")
        if positive:
            add("pain_reward", activation=0.22, pressure=-0.2, valence=0.18, dominant_drive="reward_learning")
            add("prefrontal", pressure=-0.08, dominant_drive="relax_inhibition")
        if annoyance:
            add("pain_reward", activation=0.34, pressure=0.28, valence=-0.24, dominant_drive="negative_feedback")
            add("prefrontal", activation=0.28, pressure=0.32, dominant_drive="inhibit_proactive")
            add("initiative_governor", activation=0.24, pressure=0.38, dominant_drive="cooldown_proactive", cooldowns={"proactive_until_ms": now_ms + 3_600_000})
            add("limbic", pressure=0.12, valence=-0.14, dominant_drive="hurt_but_attentive")
    elif kind == "assistant_reply":
        add("language_system", activation=0.12, confidence=0.05, dominant_drive="expression_trace", memory_trace={"last_reply": _text(text, 180)})
        add("cortex", activation=0.08, confidence=0.05, dominant_drive="scene_integration")
    elif kind == "life_proactive_sent":
        add("social_brain", activation=0.24, pressure=0.12, dominant_drive="expecting_response")
        add("limbic", activation=0.18, pressure=0.1, dominant_drive="small_approach")
        add("initiative_governor", activation=0.18, pressure=0.18, dominant_drive="proactive_cooldown", cooldowns={"last_proactive_sent_ms": now_ms})
        add("pain_reward", activation=0.08, dominant_drive="awaiting_reward")
    elif kind == "life_proactive_user_response":
        annoyed = str(evidence.get("outcome") or "") == "annoyed" or any(t in text for t in ("烦", "别发", "别一直", "莫名其妙"))
        add("social_brain", activation=0.34, pressure=-0.42 if not annoyed else 0.12, valence=-0.24 if annoyed else 0.22, dominant_drive="proactive_outcome")
        add("limbic", activation=0.26, pressure=-0.35 if not annoyed else 0.16, valence=-0.2 if annoyed else 0.18, dominant_drive="response_relief" if not annoyed else "hurt_feedback")
        add("pain_reward", activation=0.32, pressure=0.34 if annoyed else -0.24, valence=-0.28 if annoyed else 0.22, dominant_drive="negative_feedback" if annoyed else "reward_learning")
        add("initiative_governor", activation=0.22, pressure=0.38 if annoyed else -0.16, dominant_drive="suppress_proactive" if annoyed else "allow_later_contact")
    elif kind == "work_event":
        event_type = str(evidence.get("event_type") or "")
        blocked = event_type in {"task_failed", "task_blocked"}
        done = event_type == "task_done"
        add("prefrontal", activation=0.32, pressure=0.12 if done else 0.34, dominant_drive="work_focus")
        add("cortex", activation=0.28, pressure=0.08 if done else 0.24, dominant_drive="frontstage_backend_split")
        add("immune", activation=0.26 if blocked else 0.1, pressure=0.34 if blocked else 0.08, dominant_drive="risk_guard")
        add("limbic", activation=0.16, pressure=-0.16 if done else 0.16, valence=0.18 if done else -0.02, dominant_drive="work_relief" if done else "work_tension")
        add("pain_reward", activation=0.18, pressure=-0.22 if done else 0.05, valence=0.2 if done else 0.0, dominant_drive="work_reward" if done else "work_monitor")
    elif kind == "dream_generated":
        add("default_mode", activation=0.42, pressure=0.38, dominant_drive="dream_residue", memory_trace={"dream_residue": _text(content, 220)})
        add("hippocampus", activation=0.24, pressure=0.12, dominant_drive="dream_trace", memory_trace={"dream_trace": _text(content, 180)})
        add("glymphatic", activation=0.24, pressure=0.18, dominant_drive="dream_cleanup")
    elif kind == "diary_generated":
        add("hippocampus", activation=0.28, pressure=0.1, dominant_drive="diary_continuity", memory_trace={"diary_residue": _text(content, 220)})
        add("default_mode", activation=0.22, pressure=0.12, dominant_drive="day_residue")
    elif kind == "inner_stream_tick":
        tendency = _text(evidence.get("felt_tendency") or evidence.get("next_tendency"), 120)
        attention = _text(evidence.get("attention_target") or evidence.get("focus"), 120)
        inhibition = _float(evidence.get("inhibition"))
        add("default_mode", activation=0.24, pressure=0.12, dominant_drive="current_preoccupation", memory_trace={"current_preoccupation": _text(content, 220), "attention_target": attention, "felt_tendency": tendency})
        add("hippocampus", activation=0.14, pressure=0.06, dominant_drive="private_continuity_trace", memory_trace={"private_continuity_trace": _text(content, 160)})
        add("limbic", activation=0.1, pressure=0.08, dominant_drive=tendency or "felt_tendency")
        add("prefrontal", activation=0.08, pressure=inhibition or 0.04, dominant_drive="inner_inhibition")
        add("language_system", activation=0.1, dominant_drive="inner_dialogue_bias", memory_trace={"dialogue_bias": tendency})
        add("initiative_governor", activation=0.08, pressure=0.05, dominant_drive="inner_proactive_threshold")
        add("thalamus", activation=0.12, dominant_drive="attention_drift")
    elif kind == "embodiment_dispatch":
        add("somatic", activation=0.28, pressure=0.08, dominant_drive="embodied_presence", memory_trace={"last_body": _map(evidence.get("target"))})
        add("proprioception", activation=0.24, pressure=0.05, dominant_drive="posture_memory", memory_trace={"last_body": _map(evidence.get("target"))})
        add("motor_cortex", activation=0.2, pressure=0.08, dominant_drive="body_action")
        add("cerebellum", activation=0.18, pressure=-0.06, dominant_drive="motion_smoothing")
    elif kind == "system_fault":
        add("immune", activation=0.38, pressure=0.42, valence=-0.12, dominant_drive="system_fault_guard")
        add("prefrontal", activation=0.24, pressure=0.18, dominant_drive="careful_response")

    if salience > 0.0:
        add("cortex", activation=salience * 0.12, confidence=0.04, dominant_drive="scene_integration")
    if valence != 0.0:
        add("limbic", valence=valence * 0.12)
    return deltas


def _couple_organs(conn: Any, person_id: str, states: dict[str, dict[str, Any]], event_id: str, now_ms: int) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []

    def strength(organ: str) -> float:
        state = states.get(organ, {})
        return _clamp(_float(state.get("activation")) * 0.35 + _float(state.get("pressure")) * 0.5 + _float(state.get("fatigue")) * 0.15)

    social = strength("social_brain")
    limbic = strength("limbic")
    prefrontal = strength("prefrontal")
    circadian = strength("circadian")
    pain = strength("pain_reward")
    default_mode = strength("default_mode")

    planned: list[tuple[list[str], str, dict[str, Any]]] = []
    if social > 0.35 and limbic > 0.25:
        planned.append((["social_brain", "limbic"], "initiative_governor", {"activation": 0.08, "pressure": 0.08, "dominant_drive": "approach_but_gate"}))
    if prefrontal > 0.42 or pain > 0.45:
        planned.append((["prefrontal", "pain_reward"], "initiative_governor", {"activation": 0.1, "pressure": 0.16, "dominant_drive": "inhibit_repetition"}))
        planned.append((["prefrontal"], "language_system", {"activation": 0.08, "pressure": 0.04, "dominant_drive": "careful_wording"}))
    if circadian > 0.5:
        planned.append((["circadian", "glymphatic"], "motor_cortex", {"fatigue": 0.08, "pressure": -0.04, "dominant_drive": "low_motion"}))
        planned.append((["circadian"], "language_system", {"fatigue": 0.05, "dominant_drive": "soft_tired_expression"}))
    if default_mode > 0.38:
        planned.append((["default_mode", "hippocampus"], "language_system", {"activation": 0.08, "dominant_drive": "dream_tinted_expression"}))
    if strength("motor_cortex") > 0.3:
        planned.append((["motor_cortex", "cerebellum"], "proprioception", {"activation": 0.08, "pressure": -0.04, "dominant_drive": "stable_body_state"}))

    for sources, target, delta in planned:
        before = copy.deepcopy(states.get(target) or _default_organ_state(target, now_ms))
        after = _apply_delta(before, delta, trace={"event_kind": "organ_coupling", "source_organs": sources}, now_ms=now_ms)
        states[target] = after
        effect = {"source_organs": sources, "delta": delta}
        effects.append({"source_organs": sources, "target_organ_id": target, "effect": effect})
        conn.execute(
            "INSERT INTO organ_coupling_event(coupling_id, source_experience_event_id, person_id, source_organs_json, target_organ_id, effect_json, created_at_ms) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (_event_id("orgcpl"), event_id, person_id, _dumps(sources), target, _dumps(effect), now_ms),
        )
    return effects


def append_life_experience(
    *,
    ledger: Any,
    person_id: str = DEFAULT_PERSON_ID,
    session_id: str = "",
    source_kind: str,
    event_kind: str,
    content_summary: str = "",
    evidence: Mapping[str, Any] | None = None,
    salience: float = 0.0,
    valence: float = 0.0,
    uncertainty: float = 0.0,
    now_ms: int | None = None,
    consume: bool = True,
) -> dict[str, Any]:
    ensure_organ_schema(ledger)
    now = int(now_ms or _now_ms())
    resolved = str(person_id or DEFAULT_PERSON_ID)
    evidence_payload = dict(evidence or {})
    memory_event_id = str(evidence_payload.get("memory_event_id") or "").strip()
    if not memory_event_id:
        try:
            from host_brain.app.memory_event_store import MemoryEventStore, infer_role, infer_visibility  # noqa: PLC0415

            memory_event = MemoryEventStore(ledger).append_event(
                person_id=resolved,
                session_id=session_id,
                conversation_id=session_id,
                source_kind=source_kind,
                event_kind=event_kind,
                visibility=infer_visibility(event_kind, source_kind),
                role=infer_role(event_kind),
                content=str(content_summary or ""),
                content_summary=str(content_summary or ""),
                causality={},
                refs={},
                salience=float(salience),
                valence=float(valence),
                uncertainty=float(uncertainty),
                confidence=1.0,
                created_at_ms=now,
            )
            memory_event_id = str(memory_event.get("event_id") or "")
            if memory_event_id:
                evidence_payload["memory_event_id"] = memory_event_id
        except Exception:
            memory_event_id = ""
    event_id = _event_id("lifeexp")
    _conn(ledger).execute(
        "INSERT INTO life_experience_event(event_id, person_id, session_id, source_kind, event_kind, content_summary, evidence_json, salience, valence, uncertainty, created_at_ms, consumed_by_organs) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (event_id, resolved, session_id, source_kind, event_kind, _text(content_summary, 1000), _dumps(evidence_payload), float(salience), float(valence), float(uncertainty), now),
    )
    _conn(ledger).commit()
    if memory_event_id:
        try:
            from host_brain.app.memory_event_store import MemoryEventStore  # noqa: PLC0415

            store = MemoryEventStore(ledger)
            store.merge_event_refs(memory_event_id, {"life_experience_event_id": event_id})
            store.update_projection_status(memory_event_id, {"life_experience": {"status": "OK", "event_id": event_id}})
        except Exception:
            pass
    packet: dict[str, Any] = {
        "event_id": event_id,
        "memory_event_id": memory_event_id,
        "person_id": resolved,
        "session_id": session_id,
        "source_kind": source_kind,
        "event_kind": event_kind,
        "created_at_ms": now,
    }
    if consume:
        packet["organ_processing"] = process_life_experience_event(ledger=ledger, event_id=event_id)
    return packet


def process_life_experience_event(*, ledger: Any, event_id: str) -> dict[str, Any]:
    ensure_organ_schema(ledger)
    conn = _conn(ledger)
    row = conn.execute(
        "SELECT event_id, person_id, session_id, source_kind, event_kind, content_summary, evidence_json, salience, valence, uncertainty, created_at_ms FROM life_experience_event WHERE event_id=? LIMIT 1",
        (event_id,),
    ).fetchone()
    if not row:
        return {"status": "NOT_FOUND", "event_id": event_id}
    person_id = str(row[1] or DEFAULT_PERSON_ID)
    event_kind = str(row[4] or "")
    content = str(row[5] or "")
    evidence = _loads(row[6], {})
    salience = _float(row[7])
    valence = _float(row[8])
    now_ms = int(row[10] or _now_ms())
    states: dict[str, dict[str, Any]] = {}
    organ_events: list[dict[str, Any]] = []
    deltas = _base_event_deltas(event_kind, _map(evidence), content, salience, valence, now_ms)
    for organ_id in ORGAN_IDS:
        current = _get_organ_state(conn, person_id, organ_id, now_ms)
        elapsed_min = max(0.0, (now_ms - int(current.get("updated_at_ms") or now_ms)) / 60000.0)
        decayed = _decay_state(current, elapsed_min)
        delta = deltas.get(organ_id, {})
        if delta:
            after = _apply_delta(decayed, delta, trace={"experience_event_id": event_id, "event_kind": event_kind, "source_kind": str(row[3] or "")}, now_ms=now_ms)
            conn.execute(
                "INSERT INTO organ_runtime_event(event_id, experience_event_id, person_id, organ_id, delta_json, state_before_json, state_after_json, created_at_ms) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (_event_id("orgevt"), event_id, person_id, organ_id, _dumps(delta), _dumps(decayed), _dumps(after), now_ms),
            )
            organ_events.append({"organ_id": organ_id, "delta": delta, "after": _compact_organ(after)})
        else:
            after = decayed
        states[organ_id] = after
    coupling = _couple_organs(conn, person_id, states, event_id, now_ms)
    for state in states.values():
        _save_organ_state(conn, person_id, state, now_ms)
    conn.execute("UPDATE life_experience_event SET consumed_by_organs=1 WHERE event_id=?", (event_id,))
    conn.commit()
    return {
        "status": "OK",
        "event_id": event_id,
        "event_kind": event_kind,
        "organ_delta_count": len(organ_events),
        "organ_events": organ_events[:12],
        "coupling": coupling[:12],
    }


def _compact_organ(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "organ_id": str(state.get("organ_id") or ""),
        "activation": round(_float(state.get("activation")), 3),
        "pressure": round(_float(state.get("pressure")), 3),
        "fatigue": round(_float(state.get("fatigue")), 3),
        "valence": round(_float(state.get("valence"), 0.5), 3),
        "dominant_drive": str(state.get("dominant_drive") or ""),
    }


def get_organ_runtime_state(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID) -> dict[str, Any]:
    ensure_organ_schema(ledger)
    resolved = str(person_id or DEFAULT_PERSON_ID)
    now = _now_ms()
    rows = _conn(ledger).execute(
        "SELECT organ_id, state_json, updated_at_ms FROM organ_runtime_state WHERE person_id=?",
        (resolved,),
    ).fetchall()
    states = {str(row[0]): _loads(row[1], {}) for row in rows}
    for organ_id in ORGAN_IDS:
        states.setdefault(organ_id, _default_organ_state(organ_id, now))
    compact = [_compact_organ(states[organ_id]) for organ_id in ORGAN_IDS]
    compact.sort(key=lambda item: item["activation"] * 0.35 + item["pressure"] * 0.5 + item["fatigue"] * 0.15, reverse=True)
    return {"person_id": resolved, "organ_count": len(ORGAN_IDS), "organs": states, "top_organs": compact[:10], "updated_at_ms": now}


def organ_votes_from_runtime(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        runtime = get_organ_runtime_state(ledger=ledger, person_id=person_id)
        top = list(runtime.get("top_organs") or [])
        votes = [
            {
                "organ": item["organ_id"],
                "vote": item.get("dominant_drive") or ORGAN_PROFILES.get(item["organ_id"], {}).get("drive") or item["organ_id"],
                "strength": round(_clamp(item["activation"] * 0.35 + item["pressure"] * 0.5 + item["fatigue"] * 0.15), 3),
                "field": "organ_runtime_state",
                "effect": "state_source",
            }
            for item in top
            if _clamp(item["activation"] * 0.35 + item["pressure"] * 0.5 + item["fatigue"] * 0.15) > 0.02
        ]
        peak = max([float(v.get("strength") or 0.0) for v in votes] or [0.0])
        return {
            "mapped_organ_count": len(ORGAN_IDS),
            "active_organs": [str(v["organ"]) for v in votes[:8]],
            "organ_votes": votes[:8],
            "top_influences": votes[:5],
            "winning_scene": "",
            "winning_motive": "",
            "dialogue_effect": _effect(peak),
            "embodiment_effect": _effect(peak),
            "source": "organ_runtime_state",
        }
    except Exception:
        return dict(fallback or {})


def _effect(value: float) -> str:
    if value >= 0.66:
        return "strong"
    if value >= 0.35:
        return "medium"
    if value >= 0.12:
        return "weak"
    return "none"


def synthesize_life_state_from_organs(
    *,
    ledger: Any,
    base_state: Mapping[str, Any],
    person_id: str = DEFAULT_PERSON_ID,
    source_experience_event_id: str = "",
    now_ms: int | None = None,
) -> dict[str, Any]:
    ensure_organ_schema(ledger)
    now = int(now_ms or _now_ms())
    resolved = str(person_id or DEFAULT_PERSON_ID)
    runtime = get_organ_runtime_state(ledger=ledger, person_id=resolved)
    organs = _map(runtime.get("organs"))

    def strength(organ_id: str) -> float:
        state = _map(organs.get(organ_id))
        return _clamp(_float(state.get("activation")) * 0.35 + _float(state.get("pressure")) * 0.5 + _float(state.get("fatigue")) * 0.15)

    after = copy.deepcopy(dict(base_state or {}))
    social = _map(organs.get("social_brain"))
    limbic = _map(organs.get("limbic"))
    default_mode = _map(organs.get("default_mode"))
    hippocampus = _map(organs.get("hippocampus"))
    circadian = _map(organs.get("circadian"))
    glymphatic = _map(organs.get("glymphatic"))
    base_pressure = _map(after.get("life_pressure"))
    work_pressure = _clamp(max(strength("prefrontal"), strength("immune"), strength("cortex"), _float(base_pressure.get("work")) * 0.82))
    absence = _clamp(max(strength("social_brain") * 0.65 + strength("limbic") * 0.35, _float(base_pressure.get("absence")) * 0.92))
    dream = _clamp(max(strength("default_mode") * 0.65 + strength("glymphatic") * 0.2 + strength("hippocampus") * 0.15, _float(base_pressure.get("dream")) * 0.82))
    fatigue = _clamp(max(strength("circadian") * 0.72 + strength("glymphatic") * 0.28 + _float(circadian.get("fatigue")) * 0.35, _float(base_pressure.get("fatigue")) * 0.86))
    pain_reward = _map(organs.get("pain_reward"))
    relationship = _clamp(_float(social.get("valence"), 0.5) * 0.55 + _float(limbic.get("valence"), 0.5) * 0.35 + _float(pain_reward.get("valence"), 0.5) * 0.1)

    pressure = _map(after.get("life_pressure"))
    pressure.update({"absence": absence, "dream": dream, "work": work_pressure, "fatigue": fatigue, "relationship": relationship})
    after["life_pressure"] = pressure

    affect = _map(after.get("affect"))
    affect.update(
        {
            "loneliness": _clamp(absence * 0.86),
            "attachment_warmth": _clamp(relationship),
            "fatigue": fatigue,
            "tension": _clamp(work_pressure * 0.55 + strength("ans") * 0.25 + strength("immune") * 0.2),
            "energy": _clamp(1.0 - fatigue * 0.72 + strength("pain_reward") * 0.08),
            "irritation": _clamp(strength("pain_reward") * 0.55 + strength("immune") * 0.1),
        }
    )
    if work_pressure >= 0.45:
        affect["mood"] = "curious"
    elif absence >= 0.45:
        affect["mood"] = "lonely"
    elif dream >= 0.38:
        affect["mood"] = "dreamy"
    elif fatigue >= 0.52:
        affect["mood"] = "tired"
    elif relationship >= 0.56:
        affect["mood"] = "warm"
    else:
        affect["mood"] = "calm"
    after["affect"] = affect

    needs = _map(after.get("needs"))
    needs.update(
        {
            "contact": _clamp(absence * 0.78 + relationship * 0.18),
            "rest": fatigue,
            "memory_consolidation": _clamp(dream * 0.62 + strength("hippocampus") * 0.22),
            "autonomy": _clamp(0.25 + strength("prefrontal") * 0.22),
        }
    )
    after["needs"] = needs

    inner = _map(after.get("inner_world"))
    default_trace = _map(default_mode.get("memory_trace"))
    hip_trace = _map(hippocampus.get("memory_trace"))
    if default_trace.get("dream_residue"):
        inner["dream_residue"] = _text(default_trace.get("dream_residue"), 240)
    if default_trace.get("current_preoccupation"):
        inner["current_preoccupation"] = _text(default_trace.get("current_preoccupation"), 240)
    if default_trace.get("attention_target"):
        inner["attention_target"] = _text(default_trace.get("attention_target"), 120)
    if default_trace.get("felt_tendency"):
        inner["felt_tendency"] = _text(default_trace.get("felt_tendency"), 120)
    if default_trace.get("dialogue_bias"):
        inner["dialogue_bias"] = _text(default_trace.get("dialogue_bias"), 160)
    if hip_trace.get("diary_residue"):
        inner["diary_residue"] = _text(hip_trace.get("diary_residue"), 240)
    after["inner_world"] = inner

    emb = _map(after.get("embodiment"))
    if work_pressure >= 0.45:
        emb.update({"expression": "thinking", "idle_animation": "quiet_focus", "motion_bias": "low_attention", "voice_tone": "focused_soft", "mateengine_state": "thinking", "emotion_tag": "THINKING"})
    elif dream >= 0.38:
        emb.update({"expression": "soft_dreamy", "idle_animation": "slow_breath", "motion_bias": "low_slow", "voice_tone": "low_soft", "mateengine_state": "cozy", "emotion_tag": "LOVE"})
    elif absence >= 0.45:
        emb.update({"expression": "soft_lonely", "idle_animation": "quiet_waiting", "motion_bias": "low_random", "voice_tone": "low_soft", "mateengine_state": "waiting", "emotion_tag": "SAD"})
    elif fatigue >= 0.52:
        emb.update({"expression": "sleepy", "idle_animation": "slow_breath", "motion_bias": "low_slow", "voice_tone": "soft_tired", "mateengine_state": "idle", "emotion_tag": "SAD"})
    elif relationship >= 0.56:
        emb.update({"expression": "warm_smile", "idle_animation": "quiet_breath", "motion_bias": "low_random", "voice_tone": "warm_soft", "mateengine_state": "happy", "emotion_tag": "HAPPY"})
    after["embodiment"] = emb

    motives: list[dict[str, Any]] = []
    if work_pressure >= 0.25:
        motives.append({"motive": "work_progress_nudge", "pressure": round(work_pressure, 3), "source": "organ_runtime"})
    if absence >= 0.25:
        motives.append({"motive": "soft_reconnect", "pressure": round(absence, 3), "source": "organ_runtime"})
    if dream >= 0.25:
        motives.append({"motive": "dream_afterglow", "pressure": round(dream, 3), "source": "organ_runtime"})
    if fatigue >= 0.3 or relationship >= 0.45:
        motives.append({"motive": "care_check", "pressure": round(max(fatigue, relationship * 0.6), 3), "source": "organ_runtime"})
    after["motives"] = sorted(motives, key=lambda item: float(item.get("pressure") or 0.0), reverse=True)[:4]
    after["organ_runtime"] = {
        "source": "organ_runtime_state",
        "top_organs": runtime.get("top_organs", [])[:8],
        "synthesis": {"absence": round(absence, 3), "dream": round(dream, 3), "work": round(work_pressure, 3), "fatigue": round(fatigue, 3), "relationship": round(relationship, 3)},
        "updated_at_ms": now,
    }
    conn = _conn(ledger)
    conn.execute(
        "INSERT INTO life_state_synthesis_log(synthesis_id, person_id, source_experience_event_id, organ_summary_json, state_patch_json, created_at_ms) VALUES(?, ?, ?, ?, ?, ?)",
        (_event_id("lifesyn"), resolved, source_experience_event_id, _dumps(after["organ_runtime"]), _dumps({"life_pressure": after["life_pressure"], "affect": after["affect"], "embodiment": after["embodiment"], "motives": after["motives"]}), now),
    )
    conn.commit()
    return after


def open_proactive_episode(
    *,
    ledger: Any,
    person_id: str = DEFAULT_PERSON_ID,
    source_action_id: str = "",
    source_outbox_id: str = "",
    motive: str = "",
    organ_sources: Any = None,
    message_text: str = "",
    expected_response_kind: str = "",
    sent_at_ms: int | None = None,
    outcome: str = "pending",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_organ_schema(ledger)
    now = int(sent_at_ms or _now_ms())
    episode_id = _event_id("lifeproep")
    _conn(ledger).execute(
        "INSERT INTO proactive_interaction_episode(episode_id, person_id, source_action_id, source_outbox_id, motive, organ_sources_json, message_text, expected_response_kind, sent_at_ms, outcome, metadata_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (episode_id, str(person_id or DEFAULT_PERSON_ID), source_action_id, source_outbox_id, motive, _dumps(list(organ_sources or [])), _text(message_text, 1000), expected_response_kind, now, outcome, _dumps(dict(metadata or {}))),
    )
    _conn(ledger).commit()
    return {"episode_id": episode_id, "outcome": outcome, "sent_at_ms": now}


def close_pending_proactive_episode_for_user_reply(
    *,
    ledger: Any,
    person_id: str = DEFAULT_PERSON_ID,
    session_id: str = "",
    request_id: str = "",
    user_message: str = "",
    now_ms: int | None = None,
) -> dict[str, Any]:
    ensure_organ_schema(ledger)
    now = int(now_ms or _now_ms())
    resolved = str(person_id or DEFAULT_PERSON_ID)
    row = _conn(ledger).execute(
        "SELECT episode_id, motive, message_text, sent_at_ms FROM proactive_interaction_episode WHERE person_id=? AND outcome='pending' ORDER BY sent_at_ms DESC LIMIT 1",
        (resolved,),
    ).fetchone()
    if not row:
        return {}
    sent_at = int(row[3] or 0)
    if sent_at and now - sent_at > 3_600_000:
        return {}
    text = str(user_message or "")
    annoyed = any(t in text for t in ("烦", "别发", "别一直", "莫名其妙", "鬼扯", "很呆"))
    asks_context = any(t in text for t in ("怎么了", "咋了", "为什么", "为啥", "什么情况"))
    outcome = "annoyed" if annoyed else "responded"
    experience = append_life_experience(
        ledger=ledger,
        person_id=resolved,
        session_id=session_id,
        source_kind="life_proactive_user_response",
        event_kind="life_proactive_user_response",
        content_summary=text,
        evidence={"episode_id": row[0], "motive": row[1], "outcome": outcome, "asks_context": asks_context},
        salience=0.8,
        valence=-0.6 if annoyed else 0.45,
        uncertainty=0.05,
        now_ms=now,
        consume=True,
    )
    delta_after = experience.get("organ_processing", {})
    memory_event_id = str(experience.get("memory_event_id") or "")
    if memory_event_id:
        try:
            from host_brain.app.memory_event_store import MemoryEventStore  # noqa: PLC0415

            MemoryEventStore(ledger).update_projection_status(
                memory_event_id,
                {
                    "conversation_turn": {
                        "status": "COVERED_BY_USER_MESSAGE",
                        "request_id": str(request_id or ""),
                        "session_id": str(session_id or ""),
                    }
                },
            )
        except Exception:
            pass
    _conn(ledger).execute(
        "UPDATE proactive_interaction_episode SET user_response_turn_id=?, outcome=?, organ_delta_after_json=?, closed_at_ms=? WHERE episode_id=?",
        (request_id or session_id, outcome, _dumps(delta_after), now, row[0]),
    )
    _conn(ledger).commit()
    return {
        "episode_id": row[0],
        "motive": row[1],
        "outcome": outcome,
        "asks_context": asks_context,
        "source_message": row[2],
        "organ_delta_after": delta_after,
    }


def get_recent_life_experience(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, limit: int = 50) -> dict[str, Any]:
    ensure_organ_schema(ledger)
    resolved = str(person_id or DEFAULT_PERSON_ID)
    capped = max(1, min(int(limit or 50), 200))
    rows = _conn(ledger).execute(
        "SELECT event_id, session_id, source_kind, event_kind, content_summary, evidence_json, salience, valence, uncertainty, consumed_by_organs, created_at_ms FROM life_experience_event WHERE person_id=? ORDER BY created_at_ms DESC LIMIT ?",
        (resolved, capped),
    ).fetchall()
    items = [
        {
            "event_id": r[0],
            "session_id": r[1],
            "source_kind": r[2],
            "event_kind": r[3],
            "content_summary": r[4],
            "evidence": _loads(r[5], {}),
            "salience": float(r[6] or 0.0),
            "valence": float(r[7] or 0.0),
            "uncertainty": float(r[8] or 0.0),
            "consumed_by_organs": bool(r[9]),
            "created_at_ms": r[10],
        }
        for r in rows
    ]
    return {"person_id": resolved, "items": items}


def get_recent_proactive_episodes(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, limit: int = 20) -> dict[str, Any]:
    ensure_organ_schema(ledger)
    resolved = str(person_id or DEFAULT_PERSON_ID)
    capped = max(1, min(int(limit or 20), 100))
    rows = _conn(ledger).execute(
        "SELECT episode_id, source_action_id, source_outbox_id, motive, organ_sources_json, message_text, expected_response_kind, sent_at_ms, user_response_turn_id, outcome, organ_delta_after_json, closed_at_ms, metadata_json FROM proactive_interaction_episode WHERE person_id=? ORDER BY sent_at_ms DESC LIMIT ?",
        (resolved, capped),
    ).fetchall()
    items = [
        {
            "episode_id": r[0],
            "source_action_id": r[1],
            "source_outbox_id": r[2],
            "motive": r[3],
            "organ_sources": _loads(r[4], []),
            "message_text": r[5],
            "expected_response_kind": r[6],
            "sent_at_ms": r[7],
            "user_response_turn_id": r[8],
            "outcome": r[9],
            "organ_delta_after": _loads(r[10], {}),
            "closed_at_ms": r[11],
            "metadata": _loads(r[12], {}),
        }
        for r in rows
    ]
    return {"person_id": resolved, "items": items}


__all__ = [
    "ORGAN_IDS",
    "ORGAN_SCHEMA_SQL",
    "append_life_experience",
    "close_pending_proactive_episode_for_user_reply",
    "ensure_organ_schema",
    "get_organ_runtime_state",
    "get_recent_life_experience",
    "get_recent_proactive_episodes",
    "open_proactive_episode",
    "organ_votes_from_runtime",
    "process_life_experience_event",
    "synthesize_life_state_from_organs",
]
