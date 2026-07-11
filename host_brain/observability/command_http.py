"""FastAPI protocol boundary for the authenticated OBS command plane.

This module owns no domain handlers and registers no global router.  Runtime
composition must explicitly provide ``SecureObsCommandIngress`` and include the
returned router in the protected Host router registry.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
import time
from typing import Any, Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from natsume_contracts.identity import CredentialProof, parse_identity_envelope

from .secure_command_ingress import (
    MAX_OBS_COMMAND_BODY_BYTES,
    ObsCommandIngressOutcome,
    ObsCommandIngressRequest,
    SecureObsCommandIngress,
)


_SECURITY_HEADER_PREFIX = b"x-natsume-"
_IDENTITY_HEADER = b"x-natsume-identity"
_PROOF_HEADER = b"x-natsume-proof"
_IDEMPOTENCY_HEADER = b"x-natsume-idempotency-key"
_NONCE_HEADER = b"x-natsume-nonce"
_CORRELATION_HEADER = b"x-natsume-correlation-id"
_MAX_IDENTITY_HEADER_BYTES = 16_384
_MAX_PROOF_HEADER_BYTES = 8_192
_MAX_TOKEN_HEADER_BYTES = 512
_MAX_HTTP_PEER_HOST_BYTES = 255
_UNKNOWN_HTTP_PEER_TRANSPORT_KEY = "http-peer:unknown"


class ObsCommandIngressProvider(Protocol):
    def __call__(self) -> SecureObsCommandIngress | None: ...


class _HttpContractError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


def create_obs_command_http_router(
    *,
    ingress_provider: ObsCommandIngressProvider | Callable[[], SecureObsCommandIngress | None] | None = None,
    clock_ms: Callable[[], int] | None = None,
) -> APIRouter:
    """Create the one command route without registering it globally."""

    router = APIRouter(tags=["host-obs-commands"])
    now_ms = clock_ms or (lambda: int(time.time() * 1000))

    @router.post("/v1/host/obs-commands/{command_id}")
    async def post_obs_command(command_id: str, request: Request) -> JSONResponse:
        # Resolve the fail-closed dependency before receiving any untrusted
        # body bytes.  A missing provider must not make the process consume an
        # attacker-controlled stream that it cannot authenticate or audit.
        if ingress_provider is None:
            return _error_response(
                status_code=503,
                reason_code="OBS_COMMAND_INGRESS_PROVIDER_UNAVAILABLE",
                command_id=command_id,
            )
        try:
            ingress = ingress_provider()
        except Exception:
            ingress = None
        if ingress is None or not callable(getattr(ingress, "execute", None)):
            return _error_response(
                status_code=503,
                reason_code="OBS_COMMAND_INGRESS_PROVIDER_UNAVAILABLE",
                command_id=command_id,
            )
        transport_key = _http_peer_transport_key(request)

        # Freeze exact ASGI chunks without trusting Content-Length.  The
        # accumulator never grows beyond the command contract's hard limit and
        # stops receiving as soon as the next chunk would exceed that limit.
        try:
            raw_body = await _read_bounded_body(request)
        except _HttpContractError as exc:
            return await _evaluate_contract_rejection(
                ingress=ingress,
                request=request,
                raw_body=b"",
                command_id=command_id,
                reason_code=exc.reason_code,
                identity=None,
                transport_key=transport_key,
                now_ms=now_ms,
            )

        identity = None
        try:
            headers = _single_natsume_headers(request)
            identity = parse_identity_envelope(
                _decode_identity(headers.get(_IDENTITY_HEADER, b""))
            )
            proof = _required_ascii_header(
                headers,
                _PROOF_HEADER,
                max_bytes=_MAX_PROOF_HEADER_BYTES,
                missing_reason="OBS_COMMAND_PROOF_MISSING",
            )
            idempotency_key = _required_ascii_header(
                headers,
                _IDEMPOTENCY_HEADER,
                max_bytes=_MAX_TOKEN_HEADER_BYTES,
                missing_reason="OBS_COMMAND_IDEMPOTENCY_MISSING",
            )
            nonce = _required_ascii_header(
                headers,
                _NONCE_HEADER,
                max_bytes=_MAX_TOKEN_HEADER_BYTES,
                missing_reason="OBS_COMMAND_NONCE_MISSING",
            )
            correlation_id = _optional_ascii_header(
                headers,
                _CORRELATION_HEADER,
                max_bytes=_MAX_TOKEN_HEADER_BYTES,
            )
            credential = CredentialProof(
                metadata=identity.credential_proof,
                raw_proof=proof,
            )
        except _HttpContractError as exc:
            return await _evaluate_contract_rejection(
                ingress=ingress,
                request=request,
                raw_body=raw_body,
                command_id=command_id,
                reason_code=exc.reason_code,
                identity=identity,
                transport_key=transport_key,
                now_ms=now_ms,
            )
        except Exception:
            return await _evaluate_contract_rejection(
                ingress=ingress,
                request=request,
                raw_body=raw_body,
                command_id=command_id,
                reason_code="OBS_COMMAND_IDENTITY_INVALID",
                identity=identity,
                transport_key=transport_key,
                now_ms=now_ms,
            )

        normalized_path = str(request.url.path)
        ingress_request = ObsCommandIngressRequest(
            command_id=str(command_id),
            raw_body=raw_body,
            identity=identity,
            credential=credential,
            idempotency_key=idempotency_key,
            nonce=nonce,
            received_at_epoch_ms=int(now_ms()),
            transport_key=transport_key,
            method=str(request.method).upper(),
            normalized_path=normalized_path,
            correlation_id=correlation_id,
        )
        try:
            outcome = await ingress.execute(ingress_request)
        except Exception:
            return _error_response(
                status_code=503,
                reason_code="OBS_COMMAND_INGRESS_UNAVAILABLE",
                command_id=command_id,
            )
        if not isinstance(outcome, ObsCommandIngressOutcome):
            return _error_response(
                status_code=503,
                reason_code="OBS_COMMAND_INGRESS_RESULT_INVALID",
                command_id=command_id,
            )
        return _outcome_response(outcome)

    return router


async def _read_bounded_body(request: Request) -> bytes:
    buffered = bytearray()
    async for chunk in request.stream():
        if not chunk:
            continue
        if len(chunk) > MAX_OBS_COMMAND_BODY_BYTES - len(buffered):
            raise _HttpContractError(
                "OBS_COMMAND_BODY_TOO_LARGE",
                status_code=413,
            )
        buffered.extend(chunk)
    return bytes(buffered)


def _http_peer_transport_key(request: Request) -> str:
    """Return a first-layer bucket derived only from the observed ASGI peer."""

    client = request.scope.get("client")
    if not isinstance(client, (tuple, list)) or not client:
        return _UNKNOWN_HTTP_PEER_TRANSPORT_KEY
    raw_host = client[0]
    if not isinstance(raw_host, str):
        return _UNKNOWN_HTTP_PEER_TRANSPORT_KEY
    host = raw_host.strip().lower()
    try:
        encoded = host.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return _UNKNOWN_HTTP_PEER_TRANSPORT_KEY
    if (
        not encoded
        or len(encoded) > _MAX_HTTP_PEER_HOST_BYTES
        or any(byte < 0x20 or byte == 0x7F for byte in encoded)
    ):
        return _UNKNOWN_HTTP_PEER_TRANSPORT_KEY
    return f"http-peer:{host}"


def _single_natsume_headers(request: Request) -> dict[bytes, bytes]:
    selected: dict[bytes, bytes] = {}
    for raw_name, raw_value in list(request.scope.get("headers") or ()):
        name = bytes(raw_name).strip().lower()
        if not name.startswith(_SECURITY_HEADER_PREFIX):
            continue
        if name in selected:
            raise _HttpContractError(
                "OBS_COMMAND_SECURITY_HEADER_AMBIGUOUS",
                status_code=400,
            )
        selected[name] = bytes(raw_value).strip()
    return selected


async def _evaluate_contract_rejection(
    *,
    ingress: SecureObsCommandIngress,
    request: Request,
    raw_body: bytes,
    command_id: str,
    reason_code: str,
    identity: Any,
    transport_key: str,
    now_ms: Callable[[], int],
) -> JSONResponse:
    """Send malformed transport contracts through attempt-rate + audit."""

    rejected_request = ObsCommandIngressRequest(
        command_id=str(command_id),
        raw_body=bytes(raw_body),
        identity=identity,
        credential=None,
        idempotency_key="invalid-contract",
        nonce="invalid-contract",
        received_at_epoch_ms=int(now_ms()),
        transport_key=str(transport_key),
        method=str(request.method).upper(),
        normalized_path=str(request.url.path),
        correlation_id=None,
        contract_error_reason=str(reason_code),
    )
    try:
        outcome = await ingress.execute(rejected_request)
    except Exception:
        return _error_response(
            status_code=503,
            reason_code="OBS_COMMAND_INGRESS_UNAVAILABLE",
            command_id=command_id,
        )
    if not isinstance(outcome, ObsCommandIngressOutcome):
        return _error_response(
            status_code=503,
            reason_code="OBS_COMMAND_INGRESS_RESULT_INVALID",
            command_id=command_id,
        )
    return _outcome_response(outcome)


def _decode_identity(value: bytes) -> bytes:
    encoded = bytes(value).strip()
    if not encoded or len(encoded) > _MAX_IDENTITY_HEADER_BYTES:
        raise _HttpContractError("OBS_COMMAND_IDENTITY_INVALID", status_code=401)
    try:
        encoded.decode("ascii", errors="strict")
        return base64.b64decode(
            encoded + (b"=" * (-len(encoded) % 4)),
            altchars=b"-_",
            validate=True,
        )
    except Exception as exc:
        raise _HttpContractError(
            "OBS_COMMAND_IDENTITY_INVALID",
            status_code=401,
        ) from exc


def _required_ascii_header(
    headers: dict[bytes, bytes],
    name: bytes,
    *,
    max_bytes: int,
    missing_reason: str,
) -> str:
    raw = headers.get(name, b"")
    if not raw:
        raise _HttpContractError(missing_reason, status_code=401)
    return _decode_bounded_ascii(raw, max_bytes=max_bytes)


def _optional_ascii_header(
    headers: dict[bytes, bytes],
    name: bytes,
    *,
    max_bytes: int,
) -> str | None:
    raw = headers.get(name, b"")
    if not raw:
        return None
    return _decode_bounded_ascii(raw, max_bytes=max_bytes)


def _decode_bounded_ascii(raw: bytes, *, max_bytes: int) -> str:
    value = bytes(raw).strip()
    if not value or len(value) > max_bytes:
        raise _HttpContractError("OBS_COMMAND_SECURITY_HEADER_INVALID", status_code=400)
    try:
        return value.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise _HttpContractError(
            "OBS_COMMAND_SECURITY_HEADER_INVALID",
            status_code=400,
        ) from exc


def _outcome_response(outcome: ObsCommandIngressOutcome) -> JSONResponse:
    status_code, status = _status_for_outcome(outcome)
    payload: dict[str, Any] = {
        "allowed": bool(outcome.allowed),
        "command_id": outcome.command_id,
        "duplicate": bool(outcome.duplicate),
        "executed": bool(outcome.executed),
        "reason_code": str(outcome.reason_code),
        "status": status,
    }
    for key, value in (
        ("audit_ref", outcome.audit_ref),
        ("claim_ref", outcome.claim_ref),
        ("result_ref", outcome.result_ref),
        ("terminal_state", outcome.terminal_state),
        ("certainty", outcome.certainty),
    ):
        if value:
            payload[key] = str(value)
    return JSONResponse(status_code=status_code, content=payload)


def _status_for_outcome(outcome: ObsCommandIngressOutcome) -> tuple[int, str]:
    reason = str(outcome.reason_code or "")
    terminal_state = str(outcome.terminal_state or "")
    if reason in {
        "OBS_COMMAND_OUTCOME_FINALIZE_FAILED",
        "OBS_COMMAND_OUTCOME_JOURNAL_UNAVAILABLE",
        "OBS_COMMAND_OUTCOME_RECEIPT_MISSING",
        "OBS_COMMAND_RESULT_COMMIT_FAILED",
    }:
        return 503, "uncertain" if terminal_state == "UNCERTAIN" else "unavailable"
    if terminal_state in {"UNCERTAIN", "EXECUTING"}:
        return 503, "uncertain"
    if terminal_state == "SHADOW_EVALUATED":
        return 503, "shadow"
    if terminal_state == "SUCCEEDED":
        return 200, "duplicate" if outcome.duplicate else "completed"
    if outcome.allowed:
        return 200, "completed"
    if outcome.duplicate and terminal_state != "REJECTED":
        # A duplicate without its durable terminal receipt is never success.
        return 503, "unavailable"
    if outcome.executed and terminal_state != "REJECTED":
        return 503, "uncertain"
    if reason in {
        "SECURE_INGRESS_CREDENTIAL_MISSING",
        "SECURE_INGRESS_CREDENTIAL_INVALID",
        "SECURE_INGRESS_CREDENTIAL_EXPIRED",
        "SECURE_INGRESS_PRINCIPAL_INVALID",
    }:
        return 401, "rejected"
    if reason == "SECURE_INGRESS_SCOPE_DENIED":
        return 403, "rejected"
    if reason in {
        "SECURE_INGRESS_RATE_LIMITED",
        "SECURE_INGRESS_RATE_BACKEND_UNAVAILABLE",
    }:
        return (429, "rejected") if reason.endswith("RATE_LIMITED") else (503, "unavailable")
    if reason in {
        "SECURE_INGRESS_REPLAY_DETECTED",
        "SECURE_INGRESS_IDEMPOTENCY_CONFLICT",
        "OBS_COMMAND_OUTCOME_CONFLICT",
    }:
        return 409, "conflict"
    if reason in {
        "SECURE_INGRESS_SCHEMA_INVALID",
        "SECURE_INGRESS_IDEMPOTENCY_KEY_MISSING",
    }:
        return 400, "rejected"
    if reason == "OBS_COMMAND_NOT_ALLOWED":
        return 404, "rejected"
    if reason in {
        "OBS_COMMAND_IDENTITY_INVALID",
        "OBS_COMMAND_PROOF_MISSING",
    }:
        return 401, "rejected"
    if reason in {
        "OBS_COMMAND_IDEMPOTENCY_MISSING",
        "OBS_COMMAND_NONCE_MISSING",
        "OBS_COMMAND_SECURITY_HEADER_AMBIGUOUS",
        "OBS_COMMAND_SECURITY_HEADER_INVALID",
    }:
        return 400, "rejected"
    if reason == "OBS_COMMAND_DOMAIN_REJECTED":
        return 422, "rejected"
    if reason == "OBS_COMMAND_BODY_TOO_LARGE":
        return 413, "rejected"
    if reason.startswith("OBS_COMMAND_") and any(
        marker in reason
        for marker in (
            "BODY_INVALID",
            "DUPLICATE_FIELD",
            "ENVELOPE_INVALID",
            "NONFINITE_VALUE",
            "PATH_BINDING_INVALID",
            "PATH_BODY_MISMATCH",
            "PAYLOAD_",
            "SCHEMA_UNSUPPORTED",
        )
    ):
        return 400, "rejected"
    return 503, "unavailable"


def _error_response(
    *,
    status_code: int,
    reason_code: str,
    command_id: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=int(status_code),
        content={
            "allowed": False,
            "command_id": str(command_id),
            "duplicate": False,
            "executed": False,
            "reason_code": str(reason_code),
            "status": "unavailable" if int(status_code) == 503 else "rejected",
        },
    )


__all__ = [
    "ObsCommandIngressProvider",
    "create_obs_command_http_router",
]
