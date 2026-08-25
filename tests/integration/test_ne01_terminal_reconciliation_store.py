from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Barrier
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.contracts.agent_runtime.terminal_reconciliation import (
    TerminalReconciliationStoreRejected,
    build_terminal_reconciliation_attestation_binding,
)
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
    TASK_STORE_BUDGET_EXCEEDED,
    build_outbox_payload_ref,
)
from host_brain.persistence.terminal_reconciliation_query_repository import (
    TerminalReconciliationQueryRepository,
)
from host_brain.task_runtime.terminal_state_reconciliation import (
    TerminalStateReconciliationService,
)
from tests.task_runtime_authority_fixtures import seed_raw_task_graph_for_test
from tests.contract.test_ne01_atomic_failure_bundle import (
    ATTEMPT_ID,
    GRAPH_ID,
    NODE_ID,
    NOW,
    MutableClock,
    _event,
    _fail,
    _graph,
    _running_store,
)


def _proof_authority(
    key_id: str = "terminal-reconciliation-v1",
) -> tuple[Ed25519PrivateKey, Ed25519ReconciliationProofVerifier]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, Ed25519ReconciliationProofVerifier({key_id: public_key})


def _reopen(
    store: DurableTaskStore,
    *,
    verifier: Ed25519ReconciliationProofVerifier | None = None,
) -> DurableTaskStore:
    return DurableTaskStore(
        store.db_path,
        clock=store._clock,  # noqa: SLF001 - preserve the trusted fixture clock
        reconciliation_proof_verifier=verifier,
    )


def _attach_budget(
    store: DurableTaskStore,
    *,
    budget_id: str,
    graph_id: str = GRAPH_ID,
    exceed: bool = False,
) -> dict[str, Any]:
    limit = BudgetAmount(token_count=1)
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=budget_id,
            scope=f"graph:{graph_id}@1",
            limits=limit,
            reserved=limit,
            status=BudgetReservationStatus.PENDING,
        ),
        graph_id=graph_id,
        graph_revision=1,
        node_id=None,
        attempt_id=None,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            0,
            "BUDGET_RESERVATION_CREATED",
            identity=f"{budget_id}:created",
        ),
    )
    record = store.transition_budget(
        budget_id,
        expected_version=0,
        target_status=BudgetReservationStatus.ACTIVE,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            1,
            "BUDGET_RESERVATION_ACTIVE",
            identity=f"{budget_id}:active",
        ),
    )
    if not exceed:
        return record
    usage_id = f"budget-usage://terminal-reconciliation/{budget_id.rsplit('/', 1)[-1]}"
    result = store.consume_budget_once(
        budget_id,
        usage_id=usage_id,
        charge_kind="WORKER_USAGE",
        graph_id=graph_id,
        graph_revision=1,
        node_id=NODE_ID,
        attempt_id=ATTEMPT_ID,
        delta=BudgetAmount(token_count=2),
        expected_version=1,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            2,
            "BUDGET_EXCEEDED",
            attempt_id=ATTEMPT_ID,
            evidence_refs=(usage_id,),
            identity=f"{budget_id}:exceeded",
        ),
    )
    assert result.reason_code == TASK_STORE_BUDGET_EXCEEDED
    assert result.usage is not None
    return result.reservation


def _prepare_atomic_case(
    tmp_path: Path,
    *,
    graph_status: TaskGraphStatus,
    budget_status: str | None,
) -> tuple[DurableTaskStore, dict[str, Any]]:
    store, context = _running_store(tmp_path)
    failure_budget: dict[str, object] = {}
    if budget_status is not None:
        budget_id = "budget-terminal-reconciliation"
        budget = store.reconcile_budget(budget_id)
        if budget_status == "EXCEEDED":
            usage_id = "budget-usage://terminal-reconciliation/exceeded"
            exceeded = store.consume_budget_once(
                budget_id,
                usage_id=usage_id,
                charge_kind="WORKER_USAGE",
                graph_id=GRAPH_ID,
                graph_revision=1,
                node_id=NODE_ID,
                attempt_id=ATTEMPT_ID,
                delta=BudgetAmount(token_count=101),
                expected_version=int(budget["status_version"]),
                outbox=_event(
                    "BUDGET_RESERVATION",
                    budget_id,
                    int(budget["status_version"]) + 1,
                    "BUDGET_EXCEEDED",
                    attempt_id=ATTEMPT_ID,
                    evidence_refs=(usage_id,),
                    identity=f"{budget_id}:exceeded",
                ),
            )
            assert exceeded.reason_code == TASK_STORE_BUDGET_EXCEEDED
        elif budget_status == "RELEASED":
            failure_budget = {
                "budget_id": budget_id,
                "expected_budget_version": int(budget["status_version"]),
                "budget_outbox": _event(
                    "BUDGET_RESERVATION",
                    budget_id,
                    int(budget["status_version"]) + 1,
                    "BUDGET_RESERVATION_RELEASED",
                    identity=f"{budget_id}:released",
                ),
            }
        else:
            assert budget_status == "ACTIVE"
    _fail(
        store,
        context["lease"],
        graph_target_status=graph_status,
        **failure_budget,
    )
    inventory = store.inventory_terminal_reconciliation_cases()
    assert inventory["inserted_count"] == 1
    case = inventory["cases"][0]
    return store, store.read_terminal_reconciliation_case(case["case_id"])


