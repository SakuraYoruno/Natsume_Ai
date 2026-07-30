from __future__ import annotations

import base64
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)
from host_brain.app.input_service_gateway import (
    complete_host_gateway_ingress,
    evaluate_host_gateway_ingress,
)
from host_brain.persistence.secure_ingress_repository import SECURE_INGRESS_MIGRATION_SHA256
from host_brain.secure_ingress import (
    IngressSurface,
    build_hmac_sha256_signature,
    build_surface_request,
)
from edge_client.channels.input_reporter import EdgeInputReporter


NOW = 1_900_000_000_000
SECRET = "task0488-host-gateway-unit-secret"
PRINCIPAL_ID = "service.edge.gateway"
KEY_ID = "edge.gateway.hmac.v1"


class _Request:
    method = "POST"
    url = SimpleNamespace(path="/v1/host/input")
    client = SimpleNamespace(host="127.0.0.1")

    def __init__(self, headers: list[tuple[bytes, bytes]]) -> None:
        self.scope = {"headers": headers}


def _database(tmp_path: Path) -> Path:
    db_path = tmp_path / "host-gateway.sqlite3"
    migration = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "audit"
        / "migrations"
        / "0058_secure_ingress.sql"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE schema_migration_assets (
                asset_key TEXT PRIMARY KEY,
                migration_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                disposition TEXT NOT NULL,
                applied_ts_ms INTEGER NOT NULL
            )
            """
        )
        conn.executescript(migration.read_text(encoding="utf-8"))
        conn.execute(
            """
            INSERT INTO schema_migration_assets(
                asset_key, migration_id, name, checksum, disposition, applied_ts_ms
            ) VALUES ('0058#secure_ingress', 58, 'secure_ingress', ?, 'APPLIED', ?)
            """,
            (SECURE_INGRESS_MIGRATION_SHA256, NOW),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_principal(
                principal_id, principal_kind, tenant_id, status, version,
                granted_scopes_json, allowed_audiences_json,
                allowed_actions_json, valid_from_ms, valid_until_ms, updated_at_ms
            ) VALUES (?, 'service', 'local', 'ACTIVE', 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                PRINCIPAL_ID,
                json.dumps(["host:write"]),
                json.dumps(["host"]),
                json.dumps(["host:input"]),
                NOW - 1000,
                NOW + 60_000,
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_credential(
                principal_id, key_id, credential_kind, algorithm, secret_ref,
                status, version, valid_from_ms, valid_until_ms, updated_at_ms
            ) VALUES (?, ?, 'hmac-sha256', 'hmac-sha256', ?, 'ACTIVE', 1, ?, ?, ?)
            """,
            (
                PRINCIPAL_ID,
                KEY_ID,
                "env://HOST_EDGE_GATEWAY_HMAC_SECRET",
                NOW - 1000,
                NOW + 60_000,
                NOW,
            ),
        )
    return db_path


def _signed_request(body: bytes, *, suffix: str = "1") -> _Request:
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=KEY_ID,
        algorithm="hmac-sha256",
        proof_id=f"proof.gateway.{suffix}",
        issued_at_epoch_ms=NOW,
        expires_at_epoch_ms=NOW + 60_000,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=PRINCIPAL_ID,
            kind=PrincipalKind.SERVICE,
            tenant_id="local",
            granted_scopes=("host:write",),
        ),
        credential_proof=metadata,
        audience="host",
        request_id=f"request.gateway.{suffix}",
        requested_scopes=("host:write",),
    )
    unsigned = build_surface_request(
        surface=IngressSurface.HOST,
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        method="POST",
        normalized_path="/v1/host/input",
        idempotency_key=f"idem.gateway.{suffix}",
        nonce=f"nonce.gateway.{suffix}",
        raw_body=body,
        received_at_epoch_ms=NOW,
        transport_key=f"principal:{PRINCIPAL_ID}",
        correlation_id=f"correlation.gateway.{suffix}",
    )
    identity_header = base64.urlsafe_b64encode(
        identity.to_canonical_json().encode("utf-8")
    ).rstrip(b"=")
    return _Request(
        [
            (b"x-natsume-identity", identity_header),
            (b"x-natsume-proof", build_hmac_sha256_signature(unsigned, SECRET).encode()),
            (b"x-natsume-idempotency-key", f"idem.gateway.{suffix}".encode()),
            (b"x-natsume-nonce", f"nonce.gateway.{suffix}".encode()),
            (b"x-natsume-correlation-id", f"correlation.gateway.{suffix}".encode()),
        ]
    )


def _env() -> dict[str, str]:
    return {
        "HOST_SECURE_INGRESS_MODE": "ENFORCE",
        "HOST_EDGE_GATEWAY_HMAC_SECRET": SECRET,
        "HOST_EDGE_GATEWAY_HMAC_KEY_ID": KEY_ID,
    }


