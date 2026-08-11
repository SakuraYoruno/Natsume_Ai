from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)
from tests.contract.test_ne01_atomic_failure_bundle import GRAPH_ID, NOW, _event
from tests.integration.test_ne01_b5_parallel_success import _b5_fixture, _execute
from tests.contract.test_ne01_parallel_store_settlement import (
    BUDGET_ID,
    _budget_settlement_fixture,
)
from tests.unit.test_ne01_terminal_reconciliation_classification import (
    _active_budget,
    _fresh_graph_store,
    _reader,
)
from tests.integration.test_ne01_terminal_reconciliation_store import (
    _proof_authority,
    _record,
    _reopen,
    _request,
)


def _sha256_empty_object() -> str:
    return "sha256:" + hashlib.sha256(b"{}").hexdigest()


def _canonical_cancelled_store(tmp_path: Path, *, with_budget: bool = True):
    store = _fresh_graph_store(tmp_path)
    budget_id = _active_budget(store) if with_budget else None
    node_id = "node-resolver"
    request_identity = "principal://operator/terminal-authority"
    request_seed = _event(
        "TASK_GRAPH",
        GRAPH_ID,
        2,
        "TASK_GRAPH_CANCELLATION_REQUESTED",
        identity="terminal-authority-request",
    )
    intent = normalize_graph_cancellation_intent(
        {
            "schema_version": GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
            "request_id": "request-terminal-authority",
            "request_identity": request_identity,
            "source": "OPERATOR",
            "reason_code": "OPERATOR_CANCELLED_TASK",
            "graph_id": GRAPH_ID,
            "graph_revision": 1,
            "graph_status_before": "ACTIVE",
            "graph_status_version_before": 1,
            "graph_target_status": "BLOCKED",
            "budget_id": budget_id,
            "budget_status_before": "ACTIVE" if with_budget else None,
            "budget_status_version_before": 1 if with_budget else None,
            "node_states_before": {
                node_id: {"status": "READY", "status_version": 0},
            },
            "node_states_after": {
                node_id: {"status": "CANCEL_REQUESTED", "status_version": 1},
            },
            "graph_outbox_id": request_seed.outbox_id,
        }
    )
    store.request_graph_cancellation(
        GRAPH_ID,
        1,
        budget_id=budget_id,
        expected_graph_version=1,
        expected_budget_version=1 if with_budget else None,
        expected_node_states=intent.payload["node_states_before"],
        intent_ref=intent.intent_ref,
        intent_payload=intent.payload,
        graph_outbox=_event(
            "TASK_GRAPH",
            GRAPH_ID,
            2,
            "TASK_GRAPH_CANCELLATION_REQUESTED",
            evidence_refs=(intent.intent_ref, request_identity),
            identity="terminal-authority-request",
        ),
    )
    advanced = store.advance_graph_cancellation(intent.intent_ref)
    assert advanced["ready_to_finalize"] is True
    graph_seed = _event(
        "TASK_GRAPH",
        GRAPH_ID,
        3,
        "TASK_GRAPH_CANCELLED",
        identity="terminal-authority-final-graph",
    )
    budget_seed = (
        _event(
            "BUDGET_RESERVATION",
            budget_id,
            2,
            "BUDGET_RESERVATION_RELEASED",
            identity="terminal-authority-final-budget",
        )
        if with_budget
        else None
    )
    node_states_before = {
        node_id: {"status": "CANCEL_REQUESTED", "status_version": 1},
    }
    decision = normalize_graph_cancellation_decision(
        {
            "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
            "intent_ref": intent.intent_ref,
            "request_id": intent.payload["request_id"],
            "request_identity": request_identity,
            "graph_id": GRAPH_ID,
            "graph_revision": 1,
            "graph_status_before": "BLOCKED",
            "graph_status_version_before": 2,
            "graph_target_status": "CANCELLED",
            "budget_id": budget_id,
            "budget_status_before": "ACTIVE" if with_budget else None,
            "budget_status_version_before": 1 if with_budget else None,
            "budget_target_status": "RELEASED" if with_budget else None,
            "node_states_before": node_states_before,
            "node_states_after": {
                node_id: {"status": "CANCELLED", "status_version": 2},
            },
            "reconciliation_evidence_refs": list(
                advanced["reconciliation_evidence_refs"]
            ),
            "graph_outbox_id": graph_seed.outbox_id,
            "budget_outbox_id": budget_seed.outbox_id if budget_seed else None,
        }
    )
    evidence_refs = (decision.decision_ref, intent.intent_ref, request_identity)
    store.finalize_graph_cancellation(
        GRAPH_ID,
        1,
        budget_id=budget_id,
        expected_graph_version=2,
        expected_budget_version=1 if with_budget else None,
        expected_node_states=node_states_before,
        intent_ref=intent.intent_ref,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=_event(
            "TASK_GRAPH",
            GRAPH_ID,
            3,
            "TASK_GRAPH_CANCELLED",
            evidence_refs=evidence_refs,
            identity="terminal-authority-final-graph",
        ),
        budget_outbox=(
            _event(
                "BUDGET_RESERVATION",
                budget_id,
                2,
                "BUDGET_RESERVATION_RELEASED",
                evidence_refs=evidence_refs,
                identity="terminal-authority-final-budget",
            )
            if with_budget
            else None
        ),
    )
    return store, decision.decision_ref


