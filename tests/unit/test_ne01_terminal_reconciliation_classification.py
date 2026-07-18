from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.persistence.task_record_codec import _dumps
from host_brain.persistence.reconciliation_proof_verifier import (
    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
)
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)
from host_brain.persistence.terminal_reconciliation_query_repository import (
    TerminalReconciliationQueryRepository,
)
from host_brain.task_runtime.terminal_state_reconciliation import (
    TerminalStateReconciliationService,
)
from tests.contract.test_ne01_atomic_failure_bundle import (
    GRAPH_ID,
    NOW,
    MutableClock,
    _event,
    _fail,
    _fail_node,
    _graph,
    _running_store,
)
from tests.contract.test_ne01_parallel_store_settlement import (
    BUDGET_ID,
    _budget_settlement_fixture,
)


def _active_budget(store, *, budget_id: str = "budget-terminal-reconciliation") -> str:
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=budget_id,
            scope=f"graph:{GRAPH_ID}@1",
            limits=BudgetAmount(token_count=100, tool_call_count=10),
            reserved=BudgetAmount(token_count=100, tool_call_count=10),
            status=BudgetReservationStatus.PENDING,
        ),
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id=None,
        attempt_id=None,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            0,
            "BUDGET_RESERVATION_CREATED",
        ),
    )
    store.transition_budget(
        budget_id,
        expected_version=0,
        target_status=BudgetReservationStatus.ACTIVE,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            1,
            "BUDGET_RESERVATION_ACTIVE",
        ),
    )
    return budget_id


def _reader(store) -> TerminalReconciliationQueryRepository:
    return TerminalReconciliationQueryRepository(store.db_path)


def _direct_graph_status(
    store,
    *,
    status: str,
    version: int,
) -> None:
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT graph_json FROM task_runtime_graph WHERE graph_id = ? AND revision = 1",
            (GRAPH_ID,),
        ).fetchone()
        payload = json.loads(str(row[0]))
        payload["status"] = status
        conn.execute(
            """
            UPDATE task_runtime_graph
            SET status = ?, status_version = ?, graph_json = ?, updated_ts_ms = ?
            WHERE graph_id = ? AND revision = 1
            """,
            (status, version, _dumps(payload), 1_910_000_000_100, GRAPH_ID),
        )
        conn.commit()


def _fresh_graph_store(tmp_path: Path) -> DurableTaskStore:
    db_path = tmp_path / "ne01-terminal-review-checkpoint.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="migration-ne01-terminal-review-checkpoint",
        ts_ms=NOW,
    )
    assert migration.success is True, migration.as_dict()
    store = DurableTaskStore(db_path, clock=MutableClock())
    store.create_graph(
        _graph(),
        outbox=_event(
            "TASK_GRAPH",
            GRAPH_ID,
            0,
            "TASK_GRAPH_CREATED",
            identity="terminal-review-graph-created",
        ),
    )
    store.transition_graph(
        GRAPH_ID,
        1,
        expected_version=0,
        target_status="ACTIVE",
        outbox=_event(
            "TASK_GRAPH",
            GRAPH_ID,
            1,
            "TASK_GRAPH_ACTIVE",
            identity="terminal-review-graph-active",
        ),
    )
    return store


