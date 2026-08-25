from __future__ import annotations

import base64
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping

from fastapi import FastAPI
from fastapi.testclient import TestClient

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)
from host_brain.app.obs_fastapi_app import app as obs_app
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.app.observability_runtime import domain_command_ports
from host_brain.app.observability_runtime.command_composition import build_obs_command_composition
from host_brain.observability.command_http import create_obs_command_http_router
from host_brain.observability.command_outcome_journal import (
    ObsCommandOutcomeJournal,
    ObsCommandOutcomeState,
    SideEffectCertainty,
)
from host_brain.observability.command_ports import (
    DEFAULT_OBS_COMMAND_SPECS,
    OBS_COMMAND_ENVELOPE_SCHEMA,
)
from tests.support.obs_query_boundary import require_query_only_routes
from host_brain.observability.secure_command_ingress import (
    ObsCommandIngressRequest,
    build_obs_command_secure_request,
)
from host_brain.secure_ingress import build_hmac_sha256_signature
from tests.fixtures.m48b_isolation import build_m48b_isolation_harness


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"
COMMAND_ID = "sys_mode.switch"


def _migrated_db(tmp_path: Path, *, now_ms: int) -> Path:
    db_path = tmp_path / "task0489.sqlite3"
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=MIGRATIONS,
    ).apply_migrations(request_id="task0489-integration", ts_ms=now_ms - 1)
    assert result.success, result.as_dict()
    assert result.summary["current_version"] >= 59
    return db_path


def _seed_command_principal(
    db_path: Path,
    *,
    principal_id: str,
    tenant_id: str,
    key_id: str,
    action: str,
    scopes: tuple[str, ...],
    now_ms: int,
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
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
                principal_id,
                tenant_id,
                json.dumps(list(scopes)),
                json.dumps(["obs"]),
                json.dumps([action]),
                now_ms - 60_000,
                now_ms + 3_600_000,
                now_ms,
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
                now_ms - 60_000,
                now_ms + 3_600_000,
                now_ms,
            ),
        )


def _identity(
    *,
    principal_id: str,
    tenant_id: str,
    key_id: str,
    scopes: tuple[str, ...],
    now_ms: int,
) -> IngressIdentityEnvelope:
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=key_id,
        algorithm="hmac-sha256",
        proof_id="task0489.obs.proof.1",
        issued_at_epoch_ms=now_ms,
        expires_at_epoch_ms=now_ms + 600_000,
    )
    return IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=principal_id,
            kind=PrincipalKind.SERVICE,
            tenant_id=tenant_id,
            granted_scopes=scopes,
        ),
        credential_proof=metadata,
        audience="obs",
        request_id="task0489.obs.request.1",
        requested_scopes=scopes,
    )


