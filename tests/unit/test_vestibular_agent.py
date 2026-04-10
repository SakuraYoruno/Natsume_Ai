from __future__ import annotations

import asyncio

from host_brain.core.ids import now_ms
from host_brain.tribe.tribe_state import TribeStateRegistry
from host_brain.tribe.vestibular_agent import VestibularAgent


class _Event:
    def __init__(self, event_type: str, payload: dict | None = None, ts_ms: int = 0) -> None:
        self.event_type = event_type
        self.payload = payload or {}
        self.ts_ms = ts_ms


def _run(coro):
    return asyncio.run(coro)


def test_vestibular_agent_stability_jitter() -> None:
    agent = VestibularAgent(state_registry=TribeStateRegistry())
    base = now_ms()
    _run(agent._on_mode_transition(_Event("MODE_TRANSITION", {"from_mode": "chat", "to_mode": "proactive"}, ts_ms=base)))
    _run(agent._on_platform_signal(_Event("PLATFORM_DISCONNECTED", {"platform": "discord"}, ts_ms=base + 300)))
    _run(agent._on_initiative_decision(_Event("INITIATIVE_DECISION_MADE", {"decision": "DEFER"}, ts_ms=base + 600)))
    exported = agent.export_runtime_state()

    assert exported["jitter_index"] > 0.0
    assert exported["stability_state"] in {"stable", "unstable", "critical"}
    assert exported["mode_transition_count_10m"] >= 1
