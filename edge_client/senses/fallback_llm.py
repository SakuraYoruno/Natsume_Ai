"""将感知侧请求转交给仅能产出草稿的 Edge 回退 LLM 端点。"""

from __future__ import annotations

from typing import Any

from edge_client.rpc.fallback_llm import EdgeFallbackResult, EdgeFallbackRpcContract


def check_fallback_presence(
    *,
    rpc: EdgeFallbackRpcContract,
    request_id: str,
    payload: dict[str, Any] | None = None,
    ts_ms: int | None = None,
) -> EdgeFallbackResult:
    """查询本地草稿能力的存在性，不把“可用”解释为已产生或已采纳内容。"""
    return rpc.presence(
        request_id=request_id,
        payload=payload,
        ts_ms=ts_ms,
    )


def compile_asr_draft(
    *,
    rpc: EdgeFallbackRpcContract,
    request_id: str,
    payload: dict[str, Any],
    ts_ms: int | None = None,
) -> EdgeFallbackResult:
    """请求 ASR 文本整理草稿；授权、资源门控和输出审查均由 RPC 契约负责。"""
    return rpc.asr_compile(
        request_id=request_id,
        payload=payload,
        ts_ms=ts_ms,
    )


def build_retrieval_hints(
    *,
    rpc: EdgeFallbackRpcContract,
    request_id: str,
    payload: dict[str, Any],
    ts_ms: int | None = None,
) -> EdgeFallbackResult:
    """请求检索提示草稿，保留请求标识和时间以便账本将结果关联回原始输入。"""
    return rpc.retrieval_hints(
        request_id=request_id,
        payload=payload,
        ts_ms=ts_ms,
    )


def compress_context_draft(
    *,
    rpc: EdgeFallbackRpcContract,
    request_id: str,
    payload: dict[str, Any],
    ts_ms: int | None = None,
) -> EdgeFallbackResult:
    """请求上下文压缩草稿；适配器不重试失败，避免重复占用有限的本地资源。"""
    return rpc.context_compress(
        request_id=request_id,
        payload=payload,
        ts_ms=ts_ms,
    )
