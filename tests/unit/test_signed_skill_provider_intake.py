from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from types import MappingProxyType

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
    SecureIngressAdapter,
    SecureIngressMode,
    SecureIngressService,
    build_hmac_sha256_signature,
)
from host_brain.skills.signed_intake_composition import (
    FilesystemSkillProviderIntakeSourceResolver,
    SKILL_PROVIDER_INTAKE_COMPOSITION_DISABLED,
    SKILL_PROVIDER_INTAKE_GLOBAL_ENFORCE_REQUIRED,
    SKILL_PROVIDER_INTAKE_PATH_OVERLAP,
    build_signed_skill_provider_intake_composition,
)
from host_brain.skills.signed_intake_ingress import (
    SKILL_PROVIDER_INTAKE_ACTION,
    SKILL_PROVIDER_INTAKE_PATH,
    SKILL_PROVIDER_INTAKE_SCHEMA,
    SKILL_PROVIDER_INTAKE_SCOPES,
    SKILL_PROVIDER_INTAKE_SURFACE,
    SecureSignedSkillProviderIntake,
    SignedSkillProviderIntakeRequest,
)
from host_brain.skills.supply_chain_service import SignedBundleIntakeDecision


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain/audit/migrations"
NOW_MS = 1_960_000_000_000
KEY_ID = "skill-provider-intake.hmac.v1"
SECRET = "skill-provider-intake-secret-at-least-32-bytes"
PRINCIPAL_ID = "service.skill-provider.publisher"
TENANT_ID = "tenant.skill-provider.test"


class _Resolver:
    def __init__(self, source: object) -> None:
        self.source = source
        self.calls: list[str] = []

    def resolve(self, source_ref: str) -> object:
        self.calls.append(source_ref)
        return self.source


class _SupplyService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, str, str]] = []

    def intake_signed_bundle(
        self,
        source: object,
        *,
        kind: str,
        publisher: str,
        idempotency: str,
    ) -> SignedBundleIntakeDecision:
        self.calls.append((source, kind, publisher, idempotency))
        return SignedBundleIntakeDecision(
            stored=True,
            quarantined=True,
            staged=True,
            activation_performed=False,
            import_performed=False,
            quarantine_retained=True,
            reason_code="SUPPLY_CHAIN_INTAKE_STAGED",
            bundle_id="provider://safe/v1",
            bundle_kind="PROVIDER",
            bundle_name="safe",
            bundle_version="v1",
            bundle_digest="sha256:" + "a" * 64,
            repository_state="STAGED",
            intake_acceptance_status="NEW",
            stage_acceptance_status="NEW",
            audit=MappingProxyType({"stage_audit_ref": "audit://stage/safe"}),
        )


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "audit.sqlite3"
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=MIGRATIONS,
    ).apply_migrations(request_id="signed-intake-unit", ts_ms=NOW_MS - 1)
    assert result.success
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            """
            INSERT INTO secure_ingress_principal(
                principal_id, principal_kind, tenant_id, status, version,
                granted_scopes_json, allowed_audiences_json,
                allowed_actions_json, valid_from_ms, valid_until_ms, updated_at_ms
            ) VALUES (?, 'service', ?, 'ACTIVE', 1, ?, '["host"]', ?, ?, ?, ?)
            """,
            (
                PRINCIPAL_ID,
                TENANT_ID,
                json.dumps(list(SKILL_PROVIDER_INTAKE_SCOPES)),
                json.dumps([SKILL_PROVIDER_INTAKE_ACTION]),
                NOW_MS - 60_000,
                NOW_MS + 600_000,
                NOW_MS,
            ),
        )
        connection.execute(
            """
            INSERT INTO secure_ingress_credential(
                principal_id, key_id, credential_kind, algorithm,
                secret_ref, status, version, valid_from_ms,
                valid_until_ms, updated_at_ms
            ) VALUES (?, ?, 'hmac-sha256', 'hmac-sha256', ?, 'ACTIVE', 1, ?, ?, ?)
            """,
            (
                PRINCIPAL_ID,
                KEY_ID,
                "test-secret-ref://skill-provider-intake",
                NOW_MS - 60_000,
                NOW_MS + 600_000,
                NOW_MS,
            ),
        )
    return db_path


