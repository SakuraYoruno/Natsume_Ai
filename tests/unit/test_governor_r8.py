"""M28-D：Governor R8 情感感知门控测试（T-GOV-R8-01~04）"""

from __future__ import annotations

import pytest
from host_brain.governor.rules import evaluate_governor_gates


def _make_snapshot(emotion_tag: str = "NEUTRAL", mood_state: str = "steady") -> dict:
    return {
        "trust_level": 80,
        "energy_level": 80,
        "sys_mode": "normal",
        "rate_limit_hit": False,
        "scope": "host",
        "scope_allowlist": ["host", "memory", "system"],
        "safety_flag": False,
        "airlock_blocked": False,
        "emotion_tag": emotion_tag,
        "mood_state": mood_state,
    }


class TestGovernorR8:
    """T-GOV-R8-01~04：Governor R8 情感感知门控测试"""

    def test_r8_gate_exists_in_trace(self):
        """T-GOV-R8-01：evaluate_governor_gates 的 rule_trace 包含 R8_EMOTION 门控"""
        result = evaluate_governor_gates(_make_snapshot())
        gate_ids = [g["gate_id"] for g in result["rule_trace"]]
        assert "R8_EMOTION" in gate_ids

    def test_critical_mood_sets_simplified_prompt(self):
        """T-GOV-R8-02：mood_state="critical" 时，R8 门控的 simplified_prompt=True"""
        result = evaluate_governor_gates(_make_snapshot(mood_state="critical"))
        r8_gate = next(g for g in result["rule_trace"] if g["gate_id"] == "R8_EMOTION")
        assert r8_gate["simplified_prompt"] is True

    def test_happy_emotion_r8_not_triggered(self):
        """T-GOV-R8-03：emotion_tag="HAPPY" 时，R8 不触发，正常通过"""
        result = evaluate_governor_gates(_make_snapshot(emotion_tag="HAPPY"))
        r8_gate = next(g for g in result["rule_trace"] if g["gate_id"] == "R8_EMOTION")
        assert r8_gate["hit"] is False
        assert result["decision"] == "APPROVE"

    def test_steady_mood_no_simplified_prompt(self):
        """T-GOV-R8-04：mood_state="steady" 时，simplified_prompt=False"""
        result = evaluate_governor_gates(_make_snapshot(mood_state="steady"))
        r8_gate = next(g for g in result["rule_trace"] if g["gate_id"] == "R8_EMOTION")
        assert r8_gate["simplified_prompt"] is False
