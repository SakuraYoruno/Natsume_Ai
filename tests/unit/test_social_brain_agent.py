from __future__ import annotations

import inspect

from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.social_brain_agent import SocialBrainAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def _build_social_brain_agent(**kwargs) -> SocialBrainAgent:
    kwargs.setdefault("state_registry", TribeStateRegistry())
    return SocialBrainAgent(**kwargs)


def test_social_brain_legacy_snapshot_is_caller_owned_read_only_projection() -> None:
    snapshot = _build_social_brain_agent().resolve_bond_snapshot(
        person_id="user_2",
        bond_summary={
            "stage": "FRIEND",
            "trend": "steady",
            "capability_hint": "short_chat",
        },
    )

    assert snapshot["bond_stage"] == "FRIEND"
    assert snapshot["bond_trend"] == "steady"
    assert snapshot["capability_hint"] == "short_chat"
    assert snapshot["bond_state_obj"] is None
    assert snapshot["intimacy_score"] == 0.0
    assert snapshot["last_event_ts_ms"] == 0


def test_social_brain_resolve_bond_stage_uppercases_default() -> None:
    stage = _build_social_brain_agent().resolve_bond_stage(
        person_id=None,
        default="friend",
    )
    assert stage == "FRIEND"


def test_social_brain_scheduler_does_not_reopen_legacy_bond_engine() -> None:
    summary = _build_social_brain_agent(
        application_ports=TribeApplicationPorts(),
    ).resolve_scheduler_bond_summary(session_id="default")

    assert summary == {}


def test_social_brain_never_exposes_mutable_stage_engine() -> None:
    agent = _build_social_brain_agent(
        application_ports=TribeApplicationPorts(),
    )

    assert agent.resolve_stage_engine() is None
    source = inspect.getsource(SocialBrainAgent.resolve_bond_snapshot)
    assert 'require("bond_stage_engine")' not in source
    assert ".get_state(" not in source
    assert ".get_bond_state(" not in source
