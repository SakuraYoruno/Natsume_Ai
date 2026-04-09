from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from host_brain.tribe.base_agent import TribeAgentDescriptor


@dataclass(slots=True)
class ANSAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="ans_agent",
            family="ans",
            capabilities=("scheduler", "mode_transition", "proactive_tick"),
        )
    )

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()


__all__ = ["ANSAgent"]