def _install_pending_pre_execution_review(store: DurableTaskStore) -> None:
    graph_ref = GRAPH_ID
    node_id = "node-resolver"
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        graph = conn.execute(
            "SELECT * FROM task_runtime_graph WHERE graph_id = ? AND revision = 1",
            (graph_ref,),
        ).fetchone()
        graph_json = json.loads(graph["graph_json"])
        graph_json["status"] = "BLOCKED"
        node = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = 1 AND node_id = ?
            """,
            (graph_ref, node_id),
        ).fetchone()
        node_json = json.loads(node["node_json"])
        node_json["status"] = "WAITING_APPROVAL"
        node_json["status_version"] = int(node["status_version"]) + 1
        conn.execute(
            """
            UPDATE task_runtime_graph SET status = 'BLOCKED', status_version = 2,
                graph_json = ? WHERE graph_id = ? AND revision = 1
            """,
            (_dumps(graph_json), graph_ref),
        )
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = 'WAITING_APPROVAL', status_version = ?, node_json = ?
            WHERE graph_id = ? AND graph_revision = 1 AND node_id = ?
            """,
            (node_json["status_version"], _dumps(node_json), graph_ref, node_id),
        )
        subject_ref = f"task-node://{graph_ref}/1/{node_id}?root=test&attempt=not-started"
        review = {
            "schema_version": "Natsume.agent.review_item.v1",
            "review_id": "review-terminal-checkpoint",
            "review_type": "PRE_EXECUTION_APPROVAL",
            "subject_ref": subject_ref,
            "subject_version": node_json["status_version"],
            "requester": "test",
            "reason": "approval",
            "risk_level": 1,
            "side_effects": [],
            "evidence_refs": [],
            "diff_ref": None,
            "checkpoint_id": "checkpoint-terminal-review",
            "status": "PENDING",
            "expires_at": 1_910_000_060_000,
            "decision_actor": None,
            "token_ref": None,
            "resume_phase": "PRE_EXECUTION",
            "resume_count": 0,
            "resume_result_ref": None,
        }
        conn.execute(
            """
            INSERT INTO agent_runtime_review(
                review_id, schema_version, review_type, subject_ref,
                subject_version, requester, reason, risk_level,
                side_effects_json, evidence_refs_json, status, status_version,
                expires_at, resume_phase, checkpoint_id, resume_count,
                task_graph_id, task_node_id, review_json,
                created_ts_ms, updated_ts_ms
            ) VALUES (?, 'Natsume.agent.review_item.v1', ?, ?, ?, ?, ?, ?,
                      '[]', '[]', ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                review["review_id"], review["review_type"], subject_ref,
                review["subject_version"], review["requester"], review["reason"],
                review["risk_level"], review["status"], 0,
                review["expires_at"], review["resume_phase"], review["checkpoint_id"],
                graph_ref, node_id, _dumps(review), 1_910_000_000_000,
                1_910_000_000_000,
            ),
        )
        conn.commit()


def _insert_committed_operation_history(
    store: DurableTaskStore,
    *,
    suffix: str,
) -> None:
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            """
            SELECT attempt.attempt_id, lease.lease_id, lease.owner_id,
                   lease.fencing_token, lease.expires_ts_ms
            FROM task_runtime_attempt AS attempt
            JOIN task_runtime_lease AS lease
              ON lease.attempt_id = attempt.attempt_id
            WHERE attempt.graph_id = ? AND attempt.graph_revision = 1
            ORDER BY lease.fencing_token DESC LIMIT 1
            """,
            (GRAPH_ID,),
        ).fetchone()
        assert row is not None
        conn.execute(
            """
            INSERT INTO task_runtime_idempotency_reservation(
                reservation_id, business_scope, operation_fingerprint,
                attempt_id, lease_id, owner_id, fencing_token, expires_ts_ms,
                status, status_version, receipt_ref, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'COMMITTED', 1, ?, ?, ?)
            """,
            (
                f"reservation-terminal-{suffix}",
                f"scope-terminal-{suffix}",
                f"fingerprint-terminal-{suffix}",
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                f"receipt://terminal/{suffix}",
                NOW,
                NOW,
            ),
        )
        conn.commit()


def test_active_and_created_graphs_are_never_inventory_candidates(
    tmp_path: Path,
) -> None:
    store, _ = _running_store(tmp_path)
    _active_budget(store)

    assert _reader(store).list_terminal_reconciliation_inventory_candidates() == ()

    _direct_graph_status(store, status="CREATED", version=2)
    assert _reader(store).list_terminal_reconciliation_inventory_candidates() == ()


def test_failed_active_budget_requires_exact_atomic_failure_bundle(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail(store, context["lease"])

    first = _reader(store).list_terminal_reconciliation_inventory_candidates()
    second = _reader(store).list_terminal_reconciliation_inventory_candidates()

    assert len(first) == 1
    case = first[0]
    assert case["case_kind"] == "FAILED_UNSETTLED_BUDGET"
    assert case["classification"] == "EXACT_REPAIRABLE"
    assert case["recommended_action"] == "RELEASE_BUDGET"
    assert case["snapshot_sha256"].startswith("sha256:")
    assert case["case_id"].startswith("terminal-reconciliation-case://sha256/")
    assert first == second
    assert json.dumps(
        json.loads(case["snapshot_json"]),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) == case["snapshot_json"]


def test_inventory_and_system_repair_round_trip_excludes_proved_candidate(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    budget_id = _active_budget(store)
    _fail(store, context["lease"])

    inventory = store.inventory_terminal_reconciliation_cases()
    assert inventory["inserted_count"] == 1
    assert len(store.list_terminal_reconciliation_candidates()) == 1
    with pytest.raises(TaskStoreError) as blocked:
        store.assert_terminal_reconciliation_production_ready()
    assert (
        blocked.value.reason_code
        == TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED
    )

    service = TerminalStateReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        clock_ms=lambda: 1_910_000_000_100,
    )
    result = service.reconcile_exact_cases()

    assert result["reconciled_count"] == 1
    assert store.list_terminal_reconciliation_candidates() == ()
    case_id = inventory["cases"][0]["case_id"]
    aggregate = store.read_terminal_reconciliation_case(case_id)
    assert aggregate["proof"]["authority_kind"] == "SYSTEM_DURABLE_EVIDENCE"
    assert aggregate["proof"]["action"] == "RELEASE_BUDGET"
    assert store.reconcile_budget(budget_id)["status"] == "RELEASED"
    store.assert_terminal_reconciliation_production_ready()


def test_inventoried_case_fences_ordinary_budget_transition(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    budget_id = _active_budget(store)
    _fail(store, context["lease"])
    inventory = store.inventory_terminal_reconciliation_cases()
    before_outbox_count = len(store.list_pending_outbox(limit=500))

    with pytest.raises(TaskStoreError) as rejected:
        store.transition_budget(
            budget_id,
            expected_version=1,
            target_status="RELEASED",
            outbox=_event(
                "BUDGET_RESERVATION",
                budget_id,
                2,
                "BUDGET_RESERVATION_RELEASED",
                identity="ordinary-terminal-budget-bypass",
            ),
        )

    assert rejected.value.reason_code == "TASK_STORE_CAS_CONFLICT"
    assert store.reconcile_budget(budget_id)["status"] == "ACTIVE"
    assert len(store.list_pending_outbox(limit=500)) == before_outbox_count
    assert store.read_terminal_reconciliation_case(
        inventory["cases"][0]["case_id"]
    )["proof"] is None
    with pytest.raises(TaskStoreError) as blocked:
        store.assert_terminal_reconciliation_production_ready()
    assert (
        blocked.value.reason_code
        == TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED
    )


def test_inventoried_case_fences_ordinary_graph_transition(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail_node(store, context["lease"])
    store.transition_graph(
        GRAPH_ID,
        1,
        expected_version=1,
        target_status="BLOCKED",
        outbox=_event(
            "TASK_GRAPH",
            GRAPH_ID,
            2,
            "TASK_GRAPH_BLOCKED",
            identity="terminal-reconciliation-blocked",
        ),
    )
    inventory = store.inventory_terminal_reconciliation_cases()
    assert inventory["cases"][0]["classification"] == "SIGNED_REPAIR_REQUIRED"
    before_outbox_count = len(store.list_pending_outbox(limit=500))

    with pytest.raises(TaskStoreError) as rejected:
        store.transition_graph(
            GRAPH_ID,
            1,
            expected_version=2,
            target_status="FAILED",
            outbox=_event(
                "TASK_GRAPH",
                GRAPH_ID,
                3,
                "TASK_GRAPH_FAILED",
                identity="ordinary-terminal-graph-bypass",
            ),
        )

    assert rejected.value.reason_code == "TASK_STORE_CAS_CONFLICT"
    assert store.get_graph(GRAPH_ID, 1)["status"] == "BLOCKED"
    assert len(store.list_pending_outbox(limit=500)) == before_outbox_count


def test_reclassification_supersession_chain_requires_replacement_proof(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail(store, context["lease"])
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            """
            SELECT outbox_id, evidence_refs_json
            FROM task_runtime_outbox
            WHERE event_type = 'TASK_NODE_FAILED'
            """
        ).fetchone()
        assert row is not None
        original_evidence = str(row[1])
        conn.execute(
            "UPDATE task_runtime_outbox SET evidence_refs_json = '[]' WHERE outbox_id = ?",
            (row[0],),
        )
        conn.commit()

    first = store.inventory_terminal_reconciliation_cases(limit=10_000)
    assert first["cases"][0]["classification"] == "QUARANTINED_STRUCTURAL"
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE task_runtime_outbox SET evidence_refs_json = ? WHERE outbox_id = ?",
            (original_evidence, row[0]),
        )
        conn.commit()

    second = store.inventory_terminal_reconciliation_cases(limit=10_000)
    assert second["inserted_count"] == 1
    assert second["cases"][0]["classification"] == "EXACT_REPAIRABLE"
    with sqlite3.connect(store.db_path) as conn:
        supersession = conn.execute(
            """
            SELECT prior_case_id, replacement_case_id
            FROM task_runtime_terminal_reconciliation_supersession
            """
        ).fetchone()
    assert supersession == (
        first["cases"][0]["case_id"],
        second["cases"][0]["case_id"],
    )

    service = TerminalStateReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        clock_ms=lambda: 1_910_000_000_300,
    )
    result = service.reconcile_exact_cases()

    assert result["status"] == "COMPLETED"
    assert result["reconciled_count"] == 1
    store.assert_terminal_reconciliation_production_ready()


def test_outbox_delivery_status_is_not_reconciliation_authority(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail(store, context["lease"])
    before = store.list_terminal_reconciliation_inventory_candidates()[0]
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_outbox
            SET status = 'PUBLISHED', status_version = status_version + 1
            WHERE event_type IN (
                'TASK_ATTEMPT_FAILED', 'TASK_NODE_FAILED',
                'TASK_GRAPH_FAILED', 'TASK_LEASE_RELEASED_TERMINAL'
            )
            """
        )
        conn.commit()

    after = store.list_terminal_reconciliation_inventory_candidates()[0]

    assert after["case_id"] == before["case_id"]
    assert after["snapshot_sha256"] == before["snapshot_sha256"]


