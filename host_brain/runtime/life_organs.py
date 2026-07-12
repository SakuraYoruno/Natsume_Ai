"""Life organ reducers and compatibility API.

Reducers hand semantic UoWs to ``LifeRuntimeRepository``, the sole production
persistence owner for this slice.
"""

from __future__ import annotations

import copy
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any

from host_brain.persistence.life_runtime_repository import LifeRuntimeRepository

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

# Compatibility export only. Schema ownership moved to the central migration
# catalog (0049/0057/0071); runtime modules never carry DDL.
ORGAN_SCHEMA_SQL: tuple[str, ...] = ()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _event_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _repository(ledger: Any) -> LifeRuntimeRepository:
    return LifeRuntimeRepository.from_ledger(ledger)


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
    _repository(ledger).assert_schema_ready()


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
        "learning_memory": {},
        "updated_at_ms": int(now_ms or _now_ms()),
    }


def _get_organ_state(
    stored_states: Mapping[str, Mapping[str, Any]],
    person_id: str,
    organ_id: str,
    now_ms: int,
) -> dict[str, Any]:
    del person_id
    state = stored_states.get(organ_id)
    if state:
        state = dict(state)
        if isinstance(state, dict) and state:
            base = _default_organ_state(organ_id, now_ms)
            base.update(state)
            base["organ_id"] = organ_id
            return base
    return _default_organ_state(organ_id, now_ms)


def _decay_state(state: Mapping[str, Any], elapsed_min: float) -> dict[str, Any]:
    after = copy.deepcopy(dict(state))
    minutes = max(0.0, min(float(elapsed_min), 720.0))
    pressure_decay = 0.978 ** minutes
    activation_decay = 0.970 ** minutes
    fatigue_decay = 0.990 ** minutes
    after["activation"] = _clamp(_float(after.get("activation")) * activation_decay)
    after["pressure"] = _clamp(_float(after.get("pressure")) * pressure_decay)
    after["fatigue"] = _clamp(_float(after.get("fatigue")) * fatigue_decay)
    for key, target in (("activation", 0.72), ("pressure", 0.70), ("fatigue", 0.76)):
        value = _float(after.get(key))
        if value > 0.88 and minutes > 0.0:
            after[key] = _clamp(value - min(0.12, (value - target) * min(1.0, minutes / 90.0)))
    return after


def _homeostatic_add(current: float, delta: float, *, cap: float) -> float:
    if delta <= 0.0:
        return delta
    if current >= cap:
        return delta * 0.05
    if current >= cap - 0.10:
        return delta * 0.22
    if current >= cap - 0.22:
        return delta * 0.55
    return delta


def _state_cap_for(key: str) -> float:
    if key == "fatigue":
        return 0.96
    if key == "confidence":
        return 0.95
    if key in {"activation", "pressure"}:
        return 0.94
    return 1.0


def _apply_delta(state: Mapping[str, Any], delta: Mapping[str, Any], *, trace: Mapping[str, Any], now_ms: int) -> dict[str, Any]:
    after = copy.deepcopy(dict(state))
    allow_saturation = bool(delta.get("allow_saturation"))
    for key in ("activation", "pressure", "fatigue", "confidence"):
        if key in delta:
            current = _float(after.get(key))
            cap = 1.0 if allow_saturation else _state_cap_for(key)
            adjusted = _homeostatic_add(current, _float(delta.get(key)), cap=cap)
            after[key] = max(0.0, min(cap, current + adjusted))
    if "valence" in delta:
        after["valence"] = _clamp(_float(after.get("valence"), 0.5) + _float(delta.get("valence")))
    if delta.get("dominant_drive"):
        after["dominant_drive"] = _text(delta.get("dominant_drive"), 80)
    memory = _map(after.get("memory_trace"))
    for key, value in _map(delta.get("memory_trace")).items():
        memory[key] = value
    after["memory_trace"] = memory
    learning_memory = _map(after.get("learning_memory"))
    for key, value in _map(delta.get("learning_memory")).items():
        learning_memory[key] = value
    after["learning_memory"] = learning_memory
    cooldowns = _map(after.get("cooldowns"))
    for key, value in _map(delta.get("cooldowns")).items():
        cooldowns[key] = value
    after["cooldowns"] = cooldowns
    traces = list(after.get("recent_traces") or [])
    traces.insert(0, {"ts_ms": now_ms, **dict(trace or {}), "delta": {k: v for k, v in dict(delta).items() if k not in {"memory_trace", "learning_memory", "cooldowns", "allow_saturation"}}})
    after["recent_traces"] = traces[:8]
    after["updated_at_ms"] = now_ms
    return after


