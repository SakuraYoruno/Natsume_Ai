from __future__ import annotations

from host_brain.composition import process_services as dependencies
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


def test_dependencies_get_endocrine_state_delegates_to_tribe_agent(monkeypatch) -> None:
    called: dict[str, object] = {}

    class _FakeAgent:
        def resolve_endocrine_state(self, **kwargs):  # noqa: ANN003
            called.update(kwargs)
            return {"gate_state": "open", "source": "fake_endocrine_agent"}

    monkeypatch.setattr("host_brain.tribe.endocrine_agent.get_endocrine_agent", lambda: _FakeAgent())
    monkeypatch.setattr(
        "host_brain.composition.process_services.get_hard_state",
        lambda: {"energy_level": 60, "mood_state": "steady", "sys_mode": "normal"},
    )

    result = dependencies.get_endocrine_state(emotion_tag="HAPPY", emotion_intensity=0.5)
    assert result["source"] == "fake_endocrine_agent"
    assert called["emotion_tag"] == "HAPPY"
    assert float(called["emotion_intensity"]) == 0.5
