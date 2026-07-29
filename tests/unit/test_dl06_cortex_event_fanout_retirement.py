from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.cortex_agent import CortexAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_canonical_cortex_retires_global_conversation_event_fanout(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical Cortex must not dispatch legacy side effects")

    bus = _Bus()
    registry = TribeStateRegistry()
    agent = CortexAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    monkeypatch.setattr(
        "host_brain.tribe.event_bus.get_tribe_event_bus",
        lambda: bus,
    )
    monkeypatch.setattr(
        "host_brain.tribe.cortex_agent.emit_tribe_event",
        _forbidden,
    )
    monkeypatch.setattr(
        "host_brain.tribe.cortex_agent.get_pain_reward_agent",
        _forbidden,
    )

    agent.register_subscriptions()
    started = agent.emit_conversation_started(
        user_id="person-dl06",
        session_id="session-dl06",
        request_id="request-dl06",
    )
    ended = agent.emit_conversation_ended(
        user_id="person-dl06",
        session_id="session-dl06",
        request_id="request-dl06",
        emotion_tag="SAD",
        emotion_intensity=0.9,
    )
    event = SimpleNamespace(
        payload={
            "emotion_tag": "SAD",
            "bond_stage": "SOULMATE",
            "loneliness": 0.9,
            "platform": "wechat",
        },
        ts_ms=123,
    )
    callback_results = [
        asyncio.run(getattr(agent, name)(event))
        for name in (
            "_on_silence_willingness_changed",
            "_on_emotion_state_changed",
            "_on_bond_stage_changed",
            "_on_bond_progress_cortex",
            "_on_bond_regress_cortex",
            "_on_immune_block",
            "_on_loneliness_signal",
            "_on_task_success_cortex",
            "_on_circadian_phase_updated",
            "_on_proprioception_updated",
            "_on_initiative_decision_made",
            "_on_platform_connected",
            "_on_platform_disconnected",
        )
    ]

    assert bus.subscriptions == []
    assert started["reason_code"] == "LEGACY_CORTEX_EVENT_FANOUT_RETIRED"
    assert ended["reason_code"] == "LEGACY_CORTEX_EVENT_FANOUT_RETIRED"
    assert registry.get_state("cortex_agent") is None
    assert {
        result["reason_code"] for result in callback_results
    } == {"LEGACY_CORTEX_EVENT_FANOUT_RETIRED"}
