"""ASR sense adapter using voice RPC contract."""

from __future__ import annotations

from typing import Any

from edge_client.rpc.voice import VoiceRpcContract, VoiceRpcResult


def emit_asr_chunk(
    *,
    rpc: VoiceRpcContract,
    request_id: str,
    payload: dict[str, Any],
    ts_ms: int | None = None,
) -> VoiceRpcResult:
    return rpc.asr_chunk(
        request_id=request_id,
        payload=payload,
        ts_ms=ts_ms,
    )
