from __future__ import annotations

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
from host_brain.app.settings.core import get_core_settings
from host_brain.app.obs_application import ObsApplicationPorts
from host_brain.observability.metrics import MetricsCollector
from host_brain.observability.operator_query_gateway import (
    OPERATOR_QUERY_AUTH_BINDINGS_ENV,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.persistence.reconciliation_proof_verifier import (
    Ed25519ReconciliationProofVerifier,
)
from host_brain.persistence.task_repository import DurableTaskStore, OutboxMessage
from host_brain.task_runtime.terminal_state_reconciliation import (
    TerminalStateReconciliationService,
)
from tests.contract.test_ne01_atomic_failure_bundle import (
    NOW,
    MutableClock,
    _event,
    _graph,
)
from tests.unit.test_ne01_terminal_reconciliation_classification import (
    _active_budget,
)
from tests.fakes.obs_projection_runtime import compose_durable_obs_projection_runtime


def test_dedicated_obs_app_reads_terminal_reconciliation_query_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generic_token = "generic-observability-token"
    task_runtime_token = "terminal-task-runtime-read-token"
    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "true")
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(
                        generic_token.encode()
                    ).hexdigest(),
                    "principal_id": "ops.generic",
                    "scopes": ["obs:operator:read"],
                },
                {
                    "token_sha256": hashlib.sha256(
                        task_runtime_token.encode()
                    ).hexdigest(),
                    "principal_id": "ops.task-runtime",
                    "scopes": ["obs:task-runtime:read"],
                    "key_id": "terminal-reconciliation-v1",
                },
            ]
        ),
    )
    get_core_settings.cache_clear()
    try:
        with compose_durable_obs_projection_runtime(
            tmp_path,
            request_id="obs-terminal-reconciliation-read",
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
                path = "/obs/task-runtime/terminal-reconciliation/cases"
                denied = client.get(
                    path,
                    headers={"Authorization": f"Bearer {generic_token}"},
                )
                assert denied.status_code == 403

                for readable_path in (
                    path,
                    "/obs/task-runtime/terminal-reconciliation/candidates",
                ):
                    response = client.get(
                        readable_path,
                        headers={
                            "Authorization": f"Bearer {task_runtime_token}"
                        },
                    )
                    assert response.status_code == 200, response.text
                    assert response.json()["read_only"] is True
                    assert response.json()["count"] == 0

                missing = client.get(
                    "/obs/task-runtime/terminal-reconciliation/challenge",
                    params={
                        "case_id": "terminal-reconciliation-case://missing",
                        "attestation_ref": (
                            "attestation://terminal-reconciliation/missing"
                        ),
                        "attestation_key_id": "terminal-reconciliation-v1",
                        "issued_ts_ms": 1_900_000_000_000,
                    },
                    headers={"Authorization": f"Bearer {task_runtime_token}"},
                )
                assert missing.status_code == 404, missing.text
                assert (
                    missing.json()["detail"]["reason_code"]
                    == "TASK_STORE_NOT_FOUND"
                )

                reader = isolated_app.state.task_terminal_reconciliation_read_authority
                assert reader.canonical_db_path == Path(
                    runtime.ledger.canonical_db_path
                ).resolve()
                conn = reader._connect()
                try:
                    assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
                    with pytest.raises(sqlite3.OperationalError):
                        conn.execute(
                            "CREATE TABLE terminal_obs_reader_must_not_write(value TEXT)"
                        )
                finally:
                    conn.close()
            assert isolated_app.state.task_terminal_reconciliation_read_authority is None
    finally:
        get_core_settings.cache_clear()


def test_http_challenge_bytes_round_trip_through_signed_terminal_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "terminal-reconciliation-v1"
    token = "terminal-reconciliation-signer-token"
    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "true")
    monkeypatch.setenv(
        "HOST_TASK_RECONCILIATION_ED25519_PUBLIC_KEYS_JSON",
        json.dumps(
            {
                key_id: "ed25519:"
                + base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("=")
            },
            separators=(",", ":"),
        ),
    )
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    "principal_id": "ops.terminal-signer",
                    "scopes": ["obs:task-runtime:read"],
                    "key_id": key_id,
                }
            ]
        ),
    )
    get_core_settings.cache_clear()
    try:
        with compose_durable_obs_projection_runtime(
            tmp_path,
            request_id="obs-terminal-reconciliation-round-trip",
        ) as runtime:
            store = DurableTaskStore(
                runtime.ledger.canonical_db_path,
                clock=MutableClock(),
                reconciliation_proof_verifier=(
                    Ed25519ReconciliationProofVerifier({key_id: public_key})
                ),
            )
            graph = _graph()
            store.create_graph(
                graph,
                outbox=_event(
                    "TASK_GRAPH",
                    graph.graph_id,
                    0,
                    "TASK_GRAPH_CREATED",
                    identity="terminal-http-created",
                ),
            )
            store.transition_graph(
                graph.graph_id,
                graph.revision,
                expected_version=0,
                target_status=TaskGraphStatus.ACTIVE,
                outbox=_event(
                    "TASK_GRAPH",
                    graph.graph_id,
                    1,
                    "TASK_GRAPH_ACTIVE",
                    identity="terminal-http-active",
                ),
            )
            budget_id = _active_budget(
                store,
                budget_id="budget-terminal-http-challenge",
            )
            store.transition_graph(
                graph.graph_id,
                graph.revision,
                expected_version=1,
                target_status=TaskGraphStatus.CANCELLED,
                outbox=_event(
                    "TASK_GRAPH",
                    graph.graph_id,
                    2,
                    "TASK_GRAPH_CANCELLED",
                    identity="terminal-http-cancelled",
                ),
            )
            inventory = store.inventory_terminal_reconciliation_cases()
            case_id = inventory["cases"][0]["case_id"]
            attestation_ref = "attestation://terminal-reconciliation/http-round-trip"
            issued_ts_ms = NOW + 300

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
                response = client.get(
                    "/obs/task-runtime/terminal-reconciliation/challenge",
                    params={
                        "case_id": case_id,
                        "attestation_ref": attestation_ref,
                        "attestation_key_id": key_id,
                        "issued_ts_ms": issued_ts_ms,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert response.status_code == 200, response.text
                challenge = response.json()

            binding_bytes = base64.urlsafe_b64decode(
                challenge["signing_material"]["payload_b64"].encode("ascii")
            )
            assert challenge["signing_material"]["payload_sha256"] == (
                "sha256:" + hashlib.sha256(binding_bytes).hexdigest()
            )
            binding = json.loads(binding_bytes)
            assert binding["authority"]["reconciler_actor_id"] == (
                "ops.terminal-signer"
            )
            assert binding["authority"]["attestation_key_id"] == key_id
            assert binding["outboxes"]["budget"]["attempt_id"] is None

            signature = "ed25519:" + base64.urlsafe_b64encode(
                private_key.sign(binding_bytes)
            ).decode("ascii")
            result = TerminalStateReconciliationService(
                task_store=store,
                outbox_factory=OutboxMessage,
                clock_ms=lambda: issued_ts_ms,
            ).reconcile_terminal_state(
                **challenge["command_request"],
                reconciler_actor_id="ops.terminal-signer",
                attestation_signature=signature,
            )

            assert result["status"] == "RECONCILED"
            assert result["graph"]["status"] == "CANCELLED"
            assert result["budget"]["budget_id"] == budget_id
            assert result["budget"]["status"] == "RELEASED"
            persisted = store.read_terminal_reconciliation_case(case_id)
            assert persisted["proof"]["proof_id"] == challenge["record_ref"]
            assert persisted["proof"]["proof_digest"] == challenge[
                "signing_material"
            ]["payload_sha256"]
    finally:
        get_core_settings.cache_clear()
