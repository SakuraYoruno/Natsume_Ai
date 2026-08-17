"""把 Host 应用关闭衔接到活跃进程运行时能力的适配器。"""

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
    """冻结本次关闭看到的 Tribe bus，避免关闭过程中重新解析可变进程依赖。"""
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
    """委托进程级关闭顺序；此层不直接停止组件或抢占其所有权。

    调用者提供的 monitor/resource manager 仍属于当前 Host lifespan，底层组合只处理
    跨进程的依赖关系与安全的收尾顺序。
    """
    await shutdown_process_runtime(
        activity_monitor=activity_monitor,
        resource_manager=resource_manager,
        logger=logger,
        dependencies=_dependencies(tribe_event_bus=tribe_event_bus),
    )


__all__ = ["shutdown_runtime"]
