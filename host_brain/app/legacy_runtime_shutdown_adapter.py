"""Application adapter for legacy runtime shutdown capabilities."""

from __future__ import annotations

from typing import Any

from host_brain.composition.legacy_runtime_shutdown import (
    LegacyRuntimeShutdownDependencies,
    shutdown_legacy_runtime,
)


def _dependencies() -> LegacyRuntimeShutdownDependencies:
    def stop_bond_queue(logger: Any) -> None:
        from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

        get_cortex_agent().shutdown_bond_update_queue(logger=logger, timeout_s=3.0)

    async def stop_heartbeat() -> None:
        from host_brain.observability.service_health_heartbeat import (  # noqa: PLC0415
            stop_heartbeat as stop,
        )

        await stop()

    def get_tribe_event_bus() -> Any | None:
        from host_brain.app.internal_tribe_routes import (  # noqa: PLC0415
            get_tribe_event_bus as get_bus,
        )

        return get_bus()

    def no_tribe_periodic_owner() -> None:
        # Kept only to satisfy the frozen legacy shutdown composition contract.
        # Production no longer starts a Tribe-owned periodic thread.
        return None

    async def no_private_life_loop() -> None:
        # Kept only to satisfy the frozen legacy shutdown composition contract.
        # SchedulerClock is the only production Life periodic producer.
        return None

    return LegacyRuntimeShutdownDependencies(
        stop_bond_queue=stop_bond_queue,
        stop_heartbeat=stop_heartbeat,
        get_tribe_event_bus=get_tribe_event_bus,
        stop_tribe_periodic_save=no_tribe_periodic_owner,
        stop_life_continuity=no_private_life_loop,
    )


async def shutdown_runtime(
    *,
    activity_monitor: Any | None,
    resource_manager: Any | None,
    logger: Any,
) -> None:
    await shutdown_legacy_runtime(
        activity_monitor=activity_monitor,
        resource_manager=resource_manager,
        life_continuity_started=False,
        logger=logger,
        dependencies=_dependencies(),
    )


__all__ = ["shutdown_runtime"]