def test_late_operation_reclassifies_old_exact_case_without_mutation(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    budget_id = _active_budget(store)
    _fail(store, context["lease"])
    old = store.inventory_terminal_reconciliation_cases()["cases"][0]
    _insert_committed_operation_history(store, suffix="late-authority")

    assert store.list_terminal_reconciliation_candidates(
        classification="EXACT_REPAIRABLE"
    ) == ()
    service = TerminalStateReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        clock_ms=lambda: 1_910_000_000_400,
    )
    result = service.reconcile_exact_cases()
    assert result["reconciled_count"] == 0
    assert store.reconcile_budget(budget_id)["status"] == "ACTIVE"

    current = store.inventory_terminal_reconciliation_cases(limit=10_000)
    assert current["inserted_count"] == 1
    assert current["cases"][0]["classification"] == "QUARANTINED_STRUCTURAL"
    with sqlite3.connect(store.db_path) as conn:
        link = conn.execute(
            """
            SELECT replacement_case_id
            FROM task_runtime_terminal_reconciliation_supersession
            WHERE prior_case_id = ?
            """,
            (old["case_id"],),
        ).fetchone()
    assert link == (current["cases"][0]["case_id"],)
    with pytest.raises(TaskStoreError) as blocked:
        store.assert_terminal_reconciliation_production_ready()
    assert blocked.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_incremental_inventory_advances_past_already_persisted_batch(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail(store, context["lease"])

    second_graph_id = "graph-ne01-terminal-pagination"
    second_payload = _graph().to_dict()
    second_payload["graph_id"] = second_graph_id
    second_payload["root_work_order_id"] = "work-order-ne01-terminal-pagination"
    second_payload["planner_ref"] = "planner://ne01/terminal-pagination"
    second_payload["nodes"][0]["node_id"] = "node-terminal-pagination"
    second_graph = type(_graph()).from_mapping(second_payload)
    store.create_graph(
        second_graph,
        outbox=_event(
            "TASK_GRAPH",
            second_graph_id,
            0,
            "TASK_GRAPH_CREATED",
            identity="terminal-pagination-created",
        ),
    )
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT graph_json FROM task_runtime_graph WHERE graph_id = ? AND revision = 1",
            (second_graph_id,),
        ).fetchone()
        payload = json.loads(str(row[0]))
        payload["status"] = "COMPLETED"
        conn.execute(
            """
            UPDATE task_runtime_graph
            SET status = 'COMPLETED', status_version = 1,
                graph_json = ?, updated_ts_ms = ?
            WHERE graph_id = ? AND revision = 1
            """,
            (_dumps(payload), 1_910_000_000_200, second_graph_id),
        )
        conn.commit()

    first = store.inventory_terminal_reconciliation_cases(limit=1)
    second = store.inventory_terminal_reconciliation_cases(limit=1)
    exhausted = store.inventory_terminal_reconciliation_cases(limit=1)

    assert first["inserted_count"] == 1
    assert second["inserted_count"] == 1
    assert first["cases"][0]["case_id"] != second["cases"][0]["case_id"]
    assert exhausted["discovered_count"] == 0
    assert exhausted["inserted_count"] == 0
    assert len(store.list_terminal_reconciliation_cases(limit=10)) == 2


