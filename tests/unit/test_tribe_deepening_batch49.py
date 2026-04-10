"""第四十九批联动场景单元测试

覆盖 T-383~T-390 当前已落地的 8 个核心联动点：
- T-383: InteroceptionAgent 高内感受压力 -> LimbicAgent.drive_emotion_metabolism()
- T-384: InteroceptionAgent 高恢复紧迫度 -> PainRewardAgent.record_pain()
- T-385: InteroceptionAgent 低恢复紧迫度 -> PainRewardAgent.record_reward()
- T-386: PeripheralAgent 平台断开 -> InteroceptionAgent.inject_external_stress()
- T-387: PeripheralAgent 免疫拦截 -> InteroceptionAgent.inject_external_stress()
- T-388: PeripheralAgent 高强度正向情绪 -> LimbicAgent.drive_emotion_metabolism()
- T-389: PeripheralAgent 平台连接恢复 -> PainRewardAgent.record_reward()
- T-390: SomaticAgent 低疲劳 -> PainRewardAgent.record_reward()
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from host_brain.tribe.interoception_agent import InteroceptionAgent
from host_brain.tribe.peripheral_agent import PeripheralAgent
from host_brain.tribe.somatic_agent import SomaticAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _Event:
    def __init__(self, payload: dict, ts_ms: int = 0) -> None:
        self.payload = payload
        self.ts_ms = ts_ms


class _FakeSomatic:
    def __init__(self, fatigue_level: float) -> None:
        self._fatigue_level = fatigue_level

    def export_runtime_state(self) -> dict[str, float]:
        return {"fatigue_level": self._fatigue_level}


def _run(coro):
    return asyncio.run(coro)


def _make_intero_registry(*, stress: float, cognitive_load: float, sleep_pressure: float, energy: float, homeostasis_ok: bool = True, immune_risk: str = "none") -> TribeStateRegistry:
    registry = TribeStateRegistry()
    registry.set_state(
        "endocrine_agent",
        {
            "stress_index": stress,
            "energy_reserve": energy,
            "homeostasis_ok": homeostasis_ok,
        },
        writer="unit",
    )
    registry.set_state(
        "proprioception_agent",
        {
            "cognitive_load_index": cognitive_load,
            "readiness_hint": 0.5,
            "continuity_hint": 0.5,
        },
        writer="unit",
    )
    registry.set_state(
        "circadian_agent",
        {
            "phase": "day",
            "sleep_pressure": sleep_pressure,
            "proactive_multiplier": 1.0,
        },
        writer="unit",
    )
    registry.set_state(
        "immune_agent",
        {
            "risk_level": immune_risk,
            "block_rate": 0.0,
            "counters": {"block": 0},
        },
        writer="unit",
    )
    return registry


def test_t383_interoception_high_load_drives_negative_limbic(monkeypatch) -> None:
    registry = _make_intero_registry(stress=0.85, cognitive_load=0.82, sleep_pressure=0.35, energy=0.22)
    agent = InteroceptionAgent(state_registry=registry)
    mock_limbic = MagicMock()
    mock_pr = MagicMock()
    mock_glymphatic = MagicMock()

    monkeypatch.setattr("host_brain.tribe.interoception_agent.emit_tribe_event", lambda *args, **kwargs: None)
    monkeypatch.setattr("host_brain.tribe.somatic_agent.get_somatic_agent", lambda: _FakeSomatic(0.88))

    with patch("host_brain.tribe.limbic_agent.get_limbic_agent", return_value=mock_limbic), \
         patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=mock_pr), \
         patch("host_brain.tribe.glymphatic_agent.get_glymphatic_agent", return_value=mock_glymphatic):
        profile = agent.evaluate_profile(source="unit_t383", ts_ms=101)

    assert profile["interoceptive_index"] >= 0.70
    mock_limbic.drive_emotion_metabolism.assert_called()
    kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
    assert kwargs["source"] == "interoception_agent"
    assert kwargs["event_kind"].startswith("interoception_high_load:")
    assert kwargs["valence"] < 0


def test_t384_interoception_high_urgency_records_pain(monkeypatch) -> None:
    registry = _make_intero_registry(stress=0.92, cognitive_load=0.86, sleep_pressure=0.95, energy=0.18, homeostasis_ok=False)
    agent = InteroceptionAgent(state_registry=registry)
    mock_limbic = MagicMock()
    mock_pr = MagicMock()
    mock_glymphatic = MagicMock()

    monkeypatch.setattr("host_brain.tribe.interoception_agent.emit_tribe_event", lambda *args, **kwargs: None)
    monkeypatch.setattr("host_brain.tribe.somatic_agent.get_somatic_agent", lambda: _FakeSomatic(0.72))

    with patch("host_brain.tribe.limbic_agent.get_limbic_agent", return_value=mock_limbic), \
         patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=mock_pr), \
         patch("host_brain.tribe.glymphatic_agent.get_glymphatic_agent", return_value=mock_glymphatic):
        profile = agent.evaluate_profile(source="unit_t384", ts_ms=102)

    assert profile["recovery_urgency"] >= 0.75
    mock_pr.record_pain.assert_called()
    kwargs = mock_pr.record_pain.call_args.kwargs
    assert kwargs["source"].startswith("interoception_high_urgency:")
    assert kwargs["intensity"] > 0


def test_t385_interoception_low_urgency_records_reward(monkeypatch) -> None:
    registry = _make_intero_registry(stress=0.08, cognitive_load=0.10, sleep_pressure=0.05, energy=0.92)
    agent = InteroceptionAgent(state_registry=registry)
    mock_limbic = MagicMock()
    mock_pr = MagicMock()
    mock_glymphatic = MagicMock()

    monkeypatch.setattr("host_brain.tribe.interoception_agent.emit_tribe_event", lambda *args, **kwargs: None)
    monkeypatch.setattr("host_brain.tribe.somatic_agent.get_somatic_agent", lambda: _FakeSomatic(0.10))

    with patch("host_brain.tribe.limbic_agent.get_limbic_agent", return_value=mock_limbic), \
         patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=mock_pr), \
         patch("host_brain.tribe.glymphatic_agent.get_glymphatic_agent", return_value=mock_glymphatic):
        profile = agent.evaluate_profile(source="unit_t385", ts_ms=103)

    assert profile["recovery_urgency"] < 0.20
    mock_pr.record_reward.assert_called()
    kwargs = mock_pr.record_reward.call_args.kwargs
    assert kwargs["source"].startswith("interoception_low_urgency:")
    assert kwargs["intensity"] > 0


def test_t386_peripheral_disconnect_injects_stress() -> None:
    agent = PeripheralAgent(state_registry=TribeStateRegistry())
    mock_intero = MagicMock()

    with patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=mock_intero):
        _run(agent._on_platform_disconnected(_Event(payload={"platform": "wechat"}, ts_ms=201)))

    mock_intero.inject_external_stress.assert_called_once()
    kwargs = mock_intero.inject_external_stress.call_args.kwargs
    assert kwargs["source"] == "peripheral_disconnected:wechat"
    assert kwargs["intensity"] == 0.20


def test_t387_peripheral_immune_block_injects_stress() -> None:
    agent = PeripheralAgent(state_registry=TribeStateRegistry())
    mock_intero = MagicMock()

    with patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=mock_intero):
        _run(agent._on_immune_block(_Event(payload={"risk_level": "high"}, ts_ms=202)))

    mock_intero.inject_external_stress.assert_called_once()
    kwargs = mock_intero.inject_external_stress.call_args.kwargs
    assert kwargs["source"] == "peripheral_immune_block:high"
    assert kwargs["intensity"] == 0.22


def test_t388_peripheral_positive_emotion_drives_limbic() -> None:
    agent = PeripheralAgent(state_registry=TribeStateRegistry())
    mock_limbic = MagicMock()

    with patch("host_brain.tribe.limbic_agent.get_limbic_agent", return_value=mock_limbic):
        _run(
            agent._on_emotion_state_changed(
                _Event(payload={"emotion_tag": "JOY", "intensity": 0.82}, ts_ms=203)
            )
        )

    mock_limbic.drive_emotion_metabolism.assert_called_once()
    kwargs = mock_limbic.drive_emotion_metabolism.call_args.kwargs
    assert kwargs["source"] == "peripheral_agent"
    assert kwargs["event_kind"] == "peripheral_positive_emotion:joy"
    assert kwargs["valence"] > 0


def test_t389_peripheral_connected_records_reward() -> None:
    agent = PeripheralAgent(state_registry=TribeStateRegistry())
    mock_intero = MagicMock()
    mock_pr = MagicMock()

    with patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=mock_intero), \
         patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=mock_pr):
        _run(agent._on_platform_connected(_Event(payload={"platform": "wechat", "detail": "restored"}, ts_ms=204)))

    mock_intero.reduce_recovery_urgency.assert_called_once()
    mock_pr.record_reward.assert_called_once()
    kwargs = mock_pr.record_reward.call_args.kwargs
    assert kwargs["source"] == "peripheral_connected:wechat"
    assert kwargs["intensity"] == 0.12
    assert kwargs["metadata"]["platform"] == "wechat"


def test_t390_somatic_low_fatigue_records_reward() -> None:
    agent = SomaticAgent(state_registry=TribeStateRegistry())
    mock_intero = MagicMock()
    mock_limbic = MagicMock()
    mock_pr = MagicMock()

    with patch("host_brain.tribe.interoception_agent.get_interoception_agent", return_value=mock_intero), \
         patch("host_brain.tribe.limbic_agent.get_limbic_agent", return_value=mock_limbic), \
         patch("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", return_value=mock_pr):
        _run(agent._on_body_sense_updated(_Event(payload={"fatigue_level": 0.10}, ts_ms=205)))

    mock_pr.record_reward.assert_called_once()
    kwargs = mock_pr.record_reward.call_args.kwargs
    assert kwargs["source"].startswith("somatic_low_fatigue:")
    assert kwargs["intensity"] > 0
