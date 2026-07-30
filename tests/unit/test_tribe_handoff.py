from __future__ import annotations

import asyncio
import importlib

import pytest

from host_brain.app.tribe_composition import (
    build_production_tribe_runtime_agent_ports,
    build_production_tribe_runtime_scope,
)
from host_brain.app.tribe_runtime_scope import TribeRuntimeScope
from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.event_bus import InMemoryTribeEventBus
from host_brain.tribe.handoff import TribeHandoffCoordinator
from host_brain.tribe.registry import HostTribeRegistry
from host_brain.tribe.runtime_context import require_active_tribe_agent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_app_tribe_runtime_scope_owns_one_explicit_registry_pair() -> None:
    state_registry = TribeStateRegistry()
    tribe_registry = HostTribeRegistry()
    event_bus = InMemoryTribeEventBus()

    runtime = TribeRuntimeScope(
        state_registry=state_registry,
        tribe_registry=tribe_registry,
        event_bus=event_bus,
        agent_providers=(("fixture", object),),
    )

    assert runtime.handoff_coordinator.state_registry is state_registry
    assert runtime.handoff_coordinator.tribe_registry is tribe_registry
    assert runtime.event_bus is event_bus
    with pytest.raises(TypeError, match="agent provider"):
        TribeRuntimeScope(
            state_registry=TribeStateRegistry(),
            tribe_registry=tribe_registry,
            event_bus=InMemoryTribeEventBus(),
            agent_providers=(("fixture", "not-callable"),),
        )


def test_production_tribe_scopes_own_distinct_shared_runtime_objects() -> None:
    first = build_production_tribe_runtime_scope()
    second = build_production_tribe_runtime_scope()

    assert first.state_registry is not second.state_registry
    assert first.tribe_registry is not second.tribe_registry
    assert first.event_bus is not second.event_bus

    resolved_catalogs: list[dict[str, object]] = []
    for runtime in (first, second):
        owner = object()
        runtime.bind_application_ports(
            owner=owner,
            ports=TribeApplicationPorts(),
        )
        activation = runtime.activate_execution_context(owner=owner)
        try:
            resolved_catalogs.append(
                {
                    name: runtime.require_agent(name)
                    for name, _provider in runtime.agent_providers
                }
            )
        finally:
            runtime.deactivate_execution_context(activation)
            assert runtime.unbind_application_ports(owner=owner)

    first_agents, second_agents = resolved_catalogs
    assert set(first_agents) == set(second_agents)
    assert all(
        first_agents[name] is not second_agents[name]
        for name in first_agents
    )


def test_production_tribe_scope_binds_compatibility_agents_to_owned_state() -> None:
    runtime = build_production_tribe_runtime_scope()
    owner = object()
    ports = TribeApplicationPorts()
    agent_ports = build_production_tribe_runtime_agent_ports(
        tribe_runtime=runtime,
    )
    with pytest.raises(RuntimeError, match="TRIBE_RUNTIME_SCOPE_NOT_BOUND"):
        agent_ports.require_thalamus_agent()
    compatibility_getters = {
        name: getattr(
            importlib.import_module(provider.__module__),
            f"get_{name}",
        )
        for name, provider in runtime.agent_providers
    }
    for getter in compatibility_getters.values():
        with pytest.raises(
            RuntimeError,
            match="TRIBE_RUNTIME_CONTEXT_NOT_BOUND",
        ):
            getter()

    runtime.bind_application_ports(owner=owner, ports=ports)
    with pytest.raises(
        RuntimeError,
        match="TRIBE_RUNTIME_EXECUTION_CONTEXT_NOT_ACTIVE",
    ):
        agent_ports.require_thalamus_agent()
    activation = runtime.activate_execution_context(owner=owner)
    try:
        agents = {
            name: require_active_tribe_agent(
                name,
                expected_type=provider,
            )
            for name, provider in runtime.agent_providers
        }
        assert set(agents) == set(compatibility_getters)
        assert all(
            compatibility_getters[name]() is agent
            for name, agent in agents.items()
        )
        assert agent_ports.require_thalamus_agent() is agents["thalamus_agent"]
        assert agent_ports.require_immune_agent() is agents["immune_agent"]
        assert not hasattr(agent_ports, "require_ans_agent")
        assert all(
            agent.application_ports is ports
            for agent in agents.values()
        )
        stateful_agents = [
            agent for agent in agents.values()
            if hasattr(agent, "state_registry")
        ]
        assert stateful_agents
        assert all(
            agent.state_registry is runtime.state_registry
            for agent in stateful_agents
        )
    finally:
        runtime.deactivate_execution_context(activation)
        assert runtime.unbind_application_ports(owner=owner)

    for getter in compatibility_getters.values():
        with pytest.raises(
            RuntimeError,
            match="TRIBE_RUNTIME_CONTEXT_NOT_BOUND",
        ):
            getter()
    with pytest.raises(RuntimeError, match="TRIBE_RUNTIME_SCOPE_NOT_BOUND"):
        agent_ports.require_immune_agent()


