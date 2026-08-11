from __future__ import annotations

import logging

import pytest

from host_brain.app import http_hooks
from host_brain.app.lifecycle_runtime_composition import (
    build_lifecycle_runtime_application_ports,
)
from host_brain.app.host_lifecycle_composition import (
    HostLifecycleApplicationPorts,
    build_host_lifecycle_container,
)
from host_brain.app.tribe_composition import TribeRuntimeAgentPorts
from host_brain.tribe.application_ports import TribeApplicationPorts



def _host_lifecycle_ports() -> HostLifecycleApplicationPorts:
    provider = lambda: object()
    factory = lambda **_kwargs: object()
    return HostLifecycleApplicationPorts(
        memory_guard=provider,
        state_arbiter=provider,
        distillation_engine=provider,
        semantic_recaller=provider,
        episodic_store=provider,
        skill_registry=provider,
        tool_catalog=provider,
        capability_broker=provider,
        model_attempt_service=provider,
        mode_transition_service_factory=factory,
        activity_monitor_factory=factory,
        resource_manager_factory=factory,
    )


@pytest.mark.asyncio
async def test_runtime_lifespan_body_error_still_enters_shutdown_finally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def stop_startup_after_initialization(*_args, **_kwargs) -> bool:
        raise RuntimeError("deliberate startup short circuit")

    async def record_shutdown(**_kwargs) -> None:
        events.append("shutdown")

    monkeypatch.setattr(
        http_hooks,
        "get_core_settings",
        stop_startup_after_initialization,
    )
    monkeypatch.setattr(http_hooks, "_shutdown_runtime", record_shutdown)

    class _TribeRuntime:
        event_bus = object()

        def bind_application_ports(self, **_kwargs) -> None:
            events.append("tribe-bind")

        def activate_execution_context(self, **_kwargs) -> object:
            events.append("tribe-context-activate")
            return object()

        def deactivate_execution_context(self, _activation: object) -> bool:
            events.append("tribe-context-deactivate")
            return True

        def unbind_application_ports(self, **_kwargs) -> bool:
            events.append("tribe-unbind")
            return True

        def register_subscriptions(self, **_kwargs) -> list[str]:
            return []

    host_lifecycle = build_host_lifecycle_container(
        initial_sys_mode="normal",
        ports=_host_lifecycle_ports(),
    )

    with pytest.raises(RuntimeError, match="lifespan body failed"):
        async with http_hooks.runtime_lifespan(
            get_ledger=object,
            logger=logging.getLogger("test.aw01.runtime-lifespan"),
            lifecycle_ports=build_lifecycle_runtime_application_ports(
                activity_monitor_provider=(
                    host_lifecycle.get_activity_monitor
                ),
            ),
            host_lifecycle=host_lifecycle,
            tribe_application_ports=TribeApplicationPorts(),
            tribe_runtime_agent_ports=TribeRuntimeAgentPorts(
                get_thalamus_agent=object,
            ),
            tribe_runtime=_TribeRuntime(),
            begin_authority_runtime=lambda: events.append(
                "authority-begin"
            ),
            close_authority_runtime=lambda: events.append(
                "authority-close"
            ),
            begin_governance_runtime=lambda: events.append(
                "governance-begin"
            ),
            close_governance_runtime=lambda: events.append(
                "governance-close"
            ),
        ):
            events.append("body")
            raise RuntimeError("lifespan body failed")

    assert events == [
        "authority-begin",
        "governance-begin",
        "tribe-bind",
        "tribe-context-activate",
        "body",
        "shutdown",
        "governance-close",
        "authority-close",
        "tribe-context-deactivate",
        "tribe-unbind",
    ]
    assert host_lifecycle.is_open is False


@pytest.mark.asyncio
async def test_shutdown_helper_stops_resources_and_critical_paths_in_order(
) -> None:
    events: list[str] = []

    class SyncStop:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        def stop(self) -> None:
            events.append(self.name)
            if self.fail:
                raise RuntimeError(f"{self.name} failed")

    class Bus:
        async def stop(self) -> None:
            events.append("tribe-bus")

    test_logger = logging.getLogger("test.aw01.runtime-shutdown")
    await http_hooks._shutdown_runtime(
        activity_monitor=SyncStop("activity", fail=True),
        resource_manager=SyncStop("resource"),
        tribe_event_bus=Bus(),
        logger=test_logger,
    )

    assert events == [
        "activity",
        "resource",
        "tribe-bus",
    ]
