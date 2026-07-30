from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.bond.user_identity import get_canonical_bond_user_id
from host_brain.tribe.social_brain_agent import SocialBrainAgent
from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.tribe_state import TribeStateRegistry


def _build_social_brain_agent(**kwargs) -> SocialBrainAgent:
    kwargs.setdefault("state_registry", TribeStateRegistry())
    return SocialBrainAgent(**kwargs)


class _FakeRift:
    severity = "low"
    repair_progress = 0.4

    def is_active(self) -> bool:
        return True


class _FakeBondState:
    current_stage = "FRIEND"
    event_count = 6
    last_event_ts_ms = 1700000000000
    intimacy_score = 0.62
    texture = SimpleNamespace(intimacy_temperature=0.71)
    rift = _FakeRift()

    def trend(self) -> str:
        return "up"

    def trend_intensity(self) -> str:
        return "mid"

    def capability_hint(self) -> str:
        return "can_deepen"

    def to_summary(self) -> dict[str, object]:
        return {
            "stage": "FRIEND",
            "trend": "up",
            "capability_hint": "can_deepen",
            "shared_memory_count": 2,
        }


def test_social_brain_resolve_bond_snapshot_with_live_engine(monkeypatch) -> None:
    class _Engine:
        def get_state(self, user_id: str):
            assert user_id == get_canonical_bond_user_id()
            return _FakeBondState()

    snapshot = _build_social_brain_agent(
        application_ports=TribeApplicationPorts(bond_stage_engine=lambda: _Engine()),
    ).resolve_bond_snapshot(person_id="user_1")

    assert snapshot["bond_stage"] == "FRIEND"
    assert snapshot["bond_trend"] == "up"
    assert snapshot["bond_trend_intensity"] == "mid"
    assert snapshot["capability_hint"] == "can_deepen"
    assert snapshot["intimacy_score"] == 0.62
    assert snapshot["last_event_ts_ms"] == 1700000000000
    assert snapshot["bond_summary"]["stage"] == "FRIEND"
    assert snapshot["bond_state_obj"] is not None


def test_social_brain_resolve_bond_snapshot_uses_fallback_summary() -> None:
    snapshot = _build_social_brain_agent().resolve_bond_snapshot(
        person_id="user_2",
        bond_summary={"stage": "FRIEND", "trend": "steady", "capability_hint": "short_chat"},
    )

    assert snapshot["bond_stage"] == "FRIEND"
    assert snapshot["bond_trend"] == "steady"
    assert snapshot["capability_hint"] == "short_chat"
    assert snapshot["bond_state_obj"] is None


def test_social_brain_resolve_bond_stage_uppercases_default() -> None:
    stage = _build_social_brain_agent().resolve_bond_stage(
        person_id=None,
        default="friend",
    )
    assert stage == "FRIEND"


def test_social_brain_resolve_scheduler_bond_summary(monkeypatch) -> None:
    class _Snapshot:
        stage = "CLOSE_FRIEND"
        trend = "up"
        capability_hint = "more_intimate"

    class _Engine:
        def get_bond_state(self, *, session_id: str):
            assert session_id == "default"
            return _Snapshot()

    summary = _build_social_brain_agent(
        application_ports=TribeApplicationPorts(bond_stage_engine=lambda: _Engine()),
    ).resolve_scheduler_bond_summary(session_id="default")

    assert summary == {
        "stage": "CLOSE_FRIEND",
        "trend": "up",
        "capability_hint": "more_intimate",
    }


def test_social_brain_resolve_stage_engine() -> None:
    sentinel = object()
    assert _build_social_brain_agent(
        application_ports=TribeApplicationPorts(bond_stage_engine=lambda: sentinel),
    ).resolve_stage_engine() is sentinel


def test_canonical_life_retires_legacy_social_brain_relationship_reads_and_writes(
    monkeypatch,
) -> None:
    """A bare user ID must not revive the old Bond or registry authority."""

    calls: list[str] = []
    registry = TribeStateRegistry()
    agent = _build_social_brain_agent(
        state_registry=registry,
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
            bond_stage_engine=lambda: calls.append("bond_stage_engine") or object(),
        ),
    )

    import host_brain.tribe.event_bus as event_bus

    monkeypatch.setattr(
        event_bus,
        "get_tribe_event_bus",
        lambda: calls.append("event_bus") or object(),
    )
    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_limbic_agent",
        lambda: calls.append("limbic") or object(),
    )

    snapshot = agent.resolve_bond_snapshot(person_id="same-user-in-two-workspaces")
    context = agent.get_bond_context(
        session_id="session-a",
        user_id="same-user-in-two-workspaces",
    )
    repair = agent.resolve_relationship_repair_state(
        person_id="same-user-in-two-workspaces",
    )

    assert snapshot["status"] == "RETIRED_CANONICAL_LIFE"
    assert snapshot["person_id"] == "same-user-in-two-workspaces"
    assert snapshot["bond_state_obj"] is None
    assert snapshot["bond_summary"] == {}
    assert context["status"] == "RETIRED_CANONICAL_LIFE"
    assert context["bond_vector"] == {}
    assert context["bond_stage"] == "friend"
    assert repair["status"] == "RETIRED_CANONICAL_LIFE"
    assert repair["rupture_load"] == 0.0
    assert agent.resolve_scheduler_bond_summary(session_id="session-a") == {}
    assert agent.resolve_stage_engine() is None
    assert agent.resolve_bond_vector(person_id="same-user-in-two-workspaces")["status"] == "RETIRED_CANONICAL_LIFE"
    assert agent.resolve_bond_projection(person_id="same-user-in-two-workspaces")["status"] == "RETIRED_CANONICAL_LIFE"
    assert agent.resolve_bond_narrative(user_id="same-user-in-two-workspaces")["status"] == "RETIRED_CANONICAL_LIFE"
    assert agent.resolve_shared_memory_index()["status"] == "RETIRED_CANONICAL_LIFE"
    assert agent.resolve_milestone_tracker(user_id="same-user-in-two-workspaces")["status"] == "RETIRED_CANONICAL_LIFE"

    agent.register_subscriptions()
    event = SimpleNamespace(
        payload={
            "user_id": "same-user-in-two-workspaces",
            "session_id": "session-a",
            "bond_stage": "SOULMATE",
            "bond_trend": "PROGRESS",
            "to_stage": "SOULMATE",
            "intensity": 1.0,
            "effort": 1.0,
            "severity": 1.0,
            "risk_level": "critical",
            "score": 1.0,
            "loneliness": 1.0,
        },
        ts_ms=1_900_000_000_000,
    )
    for callback in (
        agent._on_interaction_recorded,
        agent._on_bond_progress_snapshot,
        agent._on_bond_stage_changed,
        agent._on_bond_regress_snapshot,
        agent._on_task_success_bond,
        agent._on_loneliness_signal_bond,
        agent._on_immune_block_bond,
        agent._on_bond_rupture_signal,
        agent._on_bond_repair_action,
        agent._on_humor_landed_bond,
    ):
        asyncio.run(callback(event))

    assert calls == []
    assert registry.get_state("social_brain_agent") is None
    exported = agent.export_runtime_state()
    assert exported["status"] == "RETIRED_CANONICAL_LIFE"
    assert exported["bond_vector_trust"] == 0.0
