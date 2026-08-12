"""召回融合服务数据模型。

从 recall_fusion.py 拆分，包含 RecallRouteConfig, RecallRuntimeConfig 等数据类。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecallRouteConfig:
    """一条受配置约束的检索路由；选择器限定其适用节点和目标语言。"""
    route_id: str
    store_id: str
    collection: str | None
    top_k: int
    score_weight: float
    node_selector: str
    render_target_lang_selector: str = "*"

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "store_id": self.store_id,
            "collection": self.collection,
            "top_k": int(self.top_k),
            "score_weight": float(self.score_weight),
            "node_selector": self.node_selector,
            "render_target_lang_selector": self.render_target_lang_selector,
        }


@dataclass(frozen=True)
class RecallRuntimeConfig:
    """已加载的检索运行时快照；routes 以 ID 索引以保证选择结果可审计。"""
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
    """配置加载期的有界问题描述，不携带原始配置或秘密。"""
    reason_code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class RecallConfigLoadResult:
    """配置加载的显式结果，允许上游闭合或降级而不是假定默认路由可用。"""
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
    """融合检索的结构化终态；输出仍须按下游权限过滤后才能进入提示词。"""
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]
