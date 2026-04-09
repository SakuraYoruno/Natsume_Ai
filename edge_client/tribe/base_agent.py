from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EdgeTribeAgentDescriptor:
    agent_id: str
    family: str
    execution_side: str = "edge"
    owner_domain: str = "edge_runtime"
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "family": self.family,
            "execution_side": self.execution_side,
            "owner_domain": self.owner_domain,
            "capabilities": list(self.capabilities),
        }


__all__ = ["EdgeTribeAgentDescriptor"]
