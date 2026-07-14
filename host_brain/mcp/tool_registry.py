"""MCP ToolMeta registry application boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from host_brain.persistence.tool_registry_repository import ToolRegistryRepository


VALID_ACCESS_LEVELS = frozenset({"PUBLIC", "INTERNAL", "RESTRICTED"})
VALID_RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})


@dataclass
class ToolMeta:
    """Metadata for a single MCP provider tool."""

    provider_id: str
    tool_id: str
    name: str
    description: str
    access_level: str
    risk_level: str
    reversible: bool
    version: str = "1.0.0"
    schema_hash: str = ""
    domain_tags: list[str] = field(default_factory=list)
    emotion_requirement: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "tool_id": self.tool_id,
            "name": self.name,
            "description": self.description,
            "access_level": self.access_level,
            "risk_level": self.risk_level,
            "reversible": self.reversible,
            "version": self.version,
            "schema_hash": self.schema_hash,
            "domain_tags": list(self.domain_tags),
            "emotion_requirement": list(self.emotion_requirement),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolMeta":
        tags = data.get("domain_tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags)
        emotion_req = data.get("emotion_requirement") or []
        if isinstance(emotion_req, str):
            emotion_req = json.loads(emotion_req)
        return cls(
            provider_id=str(data["provider_id"]),
            tool_id=str(data["tool_id"]),
            name=str(data.get("name") or data["tool_id"]),
            description=str(data.get("description") or ""),
            access_level=str(data.get("access_level") or "PUBLIC").upper(),
            risk_level=str(data.get("risk_level") or "LOW").upper(),
            reversible=bool(data.get("reversible", False)),
            version=str(data.get("version") or "1.0.0"),
            schema_hash=str(data.get("schema_hash") or ""),
            domain_tags=list(tags),
            emotion_requirement=list(emotion_req),
        )


class MCPToolRegistry:
    """Tool registry facade backed by an operation-scoped repository."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        check_same_thread: bool = True,
    ) -> None:
        self.db_path = str(db_path)
        # Kept only as an API-compatibility parameter; connection ownership is
        # exclusively inside the persistence adapter.
        _ = check_same_thread
        self._repository = ToolRegistryRepository(self.db_path)

    def register(self, meta: ToolMeta) -> dict[str, Any]:
        """Register one immutable tool revision and update the current head."""
        self._validate(meta)
        return self._repository.register(meta.as_dict())

    def get(self, *, provider_id: str, tool_id: str) -> ToolMeta | None:
        """Fetch one tool by provider and tool identity."""
        record = self._repository.get(provider_id=provider_id, tool_id=tool_id)
        return ToolMeta.from_dict(record) if record is not None else None

    def list_by_provider(self, provider_id: str) -> list[ToolMeta]:
        """List tools belonging to a provider."""
        return [
            ToolMeta.from_dict(record)
            for record in self._repository.list_by_provider(provider_id)
        ]

    def list_all(self) -> list[ToolMeta]:
        """List all current tool heads."""
        return [ToolMeta.from_dict(record) for record in self._repository.list_all()]

    def sync_log(self) -> list[dict[str, Any]]:
        """Return immutable tool revision history in commit order."""
        return self._repository.sync_log()

    def close(self) -> None:
        self._repository.close()

    def _validate(self, meta: ToolMeta) -> None:
        if not meta.provider_id or not meta.tool_id:
            raise ValueError("ToolMeta requires non-empty provider_id and tool_id")
        if meta.access_level not in VALID_ACCESS_LEVELS:
            raise ValueError(
                f"Invalid access_level: {meta.access_level!r}; "
                f"must be one of {sorted(VALID_ACCESS_LEVELS)}"
            )
        if meta.risk_level not in VALID_RISK_LEVELS:
            raise ValueError(
                f"Invalid risk_level: {meta.risk_level!r}; "
                f"must be one of {sorted(VALID_RISK_LEVELS)}"
            )


__all__ = [
    "MCPToolRegistry",
    "ToolMeta",
    "VALID_ACCESS_LEVELS",
    "VALID_RISK_LEVELS",
]
