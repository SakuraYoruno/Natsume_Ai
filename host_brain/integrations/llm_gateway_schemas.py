"""LLM gateway data class schemas."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GatewayResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


@dataclass(frozen=True)
class LLMHealthProbeResult:
    """M24-A：LLM 健康探测结果。"""
    model_id: str
    provider: str
    available: bool
    latency_ms: int
    reason_code: str | None
    detail: str


@dataclass(frozen=True)
class LLMRouteResult:
    """M24-A：多模型路由结果。"""
    model_id: str
    provider: str
    reason_code: str | None
    latency_ms: int
    fallback_used: bool
    detail: str


@dataclass(frozen=True)
class ModelAttempt:
    """单次池化模型尝试记录。"""

    model: str
    provider: str
    status: str
    latency_ms: int
    reason_code: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class PoolCompleteResult:
    """共享模型池完成结果。"""

    content: str
    final_model: str
    final_provider: str
    attempts: list[ModelAttempt]
    pool_exhausted: bool
    total_latency_ms: int
    cached: bool
    cognitive_tier: str