def test_small_inventory_limit_scans_past_healthy_terminal_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail(store, context["lease"])
    healthy_graph_ids: set[str] = set()
    for index in range(17):
        graph_id = f"graph-ne01-terminal-healthy-prefix-{index:02d}"
        healthy_graph_ids.add(graph_id)
        payload = _graph().to_dict()
        payload["graph_id"] = graph_id
        payload["root_work_order_id"] = f"work-order-healthy-prefix-{index:02d}"
        payload["planner_ref"] = f"planner://ne01/healthy-prefix/{index:02d}"
        payload["nodes"][0]["node_id"] = f"node-healthy-prefix-{index:02d}"
        graph = type(_graph()).from_mapping(payload)
        store.create_graph(
            graph,
            outbox=_event(
                "TASK_GRAPH",
                graph_id,
                0,
                "TASK_GRAPH_CREATED",
                identity=f"healthy-prefix-created-{index:02d}",
            ),
        )
    with sqlite3.connect(store.db_path) as conn:
        for graph_id in healthy_graph_ids:
            row = conn.execute(
                "SELECT graph_json FROM task_runtime_graph "
                "WHERE graph_id = ? AND revision = 1",
                (graph_id,),
            ).fetchone()
            payload = json.loads(str(row[0]))
            payload["status"] = "COMPLETED"
            conn.execute(
                """
                UPDATE task_runtime_graph
                SET status = 'COMPLETED', status_version = 1,
                    graph_json = ?, updated_ts_ms = ?
                WHERE graph_id = ? AND revision = 1
                """,
                (_dumps(payload), NOW - 1, graph_id),
            )
        conn.commit()

    original_observation = store._terminal_observation  # noqa: SLF001
    original_classifier = store._classify_terminal_observation  # noqa: SLF001

    def observation(conn, graph):
        if str(graph["graph_id"]) in healthy_graph_ids:
            return {
                "graph": {
                    "graph_id": str(graph["graph_id"]),
                    "revision": int(graph["revision"]),
                },
                "budgets": [],
            }
        return original_observation(conn, graph)

    def classify(conn, value):
        if str(value["graph"]["graph_id"]) in healthy_graph_ids:
            return None
        return original_classifier(conn, value)

    monkeypatch.setattr(store, "_terminal_observation", observation)
    monkeypatch.setattr(store, "_classify_terminal_observation", classify)

    cases = store.list_terminal_reconciliation_inventory_candidates(limit=1)

    assert len(cases) == 1
    assert cases[0]["graph_id"] == GRAPH_ID


