"""Provider-neutral tool descriptor contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolSpec:
    tool_id: str
    display_name: str
    description: str
    parameters: dict[str, Any]
    has_side_effects: bool = False
    executor: Callable[..., Any] | None = field(default=None, repr=False)

    def to_llm_schema(self) -> dict[str, Any]:
        return {
            "name": self.tool_id,
            "description": self.description,
            "parameters": self.parameters,
        }


__all__ = ["ToolSpec"]
