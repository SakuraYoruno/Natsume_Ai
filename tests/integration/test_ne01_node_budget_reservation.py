from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
    build_outbox_payload_ref,
)
from tests.task_runtime_authority_fixtures import (
    seed_raw_task_graph_for_test,
)


NOW = 1_786_000_000_000
GRAPH_ID = "graph::node-budget-integration"
BUDGET_ID = "budget::node-budget-integration"


def _event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    discriminator: str,
    attempt_id: str | None = None,
    graph_revision: int | None = None,
) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            graph_revision=graph_revision,
        ),
        idempotency_key=f"node-budget-test:{discriminator}",
        outbox_id=f"outbox::node-budget-test:{discriminator}",
        attempt_id=attempt_id,
        graph_revision=graph_revision,
    )


def _claim_outboxes(
    attempt_id: str,
) -> tuple[OutboxMessage, OutboxMessage]:
    return (
        _event(
            "TASK_ATTEMPT",
            attempt_id,
            0,
            "TASK_ATTEMPT_CREATED",
            attempt_id=attempt_id,
            discriminator=f"{attempt_id}:created",
        ),
        _event(
            "TASK_ATTEMPT",
            attempt_id,
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id=attempt_id,
            discriminator=f"{attempt_id}:leased",
        ),
    )


def _charge_outbox(
    *,
    attempt_id: str,
    usage_id: str,
    aggregate_version: int,
) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type="BUDGET_RESERVATION",
        aggregate_id=BUDGET_ID,
        aggregate_version=aggregate_version,
        event_type="BUDGET_CONSUMED",
        payload_ref=build_outbox_payload_ref(
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id=BUDGET_ID,
            aggregate_version=aggregate_version,
        ),
        idempotency_key=f"node-budget-test:charge:{usage_id}",
        outbox_id=f"outbox::node-budget-test:charge:{usage_id}",
        attempt_id=attempt_id,
        evidence_refs=(usage_id,),
    )


def _store(
    tmp_path: Path,
    *,
    name: str = "node-budget.sqlite3",
    migrations_dir: Path | None = None,
) -> tuple[Path, DurableTaskStore]:
    db_path = tmp_path / name
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=migrations_dir,
    ).apply_migrations(
        request_id="node-budget-integration",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    nodes = tuple(
        TaskNode(
            node_id=node_id,
            dependencies=(),
            route="skill:test",
            input_refs=(),
            output_contract={"type": "object"},
            retry_policy={"max_attempts": 2},
            risk_level=1,
            acceptance_criteria=("accepted",),
            status=TaskNodeStatus.READY,
        )
        for node_id in ("node-a", "node-b")
    )
    seed_raw_task_graph_for_test(
        store,
        TaskGraph(
            graph_id=GRAPH_ID,
            root_work_order_id="work-order::node-budget",
            revision=1,
            planner_ref="planner://node-budget/integration",
            status=TaskGraphStatus.CREATED,
            created_at=NOW,
            nodes=nodes,
        ),
        outbox=_event(
            "TASK_GRAPH",
            GRAPH_ID,
            0,
            "TASK_GRAPH_CREATED",
            discriminator="graph-created",
            graph_revision=1,
        ),
    )
    store.transition_graph(
        GRAPH_ID,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event(
            "TASK_GRAPH",
            GRAPH_ID,
            1,
            "TASK_GRAPH_ACTIVE",
            discriminator="graph-active",
            graph_revision=1,
        ),
    )
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=BUDGET_ID,
            scope="work-order:node-budget",
            limits=BudgetAmount(
                token_count=10,
                tool_call_count=4,
                time_ms=10_000,
                concurrency=2,
                retry_count=2,
                model_cost_micros=1_000,
            ),
            reserved=BudgetAmount(
                token_count=10,
                tool_call_count=4,
                time_ms=10_000,
                concurrency=2,
                retry_count=2,
                model_cost_micros=1_000,
            ),
            status=BudgetReservationStatus.ACTIVE,
        ),
        graph_id=GRAPH_ID,
        graph_revision=1,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            0,
            "BUDGET_RESERVATION_CREATED",
            discriminator="budget-created",
        ),
    )
    return db_path, store


