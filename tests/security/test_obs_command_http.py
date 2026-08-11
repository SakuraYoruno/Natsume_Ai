from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from natsume_contracts.identity import (
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)
from host_brain.observability.command_http import create_obs_command_http_router
from host_brain.observability.secure_command_ingress import (
    MAX_OBS_COMMAND_BODY_BYTES,
    ObsCommandIngressOutcome,
    ObsCommandIngressRequest,
)


COMMAND_ID = "sys_mode.switch"
PATH = f"/v1/host/obs-commands/{COMMAND_ID}"
NOW = 1_800_000_000_000
PROOF = "a" * 64


@dataclass
class _FakeIngress:
    outcome: object
    requests: list[ObsCommandIngressRequest] = field(default_factory=list)
    fail: bool = False

    async def execute(self, request: ObsCommandIngressRequest):  # noqa: ANN202
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("must not cross HTTP boundary")
        if request.contract_error_reason:
            return ObsCommandIngressOutcome(
                allowed=False,
                executed=False,
                duplicate=False,
                reason_code=request.contract_error_reason,
                command_id=request.command_id,
            )
        return self.outcome


def _identity(
    *,
    principal_id: str = "service.obs.http.test",
    key_id: str = "obs.command.key.test",
) -> IngressIdentityEnvelope:
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=key_id,
        algorithm="hmac-sha256",
        proof_id="obs.http.proof.1",
        issued_at_epoch_ms=NOW,
        expires_at_epoch_ms=NOW + 60_000,
    )
    return IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=principal_id,
            kind=PrincipalKind.SERVICE,
            tenant_id="tenant.obs.http.test",
            granted_scopes=("obs:write", "obs:sys-mode:write"),
        ),
        credential_proof=metadata,
        audience="obs",
        request_id="obs.http.request.1",
        requested_scopes=("obs:write", "obs:sys-mode:write"),
    )


def _headers(
    *,
    identity_envelope: IngressIdentityEnvelope | None = None,
) -> list[tuple[str, str]]:
    identity = (identity_envelope or _identity()).to_canonical_json().encode("utf-8")
    encoded = base64.urlsafe_b64encode(identity).rstrip(b"=").decode("ascii")
    return [
        ("x-natsume-identity", encoded),
        ("x-natsume-proof", PROOF),
        ("x-natsume-idempotency-key", "obs.http.idem.1"),
        ("x-natsume-nonce", "obs.http.nonce.1"),
        ("x-natsume-correlation-id", "obs.http.correlation.1"),
    ]


def _outcome(
    *,
    allowed: bool = True,
    executed: bool = True,
    duplicate: bool = False,
    reason_code: str = "OBS_COMMAND_COMPLETED",
    terminal_state: str | None = None,
    certainty: str | None = None,
) -> ObsCommandIngressOutcome:
    return ObsCommandIngressOutcome(
        allowed=allowed,
        executed=executed,
        duplicate=duplicate,
        reason_code=reason_code,
        command_id=COMMAND_ID,
        principal_id="service.obs.http.test",
        claim_ref="claim://obs/http/1",
        audit_ref="secure-ingress-audit://obs/http/1",
        result_ref="obs-command-result://obs/http/1",
        result={"secret": "must-never-cross-http-boundary"},
        terminal_state=terminal_state,
        certainty=certainty,
    )


def _client(fake: _FakeIngress | None, *, provider=None) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    resolved_provider = provider
    if resolved_provider is None and fake is not None:
        resolved_provider = lambda: fake
    app.include_router(
        create_obs_command_http_router(
            ingress_provider=resolved_provider,
            clock_ms=lambda: NOW,
        )
    )
    return TestClient(app)


