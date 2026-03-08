"""召回融合服务数据模型。

从 recall_fusion.py 拆分，包含 RecallRouteConfig, RecallRuntimeConfig 等数据类。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecallRouteConfig:
    route_id: str
    store_id: str
    collection: str | None
    top_k: int
    score_weight: float
    node_selector: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "store_id": self.store_id,
            "collection": self.collection,
            "top_k": int(self.top_k),
            "score_weight": float(self.score_weight),
            "node_selector": self.node_selector,
        }


@dataclass(frozen=True)
class RecallRuntimeConfig:
    enabled: bool
    default_route: str
    routes: dict[str, RecallRouteConfig]

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
class RecallConfigIssue:
    reason_code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class RecallConfigLoadResult:
    success: bool
    stage: str
    reason_code: str | None
    config: RecallRuntimeConfig | None
    issues: tuple[RecallConfigIssue, ...]

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
class RecallFusionResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]
