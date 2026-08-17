"""Host HTTP 路由适配器的应用局部依赖端口。

路由工厂在创建时捕获这些 provider，而非读取模块级单例；因此同一进程内
创建、关闭某个 ASGI 应用都不会改写另一应用的依赖图。路由层只在请求时取用
能力，不能借此自行构造或提升业务 authority。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
import inspect
from typing import Any


RouteProvider = Callable[[], Any | None]


@dataclass(frozen=True, slots=True)
class HostRoutePorts:
    """一个 Host ASGI 应用拥有的不可变 provider 束。

    字段保存的是延迟解析函数而非服务实例：应用启动阶段可以完成组合，具体路由
    在需要时才读取最新可用依赖。``None`` 表示该部署未安装对应能力，调用方必须
    将其转换为明确的不可用响应，不能把缺失端口误当成可绕过的授权。
    """

    ledger_provider: RouteProvider | None = None
    life_command_authority_provider: RouteProvider | None = None
    life_memory_store_provider: RouteProvider | None = None
    canon_service_provider: RouteProvider | None = None
    tool_broker_provider: RouteProvider | None = None
    tool_catalog_provider: RouteProvider | None = None
    governed_tool_application_provider: RouteProvider | None = None
    bond_engine_provider: RouteProvider | None = None
    mode_transition_service_provider: RouteProvider | None = None
    embodiment_runtime_provider: RouteProvider | None = None
    security_immune_agent_provider: RouteProvider | None = None
    companion_permission_ingress_provider: RouteProvider | None = None
    companion_profile_ingress_provider: RouteProvider | None = None
    companion_feedback_ingress_provider: RouteProvider | None = None

    def __post_init__(self) -> None:
        """尽早拒绝非 callable 配置，避免请求路径才暴露部署接线错误。"""
        for field_name in (
            "ledger_provider",
            "life_command_authority_provider",
            "life_memory_store_provider",
            "canon_service_provider",
            "tool_broker_provider",
            "tool_catalog_provider",
            "governed_tool_application_provider",
            "bond_engine_provider",
            "mode_transition_service_provider",
            "embodiment_runtime_provider",
            "security_immune_agent_provider",
            "companion_permission_ingress_provider",
            "companion_profile_ingress_provider",
            "companion_feedback_ingress_provider",
        ):
            provider = getattr(self, field_name)
            if provider is not None and not callable(provider):
                raise TypeError(f"{field_name} must be callable or None")


def bind_host_route_endpoint(
    endpoint: Callable[..., Any],
    ports: HostRoutePorts,
) -> Callable[..., Any]:
    """把内部 ``_route_ports`` 绑定给端点，同时从 HTTP 签名中隐藏它。

    包装器保留原端点的公开参数，防止框架把组合期依赖误解析为客户端输入；每次
    调用仍经由同一应用的端口束进入，避免跨应用的隐式全局依赖泄漏。
    """

    @wraps(endpoint)
    async def bound(*args: Any, **kwargs: Any) -> Any:
        return await endpoint(*args, _route_ports=ports, **kwargs)

    signature = inspect.signature(endpoint)
    bound.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=[
            parameter
            for name, parameter in signature.parameters.items()
            if name != "_route_ports"
        ]
    )
    return bound


__all__ = ["HostRoutePorts", "RouteProvider", "bind_host_route_endpoint"]
