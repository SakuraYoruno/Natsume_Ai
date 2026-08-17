"""Edge Tribe 组合时使用的 Agent 身份与能力声明。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EdgeTribeAgentDescriptor:
    """描述一个 Edge 侧 Agent 在组合图中的归属。

    该值对象用于让 Host/Edge 在装配和观测时对齐 agent_id、运行侧与能力名称；
    ``capabilities`` 只是静态声明，实际调用仍要经过各自的 RPC、资源和授权边界，
    因而不能把描述符序列化结果当作已经获准执行的凭据。
    """
    agent_id: str
    family: str
    execution_side: str = "edge"
    owner_domain: str = "edge_runtime"
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """导出跨边界可传递的元数据副本，不暴露运行对象或可变能力集合。"""
        return {
            "agent_id": self.agent_id,
            "family": self.family,
            "execution_side": self.execution_side,
            "owner_domain": self.owner_domain,
            "capabilities": list(self.capabilities),
        }


__all__ = ["EdgeTribeAgentDescriptor"]
