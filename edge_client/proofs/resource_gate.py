"""Proof helpers for voice resource gate observability snapshots."""

from __future__ import annotations

from typing import Any

from edge_client.rpc.resource_gate import S_VOICE_RESOURCE_GATE, VoiceResourceGate
from natsume_contracts.edge.v1 import now_ms


def build_resource_gate_proof(
    *,
    gate: VoiceResourceGate,
    request_id: str,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "stage": S_VOICE_RESOURCE_GATE,
        "snapshot": gate.snapshot(),
        "ts_ms": int(ts_ms if ts_ms is not None else now_ms()),
    }
