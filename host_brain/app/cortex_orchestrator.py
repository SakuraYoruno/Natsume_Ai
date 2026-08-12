"""规范 Cortex 运行时的 HTTP 适配入口。

适配层只验证应用端口已由 Host 组合完成，并原样转发 SSE；具体路由、检索和授权决策
始终位于 Agent Runtime，避免 HTTP 层演变成第二个编排权威。
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.agent_runtime.cortex_runtime import (
    _cortex_orchestrator_sse_generator_impl,
)


async def cortex_orchestrator_sse_generator(
    *,
    application_ports: AgentRuntimeApplicationPorts | None = None,
    **kwargs: Any,
) -> AsyncGenerator[str, None]:
    """验证注入端口后委托规范运行时；未完成组合时明确失败而不尝试全局回退。"""
    if not isinstance(application_ports, AgentRuntimeApplicationPorts):
        raise RuntimeError("HOST_AGENT_RUNTIME_PORTS_NOT_INJECTED")
    async for event in _cortex_orchestrator_sse_generator_impl(
        application_ports=application_ports,
        **kwargs,
    ):
        yield event

__all__ = ["cortex_orchestrator_sse_generator"]
