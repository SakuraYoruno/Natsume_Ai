"""供 Host 路由适配器使用的、带类型的应用局部 Digital Life authority。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from host_brain.app.host_route_ports import HostRoutePorts


LIFE_ROUTE_AUTHORITY_UNAVAILABLE = "HOST_LIFE_ROUTE_AUTHORITY_UNAVAILABLE"
LIFE_ROUTE_AUTHORITY_INVALID = "HOST_LIFE_ROUTE_AUTHORITY_INVALID"


@dataclass(frozen=True, slots=True)
class HostLifeRouteAuthority:
    """一个 Host lifespan 同时拥有的命令写入器与快照读取器。

    两者必须来自同一生命周期组合，才能让读取的 version 与随后的命令前置条件指向
    同一权威状态源；路由不能把任意对象拼接成看似可用的 life authority。
    """

    command_service: Any
    snapshot_reader: Any

    def __post_init__(self) -> None:
        """在应用装配时校验最小协议，避免请求路径出现半接线的读写分裂。"""
        if not callable(getattr(self.command_service, "submit", None)):
            raise TypeError("command_service must expose submit()")
        if not callable(getattr(self.snapshot_reader, "get_snapshot", None)):
            raise TypeError("snapshot_reader must expose get_snapshot()")
        if not callable(getattr(self.snapshot_reader, "list_events", None)):
            raise TypeError("snapshot_reader must expose list_events()")


def require_host_life_route_authority(
    route_ports: HostRoutePorts,
) -> HostLifeRouteAuthority:
    """解析当前应用的 authority，绝不回退到进程全局状态。

    provider 缺失、抛错或返回错误类型都转换为 503：这是部署组合未完成而非可用的空
    生命状态。保留该失败边界可避免兼容路由越过显式 authority 执行写操作。
    """

    if not isinstance(route_ports, HostRoutePorts):
        raise TypeError("route_ports must be HostRoutePorts")
    provider = route_ports.life_command_authority_provider
    if not callable(provider):
        raise HTTPException(
            status_code=503,
            detail=LIFE_ROUTE_AUTHORITY_UNAVAILABLE,
        )
    try:
        authority = provider()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=LIFE_ROUTE_AUTHORITY_UNAVAILABLE,
        ) from exc
    if not isinstance(authority, HostLifeRouteAuthority):
        raise HTTPException(
            status_code=503,
            detail=LIFE_ROUTE_AUTHORITY_INVALID,
        )
    return authority


__all__ = [
    "HostLifeRouteAuthority",
    "LIFE_ROUTE_AUTHORITY_INVALID",
    "LIFE_ROUTE_AUTHORITY_UNAVAILABLE",
    "require_host_life_route_authority",
]
