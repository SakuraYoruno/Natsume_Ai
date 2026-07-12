"""Dependency-injected ordered shutdown for legacy Host runtime components."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LegacyRuntimeShutdownDependencies:
    """Legacy teardown capabilities resolved by the application adapter."""

    stop_bond_queue: Callable[[Any], None]
    stop_heartbeat: Callable[[], Awaitable[None]]
    get_tribe_event_bus: Callable[[], Any | None]
    stop_tribe_periodic_save: Callable[[], None]
    stop_task_scheduler: Callable[[], Awaitable[None]]
    stop_life_continuity: Callable[[], Awaitable[None]]
    stop_immune_monitor: Callable[[Any], None]


async def shutdown_legacy_runtime(
    *,
    activity_monitor: Any | None,
    resource_manager: Any | None,
    life_continuity_started: bool,
    logger: Any,
    dependencies: LegacyRuntimeShutdownDependencies,
) -> None:
    """Best-effort ordered teardown; each component degrades independently."""

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
    try:
        dependencies.stop_tribe_periodic_save()
        logger.info("TribeStateRegistry 定时保存已停止，最终快照已落盘")
    except Exception as periodic_stop_exc:
        logger.debug(
            "TribeStateRegistry.stop_periodic_save 失败（降级）: %s",
            periodic_stop_exc,
        )
    try:
        await dependencies.stop_task_scheduler()
    except Exception as task_scheduler_stop_exc:
        logger.debug("HostTaskScheduler stop degraded: %s", task_scheduler_stop_exc)
    try:
        if life_continuity_started:
            await dependencies.stop_life_continuity()
            logger.info("LifeContinuity loop stopped")
    except Exception as life_loop_stop_exc:
        logger.debug("LifeContinuity loop stop degraded: %s", life_loop_stop_exc)
    try:
        dependencies.stop_immune_monitor(logger)
    except Exception as codex_monitor_stop_exc:
        logger.debug(
            "Codex immune auto-dispatch monitor stop degraded: %s",
            codex_monitor_stop_exc,
        )
    logger.info("Natsume AI Host Brain shutting down ...")


__all__ = [
    "LegacyRuntimeShutdownDependencies",
    "shutdown_legacy_runtime",
]