def _simple_cancelled_store(
    tmp_path: Path,
    *,
    name: str,
    with_0092_conflict: bool,
    verifier: Ed25519ReconciliationProofVerifier | None = None,
) -> tuple[DurableTaskStore, dict[str, Any]]:
    db_path = tmp_path / f"{name}.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"migration:{name}",
        ts_ms=NOW,
    )
    assert migration.success is True, migration.as_dict()
    store = DurableTaskStore(
        db_path,
        clock=MutableClock(),
        reconciliation_proof_verifier=verifier,
    )
    graph_id = f"graph-{name}"
    graph = replace(
        _graph(),
        graph_id=graph_id,
        root_work_order_id=f"work-order-{name}",
        planner_ref=f"planner://terminal-reconciliation/{name}",
    )
    seed_raw_task_graph_for_test(
        store,
        graph,
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            0,
            "TASK_GRAPH_CREATED",
            identity=f"{name}:created",
        ),
    )
    store.transition_graph(
        graph_id,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            1,
            "TASK_GRAPH_ACTIVE",
            identity=f"{name}:active",
        ),
    )
    budget_id = f"budget://terminal-reconciliation/{name}"
    _attach_budget(store, budget_id=budget_id, graph_id=graph_id)
    cancelled_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_CANCELLED",
        identity=f"{name}:cancelled",
    )
    store.transition_graph(
        graph_id,
        1,
        expected_version=1,
        target_status=TaskGraphStatus.CANCELLED,
        outbox=cancelled_outbox,
    )
    if with_0092_conflict:
        payload_json = "{}"
        payload_digest = "sha256:" + hashlib.sha256(
            payload_json.encode("utf-8")
        ).hexdigest()
        with store._transaction() as conn:  # noqa: SLF001 - legacy 0092 fixture
            conn.execute(
                """
                INSERT INTO task_runtime_graph_cancellation_intent(
                    intent_ref, schema_version, request_id, request_identity,
                    source, reason_code, graph_id, graph_revision,
                    graph_version_before, graph_version_after,
                    budget_id, budget_version_at_request,
                    payload_json, payload_sha256, graph_outbox_id,
                    status, status_version, requested_ts_ms, finalized_ts_ms
                ) VALUES (?, 'legacy-0092-test', ?, ?, 'LEGACY', ?, ?, 1,
                    1, 2, NULL, NULL, ?, ?, ?, 'REQUESTED', 0, ?, NULL)
                """,
                (
                    f"cancel-intent://{name}",
                    f"request-{name}",
                    f"request-identity-{name}",
                    "LEGACY_CANCEL_CONFLICT",
                    graph_id,
                    payload_json,
                    payload_digest,
                    cancelled_outbox.outbox_id,
                    NOW,
                ),
            )
    inventory = store.inventory_terminal_reconciliation_cases()
    assert inventory["inserted_count"] == 1
    case = inventory["cases"][0]
    return store, store.read_terminal_reconciliation_case(case["case_id"])


def _proof_id(
    *,
    case: dict[str, Any],
    subject_digest: str,
    action: str,
    authority_kind: str,
    reconciler_actor_id: str,
    attestation_ref: str | None,
    issued_ts_ms: int,
) -> str:
    identity = "\n".join(
        (
            str(case["case_id"]),
            subject_digest,
            action,
            authority_kind,
            attestation_ref or reconciler_actor_id,
            str(issued_ts_ms),
        )
    )
    return "terminal-reconciliation-proof://sha256/" + hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()


