from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.language_system_agent import LanguageSystemAgent
from host_brain.tribe.pain_reward_agent import PainRewardAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def _canonical_ports() -> TribeApplicationPorts:
    return TribeApplicationPorts(
        affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
    )


def test_canonical_language_keeps_pure_analysis_but_retires_affect_feedback(
    monkeypatch,
) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical language path must not resolve legacy affect organs")

    bus = _Bus()
    registry = TribeStateRegistry()
    agent = LanguageSystemAgent(
        application_ports=_canonical_ports(),
        state_registry=registry,
    )
    monkeypatch.setattr(
        "host_brain.tribe.event_bus.get_tribe_event_bus",
        lambda: bus,
    )
    for symbol in (
        "get_pain_reward_agent",
        "get_endocrine_agent",
        "get_limbic_agent",
        "get_interoception_agent",
    ):
        monkeypatch.setattr(
            f"host_brain.tribe.language_system_agent.{symbol}",
            _forbidden,
        )

    agent.register_subscriptions()
    dual = agent.resolve_dual_system(
        user_text="我现在很难过，也有点着急。",
        bond_stage="soulmate",
        emotion_tag="SAD",
        loneliness_level=0.95,
        high_loneliness_flag=True,
    )
    event = SimpleNamespace(payload={"emotion_tag": "ANGRY"})
    callback_results = [
        asyncio.run(getattr(agent, name)(event))
        for name in (
            "_on_emotion_state_changed",
            "_on_bond_stage_changed",
            "_on_loneliness_signal",
        )
    ]

    assert bus.subscriptions == []
    assert dual["style"]["bond_stage"] == "friend"
    assert dual["legacy_side_effects"]["reason_code"] == (
        "LEGACY_LANGUAGE_AFFECT_FEEDBACK_RETIRED"
    )
    assert registry.get_state("language_system_agent") is None
    assert {
        result["reason_code"] for result in callback_results
    } == {"LEGACY_LANGUAGE_AFFECT_FEEDBACK_RETIRED"}
    assert agent.wernicke_deep_parse(
        user_text="我到底应该怎么办？",
        intent_type="complex",
    )["source"] == "retired_canonical_life"
    assert agent.broca_generate_opening_hint(emotional_pressure=0.95) is None


def test_canonical_pain_reward_rejects_global_hedonic_writes(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical pain/reward path must not resolve legacy organs")

    bus = _Bus()
    registry = TribeStateRegistry()
    agent = PainRewardAgent(
        application_ports=_canonical_ports(),
        state_registry=registry,
    )
    monkeypatch.setattr(
        "host_brain.tribe.event_bus.get_tribe_event_bus",
        lambda: bus,
    )
    for symbol in (
        "get_limbic_agent",
        "get_endocrine_agent",
        "get_interoception_agent",
        "get_default_mode_agent",
    ):
        monkeypatch.setattr(
            f"host_brain.tribe.pain_reward_agent.{symbol}",
            _forbidden,
        )

    agent.register_subscriptions()
    pain = agent.record_pain(source="language_emotional_pressure", intensity=0.9)
    reward = agent.record_reward(source="task_success", intensity=0.9)
    event = SimpleNamespace(
        event_type="EMOTION_UPDATED",
        payload={"emotion_tag": "SAD", "emotion_intensity": 0.9},
    )
    callback_results = [
        asyncio.run(getattr(agent, name)(event))
        for name in (
            "_on_interaction_recorded",
            "_on_emotion_updated",
            "_on_immune_block",
            "_on_bond_progress",
            "_on_bond_regress",
            "_on_task_success",
            "_on_humor_landed",
            "_on_loneliness_signal",
        )
    ]

    assert bus.subscriptions == []
    assert pain["reason_code"] == "LEGACY_PAIN_REWARD_AFFECT_FEEDBACK_RETIRED"
    assert reward["reason_code"] == "LEGACY_PAIN_REWARD_AFFECT_FEEDBACK_RETIRED"
    assert agent.tick_decay()["pain_level"] == 0.0
    assert registry.get_state("pain_reward_agent") is None
    assert {
        result["reason_code"] for result in callback_results
    } == {"LEGACY_PAIN_REWARD_AFFECT_FEEDBACK_RETIRED"}
    assert agent.on_tribe_event(event)["accepted"] is False
    assert agent.get_hedonic_state()["hedonic_tone"] == "retired"
    assert agent.get_stress_signal()["net_stress_delta"] == 0.0
    assert agent.get_emotion_modulation()["suggested_emotion_tag"] is None
    assert agent.generate_hedonic_reflection(delta_valence=0.9) is None
    assert agent.save_state() is False
    assert agent.load_state() is False
