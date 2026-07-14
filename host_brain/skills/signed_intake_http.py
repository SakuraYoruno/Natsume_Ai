"""FastAPI boundary for authenticated Skill/Provider bundle intake."""

from __future__ import annotations

import base64
from collections.abc import Callable
import time
from typing import Any, Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from natsume_contracts.identity import CredentialProof, parse_identity_envelope

from .signed_intake_ingress import (
    MAX_SKILL_PROVIDER_INTAKE_BODY_BYTES,
    SKILL_PROVIDER_INTAKE_PATH,
    SecureSignedSkillProviderIntake,
    SignedSkillProviderIntakeOutcome,
    SignedSkillProviderIntakeRequest,
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


class SignedSkillProviderIntakeProvider(Protocol):
    def __call__(self) -> SecureSignedSkillProviderIntake | None: ...


class _HttpContractError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


def create_signed_skill_provider_intake_router(
    *,
    ingress_provider: (
        SignedSkillProviderIntakeProvider
        | Callable[[], SecureSignedSkillProviderIntake | None]
        | None
    ) = None,
    clock_ms: Callable[[], int] | None = None,
) -> APIRouter:
    """Create one unmounted route; protected runtime composition owns wiring."""

    router = APIRouter(tags=["host-skill-provider-intake"])
    now_ms = clock_ms or (lambda: int(time.time() * 1000))

    @router.post(SKILL_PROVIDER_INTAKE_PATH)
    async def post_signed_skill_provider_intake(request: Request) -> JSONResponse:
        if ingress_provider is None:
            return _error_response(
                status_code=503,
                reason_code="SKILL_PROVIDER_INTAKE_PROVIDER_UNAVAILABLE",
            )
        try:
            ingress = ingress_provider()
        except Exception:
            ingress = None
        if ingress is None or not callable(getattr(ingress, "execute", None)):
            return _error_response(
                status_code=503,
                reason_code="SKILL_PROVIDER_INTAKE_PROVIDER_UNAVAILABLE",
            )

        transport_key = _http_peer_transport_key(request)
        try:
            raw_body = await _read_bounded_body(request)
        except _HttpContractError as exc:
            return await _contract_rejection(
                ingress=ingress,
                request=request,
                raw_body=b"",
                identity=None,
                reason_code=exc.reason_code,
                transport_key=transport_key,
                now_ms=now_ms,
                fallback_status=exc.status_code,
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
                maximum=_MAX_PROOF_HEADER_BYTES,
                reason_code="SKILL_PROVIDER_INTAKE_PROOF_MISSING",
            )
            idempotency_key = _required_ascii_header(
                headers,
                _IDEMPOTENCY_HEADER,
                maximum=_MAX_TOKEN_HEADER_BYTES,
                reason_code="SKILL_PROVIDER_INTAKE_IDEMPOTENCY_MISSING",
            )
            nonce = _required_ascii_header(
                headers,
                _NONCE_HEADER,
                maximum=_MAX_TOKEN_HEADER_BYTES,
                reason_code="SKILL_PROVIDER_INTAKE_NONCE_MISSING",
            )
            correlation_id = _optional_ascii_header(
                headers,
                _CORRELATION_HEADER,
                maximum=_MAX_TOKEN_HEADER_BYTES,
            )
            credential = CredentialProof(
                metadata=identity.credential_proof,
                raw_proof=proof,
            )
        except _HttpContractError as exc:
            return await _contract_rejection(
                ingress=ingress,
                request=request,
                raw_body=raw_body,
                identity=identity,
                reason_code=exc.reason_code,
                transport_key=transport_key,
                now_ms=now_ms,
                fallback_status=exc.status_code,
            )
        except Exception:
            return await _contract_rejection(
                ingress=ingress,
                request=request,
                raw_body=raw_body,
                identity=identity,
                reason_code="SKILL_PROVIDER_INTAKE_IDENTITY_INVALID",
                transport_key=transport_key,
                now_ms=now_ms,
                fallback_status=401,
            )

        intake_request = SignedSkillProviderIntakeRequest(
            raw_body=raw_body,
            identity=identity,
            credential=credential,
            idempotency_key=idempotency_key,
            nonce=nonce,
            received_at_epoch_ms=int(now_ms()),
            transport_key=transport_key,
            method=str(request.method).upper(),
            normalized_path=str(request.url.path),
            correlation_id=correlation_id,
        )
        try:
            outcome = await ingress.execute(intake_request)
        except Exception:
            return _error_response(
                status_code=503,
                reason_code="SKILL_PROVIDER_INTAKE_UNAVAILABLE",
            )
        if not isinstance(outcome, SignedSkillProviderIntakeOutcome):
            return _error_response(
                status_code=503,
                reason_code="SKILL_PROVIDER_INTAKE_RESULT_INVALID",
            )
        return _outcome_response(outcome)

    return router


async def _read_bounded_body(request: Request) -> bytes:
    buffered = bytearray()
    async for chunk in request.stream():
        if not chunk:
            continue
        if len(chunk) > MAX_SKILL_PROVIDER_INTAKE_BODY_BYTES - len(buffered):
            raise _HttpContractError(
                "SKILL_PROVIDER_INTAKE_BODY_TOO_LARGE",
                status_code=413,
            )
        buffered.extend(chunk)
    return bytes(buffered)


def _single_natsume_headers(request: Request) -> dict[bytes, bytes]:
    selected: dict[bytes, bytes] = {}
    for raw_name, raw_value in list(request.scope.get("headers") or ()):
        name = bytes(raw_name).strip().lower()
        if not name.startswith(_SECURITY_HEADER_PREFIX):
            continue
        if name in selected:
            raise _HttpContractError(
                "SKILL_PROVIDER_INTAKE_SECURITY_HEADER_AMBIGUOUS",
                status_code=400,
            )
        selected[name] = bytes(raw_value).strip()
    return selected


def _decode_identity(raw: bytes) -> bytes:
    if not raw or len(raw) > _MAX_IDENTITY_HEADER_BYTES:
        raise _HttpContractError(
            "SKILL_PROVIDER_INTAKE_IDENTITY_INVALID",
            status_code=401,
        )
    try:
        text = raw.decode("ascii", errors="strict")
        return base64.b64decode(
            text + "=" * (-len(text) % 4),
            altchars=b"-_",
            validate=True,
        )
    except Exception as exc:
        raise _HttpContractError(
            "SKILL_PROVIDER_INTAKE_IDENTITY_INVALID",
            status_code=401,
        ) from exc


def _required_ascii_header(
    headers: dict[bytes, bytes],
    name: bytes,
    *,
    maximum: int,
    reason_code: str,
) -> str:
    raw = headers.get(name, b"")
    if not raw or len(raw) > maximum:
        raise _HttpContractError(reason_code, status_code=400)
    try:
        value = raw.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise _HttpContractError(reason_code, status_code=400) from exc
    if not value:
        raise _HttpContractError(reason_code, status_code=400)
    return value


def _optional_ascii_header(
    headers: dict[bytes, bytes],
    name: bytes,
    *,
    maximum: int,
) -> str | None:
    raw = headers.get(name)
    if raw is None:
        return None
    if not raw or len(raw) > maximum:
        raise _HttpContractError(
            "SKILL_PROVIDER_INTAKE_SECURITY_HEADER_INVALID",
            status_code=400,
        )
    try:
        value = raw.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise _HttpContractError(
            "SKILL_PROVIDER_INTAKE_SECURITY_HEADER_INVALID",
            status_code=400,
        ) from exc
    if not value:
        raise _HttpContractError(
            "SKILL_PROVIDER_INTAKE_SECURITY_HEADER_INVALID",
            status_code=400,
        )
    return value


def _http_peer_transport_key(request: Request) -> str:
    client = request.scope.get("client")
    if not isinstance(client, (tuple, list)) or not client:
        return "http-peer:unknown"
    host = client[0]
    if not isinstance(host, str):
        return "http-peer:unknown"
    normalized = host.strip().lower()
    try:
        encoded = normalized.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return "http-peer:unknown"
    if (
        not encoded
        or len(encoded) > _MAX_HTTP_PEER_HOST_BYTES
        or any(byte < 0x20 or byte == 0x7F for byte in encoded)
    ):
        return "http-peer:unknown"
    return f"http-peer:{normalized}"


async def _contract_rejection(
    *,
    ingress: SecureSignedSkillProviderIntake,
    request: Request,
    raw_body: bytes,
    identity: Any,
    reason_code: str,
    transport_key: str,
    now_ms: Callable[[], int],
    fallback_status: int,
) -> JSONResponse:
    rejected = SignedSkillProviderIntakeRequest(
        raw_body=bytes(raw_body),
        identity=identity,
        credential=None,
        idempotency_key="invalid-contract",
        nonce="invalid-contract",
        received_at_epoch_ms=int(now_ms()),
        transport_key=transport_key,
        method=str(request.method).upper(),
        normalized_path=str(request.url.path),
        contract_error_reason=reason_code,
    )
    try:
        outcome = await ingress.execute(rejected)
    except Exception:
        return _error_response(
            status_code=503,
            reason_code="SKILL_PROVIDER_INTAKE_INGRESS_UNAVAILABLE",
        )
    if not isinstance(outcome, SignedSkillProviderIntakeOutcome):
        return _error_response(
            status_code=503,
            reason_code="SKILL_PROVIDER_INTAKE_RESULT_INVALID",
        )
    response = _outcome_response(outcome)
    if response.status_code == 503 and outcome.reason_code == reason_code:
        response.status_code = int(fallback_status)
    return response


def _outcome_response(outcome: SignedSkillProviderIntakeOutcome) -> JSONResponse:
    status_code, status = _http_status(outcome)
    content: dict[str, Any] = {
        "activation_performed": bool(outcome.activation_performed),
        "allowed": bool(outcome.allowed),
        "duplicate": bool(outcome.duplicate),
        "executed": bool(outcome.executed),
        "import_performed": bool(outcome.import_performed),
        "quarantined": bool(outcome.quarantined),
        "reason_code": str(outcome.reason_code),
        "staged": bool(outcome.staged),
        "status": status,
    }
    for key, value in (
        ("bundle_id", outcome.bundle_id),
        ("bundle_digest", outcome.bundle_digest),
        ("repository_state", outcome.repository_state),
        ("result_ref", outcome.result_ref),
    ):
        if value is not None:
            content[key] = str(value)
    return JSONResponse(status_code=status_code, content=content)


def _http_status(outcome: SignedSkillProviderIntakeOutcome) -> tuple[int, str]:
    reason = str(outcome.reason_code)
    if outcome.duplicate:
        return 200, "duplicate"
    if outcome.allowed:
        return 202, "accepted"
    if reason in {
        "SECURE_INGRESS_CREDENTIAL_MISSING",
        "SECURE_INGRESS_CREDENTIAL_INVALID",
        "SECURE_INGRESS_CREDENTIAL_EXPIRED",
        "SECURE_INGRESS_PRINCIPAL_INVALID",
        "SKILL_PROVIDER_INTAKE_IDENTITY_INVALID",
        "SKILL_PROVIDER_INTAKE_PROOF_MISSING",
    }:
        return 401, "rejected"
    if reason == "SECURE_INGRESS_SCOPE_DENIED":
        return 403, "rejected"
    if reason == "SECURE_INGRESS_RATE_LIMITED":
        return 429, "rejected"
    if reason in {
        "SECURE_INGRESS_IDEMPOTENCY_CONFLICT",
        "SECURE_INGRESS_REPLAY_DETECTED",
    }:
        return 409, "conflict"
    if reason == "SKILL_PROVIDER_INTAKE_BODY_TOO_LARGE":
        return 413, "rejected"
    if reason.startswith("SKILL_PROVIDER_INTAKE_") and any(
        marker in reason
        for marker in (
            "BODY_INVALID",
            "DUPLICATE_FIELD",
            "IDEMPOTENCY_MISSING",
            "KIND_INVALID",
            "NONCE_MISSING",
            "NONFINITE_VALUE",
            "PATH_INVALID",
            "PUBLISHER_INVALID",
            "SCHEMA_UNSUPPORTED",
            "SECURITY_HEADER",
            "SOURCE_REF_INVALID",
        )
    ):
        return 400, "rejected"
    if reason.startswith("SUPPLY_CHAIN_"):
        return 422, "rejected"
    return 503, "unavailable"


def _error_response(*, status_code: int, reason_code: str) -> JSONResponse:
    return JSONResponse(
        status_code=int(status_code),
        content={
            "activation_performed": False,
            "allowed": False,
            "duplicate": False,
            "executed": False,
            "import_performed": False,
            "quarantined": False,
            "reason_code": str(reason_code),
            "staged": False,
            "status": "unavailable" if int(status_code) == 503 else "rejected",
        },
    )


__all__ = [
    "SignedSkillProviderIntakeProvider",
    "create_signed_skill_provider_intake_router",
]
