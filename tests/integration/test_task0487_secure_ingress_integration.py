from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.secure_ingress import (
    CredentialVerifierRegistry,
    FileEmergencyAuditSink,
    HmacSha256CredentialVerifier,
    IngressReasonCode,
    IngressSurface,
    SECURE_INGRESS_SURFACE_POLICIES,
    SecureIngressService,
    build_hmac_sha256_signature,
    build_surface_request,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain/audit/migrations"
NOW = 1_800_000_000_000


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "task0487.sqlite3"
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=MIGRATIONS,
    ).apply_migrations(request_id="task0487-integration", ts_ms=NOW - 1)
    assert result.success, result.as_dict()
    assert result.summary["current_version"] >= 60
    return db_path


def _seed_surfaces(db_path: Path) -> dict[str, str]:
    secrets: dict[str, str] = {}
    with sqlite3.connect(str(db_path)) as conn:
        for policy in SECURE_INGRESS_SURFACE_POLICIES.values():
            principal_id = f"service.{policy.surface.value}.test"
            key_id = f"key.{policy.surface.value}.test"
            secrets[key_id] = f"task0487-{policy.surface.value}-test-secret"
            conn.execute(
                """
                INSERT INTO secure_ingress_principal(
                    principal_id, principal_kind, tenant_id, status, version,
                    granted_scopes_json, allowed_audiences_json,
                    allowed_actions_json, valid_from_ms, valid_until_ms,
                    updated_at_ms
                ) VALUES (?, 'service', 'tenant.test', 'ACTIVE', 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    principal_id,
                    json.dumps(list(policy.required_scopes)),
                    json.dumps([policy.audience]),
                    json.dumps([policy.action]),
                    NOW - 1000,
                    NOW + 100_000,
                    NOW,
                ),
            )
            conn.execute(
                """
                INSERT INTO secure_ingress_credential(
                    principal_id, key_id, credential_kind, algorithm,
                    secret_ref, status, version, valid_from_ms,
                    valid_until_ms, updated_at_ms
                ) VALUES (?, ?, 'hmac-sha256', 'hmac-sha256', ?, 'ACTIVE', 1, ?, ?, ?)
                """,
                (
                    principal_id,
                    key_id,
                    f"test-secret-ref://{key_id}",
                    NOW - 1000,
                    NOW + 100_000,
                    NOW,
                ),
            )
    return secrets


def _service(
    db_path: Path,
    secrets: dict[str, str],
) -> tuple[SecureIngressService, SQLiteSecureIngressStore]:
    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    verifier = CredentialVerifierRegistry(
        {
            CredentialProofKind.HMAC_SHA256: HmacSha256CredentialVerifier(
                secret_resolver=lambda authority: secrets.get(authority.key_id),
            )
        }
    )
    return (
        SecureIngressService(
            principal_store=store,
            credential_verifier=verifier,
            attempt_limiter=store,
            rate_limiter=store,
            state_store=store,
            emergency_audit_sink=FileEmergencyAuditSink(
                db_path.with_suffix(".emergency-audit.jsonl")
            ),
        ),
        store,
    )


def _request(
    surface: IngressSurface,
    secret: str,
    *,
    suffix: str = "1",
    requested_scopes: tuple[str, ...] | None = None,
):
    policy = SECURE_INGRESS_SURFACE_POLICIES[surface]
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=f"key.{surface.value}.test",
        algorithm="hmac-sha256",
        proof_id=f"proof.{surface.value}.{suffix}",
        issued_at_epoch_ms=NOW,
        expires_at_epoch_ms=NOW + 60_000,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=f"service.{surface.value}.test",
            kind=PrincipalKind.SERVICE,
            tenant_id="tenant.test",
            granted_scopes=policy.required_scopes,
        ),
        credential_proof=metadata,
        audience=policy.audience,
        request_id=f"request.{surface.value}.{suffix}",
        requested_scopes=requested_scopes or policy.required_scopes,
    )
    unsigned = build_surface_request(
        surface=surface,
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        method="POST",
        normalized_path=f"/test/{surface.value}/command",
        idempotency_key=f"idem.{surface.value}.{suffix}",
        nonce=f"nonce.{surface.value}.{suffix}",
        raw_body=f'{{"surface":"{surface.value}"}}'.encode(),
        received_at_epoch_ms=NOW,
        transport_key=f"loopback:{surface.value}:test",
        correlation_id=f"correlation.{surface.value}.{suffix}",
    )
    return replace(
        unsigned,
        credential=CredentialProof(
            metadata=metadata,
            raw_proof=build_hmac_sha256_signature(unsigned, secret),
        ),
    )


def test_task0487_all_surface_fail_closed_restart_and_audit_wave(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    secrets = _seed_surfaces(db_path)
    service, store = _service(db_path, secrets)
    requests = {
        surface: _request(surface, secrets[f"key.{surface.value}.test"])
        for surface in IngressSurface
    }
    first = {surface: service.decide(request) for surface, request in requests.items()}
    store.close()

    restarted_service, restarted_store = _service(db_path, secrets)
    try:
        duplicates = {
            surface: restarted_service.decide(request)
            for surface, request in requests.items()
        }
        assert all(decision.allowed for decision in first.values())
        assert all(
            decision.reason_code is IngressReasonCode.IDEMPOTENCY_DUPLICATE
            and not decision.allowed
            for decision in duplicates.values()
        )
        rows = restarted_store.read_audit()
        assert len(rows) == 2 * len(IngressSurface)
        assert {row["surface"] for row in rows} == {
        policy.surface_id for policy in SECURE_INGRESS_SURFACE_POLICIES.values()
        }
        _assert_missing_security_matrix(
            service=restarted_service,
            store=restarted_store,
            secrets=secrets,
        )
    finally:
        restarted_store.close()


def _assert_missing_security_matrix(
    *,
    service: SecureIngressService,
    store: SQLiteSecureIngressStore,
    secrets: dict[str, str],
) -> None:
    cases = (
        ("principal", IngressReasonCode.PRINCIPAL_INVALID),
        ("credential", IngressReasonCode.CREDENTIAL_MISSING),
        ("signature", IngressReasonCode.CREDENTIAL_INVALID),
        ("scope", IngressReasonCode.SCOPE_DENIED),
        ("idempotency", IngressReasonCode.IDEMPOTENCY_KEY_MISSING),
        ("nonce", IngressReasonCode.SCHEMA_INVALID),
    )
    suffix = 100
    for surface in IngressSurface:
        for missing, reason in cases:
            suffix += 1
            request = _request(
                surface,
                secrets[f"key.{surface.value}.test"],
                suffix=str(suffix),
                requested_scopes=(("other:write",) if missing == "scope" else None),
            )
            if missing == "principal":
                request = replace(request, identity=None, credential=None)
            elif missing == "credential":
                request = replace(request, credential=None)
            elif missing == "signature":
                request = replace(
                    request,
                    credential=CredentialProof(
                        metadata=request.identity.credential_proof,
                        raw_proof="0" * 64,
                    ),
                )
            elif missing == "scope":
                request = replace(
                    request,
                    required_scopes=("other:write",),
                    credential=CredentialProof(
                        metadata=request.identity.credential_proof,
                        raw_proof="0" * 64,
                    ),
                )
                request = replace(
                    request,
                    credential=CredentialProof(
                        metadata=request.identity.credential_proof,
                        raw_proof=build_hmac_sha256_signature(
                            request,
                            secrets[f"key.{surface.value}.test"],
                        ),
                    ),
                )
            elif missing == "idempotency":
                request = replace(request, idempotency_key="")
            elif missing == "nonce":
                request = replace(request, nonce="")
            before = len(store.read_audit())
            decision = service.decide(request)
            rows = store.read_audit()
            assert decision.allowed is False
            assert decision.reason_code is reason
            assert len(rows) == before + 1
            audit_row = next(
                row for row in rows if row["audit_ref"] == decision.audit_ref
            )
            assert audit_row["decision"] == "DENY"
            assert audit_row["side_effects_executed"] == 0
