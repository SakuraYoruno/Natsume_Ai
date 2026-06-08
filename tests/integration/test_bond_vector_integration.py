from __future__ import annotations

import asyncio

from host_brain.tribe.social_brain_agent import SocialBrainAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _Event:
    def __init__(self, payload: dict, ts_ms: int = 0) -> None:
        self.payload = payload
        self.ts_ms = ts_ms


def _run(coro):
    return asyncio.run(coro)


def test_social_brain_bond_vector_enters_vital_signs() -> None:
    registry = TribeStateRegistry()
    agent = SocialBrainAgent(state_registry=registry)

    _run(
        agent._on_bond_progress_snapshot(
            _Event(
                payload={
                    "user_id": "u_1",
                    "bond_stage": "FRIEND",
                    "bond_trend": "PROGRESS",
                    "intensity": 0.66,
                },
                ts_ms=2000,
            )
        )
    )
    ctx = agent.get_bond_context(session_id="sess_1", user_id="u_1")
    vital_signs = registry.aggregate_vital_signs()

    assert ctx["bond_stage"] in {"friend", "familiar", "close_friend", "close_friend", "intimate", "soulmate"}
    assert "bond_vector" in ctx
    assert float(ctx["bond_vector"]["trust"]) >= 0.0
    assert isinstance(vital_signs.get("bond_vector"), dict)
    assert "trust" in vital_signs["bond_vector"]
