"""LLM 网关跨层传递的不可变结果契约。

这些对象把成功、降级和健康探测统一为稳定字段，让调用方
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