def _repair_outbox(
    *,
    kind: str,
    case: dict[str, Any],
    proof_id: str,
    attestation_ref: str | None,
) -> OutboxMessage:
    digest = proof_id.rsplit("/", 1)[-1]
    graph = kind == "graph"
    aggregate_type = "TASK_GRAPH" if graph else "BUDGET_RESERVATION"
    aggregate_id = str(case["graph_id"] if graph else case["budget_id"])
    aggregate_version = int(
        case["graph_status_version"] if graph else case["budget_status_version"]
    ) + 1
    graph_revision = int(case["graph_revision"]) if graph else None
    evidence = [str(case["case_id"]), proof_id]
    if attestation_ref is not None:
        evidence.append(attestation_ref)
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        graph_revision=graph_revision,
        event_type=("TASK_GRAPH_FAILED" if graph else "BUDGET_RESERVATION_RELEASED"),
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            graph_revision=graph_revision,
        ),
        evidence_refs=tuple(evidence),
        attempt_id=None,
        idempotency_key=f"terminal-reconciliation:{kind}:{digest}",
        outbox_id=f"outbox::terminal-reconciliation::{kind}::{digest}",
    )


def _request(
    aggregate: dict[str, Any],
    *,
    authority_kind: str,
    actor: str,
    issued_ts_ms: int,
    private_key: Ed25519PrivateKey | None = None,
    key_id: str | None = None,
    attestation_ref: str | None = None,
) -> dict[str, Any]:
    case = aggregate["case"]
    subject_digest = str(aggregate["subject"]["snapshot_sha256"])
    action = str(case["recommended_action"])
    targets = {
        "RELEASE_BUDGET": (str(case["graph_status"]), "RELEASED"),
        "FAIL_GRAPH": ("FAILED", None),
        "FAIL_GRAPH_AND_RELEASE_BUDGET": ("FAILED", "RELEASED"),
        "NONE": (None, None),
    }
    target_graph_status, target_budget_status = targets[action]
    proof_id = _proof_id(
        case=case,
        subject_digest=subject_digest,
        action=action,
        authority_kind=authority_kind,
        reconciler_actor_id=actor,
        attestation_ref=attestation_ref,
        issued_ts_ms=issued_ts_ms,
    )
    graph_outbox = (
        _repair_outbox(
            kind="graph",
            case=case,
            proof_id=proof_id,
            attestation_ref=attestation_ref,
        )
        if action in {"FAIL_GRAPH", "FAIL_GRAPH_AND_RELEASE_BUDGET"}
        else None
    )
    budget_outbox = (
        _repair_outbox(
            kind="budget",
            case=case,
            proof_id=proof_id,
            attestation_ref=attestation_ref,
        )
        if action in {"RELEASE_BUDGET", "FAIL_GRAPH_AND_RELEASE_BUDGET"}
        else None
    )
    request = {
        "expected_case_snapshot_sha256": str(case["snapshot_sha256"]),
        "expected_subject_snapshot_sha256": subject_digest,
        "action": action,
        "target_graph_status": target_graph_status,
        "target_budget_status": target_budget_status,
        "authority_kind": authority_kind,
        "reconciler_actor_id": actor,
        "issued_ts_ms": issued_ts_ms,
        "proof_id": proof_id,
        "graph_outbox": graph_outbox,
        "budget_outbox": budget_outbox,
        "attestation_ref": attestation_ref,
        "attestation_key_id": key_id,
        "attestation_signature": None,
    }
    if authority_kind == "SIGNED_OPERATOR":
        assert private_key is not None and key_id is not None and attestation_ref
        _, binding_bytes = build_terminal_reconciliation_attestation_binding(
            case=case,
            subject_snapshot_sha256=subject_digest,
            action=action,
            target_graph_status=target_graph_status,
            target_budget_status=target_budget_status,
            authority_kind=authority_kind,
            reconciler_actor_id=actor,
            attestation_ref=attestation_ref,
            attestation_key_id=key_id,
            issued_ts_ms=issued_ts_ms,
            graph_outbox=graph_outbox,
            budget_outbox=budget_outbox,
        )
        request["attestation_signature"] = "ed25519:" + base64.urlsafe_b64encode(
            private_key.sign(binding_bytes)
        ).decode("ascii")
    return request


