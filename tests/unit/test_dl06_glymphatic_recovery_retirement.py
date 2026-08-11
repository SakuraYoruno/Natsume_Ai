from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.glymphatic_agent import GlymphaticAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_canonical_glymphatic_retires_global_recovery_and_memory_fanout(
    monkeypatch,
) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical Glymphatic must not recreate a global recovery writer")

    registry = TribeStateRegistry()
    agent = GlymphaticAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.glymphatic_agent.emit_tribe_event", _forbidden)
    monkeypatch.setattr("host_brain.tribe.glymphatic_agent.get_hippocampus_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.glymphatic_agent.get_default_mode_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.glymphatic_agent.get_limbic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.glymphatic_agent.get_interoception_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.glymphatic_agent.get_pain_reward_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.glymphatic_agent.get_ans_agent", _forbidden)

    agent.register_subscriptions()
    cycle = agent.run_recovery_cycle(source="canonical-test", force=True, now_ts_ms=123)
    tick = agent.tick_recovery_window(source="canonical-test")
    callback_results = [
        asyncio.run(agent._on_circadian_phase_updated(SimpleNamespace(ts_ms=124))),
        asyncio.run(agent._on_conversation_ended(SimpleNamespace(ts_ms=125))),
        asyncio.run(agent._on_default_mode_ticked(SimpleNamespace(ts_ms=126))),
    ]
    exported = agent.export_runtime_state()

    assert bus.subscriptions == []
    assert agent.generate_consolidation_summary(episode_summary="private") is None
    assert registry.get_state("glymphatic_agent") is None
    assert {
        cycle["reason_code"],
        tick["reason_code"],
        exported["reason_code"],
        *(item["reason_code"] for item in callback_results if item is not None),
    } == {"LEGACY_GLYMPHATIC_RECOVERY_WRITER_RETIRED"}
    assert cycle["executed"] is False
    assert tick["ticked"] is False
    assert exported["projection"] == "UNAVAILABLE"
