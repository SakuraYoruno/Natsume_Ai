"""Versioned, runtime-independent contracts for durable operator projections."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


OPERATOR_PROJECTION_SCHEMA_VERSION = "Natsume.operator.query_projection.v1"


class ProjectionDegradedState(str, Enum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    REBUILDING = "REBUILDING"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"


@dataclass(frozen=True, slots=True)
class ProjectionDocument:
    """One generic kind/key projection envelope shared by all runtimes."""

    kind: str
    key: str
    payload: Mapping[str, Any]
    projection_version: int
    projection_cursor: int
    source_cursor: int
    source_digest: str
    payload_digest: str
    freshness_ts_ms: int
    degraded_state: ProjectionDegradedState = ProjectionDegradedState.HEALTHY
    degraded_detail: str | None = None
    schema_version: str = OPERATOR_PROJECTION_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "key": self.key,
            "payload": dict(self.payload),
            "projection_version": self.projection_version,
            "projection_cursor": self.projection_cursor,
            "source_cursor": self.source_cursor,
            "source_digest": self.source_digest,
            "payload_digest": self.payload_digest,
            "freshness_ts_ms": self.freshness_ts_ms,
            "degraded_state": self.degraded_state.value,
            "degraded_detail": self.degraded_detail,
        }


__all__ = [
    "OPERATOR_PROJECTION_SCHEMA_VERSION",
    "ProjectionDegradedState",
    "ProjectionDocument",
]
