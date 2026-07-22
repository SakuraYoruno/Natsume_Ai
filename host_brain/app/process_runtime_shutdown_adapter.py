"""Application adapter for active process-runtime shutdown capabilities."""

from __future__ import annotations

from typing import Any

from host_brain.composition.process_runtime_shutdown import (
    ProcessRuntimeShutdownDependencies,
    shutdown_process_runtime,
)


def _dependencies() -> ProcessRuntimeShutdownDependencies:
    def stop_bond_queue(logger: Any) -> None:
        from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

        get_cortex_agent().shutdown_bond_update_queue(logger=logger, timeout_s=3.0)

    def get_tribe_event_bus() -> Any | None:
        from host_brain.app.internal_tribe_routes import (  # noqa: PLC0415
            get_tribe_event_bus as get_bus,
        )

        return get_bus()

    return ProcessRuntimeShutdownDependencies(
        stop_bond_queue=stop_bond_queue,
        get_tribe_event_bus=get_tribe_event_bus,
    )


async def shutdown_runtime(
    *,
    activity_monitor: Any | None,
    resource_manager: Any | None,
    logger: Any,
) -> None:
    await shutdown_process_runtime(
        activity_monitor=activity_monitor,
        resource_manager=resource_manager,
        logger=logger,
        dependencies=_dependencies(),
    )


__all__ = ["shutdown_runtime"]
