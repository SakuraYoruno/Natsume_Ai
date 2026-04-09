from __future__ import annotations

import asyncio

from host_brain.app.state_arbiter_queries import _resolve_state_arbiter_bond_fact


def _run(coro):
    return asyncio.run(coro)


def test_state_arbiter_bond_fact_degraded_when_stage_engine_missing(monkeypatch) -> None:
    class _SocialBrain:
        def resolve_stage_engine(self):
            return None

    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        lambda: _SocialBrain(),
    )

    summary, evidence = _run(
        _resolve_state_arbiter_bond_fact(
            session_id="sess_1",
            request_id="req_1",
        )
    )

    assert evidence is None
    assert summary["status"] == "SKIPPED"
    assert summary["reason_code"] == "BOND_ENGINE_NOT_AVAILABLE"


def test_state_arbiter_bond_fact_uses_social_brain_engine(monkeypatch) -> None:
    class _BondState:
        stage = "ALLY"
        score = 0.72
        label = "trusting"
        interaction_count = 9

    class _Engine:
        def get_bond_state(self, *, session_id: str):
            assert session_id == "sess_2"
            return _BondState()

    class _SocialBrain:
        def resolve_stage_engine(self):
            return _Engine()

    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        lambda: _SocialBrain(),
    )

    summary, evidence = _run(
        _resolve_state_arbiter_bond_fact(
            session_id="sess_2",
            request_id="req_2",
        )
    )

    assert summary["status"] == "APPLIED"
    assert summary["reason_code"] is None
    assert summary["bond_stage"] == "ALLY"
    assert evidence is not None
    assert evidence["source"] == "state_arbiter_bond"
    assert evidence["metadata"]["source"] == "social_brain_agent"