async def _asgi_post(
    *,
    provider,  # noqa: ANN001
    chunks: list[bytes],
    content_length: str | None = None,
    headers: list[tuple[str, str]] | None = None,
    client: tuple[str, int] | None = ("127.0.0.1", 12345),
) -> tuple[int, dict, int]:
    app = FastAPI()
    app.include_router(
        create_obs_command_http_router(
            ingress_provider=provider,
            clock_ms=lambda: NOW,
        )
    )
    raw_headers = [
        (name.lower().encode("ascii"), value.encode("ascii"))
        for name, value in (_headers() if headers is None else headers)
    ]
    if content_length is not None:
        raw_headers.append((b"content-length", content_length.encode("ascii")))

    receive_calls = 0
    sent: list[dict] = []

    async def receive() -> dict:
        nonlocal receive_calls
        index = receive_calls
        receive_calls += 1
        if index >= len(chunks):
            raise AssertionError("endpoint received beyond the supplied ASGI body")
        return {
            "type": "http.request",
            "body": chunks[index],
            "more_body": index < len(chunks) - 1,
        }

    async def send(message: dict) -> None:
        sent.append(dict(message))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": PATH,
        "raw_path": PATH.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "server": ("testserver", 80),
    }
    if client is not None:
        scope["client"] = client
    await app(scope, receive, send)
    status_code = next(
        int(message["status"])
        for message in sent
        if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        bytes(message.get("body", b""))
        for message in sent
        if message["type"] == "http.response.body"
    )
    return status_code, json.loads(response_body), receive_calls


def test_router_exposes_only_contract_query_and_single_command_path() -> None:
    router = create_obs_command_http_router(ingress_provider=None)

    paths = [
        (route.path, frozenset(route.methods or ()))
        for route in router.routes
    ]

    assert paths == [
        (
            "/v1/host/operator-command-gateway/openapi.json",
            frozenset({"GET"}),
        ),
        ("/v1/host/obs-commands/{command_id}", frozenset({"POST"}))
    ]


def test_default_missing_provider_is_503_fail_closed_even_without_headers() -> None:
    client = _client(None)

    response = client.post(PATH, content=b"not-json")

    assert response.status_code == 503
    assert response.json()["reason_code"] == "OBS_COMMAND_INGRESS_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_missing_provider_returns_before_receiving_any_body_chunk() -> None:
    status_code, body, receive_calls = await _asgi_post(
        provider=None,
        chunks=[b"must-not-be-received"],
    )

    assert status_code == 503
    assert body["reason_code"] == "OBS_COMMAND_INGRESS_PROVIDER_UNAVAILABLE"
    assert receive_calls == 0


@pytest.mark.asyncio
async def test_forged_small_content_length_cannot_bypass_stream_limit() -> None:
    fake = _FakeIngress(_outcome())
    status_code, body, receive_calls = await _asgi_post(
        provider=lambda: fake,
        chunks=[b"x" * (MAX_OBS_COMMAND_BODY_BYTES + 1)],
        content_length="1",
    )

    assert status_code == 413
    assert body["reason_code"] == "OBS_COMMAND_BODY_TOO_LARGE"
    assert receive_calls == 1
    assert len(fake.requests) == 1
    assert fake.requests[0].contract_error_reason == "OBS_COMMAND_BODY_TOO_LARGE"
    assert fake.requests[0].raw_body == b""


@pytest.mark.asyncio
async def test_no_content_length_multichunk_overflow_stops_receiving_at_limit() -> None:
    fake = _FakeIngress(_outcome())
    half = MAX_OBS_COMMAND_BODY_BYTES // 2
    status_code, body, receive_calls = await _asgi_post(
        provider=lambda: fake,
        chunks=[b"a" * half, b"b" * half, b"c", b"must-not-be-received"],
    )

    assert status_code == 413
    assert body["reason_code"] == "OBS_COMMAND_BODY_TOO_LARGE"
    assert receive_calls == 3
    assert len(fake.requests) == 1
    assert fake.requests[0].contract_error_reason == "OBS_COMMAND_BODY_TOO_LARGE"
    assert fake.requests[0].raw_body == b""


