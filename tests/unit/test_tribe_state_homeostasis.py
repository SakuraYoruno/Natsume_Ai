from __future__ import annotations

from host_brain.tribe.tribe_state import TribeStateRegistry


def test_tribe_state_homeostasis_report_and_history() -> None:
    registry = TribeStateRegistry()
    registry.set_state(
        "endocrine_agent",
        {"gate_state": "throttle", "stress_index": 0.64, "energy_reserve": 0.38, "homeostasis_ok": False},
        writer="unit",
    )
    registry.set_state(
        "limbic_agent",
        {"emotion_tag": "ANXIOUS", "mood_state": "restless", "silence_willingness": 0.42},
        writer="unit",
    )
    registry.set_state(
        "social_brain_agent",
        {
            "bond_stage": "friend",
            "bond_vector": {"trust": 0.65, "affinity": 0.62, "safety": 0.7, "repair_confidence": 0.58},
            "bond_trend_hint": "progress",
        },
        writer="unit",
    )
    registry.set_state(
        "pain_reward_agent",
        {"hedonic_tone": "negative", "pain_level": 0.51, "reward_level": 0.2, "net_valence": -0.33, "last_loneliness_level": 0.72},
        writer="unit",
    )
    registry.set_state("immune_agent", {"risk_level": "medium", "block_rate": 0.2, "counters": {"block": 3}}, writer="unit")
    registry.set_state("circadian_agent", {"phase": "night", "proactive_multiplier": 0.35, "sleep_pressure": 0.81}, writer="unit")
    registry.set_state("proprioception_agent", {"cognitive_load_index": 0.66, "readiness_hint": 0.32, "continuity_hint": 0.37}, writer="unit")

    vital = registry.aggregate_vital_signs()
    report = registry.get_homeostasis_report(now_ts_ms=int(vital["ts_ms"]))
    history = registry.export_homeostasis_history()

    assert vital["gate_state"] == "throttle"
    assert vital["high_loneliness_flag"] is True
    assert "homeostasis_report" in vital
    assert report["status"] in {"warning", "critical"}
    assert len(history["minute_samples"]) >= 1
    assert len(history["hourly_samples"]) >= 1
    assert len(history["daily_samples"]) >= 1
