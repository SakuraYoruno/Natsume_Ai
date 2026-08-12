"""LLM 网关跨层传递的不可变结果契约。

这些对象把成功、降级、健康探测、路由选择和池化尝试统一为稳定字段，让调用方
按状态和原因码作决定，而不必依赖提供方异常文本或内部对象。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GatewayResult:
    """单次网关调用的最终边界结果；``output`` 可为草稿降级，但结构始终完整。"""
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


@dataclass(frozen=True)
class LLMHealthProbeResult:
    # 探测结果不包含探针载荷、凭据或原始响应，只提供可用性和诊断摘要。
    """M24-A：LLM 健康探测结果。"""
    model_id: str
    provider: str
    available: bool
    latency_ms: int
    reason_code: str | None
    detail: str


@dataclass(frozen=True)
class LLMRouteResult:
    # 路由记录明确是否使用回退候选，供审计还原模型选择而非保存业务内容。
    """M24-A：多模型路由结果。"""
    model_id: str
    provider: str
    reason_code: str | None
    latency_ms: int
    fallback_used: bool
    detail: str


@dataclass(frozen=True)
class ModelAttempt:
    # 单次尝试只记录延迟、状态和原因码，避免在池化观测中复制请求正文。
    """单次池化模型尝试记录。"""

    model: str
    provider: str
    status: str
    latency_ms: int
    reason_code: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class PoolCompleteResult:
    # 聚合结果保留最终路由与所有尝试，供上层判断耗尽、降级或重试策略。
    """共享模型池完成结果。"""

    content: str
    final_model: str
    final_provider: str
    attempts: list[ModelAttempt]
    pool_exhausted: bool
    total_latency_ms: int
    cached: bool
    cognitive_tier: str
