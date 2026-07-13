from __future__ import annotations

import logging

import pytest

from host_brain.app import http_hooks
from host_brain.app.lifecycle_runtime_composition import (
    build_lifecycle_runtime_application_ports,
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

    with pytest.raises(RuntimeError, match="lifespan body failed"):
        async with http_hooks.runtime_lifespan(
            get_ledger=object,
            logger=logging.getLogger("test.aw01.runtime-lifespan"),
            lifecycle_ports=build_lifecycle_runtime_application_ports(),
        ):
            events.append("body")
            raise RuntimeError("lifespan body failed")

    assert events == ["body", "shutdown"]


@pytest.mark.asyncio
async def test_shutdown_helper_stops_resources_and_critical_paths_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from host_brain.app import internal_tribe_routes
    from host_brain.app.observability_runtime import service_health_heartbeat
    from host_brain.tribe import cortex_agent

    events: list[str] = []

    class SyncStop:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        def stop(self) -> None:
            events.append(self.name)
            if self.fail:
                raise RuntimeError(f"{self.name} failed")

    class Cortex:
        def shutdown_bond_update_queue(self, *, logger, timeout_s: float) -> None:
            assert logger is test_logger
            assert timeout_s == 3.0
            events.append("bond-queue")

    class Bus:
        async def stop(self) -> None:
            events.append("tribe-bus")

    async def stop_heartbeat() -> None:
        events.append("heartbeat")

    test_logger = logging.getLogger("test.aw01.runtime-shutdown")
    monkeypatch.setattr(cortex_agent, "get_cortex_agent", Cortex)
    monkeypatch.setattr(
        service_health_heartbeat,
        "stop_heartbeat",
        stop_heartbeat,
    )
    monkeypatch.setattr(internal_tribe_routes, "get_tribe_event_bus", Bus)

    await http_hooks._shutdown_runtime(
        activity_monitor=SyncStop("activity", fail=True),
        resource_manager=SyncStop("resource"),
        logger=test_logger,
    )

    assert events == [
        "activity",
        "resource",
        "bond-queue",
        "heartbeat",
        "tribe-bus",
    ]
