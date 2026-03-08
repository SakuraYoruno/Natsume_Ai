"""Embedding data class schemas (extracted from embedding.py)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EmbeddingRouteConfig:
    route_id: str
    provider: str
    model_id: str
    dim: int
    timeout_ms: int
    node_selector: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "provider": self.provider,
            "model_id": self.model_id,
            "dim": int(self.dim),
            "timeout_ms": int(self.timeout_ms),
            "node_selector": self.node_selector,
        }


@dataclass(frozen=True)
class EmbeddingRuntimeConfig:
    enabled: bool
    default_route: str
    routes: dict[str, EmbeddingRouteConfig]

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "default_route": self.default_route,
            "routes": {
                route_id: route.as_dict()
                for route_id, route in sorted(self.routes.items())
            },
        }


@dataclass(frozen=True)
class EmbeddingConfigIssue:
    reason_code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class EmbeddingConfigLoadResult:
    success: bool
    stage: str
    reason_code: str | None
    config: EmbeddingRuntimeConfig | None
    issues: tuple[EmbeddingConfigIssue, ...]

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
class EmbeddingResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]
