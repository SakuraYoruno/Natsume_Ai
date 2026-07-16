from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)
from host_brain.app.observability_runtime.domain_command_ports import (
    build_default_obs_domain_command_registry,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.command_outcome_journal import ObsCommandOutcomeJournal
from host_brain.observability.command_router import ObsCommandRouter
from host_brain.observability.operator_command_gateway import (
    bind_operator_command_runtime,
)
from host_brain.observability.secure_command_ingress import (
    ObsCommandIngressRequest,
    SecureObsCommandIngress,
    build_obs_command_secure_request,
)
from host_brain.persistence.secure_ingress_repository import (
    SQLiteSecureIngressStore,
)
from host_brain.secure_ingress import (
    CredentialVerifierRegistry,
    FileEmergencyAuditSink,
    HmacSha256CredentialVerifier,
    SecureIngressAdapter,
    SecureIngressMode,
    SecureIngressService,
    build_hmac_sha256_signature,
)
from host_brain.secure_ingress.graph_cancellation_authority import (
    SecureIngressGraphCancellationAuthorityVerifier,
    TASK_GRAPH_CANCEL_ACTION,
    TASK_GRAPH_CANCEL_COMMAND_ID,
    TASK_GRAPH_CANCEL_PATH,
    TASK_GRAPH_CANCEL_REQUIRED_SCOPES,
    canonical_task_graph_cancel_body,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain/audit/migrations"
NOW = 1_800_000_000_000
GRAPH_ID = "graph.ne01.secure-operator-path"
GRAPH_REVISION = 11
REASON_CODE = "OPERATOR_REQUEST"
PRINCIPAL_ID = "service.obs.graph-cancellation.integration"
TENANT_ID = "tenant.ne01.integration"
KEY_ID = "key.obs.graph-cancellation.integration"
SECRET = "ne01-graph-cancellation-integration-secret"


class _VerifyingCancellationService:
    def __init__(
        self,
        verifier: SecureIngressGraphCancellationAuthorityVerifier,
    ) -> None:
        self._verifier = verifier
        self.calls: list[dict[str, Any]] = []

    def request_graph_cancellation(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        reason_code: str,
        authority: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        verified = dict(
            self._verifier.verify_graph_cancellation(
                authority=authority,
                source="OPERATOR",
                reason_code=reason_code,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
        )
        self.calls.append({"authority": dict(authority), "verified": verified})
        return {
            "graph_id": graph_id,
            "graph_revision": graph_revision,
            "status": "CANCELLATION_REQUESTED",
            "request_identity": verified["request_identity"],
        }


def test_authenticated_operator_command_binds_durable_authority_to_exact_revision(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "ne01-secure-operator-path.sqlite3"
    migration = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=MIGRATIONS,
    ).apply_migrations(request_id="ne01-secure-operator-path", ts_ms=NOW - 1)
    assert migration.success, migration.as_dict()

    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            """
            INSERT INTO secure_ingress_principal(
                principal_id, principal_kind, tenant_id, status, version,
                granted_scopes_json, allowed_audiences_json,
                allowed_actions_json, valid_from_ms, valid_until_ms,
                updated_at_ms
            ) VALUES (?, 'service', ?, 'ACTIVE', 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                PRINCIPAL_ID,
                TENANT_ID,
                json.dumps(list(TASK_GRAPH_CANCEL_REQUIRED_SCOPES)),
                json.dumps(["obs"]),
                json.dumps([TASK_GRAPH_CANCEL_ACTION]),
                NOW - 1_000,
                NOW + 300_000,
                NOW,
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
                "test-secret-ref://ne01/operator-cancellation",
                NOW - 1_000,
                NOW + 300_000,
                NOW,
            ),
        )

    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    registry = build_default_obs_domain_command_registry()
    service = SecureIngressService(
        principal_store=store,
        credential_verifier=CredentialVerifierRegistry(
            {
                CredentialProofKind.HMAC_SHA256: HmacSha256CredentialVerifier(
                    secret_resolver=lambda authority: (
                        SECRET if authority.key_id == KEY_ID else None
                    ),
                )
            }
        ),
        attempt_limiter=store,
        rate_limiter=store,
        state_store=store,
        emergency_audit_sink=FileEmergencyAuditSink(
            tmp_path / "ne01-secure-operator-path.emergency.jsonl"
        ),
    )
    verifier = SecureIngressGraphCancellationAuthorityVerifier(store)
    cancellation_service = _VerifyingCancellationService(verifier)
    ingress = SecureObsCommandIngress(
        ingress_adapter=SecureIngressAdapter(
            service=service,
            mode=SecureIngressMode.ENFORCE,
        ),
        ingress_service=service,
        claim_result_store=store,
        outcome_journal=ObsCommandOutcomeJournal(
            tmp_path / "ne01-secure-operator-path.outcomes.jsonl",
            clock_ms=lambda: NOW + 1,
        ),
        registry=registry,
        router=ObsCommandRouter(registry),
        clock_ms=lambda: NOW + 1,
    )

    raw_body = canonical_task_graph_cancel_body(
        graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
        reason_code=REASON_CODE,
    )
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=KEY_ID,
        algorithm="hmac-sha256",
        proof_id="proof.ne01.secure-operator-path",
        issued_at_epoch_ms=NOW,
        expires_at_epoch_ms=NOW + 60_000,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=PRINCIPAL_ID,
            kind=PrincipalKind.SERVICE,
            tenant_id=TENANT_ID,
            granted_scopes=TASK_GRAPH_CANCEL_REQUIRED_SCOPES,
        ),
        credential_proof=metadata,
        audience="obs",
        request_id="request.ne01.secure-operator-path",
        requested_scopes=TASK_GRAPH_CANCEL_REQUIRED_SCOPES,
    )
    unsigned = ObsCommandIngressRequest(
        command_id=TASK_GRAPH_CANCEL_COMMAND_ID,
        raw_body=raw_body,
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        idempotency_key="idem.ne01.secure-operator-path",
        nonce="nonce.ne01.secure-operator-path",
        received_at_epoch_ms=NOW,
        transport_key="loopback:obs:ne01-secure-operator-path",
        normalized_path=TASK_GRAPH_CANCEL_PATH,
        correlation_id="correlation.ne01.secure-operator-path",
    )
    spec = registry.resolve_spec(TASK_GRAPH_CANCEL_COMMAND_ID)
    assert spec is not None
    secure_request = build_obs_command_secure_request(unsigned, spec)
    signed = replace(
        unsigned,
        credential=CredentialProof(
            metadata=metadata,
            raw_proof=build_hmac_sha256_signature(secure_request, SECRET),
        ),
    )

    try:
        app_state = SimpleNamespace(
            task_graph_cancellation_command_service=cancellation_service,
        )
        with bind_operator_command_runtime(app_state, raw_body=raw_body):
            outcome = asyncio.run(ingress.execute(signed))

        assert outcome.allowed is True
        assert outcome.executed is True
        assert outcome.reason_code == "OBS_COMMAND_COMPLETED"
        assert len(cancellation_service.calls) == 1
        call = cancellation_service.calls[0]
        assert call["verified"] == {
            "request_id": identity.request_id,
            "request_identity": outcome.audit_ref,
            "principal_id": PRINCIPAL_ID,
            "source": "OPERATOR",
            "reason_code": REASON_CODE,
            "graph_id": GRAPH_ID,
            "graph_revision": GRAPH_REVISION,
        }
        assert call["authority"]["audit_ref"] == outcome.audit_ref
        assert call["authority"]["claim_ref"] == outcome.claim_ref
        assert call["authority"]["body_sha256"] == hashlib.sha256(raw_body).hexdigest()
        assert call["authority"]["action"] == TASK_GRAPH_CANCEL_ACTION
        assert tuple(call["authority"]["required_scopes"]) == (
            TASK_GRAPH_CANCEL_REQUIRED_SCOPES
        )
        durable = store.resolve_committed_authority_by_audit(
            audit_ref=str(outcome.audit_ref),
        )
        assert durable is not None
        assert durable["request_id"] == identity.request_id
        assert durable["body_sha256"] == hashlib.sha256(raw_body).hexdigest()
        assert durable["claim_status"] == "COMMITTED"
        assert durable["claim_result_ref"] == outcome.result_ref

        with pytest.raises(
            ValueError,
            match="TASK_GRAPH_CANCELLATION_AUTHORITY_ALREADY_SETTLED",
        ):
            verifier.verify_graph_cancellation(
                authority=call["authority"],
                source="OPERATOR",
                reason_code=REASON_CODE,
                graph_id=GRAPH_ID,
                graph_revision=GRAPH_REVISION,
            )

        duplicate_unsigned = replace(
            unsigned,
            nonce="nonce.ne01.secure-operator-path.duplicate",
            credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        )
        duplicate_secure_request = build_obs_command_secure_request(
            duplicate_unsigned,
            spec,
        )
        duplicate_signed = replace(
            duplicate_unsigned,
            credential=CredentialProof(
                metadata=metadata,
                raw_proof=build_hmac_sha256_signature(
                    duplicate_secure_request,
                    SECRET,
                ),
            ),
        )
        with bind_operator_command_runtime(app_state, raw_body=raw_body):
            duplicate = asyncio.run(ingress.execute(duplicate_signed))
        assert duplicate.duplicate is True
        assert duplicate.executed is False
        assert duplicate.result_ref == outcome.result_ref
        assert len(cancellation_service.calls) == 1

        tampered_body = canonical_task_graph_cancel_body(
            graph_id=GRAPH_ID,
            graph_revision=GRAPH_REVISION + 1,
            reason_code=REASON_CODE,
        )
        with bind_operator_command_runtime(app_state, raw_body=tampered_body):
            tampered = asyncio.run(
                ingress.execute(replace(signed, raw_body=tampered_body))
            )
        assert tampered.allowed is False
        assert tampered.executed is False
        assert tampered.reason_code == "SECURE_INGRESS_CREDENTIAL_INVALID"
        assert len(cancellation_service.calls) == 1
    finally:
        store.close()
