from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.ans_agent import ANSAgent
from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_canonical_ans_retires_global_affect_and_relationship_event_fanout(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical ANS must not invoke a legacy affect dependency")

    registry = TribeStateRegistry()
    agent = ANSAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.ans_agent.emit_tribe_event", _forbidden)
    monkeypatch.setattr("host_brain.tribe.ans_agent.get_interoception_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.ans_agent.get_limbic_agent", _forbidden)

    agent.register_subscriptions()
    emitted = [
        agent.emit_from_thread(event_type, {"source": "canonical-test"})
        for event_type in (
            "INNER_WORLD_TICKED",
            "BODY_SENSE_UPDATED",
            "LONELINESS_SIGNAL",
            "TASK_SUCCESS",
            "HUMOR_LANDED",
            "BOND_PROGRESS",
            "BOND_REGRESS",
        )
    ]
    explicit = [
        agent.emit_inner_world_ticked(),
        agent.emit_body_sense_updated(),
        agent.emit_loneliness_signal(loneliness=0.8),
        agent.emit_task_success(task_type="test", intensity=0.8),
        agent.emit_humor_landed(humor_type="test", intensity=0.8),
        agent.emit_bond_progress(delta=0.2),
        agent.emit_bond_regress(delta=0.2),
        agent.adjust_rhythm_for_high_interrupt(interrupt_count=6),
        agent.set_recovery_mode(True),
        asyncio.run(agent._async_emit("INNER_WORLD_TICKED", {"source": "test"})),
    ]
    callbacks = [
        asyncio.run(getattr(agent, name)(SimpleNamespace(payload={}, ts_ms=123)))
        for name in (
            "_on_glymphatic_cycle",
            "_on_bond_stage_changed",
            "_on_voice_pipeline_started",
            "_on_voice_pipeline_ended",
            "_on_channel_message_received",
        )
    ]
    exported = agent.export_runtime_state()

    assert bus.subscriptions == []
    assert {
        result["reason_code"]
        for result in [*emitted, *explicit, *callbacks]
        if result is not None
    } == {"LEGACY_ANS_AFFECT_EVENT_RETIRED"}
    assert exported["status"] == "RETIRED_CANONICAL_LIFE"
    assert exported["authority"] == "CANONICAL_LIFE_JOURNAL"
    assert exported["proactive_multiplier"] == 0.0
    assert registry.get_state("ans_agent") is None