@pytest.mark.parametrize("graph_status", ("FAILED", "CANCELLED"))
@pytest.mark.parametrize("budget_status", ("PENDING", "CONSUMED"))
def test_terminal_graph_with_non_releasable_budget_is_quarantined(
    tmp_path: Path,
    graph_status: str,
    budget_status: str,
) -> None:
    store, context = _running_store(tmp_path)
    budget_id = _active_budget(store)
    _fail(store, context["lease"])
    if graph_status == "CANCELLED":
        _direct_graph_status(store, status="CANCELLED", version=3)
    # Historical/imported corruption fixture: neither state has a safe
    # terminal repair transition, so discovery must never silently ignore it.
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_budget_reservation
            SET status = ?, status_version = status_version + 1
            WHERE budget_id = ?
            """,
            (budget_status, budget_id),
        )
        conn.commit()

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["case_kind"] == "TERMINAL_STRUCTURAL_ANOMALY"
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert cases[0]["recommended_action"] == "NONE"


def test_failed_budget_with_failure_bundle_drift_is_quarantined(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail(store, context["lease"])
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            """
            SELECT outbox_id FROM task_runtime_outbox
            WHERE event_type = 'TASK_NODE_FAILED'
            """
        ).fetchone()
        conn.execute(
            "UPDATE task_runtime_outbox SET evidence_refs_json = '[]' WHERE outbox_id = ?",
            (row[0],),
        )
        conn.commit()

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["case_kind"] == "TERMINAL_STRUCTURAL_ANOMALY"
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert cases[0]["recommended_action"] == "NONE"


def test_failed_budget_with_attempt_outbox_payload_drift_is_quarantined(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail(store, context["lease"])
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_outbox
            SET payload_ref = 'taskstore://event/TASK_ATTEMPT/tampered/999'
            WHERE event_type = 'TASK_ATTEMPT_FAILED'
            """
        )
        conn.commit()

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert cases[0]["recommended_action"] == "NONE"


