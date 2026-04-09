from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TribeAgentDescriptor:
    """轻量 Tribe Agent 描述信息（用于注册/观测，不耦合业务实现）。"""

    agent_id: str
    family: str
    execution_side: str = "host"
    owner_domain: str = "brain"
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "family": self.family,
            "execution_side": self.execution_side,
            "owner_domain": self.owner_domain,
            "capabilities": list(self.capabilities),
        }


__all__ = ["TribeAgentDescriptor"]