def _record(
    store: DurableTaskStore,
    aggregate: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    return store.record_terminal_reconciliation_proof(
        aggregate["case"]["case_id"],
        **request,
    )


def _truth(store: DurableTaskStore) -> dict[str, Any]:
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return {
            "proofs": int(
                conn.execute(
                    "SELECT COUNT(*) FROM task_runtime_terminal_reconciliation_proof"
                ).fetchone()[0]
            ),
            "cases": int(
                conn.execute(
                    "SELECT COUNT(*) FROM task_runtime_terminal_reconciliation_case"
                ).fetchone()[0]
            ),
            "outboxes": int(
                conn.execute("SELECT COUNT(*) FROM task_runtime_outbox").fetchone()[0]
            ),
            "graph": tuple(
                conn.execute(
                    "SELECT status, status_version FROM task_runtime_graph "
                    "WHERE graph_id = ? AND revision = 1",
                    (GRAPH_ID,),
                ).fetchone()
            ),
            "budget": (
                tuple(
                    conn.execute(
                        "SELECT status, status_version "
                        "FROM task_runtime_budget_reservation ORDER BY budget_id LIMIT 1"
                    ).fetchone()
                )
                if conn.execute(
                    "SELECT 1 FROM task_runtime_budget_reservation LIMIT 1"
                ).fetchone()
                else None
            ),
        }


def test_active_and_created_graphs_are_not_inventoried_and_write_nothing(
    tmp_path: Path,
) -> None:
    store, _ = _running_store(tmp_path)
    created_graph = replace(
        _graph(),
        graph_id="graph-terminal-created",
        root_work_order_id="work-order-terminal-created",
        planner_ref="planner://terminal-reconciliation/created",
    )
    seed_raw_task_graph_for_test(
        store,
        created_graph,
        outbox=_event(
            "TASK_GRAPH",
            created_graph.graph_id,
            0,
            "TASK_GRAPH_CREATED",
            identity="terminal-created",
        ),
    )
    before = _truth(store)

    assert store.list_terminal_reconciliation_inventory_candidates() == ()
    inventory = store.inventory_terminal_reconciliation_cases()

    assert inventory["discovered_count"] == 0
    assert inventory["inserted_count"] == 0
    assert _truth(store) == before


@pytest.mark.parametrize("budget_status", ("ACTIVE", "EXCEEDED"))
def test_failed_exact_system_repair_commits_budget_outbox_and_proof_atomically(
    tmp_path: Path,
    budget_status: str,
) -> None:
    store, aggregate = _prepare_atomic_case(
        tmp_path,
        graph_status=TaskGraphStatus.FAILED,
        budget_status=budget_status,
    )
    case = aggregate["case"]
    assert case["classification"] == "EXACT_REPAIRABLE"
    assert case["budget_status"] == budget_status
    request = _request(
        aggregate,
        authority_kind="SYSTEM_DURABLE_EVIDENCE",
        actor="runtime://terminal-reconciliation/system",
        issued_ts_ms=NOW,
    )

    settled = _record(store, aggregate, request)

    assert settled["replayed"] is False
    assert settled["graph"]["status"] == "FAILED"
    assert settled["graph"]["status_version"] == case["graph_status_version"]
    assert settled["budget"]["status"] == "RELEASED"
    assert settled["budget"]["status_version"] == case["budget_status_version"] + 1
    assert settled["proof"]["authority_kind"] == "SYSTEM_DURABLE_EVIDENCE"
    assert settled["proof"]["graph_outbox_id"] is None
    assert settled["proof"]["budget_outbox_id"] == request["budget_outbox"].outbox_id
    with sqlite3.connect(store.db_path) as conn:
        proof = conn.execute(
            "SELECT COUNT(*), MIN(action) "
            "FROM task_runtime_terminal_reconciliation_proof"
        ).fetchone()
        repair_outbox = conn.execute(
            "SELECT event_type, evidence_refs_json FROM task_runtime_outbox "
            "WHERE outbox_id = ?",
            (request["budget_outbox"].outbox_id,),
        ).fetchone()
        winners = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_budget_usage "
            "WHERE reason_code = ?",
            (TASK_STORE_BUDGET_EXCEEDED,),
        ).fetchone()[0]
    assert proof == (1, "RELEASE_BUDGET")
    assert repair_outbox is not None
    assert repair_outbox[0] == "BUDGET_RESERVATION_RELEASED"
    assert json.loads(repair_outbox[1]) == [case["case_id"], request["proof_id"]]
    assert winners == int(budget_status == "EXCEEDED")