def test_terminal_projection_codec_drift_is_inventoried_as_readable_quarantine(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail(store, context["lease"])
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE task_runtime_graph SET graph_json = '{\"status\":\"ACTIVE\"}' WHERE graph_id = ?",
            (GRAPH_ID,),
        )
        conn.commit()

    discovered = _reader(store).list_terminal_reconciliation_inventory_candidates()
    assert len(discovered) == 1
    assert discovered[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert discovered[0]["reason_code"] == "TERMINAL_DURABLE_PROJECTION_INVALID"

    inventoried = store.inventory_terminal_reconciliation_cases()
    aggregate = store.read_terminal_reconciliation_case(
        inventoried["cases"][0]["case_id"]
    )
    assert aggregate["case"]["classification"] == "QUARANTINED_STRUCTURAL"
    assert aggregate["subject"]["snapshot_sha256"].startswith("sha256:")


def test_clean_terminal_graph_identity_drift_blocks_production(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    budget_id = _active_budget(store)
    _fail(store, context["lease"])
    store.transition_budget(
        budget_id,
        expected_version=1,
        target_status="RELEASED",
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            2,
            "BUDGET_RESERVATION_RELEASED",
            identity="terminal-clean-budget-release",
        ),
    )
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT graph_json FROM task_runtime_graph WHERE graph_id = ? AND revision = 1",
            (GRAPH_ID,),
        ).fetchone()
        payload = json.loads(str(row[0]))
        payload["root_work_order_id"] = "work-order-forged-terminal"
        conn.execute(
            "UPDATE task_runtime_graph SET graph_json = ? WHERE graph_id = ? AND revision = 1",
            (_dumps(payload), GRAPH_ID),
        )
        conn.commit()

    cases = store.list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert cases[0]["reason_code"] == "TERMINAL_DURABLE_PROJECTION_INVALID"
    with pytest.raises(TaskStoreError) as blocked:
        store.assert_terminal_reconciliation_production_ready()
    assert blocked.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_unsettled_child_budget_is_never_repairable_as_graph_budget(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    attempt_id = str(context["lease"]["attempt_id"])
    budget_id = "budget-terminal-child"
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=budget_id,
            scope=f"attempt:{attempt_id}",
            limits=BudgetAmount(token_count=10),
            reserved=BudgetAmount(token_count=10),
            status=BudgetReservationStatus.PENDING,
        ),
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id="node-resolver",
        attempt_id=attempt_id,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            0,
            "BUDGET_RESERVATION_CREATED",
            attempt_id=attempt_id,
            identity="terminal-child-budget-created",
        ),
    )
    store.transition_budget(
        budget_id,
        expected_version=0,
        target_status="ACTIVE",
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            1,
            "BUDGET_RESERVATION_ACTIVE",
            attempt_id=attempt_id,
            identity="terminal-child-budget-active",
        ),
    )
    _fail(store, context["lease"])

    cases = store.list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert cases[0]["reason_code"] == "TERMINAL_GRAPH_BUDGET_BINDING_AMBIGUOUS"
    store.inventory_terminal_reconciliation_cases()
    service = TerminalStateReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        clock_ms=lambda: NOW,
    )
    assert service.reconcile_exact_cases()["reconciled_count"] == 0
    assert store.reconcile_budget(budget_id)["status"] == "ACTIVE"


def test_failed_exceeded_budget_remains_exact_when_bundle_is_exact(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    budget_id = _active_budget(store)
    usage_id = "budget-usage://sha256/" + hashlib.sha256(
        b"terminal-reconciliation-exceeded"
    ).hexdigest()
    store.consume_budget_once(
        budget_id,
        usage_id=usage_id,
        charge_kind="WORKER_USAGE",
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id="node-resolver",
        attempt_id="attempt-ne01-atomic-failure",
        delta=BudgetAmount(token_count=101),
        expected_version=1,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            2,
            "BUDGET_EXCEEDED",
            attempt_id="attempt-ne01-atomic-failure",
            evidence_refs=(usage_id,),
        ),
    )
    _fail(store, context["lease"])

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["classification"] == "EXACT_REPAIRABLE"
    assert cases[0]["budget_status"] == "EXCEEDED"


