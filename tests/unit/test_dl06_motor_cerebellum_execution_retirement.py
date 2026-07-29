from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.cerebellum_agent import CerebellumAgent
from host_brain.tribe.motor_cortex_agent import MotorCortexAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _Bus:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, object]] = []

    def subscribe(self, event_type: str, handler: object) -> None:
        self.subscriptions.append((event_type, handler))


def _canonical_ports() -> TribeApplicationPorts:
    return TribeApplicationPorts(
        affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
    )


def test_canonical_motor_cortex_retires_eventbus_plan_feedback_and_style(monkeypatch) -> None:
    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical MotorCortex must not reach legacy execution or affect organs")

    registry = TribeStateRegistry()
    motor = MotorCortexAgent(
        application_ports=_canonical_ports(),
        state_registry=registry,
    )
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.motor_cortex_agent.emit_tribe_event", _forbidden)
    monkeypatch.setattr("host_brain.tribe.motor_cortex_agent.get_limbic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.motor_cortex_agent.get_endocrine_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.motor_cortex_agent.get_pain_reward_agent", _forbidden)

    motor.register_subscriptions()
    planned = motor.plan_action(action="legacy_edge_action")
    feedback = motor.receive_execution_feedback(
        plan_id="legacy-plan",
        ok=False,
        latency_ms=500,
        reason="EDGE_TIMEOUT",
    )
    dispatched = motor.dispatch_action(plan={"plan_id": "legacy-plan"})
    style = motor.plan_speak_style(emotion_tag="SAD", valence=-0.5, arousal=0.8)
    asyncio.run(motor._on_execution_feedback(SimpleNamespace(payload={"plan_id": "legacy-plan"})))
    asyncio.run(motor._on_retry_requested(SimpleNamespace(payload={"plan_id": "legacy-plan"})))

    assert bus.subscriptions == []
    assert planned["reason_code"] == "LEGACY_MOTOR_CORTEX_EXECUTION_RETIRED"
    assert feedback["reason_code"] == "LEGACY_MOTOR_CORTEX_EXECUTION_RETIRED"
    assert dispatched["reason_code"] == "LEGACY_MOTOR_CORTEX_EXECUTION_RETIRED"
    assert style["reason_code"] == "LEGACY_MOTOR_CORTEX_EXECUTION_RETIRED"
    assert registry.get_state("motor_cortex_agent") is None
    assert motor.export_runtime_state()["last_dispatch_reason"] == "LEGACY_MOTOR_CORTEX_EXECUTION_RETIRED"


def test_canonical_cerebellum_keeps_no_local_retry_affect_or_memory_writer(monkeypatch) -> None:
    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical Cerebellum must not revive a legacy correction side effect")

    registry = TribeStateRegistry()
    cerebellum = CerebellumAgent(
        application_ports=_canonical_ports(),
        state_registry=registry,
    )
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.cerebellum_agent.emit_tribe_event", _forbidden)
    monkeypatch.setattr("host_brain.tribe.cerebellum_agent.get_limbic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.cerebellum_agent.get_endocrine_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.cerebellum_agent.get_pain_reward_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.cerebellum_agent.get_interoception_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.cerebellum_agent.get_default_mode_agent", _forbidden)

    cerebellum.register_subscriptions()
    decision = cerebellum.evaluate_correction(
        execution_feedback={
            "plan_id": "legacy-plan",
            "accepted": False,
            "dispatched": False,
            "latency_ms": 750,
            "reason": "EDGE_TIMEOUT",
        },
    )
    asyncio.run(cerebellum._on_motor_action_dispatched(SimpleNamespace(payload={})))
    asyncio.run(cerebellum._on_platform_disconnected(SimpleNamespace(payload={})))

    assert bus.subscriptions == []
    assert decision["reason"] == "LEGACY_CEREBELLUM_CORRECTION_RETIRED"
    assert decision["strategy"] == "GOVERNED_TASKGRAPH_REQUIRED"
    assert cerebellum.generate_correction_narrative(strategy="RETRY_ONCE") is None
    assert cerebellum.analyze_failure_pattern(consecutive_failures=3) is None
    assert registry.get_state("cerebellum_agent") is None
    assert cerebellum.export_runtime_state()["reason_code"] == "LEGACY_CEREBELLUM_CORRECTION_RETIRED"