def test_blocked_failure_accepts_real_signed_ed25519_proof(tmp_path: Path) -> None:
    base, aggregate = _prepare_atomic_case(
        tmp_path,
        graph_status=TaskGraphStatus.BLOCKED,
        budget_status="RELEASED",
    )
    private_key, verifier = _proof_authority()
    store = _reopen(base, verifier=verifier)
    aggregate = store.read_terminal_reconciliation_case(
        aggregate["case"]["case_id"]
    )
    request = _request(
        aggregate,
        authority_kind="SIGNED_OPERATOR",
        actor="operator://terminal-reconciliation/alice",
        issued_ts_ms=NOW + 1,
        private_key=private_key,
        key_id="terminal-reconciliation-v1",
        attestation_ref="attestation://terminal-reconciliation/blocked",
    )

    settled = _record(store, aggregate, request)

    assert settled["graph"]["status"] == "FAILED"
    assert settled["graph"]["status_version"] == 3
    assert settled["budget"]["status"] == "RELEASED"
    assert settled["proof"]["authority_kind"] == "SIGNED_OPERATOR"
    assert settled["proof"]["attestation_signature"] == request[
        "attestation_signature"
    ]
    assert settled["proof"]["graph_outbox_id"] == request["graph_outbox"].outbox_id


def test_anomaly_to_healthy_requires_signed_noop_closure_and_changes_no_authority(
    tmp_path: Path,
) -> None:
    base, anomaly = _prepare_atomic_case(
        tmp_path,
        graph_status=TaskGraphStatus.FAILED,
        budget_status="ACTIVE",
    )
    budget_id = str(anomaly["case"]["budget_id"])
    with sqlite3.connect(base.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_budget_reservation
            SET status = 'RELEASED', status_version = status_version + 1,
                updated_ts_ms = updated_ts_ms + 1
            WHERE budget_id = ?
            """,
            (budget_id,),
        )
        conn.commit()

    inventory = base.inventory_terminal_reconciliation_cases(limit=10_000)
    assert inventory["inserted_count"] == 1
    closure = inventory["cases"][0]
    assert closure["case_kind"] == "TERMINAL_HEALTHY_CLOSURE"
    assert closure["classification"] == "SIGNED_REPAIR_REQUIRED"
    assert closure["recommended_action"] == "NONE"
    with sqlite3.connect(base.db_path) as conn:
        assert conn.execute(
            """
            SELECT replacement_case_id
            FROM task_runtime_terminal_reconciliation_supersession
            WHERE prior_case_id = ?
            """,
            (anomaly["case"]["case_id"],),
        ).fetchone() == (closure["case_id"],)

    private_key, verifier = _proof_authority()
    store = _reopen(base, verifier=verifier)
    aggregate = store.read_terminal_reconciliation_case(closure["case_id"])
    request = _request(
        aggregate,
        authority_kind="SIGNED_OPERATOR",
        actor="operator://terminal-reconciliation/healthy-closure",
        issued_ts_ms=NOW + 3,
        private_key=private_key,
        key_id="terminal-reconciliation-v1",
        attestation_ref="attestation://terminal-reconciliation/healthy-closure",
    )
    assert request["target_graph_status"] is None
    assert request["target_budget_status"] is None
    assert request["graph_outbox"] is None
    assert request["budget_outbox"] is None
    before = _truth(store)

    settled = _record(store, aggregate, request)

    after = _truth(store)
    assert settled["proof"]["action"] == "NONE"
    assert settled["graph"]["status"] == "FAILED"
    assert settled["budget"]["status"] == "RELEASED"
    assert after["proofs"] == before["proofs"] + 1
    assert after["cases"] == before["cases"]
    assert after["outboxes"] == before["outboxes"]
    assert after["graph"] == before["graph"]
    assert after["budget"] == before["budget"]
    store.assert_terminal_reconciliation_production_ready()


def test_healthy_closure_snapshot_drift_creates_signed_replacement_chain(
    tmp_path: Path,
) -> None:
    base, anomaly = _prepare_atomic_case(
        tmp_path,
        graph_status=TaskGraphStatus.FAILED,
        budget_status="ACTIVE",
    )
    budget_id = str(anomaly["case"]["budget_id"])
    with sqlite3.connect(base.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_budget_reservation
            SET status = 'RELEASED', status_version = status_version + 1,
                updated_ts_ms = updated_ts_ms + 1
            WHERE budget_id = ?
            """,
            (budget_id,),
        )
        conn.commit()
    first = base.inventory_terminal_reconciliation_cases(limit=10_000)["cases"][0]
    with sqlite3.connect(base.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_budget_reservation
            SET updated_ts_ms = updated_ts_ms + 1 WHERE budget_id = ?
            """,
            (budget_id,),
        )
        conn.commit()

    second_inventory = base.inventory_terminal_reconciliation_cases(limit=10_000)
    assert second_inventory["inserted_count"] == 1
    second = second_inventory["cases"][0]
    assert second["case_kind"] == "TERMINAL_HEALTHY_CLOSURE"
    assert second["case_id"] != first["case_id"]
    with sqlite3.connect(base.db_path) as conn:
        links = conn.execute(
            """
            SELECT prior_case_id, replacement_case_id
            FROM task_runtime_terminal_reconciliation_supersession
            ORDER BY created_ts_ms, supersession_id
            """
        ).fetchall()
    assert (anomaly["case"]["case_id"], first["case_id"]) in links
    assert (first["case_id"], second["case_id"]) in links

    private_key, verifier = _proof_authority()
    store = _reopen(base, verifier=verifier)
    aggregate = store.read_terminal_reconciliation_case(second["case_id"])
    request = _request(
        aggregate,
        authority_kind="SIGNED_OPERATOR",
        actor="operator://terminal-reconciliation/healthy-replacement",
        issued_ts_ms=NOW + 4,
        private_key=private_key,
        key_id="terminal-reconciliation-v1",
        attestation_ref="attestation://terminal-reconciliation/healthy-replacement",
    )
    _record(store, aggregate, request)
    store.assert_terminal_reconciliation_production_ready()


def test_legacy_cancelled_signed_release_and_0092_conflict_quarantine(
    tmp_path: Path,
) -> None:
    private_key, verifier = _proof_authority()
    store, aggregate = _simple_cancelled_store(
        tmp_path,
        name="legacy-cancelled",
        with_0092_conflict=False,
        verifier=verifier,
    )
    case = aggregate["case"]
    assert case["case_kind"] == "CANCELLED_UNSETTLED_BUDGET"
    assert case["classification"] == "SIGNED_REPAIR_REQUIRED"
    request = _request(
        aggregate,
        authority_kind="SIGNED_OPERATOR",
        actor="operator://terminal-reconciliation/cancel",
        issued_ts_ms=NOW + 2,
        private_key=private_key,
        key_id="terminal-reconciliation-v1",
        attestation_ref="attestation://terminal-reconciliation/cancelled",
    )
    settled = _record(store, aggregate, request)
    assert settled["graph"]["status"] == "CANCELLED"
    assert settled["budget"]["status"] == "RELEASED"

    conflicted, quarantined = _simple_cancelled_store(
        tmp_path,
        name="cancelled-0092-conflict",
        with_0092_conflict=True,
    )
    assert quarantined["case"]["case_kind"] == "TERMINAL_STRUCTURAL_ANOMALY"
    assert quarantined["case"]["classification"] == "QUARANTINED_STRUCTURAL"
    assert conflicted.list_terminal_reconciliation_candidates() == ()
    truth = _truth_for_graph(
        conflicted,
        graph_id="graph-cancelled-0092-conflict",
        budget_id="budget://terminal-reconciliation/cancelled-0092-conflict",
    )
    assert truth["graph"] == ("CANCELLED", 2)
    assert truth["budget"] == ("ACTIVE", 1)
    assert truth["proofs"] == 0


def _truth_for_graph(
    store: DurableTaskStore,
    *,
    graph_id: str,
    budget_id: str | None,
) -> dict[str, Any]:
    with sqlite3.connect(store.db_path) as conn:
        return {
            "graph": conn.execute(
                "SELECT status, status_version FROM task_runtime_graph "
                "WHERE graph_id = ? AND revision = 1",
                (graph_id,),
            ).fetchone(),
            "budget": (
                conn.execute(
                    "SELECT status, status_version "
                    "FROM task_runtime_budget_reservation WHERE budget_id = ?",
                    (budget_id,),
                ).fetchone()
                if budget_id is not None
                else None
            ),
            "proofs": conn.execute(
                "SELECT COUNT(*) FROM task_runtime_terminal_reconciliation_proof"
            ).fetchone()[0],
            "outboxes": conn.execute(
                "SELECT COUNT(*) FROM task_runtime_outbox"
            ).fetchone()[0],
        }


def test_invalid_unknown_and_stale_signed_requests_leave_zero_side_effects(
    tmp_path: Path,
) -> None:
    base, aggregate = _prepare_atomic_case(
        tmp_path,
        graph_status=TaskGraphStatus.BLOCKED,
        budget_status="RELEASED",
    )
    private_key, verifier = _proof_authority()
    wrong_key = Ed25519PrivateKey.generate()
    store = _reopen(base, verifier=verifier)
    aggregate = store.read_terminal_reconciliation_case(
        aggregate["case"]["case_id"]
    )
    before = _truth(store)

    false_signature = _request(
        aggregate,
        authority_kind="SIGNED_OPERATOR",
        actor="operator://terminal-reconciliation/false",
        issued_ts_ms=NOW + 10,
        private_key=wrong_key,
        key_id="terminal-reconciliation-v1",
        attestation_ref="attestation://terminal-reconciliation/false",
    )
    unknown_key = _request(
        aggregate,
        authority_kind="SIGNED_OPERATOR",
        actor="operator://terminal-reconciliation/unknown",
        issued_ts_ms=NOW + 11,
        private_key=private_key,
        key_id="unknown-key",
        attestation_ref="attestation://terminal-reconciliation/unknown",
    )
    stale_case = _request(
        aggregate,
        authority_kind="SIGNED_OPERATOR",
        actor="operator://terminal-reconciliation/stale-case",
        issued_ts_ms=NOW + 12,
        private_key=private_key,
        key_id="terminal-reconciliation-v1",
        attestation_ref="attestation://terminal-reconciliation/stale-case",
    )
    stale_case["expected_case_snapshot_sha256"] = "sha256:" + ("0" * 64)
    stale_subject = _request(
        aggregate,
        authority_kind="SIGNED_OPERATOR",
        actor="operator://terminal-reconciliation/stale-subject",
        issued_ts_ms=NOW + 13,
        private_key=private_key,
        key_id="terminal-reconciliation-v1",
        attestation_ref="attestation://terminal-reconciliation/stale-subject",
    )
    stale_subject["expected_subject_snapshot_sha256"] = "sha256:" + ("1" * 64)

    for request in (false_signature, unknown_key, stale_case, stale_subject):
        with pytest.raises(TerminalReconciliationStoreRejected):
            _record(store, aggregate, request)
        assert _truth(store) == before


def test_exact_request_replay_is_idempotent(tmp_path: Path) -> None:
    store, aggregate = _prepare_atomic_case(
        tmp_path,
        graph_status=TaskGraphStatus.FAILED,
        budget_status="ACTIVE",
    )
    request = _request(
        aggregate,
        authority_kind="SYSTEM_DURABLE_EVIDENCE",
        actor="runtime://terminal-reconciliation/replay",
        issued_ts_ms=NOW + 20,
    )

    first = _record(store, aggregate, request)
    second = _record(store, aggregate, request)

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["proof"]["proof_id"] == second["proof"]["proof_id"]
    truth = _truth(store)
    assert truth["proofs"] == 1
    assert truth["budget"] == ("RELEASED", 2)


def test_concurrent_different_requests_commit_one_unique_proof(tmp_path: Path) -> None:
    seed, aggregate = _prepare_atomic_case(
        tmp_path,
        graph_status=TaskGraphStatus.FAILED,
        budget_status="ACTIVE",
    )
    workers = 8
    stores = [_reopen(seed) for _ in range(workers)]
    requests = [
        _request(
            aggregate,
            authority_kind="SYSTEM_DURABLE_EVIDENCE",
            actor=f"runtime://terminal-reconciliation/contender-{index}",
            issued_ts_ms=NOW + 100 + index,
        )
        for index in range(workers)
    ]
    barrier = Barrier(workers)

    def contend(index: int) -> tuple[str, str]:
        barrier.wait(timeout=10)
        try:
            result = _record(stores[index], aggregate, requests[index])
        except TerminalReconciliationStoreRejected as exc:
            return "REJECTED", exc.reason_code
        return "COMMITTED", str(result["proof"]["proof_id"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(contend, range(workers)))

    assert [status for status, _ in outcomes].count("COMMITTED") == 1
    assert [status for status, _ in outcomes].count("REJECTED") == workers - 1
    truth = _truth(seed)
    assert truth["proofs"] == 1
    assert truth["budget"] == ("RELEASED", 2)
    with sqlite3.connect(seed.db_path) as conn:
        terminal_outboxes = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox "
            "WHERE event_type = 'BUDGET_RESERVATION_RELEASED'"
        ).fetchone()[0]
    assert terminal_outboxes == 1


def test_concurrent_system_auto_reconciliation_converges_without_startup_loser(
    tmp_path: Path,
) -> None:
    seed, _aggregate = _prepare_atomic_case(
        tmp_path,
        graph_status=TaskGraphStatus.FAILED,
        budget_status="ACTIVE",
    )
    workers = 8
    stores = [_reopen(seed) for _ in range(workers)]
    services = [
        TerminalStateReconciliationService(
            task_store=store,
            outbox_factory=OutboxMessage,
            clock_ms=lambda index=index: NOW + 10_000 + index,
        )
        for index, store in enumerate(stores)
    ]
    barrier = Barrier(workers)

    def reconcile(index: int) -> dict[str, Any]:
        barrier.wait(timeout=10)
        return dict(services[index].reconcile_exact_cases(limit=1))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(reconcile, range(workers)))

    assert all(result["status"] == "COMPLETED" for result in outcomes)
    assert sum(int(result["reconciled_count"]) for result in outcomes) >= 1
    truth = _truth(seed)
    assert truth["proofs"] == 1
    assert truth["budget"] == ("RELEASED", 2)
    with sqlite3.connect(seed.db_path) as conn:
        proof_times = conn.execute(
            "SELECT DISTINCT issued_ts_ms "
            "FROM task_runtime_terminal_reconciliation_proof"
        ).fetchall()
        terminal_outboxes = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox "
            "WHERE event_type = 'BUDGET_RESERVATION_RELEASED'"
        ).fetchone()[0]
    assert proof_times == [(NOW,)]
    assert terminal_outboxes == 1


def test_late_injected_exception_rolls_back_graph_budget_outboxes_and_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, aggregate = _prepare_atomic_case(
        tmp_path,
        graph_status=TaskGraphStatus.BLOCKED,
        budget_status="ACTIVE",
    )
    private_key, verifier = _proof_authority()
    store = _reopen(base, verifier=verifier)
    aggregate = store.read_terminal_reconciliation_case(
        aggregate["case"]["case_id"]
    )
    request = _request(
        aggregate,
        authority_kind="SIGNED_OPERATOR",
        actor="operator://terminal-reconciliation/injected-failure",
        issued_ts_ms=NOW + 200,
        private_key=private_key,
        key_id="terminal-reconciliation-v1",
        attestation_ref="attestation://terminal-reconciliation/injected-failure",
    )
    before = _truth(store)
    original = store._apply_terminal_reconciliation_budget  # noqa: SLF001

    def apply_then_fail(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        original(*args, **kwargs)
        raise RuntimeError("injected after Graph and Budget mutation")

    monkeypatch.setattr(store, "_apply_terminal_reconciliation_budget", apply_then_fail)
    with pytest.raises(RuntimeError, match="injected after Graph and Budget mutation"):
        _record(store, aggregate, request)

    assert _truth(store) == before
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox WHERE outbox_id IN (?, ?)",
            (
                request["graph_outbox"].outbox_id,
                request["budget_outbox"].outbox_id,
            ),
        ).fetchone() == (0,)


def test_query_repository_is_sqlite_read_only_and_never_inventories(
    tmp_path: Path,
) -> None:
    store, aggregate = _prepare_atomic_case(
        tmp_path,
        graph_status=TaskGraphStatus.FAILED,
        budget_status="ACTIVE",
    )
    before = _truth(store)
    repository = TerminalReconciliationQueryRepository(store.db_path)

    cases = repository.list_terminal_reconciliation_cases()
    discovered = repository.list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["case_id"] == aggregate["case"]["case_id"]
    assert len(discovered) == 1
    with repository._connect() as conn:  # noqa: SLF001 - assert physical ro mode
        assert tuple(conn.execute("PRAGMA query_only").fetchone()) == (1,)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE terminal_reconciliation_write_forbidden(id INTEGER)")
    assert _truth(store) == before
