from __future__ import annotations

import base64
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any

import pytest
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
from host_brain.app.pentest_secure_run_routes import (
    create_pentest_secure_run_http_router,
)
from host_brain.app.router_registry import register_host_routers
from host_brain.app.input_runtime_composition import build_input_runtime_application_ports
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.governor.tokens import TokenService
from host_brain.pentest.lab_isolation import PentestEffectAction
from host_brain.pentest.production_runtime import (
    PENTEST_EXECUTOR_AUTHORIZED_LAB,
    PENTEST_EXECUTOR_DISABLED,
    PENTEST_EXECUTOR_RECORDING_ONLY,
    build_pentest_secure_run_composition,
)
from host_brain.persistence.pentest_run_repository import PentestRunStoreError
from host_brain.pentest.secure_run import (
    SECURE_RUN_INGRESS_ACTION,
    SECURE_RUN_NORMALIZED_PATH,
    SECURE_RUN_REQUIRED_SCOPES,
    SECURE_RUN_SURFACE,
    SecureRunRequest,
    build_governor_token_binding,
    canonical_effect_args_digest,
)
from host_brain.secure_ingress import (
    SecureIngressRequest,
    build_hmac_sha256_signature,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain/audit/migrations"
PRINCIPAL_ID = "service.pentest.production.test"
TENANT_ID = "tenant.pentest.production.test"
KEY_ID = "key.pentest.production.test"
AUDIENCE = "host.pentest.secure-run"
SECRET = "task0496-production-hmac-secret-value"
TARGET = "http://127.0.0.1:18080/fixture"
TOOL_ID = "pentest_http_probe"


def test_production_router_registry_mounts_each_secure_route_once() -> None:
    app = FastAPI()
    register_host_routers(
        app,
        input_runtime_ports=build_input_runtime_application_ports(),
        route_ports=HostRoutePorts(),
    )
    paths = [route.path for route in app.routes]

    assert paths.count("/v1/host/obs-commands/{command_id}") == 1
    assert paths.count(SECURE_RUN_NORMALIZED_PATH) == 1
    assert paths.count("/v1/host/skill-provider/intake") == 1

def test_pentest_production_defaults_to_disabled_without_touching_database(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "must-not-be-created.sqlite3"
    composition = build_pentest_secure_run_composition(
        token_service=TokenService(),
        env={},
        db_path=missing,
    )

    assert not composition.enabled
    assert not composition.execution_enabled
    assert composition.executor_mode == PENTEST_EXECUTOR_DISABLED
    assert composition.reason_code == "PENTEST_SECURE_RUN_LAB_ENFORCED_REQUIRED"
    assert not missing.exists()


def test_pentest_recording_route_executes_once_without_real_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ms = int(time.time() * 1_000)
    db_path = _migrated_db(tmp_path, now_ms=now_ms)
    _seed_principal(db_path, now_ms=now_ms)
    env = {
        "HOST_PENTEST_SECURE_RUN_MODE": "LAB_ENFORCED",
        "HOST_SECURE_INGRESS_MODE": "ENFORCE",
        "HOST_PENTEST_PRODUCTION_EXECUTOR_MODE": "RECORDING_ONLY",
        "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON": json.dumps({
            KEY_ID: SECRET,
        }),
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    tokens = TokenService()
    composition = build_pentest_secure_run_composition(
        token_service=tokens,
        env=env,
        db_path=db_path,
    )
    assert composition.enabled
    assert composition.execution_enabled
    assert composition.executor_mode == PENTEST_EXECUTOR_RECORDING_ONLY

    effect_args = {"url": TARGET}
    base_request = SecureRunRequest(
        principal_id=PRINCIPAL_ID,
        tenant_id=TENANT_ID,
        run_id="ptrun_task0496_production",
        tool_id=TOOL_ID,
        target=TARGET,
        action=PentestEffectAction.HTTP_REQUEST.value,
        intent_id="intent.pentest.task0496",
        session_id="session.pentest.task0496",
        seq=49_600,
        effect_args_digest=canonical_effect_args_digest(effect_args),
    )
    binding = build_governor_token_binding(base_request)
    token_args = {
        "intent_id": binding.intent_id,
        "skill_name": binding.skill_name,
        "session_id": binding.session_id,
        "seq": binding.seq,
        "now_ts_ms": now_ms,
    }
    request_with_tokens = replace(
        base_request,
        approval_token_ref=tokens.issue_approval_token(**token_args),
        confirm_token_ref=tokens.issue_confirm_token(**token_args),
    )
    headers = _signed_headers(
        request_with_tokens,
        effect_args=effect_args,
        now_ms=now_ms,
    )

    app = FastAPI()
    app.include_router(
        create_pentest_secure_run_http_router(
            composition_provider=lambda: composition,
        )
    )
    with TestClient(app) as client:
        first = client.post(
            SECURE_RUN_NORMALIZED_PATH,
            content=base_request.canonical_authority_body(),
            headers=headers,
        )
        replay = client.post(
            SECURE_RUN_NORMALIZED_PATH,
            content=base_request.canonical_authority_body(),
            headers=headers,
        )

    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["allowed"] is True
    assert first_body["duplicate"] is False
    assert first_body["execution_mode"] == PENTEST_EXECUTOR_RECORDING_ONLY
    assert first_body["effect_state"] == "EXECUTED"
    assert first_body["physical_effects_executed"] is False
    assert first_body["reason_code"] == "PENTEST_SECURE_EXECUTION_COMPLETED"

    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["allowed"] is True
    assert replay_body["duplicate"] is True
    assert replay_body["effect_state"] == "EXECUTED"
    assert replay_body["physical_effects_executed"] is False
    assert replay_body["reason_code"] == "PENTEST_SECURE_EXECUTION_COMPLETED"
    assert replay_body["result_ref"] == first_body["result_ref"]

    persisted = composition.run_store.get_run(base_request.run_id)
    assert persisted["version"] == 1
    tool_results = persisted["snapshot"]["tool_results"]
    assert len(tool_results) == 1
    assert tool_results[0]["result"]["status"] == "recorded"
    assert tool_results[0]["result"]["network_io"] is False
    assert tool_results[0]["result"]["process_io"] is False
    assert tool_results[0]["result"]["physical_io"] is False
    composition.close()


def test_pentest_authorized_lab_route_calls_real_server_executor_only_after_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full executors are available, but only the signed SecureRun may call one."""

    from host_brain.pentest.tools import tool_executors

    calls: list[dict[str, Any]] = []

    def _authorized_executor(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(args))
        return {
            "status": "authorized-test-executor",
            "network_io": True,
            "process_io": False,
            "physical_io": False,
        }

    monkeypatch.setattr(
        tool_executors,
        "EXECUTOR_MAP",
        {TOOL_ID: _authorized_executor},
    )

    now_ms = int(time.time() * 1_000)
    db_path = _migrated_db(tmp_path, now_ms=now_ms)
    _seed_principal(db_path, now_ms=now_ms)
    env = {
        "HOST_PENTEST_SECURE_RUN_MODE": "LAB_ENFORCED",
        "HOST_SECURE_INGRESS_MODE": "ENFORCE",
        "HOST_PENTEST_PRODUCTION_EXECUTOR_MODE": "AUTHORIZED_LAB",
        "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON": json.dumps({
            KEY_ID: SECRET,
        }),
    }
    tokens = TokenService()
    composition = build_pentest_secure_run_composition(
        token_service=tokens,
        env=env,
        db_path=db_path,
    )
    assert composition.enabled
    assert composition.execution_enabled
    assert composition.executor_mode == PENTEST_EXECUTOR_AUTHORIZED_LAB
    assert composition.reason_code == "PENTEST_PRODUCTION_AUTHORIZED_LAB"

    effect_args = {"url": TARGET}
    base_request = SecureRunRequest(
        principal_id=PRINCIPAL_ID,
        tenant_id=TENANT_ID,
        run_id="ptrun_task0496_authorized_lab",
        tool_id=TOOL_ID,
        target=TARGET,
        action=PentestEffectAction.HTTP_REQUEST.value,
        intent_id="intent.pentest.task0496.authorized-lab",
        session_id="session.pentest.task0496.authorized-lab",
        seq=49_602,
        effect_args_digest=canonical_effect_args_digest(effect_args),
    )
    binding = build_governor_token_binding(base_request)
    token_args = {
        "intent_id": binding.intent_id,
        "skill_name": binding.skill_name,
        "session_id": binding.session_id,
        "seq": binding.seq,
        "now_ts_ms": now_ms,
    }
    request_with_tokens = replace(
        base_request,
        approval_token_ref=tokens.issue_approval_token(**token_args),
        confirm_token_ref=tokens.issue_confirm_token(**token_args),
    )
    headers = _signed_headers(
        request_with_tokens,
        effect_args=effect_args,
        now_ms=now_ms,
    )

    app = FastAPI()
    app.include_router(
        create_pentest_secure_run_http_router(
            composition_provider=lambda: composition,
        )
    )
    with TestClient(app) as client:
        unauthorized = client.post(
            SECURE_RUN_NORMALIZED_PATH,
            content=base_request.canonical_authority_body(),
            headers={},
        )
        first = client.post(
            SECURE_RUN_NORMALIZED_PATH,
            content=base_request.canonical_authority_body(),
            headers=headers,
        )
        replay = client.post(
            SECURE_RUN_NORMALIZED_PATH,
            content=base_request.canonical_authority_body(),
            headers=headers,
        )

    assert unauthorized.status_code in {400, 401, 403}
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["allowed"] is True
    assert first_body["execution_mode"] == PENTEST_EXECUTOR_AUTHORIZED_LAB
    assert first_body["physical_effects_executed"] is True
    assert first_body["reason_code"] == "PENTEST_SECURE_EXECUTION_COMPLETED"
    assert calls == [effect_args]

    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["allowed"] is True
    assert replay_body["duplicate"] is True
    assert replay_body["physical_effects_executed"] is True
    assert replay_body["result_ref"] == first_body["result_ref"]
    assert calls == [effect_args]
    composition.close()


def test_pentest_target_rejection_replay_stays_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ms = int(time.time() * 1_000)
    db_path = _migrated_db(tmp_path, now_ms=now_ms)
    _seed_principal(db_path, now_ms=now_ms)
    env = {
        "HOST_PENTEST_SECURE_RUN_MODE": "LAB_ENFORCED",
        "HOST_SECURE_INGRESS_MODE": "ENFORCE",
        "HOST_PENTEST_PRODUCTION_EXECUTOR_MODE": "RECORDING_ONLY",
        "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON": json.dumps({
            KEY_ID: SECRET,
        }),
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    tokens = TokenService()
    composition = build_pentest_secure_run_composition(
        token_service=tokens,
        env=env,
        db_path=db_path,
    )
    assert composition.enabled

    denied_target = "https://example.com/not-a-lab-fixture"
    effect_args = {"url": denied_target}
    base_request = SecureRunRequest(
        principal_id=PRINCIPAL_ID,
        tenant_id=TENANT_ID,
        run_id="ptrun_task0496_target_denied",
        tool_id=TOOL_ID,
        target=denied_target,
        action=PentestEffectAction.HTTP_REQUEST.value,
        intent_id="intent.pentest.task0496.target-denied",
        session_id="session.pentest.task0496.target-denied",
        seq=49_601,
        effect_args_digest=canonical_effect_args_digest(effect_args),
    )
    binding = build_governor_token_binding(base_request)
    token_args = {
        "intent_id": binding.intent_id,
        "skill_name": binding.skill_name,
        "session_id": binding.session_id,
        "seq": binding.seq,
        "now_ts_ms": now_ms,
    }
    request_with_tokens = replace(
        base_request,
        approval_token_ref=tokens.issue_approval_token(**token_args),
        confirm_token_ref=tokens.issue_confirm_token(**token_args),
    )
    headers = _signed_headers(
        request_with_tokens,
        effect_args=effect_args,
        now_ms=now_ms,
    )

    app = FastAPI()
    app.include_router(
        create_pentest_secure_run_http_router(
            composition_provider=lambda: composition,
        )
    )
    with TestClient(app) as client:
        first = client.post(
            SECURE_RUN_NORMALIZED_PATH,
            content=base_request.canonical_authority_body(),
            headers=headers,
        )
        replay = client.post(
            SECURE_RUN_NORMALIZED_PATH,
            content=base_request.canonical_authority_body(),
            headers=headers,
        )

    assert first.status_code == 403, first.text
    first_body = first.json()
    assert first_body["allowed"] is False
    assert first_body["effect_state"] == "NOT_EXECUTED"
    assert first_body["reason_code"] == "PENTEST_SECURE_RUN_TARGET_DENIED"

    assert replay.status_code == first.status_code, replay.text
    replay_body = replay.json()
    assert replay_body["allowed"] is False
    assert replay_body["duplicate"] is True
    assert replay_body["effect_state"] == first_body["effect_state"]
    assert replay_body["reason_code"] == first_body["reason_code"]
    assert replay_body["result_ref"] == first_body["result_ref"]
    with pytest.raises(PentestRunStoreError, match="PENTEST_RUN_NOT_FOUND"):
        composition.run_store.get_run(base_request.run_id)
    composition.close()


def _migrated_db(tmp_path: Path, *, now_ms: int) -> Path:
    catalog = tmp_path / "migrations-through-0061"
    catalog.mkdir()
    for asset in MIGRATIONS.glob("*.sql"):
        prefix = asset.name.split("_", 1)[0]
        if prefix.isdigit() and int(prefix) <= 61:
            shutil.copy2(asset, catalog / asset.name)
    db_path = tmp_path / "task0496.sqlite3"
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=catalog,
    ).apply_migrations(request_id="task0496-unit", ts_ms=now_ms - 1)
    assert result.success, result.as_dict()
    assert result.summary["current_version"] == 61
    return db_path


def _seed_principal(db_path: Path, *, now_ms: int) -> None:
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
                PRINCIPAL_ID,
                TENANT_ID,
                json.dumps(list(SECURE_RUN_REQUIRED_SCOPES)),
                json.dumps([AUDIENCE]),
                json.dumps([SECURE_RUN_INGRESS_ACTION]),
                now_ms - 1_000,
                now_ms + 300_000,
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
                PRINCIPAL_ID,
                KEY_ID,
                "test-secret-ref://task0496/hmac",
                now_ms - 1_000,
                now_ms + 300_000,
                now_ms,
            ),
        )


def _signed_headers(
    request: SecureRunRequest,
    *,
    effect_args: dict[str, Any],
    now_ms: int,
) -> dict[str, str]:
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=KEY_ID,
        algorithm="hmac-sha256",
        proof_id="proof.pentest.task0496",
        issued_at_epoch_ms=now_ms,
        expires_at_epoch_ms=now_ms + 300_000,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=PRINCIPAL_ID,
            kind=PrincipalKind.SERVICE,
            tenant_id=TENANT_ID,
            granted_scopes=SECURE_RUN_REQUIRED_SCOPES,
        ),
        credential_proof=metadata,
        audience=AUDIENCE,
        request_id="request.pentest.task0496",
        requested_scopes=SECURE_RUN_REQUIRED_SCOPES,
    )
    unsigned = SecureIngressRequest(
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        surface=SECURE_RUN_SURFACE,
        method="POST",
        normalized_path=SECURE_RUN_NORMALIZED_PATH,
        action=SECURE_RUN_INGRESS_ACTION,
        required_scopes=SECURE_RUN_REQUIRED_SCOPES,
        idempotency_key="idem.pentest.task0496",
        nonce="nonce.pentest.task0496",
        raw_body=request.canonical_authority_body(),
        received_at_epoch_ms=now_ms,
        transport_key="http-peer:testclient",
        correlation_id="correlation.pentest.task0496",
    )
    proof = build_hmac_sha256_signature(unsigned, SECRET)
    identity_header = base64.urlsafe_b64encode(
        identity.to_canonical_json().encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    args_header = base64.urlsafe_b64encode(
        json.dumps(
            effect_args,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    return {
        "x-natsume-identity": identity_header,
        "x-natsume-proof": proof,
        "x-natsume-idempotency-key": unsigned.idempotency_key,
        "x-natsume-nonce": unsigned.nonce,
        "x-natsume-correlation-id": str(unsigned.correlation_id),
        "x-natsume-approval-token": str(request.approval_token_ref),
        "x-natsume-confirm-token": str(request.confirm_token_ref),
        "x-natsume-pentest-executor-args": args_header,
    }
