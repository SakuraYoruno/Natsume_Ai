from __future__ import annotations

import base64
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)

from host_brain.app.internal_agent_ingress import InternalAgentIngress
from host_brain.scheduler.command_ingress import (
    SCHEDULER_CLOCK_CREDENTIAL_INVALID,
    SCHEDULER_CLOCK_CREDENTIAL_REQUIRED,
    SchedulerClockSigningContext,
    SchedulerCommandIngress,
)
from host_brain.persistence.secure_ingress_repository import (
    SECURE_INGRESS_MIGRATION_SHA256,
    SQLiteSecureIngressStore,
)
from host_brain.secure_ingress import (
    SECURE_INGRESS_ENFORCE_REQUIRED,
    CredentialVerifierRegistry,
    FileEmergencyAuditSink,
    HmacSha256CredentialVerifier,
    IngressReasonCode,
    IngressSurface,
    M48B_P0_SURFACE_POLICIES,
    SecureIngressAuthorityEvaluator,
    SecureIngressMode,
    SecureIngressService,
    build_hmac_sha256_signature,
    build_surface_request,
)


NOW = 1_900_000_000_000
TENANT_ID = "tenant.security.test"
SECRETS = {
    "key.internal-agent.test": "internal-agent-test-secret",
    "key.review-operator.test": "review-operator-test-secret",
    "key.scheduler-admin.test": "scheduler-admin-test-secret",
}
PRINCIPALS = {
    IngressSurface.INTERNAL_AGENT: "service.internal-agent.test",
    IngressSurface.REVIEW_OPERATOR: "operator.review.test",
    IngressSurface.SCHEDULER_ADMIN: "service.scheduler-admin.test",
}
KEYS = {
    IngressSurface.INTERNAL_AGENT: "key.internal-agent.test",
    IngressSurface.REVIEW_OPERATOR: "key.review-operator.test",
    IngressSurface.SCHEDULER_ADMIN: "key.scheduler-admin.test",
}


class _Request:
    method = "POST"
    client = SimpleNamespace(host="127.0.0.1")

    def __init__(
        self,
        *,
        path: str,
        headers: list[tuple[bytes, bytes]],
    ) -> None:
        self.url = SimpleNamespace(path=path)
        self.scope = {"headers": headers}


def _database(tmp_path: Path) -> Path:
    db_path = tmp_path / "internal-scheduler-ingress.sqlite3"
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
        for surface in (
            IngressSurface.INTERNAL_AGENT,
            IngressSurface.REVIEW_OPERATOR,
            IngressSurface.SCHEDULER_ADMIN,
        ):
            policy = M48B_P0_SURFACE_POLICIES[surface]
            conn.execute(
                """
                INSERT INTO secure_ingress_principal(
                    principal_id, principal_kind, tenant_id, status, version,
                    granted_scopes_json, allowed_audiences_json,
                    allowed_actions_json, valid_from_ms, valid_until_ms,
                    updated_at_ms
                ) VALUES (?, 'service', ?, 'ACTIVE', 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    PRINCIPALS[surface],
                    TENANT_ID,
                    json.dumps(list(policy.required_scopes)),
                    json.dumps([policy.audience]),
                    json.dumps([policy.action]),
                    NOW - 1_000,
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
                ) VALUES (?, ?, 'hmac-sha256', 'hmac-sha256', ?,
                          'ACTIVE', 1, ?, ?, ?)
                """,
                (
                    PRINCIPALS[surface],
                    KEYS[surface],
                    f"test-secret-ref://{KEYS[surface]}",
                    NOW - 1_000,
                    NOW + 100_000,
                    NOW,
                ),
            )
    return db_path


def _evaluator(
    db_path: Path,
    *,
    mode: SecureIngressMode = SecureIngressMode.ENFORCE,
) -> tuple[SecureIngressAuthorityEvaluator, SQLiteSecureIngressStore]:
    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    verifier = CredentialVerifierRegistry(
        {
            CredentialProofKind.HMAC_SHA256: HmacSha256CredentialVerifier(
                secret_resolver=lambda authority: SECRETS.get(authority.key_id),
            )
        }
    )
    service = SecureIngressService(
        principal_store=store,
        credential_verifier=verifier,
        attempt_limiter=store,
        rate_limiter=store,
        state_store=store,
        emergency_audit_sink=FileEmergencyAuditSink(
            db_path.with_suffix(".emergency-audit.jsonl")
        ),
    )
    return SecureIngressAuthorityEvaluator(service=service, mode=mode), store


