from __future__ import annotations

from host_brain.app.host_hard_state import HostHardStateOwner
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
