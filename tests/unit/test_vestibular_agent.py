from __future__ import annotations

import asyncio

from host_brain.core.ids import now_ms
from host_brain.tribe.tribe_state import TribeStateRegistry
from host_brain.tribe.vestibular_agent import VestibularAgent


class _Event:
    def __init__(self, event_type: str, payload: dict | None = None, ts_ms: int = 0) -> None:
        self.event_type = event_type
        self.payload = payload or {}
        self.ts_ms = ts_ms


def _run(coro):
    return asyncio.run(coro)


def test_vestibular_agent_stability_jitter() -> None:
    agent = VestibularAgent(state_registry=TribeStateRegistry())
    base = now_ms()
    _run(agent._on_mode_transition(_Event("MODE_TRANSITION", {"from_mode": "chat", "to_mode": "proactive"}, ts_ms=base)))
    _run(agent._on_platform_signal(_Event("PLATFORM_DISCONNECTED", {"platform": "discord"}, ts_ms=base + 300)))
    _run(agent._on_initiative_decision(_Event("INITIATIVE_DECISION_MADE", {"decision": "DEFER"}, ts_ms=base + 600)))
    exported = agent.export_runtime_state()

    assert exported["jitter_index"] > 0.0
    assert exported["stability_state"] in {"stable", "unstable", "critical"}
    assert exported["mode_transition_count_10m"] >= 1


def test_vestibular_high_jitter_side_effects_are_debounced(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class _FakeAns:
        def adjust_rhythm_for_high_interrupt(self, **kwargs):  # noqa: ANN003
            calls.append(dict(kwargs))

    class _Noop:
        def drive_emotion_metabolism(self, **kwargs):  # noqa: ANN003
            pass

        def record_stress_event(self, **kwargs):  # noqa: ANN003
            pass

        def record_pain(self, **kwargs):  # noqa: ANN003
            pass

        def inject_external_stress(self, **kwargs):  # noqa: ANN003
            pass

    monkeypatch.setenv("HOST_VESTIBULAR_HIGH_JITTER_EFFECT_COOLDOWN_MS", "300000")
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.get_ans_agent", lambda: _FakeAns())
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.get_limbic_agent", lambda: _Noop())
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.get_endocrine_agent", lambda: _Noop())
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.get_pain_reward_agent", lambda: _Noop())
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.get_interoception_agent", lambda: _Noop())

    agent = VestibularAgent(state_registry=TribeStateRegistry())
    base = now_ms()
    agent._mode_transition_ts = [base + i for i in range(7)]  # noqa: SLF001

    agent.evaluate_stability(source="unit", ts_ms=base + 10)
    agent.evaluate_stability(source="unit", ts_ms=base + 20)

    assert len(calls) == 1