def test_cross_agent_getter_resolves_within_active_host_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from host_brain.tribe.interoception_agent import get_interoception_agent
    from host_brain.tribe.somatic_agent import SomaticAgent, get_somatic_agent

    runtime = build_production_tribe_runtime_scope()
    owner = object()
    runtime.bind_application_ports(
        owner=owner,
        ports=TribeApplicationPorts(),
    )
    activation = runtime.activate_execution_context(owner=owner)
    try:
        scoped_somatic = get_somatic_agent()
        scoped_interoception = get_interoception_agent()
        monkeypatch.setattr(
            SomaticAgent,
            "export_runtime_state",
            lambda self: {
                "fatigue_level": 0.91 if self is scoped_somatic else 0.0
            },
        )

        result = scoped_interoception.evaluate_profile(
            source="test-active-host-catalog",
            ts_ms=1_700_000_000_000,
        )

        assert result["somatic_fatigue"] == 0.91
    finally:
        runtime.deactivate_execution_context(activation)
        assert runtime.unbind_application_ports(owner=owner)

    with pytest.raises(
        RuntimeError,
        match="TRIBE_RUNTIME_CONTEXT_NOT_BOUND",
    ):
        get_somatic_agent()


@pytest.mark.asyncio
async def test_execution_local_contexts_isolate_two_concurrent_hosts() -> None:
    from host_brain.tribe.limbic_agent import get_limbic_agent
    from host_brain.tribe.tribe_state import get_global_registry

    runtimes = [
        build_production_tribe_runtime_scope(),
        build_production_tribe_runtime_scope(),
    ]
    owners = [object(), object()]
    ready = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    for runtime, owner in zip(runtimes, owners, strict=True):
        runtime.bind_application_ports(
            owner=owner,
            ports=TribeApplicationPorts(),
        )

    async def inspect_scope(index: int) -> tuple[object, object]:
        runtime = runtimes[index]
        activation = runtime.activate_execution_context(
            owner=owners[index],
        )
        try:
            ready[index].set()
            await release.wait()
            return get_limbic_agent(), get_global_registry()
        finally:
            runtime.deactivate_execution_context(activation)

    tasks = [
        asyncio.create_task(inspect_scope(index))
        for index in range(2)
    ]
    try:
        await asyncio.gather(*(event.wait() for event in ready))
        release.set()
        resolved = await asyncio.gather(*tasks)

        assert resolved[0][0] is not resolved[1][0]
        assert resolved[0][1] is runtimes[0].state_registry
        assert resolved[1][1] is runtimes[1].state_registry
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for runtime, owner in zip(runtimes, owners, strict=True):
            assert runtime.unbind_application_ports(owner=owner)


@pytest.mark.asyncio
async def test_inherited_context_fails_closed_after_owner_release() -> None:
    from host_brain.tribe.somatic_agent import get_somatic_agent

    runtime = build_production_tribe_runtime_scope()
    owner = object()
    runtime.bind_application_ports(
        owner=owner,
        ports=TribeApplicationPorts(),
    )
    activation = runtime.activate_execution_context(owner=owner)
    child_started = asyncio.Event()
    child_release = asyncio.Event()

    async def read_after_release() -> object:
        child_started.set()
        await child_release.wait()
        return get_somatic_agent()

    child = asyncio.create_task(read_after_release())
    await child_started.wait()
    with pytest.raises(
        RuntimeError,
        match="TRIBE_RUNTIME_EXECUTIONS_ACTIVE:1",
    ):
        runtime.unbind_application_ports(owner=owner)
    runtime.deactivate_execution_context(activation)
    assert runtime.unbind_application_ports(owner=owner)
    child_release.set()

    with pytest.raises(RuntimeError, match="TRIBE_RUNTIME_CONTEXT_STALE"):
        await child


def test_tribe_handoff_records_state_and_publishes_payload() -> None:
    published: list[dict] = []
    state_registry = TribeStateRegistry()
    coordinator = TribeHandoffCoordinator(
        state_registry=state_registry,
        tribe_registry=HostTribeRegistry(),
        event_publisher=lambda payload: published.append(dict(payload)),
    )

    payload = coordinator.record_dispatch_handoff(
        request_id="req_handoff_1",
        session_id="sess_handoff_1",
        route="skill",
        target_id="memory_recall",
        reason="keyword:search",
    )

    assert payload["source_family"] == "cortex"
    assert payload["target_family"] == "hippocampus"
    assert payload["route"] == "skill"
    assert payload["request_id"] == "req_handoff_1"

    source_state = state_registry.get_state("cortex")
    target_state = state_registry.get_state("hippocampus")
    assert source_state is not None
    assert target_state is not None
    assert source_state["state"]["handoff_count"] == 1
    assert target_state["state"]["handoff_count"] == 1
    assert source_state["state"]["last_handoff_outbound"]["request_id"] == "req_handoff_1"
    assert target_state["state"]["last_handoff_inbound"]["target_family"] == "hippocampus"

    assert len(published) == 1
    assert published[0]["request_id"] == "req_handoff_1"


def test_tribe_handoff_snapshot_contains_families_agents_and_states() -> None:
    state_registry = TribeStateRegistry()
    coordinator = TribeHandoffCoordinator(
        state_registry=state_registry,
        tribe_registry=HostTribeRegistry(),
        event_publisher=lambda _payload: None,
    )

    coordinator.record_dispatch_handoff(
        request_id="req_handoff_2",
        session_id="sess_handoff_2",
        route="plan",
        target_id=None,
        reason="llm_plan:plan",
    )

    snapshot = coordinator.snapshot()

    family_ids = {item["family_id"] for item in snapshot["families"]}
    agent_ids = {item["agent_id"] for item in snapshot["agents"]}
    assert "prefrontal" in family_ids
    assert "executive_router" in agent_ids
    assert "cortex" in snapshot["states"]
    assert "prefrontal" in snapshot["states"]
    assert isinstance(snapshot["timestamp"], int)