def test_blocked_failure_requires_signed_reconciliation(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail(
        store,
        context["lease"],
        graph_target_status="BLOCKED",
    )

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["case_kind"] == "BLOCKED_FAILURE_NODE"
    assert cases[0]["classification"] == "SIGNED_REPAIR_REQUIRED"
    assert cases[0]["recommended_action"] == "FAIL_GRAPH_AND_RELEASE_BUDGET"


def test_blocked_failure_with_non_releasable_budget_is_quarantined(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    budget_id = _active_budget(store)
    _fail(store, context["lease"], graph_target_status="BLOCKED")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE task_runtime_budget_reservation SET status = 'CONSUMED' WHERE budget_id = ?",
            (budget_id,),
        )
        conn.commit()

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert cases[0]["recommended_action"] == "NONE"


def test_any_operation_history_disqualifies_automatic_failure_repair(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail(store, context["lease"])
    lease = context["lease"]
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_idempotency_reservation(
                reservation_id, business_scope, operation_fingerprint,
                attempt_id, lease_id, owner_id, fencing_token, expires_ts_ms,
                status, status_version, receipt_ref, created_ts_ms, updated_ts_ms
            ) VALUES (
                'reservation-terminal-history', 'scope-terminal-history',
                'fingerprint-terminal-history', 'attempt-ne01-atomic-failure',
                ?, ?, ?, ?, 'COMMITTED', 1,
                'receipt://terminal/history', ?, ?
            )
            """,
            (
                lease["lease_id"],
                lease["owner_id"],
                lease["fencing_token"],
                1_910_000_060_000,
                1_910_000_000_000,
                1_910_000_000_000,
            ),
        )
        conn.commit()

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"


def test_second_same_version_graph_outbox_disqualifies_exact_bundle(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    _active_budget(store)
    _fail(store, context["lease"])
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_outbox(
                outbox_id, aggregate_type, aggregate_id, graph_revision,
                aggregate_version, event_type, payload_ref,
                evidence_refs_json, attempt_id, idempotency_key,
                status, available_ts_ms, created_ts_ms, status_version
            ) VALUES (
                'outbox::terminal-conflicting-event', 'TASK_GRAPH', ?, 1, 2,
                'TASK_GRAPH_BLOCKED', ?, '[]', NULL,
                'terminal-conflicting-event', 'PENDING', ?, ?, 0
            )
            """,
            (
                GRAPH_ID,
                f"taskstore://event/TASK_GRAPH/{GRAPH_ID}@1/2",
                1_910_000_000_000,
                1_910_000_000_000,
            ),
        )
        conn.commit()

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()

    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"


def test_canonical_0090_blocked_decision_is_not_an_inventory_case(
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
    ) = _budget_settlement_fixture(tmp_path, graph_id="graph-canonical-0090")
    store.settle_parallel_graph_budget(
        "graph-canonical-0090",
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

    assert _reader(store).list_terminal_reconciliation_inventory_candidates() == ()


def test_canonical_0090_block_with_reactivated_lease_is_quarantined(
    tmp_path: Path,
) -> None:
    (
        store,
        attempt_id,
        _usage_id,
        states,
        decision,
        graph_outbox,
        budget_outbox,
    ) = _budget_settlement_fixture(tmp_path, graph_id="graph-canonical-0090-live")
    store.settle_parallel_graph_budget(
        "graph-canonical-0090-live",
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
            UPDATE task_runtime_lease
            SET status = 'ACTIVE', released_ts_ms = NULL
            WHERE attempt_id = ?
            """,
            (attempt_id,),
        )
        conn.commit()

    cases = store.list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert (
        cases[0]["reason_code"]
        == "BLOCKED_PARALLEL_DECISION_AFTER_STATE_DRIFTED"
    )
    with pytest.raises(TaskStoreError):
        store.assert_terminal_reconciliation_production_ready()


def test_clean_blocked_review_checkpoint_is_not_an_inventory_case(
    tmp_path: Path,
) -> None:
    store = _fresh_graph_store(tmp_path)
    _active_budget(store)
    _install_pending_pre_execution_review(store)

    assert _reader(store).list_terminal_reconciliation_inventory_candidates() == ()


def test_blocked_review_checkpoint_with_live_attempt_is_quarantined(
    tmp_path: Path,
) -> None:
    store, _ = _running_store(tmp_path)
    _active_budget(store)
    _install_pending_pre_execution_review(store)

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert cases[0]["reason_code"] == "TERMINAL_DURABLE_PROJECTION_INVALID"
    with pytest.raises(TaskStoreError) as blocked:
        store.assert_terminal_reconciliation_production_ready()
    assert blocked.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_review_json_or_subject_drift_is_quarantined(
    tmp_path: Path,
) -> None:
    store = _fresh_graph_store(tmp_path)
    _active_budget(store)
    _install_pending_pre_execution_review(store)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE agent_runtime_review
            SET review_json = '{}', subject_ref =
                'task-node://graph-ne01-atomic-failure/1/wrong-node?attempt=not-started'
            WHERE review_id = 'review-terminal-checkpoint'
            """
        )
        conn.commit()

    cases = store.list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert cases[0]["reason_code"] == "TERMINAL_DURABLE_PROJECTION_INVALID"
    with pytest.raises(TaskStoreError):
        store.assert_terminal_reconciliation_production_ready()


def test_terminal_attempt_and_lease_lineage_drift_is_quarantined(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    budget_id = _active_budget(store)
    _fail(store, context["lease"])
    store.transition_budget(
        budget_id,
        expected_version=1,
        target_status="RELEASED",
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            2,
            "BUDGET_RESERVATION_RELEASED",
            identity="terminal-lineage-budget-release",
        ),
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_attempt
            SET completed_ts_ms = NULL, error_ref = NULL,
                owner_id = 'forged-owner'
            WHERE graph_id = ? AND graph_revision = 1
            """,
            (GRAPH_ID,),
        )
        conn.execute(
            """
            UPDATE task_runtime_lease
            SET released_ts_ms = NULL
            WHERE attempt_id IN (
                SELECT attempt_id FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = 1
            )
            """,
            (GRAPH_ID,),
        )
        conn.commit()

    cases = store.list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    with pytest.raises(TaskStoreError):
        store.assert_terminal_reconciliation_production_ready()


def test_legacy_cancelled_budget_is_signed_but_0092_conflict_is_quarantined(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    budget_id = _active_budget(store)
    _fail(store, context["lease"])
    _direct_graph_status(store, status="CANCELLED", version=3)

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()
    assert len(cases) == 1
    assert cases[0]["case_kind"] == "CANCELLED_UNSETTLED_BUDGET"
    assert cases[0]["classification"] == "SIGNED_REPAIR_REQUIRED"

    with sqlite3.connect(store.db_path) as conn:
        graph_outbox_id = conn.execute(
            """
            SELECT outbox_id FROM task_runtime_outbox
            WHERE aggregate_type = 'TASK_GRAPH' AND aggregate_id = ?
            ORDER BY aggregate_version DESC LIMIT 1
            """,
            (GRAPH_ID,),
        ).fetchone()[0]
        payload_sha256 = "sha256:" + hashlib.sha256(b"{}").hexdigest()
        conn.execute(
            """
            INSERT INTO task_runtime_graph_cancellation_intent(
                intent_ref, schema_version, request_id, request_identity,
                source, reason_code, graph_id, graph_revision,
                graph_version_before, graph_version_after,
                budget_id, budget_version_at_request,
                payload_json, payload_sha256, graph_outbox_id,
                status, status_version, requested_ts_ms
            ) VALUES (
                'cancellation://terminal/conflict',
                'Natsume.task_runtime.graph_cancellation_intent.v1',
                'request-terminal-conflict', 'identity-terminal-conflict',
                'operator', 'LEGACY_CONFLICT', ?, 1, 2, 3, ?, 1,
                '{}', ?, ?, 'REQUESTED', 0, ?
            )
            """,
            (
                GRAPH_ID,
                budget_id,
                payload_sha256,
                graph_outbox_id,
                1_910_000_000_200,
            ),
        )
        conn.commit()

    conflicted = _reader(store).list_terminal_reconciliation_inventory_candidates()
    assert conflicted[0]["classification"] == "QUARANTINED_STRUCTURAL"


def test_completed_inconsistent_state_is_quarantined(tmp_path: Path) -> None:
    store, _ = _running_store(tmp_path)
    _active_budget(store)
    _direct_graph_status(store, status="COMPLETED", version=2)

    cases = _reader(store).list_terminal_reconciliation_inventory_candidates()

    assert len(cases) == 1
    assert cases[0]["case_kind"] == "COMPLETED_INCONSISTENT"
    assert cases[0]["classification"] == "QUARANTINED_STRUCTURAL"
    assert cases[0]["recommended_action"] == "NONE"
    assert store.inventory_terminal_reconciliation_cases()["inserted_count"] == 1
    with pytest.raises(TaskStoreError) as blocked:
        store.assert_terminal_reconciliation_production_ready()
    assert blocked.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
