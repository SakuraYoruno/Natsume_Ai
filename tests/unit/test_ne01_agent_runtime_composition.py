from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.app import agent_runtime_composition
from host_brain.app.agent_runtime_composition import (
    AgentRuntimeApplicationScope,
    AgentRuntimeCompositionError,
    build_agent_runtime_application_ports,
)
from host_brain.app.host_lifecycle_composition import (
    HostLifecycleApplicationPorts,
    HostLifecycleScopeError,
)


def _host_services(**overrides: object) -> HostLifecycleApplicationPorts:
    provider = lambda: object()
    factory = lambda **_kwargs: object()
    values: dict[str, object] = {
        "memory_guard": provider,
        "state_arbiter": provider,
        "distillation_engine": provider,
        "semantic_recaller": provider,
        "episodic_store": provider,
        "skill_registry": provider,
        "tool_catalog": provider,
        "capability_broker": provider,
        "model_attempt_service": provider,
        "mode_transition_service_factory": factory,
        "activity_monitor_factory": factory,
        "resource_manager_factory": factory,
        "ledger": provider,
        "life_memory_store": provider,
        "life_memory_service": provider,
        "conversation_history": provider,
        "prompt_registry": provider,
        "bond_update_queue": provider,
    }
    values.update(overrides)
    return HostLifecycleApplicationPorts(**values)  # type: ignore[arg-type]


def test_agent_runtime_scope_owns_one_lifespan_bundle_and_injects_life_authority(
    monkeypatch,
) -> None:
    ledger = object()
    life_service = object()
    life_reader = object()
    captured: dict[str, object] = {}
    host_services = _host_services(ledger=lambda: ledger)
    scope = AgentRuntimeApplicationScope(host_services=host_services)

    def fake_record_user_turn(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        return {"status": "OK"}

    monkeypatch.setattr(
        agent_runtime_composition,
        "record_user_turn",
        fake_record_user_turn,
    )

    assert scope.provider() is None
    ports = scope.initialize(
        life_authority_provider=lambda: (life_service, life_reader),
    )
    assert scope.provider() is ports
    assert ports.record_user_turn(
        request_id="request-ne01-agent-runtime-ports",
        session_id="session-ne01-agent-runtime-ports",
        person_id="person-ne01-agent-runtime-ports",
        content="accepted input",
        ts_ms=1,
        lang="zh",
        metadata={},
    ) == {"status": "OK"}
    assert captured["ledger"] is ledger
    assert captured["life_service"] is life_service
    assert captured["life_reader"] is life_reader

    with pytest.raises(
        AgentRuntimeCompositionError,
        match="HOST_AGENT_RUNTIME_PORTS_ALREADY_INITIALIZED",
    ):
        scope.initialize(life_authority_provider=lambda: (life_service, life_reader))

    scope.clear()
    assert scope.provider() is None
    assert scope.initialize(
        life_authority_provider=lambda: (life_service, life_reader),
    ) is not ports


def test_agent_runtime_composition_fails_closed_for_missing_host_or_life_authority() -> None:
    with pytest.raises(
        HostLifecycleScopeError,
        match="HOST_SERVICE_PROVIDER_NOT_INJECTED:life_memory_store",
    ):
        build_agent_runtime_application_ports(
            host_services=_host_services(life_memory_store=None),
            life_authority_provider=lambda: (object(), object()),
        )

    ports = build_agent_runtime_application_ports(
        host_services=_host_services(),
        life_authority_provider=lambda: (None, None),
    )
    with pytest.raises(
        AgentRuntimeCompositionError,
        match="HOST_AGENT_RUNTIME_LIFE_AUTHORITY_UNAVAILABLE",
    ):
        ports.record_user_turn(
            request_id="request-ne01-missing-life",
            session_id="session-ne01-missing-life",
            person_id="person-ne01-missing-life",
            content="input",
            ts_ms=1,
            lang="zh",
            metadata={},
        )


def test_production_adapters_do_not_import_the_retired_global_port_bundle() -> None:
    root = Path(__file__).resolve().parents[2]
    production_files = (
        "host_brain/app/cortex_orchestrator.py",
        "host_brain/app/input_runtime_composition.py",
        "host_brain/app/life_projection_handlers.py",
        "host_brain/app/acceptance_effect_handlers.py",
        "host_brain/app/sub_agent_protocol.py",
        "host_brain/app/surface_reply_postprocessor.py",
    )
    for relative in production_files:
        source = (root / relative).read_text(encoding="utf-8-sig")
        assert "host_brain.app.agent_runtime_shared" not in source, relative
        assert "import AGENT_RUNTIME_PORTS" not in source, relative
        assert ".AGENT_RUNTIME_PORTS" not in source, relative

    retired_source = (root / "host_brain/app/agent_runtime_shared.py").read_text(
        encoding="utf-8-sig"
    )
    assert "AGENT_RUNTIME_PORTS" not in retired_source
