"""Context pack schema for multi-agent orchestration."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


CONTEXT_PACK_SCHEMA_VERSION = "1.0"


class ContextPack(BaseModel):
    """Standardized context bundle shared across agent families."""

    schema_version: str = Field(CONTEXT_PACK_SCHEMA_VERSION, description="ContextPack schema version")

    request_meta: dict[str, Any] = Field(default_factory=dict, description="Request-level metadata")
    planner: dict[str, Any] = Field(default_factory=dict, description="Planner outputs")
    retrieval: dict[str, Any] = Field(default_factory=dict, description="Retrieval/RAG outputs")
    motive: dict[str, Any] = Field(default_factory=dict, description="Motive state summary")
    response_policy: dict[str, Any] = Field(default_factory=dict, description="Response policy summary")

    soul_state: dict[str, Any] = Field(default_factory=dict, description="Long-term persona state")
    offline_state: dict[str, Any] = Field(default_factory=dict, description="Offline/continuity state")
    runtime_state: dict[str, Any] = Field(default_factory=dict, description="Runtime state snapshot")
    memory_state: dict[str, Any] = Field(default_factory=dict, description="Memory state snapshot")

    trace_refs: list[str] = Field(default_factory=list, description="Trace references")
    extensions: dict[str, Any] = Field(default_factory=dict, description="Optional extension fields")

    def to_agent_input(self) -> dict[str, Any]:
        """Normalized dict passed to downstream agent processors."""
        return self.model_dump()


__all__ = [
    "CONTEXT_PACK_SCHEMA_VERSION",
    "ContextPack",
]