def test_prebudget_cancelled_graph_is_healthy_and_old_false_positive_closes_signed(
    tmp_path: Path,
) -> None:
    base, _ = _canonical_cancelled_store(tmp_path, with_budget=False)
    assert base.list_terminal_reconciliation_inventory_candidates() == ()
    base.assert_terminal_reconciliation_production_ready()

    with base._transaction() as conn:  # noqa: SLF001 - freeze the old classifier case
        graph = conn.execute(
            "SELECT * FROM task_runtime_graph WHERE graph_id = ? AND revision = 1",
            (GRAPH_ID,),
        ).fetchone()
        assert graph is not None
        observation = base._terminal_observation(conn, graph)  # noqa: SLF001
        legacy_case = base._build_terminal_case(  # noqa: SLF001
            observation,
            case_kind="TERMINAL_STRUCTURAL_ANOMALY",
            classification="QUARANTINED_STRUCTURAL",
            reason_code="TERMINAL_GRAPH_BUDGET_BINDING_AMBIGUOUS",
            recommended_action="NONE",
            selected_budget=None,
            selected_node=None,
            selected_attempt=None,
            failure_bundle=None,
        )
        base._insert_terminal_reconciliation_case(  # noqa: SLF001
            conn,
            legacy_case,
            created_ts_ms=NOW + 1,
        )

    private_key, verifier = _proof_authority()
    store = _reopen(base, verifier=verifier)
    candidates = store.list_terminal_reconciliation_candidates()
    assert len(candidates) == 1
    aggregate = candidates[0]
    before = store.get_graph(GRAPH_ID, 1)
    request = _request(
        aggregate,
        authority_kind="SIGNED_OPERATOR",
        actor="operator://terminal-reconciliation/classifier-correction",
        issued_ts_ms=NOW + 2,
        private_key=private_key,
        key_id="terminal-reconciliation-v1",
        attestation_ref="attestation://terminal-reconciliation/classifier-correction",
    )

    settled = _record(store, aggregate, request)

    assert settled["proof"]["action"] == "NONE"
    assert settled["budget"] is None
    assert store.get_graph(GRAPH_ID, 1) == before
    store.assert_terminal_reconciliation_production_ready()


@pytest.mark.asyncio
async def test_completed_graph_requires_complete_0091_authority(tmp_path: Path) -> None:
    fixture = _b5_fixture(tmp_path, delays={})
    db_path, contracts, _, store, runtime, _, _, _ = fixture

    await _execute(db_path, contracts, runtime)

    assert _reader(store).list_terminal_reconciliation_inventory_candidates() == ()
    decision = store.get_latest_parallel_success_decision("graph-gr03a", 1)
    assert decision is not None
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE task_runtime_outbox SET evidence_refs_json = '[]' WHERE outbox_id = ?",
            (decision["graph_outbox_id"],),
        )
        conn.commit()

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()
    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert cases[0]["case_kind"] == "COMPLETED_INCONSISTENT"


