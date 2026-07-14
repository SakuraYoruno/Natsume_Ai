"""FastAPI protocol boundary for the authenticated OBS command plane.

This module owns no domain handlers and registers no global router.  Runtime
composition must explicitly provide ``SecureObsCommandIngress`` and include the
returned router in the protected Host router registry.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
import json
import os
import secrets
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
    parse_identity_envelope,
)
from host_brain.secure_ingress import build_hmac_sha256_signature

from .command_ports import (
    AUTHORITY_PAYLOAD_FIELDS,
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandPortRegistry,
    ObsCommandSpec,
)
from .secure_command_ingress import (
    MAX_OBS_COMMAND_BODY_BYTES,
    ObsCommandIngressOutcome,
    ObsCommandIngressRequest,
    SecureObsCommandIngress,
    build_obs_command_secure_request,
)
from .operator_command_gateway import bind_operator_command_runtime
from .operator_query_gateway import OperatorQueryGateway


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
_HOST_HMAC_SECRETS_ENV = "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON"
_OPERATOR_BFF_ALLOWED_ORIGINS_ENV = "NATSUME_OPERATOR_BFF_ALLOWED_ORIGINS"
OPERATOR_COMMAND_GATEWAY_VERSION = "natsume.operator-command-gateway.v1"


class ObsCommandIngressProvider(Protocol):
    def __call__(self) -> SecureObsCommandIngress | None: ...


class OperatorQueryGatewayProvider(Protocol):
    def __call__(self) -> OperatorQueryGateway | None: ...


class _HttpContractError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


def create_obs_command_http_router(
    *,
    ingress_provider: ObsCommandIngressProvider | Callable[[], SecureObsCommandIngress | None] | None = None,
    operator_query_gateway_provider: (
        OperatorQueryGatewayProvider | Callable[[], OperatorQueryGateway | None] | None
    ) = None,
    clock_ms: Callable[[], int] | None = None,
) -> APIRouter:
    """Create the one command route without registering it globally."""

    router = APIRouter(tags=["host-obs-commands"])
    now_ms = clock_ms or (lambda: int(time.time() * 1000))

    @router.get(
        "/v1/host/operator-command-gateway/openapi.json",
        include_in_schema=False,
    )
    async def get_operator_command_openapi() -> JSONResponse:
        """Return the deterministic public contract used to generate UI clients."""

        return JSONResponse(content=build_operator_command_openapi())

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
            with bind_operator_command_runtime(request.app.state, raw_body=raw_body):
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

    if operator_query_gateway_provider is not None:

        @router.post("/v1/host/operator-command-bff/{command_id}")
        async def post_operator_command_bff(
            command_id: str,
            request: Request,
        ) -> JSONResponse:
            """Same-origin operator-session adapter with server-only signing.

            The browser supplies a typed payload plus opaque replay keys.  Its
            HttpOnly session is resolved to a server-owned principal/scope
            binding, and HMAC proof material is created only in this process.
            """

            try:
                gateway = operator_query_gateway_provider()
            except Exception:
                gateway = None
            if not isinstance(gateway, OperatorQueryGateway):
                return _error_response(
                    status_code=503,
                    reason_code="OPERATOR_QUERY_GATEWAY_UNAVAILABLE",
                    command_id=command_id,
                )
            context = gateway.open_asgi_context(request.scope)
            try:
                context.require_scope("obs:write")
            except Exception as exc:
                status_code = int(getattr(exc, "status_code", 403) or 403)
                return _error_response(
                    status_code=status_code if status_code in {401, 403} else 403,
                    reason_code=(
                        "OPERATOR_SESSION_AUTH_REQUIRED"
                        if status_code == 401
                        else "OPERATOR_SESSION_SCOPE_DENIED"
                    ),
                    command_id=command_id,
                )

            policy_registry = ObsCommandPortRegistry()
            spec = policy_registry.resolve_spec(command_id)
            if spec is None:
                return _error_response(
                    status_code=404,
                    reason_code="OBS_COMMAND_NOT_ALLOWED",
                    command_id=command_id,
                )
            try:
                for required_scope in spec.required_scopes:
                    context.require_scope(required_scope)
            except Exception:
                return _error_response(
                    status_code=403,
                    reason_code="OPERATOR_SESSION_SCOPE_DENIED",
                    command_id=command_id,
                )

            try:
                _validate_bff_csrf(request)
                headers = _single_natsume_headers(request)
                # Identity/proof headers on this route would reintroduce
                # browser-held credentials and are therefore rejected.
                if _IDENTITY_HEADER in headers or _PROOF_HEADER in headers:
                    raise _HttpContractError(
                        "OPERATOR_BFF_CLIENT_PROOF_DENIED",
                        status_code=400,
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
                client_body = await _read_bounded_body(request)
                payload = _decode_bff_payload(client_body)
                signed_request = _build_bff_signed_request(
                    command_id=command_id,
                    payload=payload,
                    context=context,
                    idempotency_key=idempotency_key,
                    nonce=nonce,
                    correlation_id=correlation_id,
                    received_at_ms=int(now_ms()),
                    spec=spec,
                )
            except _HttpContractError as exc:
                return _error_response(
                    status_code=exc.status_code,
                    reason_code=exc.reason_code,
                    command_id=command_id,
                )
            except Exception:
                return _error_response(
                    status_code=503,
                    reason_code="OPERATOR_BFF_SIGNING_UNAVAILABLE",
                    command_id=command_id,
                )

            try:
                ingress = ingress_provider() if ingress_provider is not None else None
            except Exception:
                ingress = None
            if ingress is None or not callable(getattr(ingress, "execute", None)):
                return _error_response(
                    status_code=503,
                    reason_code="OBS_COMMAND_INGRESS_PROVIDER_UNAVAILABLE",
                    command_id=command_id,
                )
            try:
                with bind_operator_command_runtime(
                    request.app.state,
                    raw_body=signed_request.raw_body,
                ):
                    outcome = await ingress.execute(signed_request)
            except Exception:
                return _error_response(
                    status_code=503,
                    reason_code="OBS_COMMAND_INGRESS_UNAVAILABLE",
                    command_id=command_id,
                )
            return (
                _outcome_response(outcome)
                if isinstance(outcome, ObsCommandIngressOutcome)
                else _error_response(
                    status_code=503,
                    reason_code="OBS_COMMAND_INGRESS_RESULT_INVALID",
                    command_id=command_id,
                )
            )

    return router


def build_operator_command_openapi(
    registry: ObsCommandPortRegistry | None = None,
) -> dict[str, Any]:
    """Build the versioned, secret-free Operator Command OpenAPI contract.

    The server-owned command manifest is the only source of mutation DTOs.
    Generated clients therefore drift whenever a command ID, scope or payload
    field changes, while runtime credentials remain entirely out of the spec.
    """

    selected = registry or ObsCommandPortRegistry()
    specs = tuple(
        spec
        for command_id in selected.bound_command_ids
        if (spec := selected.resolve_spec(command_id)) is not None
    )
    if not specs:
        specs = tuple(
            spec
            for entry in selected.manifest()
            if (spec := selected.resolve_spec(str(entry["command_id"]))) is not None
        )
    paths = {
        f"/v1/host/obs-commands/{spec.command_id}": {
            "post": _operator_command_operation(spec)
        }
        for spec in sorted(specs, key=lambda item: item.command_id)
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Natsume Operator Command Gateway",
            "version": "1.0.0",
            "x-natsume-contract": OPERATOR_COMMAND_GATEWAY_VERSION,
        },
        "paths": paths,
        "components": {
            "securitySchemes": {
                header: {
                    "type": "apiKey",
                    "in": "header",
                    "name": name,
                }
                for header, name in (
                    ("identity", "X-Natsume-Identity"),
                    ("proof", "X-Natsume-Proof"),
                    ("idempotency", "X-Natsume-Idempotency-Key"),
                    ("nonce", "X-Natsume-Nonce"),
                )
            },
            "schemas": {
                "OperatorCommandReceipt": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "allowed", "command_id", "duplicate", "executed",
                        "reason_code", "status",
                    ],
                    "properties": {
                        "allowed": {"type": "boolean"},
                        "command_id": {"type": "string"},
                        "duplicate": {"type": "boolean"},
                        "executed": {"type": "boolean"},
                        "reason_code": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": [
                                "completed", "duplicate", "rejected",
                                "conflict", "uncertain", "unavailable", "shadow",
                            ],
                        },
                        "audit_ref": {"type": "string"},
                        "claim_ref": {"type": "string"},
                        "result_ref": {"type": "string"},
                        "terminal_state": {"type": "string"},
                        "certainty": {"type": "string"},
                    },
                }
            },
        },
    }


def _operator_command_operation(spec: ObsCommandSpec) -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {}
    schema = spec.payload_schema
    field_names = tuple(dict.fromkeys((*schema.required, *schema.optional)))
    for field in field_names:
        if field in AUTHORITY_PAYLOAD_FIELDS:
            continue
        if field in schema.string_fields:
            field_schema = {"type": "string", "maxLength": 4096}
        elif field in schema.mapping_fields:
            field_schema = {"type": "object"}
        elif field in schema.boolean_fields:
            field_schema = {"type": "boolean"}
        elif field in schema.integer_fields:
            field_schema = {"type": "integer"}
        elif field in schema.number_fields:
            field_schema = {"type": "number"}
        else:
            field_schema = {}
        properties[field] = field_schema
    required = [field for field in schema.required if field not in AUTHORITY_PAYLOAD_FIELDS]
    payload_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        payload_schema["required"] = required
    return {
        "operationId": "execute_" + spec.command_id.replace(".", "_"),
        "summary": f"Execute {spec.command_id}",
        "security": [
            {"identity": [], "proof": [], "idempotency": [], "nonce": []}
        ],
        "x-natsume-command-id": spec.command_id,
        "x-natsume-action": spec.action,
        "x-natsume-required-scopes": list(spec.required_scopes),
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["schema_version", "command_id", "payload"],
                        "properties": {
                            "schema_version": {
                                "type": "string",
                                "const": "natsume.obs-command.v1",
                            },
                            "command_id": {"type": "string", "const": spec.command_id},
                            "payload": payload_schema,
                        },
                    }
                }
            },
        },
        "responses": {
            str(status): {
                "description": description,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/OperatorCommandReceipt"}
                    }
                },
            }
            for status, description in (
                ("200", "Completed or replayed durable receipt"),
                ("400", "Invalid command contract"),
                ("401", "Invalid principal or signature"),
                ("403", "Insufficient scope"),
                ("409", "Idempotency or replay conflict"),
                ("503", "Unavailable or uncertain outcome"),
            )
        },
    }


def _decode_bff_payload(raw_body: bytes) -> dict[str, Any]:
    if not raw_body:
        raise _HttpContractError("OBS_COMMAND_BODY_INVALID", status_code=400)

    def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate field")
            output[str(key)] = value
        return output

    try:
        decoded = json.loads(
            raw_body.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
    except Exception as exc:
        raise _HttpContractError("OBS_COMMAND_BODY_INVALID", status_code=400) from exc
    if not isinstance(decoded, dict) or set(decoded) != {"payload"}:
        raise _HttpContractError("OBS_COMMAND_ENVELOPE_INVALID", status_code=400)
    payload = decoded.get("payload")
    if not isinstance(payload, dict):
        raise _HttpContractError("OBS_COMMAND_PAYLOAD_INVALID", status_code=400)
    return payload


def _validate_bff_csrf(request: Request) -> None:
    """Require a browser same-origin signal before using an operator cookie."""

    def _single_header(name: bytes) -> str | None:
        values = [
            bytes(value).decode("latin-1").strip()
            for raw_name, value in request.scope.get("headers") or ()
            if bytes(raw_name).lower() == name
        ]
        if len(values) != 1 or not values[0]:
            return None
        return values[0]

    origin = _single_header(b"origin")
    host = _single_header(b"host")
    fetch_site = _single_header(b"sec-fetch-site")
    if fetch_site is not None and fetch_site.lower() != "same-origin":
        raise _HttpContractError("OPERATOR_BFF_CSRF_DENIED", status_code=403)
    if origin is None or host is None:
        raise _HttpContractError("OPERATOR_BFF_CSRF_DENIED", status_code=403)
    try:
        parsed = urlsplit(origin)
    except Exception as exc:
        raise _HttpContractError("OPERATOR_BFF_CSRF_DENIED", status_code=403) from exc
    normalized_origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise _HttpContractError("OPERATOR_BFF_CSRF_DENIED", status_code=403)
    configured = {
        value.strip().lower().rstrip("/")
        for value in str(os.environ.get(_OPERATOR_BFF_ALLOWED_ORIGINS_ENV) or "").split(",")
        if value.strip()
    }
    same_host = parsed.netloc.lower() == host.lower()
    if not same_host and normalized_origin not in configured:
        raise _HttpContractError("OPERATOR_BFF_CSRF_DENIED", status_code=403)


def _build_bff_signed_request(
    *,
    command_id: str,
    payload: dict[str, Any],
    context: Any,
    idempotency_key: str,
    nonce: str,
    correlation_id: str | None,
    received_at_ms: int,
    spec: ObsCommandSpec,
) -> ObsCommandIngressRequest:
    authority = context.authority
    key_id = str(authority.key_id or "").strip()
    tenant_id = str(authority.tenant_id or "").strip()
    principal_id = str(authority.principal_id or "").strip()
    if not key_id or not tenant_id or not principal_id:
        raise _HttpContractError("OPERATOR_BFF_SIGNING_UNAVAILABLE", status_code=503)
    try:
        principal_kind = PrincipalKind(str(authority.principal_kind or "service").lower())
    except ValueError as exc:
        raise _HttpContractError("OPERATOR_BFF_SIGNING_UNAVAILABLE", status_code=503) from exc
    secret = _resolve_bff_hmac_secret(key_id)
    if secret is None:
        raise _HttpContractError("OPERATOR_BFF_SIGNING_UNAVAILABLE", status_code=503)

    request_suffix = secrets.token_hex(12)
    proof_suffix = secrets.token_hex(12)
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=key_id,
        algorithm="hmac-sha256",
        proof_id=f"operator-bff.proof.{proof_suffix}",
        issued_at_epoch_ms=received_at_ms,
        expires_at_epoch_ms=received_at_ms + 60_000,
    )
    required_scopes = tuple(sorted(set(spec.required_scopes)))
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=principal_id,
            kind=principal_kind,
            tenant_id=tenant_id,
            granted_scopes=required_scopes,
        ),
        credential_proof=metadata,
        audience="obs",
        request_id=f"operator-bff.request.{request_suffix}",
        requested_scopes=required_scopes,
    )
    command_body = json.dumps(
        {
            "schema_version": OBS_COMMAND_ENVELOPE_SCHEMA,
            "command_id": command_id,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    transport_ref = str(authority.auth_ref or "operator-auth://unavailable")
    unsigned = ObsCommandIngressRequest(
        command_id=command_id,
        raw_body=command_body,
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        idempotency_key=idempotency_key,
        nonce=nonce,
        received_at_epoch_ms=received_at_ms,
        transport_key=f"operator-session:{transport_ref.rsplit('/', 1)[-1]}",
        method="POST",
        normalized_path=f"/v1/host/obs-commands/{command_id}",
        correlation_id=correlation_id,
    )
    signature = build_hmac_sha256_signature(
        build_obs_command_secure_request(unsigned, spec),
        secret,
    )
    return ObsCommandIngressRequest(
        command_id=unsigned.command_id,
        raw_body=unsigned.raw_body,
        identity=unsigned.identity,
        credential=CredentialProof(metadata=metadata, raw_proof=signature),
        idempotency_key=unsigned.idempotency_key,
        nonce=unsigned.nonce,
        received_at_epoch_ms=unsigned.received_at_epoch_ms,
        transport_key=unsigned.transport_key,
        method=unsigned.method,
        normalized_path=unsigned.normalized_path,
        correlation_id=unsigned.correlation_id,
    )


def _resolve_bff_hmac_secret(key_id: str) -> str | None:
    raw = str(os.environ.get(_HOST_HMAC_SECRETS_ENV) or "").strip()
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except Exception:
        return None
    if not isinstance(decoded, dict):
        return None
    secret = decoded.get(str(key_id))
    if not isinstance(secret, str):
        return None
    try:
        size = len(secret.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        return None
    return secret if 32 <= size <= 4_096 else None


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
    "OPERATOR_COMMAND_GATEWAY_VERSION",
    "ObsCommandIngressProvider",
    "build_operator_command_openapi",
    "create_obs_command_http_router",
]
