"""Edge RPC request/response schema contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.core.errors import EDGE_RPC_SCHEMA_INVALID


S_EDGE_RPC_SCHEMA = "S_EDGE_RPC_SCHEMA"


@dataclass(frozen=True)
class EdgeRpcEnvelope:
    request_id: str
    ts_ms: int
    nonce: str
    signature: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "ts_ms": self.ts_ms,
            "nonce": self.nonce,
            "signature": self.signature,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class EdgeRpcEnvelopeParseResult:
    success: bool
    stage: str
    reason_code: str | None
    detail: str
    envelope: EdgeRpcEnvelope | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "envelope": (self.envelope.as_dict() if self.envelope is not None else None),
        }


@dataclass(frozen=True)
class EdgeRpcResponse:
    success: bool
    status: str
    reason_code: str | None
    detail: str
    event: dict[str, Any]
    ts_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "event": dict(self.event),
            "ts_ms": int(self.ts_ms),
        }


def parse_edge_rpc_envelope(raw: Mapping[str, Any] | Any) -> EdgeRpcEnvelopeParseResult:
    if not isinstance(raw, Mapping):
        return _schema_failure("envelope must be object")

    request_id = _as_non_empty_text(raw.get("request_id"))
    if request_id is None:
        return _schema_failure("request_id must be non-empty string")

    ts_value = raw.get("ts_ms")
    if not isinstance(ts_value, int) or isinstance(ts_value, bool):
        return _schema_failure("ts_ms must be integer")

    nonce = _as_non_empty_text(raw.get("nonce"))
    if nonce is None:
        return _schema_failure("nonce must be non-empty string")

    signature = _as_non_empty_text(raw.get("signature"))
    if signature is None:
        return _schema_failure("signature must be non-empty string")

    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        return _schema_failure("payload must be object")

    envelope = EdgeRpcEnvelope(
        request_id=request_id,
        ts_ms=int(ts_value),
        nonce=nonce,
        signature=signature,
        payload=dict(payload),
    )
    return EdgeRpcEnvelopeParseResult(
        success=True,
        stage=S_EDGE_RPC_SCHEMA,
        reason_code=None,
        detail="edge rpc envelope parsed",
        envelope=envelope,
    )


def _schema_failure(detail: str) -> EdgeRpcEnvelopeParseResult:
    return EdgeRpcEnvelopeParseResult(
        success=False,
        stage=S_EDGE_RPC_SCHEMA,
        reason_code=EDGE_RPC_SCHEMA_INVALID,
        detail=detail,
        envelope=None,
    )


def _as_non_empty_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text else None