def _signed_headers(
    *,
    registry: Any,
    identity: IngressIdentityEnvelope,
    secret: str,
    raw_body: bytes,
    idempotency_key: str,
    nonce: str,
    now_ms: int,
) -> dict[str, str]:
    path = f"/v1/host/obs-commands/{COMMAND_ID}"
    spec = registry.resolve_spec(COMMAND_ID)
    assert spec is not None
    unsigned = ObsCommandIngressRequest(
        command_id=COMMAND_ID,
        raw_body=raw_body,
        identity=identity,
        credential=CredentialProof(
            metadata=identity.credential_proof,
            raw_proof="0" * 64,
        ),
        idempotency_key=idempotency_key,
        nonce=nonce,
        received_at_epoch_ms=now_ms,
        # Starlette TestClient exposes this server-observed ASGI peer.  The
        # HTTP adapter deliberately ignores principal/X-Forwarded-For values
        # when constructing the first-layer transport rate bucket.
        transport_key="http-peer:testclient",
        normalized_path=path,
        correlation_id="task0489.obs.correlation.1",
    )
    secure_request = build_obs_command_secure_request(unsigned, spec)
    signature = build_hmac_sha256_signature(secure_request, secret)
    encoded_identity = base64.urlsafe_b64encode(
        identity.to_canonical_json().encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    return {
        "Content-Type": "application/json",
        "X-Natsume-Identity": encoded_identity,
        "X-Natsume-Proof": signature,
        "X-Natsume-Idempotency-Key": idempotency_key,
        "X-Natsume-Nonce": nonce,
        "X-Natsume-Correlation-Id": "task0489.obs.correlation.1",
    }


def _http_app(composition: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(create_obs_command_http_router(
        ingress_provider=lambda: composition.ingress,
    ))
    return app


def test_task0489_query_only_secure_command_restart_wave(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now_ms = int(time.time() * 1000)
    harness = build_m48b_isolation_harness(
        domain="obs",
        state_root=tmp_path / "isolation",
    )
    db_path = _migrated_db(tmp_path, now_ms=now_ms)
    principal_id = harness.principal
    tenant_id = "tenant.task0489"
    key_id = "obs.command.task0489.hmac.v1"
    secret = harness.test_secret
    captured: list[dict[str, Any]] = []

    def _recording_sysmode(
        payload: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        captured.append({
            "payload": dict(payload),
            "request_id": request_id,
            "executed": False,
        })
        return {"success": True, "recording_only": True}

    monkeypatch.setitem(
        domain_command_ports._HANDLERS,  # noqa: SLF001 - integration seam
        COMMAND_ID,
        _recording_sysmode,
    )
    bootstrap_registry = domain_command_ports.build_default_obs_domain_command_registry()
    spec = bootstrap_registry.resolve_spec(COMMAND_ID)
    assert spec is not None
    _seed_command_principal(
        db_path,
        principal_id=principal_id,
        tenant_id=tenant_id,
        key_id=key_id,
        action=spec.action,
        scopes=spec.required_scopes,
        now_ms=now_ms,
    )
    env = {
        "HOST_SECURE_INGRESS_MODE": "ENFORCE",
        "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON": json.dumps({key_id: secret}),
    }
    identity = _identity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        key_id=key_id,
        scopes=spec.required_scopes,
        now_ms=now_ms,
    )
    raw_body = json.dumps(
        {
            "schema_version": OBS_COMMAND_ENVELOPE_SCHEMA,
            "command_id": COMMAND_ID,
            "payload": {
                "target_mode": "focus",
                "reason": "TASK-0489 isolated integration",
                "operator": "attacker-supplied",
                "owner_organ": "attacker-supplied",
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    # The real Obs application is already Query-only before the new Host
    # command component is mounted; the seven known duplicate GET routes are
    # query-only debt and contain no writer.
    query_audit = require_query_only_routes(obs_app)
    assert query_audit.is_query_only is True
    assert query_audit.mutation_entries == ()

    first_composition = build_obs_command_composition(env=env, db_path=db_path)
    assert first_composition.execution_enabled is True
    assert len(first_composition.registry.bound_command_ids) == len(
        DEFAULT_OBS_COMMAND_SPECS
    )
    path = f"/v1/host/obs-commands/{COMMAND_ID}"
    first_headers = _signed_headers(
        registry=first_composition.registry,
        identity=identity,
        secret=secret,
        raw_body=raw_body,
        idempotency_key="task0489.obs.idem.1",
        nonce="task0489.obs.nonce.1",
        now_ms=now_ms,
    )
    with TestClient(_http_app(first_composition)) as client:
        first = client.post(path, content=raw_body, headers=first_headers)
        assert first.status_code == 503, first.text
        assert first.json()["status"] == "uncertain"
        assert first.json()["reason_code"] == "OBS_COMMAND_EXECUTION_UNCERTAIN"
        assert captured == []

        duplicate_headers = _signed_headers(
            registry=first_composition.registry,
            identity=identity,
            secret=secret,
            raw_body=raw_body,
            idempotency_key="task0489.obs.idem.1",
            nonce="task0489.obs.nonce.2",
            now_ms=now_ms,
        )
        duplicate = client.post(path, content=raw_body, headers=duplicate_headers)
        assert duplicate.status_code == 503, duplicate.text
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["reason_code"] == "OBS_COMMAND_EXECUTION_UNCERTAIN"
        assert duplicate.json()["result_ref"] == first.json()["result_ref"]
        assert captured == []

        tampered_body = raw_body.replace(b'"focus"', b'"locked"')
        tampered = client.post(path, content=tampered_body, headers=duplicate_headers)
        assert tampered.status_code == 401, tampered.text
        assert captured == []
    first_result_ref = first.json()["result_ref"]
    first_composition.close()

    # A fresh composition over the same central store returns the completed
    # result reference and never invokes the domain port again.
    restarted = build_obs_command_composition(env=env, db_path=db_path)
    assert restarted.execution_enabled is True
    restart_headers = _signed_headers(
        registry=restarted.registry,
        identity=identity,
        secret=secret,
        raw_body=raw_body,
        idempotency_key="task0489.obs.idem.1",
        nonce="task0489.obs.nonce.3",
        now_ms=now_ms,
    )
    try:
        with TestClient(_http_app(restarted)) as client:
            after_restart = client.post(path, content=raw_body, headers=restart_headers)
        assert after_restart.status_code == 503, after_restart.text
        assert after_restart.json()["duplicate"] is True
        assert after_restart.json()["reason_code"] == "OBS_COMMAND_EXECUTION_UNCERTAIN"
        assert after_restart.json()["result_ref"] == first_result_ref
        assert captured == []
    finally:
        restarted.close()

    with sqlite3.connect(str(db_path)) as conn:
        claim = conn.execute(
            """
            SELECT action, required_scopes_json, body_sha256, result_ref
            FROM secure_ingress_idempotency_claim
            WHERE principal_id = ?
            """,
            (principal_id,),
        ).fetchone()
        audits = conn.execute(
            """
            SELECT action, decision, reason_code, body_sha256,
                   side_effects_executed
            FROM secure_ingress_audit
            WHERE principal_id = ?
            ORDER BY ts_ms, audit_ref
            """,
            (principal_id,),
        ).fetchall()
        audit_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(secure_ingress_audit)")
        }
    assert claim is not None
    assert claim[0] == spec.action
    assert tuple(json.loads(claim[1])) == tuple(sorted(spec.required_scopes))
    assert claim[3] == first_result_ref
    assert audits and all(int(row[4]) == 0 for row in audits)
    assert "raw_body" not in audit_columns
    serialized_evidence = json.dumps([tuple(row) for row in audits])
    assert secret not in serialized_evidence
    assert "attacker-supplied" not in serialized_evidence

    outcome_path = db_path.with_suffix(".obs-command-outcomes.jsonl")
    outcome_records = ObsCommandOutcomeJournal(outcome_path).records()
    # Select the authenticated command lane independently of opaque identity
    # digests.  The tampered unauthenticated attempt is audited centrally but
    # must not be able to append to the durable command-outcome WAL.
    command_records = [
        record for record in outcome_records
        if record.key.command_id == COMMAND_ID
        and record.state in {
            ObsCommandOutcomeState.EXECUTING,
            ObsCommandOutcomeState.UNCERTAIN,
        }
    ]
    assert len(outcome_records) == 2
    assert [record.state for record in command_records] == [
        ObsCommandOutcomeState.EXECUTING,
        ObsCommandOutcomeState.UNCERTAIN,
    ]
    assert (
        command_records[-1].side_effect_certainty
        is SideEffectCertainty.MAY_HAVE_EXECUTED
    )
    assert command_records[-1].receipt_ref == first_result_ref
    wal_text = outcome_path.read_text(encoding="utf-8")
    assert secret not in wal_text
    assert "attacker-supplied" not in wal_text
    assert "raw_body" not in wal_text
