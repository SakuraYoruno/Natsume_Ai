"""Single-attempt Edge -> Host delivery for the durable Gateway inbox.

Retry, idempotency, leases, fencing and DLQ are owned exclusively by the
durable Gateway transport store.  This adapter signs and performs exactly one
HTTP attempt, then returns a receipt that lets the store make the next state
transition.  It intentionally has no cache, retry thread or compatibility
entrypoint.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any


class InputSourceType(str, Enum):
    """Stable Edge input-source identifiers shared by channel contracts."""

    ASR = "ASR"
    WECHAT = "WECHAT"
    GATEWAY = "GATEWAY"


@dataclass(frozen=True, slots=True)
class InputReportResult:
    success: bool
    source: InputSourceType
    reason_code: str | None
    detail: str
    cached: bool = False
    receipt_ref: str | None = None
    receipt_body_sha256: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "source": self.source.value,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "cached": False,
            "receipt_ref": self.receipt_ref,
            "receipt_body_sha256": self.receipt_body_sha256,
        }


class EdgeInputReporter:
    """Deliver one claimed durable inbox record to Host secure ingress."""

    INPUT_ENDPOINT = "/v1/host/input"

    def __init__(
        self,
        *,
        host_url: str,
        hmac_key: str | None = None,
        timeout_s: float = 3.0,
        secure_principal_id: str | None = None,
        secure_tenant_id: str | None = None,
        secure_key_id: str | None = None,
        secure_audience: str | None = None,
        secure_scope: str | None = None,
    ) -> None:
        self._host_url = str(host_url or "").rstrip("/")
        self._hmac_key = str(hmac_key or "").strip() or None
        self._timeout_s = max(0.2, float(timeout_s))
        self._secure_principal_id = str(
            secure_principal_id
            or os.environ.get("EDGE_GATEWAY_HOST_PRINCIPAL_ID")
            or "service.edge.gateway"
        ).strip()
        self._secure_tenant_id = str(
            secure_tenant_id
            or os.environ.get("EDGE_GATEWAY_HOST_TENANT_ID")
            or "local"
        ).strip()
        self._secure_key_id = str(
            secure_key_id
            or os.environ.get("EDGE_GATEWAY_HOST_KEY_ID")
            or "edge.gateway.hmac.v1"
        ).strip()
        self._secure_audience = str(
            secure_audience
            or os.environ.get("EDGE_GATEWAY_HOST_AUDIENCE")
            or "host"
        ).strip()
        self._secure_scope = str(
            secure_scope
            or os.environ.get("EDGE_GATEWAY_HOST_SCOPE")
            or "host:write"
        ).strip()
        self._lock = threading.Lock()
        self._total_sent = 0
        self._total_failed = 0
        self._last_result: dict[str, Any] | None = None
        self._last_payload_summary: dict[str, Any] | None = None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "host_url": self._host_url,
                "input_endpoint": self.INPUT_ENDPOINT,
                "timeout_s": self._timeout_s,
                "transport_owner": "gateway_durable_store",
                "total_sent": self._total_sent,
                "total_failed": self._total_failed,
                "last_result": dict(self._last_result or {}),
                "last_payload": dict(self._last_payload_summary or {}),
                "secure_principal_id": self._secure_principal_id,
                "secure_key_id": self._secure_key_id,
                "secure_auth_enabled": bool(self._hmac_key),
            }

    def deliver_gateway_payload(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> InputReportResult:
        """Perform one frozen-byte delivery and require Host's durable receipt."""

        source = InputSourceType.GATEWAY
        body = self._serialize_payload(payload)
        stable_idempotency_key = self._host_idempotency_key(idempotency_key, body)
        started = time.monotonic()
        try:
            receipt = self._do_http_post_frozen(
                payload,
                body=body,
                idempotency_key=stable_idempotency_key,
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._record_result(
                payload,
                success=True,
                reason_code=None,
                detail="durable inbox delivered",
                elapsed_ms=elapsed_ms,
            )
            return InputReportResult(
                True,
                source,
                None,
                "durable inbox delivered",
                receipt_ref=str(receipt.get("receipt_ref") or "") or None,
                receipt_body_sha256=str(receipt.get("body_sha256") or "") or None,
            )
        except Exception as exc:
            reason_code = self._classify_delivery_failure(exc)
            self._record_result(
                payload,
                success=False,
                reason_code=reason_code,
                detail=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            return InputReportResult(False, source, reason_code, str(exc))

    @staticmethod
    def _serialize_payload(payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @staticmethod
    def _host_idempotency_key(value: str | None, body: bytes) -> str:
        material = str(value or "").strip().encode("utf-8") or bytes(body)
        return f"edge-host:{hashlib.sha256(material).hexdigest()}"

    def _do_http_post_frozen(
        self,
        payload: dict[str, Any],
        *,
        body: bytes,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not self._hmac_key:
            raise RuntimeError("durable Host ingress HMAC key is not configured")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Edge-Source": "edge_gateway_durable_delivery",
            **self._build_secure_ingress_headers(
                body=body,
                idempotency_key=idempotency_key,
            ),
        }
        request = urllib.request.Request(
            self._host_url + self.INPUT_ENDPOINT,
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
            if response.status not in (200, 201, 202):
                raise RuntimeError(f"Host returned non-2xx status: {response.status}")
            response_body = response.read()
        try:
            data = json.loads(response_body.decode("utf-8", errors="strict"))
        except Exception as exc:
            raise RuntimeError("durable Host receipt is invalid JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("durable Host receipt must be an object")
        status = str(data.get("status") or "").strip().upper()
        expected_body_sha256 = hashlib.sha256(body).hexdigest()
        expected_idempotency_sha256 = hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()
        receipt_ref = str(data.get("receipt_ref") or "").strip()
        if (
            data.get("success") is not True
            or status not in {"ACCEPTED", "COMPLETED", "IGNORED"}
            or str(data.get("source") or "").strip().upper() != "GATEWAY"
            or not receipt_ref.startswith("host-input-result://")
            or str(data.get("body_sha256") or "") != expected_body_sha256
            or str(data.get("idempotency_sha256") or "")
            != expected_idempotency_sha256
        ):
            raise RuntimeError("durable Host receipt contract mismatch")
        return data

    def _build_secure_ingress_headers(
        self,
        *,
        body: bytes,
        idempotency_key: str,
    ) -> dict[str, str]:
        from natsume_contracts.edge.v1 import (
            IngressSurface,
            build_hmac_sha256_signature,
            build_surface_request,
        )
        from natsume_contracts.identity import (
            CredentialProofKind,
            CredentialProofMetadata,
            IdentitySchemaVersion,
            IngressIdentityEnvelope,
            Principal,
            PrincipalKind,
        )

        issued_at_ms = int(time.time() * 1000)
        request_id = f"req:{uuid.uuid4().hex}"
        nonce = f"nonce:{uuid.uuid4().hex}"
        metadata = CredentialProofMetadata(
            kind=CredentialProofKind.HMAC_SHA256,
            key_id=self._secure_key_id,
            algorithm="hmac-sha256",
            proof_id=f"proof:{uuid.uuid4().hex}",
            issued_at_epoch_ms=issued_at_ms,
            expires_at_epoch_ms=issued_at_ms + 60_000,
        )
        identity = IngressIdentityEnvelope(
            schema_version=IdentitySchemaVersion.V1,
            principal=Principal(
                principal_id=self._secure_principal_id,
                kind=PrincipalKind.SERVICE,
                tenant_id=self._secure_tenant_id,
                granted_scopes=(self._secure_scope,),
            ),
            credential_proof=metadata,
            audience=self._secure_audience,
            request_id=request_id,
            requested_scopes=(self._secure_scope,),
        )
        secure_request = build_surface_request(
            surface=IngressSurface.HOST,
            identity=identity,
            credential=None,
            method="POST",
            normalized_path=self.INPUT_ENDPOINT,
            idempotency_key=idempotency_key,
            nonce=nonce,
            raw_body=body,
            received_at_epoch_ms=issued_at_ms,
            transport_key=f"principal:{self._secure_principal_id}",
            correlation_id=request_id,
        )
        signature = build_hmac_sha256_signature(
            secure_request,
            self._hmac_key or "",
        )
        identity_header = base64.urlsafe_b64encode(
            identity.to_canonical_json().encode("utf-8")
        ).decode("ascii").rstrip("=")
        return {
            "X-Natsume-Identity": identity_header,
            "X-Natsume-Proof": signature,
            "X-Natsume-Idempotency-Key": idempotency_key,
            "X-Natsume-Nonce": nonce,
            "X-Natsume-Correlation-Id": request_id,
        }

    @staticmethod
    def _classify_delivery_failure(exc: Exception) -> str:
        if isinstance(exc, urllib.error.HTTPError):
            return (
                "INPUT_REPORT_DELIVERY_UNCERTAIN"
                if int(getattr(exc, "code", 0) or 0) >= 500
                else "INPUT_REPORT_REJECTED"
            )
        if isinstance(
            exc,
            (TimeoutError, socket.timeout, ConnectionResetError, BrokenPipeError),
        ):
            return "INPUT_REPORT_DELIVERY_UNCERTAIN"
        if isinstance(exc, urllib.error.URLError):
            reason = getattr(exc, "reason", None)
            if isinstance(
                reason,
                (TimeoutError, socket.timeout, ConnectionResetError, BrokenPipeError),
            ):
                return "INPUT_REPORT_DELIVERY_UNCERTAIN"
            return "INPUT_REPORT_CONNECT_FAILED"
        if "receipt contract mismatch" in str(exc):
            return "INPUT_REPORT_DELIVERY_UNCERTAIN"
        return "INPUT_REPORT_DELIVERY_UNCERTAIN"

    def _record_result(
        self,
        payload: dict[str, Any],
        *,
        success: bool,
        reason_code: str | None,
        detail: str,
        elapsed_ms: int,
    ) -> None:
        content = str(payload.get("content") or payload.get("text") or "")
        summary = {
            "source": str(payload.get("source") or ""),
            "platform": str(payload.get("platform") or ""),
            "conversation_id": str(
                payload.get("conversation_id") or payload.get("session_id") or ""
            ),
            "msg_id": str(payload.get("msg_id") or ""),
            "content_len": len(content),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        with self._lock:
            if success:
                self._total_sent += 1
            else:
                self._total_failed += 1
            self._last_payload_summary = {
                key: value for key, value in summary.items() if value not in ("", 0)
            }
            self._last_result = {
                "success": bool(success),
                "cached": False,
                "source": InputSourceType.GATEWAY.value,
                "reason_code": reason_code,
                "detail": str(detail or "")[:240],
                "elapsed_ms": int(elapsed_ms),
                "ts_ms": int(time.time() * 1000),
            }


__all__ = ["EdgeInputReporter", "InputReportResult", "InputSourceType"]
