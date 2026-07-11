from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import uuid
from typing import Any, Mapping

from host_brain.governor.tokens import TokenService
from host_brain.pentest.lab_isolation import (
    LabCapability,
    LabIsolationDenied,
    canonical_effect_args_digest,
)
from host_brain.pentest.secure_run import (
    SECURE_RUN_INGRESS_ACTION,
    SECURE_RUN_NORMALIZED_PATH,
    SECURE_RUN_REQUIRED_SCOPES,
    SECURE_RUN_SURFACE,
    SecureRunAuthorityGate,
    SecureRunReceiptDecision,
    SecureRunReceiptStatus,
    SecureRunRequest,
    bind_secure_ingress_authority,
    build_governor_token_binding,
)
from host_brain.pentest.secure_ingress_authority import (
    SQLiteSecureIngressAuthorityResolver,
)
from host_brain.secure_ingress import (
    AuditStatus,
    IngressReasonCode,
    IngressStage,
    SecureIngressDecision,
    SecureIngressRequest,
)
from host_brain.secure_ingress.models import digest_identifier
from host_brain.secure_ingress.store import (
    SECURE_INGRESS_MIGRATION_ASSET_KEY,
    SECURE_INGRESS_MIGRATION_SHA256,
)
from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "host_brain/audit/migrations/0058_secure_ingress.sql"


class _ReceiptStore:
    def consume_once(self, **kwargs: Any) -> SecureRunReceiptDecision:
        return SecureRunReceiptDecision(
            SecureRunReceiptStatus.CONSUMED,
            str(kwargs["receipt_ref"]),
            str(kwargs["binding_digest"]),
        )