def _identity(
    surface: IngressSurface,
    *,
    suffix: str,
    request_id: str | None = None,
    kind: PrincipalKind = PrincipalKind.SERVICE,
) -> IngressIdentityEnvelope:
    policy = M48B_P0_SURFACE_POLICIES[surface]
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=KEYS[surface],
        algorithm="hmac-sha256",
        proof_id=f"proof.{surface.value}.{suffix}",
        issued_at_epoch_ms=NOW,
        expires_at_epoch_ms=NOW + 60_000,
    )
    return IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=PRINCIPALS[surface],
            kind=kind,
            tenant_id=TENANT_ID,
            granted_scopes=policy.required_scopes,
        ),
        credential_proof=metadata,
        audience=policy.audience,
        request_id=request_id or f"request.{surface.value}.{suffix}",
        requested_scopes=policy.required_scopes,
    )


def _signed_http_request(
    surface: IngressSurface,
    body: bytes,
    *,
    suffix: str,
    policy_surface: IngressSurface | None = None,
    request_id: str | None = None,
    idempotency_key: str | None = None,
    nonce: str | None = None,
) -> _Request:
    selected_surface = policy_surface or surface
    policy = M48B_P0_SURFACE_POLICIES[selected_surface]
    identity = _identity(surface, suffix=suffix, request_id=request_id)
    idem = idempotency_key or f"idem.{selected_surface.value}.{surface.value}.{suffix}"
    nonce_value = nonce or f"nonce.{selected_surface.value}.{surface.value}.{suffix}"
    unsigned = build_surface_request(
        surface=selected_surface,
        identity=identity,
        credential=None,
        method="POST",
        normalized_path=policy.default_path,
        idempotency_key=idem,
        nonce=nonce_value,
        raw_body=body,
        received_at_epoch_ms=NOW,
        transport_key=f"principal:{identity.principal.principal_id}",
        correlation_id=(
            f"correlation.{selected_surface.value}.{surface.value}.{suffix}"
        ),
    )
    identity_header = base64.urlsafe_b64encode(
        identity.to_canonical_json().encode("utf-8")
    ).rstrip(b"=")
    return _Request(
        path=policy.default_path,
        headers=[
            (b"x-natsume-identity", identity_header),
            (
                b"x-natsume-proof",
                build_hmac_sha256_signature(
                    unsigned,
                    SECRETS[KEYS[surface]],
                ).encode("ascii"),
            ),
            (
                b"x-natsume-idempotency-key",
                idem.encode("ascii"),
            ),
            (b"x-natsume-nonce", nonce_value.encode("ascii")),
            (
                b"x-natsume-correlation-id",
                (
                    f"correlation.{selected_surface.value}.{surface.value}.{suffix}"
                ).encode("ascii"),
            ),
        ],
    )


class _ClockCredentialProvider:
    def __init__(self, *, wrong_kind: bool = False) -> None:
        self._identity: IngressIdentityEnvelope | None = None
        self._wrong_kind = wrong_kind

    def identity_for(
        self,
        *,
        context: SchedulerClockSigningContext,
    ) -> IngressIdentityEnvelope:
        self._identity = _identity(
            IngressSurface.SCHEDULER_ADMIN,
            suffix=context.body_sha256[:16],
            request_id=context.request_id,
            kind=(PrincipalKind.USER if self._wrong_kind else PrincipalKind.SERVICE),
        )
        return self._identity

    def issue_proof(self, *, request):
        assert self._identity is not None
        return CredentialProof(
            metadata=self._identity.credential_proof,
            raw_proof=build_hmac_sha256_signature(
                request,
                SECRETS[KEYS[IngressSurface.SCHEDULER_ADMIN]],
            ),
        )


def test_http_authority_is_server_owned_valid_and_secret_free(tmp_path: Path) -> None:
    evaluator, store = _evaluator(_database(tmp_path))
    try:
        internal_body = b'{"work_order_id":"wo.test"}'
        internal = InternalAgentIngress(evaluator=evaluator).evaluate_http(
            request=_signed_http_request(
                IngressSurface.INTERNAL_AGENT,
                internal_body,
                suffix="internal",
            ),
            raw_body=internal_body,
            received_at_ms=NOW,
        )
        scheduler_body = b'{"command":"pause","task_id":"task.test"}'
        scheduler = SchedulerCommandIngress(evaluator=evaluator).evaluate_http(
            request=_signed_http_request(
                IngressSurface.SCHEDULER_ADMIN,
                scheduler_body,
                suffix="scheduler",
            ),
            raw_body=scheduler_body,
            received_at_ms=NOW,
        )

        for authority, body, surface in (
            (internal, internal_body, IngressSurface.INTERNAL_AGENT),
            (scheduler, scheduler_body, IngressSurface.SCHEDULER_ADMIN),
        ):
            assert authority.allowed is True
            assert authority.principal_id == PRINCIPALS[surface]
            assert authority.request_id
            assert authority.claim_ref
            assert authority.audit_ref
            assert authority.body_sha256 == hashlib.sha256(body).hexdigest()
            exposed = repr(authority) + json.dumps(authority.to_dict(), sort_keys=True)
            assert SECRETS[KEYS[surface]] not in exposed
            assert "raw_proof" not in exposed
            assert "x-natsume-proof" not in exposed
    finally:
        store.close()


