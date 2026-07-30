from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.cortex_route_decider import route_intent
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

    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_circadian_agent", lambda: _FakeCircadian())
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_proprioception_agent", lambda: _FakeProprioception())
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_social_brain_agent", lambda: _FakeSocialBrain())

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
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_circadian_agent", lambda: _FakeCircadian())
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_proprioception_agent", lambda: _FakeProprioception())
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_social_brain_agent", lambda: _FakeSocialBrain())

    result = InitiativeGovernorAgent(state_registry=registry).evaluate_initiative(
        action="proactive_voice",
        session_id="sess_1",
        user_id="u1",
        motive=0.8,
        context={},
    )

    assert result["decision"] == "BLOCK"
    assert result["reason_code"] == "GATE_PAUSE"


def test_initiative_governor_retires_legacy_bond_and_organ_fanout_with_canonical_life(
    monkeypatch,
) -> None:
    registry = TribeStateRegistry(persist_throttle=999999)
    agent = InitiativeGovernorAgent(
        state_registry=registry,
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
    )

    def _unexpected(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("canonical Life must not invoke legacy InitiativeGovernor dependencies")

    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_circadian_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_proprioception_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_social_brain_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_interoception_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_pain_reward_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_default_mode_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_vestibular_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.emit_tribe_event", _unexpected)
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", _unexpected)

    result = agent.evaluate_initiative(
        action="proactive_talk",
        motive=0.9,
        session_id="s-canonical",
        user_id="u-canonical",
        context={"pending_followup_count": 3},
    )
    assert result["status"] == "RETIRED_CANONICAL_LIFE"
    assert result["decision"] == "DEFER"
    assert result["reason_code"] == "LEGACY_INITIATIVE_GOVERNOR_RETIRED"
    assert agent.generate_proactive_topic(
        loneliness_level=0.9,
        anticipation_level=0.9,
        bond_stage="SOULMATE",
    ) is None
    assert agent.get_last_decision()["status"] == "RETIRED_CANONICAL_LIFE"
    assert agent.export_runtime_state()["last_decision"] == "RETIRED"

    agent.register_subscriptions()
    event = SimpleNamespace(
        payload={"executed": True},
        event_type="BOND_VECTOR_UPDATED",
        ts_ms=123,
    )
    asyncio.run(agent._on_glymphatic_cycle(event))
    asyncio.run(agent._on_state_signal(event))

    assert agent._recovery_mode is False
    assert registry.get_state("initiative_governor_agent") is None


def test_canonical_life_forces_legacy_initiative_route_to_silence(monkeypatch) -> None:
    agent = InitiativeGovernorAgent(
        state_registry=TribeStateRegistry(persist_throttle=999999),
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.cortex_route_decider.get_initiative_governor_agent",
        lambda: agent,
    )

    result = route_intent(
        message="想主动聊一聊",
        tribe_context={
            "initiative_request": True,
            "initiative_action": "proactive_talk",
            "session_id": "s-canonical",
            "user_id": "u-canonical",
            "vital_signs": {},
        },
    )

    assert result == {
        "route": "silent",
        "reason": "initiative_defer:LEGACY_INITIATIVE_GOVERNOR_RETIRED",
        "gate_state": "open",
        "confidence": 0.78,
    }
