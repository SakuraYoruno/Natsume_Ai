from __future__ import annotations

from host_brain.tribe.initiative_governor_agent import InitiativeGovernorAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _FakeCircadian:
    def export_runtime_state(self) -> dict:
        return {
            "phase": "day",
            "proactive_multiplier": 1.0,
            "sleep_pressure": 0.2,
        }


class _FakeProprioception:
    def export_runtime_state(self) -> dict:
        return {
            "active_session_id": "sess_1",
            "cognitive_load_index": 0.18,
            "continuity_hint": 0.78,
        }


class _FakeSocialBrain:
    def get_bond_context(self, *, session_id: str, user_id: str):  # noqa: ANN001
        return {
            "bond_vector": {
                "trust": 0.72,
                "affinity": 0.75,
                "safety": 0.74,
                "novelty": 0.42,
                "dependency": 0.5,
                "repair_confidence": 0.7,
            }
        }


def test_initiative_governor_allows_when_scores_pass(monkeypatch) -> None:
    registry = TribeStateRegistry()
    registry.set_state(
        "endocrine_agent",
        {"gate_state": "open", "stress_index": 0.15, "energy_reserve": 0.88, "homeostasis_ok": True},
        writer="unit",
    )
    registry.set_state("pain_reward_agent", {"last_loneliness_level": 0.62}, writer="unit")

    monkeypatch.setattr("host_brain.tribe.circadian_agent.get_circadian_agent", lambda: _FakeCircadian())
    monkeypatch.setattr("host_brain.tribe.proprioception_agent.get_proprioception_agent", lambda: _FakeProprioception())
    monkeypatch.setattr("host_brain.tribe.social_brain_agent.get_social_brain_agent", lambda: _FakeSocialBrain())

    result = InitiativeGovernorAgent(state_registry=registry).evaluate_initiative(
        action="proactive_voice",
        session_id="sess_1",
        user_id="u1",
        context={"active_topic_id": "topic_1", "pending_followup_count": 2},
    )

    assert result["decision"] == "ALLOW"
    assert result["reason_code"] == "PASS_ALL_GATES"
    assert result["scores"]["motive"] >= 0.35
    assert result["scores"]["readiness"] >= 0.45
    assert result["scores"]["continuity"] >= 0.35


def test_initiative_governor_blocks_when_gate_pause(monkeypatch) -> None:
    registry = TribeStateRegistry()
    registry.set_state(
        "endocrine_agent",
        {"gate_state": "pause", "stress_index": 0.1, "energy_reserve": 0.9, "homeostasis_ok": True},
        writer="unit",
    )
    monkeypatch.setattr("host_brain.tribe.circadian_agent.get_circadian_agent", lambda: _FakeCircadian())
    monkeypatch.setattr("host_brain.tribe.proprioception_agent.get_proprioception_agent", lambda: _FakeProprioception())
    monkeypatch.setattr("host_brain.tribe.social_brain_agent.get_social_brain_agent", lambda: _FakeSocialBrain())

    result = InitiativeGovernorAgent(state_registry=registry).evaluate_initiative(
        action="proactive_voice",
        session_id="sess_1",
        user_id="u1",
        motive=0.8,
        context={},
    )

    assert result["decision"] == "BLOCK"
    assert result["reason_code"] == "GATE_PAUSE"
