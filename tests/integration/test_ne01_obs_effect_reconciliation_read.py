from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from host_brain.app.obs_fastapi_app import create_obs_app
from host_brain.app.obs_routes_task_runtime import (
    get_task_effect_reconciliation_challenge,
)
from host_brain.app.settings.core import get_core_settings
from host_brain.composition.obs_application import ObsApplicationPorts
from host_brain.observability.metrics import MetricsCollector
from host_brain.observability.operator_query_gateway import (
    DigestOperatorQueryAuthenticator,
    OPERATOR_QUERY_AUTH_BINDINGS_ENV,
    OperatorQueryGateway,
)
from host_brain.persistence.reconciliation_proof_verifier import (
    Ed25519ReconciliationProofVerifier,
)
from host_brain.persistence.task_repository import (
    OutboxMessage,
)
from host_brain.task_runtime.graph_cancellation_effect_reconciliation import (
    GraphCancellationEffectReconciliationService,
)
from tests.contract.test_ne01_graph_cancellation_store import (
    NOW,
    _request_snapshot,
    _stage_effect,
    _store,
)
from tests.fakes.obs_projection_runtime import compose_durable_obs_projection_runtime


def test_dedicated_obs_app_reads_effect_reconciliation_from_canonical_db_read_only(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generic_token = "generic-observability-token"
    task_runtime_token = "task-runtime-read-token"
    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "true")
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(generic_token.encode()).hexdigest(),
                    "principal_id": "ops.generic",
                    "scopes": ["obs:operator:read"],
                },
                {
                    "token_sha256": hashlib.sha256(
                        task_runtime_token.encode()
                    ).hexdigest(),
                    "principal_id": "ops.task-runtime",
                    "scopes": ["obs:task-runtime:read"],
                },
            ]
        ),
    )
    get_core_settings.cache_clear()
    try:
        with compose_durable_obs_projection_runtime(
            tmp_path,
            request_id="obs-effect-reconciliation-read",
        ) as runtime:
            isolated_app = create_obs_app(
                ports=ObsApplicationPorts(
                    ledger_query=runtime.ledger,
                    query_projection_store=runtime.store,
                    metrics_reader=MetricsCollector(ledger=runtime.ledger),
                    life_snapshot_reader=lambda person_id: {
                        "person_id": person_id,
                        "state": {},
                    },
                )
            )
            with TestClient(isolated_app) as client:
                path = "/obs/task-runtime/effect-reconciliation/cases"
                denied = client.get(
                    path,
                    headers={"Authorization": f"Bearer {generic_token}"},
                )
                assert denied.status_code == 403

                for readable_path in (
                    path,
                    "/obs/task-runtime/effect-reconciliation/candidates",
                ):
                    response = client.get(
                        readable_path,
                        headers={"Authorization": f"Bearer {task_runtime_token}"},
                    )
                    assert response.status_code == 200, response.text
                    assert response.json()["count"] == 0

                missing = client.get(
                    "/obs/task-runtime/effect-reconciliation/challenge",
                    params={
                        "intent_ref": "cancel-intent://missing",
                        "effect_id": "effect://missing",
                    },
                    headers={"Authorization": f"Bearer {task_runtime_token}"},
                )
                assert missing.status_code == 404, missing.text
                assert (
                    missing.json()["detail"]["reason_code"]
                    == "TASK_STORE_NOT_FOUND"
                )

                reader = isolated_app.state.task_effect_reconciliation_read_authority
                assert reader.canonical_db_path == Path(
                    runtime.ledger.canonical_db_path
                ).resolve()
                conn = reader._connect()
                try:
                    assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
                    with pytest.raises(sqlite3.OperationalError):
                        conn.execute(
                            "CREATE TABLE obs_reader_must_not_write(value TEXT)"
                        )
                finally:
                    conn.close()
    finally:
        get_core_settings.cache_clear()


def test_operator_challenge_round_trips_through_real_service_and_signature_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    store = _store(
        tmp_path,
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {"effect-reconciliation-v1": public_key}
        ),
    )
    effect_id = "effect://reconciliation/operator-challenge"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'FAILED', status_version = 2,
                lease_owner = 'effect-dispatcher-operator-challenge',
                lease_expires_ts_ms = NULL,
                fencing_token = 1, attempt_count = 1,
                reason_code = 'ACCEPTANCE_EFFECT_HANDLER_FAILED',
                last_error = 'private transport failure',
                failed_ts_ms = ?, updated_ts_ms = ?
            WHERE effect_id = ?
            """,
            (NOW, NOW, effect_id),
        )
        conn.commit()
    requested = _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )
    intent_ref = str(requested["intent"]["intent_ref"])
    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()
    try:
        challenge = asyncio.run(
            get_task_effect_reconciliation_challenge(
                intent_ref=intent_ref,
                effect_id=effect_id,
                store=store,
            )
        )
    finally:
        get_core_settings.cache_clear()
    transport = OperatorQueryGateway(
        DigestOperatorQueryAuthenticator()
    ).serialize_public(challenge)
    assert transport.redacted_fields == 0
    transport_challenge = transport.payload
    signing_payload = base64.urlsafe_b64decode(
        transport_challenge["signing_material"]["payload_b64"].encode("ascii")
    )
    assert transport_challenge["signing_material"]["payload_sha256"] == (
        "sha256:" + hashlib.sha256(signing_payload).hexdigest()
    )
    offline_snapshot = json.loads(signing_payload)

    wakes: list[str] = []
    service = GraphCancellationEffectReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        wake_cancellation=lambda: wakes.append("wake"),
        clock_ms=lambda: NOW,
    )
    request = {
        "intent_ref": intent_ref,
        "effect_id": effect_id,
        "case_id": offline_snapshot["case"]["case_id"],
        "expected_case_snapshot_sha256": offline_snapshot["case"][
            "snapshot_sha256"
        ],
        "expected_subject_snapshot_sha256": offline_snapshot[
            "subject_snapshot_sha256"
        ],
        "purpose": offline_snapshot["case"]["purpose"],
        "outcome": "NO_SIDE_EFFECT",
        "reconciler_actor_id": "reconciler://integration/operator-challenge",
        "issued_ts_ms": NOW,
        "downstream_receipt_ref": None,
        "executor_quiescence_ref": None,
        "attestation_ref": "proof://effect/operator-challenge",
        "attestation_key_id": "effect-reconciliation-v1",
        "attestation_signature": "ed25519:" + "A" * 86 + "==",
    }
    normalized = service._normalize_request(**request)
    offline_snapshot["proof"] = None
    artifacts = service._build_artifacts(
        snapshot=offline_snapshot,
        request=normalized,
    )
    request["attestation_signature"] = "ed25519:" + base64.urlsafe_b64encode(
        private_key.sign(artifacts["binding_bytes"])
    ).decode("ascii")

    result = service.reconcile_graph_cancellation_effect(**request)

    assert result["status"] == "PROOF_RECORDED"
    assert wakes == ["wake"]
    persisted = store.read_graph_cancellation_effect_reconciliation(
        intent_ref,
        effect_id,
    )["proof"]
    assert persisted is not None
    binding = json.loads(persisted["proof_binding_json"])
    for field_name in ("case", "intent", "effect", "fence", "attempt"):
        assert binding[field_name] == offline_snapshot[field_name]
    assert binding["outbox"] == artifacts["binding"]["outbox"]
    assert persisted["proof_digest"] == artifacts["proof_digest"]
    assert "private transport failure" not in persisted["proof_binding_json"]
