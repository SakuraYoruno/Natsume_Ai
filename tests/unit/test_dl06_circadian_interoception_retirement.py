from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.circadian_agent import CircadianAgent
from host_brain.tribe.interoception_agent import InteroceptionAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_canonical_circadian_keeps_time_projection_but_retires_affect_fanout(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical circadian must not run a legacy affect side effect")

    registry = TribeStateRegistry()
    agent = CircadianAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.circadian_agent.emit_tribe_event", _forbidden)
    monkeypatch.setattr("host_brain.tribe.circadian_agent.get_glymphatic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.circadian_agent.get_endocrine_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.circadian_agent.get_ans_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.circadian_agent.get_pain_reward_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.circadian_agent.get_limbic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.circadian_agent.get_interoception_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.circadian_agent.get_default_mode_agent", _forbidden)

    agent.register_subscriptions()
    refreshed = agent.refresh_state(source="canonical-test", ts_ms=1_712_620_800_000)
    exported = agent.export_runtime_state()
    callbacks = [
        asyncio.run(getattr(agent, name)(SimpleNamespace(event_type=name, ts_ms=123)))
        for name in (
            "_on_mode_transition",
            "_on_channel_message_received",
            "_on_voice_pipeline_started",
            "_on_voice_pipeline_ended",
        )
    ]

    assert bus.subscriptions == []
    assert refreshed["authority"] == "RUNTIME_TIME_OBSERVATION"
    assert refreshed["self_affect_authority"] == "CANONICAL_LIFE_JOURNAL"
    assert refreshed["projection"] == "TIME_ONLY"
    assert refreshed["legacy_side_effects_suppressed"] is True
    assert exported["authority"] == "RUNTIME_TIME_OBSERVATION"
    assert agent.generate_circadian_mood_hint(energy_level=0.1) is None
    assert agent.tick_emotion_baseline()["reason_code"] == "LEGACY_CIRCADIAN_AFFECT_FANOUT_RETIRED"
    assert {result["reason_code"] for result in callbacks if result} == {
        "LEGACY_CIRCADIAN_AFFECT_FANOUT_RETIRED"
    }
    assert registry.get_state("circadian_agent") is None


def test_canonical_interoception_rejects_unscoped_body_budget_writes(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical interoception must not use a legacy body-affect dependency")

    registry = TribeStateRegistry()
    agent = InteroceptionAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.interoception_agent.emit_tribe_event", _forbidden)
    monkeypatch.setattr("host_brain.tribe.interoception_agent.get_somatic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.interoception_agent.get_glymphatic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.interoception_agent.get_limbic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.interoception_agent.get_pain_reward_agent", _forbidden)

    agent.register_subscriptions()
    profile = agent.evaluate_profile(source="canonical-test", ts_ms=456)
    exported = agent.export_runtime_state()
    rejected = [
        agent.inject_external_stress(source="legacy-somatic", intensity=0.8, ts_ms=457),
        agent.reduce_recovery_urgency(source="legacy-sleep", relief=0.8, ts_ms=458),
        agent.tick_body_budget(source="legacy-scheduler"),
        asyncio.run(agent._on_state_signal(SimpleNamespace(event_type="EMOTION_STATE_CHANGED", ts_ms=459))),
    ]

    assert bus.subscriptions == []
    assert profile["reason_code"] == "LEGACY_INTEROCEPTION_BODY_BUDGET_RETIRED"
    assert profile["projection"] == "UNAVAILABLE"
    assert profile["accepted"] is False
    assert exported["status"] == "RETIRED_CANONICAL_LIFE"
    assert exported["projection"] == "UNAVAILABLE"
    assert agent.generate_interoceptive_narrative(recovery_urgency=0.9) is None
    assert {
        result["reason_code"]
        for result in rejected
        if result is not None
    } == {"LEGACY_INTEROCEPTION_BODY_BUDGET_RETIRED"}
    assert registry.get_state("interoception_agent") is None