@pytest.mark.asyncio
async def test_forged_large_content_length_does_not_reject_small_exact_body() -> None:
    fake = _FakeIngress(_outcome())
    raw_body = b' {"opaque":"exact bytes"}\n'
    status_code, body, receive_calls = await _asgi_post(
        provider=lambda: fake,
        chunks=[raw_body],
        content_length=str(MAX_OBS_COMMAND_BODY_BYTES + 99_999),
    )

    assert status_code == 200
    assert body["reason_code"] == "OBS_COMMAND_COMPLETED"
    assert receive_calls == 1
    assert len(fake.requests) == 1
    assert fake.requests[0].raw_body == raw_body


@pytest.mark.asyncio
async def test_rotating_claimed_identity_and_key_cannot_rotate_transport_bucket() -> None:
    fake = _FakeIngress(_outcome())
    peer = ("203.0.113.41", 43100)
    identities = (
        _identity(principal_id="service.obs.operator.alpha", key_id="obs.key.alpha"),
        _identity(principal_id="service.obs.operator.beta", key_id="obs.key.beta"),
    )

    for identity in identities:
        status_code, _, _ = await _asgi_post(
            provider=lambda: fake,
            chunks=[b"opaque"],
            headers=_headers(identity_envelope=identity),
            client=peer,
        )
        assert status_code == 200

    assert [request.transport_key for request in fake.requests] == [
        "http-peer:203.0.113.41",
        "http-peer:203.0.113.41",
    ]
    assert [request.identity for request in fake.requests] == list(identities)


@pytest.mark.asyncio
async def test_forwarded_headers_cannot_override_server_observed_peer() -> None:
    fake = _FakeIngress(_outcome())
    headers = _headers() + [
        ("forwarded", 'for="198.51.100.200"'),
        ("x-forwarded-for", "198.51.100.201, 198.51.100.202"),
        ("x-real-ip", "198.51.100.203"),
    ]

    status_code, _, _ = await _asgi_post(
        provider=lambda: fake,
        chunks=[b"opaque"],
        headers=headers,
        client=("10.23.45.67", 9876),
    )

    assert status_code == 200
    assert fake.requests[0].transport_key == "http-peer:10.23.45.67"


@pytest.mark.asyncio
async def test_missing_scope_client_uses_one_unknown_bucket_for_valid_and_invalid_identity() -> None:
    fake = _FakeIngress(_outcome())
    valid_status, _, _ = await _asgi_post(
        provider=lambda: fake,
        chunks=[b"valid-identity"],
        client=None,
    )
    invalid_headers = [
        (name, "%%%" if name == "x-natsume-identity" else value)
        for name, value in _headers()
    ]
    invalid_status, invalid_body, _ = await _asgi_post(
        provider=lambda: fake,
        chunks=[b"invalid-identity"],
        headers=invalid_headers,
        client=None,
    )

    assert valid_status == 200
    assert invalid_status == 401
    assert invalid_body["reason_code"] == "OBS_COMMAND_IDENTITY_INVALID"
    assert [request.transport_key for request in fake.requests] == [
        "http-peer:unknown",
        "http-peer:unknown",
    ]


def test_exact_raw_body_and_signed_path_are_forwarded_without_json_parsing() -> None:
    fake = _FakeIngress(_outcome())
    client = _client(fake)
    raw_body = b'{"duplicate":1,"duplicate":2,"opaque":"body-secret"}'

    response = client.post(PATH, content=raw_body, headers=_headers())

    assert response.status_code == 200
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.command_id == COMMAND_ID
    assert request.normalized_path == PATH
    assert request.method == "POST"
    assert request.raw_body == raw_body
    assert request.received_at_epoch_ms == NOW
    assert request.identity == _identity()
    assert request.credential is not None
    assert request.credential.metadata == request.identity.credential_proof
    assert request.credential.raw_proof == PROOF
    assert request.idempotency_key == "obs.http.idem.1"
    assert request.nonce == "obs.http.nonce.1"
    assert request.correlation_id == "obs.http.correlation.1"
    assert request.transport_key == "http-peer:testclient"
    response_text = response.text
    assert "body-secret" not in response_text
    assert PROOF not in response_text
    assert "must-never-cross-http-boundary" not in response_text


