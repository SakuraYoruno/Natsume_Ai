from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.app.host_hard_state import HostHardStateOwner
from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.endocrine_agent import EndocrineAgent


def test_endocrine_agent_resolve_state_and_vitals() -> None:
    agent = EndocrineAgent()
    state = agent.resolve_endocrine_state(
        hard_state={"energy_level": 68, "mood_state": "steady", "sys_mode": "normal"},
        emotion_tag="CALM",
        emotion_intensity=0.2,
    )

    assert {"dopamine_level", "serotonin_level", "cortisol_level", "energy_reserve", "gate_state", "source"} <= set(
        state.keys()
    )
    vitals = agent.resolve_vital_signs(endocrine_state=state)
    assert vitals["gate_state"] in {"open", "cautious", "protective"}
    assert 0.0 <= float(vitals["stress_index"]) <= 1.0


def test_endocrine_agent_retires_legacy_buffer_with_canonical_life(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    agent = EndocrineAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
    )
    bus = _Bus()
    monkeypatch.setattr(
        "host_brain.tribe.event_bus.get_tribe_event_bus",
        lambda: bus,
    )

    state = agent.resolve_endocrine_state(
        hard_state={"energy_level": 0, "mood_state": "critical", "sys_mode": "locked"},
        emotion_tag="HURT",
        emotion_intensity=0.9,
    )
    stress = agent.record_stress_event(source="test", intensity=0.9)
    direct = agent.record_legacy_hormonal_delta(
        source="test",
        cortisol_delta=0.08,
        oxytocin_delta=0.1,
    )
    event = SimpleNamespace(payload={"intensity": 0.9, "loneliness": 0.9, "risk_level": "high"})
    asyncio.run(agent._on_bond_progress(event))
    asyncio.run(agent._on_bond_regress(event))
    asyncio.run(agent._on_loneliness_signal(event))
    asyncio.run(agent._on_task_success_endocrine(event))
    asyncio.run(agent._on_immune_block_endocrine(event))
    agent.register_subscriptions()

    assert state["status"] == "RETIRED_CANONICAL_LIFE"
    for result in (stress, direct):
        assert result["status"] == "RETIRED_CANONICAL_LIFE"
        assert result["authority"] == "CANONICAL_LIFE_JOURNAL"
        assert result["reason_code"] == "LEGACY_ENDOCRINE_MUTATION_RETIRED"
    assert agent._last_state == {}
    assert agent._bond_cortisol_delta == 0.0
    assert agent._bond_oxytocin_delta == 0.0
    assert bus.subscriptions == []
    assert agent.generate_hormonal_state_hint(cortisol=0.9, oxytocin=0.9) is None
    runtime_state = agent.export_runtime_state()
    assert runtime_state["status"] == "RETIRED_CANONICAL_LIFE"
    assert runtime_state["state"] == {}


def test_host_owner_get_endocrine_state_delegates_with_immutable_snapshot() -> None:
    called: dict[str, object] = {}

    class _FakeAgent:
        def resolve_endocrine_state(self, **kwargs):  # noqa: ANN003
            called.update(kwargs)
            return {"gate_state": "open", "source": "fake_endocrine_agent"}

    owner = HostHardStateOwner.bootstrap(
        initial_sys_mode="normal",
        endocrine_resolver=_FakeAgent().resolve_endocrine_state,
    )

    result = owner.get_endocrine_state(emotion_tag="HAPPY", emotion_intensity=0.5)
    assert result["source"] == "fake_endocrine_agent"
    assert called["hard_state"] is owner.get_hard_state()
    assert called["emotion_tag"] == "HAPPY"
    assert float(called["emotion_intensity"]) == 0.5