def test_atomic_claim_charge_restart_and_tamper_guard(
    tmp_path: Path,
) -> None:
    db_path, store = _store(tmp_path)
    claims: dict[str, dict[str, object]] = {}
    outboxes: dict[str, tuple[OutboxMessage, OutboxMessage]] = {}
    for node_id in ("node-a", "node-b"):
        attempt_id = f"attempt::{node_id}"
        created, leased = _claim_outboxes(attempt_id)
        outboxes[node_id] = (created, leased)
        claims[node_id] = store.claim_ready_node(
            attempt_id=attempt_id,
            graph_id=GRAPH_ID,
            graph_revision=1,
            node_id=node_id,
            expected_node_version=0,
            expected_attempt_count=0,
            owner_id=f"owner::{node_id}",
            lease_ttl_ms=60_000,
            budget_id=BUDGET_ID,
            operation_scope=f"node-budget:{node_id}",
            operation_fingerprint=f"sha256:{'a' if node_id == 'node-a' else 'b'}" + "0" * 63,
            attempt_created_outbox=created,
            attempt_leased_outbox=leased,
        )

    node_budget_ids = {
        str(claim["node_budget"]["node_budget_id"])
        for claim in claims.values()
    }
    assert len(node_budget_ids) == 2
    assert all(
        claim["node_budget"]["parent_budget_id"] == BUDGET_ID
        and claim["node_budget"]["reserved"]["concurrency"] == 1
        and claim["node_budget"]["status"] == "ACTIVE"
        for claim in claims.values()
    )

    for version, (node_id, tokens) in enumerate(
        (("node-a", 4), ("node-b", 3)),
        start=1,
    ):
        attempt_id = f"attempt::{node_id}"
        usage_id = f"budget-usage://node-budget/{node_id}"
        result = store.consume_budget_once(
            BUDGET_ID,
            usage_id=usage_id,
            charge_kind="WORKER_USAGE",
            graph_id=GRAPH_ID,
            graph_revision=1,
            node_id=node_id,
            attempt_id=attempt_id,
            delta=BudgetAmount(token_count=tokens),
            expected_version=version - 1,
            outbox=_charge_outbox(
                attempt_id=attempt_id,
                usage_id=usage_id,
                aggregate_version=version,
            ),
        )
        assert result.accepted is True

    reopened = DurableTaskStore(db_path, clock=lambda: NOW)
    snapshot = reopened.read_scheduler_snapshot(GRAPH_ID, 1)
    assert len(snapshot["node_budgets"]) == 2
    assert len(snapshot["node_budget_usage"]) == 2
    assert {
        item["node_id"]: item["consumed"]["token_count"]
        for item in snapshot["node_budgets"]
    } == {"node-a": 4, "node-b": 3}

    created, leased = outboxes["node-a"]
    replay = reopened.claim_ready_node(
        attempt_id="attempt::node-a",
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id="node-a",
        expected_node_version=0,
        expected_attempt_count=0,
        owner_id="owner::node-a",
        lease_ttl_ms=60_000,
        budget_id=BUDGET_ID,
        operation_scope="node-budget:node-a",
        operation_fingerprint="sha256:" + "a" + "0" * 63,
        attempt_created_outbox=created,
        attempt_leased_outbox=leased,
    )
    assert replay["replayed"] is True
    assert replay["node_budget"]["consumed"]["token_count"] == 4
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_node_budget_reservation"
        ).fetchone() == (2,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_node_budget_usage"
        ).fetchone() == (2,)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE task_runtime_node_budget_reservation
                SET node_id = 'tampered'
                WHERE attempt_id = 'attempt::node-a'
                """
            )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_node_budget_reservation
            SET consumed_json = json_set(
                consumed_json, '$.token_count', 5
            )
            WHERE attempt_id = 'attempt::node-a'
            """
        )
    with pytest.raises(TaskStoreError) as exc_info:
        reopened.get_node_budget_reservation(
            parent_budget_id=BUDGET_ID,
            attempt_id="attempt::node-a",
        )
    assert exc_info.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_0132_backfills_existing_attempt_usage_without_regaining_budget(
    tmp_path: Path,
) -> None:
    source_catalog = Path("host_brain/audit/migrations")
    prefix_catalog = tmp_path / "migrations-through-0131"
    prefix_catalog.mkdir()
    for source in source_catalog.glob("*.sql"):
        if int(source.name[:4]) <= 131:
            shutil.copy2(source, prefix_catalog / source.name)

    db_path, store = _store(
        tmp_path,
        name="node-budget-upgrade.sqlite3",
        migrations_dir=prefix_catalog,
    )
    attempt_id = "attempt::legacy-before-0132"
    store.create_attempt(
        attempt_id=attempt_id,
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id="node-a",
        attempt_no=1,
        expected_node_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            0,
            "TASK_ATTEMPT_CREATED",
            attempt_id=attempt_id,
            discriminator="legacy-attempt-created",
        ),
    )
    usage_id = "budget-usage://node-budget/legacy-before-0132"
    store.consume_budget_once(
        BUDGET_ID,
        usage_id=usage_id,
        charge_kind="WORKER_USAGE",
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id="node-a",
        attempt_id=attempt_id,
        delta=BudgetAmount(token_count=4),
        expected_version=0,
        outbox=_charge_outbox(
            attempt_id=attempt_id,
            usage_id=usage_id,
            aggregate_version=1,
        ),
    )

    shutil.copy2(
        source_catalog / "0132_task_node_budget_reservation.sql",
        prefix_catalog / "0132_task_node_budget_reservation.sql",
    )
    upgraded = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=prefix_catalog,
    ).apply_migrations(
        request_id="node-budget-upgrade-0132",
        ts_ms=NOW + 1,
    )
    assert upgraded.success is True, upgraded.as_dict()
    assert upgraded.summary["applied_count"] == 1

    reopened = DurableTaskStore(db_path, clock=lambda: NOW + 1)
    child = reopened.get_node_budget_reservation(
        parent_budget_id=BUDGET_ID,
        attempt_id=attempt_id,
    )
    assert child["claim_origin"] == "BACKFILL_0132"
    assert child["consumed"]["token_count"] == 4
    assert child["usage_version"] == 1
    binding = reopened.get_node_budget_usage(
        parent_budget_id=BUDGET_ID,
        usage_id=usage_id,
    )
    assert binding["attempt_id"] == attempt_id
    assert binding["accepted"] is True