@pytest.mark.parametrize("conflict_kind", ("PARALLEL", "CANCELLATION"))
@pytest.mark.asyncio
async def test_completed_graph_rejects_cross_authority_decisions(
    tmp_path: Path,
    conflict_kind: str,
) -> None:
    fixture = _b5_fixture(tmp_path, delays={})
    db_path, contracts, _, store, runtime, _, _, _ = fixture
    await _execute(db_path, contracts, runtime)
    decision = store.get_latest_parallel_success_decision("graph-gr03a", 1)
    assert decision is not None
    digest = _sha256_empty_object()
    with sqlite3.connect(store.db_path) as conn:
        if conflict_kind == "PARALLEL":
            conn.execute(
                """
                INSERT INTO task_runtime_parallel_settlement_decision(
                    decision_ref, schema_version, graph_id, graph_revision,
                    graph_version_before, graph_version_after, budget_id,
                    budget_version_before, budget_version_after, decision_kind,
                    selected_node_id, graph_target_status, budget_target_status,
                    payload_json, payload_sha256, graph_outbox_id,
                    budget_outbox_id, created_ts_ms
                ) VALUES (
                    'parallel-settlement://terminal/conflict',
                    'Natsume.task_runtime.parallel_settlement.v1',
                    'graph-gr03a', 1, 1, 2, ?, 2, 2, 'FAILURE',
                    'right', 'FAILED', NULL, '{}', ?, ?, NULL, 1910000000500
                )
                """,
                (
                    decision["budget_id"],
                    digest,
                    decision["graph_outbox_id"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO task_runtime_graph_cancellation_intent(
                    intent_ref, schema_version, request_id, request_identity,
                    source, reason_code, graph_id, graph_revision,
                    graph_version_before, graph_version_after, budget_id,
                    budget_version_at_request, payload_json, payload_sha256,
                    graph_outbox_id, status, status_version, requested_ts_ms,
                    finalized_ts_ms
                ) VALUES (
                    'cancellation://terminal/completed-conflict',
                    'Natsume.task_runtime.graph_cancellation_intent.v1',
                    'request-terminal-completed-conflict',
                    'principal://operator/completed-conflict', 'OPERATOR',
                    'CONFLICTING_TERMINAL_AUTHORITY', 'graph-gr03a', 1,
                    1, 2, ?, 2, '{}', ?, ?, 'FINALIZED', 1,
                    1910000000400, 1910000000500
                )
                """,
                (
                    decision["budget_id"],
                    digest,
                    decision["graph_outbox_id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO task_runtime_graph_cancellation_decision(
                    decision_ref, schema_version, intent_ref, graph_id,
                    graph_revision, graph_version_before, graph_version_after,
                    budget_id, budget_version_before, budget_version_after,
                    payload_json, payload_sha256, graph_outbox_id,
                    budget_outbox_id, created_ts_ms
                ) VALUES (
                    'cancellation-decision://terminal/completed-conflict',
                    'Natsume.task_runtime.graph_cancellation_decision.v1',
                    'cancellation://terminal/completed-conflict',
                    'graph-gr03a', 1, 1, 2, ?, 2, 2, '{}', ?, ?, NULL,
                    1910000000500
                )
                """,
                (
                    decision["budget_id"],
                    digest,
                    decision["graph_outbox_id"],
                ),
            )
        conn.commit()

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()
    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"


def test_parallel_terminal_authority_rejects_extra_older_or_newer_decision(
    tmp_path: Path,
) -> None:
    (
        store,
        _attempt_id,
        _usage_id,
        states,
        decision,
        graph_outbox,
        budget_outbox,
    ) = _budget_settlement_fixture(tmp_path, graph_id="graph-terminal-duplicate-0090")
    store.settle_parallel_graph_budget(
        "graph-terminal-duplicate-0090",
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=2,
        expected_node_states=states,
        graph_target_status="BLOCKED",
        budget_target_status="RELEASED",
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_parallel_settlement_decision(
                decision_ref, schema_version, graph_id, graph_revision,
                graph_version_before, graph_version_after, budget_id,
                budget_version_before, budget_version_after, decision_kind,
                selected_node_id, graph_target_status, budget_target_status,
                payload_json, payload_sha256, graph_outbox_id,
                budget_outbox_id, created_ts_ms
            ) VALUES (
                'parallel-settlement://terminal/duplicate',
                'Natsume.task_runtime.parallel_settlement.v1',
                'graph-terminal-duplicate-0090', 1, 2, 3, ?, 3, 3,
                'FAILURE', ?, 'FAILED', NULL, '{}', ?, ?, NULL,
                1910000000700
            )
            """,
            (
                BUDGET_ID,
                next(iter(states)),
                _sha256_empty_object(),
                graph_outbox.outbox_id,
            ),
        )
        conn.commit()

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()
    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"


def test_cancelled_graph_requires_trusted_0092_decision(tmp_path: Path) -> None:
    store, decision_ref = _canonical_cancelled_store(tmp_path)

    assert _reader(store).list_terminal_reconciliation_inventory_candidates() == ()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_graph_cancellation_decision
            SET payload_sha256 = ? WHERE decision_ref = ?
            """,
            (_sha256_empty_object(), decision_ref),
        )
        conn.commit()

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()
    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert cases[0]["reason_code"] == "CANCELLED_TERMINAL_FACTS_AMBIGUOUS"


def test_cancelled_graph_rejects_conflicting_success_decision(tmp_path: Path) -> None:
    store, _ = _canonical_cancelled_store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        budget = conn.execute(
            """
            SELECT budget_id, status_version FROM task_runtime_budget_reservation
            WHERE graph_id = ? AND graph_revision = 1
            """,
            (GRAPH_ID,),
        ).fetchone()
        graph_outbox = conn.execute(
            """
            SELECT outbox_id FROM task_runtime_outbox
            WHERE aggregate_type = 'TASK_GRAPH' AND aggregate_id = ?
              AND event_type = 'TASK_GRAPH_CANCELLED'
            """,
            (GRAPH_ID,),
        ).fetchone()[0]
        budget_outbox = conn.execute(
            """
            SELECT outbox_id FROM task_runtime_outbox
            WHERE aggregate_type = 'BUDGET_RESERVATION' AND aggregate_id = ?
              AND event_type = 'BUDGET_RESERVATION_RELEASED'
            """,
            (budget[0],),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, created_ts_ms, completed_ts_ms,
                updated_ts_ms
            ) VALUES (
                'attempt-conflict', ?, 1, 'node-resolver', 1,
                'SUCCEEDED', 1, 1910000000500, 1910000000600,
                1910000000600
            )
            """,
            (GRAPH_ID,),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_parallel_success_decision(
                decision_ref, schema_version, graph_id, graph_revision,
                graph_version_before, graph_version_after, budget_id,
                budget_version_before, budget_version_after, result_node_id,
                result_attempt_id, result_receipt_ref, payload_json,
                payload_sha256, graph_outbox_id, budget_outbox_id, created_ts_ms
            ) VALUES (
                'parallel-success://terminal/cancelled-conflict',
                'Natsume.task_runtime.parallel_success.v1', ?, 1, 2, 3, ?,
                1, 2, 'node-resolver', 'attempt-conflict',
                'receipt://terminal/cancelled-conflict', '{}', ?, ?, ?,
                1910000000600
            )
            """,
            (
                GRAPH_ID,
                budget[0],
                _sha256_empty_object(),
                graph_outbox,
                budget_outbox,
            ),
        )
        conn.commit()

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()
    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