def test_valid_exact_raw_body_is_allowed_and_restart_duplicate_is_stable(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    body = b'{"source":"GATEWAY","content":"hello"}'
    request = _signed_request(body)

    first = evaluate_host_gateway_ingress(
        request=request,
        raw_body=body,
        received_at_ms=NOW,
        env=_env(),
        db_path=db_path,
    )
    duplicate = evaluate_host_gateway_ingress(
        request=request,
        raw_body=body,
        received_at_ms=NOW + 1,
        env=_env(),
        db_path=db_path,
    )

    assert first.allowed is True
    assert first.request_id == "request.gateway.1"
    assert first.tenant_id == "local"
    assert first.claim_ref
    assert first.body_sha256
    assert duplicate.allowed is False
    assert duplicate.duplicate is True
    assert duplicate.request_id == first.request_id
    assert duplicate.claim_ref == first.claim_ref

    assert complete_host_gateway_ingress(
        claim_ref=first.claim_ref,
        result_ref="host-input-result://request.gateway.1/ACCEPTED",
        at_ms=NOW + 2,
        env=_env(),
        db_path=db_path,
    ) is True
    completed_duplicate = evaluate_host_gateway_ingress(
        request=request,
        raw_body=body,
        received_at_ms=NOW + 3,
        env=_env(),
        db_path=db_path,
    )
    assert completed_duplicate.existing_result_ref == "host-input-result://request.gateway.1/ACCEPTED"


def test_body_tamper_and_ambiguous_security_header_fail_closed(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    original = b'{"source":"GATEWAY","content":"hello"}'
    request = _signed_request(original, suffix="tamper")
    tampered = evaluate_host_gateway_ingress(
        request=request,
        raw_body=original + b" ",
        received_at_ms=NOW,
        env=_env(),
        db_path=db_path,
    )
    duplicated_headers = _Request(
        list(request.scope["headers"])
        + [(b"X-Natsume-Proof", b"0" * 64)]
    )
    ambiguous = evaluate_host_gateway_ingress(
        request=duplicated_headers,
        raw_body=original,
        received_at_ms=NOW,
        env=_env(),
        db_path=db_path,
    )

    assert tampered.allowed is False
    assert tampered.duplicate is False
    assert ambiguous.allowed is False
    assert ambiguous.duplicate is False
    assert SECRET not in repr(tampered)
    assert original.decode() not in repr(tampered)


def test_observed_peer_address_does_not_change_the_signed_transport_identity(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    body = b'{"source":"GATEWAY","content":"remote"}'
    request = _signed_request(body, suffix="remote")
    request.client = SimpleNamespace(host="203.0.113.99")

    outcome = evaluate_host_gateway_ingress(
        request=request,
        raw_body=body,
        received_at_ms=NOW,
        env=_env(),
        db_path=db_path,
    )

    assert outcome.allowed is True


def test_edge_reporter_wire_headers_verify_against_exact_frozen_body(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    reporter = EdgeInputReporter(
        host_url="http://host.invalid",
        hmac_key=SECRET,
        secure_principal_id=PRINCIPAL_ID,
        secure_tenant_id="local",
        secure_key_id=KEY_ID,
    )
    payload = {
        "source": "GATEWAY",
        "platform": "line",
        "conversation_id": "conversation-1",
        "content": "wire contract",
    }
    body = reporter._serialize_payload(payload)
    with patch("edge_client.channels.input_reporter.time.time", return_value=NOW / 1000):
        headers = reporter._build_secure_ingress_headers(
            body=body,
            idempotency_key="edge-host:wire-contract",
        )
    request = _Request(
        [(name.encode("ascii"), value.encode("ascii")) for name, value in headers.items()]
    )
    request.client = SimpleNamespace(host="198.51.100.44")

    outcome = evaluate_host_gateway_ingress(
        request=request,
        raw_body=body,
        received_at_ms=NOW,
        env=_env(),
        db_path=db_path,
    )

    assert outcome.allowed is True
    assert outcome.idempotency_key == "edge-host:wire-contract"


def test_off_mode_and_missing_schema_never_fall_back_to_anonymous(tmp_path: Path) -> None:
    body = b"{}"
    request = _signed_request(body, suffix="off")
    off = evaluate_host_gateway_ingress(
        request=request,
        raw_body=body,
        received_at_ms=NOW,
        env={"HOST_SECURE_INGRESS_MODE": "OFF"},
        db_path=tmp_path / "missing.sqlite3",
    )
    unavailable = evaluate_host_gateway_ingress(
        request=request,
        raw_body=body,
        received_at_ms=NOW,
        env=_env(),
        db_path=tmp_path / "missing.sqlite3",
    )

    assert off.reason_code == "HOST_GATEWAY_SECURE_INGRESS_DISABLED"
    assert unavailable.reason_code == "HOST_GATEWAY_SECURE_INGRESS_UNAVAILABLE"
    assert off.allowed is unavailable.allowed is False


def test_generic_edge_rpc_key_is_never_accepted_as_host_write_credential(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    body = b'{"source":"GATEWAY","content":"isolated key domain"}'
    outcome = evaluate_host_gateway_ingress(
        request=_signed_request(body, suffix="isolated-key"),
        raw_body=body,
        received_at_ms=NOW,
        env={
            "HOST_SECURE_INGRESS_MODE": "ENFORCE",
            "HOST_EDGE_GATEWAY_HMAC_KEY_ID": KEY_ID,
            "EDGE_HMAC_SHARED_KEY": SECRET,
        },
        db_path=db_path,
    )

    assert outcome.allowed is False
    assert outcome.duplicate is False