@pytest.mark.parametrize("duplicate_name", ["x-natsume-proof", "x-natsume-extra"])
def test_duplicate_natsume_headers_are_rejected_from_raw_asgi_headers(
    duplicate_name: str,
) -> None:
    fake = _FakeIngress(_outcome())
    client = _client(fake)
    headers = _headers() + [
        (duplicate_name, "first"),
        (duplicate_name.upper(), "second"),
    ]

    response = client.post(PATH, content=b"opaque", headers=headers)

    assert response.status_code == 400
    assert response.json()["reason_code"] == "OBS_COMMAND_SECURITY_HEADER_AMBIGUOUS"
    assert len(fake.requests) == 1
    assert fake.requests[0].contract_error_reason == "OBS_COMMAND_SECURITY_HEADER_AMBIGUOUS"
    assert fake.requests[0].raw_body == b"opaque"
    assert fake.requests[0].credential is None


@pytest.mark.parametrize(
    ("headers", "expected_status", "expected_reason"),
    [
        (
            [(name, value) for name, value in _headers() if name != "x-natsume-proof"],
            401,
            "OBS_COMMAND_PROOF_MISSING",
        ),
        (
            [
                (name, "%%%" if name == "x-natsume-identity" else value)
                for name, value in _headers()
            ],
            401,
            "OBS_COMMAND_IDENTITY_INVALID",
        ),
    ],
)
def test_missing_proof_and_invalid_identity_fail_before_ingress(
    headers: list[tuple[str, str]],
    expected_status: int,
    expected_reason: str,
) -> None:
    fake = _FakeIngress(_outcome())
    response = _client(fake).post(PATH, content=b"opaque", headers=headers)

    assert response.status_code == expected_status
    assert response.json()["reason_code"] == expected_reason
    assert len(fake.requests) == 1
    assert fake.requests[0].contract_error_reason == expected_reason
    assert fake.requests[0].credential is None


@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_body_status"),
    [
        (_outcome(), 200, "completed"),
        (
            _outcome(
                allowed=True,
                executed=False,
                duplicate=True,
                reason_code="OBS_COMMAND_COMPLETED",
                terminal_state="SUCCEEDED",
                certainty="EXECUTED",
            ),
            200,
            "duplicate",
        ),
        (
            _outcome(
                allowed=False,
                executed=False,
                duplicate=True,
                reason_code="SECURE_INGRESS_IDEMPOTENCY_DUPLICATE",
            ),
            503,
            "unavailable",
        ),
        (
            _outcome(
                allowed=False,
                executed=False,
                reason_code="SECURE_INGRESS_SCOPE_DENIED",
            ),
            403,
            "rejected",
        ),
        (
            _outcome(
                allowed=False,
                executed=False,
                reason_code="SECURE_INGRESS_RATE_LIMITED",
            ),
            429,
            "rejected",
        ),
        (
            _outcome(
                allowed=False,
                executed=False,
                reason_code="SECURE_INGRESS_REPLAY_DETECTED",
            ),
            409,
            "conflict",
        ),
        (
            _outcome(
                allowed=False,
                executed=False,
                reason_code="SECURE_INGRESS_SCHEMA_INVALID",
            ),
            400,
            "rejected",
        ),
        (
            _outcome(
                allowed=False,
                executed=False,
                reason_code="OBS_COMMAND_NOT_ALLOWED",
            ),
            404,
            "rejected",
        ),
        (
            _outcome(
                allowed=False,
                executed=False,
                reason_code="OBS_COMMAND_DOMAIN_REJECTED",
            ),
            422,
            "rejected",
        ),
        (
            _outcome(
                allowed=False,
                executed=True,
                duplicate=True,
                reason_code="OBS_COMMAND_DOMAIN_REJECTED",
                terminal_state="REJECTED",
                certainty="EXECUTED",
            ),
            422,
            "rejected",
        ),
        (
            _outcome(
                allowed=False,
                executed=False,
                duplicate=True,
                reason_code="OBS_COMMAND_DUPLICATE_FIELD",
                terminal_state="REJECTED",
                certainty="NOT_EXECUTED",
            ),
            400,
            "rejected",
        ),
        (
            _outcome(
                allowed=False,
                executed=False,
                reason_code="OBS_COMMAND_DOMAIN_PORT_UNAVAILABLE",
            ),
            503,
            "unavailable",
        ),
    ],
)
def test_secret_free_stable_outcome_status_mapping(
    outcome: ObsCommandIngressOutcome,
    expected_status: int,
    expected_body_status: str,
) -> None:
    response = _client(_FakeIngress(outcome)).post(
        PATH,
        content=b"opaque-secret-body",
        headers=_headers(),
    )

    assert response.status_code == expected_status
    body = response.json()
    assert body["status"] == expected_body_status
    assert body["reason_code"] == outcome.reason_code
    assert set(body) <= {
        "allowed", "audit_ref", "claim_ref", "command_id", "duplicate",
        "certainty", "executed", "reason_code", "result_ref", "status",
        "terminal_state",
    }
    assert "opaque-secret-body" not in response.text
    assert "must-never-cross-http-boundary" not in response.text


