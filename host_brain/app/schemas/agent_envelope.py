"""Agent envelope schema for multi-agent pipeline interoperability."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


AGENT_ENVELOPE_SCHEMA_VERSION = "1.0"


class AgentEnvelope(BaseModel):
    """Standardized output contract between agent nodes."""

    agent_id: str = Field(..., description="Stable agent identifier, e.g. motive_agent")
    agent_family: str = Field("", description="Agent family, e.g. motive/planner/memory")
    status: str = Field("OK", description="Execution status: OK/SKIPPED/DEGRADED/ERROR")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Result confidence score [0,1]")
    reason: str = Field("", description="Human-readable concise reason")
    reason_code: str | None = Field(None, description="Machine-readable reason code")
    schema_version: str = Field(
        AGENT_ENVELOPE_SCHEMA_VERSION,
        description="Envelope schema version",
    )

    request_id: str = Field("", description="Upstream request id")
    node_id: str = Field("", description="Current node id")
    parent_node_id: str | None = Field(None, description="Parent node id in DAG")

    input_refs: list[str] = Field(default_factory=list, description="Input context references")
    output: dict[str, Any] = Field(default_factory=dict, description="Structured output payload")
    output_digest: dict[str, Any] = Field(default_factory=dict, description="Digest for trace rendering")

    fallback_used: bool = Field(False, description="Whether fallback path was used")
    degrade_code: str | None = Field(None, description="Degrade code when status is DEGRADED")

    ts_ms: int = Field(0, description="Envelope timestamp (epoch ms)")

    def as_trace_dict(self) -> dict[str, Any]:
        """Compact trace payload for observability pipeline."""
        return {
            "agent_id": self.agent_id,
            "agent_family": self.agent_family,
            "node_id": self.node_id,
            "parent_node_id": self.parent_node_id,
            "status": self.status,
            "confidence": self.confidence,
            "reason_code": self.reason_code,
            "fallback_used": self.fallback_used,
            "degrade_code": self.degrade_code,
            "output_digest": dict(self.output_digest),
            "ts_ms": int(self.ts_ms),
            "schema_version": self.schema_version,
        }


__all__ = [
    "AGENT_ENVELOPE_SCHEMA_VERSION",
    "AgentEnvelope",
]