def _identity() -> IngressIdentityEnvelope:
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=KEY_ID,
        algorithm="hmac-sha256",
        proof_id="signed-intake-unit-proof",
        issued_at_epoch_ms=NOW_MS,
        expires_at_epoch_ms=NOW_MS + 300_000,
    )
    return IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=PRINCIPAL_ID,
            kind=PrincipalKind.SERVICE,
            tenant_id=TENANT_ID,
            granted_scopes=SKILL_PROVIDER_INTAKE_SCOPES,
        ),
        credential_proof=metadata,
        audience="host",
        request_id="signed-intake-unit-request",
        requested_scopes=SKILL_PROVIDER_INTAKE_SCOPES,
    )


def _body() -> bytes:
    return json.dumps(
        {
            "schema_version": SKILL_PROVIDER_INTAKE_SCHEMA,
            "source_ref": "intake://bundle-a",
            "bundle_kind": "PROVIDER",
            "publisher_id": "publisher://trusted/test",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _signed_request(
    *,
    body: bytes,
    idempotency: str,
    nonce: str,
) -> SignedSkillProviderIntakeRequest:
    identity = _identity()
    unsigned = SignedSkillProviderIntakeRequest(
        raw_body=body,
        identity=identity,
        credential=CredentialProof(metadata=identity.credential_proof, raw_proof="0" * 64),
        idempotency_key=idempotency,
        nonce=nonce,
        received_at_epoch_ms=NOW_MS,
        transport_key="test+loopback:skill-provider-intake",
    )
    from host_brain.secure_ingress import SecureIngressRequest

    canonical = SecureIngressRequest(
        identity=identity,
        credential=unsigned.credential,
        surface=SKILL_PROVIDER_INTAKE_SURFACE,
        method="POST",
        normalized_path=SKILL_PROVIDER_INTAKE_PATH,
        action=SKILL_PROVIDER_INTAKE_ACTION,
        required_scopes=SKILL_PROVIDER_INTAKE_SCOPES,
        idempotency_key=idempotency,
        nonce=nonce,
        raw_body=body,
        received_at_epoch_ms=NOW_MS,
        transport_key=unsigned.transport_key,
    )
    proof = build_hmac_sha256_signature(canonical, SECRET)
    return replace(
        unsigned,
        credential=CredentialProof(
            metadata=identity.credential_proof,
            raw_proof=proof,
        ),
    )


def _ingress(tmp_path: Path):
    db_path = _migrated_db(tmp_path)
    store = SQLiteSecureIngressStore(db_path)
    verifier = CredentialVerifierRegistry(
        {
            CredentialProofKind.HMAC_SHA256: HmacSha256CredentialVerifier(
                secret_resolver=lambda _authority: SECRET
            )
        }
    )
    service = SecureIngressService(
        principal_store=store,
        credential_verifier=verifier,
        attempt_limiter=store,
        rate_limiter=store,
        state_store=store,
        emergency_audit_sink=FileEmergencyAuditSink(tmp_path / "emergency.jsonl"),
    )
    resolver = _Resolver(tmp_path / "inbox" / "bundle-a")
    supply = _SupplyService()
    ingress = SecureSignedSkillProviderIntake(
        ingress_adapter=SecureIngressAdapter(
            service=service,
            mode=SecureIngressMode.ENFORCE,
        ),
        ingress_service=service,
        claim_store=store,
        supply_chain_service=supply,  # type: ignore[arg-type]
        source_resolver=resolver,
    )
    return ingress, store, resolver, supply


def test_raw_body_hmac_stages_once_and_duplicate_never_reenters_service(
    tmp_path: Path,
) -> None:
    ingress, store, resolver, supply = _ingress(tmp_path)
    try:
        first = asyncio.run(
            ingress.execute(
                _signed_request(
                    body=_body(),
                    idempotency="signed-intake-same",
                    nonce="signed-intake-first",
                )
            )
        )
        duplicate = asyncio.run(
            ingress.execute(
                _signed_request(
                    body=_body(),
                    idempotency="signed-intake-same",
                    nonce="signed-intake-second",
                )
            )
        )

        assert first.allowed and first.quarantined and first.staged
        assert first.activation_performed is first.import_performed is False
        assert duplicate.duplicate and duplicate.executed is False
        assert duplicate.result_ref == first.result_ref
        assert resolver.calls == ["intake://bundle-a"]
        assert len(supply.calls) == 1
        audits = store.read_audit(request_id="signed-intake-unit-request")
        assert sorted(
            (row["decision"], row["reason_code"], row["stage"])
            for row in audits
        ) == sorted(
            (
                ("ALLOW", "SECURE_INGRESS_ALLOWED", "COMPLETE"),
                ("ALLOW", "SUPPLY_CHAIN_INTAKE_STAGED", "APPLICATION"),
                ("DENY", "SECURE_INGRESS_IDEMPOTENCY_DUPLICATE", "CLAIM"),
            )
        )
    finally:
        store.close()


def test_mutated_raw_body_with_original_proof_is_rejected_before_source_access(
    tmp_path: Path,
) -> None:
    ingress, store, resolver, supply = _ingress(tmp_path)
    try:
        signed = _signed_request(
            body=_body(),
            idempotency="signed-intake-tamper",
            nonce="signed-intake-tamper",
        )
        tampered = replace(
            signed,
            raw_body=signed.raw_body.replace(b"bundle-a", b"bundle-b"),
        )
        outcome = asyncio.run(ingress.execute(tampered))

        assert outcome.allowed is outcome.executed is False
        assert outcome.reason_code == "SECURE_INGRESS_CREDENTIAL_INVALID"
        assert resolver.calls == []
        assert supply.calls == []
    finally:
        store.close()


def test_composition_is_default_disabled_and_rejects_source_quarantine_overlap(
    tmp_path: Path,
) -> None:
    default = build_signed_skill_provider_intake_composition(env={})
    assert default.enabled is False
    assert default.reason_code == SKILL_PROVIDER_INTAKE_GLOBAL_ENFORCE_REQUIRED

    builtins = build_signed_skill_provider_intake_composition(
        env={
            "HOST_SECURE_INGRESS_MODE": "ENFORCE",
            "HOST_SUPPLY_CHAIN_ACTIVATION_MODE": "BUILTINS_ONLY",
        }
    )
    assert builtins.enabled is False
    assert builtins.reason_code == SKILL_PROVIDER_INTAKE_COMPOSITION_DISABLED

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    overlap = build_signed_skill_provider_intake_composition(
        env={
            "HOST_SECURE_INGRESS_MODE": "ENFORCE",
            "HOST_SUPPLY_CHAIN_ACTIVATION_MODE": "STAGED",
            "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON": json.dumps({KEY_ID: SECRET}),
            "HOST_SKILL_PROVIDER_INTAKE_ROOT": str(inbox),
            "HOST_SKILL_PROVIDER_QUARANTINE_ROOT": str(inbox / "quarantine"),
        }
    )
    assert overlap.enabled is False
    assert overlap.reason_code == SKILL_PROVIDER_INTAKE_PATH_OVERLAP


def test_filesystem_resolver_accepts_only_opaque_children(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    source = inbox / "bundle-a"
    source.mkdir(parents=True)
    resolver = FilesystemSkillProviderIntakeSourceResolver(inbox)

    assert resolver.resolve("intake://bundle-a") == source.resolve()
    for invalid in ("../bundle-a", "intake://../bundle-a", str(source)):
        try:
            resolver.resolve(invalid)
        except ValueError:
            pass
        else:  # pragma: no cover - explicit fail-closed assertion
            raise AssertionError(f"unsafe source reference accepted: {invalid}")