def test_waiting_review_returns_202_with_only_durable_continuation_ref() -> None:
    continuation_ref = (
        "governed-review://graph-operator/1/review-operator"
        "?binding=sha256:" + "a" * 64
    )
    pending = ObsCommandIngressOutcome(
        allowed=True,
        executed=False,
        duplicate=False,
        reason_code="OBS_COMMAND_WAITING_REVIEW",
        command_id=COMMAND_ID,
        principal_id="service.obs.http.test",
        claim_ref="claim://obs/http/pending",
        audit_ref="secure-ingress-audit://obs/http/pending",
        result_ref="obs-command-outcome://obs/http/pending",
        result={
            "continuation_ref": continuation_ref,
            "private_review": "must-never-cross-http-boundary",
        },
        terminal_state="WAITING_REVIEW",
        certainty="NOT_EXECUTED",
    )

    response = _client(_FakeIngress(pending)).post(
        PATH,
        content=b"opaque-secret-body",
        headers=_headers(),
    )

    assert response.status_code == 202
    assert response.json() == {
        "allowed": True,
        "audit_ref": "secure-ingress-audit://obs/http/pending",
        "certainty": "NOT_EXECUTED",
        "claim_ref": "claim://obs/http/pending",
        "command_id": COMMAND_ID,
        "continuation_ref": continuation_ref,
        "duplicate": False,
        "executed": False,
        "reason_code": "OBS_COMMAND_WAITING_REVIEW",
        "result_ref": "obs-command-outcome://obs/http/pending",
        "status": "pending",
        "terminal_state": "WAITING_REVIEW",
    }
    assert "must-never-cross-http-boundary" not in response.text


def test_provider_or_ingress_exception_is_secret_free_503() -> None:
    def _raise_provider():
        raise RuntimeError("provider-secret")

    provider_failure = _client(None, provider=_raise_provider).post(
        PATH,
        content=b"body-secret",
        headers=_headers(),
    )
    assert provider_failure.status_code == 503
    assert "provider-secret" not in provider_failure.text

    fake = _FakeIngress(_outcome(), fail=True)
    ingress_failure = _client(fake).post(
        PATH,
        content=b"body-secret",
        headers=_headers(),
    )
    assert ingress_failure.status_code == 503
    assert ingress_failure.json()["reason_code"] == "OBS_COMMAND_INGRESS_UNAVAILABLE"
    assert "body-secret" not in ingress_failure.text