def _issue_test_secure_run_grant(
    request: SecureRunRequest,
    *,
    now: int,
    nonce: str,
    tamper_evidence: tuple[str, object] | None = None,
    audit_credential_ref_digest: str | None = None,
):
    claim_ref = "secure-ingress-claim://" + nonce
    audit_ref = "secure-ingress-audit://" + uuid.uuid4().hex
    proof_id = f"proof.task0491.{nonce}"
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id="key.task0491.test",
        algorithm="hmac-sha256",
        proof_id=proof_id,
        issued_at_epoch_ms=now,
        expires_at_epoch_ms=now + 60_000,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=request.principal_id,
            kind=PrincipalKind.SERVICE,
            tenant_id=request.tenant_id,
            granted_scopes=SECURE_RUN_REQUIRED_SCOPES,
        ),
        credential_proof=metadata,
        audience="pentest",
        request_id=f"request.task0491.{nonce}",
        requested_scopes=SECURE_RUN_REQUIRED_SCOPES,
    )
    ingress = SecureIngressRequest(
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        surface=SECURE_RUN_SURFACE,
        method="POST",
        normalized_path=SECURE_RUN_NORMALIZED_PATH,
        action=SECURE_RUN_INGRESS_ACTION,
        required_scopes=SECURE_RUN_REQUIRED_SCOPES,
        idempotency_key=f"idem.task0491.{nonce}",
        nonce=f"nonce.task0491.{nonce}",
        raw_body=request.canonical_authority_body(),
        received_at_epoch_ms=now,
        transport_key="loopback:test",
    )
    decision = SecureIngressDecision(
        allowed=True,
        stage=IngressStage.COMPLETE,
        reason_code=IngressReasonCode.ALLOWED,
        audit_ref=audit_ref,
        audit_status=AuditStatus.PRIMARY_DURABLE,
        primary_reason_code=IngressReasonCode.ALLOWED,
        claim_ref=claim_ref,
        existing_result_ref=None,
    )
    body_sha256 = hashlib.sha256(ingress.raw_body).hexdigest()
    scopes_json = json.dumps(list(SECURE_RUN_REQUIRED_SCOPES), separators=(",", ":"))
    with tempfile.TemporaryDirectory(prefix="task0491-authority-") as directory:
        path = Path(directory) / "authority.sqlite3"
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                "CREATE TABLE schema_migration_assets(asset_key TEXT PRIMARY KEY, checksum TEXT NOT NULL, disposition TEXT NOT NULL)"
            )
            conn.commit()
            conn.executescript(MIGRATION.read_text(encoding="utf-8-sig"))
            conn.execute(
                "INSERT INTO schema_migration_assets VALUES (?, ?, 'APPLIED')",
                (SECURE_INGRESS_MIGRATION_ASSET_KEY, SECURE_INGRESS_MIGRATION_SHA256),
            )
            conn.execute(
                """
                INSERT INTO secure_ingress_idempotency_claim(
                    principal_id, surface, idempotency_digest, action,
                    required_scopes_json, request_id, body_sha256, claim_ref,
                    result_ref, claim_status, provisional_expires_at_ms,
                    claimed_at_ms, updated_at_ms, expires_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'COMMITTED', NULL, ?, ?, ?)
                """,
                (
                    request.principal_id,
                    SECURE_RUN_SURFACE,
                    "sha256:" + "1" * 64,
                    SECURE_RUN_INGRESS_ACTION,
                    scopes_json,
                    identity.request_id,
                    body_sha256,
                    claim_ref,
                    now,
                    now,
                    now + 60_000,
                ),
            )
            conn.execute(
                """
                INSERT INTO secure_ingress_audit(
                    audit_ref, request_id, correlation_id, surface, action,
                    method, normalized_path, principal_id, tenant_id, key_id,
                    credential_kind, credential_ref_digest,
                    requested_scopes_json, required_scopes_json, body_sha256,
                    idempotency_digest, nonce_digest, transport_key_digest,
                    decision, reason_code, stage, claim_ref, rate_snapshot_json,
                    side_effects_executed, ts_ms
                ) VALUES (?, ?, NULL, ?, ?, 'POST', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'ALLOW', 'SECURE_INGRESS_ALLOWED', 'COMPLETE', ?, NULL, 0, ?)
                """,
                (
                    audit_ref,
                    identity.request_id,
                    SECURE_RUN_SURFACE,
                    SECURE_RUN_INGRESS_ACTION,
                    SECURE_RUN_NORMALIZED_PATH,
                    request.principal_id,
                    request.tenant_id,
                    metadata.key_id,
                    metadata.kind.value,
                    audit_credential_ref_digest
                    or digest_identifier(metadata.proof_id),
                    scopes_json,
                    scopes_json,
                    body_sha256,
                    "sha256:" + "2" * 64,
                    "sha256:" + "3" * 64,
                    "sha256:" + "4" * 64,
                    claim_ref,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        resolver = SQLiteSecureIngressAuthorityResolver(path)
        if tamper_evidence is not None:
            evidence = resolver.resolve_committed_authority(
                audit_ref=audit_ref,
                claim_ref=claim_ref,
            )
            assert evidence is not None
            object.__setattr__(evidence, tamper_evidence[0], tamper_evidence[1])
            resolver.resolve_committed_authority = lambda **_kwargs: evidence  # type: ignore[method-assign]
        return bind_secure_ingress_authority(
            ingress_request=ingress,
            ingress_decision=decision,
            request=request,
            evidence_resolver=resolver,
        )


def issue_test_lab_capability(
    *,
    tool_id: str,
    target: str,
    action: str,
    effect_args: Mapping[str, Any],
    run_id: str | None = None,
) -> LabCapability:
    """Issue through the real Gate; only Secure Ingress projection is a sealed fixture."""

    now = int(time.time() * 1000)
    nonce = uuid.uuid4().hex
    request = SecureRunRequest(
        principal_id="service.pentest.task0491-test",
        tenant_id="tenant.task0491-test",
        run_id=run_id or f"run.task0491.{nonce}",
        tool_id=tool_id,
        target=target,
        action=action,
        intent_id=f"intent.task0491.{nonce}",
        session_id=f"session.task0491.{nonce}",
        seq=1,
        effect_args_digest=canonical_effect_args_digest(effect_args),
    )
    tokens = TokenService()
    binding = build_governor_token_binding(request)
    approval = tokens.issue_approval_token(
        intent_id=binding.intent_id,
        skill_name=binding.skill_name,
        session_id=binding.session_id,
        seq=binding.seq,
        now_ts_ms=now,
    )
    confirm = tokens.issue_confirm_token(
        intent_id=binding.intent_id,
        skill_name=binding.skill_name,
        session_id=binding.session_id,
        seq=binding.seq,
        now_ts_ms=now,
    )
    request = replace(
        request,
        approval_token_ref=approval,
        confirm_token_ref=confirm,
    )
    grant = _issue_test_secure_run_grant(request, now=now, nonce=nonce)
    decision = SecureRunAuthorityGate(
        governor_tokens=tokens,
        receipt_store=_ReceiptStore(),
        clock=lambda: now + 1,
    ).authorize(request, grant)
    if not decision.allowed:
        raise LabIsolationDenied(f"TEST_GATE_REJECTED:{decision.reason_code.value}")
    assert isinstance(decision.capability, LabCapability)
    return decision.capability
