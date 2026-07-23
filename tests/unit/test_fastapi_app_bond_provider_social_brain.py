from __future__ import annotations

import inspect

import host_brain.app.bond_routes as bond_routes
import host_brain.app.asgi as asgi
from host_brain.app.host_lifecycle_composition import HostLifecycleApplicationPorts
from host_brain.composition.host_application import HostApplicationPorts


def test_asgi_bond_provider_wired_to_canonical_composition() -> None:
    assert not hasattr(bond_routes, "_engine_provider")
    source = inspect.getsource(asgi.create_app)
    assert "bond_engine_provider=bond_engine_provider" in source
    assert "process_services" not in source


def test_asgi_bond_provider_uses_the_injected_lifecycle_port() -> None:
    sentinel = object()
    provider = lambda: sentinel
    service_provider = lambda: object()
    lifecycle = HostLifecycleApplicationPorts(
        memory_guard=service_provider,
        state_arbiter=service_provider,
        distillation_engine=service_provider,
        semantic_recaller=service_provider,
        episodic_store=service_provider,
        skill_registry=service_provider,
        tool_catalog=service_provider,
        capability_broker=service_provider,
        model_attempt_service=service_provider,
        mode_transition_service_factory=lambda **_kwargs: object(),
        activity_monitor_factory=lambda **_kwargs: object(),
        resource_manager_factory=lambda **_kwargs: object(),
        ledger=service_provider,
        life_snapshot_reader=service_provider,
        intent_engine=service_provider,
        prompt_registry=service_provider,
        bond_stage_engine=provider,
    )
    ports = HostApplicationPorts(
        lifecycle=lifecycle,
        memory_worth_evaluator=service_provider,
        security_immune_agent=service_provider,
        token_service=service_provider,
        bond_dynamics_writer=service_provider,
        capability_forge_service=service_provider,
        capability_gap_telemetry=service_provider,
        capability_scorecard_provider=service_provider,
        memory_service_container_setter=lambda _value: None,
    )

    assert ports.lifecycle.require_provider("bond_stage_engine") is provider
    assert ports.lifecycle.require_provider("bond_stage_engine")() is sentinel