def _merge_delta(base: Mapping[str, Any] | None, extra: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in dict(extra or {}).items():
        if key in {"activation", "pressure", "fatigue", "confidence", "valence"}:
            merged[key] = _float(merged.get(key)) + _float(value)
        elif key in {"memory_trace", "learning_memory", "cooldowns"}:
            current = _map(merged.get(key))
            current.update(_map(value))
            merged[key] = current
        else:
            merged[key] = value
    return merged


def _autonomous_organ_delta(
    *,
    organ_id: str,
    state: Mapping[str, Any],
    evidence: Mapping[str, Any],
    elapsed_min: float,
    now_ms: int,
) -> dict[str, Any]:
    minutes = max(0.0, min(float(elapsed_min), 24.0 * 60.0))
    if minutes <= 0.0:
        return {}
    env = _map(evidence.get("environment"))
    day_phase = str(env.get("day_phase") or evidence.get("day_phase") or "")
    meal_phase = str(env.get("meal_phase") or "")
    idle_minutes = max(0.0, _float(evidence.get("idle_minutes")))
    fatigue = _clamp(evidence.get("fatigue"), 0.0)
    absence = _clamp(idle_minutes / 90.0)
    scale = min(1.0, minutes / 60.0)
    slow_scale = min(1.0, minutes / 360.0)
    trace = {
        "autonomous": True,
        "elapsed_min": round(minutes, 3),
        "day_phase": day_phase,
        "meal_phase": meal_phase,
        "idle_minutes": round(idle_minutes, 2),
    }
    if organ_id == "limbic":
        return {"activation": absence * 0.06 * scale, "pressure": absence * 0.07 * scale, "valence": -absence * 0.025 * scale, "dominant_drive": "autonomous_attachment_drift", "memory_trace": trace}
    if organ_id == "social_brain":
        return {"activation": absence * 0.08 * scale, "pressure": absence * 0.10 * scale, "dominant_drive": "autonomous_contact_pressure", "memory_trace": trace}
    if organ_id == "hippocampus":
        trace["continuity_decay"] = round(scale * 0.06, 4)
        return {"activation": 0.025 * scale, "pressure": max(0.0, absence - 0.35) * 0.035 * scale, "dominant_drive": "autonomous_continuity_binding", "memory_trace": trace}
    if organ_id == "default_mode":
        return {"activation": 0.035 * scale + absence * 0.03 * scale, "pressure": 0.025 * scale + absence * 0.045 * scale, "dominant_drive": "autonomous_inner_drift", "memory_trace": trace}
    if organ_id == "circadian":
        night = 1.0 if day_phase == "late_night" else 0.65 if day_phase == "night" else 0.30 if day_phase == "morning" else 0.18
        return {"activation": 0.04 * scale, "pressure": night * 0.08 * scale, "fatigue": max(fatigue, night) * 0.08 * scale, "dominant_drive": f"autonomous_rhythm_{day_phase or 'unknown'}", "memory_trace": trace}
    if organ_id == "glymphatic":
        recovery = 0.08 if day_phase in {"late_night", "night"} else -0.02
        return {"activation": max(0.0, recovery) * scale, "pressure": max(0.0, recovery * 0.6) * scale, "fatigue": -0.03 * scale if recovery > 0 else 0.02 * scale, "dominant_drive": "autonomous_recovery_cycle", "memory_trace": trace}
    if organ_id == "endocrine":
        return {"activation": 0.025 * slow_scale + absence * 0.04 * slow_scale, "pressure": absence * 0.06 * slow_scale, "valence": -absence * 0.02 * slow_scale, "dominant_drive": "autonomous_slow_pressure", "memory_trace": trace}
    if organ_id == "immune":
        return {"activation": 0.01 * scale, "pressure": 0.012 * scale, "dominant_drive": "autonomous_low_guard", "memory_trace": trace}
    if organ_id == "prefrontal":
        inhibition = 0.055 * scale + (0.04 * scale if day_phase not in {"dawn", "morning"} else 0.0)
        return {"activation": 0.035 * scale, "pressure": inhibition, "dominant_drive": "autonomous_inhibition_budget", "memory_trace": trace}
    if organ_id == "initiative_governor":
        return {"activation": 0.025 * scale, "pressure": max(0.0, absence * 0.05 - 0.025) * scale, "dominant_drive": "autonomous_proactive_threshold", "memory_trace": trace}
    if organ_id == "motor_cortex":
        return {"activation": 0.02 * scale, "pressure": -0.025 * scale if fatigue > 0.45 else 0.015 * scale, "fatigue": fatigue * 0.025 * scale, "dominant_drive": "autonomous_motion_budget", "memory_trace": trace}
    if organ_id == "cerebellum":
        return {"activation": 0.025 * scale, "pressure": -0.03 * scale, "dominant_drive": "autonomous_motion_smoothing", "memory_trace": trace}
    if organ_id == "vestibular":
        return {"activation": 0.02 * scale, "pressure": 0.025 * scale, "dominant_drive": "autonomous_body_stability", "memory_trace": trace}
    if organ_id == "somatic":
        return {"activation": 0.03 * scale, "pressure": 0.025 * scale + absence * 0.025 * scale, "dominant_drive": "autonomous_embodied_presence", "memory_trace": trace}
    if organ_id == "interoception":
        return {"activation": 0.035 * scale, "pressure": max(fatigue, absence * 0.5) * 0.06 * scale, "fatigue": fatigue * 0.03 * scale, "dominant_drive": "autonomous_internal_sense", "memory_trace": trace}
    if organ_id == "thalamus":
        return {"activation": 0.03 * scale, "pressure": 0.02 * scale, "dominant_drive": "autonomous_attention_route", "memory_trace": trace}
    if organ_id == "language_system":
        return {"activation": 0.02 * scale, "pressure": 0.025 * scale if day_phase else 0.0, "dominant_drive": "autonomous_expression_bias", "memory_trace": trace}
    if organ_id == "mirror_neuron":
        return {"activation": -0.02 * scale, "pressure": -0.02 * scale, "dominant_drive": "autonomous_mirror_decay", "memory_trace": trace}
    if organ_id == "pain_reward":
        return {"activation": -0.025 * scale, "pressure": -0.025 * scale, "dominant_drive": "autonomous_reward_decay", "memory_trace": trace}
    if organ_id == "ans":
        return {"activation": 0.025 * scale, "pressure": max(fatigue, absence * 0.4) * 0.05 * scale, "fatigue": fatigue * 0.02 * scale, "dominant_drive": "autonomous_tension_relaxation", "memory_trace": trace}
    if organ_id == "peripheral":
        return {"activation": 0.018 * scale, "pressure": 0.018 * scale, "dominant_drive": "autonomous_channel_presence", "memory_trace": trace}
    if organ_id == "proprioception":
        return {"activation": 0.025 * scale, "pressure": -0.018 * scale, "dominant_drive": "autonomous_posture_memory", "memory_trace": trace}
    if organ_id == "cortex":
        return {"activation": 0.03 * scale, "confidence": 0.025 * scale, "dominant_drive": "autonomous_scene_integration", "memory_trace": trace}
    return {}


def _numeric_has_concrete_anchor(numeric: Mapping[str, Any]) -> bool:
    anchor = _text(numeric.get("attention_target") or numeric.get("current_preoccupation"), 96)
    if not anchor:
        return False
    compact = re.sub(r"[\s，,。.!！?？;；：:、…~～\"“”'‘’]", "", anchor)
    if len(compact) < 4:
        return False
    vague = ("这种", "这个", "那个", "喜欢", "不喜欢", "怎么了", "继续", "然后呢", "刚才", "回复")
    if any(term in anchor for term in vague):
        return False
    return True


def _attenuate_inner_numeric_without_anchor(deltas: dict[str, dict[str, Any]], numeric: Mapping[str, Any]) -> None:
    if _numeric_has_concrete_anchor(numeric):
        return
    suppression = _text(numeric.get("monologue_suppression_reason") or "NO_CONCRETE_INNER_ANCHOR", 120)
    for organ, scale in (("limbic", 0.24), ("social_brain", 0.22), ("initiative_governor", 0.18), ("language_system", 0.35)):
        current = deltas.get(organ)
        if not current:
            continue
        for key in ("activation", "pressure"):
            if key in current:
                current[key] = _float(current.get(key)) * scale
        memory = _map(current.get("memory_trace"))
        memory["monologue_suppression_reason"] = suppression
        memory["anchor_quality"] = _float(numeric.get("anchor_quality"))
        current["memory_trace"] = memory
        current["dominant_drive"] = "suppressed_diffuse_inner_pressure"
    prefrontal = deltas.setdefault("prefrontal", {})
    prefrontal["activation"] = _float(prefrontal.get("activation")) + 0.04
    prefrontal["pressure"] = _float(prefrontal.get("pressure")) + 0.10
    prefrontal["dominant_drive"] = "suppress_unanchored_inner_monologue"
    memory = _map(prefrontal.get("memory_trace"))
    memory["monologue_suppression_reason"] = suppression
    prefrontal["memory_trace"] = memory
    default = deltas.setdefault("default_mode", {})
    memory = _map(default.get("memory_trace"))
    memory["current_preoccupation"] = ""
    memory["attention_target"] = ""
    memory["attention_kind"] = _text(numeric.get("attention_kind") or numeric.get("attention"), 80)
    memory["monologue_suppression_reason"] = suppression
    default["memory_trace"] = memory


def _base_event_deltas(event_kind: str, evidence: Mapping[str, Any], content: str, salience: float, valence: float, now_ms: int) -> dict[str, dict[str, Any]]:
    kind = str(event_kind or "").strip()
    text = str(content or "")
    deltas: dict[str, dict[str, Any]] = {}

    def add(organ: str, **delta: Any) -> None:
        current = deltas.setdefault(organ, {})
        for key, value in delta.items():
            if key in {"activation", "pressure", "fatigue", "confidence", "valence"}:
                current[key] = _float(current.get(key)) + _float(value)
            elif key in {"memory_trace", "learning_memory", "cooldowns"}:
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
        add("endocrine", activation=0.08 + absence * 0.10, pressure=absence * 0.14, valence=-absence * 0.04, dominant_drive="slow_pressure", memory_trace={"idle_minutes": idle_minutes})
        add("vestibular", activation=0.06 + absence * 0.06, pressure=absence * 0.08, dominant_drive="body_stability", memory_trace={"idle_minutes": idle_minutes})
        add("ans", activation=0.06 + absence * 0.12, pressure=absence * 0.10 + fatigue * 0.05, fatigue=fatigue * 0.06, dominant_drive="tension_relaxation", memory_trace={"idle_minutes": idle_minutes, "fatigue": fatigue})
        add("peripheral", activation=0.04 + absence * 0.06, pressure=absence * 0.06, dominant_drive="channel_presence", memory_trace={"idle_minutes": idle_minutes})
    elif kind == "inner_numeric_tick":
        numeric = _map(evidence.get("inner_numeric_state"))
        attention = _text(numeric.get("attention"), 96)
        target = _text(numeric.get("attention_target") or numeric.get("current_preoccupation"), 160)
        tendency = _text(numeric.get("felt_tendency"), 120)
        dialogue_bias = _text(numeric.get("dialogue_bias"), 180)
        inhibition = _clamp(numeric.get("inhibition"), 0.0)
        numeric_salience = _clamp(numeric.get("salience"), salience)
        loneliness = _clamp(numeric.get("loneliness"), 0.0)
        anticipation = _clamp(numeric.get("anticipation"), 0.0)
        continuity = _clamp(numeric.get("continuity_score"), 0.0)
        has_anchor = bool(target)
        trace = {
            "inner_numeric_state": numeric,
            "attention": attention,
            "attention_target": target,
            "felt_tendency": tendency,
            "dialogue_bias": dialogue_bias,
            "monologue_policy": _text(numeric.get("monologue_policy"), 120),
        }
        add(
            "default_mode",
            activation=0.10 + numeric_salience * 0.24,
            pressure=0.04 + numeric_salience * 0.18,
            confidence=0.06,
            dominant_drive="private_numeric_continuity",
            memory_trace={
                "current_preoccupation": target,
                "attention_target": target,
                "felt_tendency": tendency,
                "dialogue_bias": dialogue_bias,
                "inner_numeric_state": numeric,
            },
        )
        add(
            "hippocampus",
            activation=0.08 + (0.18 if has_anchor else 0.06),
            pressure=0.04 + max(0.0, 1.0 - continuity) * 0.08,
            confidence=0.08,
            dominant_drive="private_continuity_binding" if has_anchor else "silent_continuity_trace",
            memory_trace={"private_continuity_trace": target or attention, "inner_numeric_state": numeric},
        )
        add(
            "thalamus",
            activation=0.10 + numeric_salience * 0.16,
            pressure=0.04 + (0.08 if attention else 0.0),
            dominant_drive="route_inner_attention",
            memory_trace=trace,
        )
        add(
            "prefrontal",
            activation=0.08 + inhibition * 0.18,
            pressure=inhibition * 0.22,
            confidence=0.06,
            dominant_drive="inner_inhibition_gate",
            memory_trace={"inhibition": inhibition, "dialogue_bias": dialogue_bias},
        )
        add(
            "initiative_governor",
            activation=0.06 + numeric_salience * 0.10,
            pressure=max(0.0, inhibition * 0.18 - (0.08 if has_anchor else 0.0)),
            dominant_drive="numeric_proactive_threshold",
            memory_trace={"inhibition": inhibition, "felt_tendency": tendency, "attention_target": target},
        )
        add(
            "language_system",
            activation=0.08 + (0.12 if dialogue_bias else 0.0),
            pressure=0.04 + inhibition * 0.08,
            dominant_drive="numeric_dialogue_bias",
            memory_trace={"dialogue_bias": dialogue_bias, "felt_tendency": tendency, "attention_target": target},
        )
        add(
            "interoception",
            activation=0.08 + max(loneliness, anticipation) * 0.16,
            pressure=0.04 + max(loneliness, anticipation) * 0.14,
            dominant_drive="numeric_internal_sense",
            memory_trace={"loneliness": loneliness, "anticipation": anticipation, "felt_tendency": tendency},
        )
        add(
            "limbic",
            activation=0.06 + loneliness * 0.14 + anticipation * 0.08,
            pressure=loneliness * 0.10 + anticipation * 0.04,
            valence=-loneliness * 0.04,
            dominant_drive="quiet_attachment_pressure",
            memory_trace={"felt_tendency": tendency, "attention_target": target},
        )
        add(
            "social_brain",
            activation=0.05 + loneliness * 0.12,
            pressure=loneliness * 0.12,
            dominant_drive="silent_contact_model",
            memory_trace={"felt_tendency": tendency, "attention_target": target},
        )
        add(
            "somatic",
            activation=0.06 + numeric_salience * 0.10,
            pressure=0.03 + max(loneliness, anticipation) * 0.08,
            dominant_drive="quiet_body_presence",
            memory_trace={"inner_numeric_state": numeric},
        )
        add(
            "proprioception",
            activation=0.05 + numeric_salience * 0.08,
            pressure=0.02,
            dominant_drive="posture_continuity",
            memory_trace={"inner_numeric_state": numeric},
        )
        add(
            "cortex",
            activation=0.08 + numeric_salience * 0.10,
            confidence=0.05,
            dominant_drive="inner_state_integration",
            memory_trace={"inner_numeric_state": numeric},
        )
    elif kind in {"autonomous_cycle", "learning_signal_generated", "growth_candidate_generated", "personality_growth_applied", "personality_growth_suppressed", "self_project_progress"}:
        agenda = list(_map(evidence).get("self_agenda") or [])[:5]
        learning_state = _map(_map(evidence).get("learning_state"))
        signals = list(learning_state.get("learning_signals") or [])[:6]
        signal_names = [_text(_map(item).get("signal_kind"), 80) for item in signals if _text(_map(item).get("signal_kind"), 80)]
        failure = any("failure" in item for item in signal_names)
        style = any("style" in item or "repetition" in item for item in signal_names)
        add("hippocampus", activation=0.16, pressure=0.06, confidence=0.06, dominant_drive="autonomous_agenda_memory", learning_memory={"self_agenda": agenda, "learning_signals": signal_names})
        add("default_mode", activation=0.14, pressure=0.08, dominant_drive="self_project_drift", learning_memory={"agenda_projection": agenda})
        add("cortex", activation=0.18, confidence=0.08, dominant_drive="autonomous_self_integration", learning_memory={"self_reward": _map(learning_state.get("self_reward"))})
        add("language_system", activation=0.12 + (0.08 if style else 0.0), pressure=0.12 if style else 0.04, dominant_drive="style_learning" if style else "expression_growth", learning_memory={"style_signals": signal_names})
        add("prefrontal", activation=0.10, pressure=0.16 if failure else 0.06, dominant_drive="growth_safety_control", learning_memory={"growth_guard": "adaptive_layer_only"})
        add("pain_reward", activation=0.14 if failure else 0.06, pressure=0.18 if failure else -0.04, valence=-0.12 if failure else 0.04, dominant_drive="self_supervised_reward", learning_memory={"reward_signals": signal_names})
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
        add("endocrine", activation=0.14, pressure=-0.18 if positive else 0.04, valence=0.10 if positive else -0.06, dominant_drive="hormone_rebalance")
        add("ans", activation=-0.10, pressure=-0.22, dominant_drive="idle_reset")
        add("peripheral", activation=0.12, pressure=-0.08, dominant_drive="expression_update")
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
        add("endocrine", activation=0.08, pressure=0.06, dominant_drive=tendency or "emotional_drift", memory_trace={"felt_tendency": tendency})
    elif kind == "embodiment_dispatch":
        add("somatic", activation=0.28, pressure=0.08, dominant_drive="embodied_presence", memory_trace={"last_body": _map(evidence.get("target"))})
        add("proprioception", activation=0.24, pressure=0.05, dominant_drive="posture_memory", memory_trace={"last_body": _map(evidence.get("target"))})
        add("motor_cortex", activation=0.2, pressure=0.08, dominant_drive="body_action")
        add("cerebellum", activation=0.18, pressure=-0.06, dominant_drive="motion_smoothing")
        add("vestibular", activation=0.16, pressure=0.06, dominant_drive="body_stability", memory_trace={"last_body": _map(evidence.get("target"))})
    elif kind == "system_fault":
        add("immune", activation=0.38, pressure=0.42, valence=-0.12, dominant_drive="system_fault_guard")
        add("prefrontal", activation=0.24, pressure=0.18, dominant_drive="careful_response")

    if salience > 0.0:
        add("cortex", activation=salience * 0.12, confidence=0.04, dominant_drive="scene_integration")
    if valence != 0.0:
        add("limbic", valence=valence * 0.12)
    if kind == "inner_numeric_tick":
        numeric = _map(evidence.get("inner_numeric_state"))
        _attenuate_inner_numeric_without_anchor(deltas, numeric)
    return deltas


def _couple_organs(
    person_id: str,
    states: dict[str, dict[str, Any]],
    event_id: str,
    now_ms: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    effects: list[dict[str, Any]] = []
    durable_events: list[dict[str, Any]] = []

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
    endocrine_val = strength("endocrine")
    if endocrine_val > 0.35:
        planned.append((["endocrine"], "limbic", {"activation": 0.06, "valence": 0.04 if endocrine_val > 0.55 else -0.02, "dominant_drive": "hormone_tinted_emotion"}))
    if strength("vestibular") > 0.30:
        planned.append((["vestibular", "cerebellum"], "interoception", {"activation": 0.06, "pressure": 0.08, "fatigue": 0.05, "dominant_drive": "jitter_tax_on_body_budget"}))
    if strength("ans") > 0.30:
        planned.append((["ans", "glymphatic"], "circadian", {"fatigue": 0.06, "pressure": 0.04, "dominant_drive": "recovery_tinted_rhythm"}))
    if strength("mirror_neuron") > 0.30:
        planned.append((["mirror_neuron", "social_brain"], "limbic", {"activation": 0.08, "valence": 0.06, "dominant_drive": "resonance_warmed_emotion"}))

    for sources, target, delta in planned:
        before = copy.deepcopy(states.get(target) or _default_organ_state(target, now_ms))
        after = _apply_delta(before, delta, trace={"event_kind": "organ_coupling", "source_organs": sources}, now_ms=now_ms)
        states[target] = after
        effect = {"source_organs": sources, "delta": delta}
        effects.append({"source_organs": sources, "target_organ_id": target, "effect": effect})
        durable_events.append(
            {
                "coupling_id": _event_id("orgcpl"),
                "source_experience_event_id": event_id,
                "person_id": person_id,
                "source_organs": list(sources),
                "target_organ_id": target,
                "effect": effect,
                "created_at_ms": now_ms,
            }
        )
    return effects, durable_events


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
    _repository(ledger).append_experience(
        {
            "event_id": event_id,
            "person_id": resolved,
            "session_id": session_id,
            "source_kind": source_kind,
            "event_kind": event_kind,
            "content_summary": _text(content_summary, 1000),
            "evidence": evidence_payload,
            "salience": float(salience),
            "valence": float(valence),
            "uncertainty": float(uncertainty),
            "created_at_ms": now,
        }
    )
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
    repository = _repository(ledger)
    row = repository.get_experience(event_id)
    if row is None:
        return {"status": "NOT_FOUND", "event_id": event_id}
    if bool(row.get("consumed_by_organs")):
        return {"status": "ALREADY_CONSUMED", "event_id": event_id}
    person_id = str(row["person_id"] or DEFAULT_PERSON_ID)
    event_kind = str(row["event_kind"] or "")
    content = str(row["content_summary"] or "")
    evidence = _map(row.get("evidence"))
    salience = _float(row["salience"])
    valence = _float(row["valence"])
    now_ms = int(row["created_at_ms"] or _now_ms())
    states: dict[str, dict[str, Any]] = {}
    organ_events: list[dict[str, Any]] = []
    durable_organ_events: list[dict[str, Any]] = []
    stored_states = repository.get_organ_states(person_id)
    deltas = _base_event_deltas(event_kind, _map(evidence), content, salience, valence, now_ms)
    for organ_id in ORGAN_IDS:
        current = _get_organ_state(stored_states, person_id, organ_id, now_ms)
        elapsed_min = max(0.0, (now_ms - int(current.get("updated_at_ms") or now_ms)) / 60000.0)
        decayed = _decay_state(current, elapsed_min)
        autonomous_delta = _autonomous_organ_delta(
            organ_id=organ_id,
            state=decayed,
            evidence=_map(evidence),
            elapsed_min=elapsed_min,
            now_ms=now_ms,
        )
        delta = _merge_delta(autonomous_delta, deltas.get(organ_id, {}))
        if delta:
            after = _apply_delta(
                decayed,
                delta,
                trace={
                    "experience_event_id": event_id,
                    "event_kind": event_kind,
                    "source_kind": str(row["source_kind"] or ""),
                    "autonomous_elapsed_min": round(elapsed_min, 3),
                    "has_event_perturbation": bool(deltas.get(organ_id)),
                },
                now_ms=now_ms,
            )
            durable_organ_events.append(
                {
                    "event_id": _event_id("orgevt"),
                    "organ_id": organ_id,
                    "delta": delta,
                    "state_before": decayed,
                    "state_after": after,
                }
            )
            organ_events.append({"organ_id": organ_id, "delta": delta, "after": _compact_organ(after)})
        else:
            after = decayed
        states[organ_id] = after
    coupling, durable_coupling_events = _couple_organs(
        person_id, states, event_id, now_ms
    )
    committed = repository.commit_organ_processing(
        experience_event_id=event_id,
        person_id=person_id,
        organ_events=durable_organ_events,
        coupling_events=durable_coupling_events,
        organ_states=states,
        created_at_ms=now_ms,
    )
    if not committed:
        return {"status": "ALREADY_CONSUMED", "event_id": event_id}
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
        "learning_memory": _map(state.get("learning_memory")),
    }


def get_organ_runtime_state(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID) -> dict[str, Any]:
    ensure_organ_schema(ledger)
    resolved = str(person_id or DEFAULT_PERSON_ID)
    now = _now_ms()
    states = _repository(ledger).get_organ_states(resolved)
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
    base_inner_for_dream = _map(after.get("inner_world"))
    default_trace_for_dream = _map(default_mode.get("memory_trace"))
    dream_residue_present = bool(_text(base_inner_for_dream.get("dream_residue"), 240) or _text(default_trace_for_dream.get("dream_residue"), 240))
    work_pressure = _clamp(max(strength("prefrontal"), strength("immune"), strength("cortex"), _float(base_pressure.get("work")) * 0.82))
    absence = _clamp(max(strength("social_brain") * 0.65 + strength("limbic") * 0.35, _float(base_pressure.get("absence")) * 0.92))
    dream = _clamp(max(strength("default_mode") * 0.65 + strength("glymphatic") * 0.2 + strength("hippocampus") * 0.15, _float(base_pressure.get("dream")) * 0.82))
    if not dream_residue_present:
        dream = min(dream, max(_float(base_pressure.get("dream")) * 0.35, strength("default_mode") * 0.22))
    fatigue = _clamp(max(strength("circadian") * 0.55 + strength("glymphatic") * 0.18 + strength("interoception") * 0.27 + _float(circadian.get("fatigue")) * 0.35, _float(base_pressure.get("fatigue")) * 0.86))
    pain_reward = _map(organs.get("pain_reward"))
    relationship = _clamp(_float(social.get("valence"), 0.5) * 0.50 + _float(limbic.get("valence"), 0.5) * 0.30 + _float(pain_reward.get("valence"), 0.5) * 0.08 + strength("mirror_neuron") * 0.12)

    pressure = _map(after.get("life_pressure"))
    pressure.update({"absence": absence, "dream": dream, "work": work_pressure, "fatigue": fatigue, "relationship": relationship})
    after["life_pressure"] = pressure

    affect = _map(after.get("affect"))
    affect.update(
        {
            "loneliness": _clamp(absence * 0.86),
            "attachment_warmth": _clamp(relationship * 0.88 + strength("mirror_neuron") * 0.12),
            "fatigue": fatigue,
            "tension": _clamp(work_pressure * 0.55 + strength("ans") * 0.25 + strength("immune") * 0.10 + strength("interoception") * 0.10),
            "energy": _clamp(1.0 - fatigue * 0.72 + strength("pain_reward") * 0.08 + strength("somatic") * 0.12),
            "irritation": _clamp(strength("pain_reward") * 0.55 + strength("immune") * 0.1),
            "arousal": _clamp(0.24 + strength("thalamus") * 0.52 + strength("prefrontal") * 0.12),
            "valence": _clamp(0.18 + strength("mirror_neuron") * 0.38 + strength("limbic") * 0.12),
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
    if default_trace.get("inner_numeric_state"):
        numeric = _map(default_trace.get("inner_numeric_state"))
        inner["numeric_state"] = numeric
        inner["attention_kind"] = _text(numeric.get("attention_kind") or numeric.get("attention"), 80)
        inner["preoccupation_expires_at_ms"] = int(_float(numeric.get("preoccupation_expires_at_ms")))
        inner["monologue_due"] = bool(numeric.get("monologue_due"))
        inner["monologue_suppression_reason"] = _text(numeric.get("monologue_suppression_reason"), 160)
        anchor = _text(numeric.get("current_preoccupation") or numeric.get("attention_target"), 180)
        if anchor:
            inner["current_preoccupation"] = anchor
            inner["attention_target"] = _text(numeric.get("attention_target") or anchor, 120)
        elif not _text(inner.get("dream_residue"), 240):
            inner["current_preoccupation"] = ""
            inner["attention_target"] = ""
            inner["unresolved_loop"] = ""
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
    _repository(ledger).append_synthesis(
        synthesis_id=_event_id("lifesyn"),
        person_id=resolved,
        source_experience_event_id=source_experience_event_id,
        organ_summary=after["organ_runtime"],
        state_patch={
            "life_pressure": after["life_pressure"],
            "affect": after["affect"],
            "embodiment": after["embodiment"],
            "motives": after["motives"],
        },
        created_at_ms=now,
    )
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
    _repository(ledger).open_proactive_episode(
        {
            "episode_id": episode_id,
            "person_id": str(person_id or DEFAULT_PERSON_ID),
            "source_action_id": source_action_id,
            "source_outbox_id": source_outbox_id,
            "motive": motive,
            "organ_sources": list(organ_sources or []),
            "message_text": _text(message_text, 1000),
            "expected_response_kind": expected_response_kind,
            "sent_at_ms": now,
            "outcome": outcome,
            "metadata": dict(metadata or {}),
        }
    )
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
    row = _repository(ledger).latest_pending_episode(resolved)
    if not row:
        return {}
    sent_at = int(row["sent_at_ms"] or 0)
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
        evidence={"episode_id": row["episode_id"], "motive": row["motive"], "outcome": outcome, "asks_context": asks_context},
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
    _repository(ledger).close_proactive_episode(
        episode_id=str(row["episode_id"]),
        person_id=resolved,
        user_response_turn_id=request_id or session_id,
        outcome=outcome,
        organ_delta_after=_map(delta_after),
        closed_at_ms=now,
    )
    return {
        "episode_id": row["episode_id"],
        "motive": row["motive"],
        "outcome": outcome,
        "asks_context": asks_context,
        "source_message": row["message_text"],
        "organ_delta_after": delta_after,
    }


def get_recent_life_experience(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, limit: int = 50) -> dict[str, Any]:
    ensure_organ_schema(ledger)
    resolved = str(person_id or DEFAULT_PERSON_ID)
    capped = max(1, min(int(limit or 50), 200))
    items = _repository(ledger).list_experiences(resolved, limit=capped)
    return {"person_id": resolved, "items": items}


def get_recent_proactive_episodes(*, ledger: Any, person_id: str = DEFAULT_PERSON_ID, limit: int = 20) -> dict[str, Any]:
    ensure_organ_schema(ledger)
    resolved = str(person_id or DEFAULT_PERSON_ID)
    capped = max(1, min(int(limit or 20), 100))
    items = _repository(ledger).list_proactive_episodes(resolved, limit=capped)
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
