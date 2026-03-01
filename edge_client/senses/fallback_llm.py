"""Fallback LLM sense adapters for draft-only edge endpoints."""

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
    return rpc.context_compress(
        request_id=request_id,
        payload=payload,
        ts_ms=ts_ms,
    )
