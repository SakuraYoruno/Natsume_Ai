from __future__ import annotations

import asyncio
from types import SimpleNamespace

import host_brain.tribe.pain_reward_agent as pain_reward_module
from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.pain_reward_agent import PainRewardAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_pain_reward_canonical_life_retires_hedonic_writer_and_fanout(
    monkeypatch,
) -> None:
    registry = TribeStateRegistry()
    agent = PainRewardAgent(
        state_registry=registry,
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
    )

    def _unexpected(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("canonical Life must not invoke legacy hedonic dependencies")

    monkeypatch.setattr(pain_reward_module, "get_default_mode_agent", _unexpected)
    monkeypatch.setattr(pain_reward_module, "get_limbic_agent", _unexpected)
    monkeypatch.setattr(pain_reward_module, "get_endocrine_agent", _unexpected)
    monkeypatch.setattr(pain_reward_module, "get_interoception_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", _unexpected)

    pain = agent.record_pain(source="rejection", intensity=0.9)
    reward = agent.record_reward(source="task_success", intensity=0.9)
    decay = agent.tick_decay()
    state = agent.get_hedonic_state()
    stress = agent.get_stress_signal()
    modulation = agent.get_emotion_modulation()

    assert pain["status"] == "RETIRED_CANONICAL_LIFE"
    assert reward["operation"] == "record_reward"
    assert decay["operation"] == "tick_decay"
    assert state["hedonic_tone"] == "RETIRED"
    assert stress["net_stress_delta"] == 0.0
    assert modulation["suggested_emotion_tag"] is None
    assert agent.get_loneliness_level() == 0.0
    assert agent.export_runtime_state()["operation"] == "export_runtime_state"
    assert agent.save_state() is False
    assert agent.load_state() is False
    assert agent.generate_hedonic_reflection(trigger_event="rejection") is None
    assert registry.get_state("pain_reward_agent") is None

    agent.register_subscriptions()
    event = SimpleNamespace(
        event_type="BOND_REGRESS",
        payload={"emotion_tag": "SAD", "emotion_intensity": 0.9, "intensity": 0.9},
    )
    asyncio.run(agent._on_interaction_recorded(event))
    asyncio.run(agent._on_emotion_updated(event))
    asyncio.run(agent._on_immune_block(event))
    asyncio.run(agent._on_bond_progress(event))
    asyncio.run(agent._on_bond_regress(event))
    asyncio.run(agent._on_task_success(event))
    asyncio.run(agent._on_humor_landed(event))
    asyncio.run(agent._on_loneliness_signal(event))
    agent.on_tribe_event(event)

    assert registry.get_state("pain_reward_agent") is None
