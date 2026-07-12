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

    def stop_tribe_periodic_save() -> None:
        from host_brain.tribe.tribe_state import (  # noqa: PLC0415
            get_global_registry,
        )

        get_global_registry().stop_periodic_save()

    async def stop_task_scheduler() -> None:
        from host_brain.app.automation.task_scheduler import (  # noqa: PLC0415
            get_scheduler,
        )

        await get_scheduler().stop()

    async def stop_life_continuity() -> None:
        from host_brain.runtime.life_continuity import (  # noqa: PLC0415
            stop_life_continuity_loop,
        )

        await stop_life_continuity_loop()

    def stop_immune_monitor(logger: Any) -> None:
        from host_brain.app.internal_agent_routes import (  # noqa: PLC0415
            stop_immune_auto_dispatch_monitor,
        )

        stop_immune_auto_dispatch_monitor(logger=logger)

    return LegacyRuntimeShutdownDependencies(
        stop_bond_queue=stop_bond_queue,
        stop_heartbeat=stop_heartbeat,
        get_tribe_event_bus=get_tribe_event_bus,
        stop_tribe_periodic_save=stop_tribe_periodic_save,
        stop_task_scheduler=stop_task_scheduler,
        stop_life_continuity=stop_life_continuity,
        stop_immune_monitor=stop_immune_monitor,
    )


async def shutdown_runtime(
    *,
    activity_monitor: Any | None,
    resource_manager: Any | None,
    life_continuity_started: bool,
    logger: Any,
) -> None:
    await shutdown_legacy_runtime(
        activity_monitor=activity_monitor,
        resource_manager=resource_manager,
        life_continuity_started=life_continuity_started,
        logger=logger,
        dependencies=_dependencies(),
    )


__all__ = ["shutdown_runtime"]