def test_review_http_path_requires_server_owned_review_decide_scope(
    tmp_path: Path,
) -> None:
    evaluator, store = _evaluator(_database(tmp_path))
    body = b'{"review_id":"review.scope.test"}'
    ingress = InternalAgentIngress(evaluator=evaluator)
    review_policy = M48B_P0_SURFACE_POLICIES[IngressSurface.REVIEW_OPERATOR]
    try:
        allowed = ingress.evaluate_http(
            request=_signed_http_request(
                IngressSurface.REVIEW_OPERATOR,
                body,
                suffix="review-operator",
            ),
            raw_body=body,
            received_at_ms=NOW,
        )
        internal_only = ingress.evaluate_http(
            request=_signed_http_request(
                IngressSurface.INTERNAL_AGENT,
                body,
                suffix="internal-only-on-review-path",
                policy_surface=IngressSurface.REVIEW_OPERATOR,
            ),
            raw_body=body,
            received_at_ms=NOW + 1,
        )

        assert review_policy.action == "review:decide"
        assert review_policy.required_scopes == ("review:decide",)
        assert review_policy.default_path == "/internal/codex/reviews"
        assert allowed.allowed is True
        assert allowed.principal_id == PRINCIPALS[IngressSurface.REVIEW_OPERATOR]
        assert allowed.claim_ref and allowed.audit_ref
        assert internal_only.allowed is False
        assert internal_only.admissible is False
        assert internal_only.reason_code == IngressReasonCode.PRINCIPAL_INVALID.value
        assert internal_only.claim_ref is None
        assert internal_only.audit_ref
    finally:
        store.close()


def test_http_duplicate_reuses_first_verified_canonical_authority(
    tmp_path: Path,
) -> None:
    evaluator, store = _evaluator(_database(tmp_path))
    body = b'{"work_order_id":"wo.canonical-replay"}'
    ingress = InternalAgentIngress(evaluator=evaluator)
    try:
        first = ingress.evaluate_http(
            request=_signed_http_request(
                IngressSurface.INTERNAL_AGENT,
                body,
                suffix="canonical-first",
                request_id="request.internal.canonical.first",
                idempotency_key="idem.internal.canonical",
                nonce="nonce.internal.canonical.first",
            ),
            raw_body=body,
            received_at_ms=NOW,
        )
        assert first.allowed is True
        store.complete_claim(
            claim_ref=first.claim_ref or "",
            result_ref="agent-execution://canonical/replay",
            at_ms=NOW + 1,
        )

        duplicate = ingress.evaluate_http(
            request=_signed_http_request(
                IngressSurface.INTERNAL_AGENT,
                body,
                suffix="canonical-second",
                request_id="request.internal.canonical.second",
                idempotency_key="idem.internal.canonical",
                nonce="nonce.internal.canonical.second",
            ),
            raw_body=body,
            received_at_ms=NOW + 2,
        )

        assert duplicate.allowed is False
        assert duplicate.duplicate is True
        assert duplicate.admissible is True
        assert duplicate.request_id == first.request_id
        assert duplicate.claim_ref == first.claim_ref
        assert duplicate.audit_ref == first.audit_ref
        assert duplicate.body_sha256 == first.body_sha256
        assert duplicate.existing_result_ref == "agent-execution://canonical/replay"
        assert duplicate.request_id != "request.internal.canonical.second"
        duplicate_attempt = store.read_audit(
            request_id="request.internal.canonical.second"
        )
        assert len(duplicate_attempt) == 1
        assert duplicate_attempt[0]["reason_code"] == (
            IngressReasonCode.IDEMPOTENCY_DUPLICATE.value
        )
    finally:
        store.close()


