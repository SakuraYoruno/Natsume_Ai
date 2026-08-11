"""Application adapter for active process-runtime shutdown capabilities."""

from __future__ import annotations

from typing import Any

from host_brain.composition.process_runtime_shutdown import (
    ProcessRuntimeShutdownDependencies,
    shutdown_process_runtime,
)


def _dependencies(
    *,
    tribe_event_bus: Any | None,
) -> ProcessRuntimeShutdownDependencies:
    return ProcessRuntimeShutdownDependencies(
        get_tribe_event_bus=lambda: tribe_event_bus,
    )


async def shutdown_runtime(
    *,
    activity_monitor: Any | None,
    resource_manager: Any | None,
    tribe_event_bus: Any | None,
    logger: Any,
) -> None:
    await shutdown_process_runtime(
        activity_monitor=activity_monitor,
        resource_manager=resource_manager,
        logger=logger,
        dependencies=_dependencies(tribe_event_bus=tribe_event_bus),
    )


__all__ = ["shutdown_runtime"]
