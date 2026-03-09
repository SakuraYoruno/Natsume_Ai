"""Neo4j data class schemas (extracted from neo4j_client.py)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Neo4jConfigIssue:
    reason_code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class Neo4jRuntimeConfig:
    enabled: bool
    uri: str
    database: str
    auth_ref: str | None
    pool_size: int
    tls_enabled: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "uri": self.uri,
            "database": self.database,
            "auth_ref": self.auth_ref,
            "pool_size": self.pool_size,
            "tls_enabled": self.tls_enabled,
        }


@dataclass(frozen=True)
class Neo4jConfigLoadResult:
    success: bool
    stage: str
    reason_code: str | None
    config: Neo4jRuntimeConfig | None
    issues: tuple[Neo4jConfigIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "config": (self.config.as_dict() if self.config is not None else None),
            "issues": [
                {
                    "reason_code": issue.reason_code,
                    "detail": issue.detail,
                    "field": issue.field,
                }
                for issue in self.issues
            ],
        }


@dataclass(frozen=True)
class BondEventRecord:
    event_id: str
    user_id: str
    session_id: str
    topic_id: str
    capability_hint: str
    evidence_refs: tuple[str, ...]
    payload: dict[str, Any]
    ts_ms: int
