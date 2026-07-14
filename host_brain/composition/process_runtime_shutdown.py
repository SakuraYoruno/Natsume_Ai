"""Dependency-injected ordered shutdown for active Host process services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProcessRuntimeShutdownDependencies:
    """Teardown capabilities resolved once by the application adapter."""

    stop_bond_queue: Callable[[Any], None]
    stop_heartbeat: Callable[[], Awaitable[None]]
    get_tribe_event_bus: Callable[[], Any | None]


async def shutdown_process_runtime(
    *,
    activity_monitor: Any | None,
    resource_manager: Any | None,
    logger: Any,
    dependencies: ProcessRuntimeShutdownDependencies,
) -> None:
    """Best-effort ordered teardown; each active service is isolated."""

    try:
        if activity_monitor is not None:
            activity_monitor.stop()
            logger.info("ActivityMonitor 已停止")
    except Exception as activity_stop_exc:
        logger.warning("ActivityMonitor 停止失败: %s", activity_stop_exc)
    try:
        if resource_manager is not None:
            resource_manager.stop()
            logger.info("SystemResourceManager 已停止")
    except Exception as resource_stop_exc:
        logger.warning("SystemResourceManager 停止失败: %s", resource_stop_exc)
    try:
        dependencies.stop_bond_queue(logger)
    except Exception as bond_queue_exc:
        logger.warning("BondUpdateQueue 关闭失败: %s", bond_queue_exc)
    try:
        await dependencies.stop_heartbeat()
    except Exception:
        pass
    try:
        shutdown_bus = dependencies.get_tribe_event_bus()
        if shutdown_bus is not None:
            await shutdown_bus.stop()
            logger.info("TribeEventBus dispatch_loop 已停止")
    except Exception as bus_stop_exc:
        logger.warning("TribeEventBus dispatch_loop 停止失败: %s", bus_stop_exc)
    logger.info("Natsume AI Host Brain shutting down ...")


__all__ = [
    "ProcessRuntimeShutdownDependencies",
    "shutdown_process_runtime",
]