def test_http_missing_duplicate_headers_and_non_enforce_fail_closed(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    evaluator, store = _evaluator(db_path)
    body = b"{}"
    try:
        ingress = InternalAgentIngress(evaluator=evaluator)
        missing = ingress.evaluate_http(
            request=_Request(
                path="/internal/codex/work-orders/execute",
                headers=[],
            ),
            raw_body=body,
            received_at_ms=NOW,
        )
        signed = _signed_http_request(
            IngressSurface.INTERNAL_AGENT,
            body,
            suffix="duplicate-header",
        )
        signed.scope["headers"].append((b"X-Natsume-Nonce", b"forged.nonce"))
        duplicate_header = ingress.evaluate_http(
            request=signed,
            raw_body=body,
            received_at_ms=NOW,
        )
        assert missing.allowed is False and missing.audit_ref
        assert duplicate_header.allowed is False and duplicate_header.audit_ref
        assert missing.principal_id is None and missing.claim_ref is None
        assert duplicate_header.principal_id is None
    finally:
        store.close()

    shadow_evaluator, shadow_store = _evaluator(
        db_path,
        mode=SecureIngressMode.SHADOW,
    )
    try:
        shadow = InternalAgentIngress(evaluator=shadow_evaluator).evaluate_http(
            request=_signed_http_request(
                IngressSurface.INTERNAL_AGENT,
                body,
                suffix="shadow",
            ),
            raw_body=body,
            received_at_ms=NOW,
        )
        assert shadow.allowed is False
        assert shadow.duplicate is False
        assert shadow.reason_code == SECURE_INGRESS_ENFORCE_REQUIRED
        assert shadow.claim_ref is None
    finally:
        shadow_store.close()


def test_clock_requires_injected_registered_service_credential_and_replays(
    tmp_path: Path,
) -> None:
    evaluator, store = _evaluator(_database(tmp_path))
    occurrence = {
        "task_id": "task.daily-reflection",
        "definition_revision": 3,
        "scheduled_at_epoch_ms": NOW - 1_000,
        "payload": {"reason": "scheduled"},
        "received_at_ms": NOW,
    }
    try:
        missing = SchedulerCommandIngress(
            evaluator=evaluator,
        ).evaluate_clock_occurrence(**occurrence)
        forged_kind = SchedulerCommandIngress(
            evaluator=evaluator,
            clock_credential_provider=_ClockCredentialProvider(wrong_kind=True),
        ).evaluate_clock_occurrence(**occurrence)
        ingress = SchedulerCommandIngress(
            evaluator=evaluator,
            clock_credential_provider=_ClockCredentialProvider(),
        )
        first = ingress.evaluate_clock_occurrence(**occurrence)
        replay = ingress.evaluate_clock_occurrence(**occurrence)
        conflicting_payload = ingress.evaluate_clock_occurrence(
            **{**occurrence, "payload": {"reason": "tampered"}}
        )

        assert missing.allowed is False
        assert missing.reason_code == SCHEDULER_CLOCK_CREDENTIAL_REQUIRED
        assert missing.audit_ref
        assert forged_kind.allowed is False
        assert forged_kind.reason_code == SCHEDULER_CLOCK_CREDENTIAL_INVALID
        assert forged_kind.audit_ref
        assert first.allowed is True
        assert first.principal_id == PRINCIPALS[IngressSurface.SCHEDULER_ADMIN]
        assert first.request_id and first.claim_ref and first.audit_ref
        assert replay.allowed is False
        assert replay.duplicate is True
        assert replay.admissible is True
        assert replay.request_id == first.request_id
        assert replay.claim_ref == first.claim_ref
        assert replay.audit_ref == first.audit_ref
        assert replay.body_sha256 == first.body_sha256
        assert conflicting_payload.allowed is False
        assert conflicting_payload.duplicate is False
        assert conflicting_payload.reason_code == (
            IngressReasonCode.IDEMPOTENCY_CONFLICT.value
        )
        assert conflicting_payload.request_id == first.request_id
        assert conflicting_payload.claim_ref == first.claim_ref

        parameters = inspect.signature(
            SchedulerCommandIngress.evaluate_clock_occurrence
        ).parameters
        assert "principal_id" not in parameters
        assert "credential" not in parameters
        assert "identity" not in parameters
    finally:
        store.close()


def test_clock_non_enforce_cannot_mint_authority(tmp_path: Path) -> None:
    evaluator, store = _evaluator(
        _database(tmp_path),
        mode=SecureIngressMode.OFF,
    )
    try:
        denied = SchedulerCommandIngress(
            evaluator=evaluator,
            clock_credential_provider=_ClockCredentialProvider(),
        ).evaluate_clock_occurrence(
            task_id="task.off",
            definition_revision=1,
            scheduled_at_epoch_ms=NOW,
            payload={},
            received_at_ms=NOW,
        )
        assert denied.allowed is False
        assert denied.duplicate is False
        assert denied.reason_code == SECURE_INGRESS_ENFORCE_REQUIRED
        assert denied.claim_ref is None
    finally:
        store.close()
