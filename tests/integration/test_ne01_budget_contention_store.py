from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import hashlib
from pathlib import Path
import sqlite3
from threading import Barrier
from typing import Any

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
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    OutboxMessage,
    TASK_STORE_BUDGET_ALREADY_EXCEEDED,
    TASK_STORE_BUDGET_EXCEEDED,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_INVALID,
    TASK_STORE_OUTBOX_CONFLICT,
    TaskStoreError,
    build_outbox_payload_ref,
)
from host_brain.persistence.task_decision_integrity import (
    _assert_parallel_budget_usage_lineage,
)
from tests.task_runtime_authority_fixtures import seed_raw_task_graph_for_test


NOW = 2_000_000_000_000
GRAPH_ID = "graph-budget-contention"
NODE_ID = "worker"
ATTEMPT_ID = "attempt::budget-contention"
BUDGET_ID = "budget::contention"


def _event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    attempt_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    discriminator: str,
) -> OutboxMessage:
    digest = hashlib.sha256(discriminator.encode("utf-8")).hexdigest()
    graph_revision = 1 if aggregate_type == "TASK_GRAPH" else None
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        graph_revision=graph_revision,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            graph_revision=graph_revision,
        ),
        idempotency_key=f"budget-contention:{digest}",
        outbox_id=f"outbox::budget-contention::{digest}",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def _budget_candidate(
    *,
    usage_id: str,
    aggregate_version: int,
    event_type: str,
) -> OutboxMessage:
    return _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        aggregate_version,
        event_type,
        attempt_id=ATTEMPT_ID,
        evidence_refs=(usage_id,),
        discriminator=f"candidate:{usage_id}",
    )


def _migrated_budget_db(tmp_path: Path, *, name: str, limit: int = 1) -> Path:
    db_path = tmp_path / name
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"budget-contention:{name}",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    graph = TaskGraph(
        graph_id=GRAPH_ID,
        root_work_order_id="work-order::budget-contention",
        revision=1,
        planner_ref="planner://budget-contention/1",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(
            TaskNode(
                node_id=NODE_ID,
                dependencies=(),
                route="skill:test",
                input_refs=(),
                output_contract={},
                retry_policy={"max_attempts": 1},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
    )
    seed_raw_task_graph_for_test(
        store,
        graph,
        outbox=_event(
            "TASK_GRAPH",
            GRAPH_ID,
            0,
            "TASK_GRAPH_CREATED",
            discriminator=f"graph:{name}",
        ),
    )
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=BUDGET_ID,
            scope="work-order:budget-contention",
            limits=BudgetAmount(token_count=limit),
            reserved=BudgetAmount(token_count=limit),
            status=BudgetReservationStatus.ACTIVE,
            status_version=0,
        ),
        graph_id=GRAPH_ID,
        graph_revision=1,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            0,
            "BUDGET_RESERVATION_CREATED",
            discriminator=f"budget:{name}",
        ),
    )
    store.create_attempt(
        attempt_id=ATTEMPT_ID,
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id=NODE_ID,
        attempt_no=1,
        expected_node_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            ATTEMPT_ID,
            0,
            "TASK_ATTEMPT_CREATED",
            attempt_id=ATTEMPT_ID,
            discriminator=f"attempt:{name}",
        ),
    )
    return db_path


def _charge_with_bounded_refresh(
    db_path: str,
    usage_id: str,
    token_count: int,
    *,
    initial_expected_version: int = 0,
) -> dict[str, Any]:
    expected_version = int(initial_expected_version)
    calls = 0
    while calls < 2:
        calls += 1
        store = DurableTaskStore(db_path, clock=lambda: NOW)
        try:
            result = store.consume_budget_once(
                BUDGET_ID,
                usage_id=usage_id,
                charge_kind="WORKER_USAGE",
                graph_id=GRAPH_ID,
                graph_revision=1,
                node_id=NODE_ID,
                attempt_id=ATTEMPT_ID,
                delta=BudgetAmount(token_count=token_count),
                expected_version=expected_version,
                outbox=_budget_candidate(
                    usage_id=usage_id,
                    aggregate_version=expected_version + 1,
                    event_type="BUDGET_EXCEEDED",
                ),
            )
            return {
                "accepted": result.accepted,
                "reason_code": result.reason_code,
                "replayed": result.replayed,
                "calls": calls,
                "usage": result.usage,
            }
        except TaskStoreError as exc:
            if exc.reason_code != TASK_STORE_CAS_CONFLICT or calls >= 2:
                raise
            expected_version = int(store.reconcile_budget(BUDGET_ID)["status_version"])
    raise AssertionError("bounded budget CAS did not settle in two calls")


def _sql_budget_truth(db_path: Path) -> tuple[Any, list[Any], list[Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        budget = conn.execute(
            "SELECT * FROM task_runtime_budget_reservation WHERE budget_id = ?",
            (BUDGET_ID,),
        ).fetchone()
        usages = conn.execute(
            """
            SELECT * FROM task_runtime_budget_usage
            WHERE budget_id = ? ORDER BY usage_id
            """,
            (BUDGET_ID,),
        ).fetchall()
        outboxes = conn.execute(
            """
            SELECT * FROM task_runtime_outbox
            WHERE aggregate_type = 'BUDGET_RESERVATION' AND aggregate_id = ?
            ORDER BY aggregate_version, outbox_id
            """,
            (BUDGET_ID,),
        ).fetchall()
    assert budget is not None
    return dict(budget), [dict(row) for row in usages], [dict(row) for row in outboxes]


def test_terminal_budget_transition_refuses_unsettled_attempt_authority(
    tmp_path: Path,
) -> None:
    db_path = _migrated_budget_db(tmp_path, name="terminal-budget-fence.sqlite3")
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    before = store.reconcile_budget(BUDGET_ID)

    with pytest.raises(TaskStoreError) as blocked:
        store.transition_budget(
            BUDGET_ID,
            expected_version=int(before["status_version"]),
            target_status=BudgetReservationStatus.RELEASED,
            outbox=_event(
                "BUDGET_RESERVATION",
                BUDGET_ID,
                int(before["status_version"]) + 1,
                "BUDGET_RESERVATION_RELEASED",
                discriminator="terminal-budget-fenced",
            ),
        )

    assert blocked.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert store.reconcile_budget(BUDGET_ID) == before


def test_terminal_budget_atomically_fences_later_attempt_authority(
    tmp_path: Path,
) -> None:
    db_path = _migrated_budget_db(tmp_path, name="terminal-budget-wins.sqlite3")
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM task_runtime_attempt WHERE attempt_id = ?",
            (ATTEMPT_ID,),
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
            discriminator="terminal-budget-wins:graph-active",
        ),
    )
    store.transition_budget(
        BUDGET_ID,
        expected_version=0,
        target_status=BudgetReservationStatus.RELEASED,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            1,
            "BUDGET_RESERVATION_RELEASED",
            discriminator="terminal-budget-wins:released",
        ),
    )
    attempt_id = "attempt::after-budget-terminal"
    with pytest.raises(TaskStoreError) as blocked:
        store.create_attempt(
            attempt_id=attempt_id,
            graph_id=GRAPH_ID,
            graph_revision=1,
            node_id=NODE_ID,
            attempt_no=1,
            expected_node_version=0,
            operation_scope="host-command:after-budget-terminal",
            operation_fingerprint="sha256:" + "a" * 64,
            outbox=_event(
                "TASK_ATTEMPT",
                attempt_id,
                0,
                "TASK_ATTEMPT_CREATED",
                attempt_id=attempt_id,
                discriminator="terminal-budget-wins:attempt",
            ),
        )

    assert blocked.value.reason_code == TASK_STORE_CAS_CONFLICT
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_attempt WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_lease"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_idempotency_reservation"
        ).fetchone() == (0,)


def test_parallel_decision_read_revalidates_terminal_sibling_authority(
    tmp_path: Path,
) -> None:
    db_path = _migrated_budget_db(tmp_path, name="decision-read-authority.db")
    winner_id = "budget-usage://decision-read/winner"
    sibling_id = "budget-usage://decision-read/sibling"
    winner = _charge_with_bounded_refresh(str(db_path), winner_id, 2)
    sibling = _charge_with_bounded_refresh(str(db_path), sibling_id, 2)
    assert winner["reason_code"] == TASK_STORE_BUDGET_EXCEEDED
    assert sibling["reason_code"] == TASK_STORE_BUDGET_ALREADY_EXCEEDED
    payload = {
        "decision_kind": "BUDGET",
        "budget_id": BUDGET_ID,
        "budget_status_version_before": 1,
        "graph_id": GRAPH_ID,
        "graph_revision": 1,
        "selected_node_id": NODE_ID,
        "outcomes": [
            {
                "node_id": NODE_ID,
                "attempt_id": ATTEMPT_ID,
                "evidence_refs": [winner_id],
            }
        ],
    }
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _assert_parallel_budget_usage_lineage(
            conn,
            payload,
            reason_code="TEST_INVALID",
        )
        conn.execute(
            """
            UPDATE task_runtime_budget_usage SET outbox_id = ?
            WHERE budget_id = ? AND usage_id = ?
            """,
            (
                "outbox::budget-contention::"
                + hashlib.sha256(b"tampered-sibling").hexdigest(),
                BUDGET_ID,
                sibling_id,
            ),
        )
        with pytest.raises(TaskStoreError) as invalid:
            _assert_parallel_budget_usage_lineage(
                conn,
                payload,
                reason_code="TEST_INVALID",
            )
    assert invalid.value.reason_code == "TEST_INVALID"


@pytest.mark.parametrize("workers", (8, 16, 32))
def test_budget_contention_settles_in_two_calls_with_one_terminal_authority(
    tmp_path: Path,
    workers: int,
) -> None:
    db_path = _migrated_budget_db(
        tmp_path,
        name=f"contention-{workers}.sqlite3",
    )
    barrier = Barrier(workers)

    def contend(index: int) -> dict[str, Any]:
        usage_id = f"budget-usage://contention/{workers}/{index}"
        barrier.wait(timeout=10)
        return _charge_with_bounded_refresh(
            str(db_path),
            usage_id,
            token_count=2 + index,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(contend, range(workers)))

    assert all(outcome["accepted"] is False for outcome in outcomes)
    assert max(outcome["calls"] for outcome in outcomes) <= 2
    assert [outcome["reason_code"] for outcome in outcomes].count(
        TASK_STORE_BUDGET_EXCEEDED
    ) == 1
    assert [outcome["reason_code"] for outcome in outcomes].count(
        TASK_STORE_BUDGET_ALREADY_EXCEEDED
    ) == workers - 1

    budget, usages, outboxes = _sql_budget_truth(db_path)
    assert budget["status"] == "EXCEEDED"
    assert budget["status_version"] == 1
    assert len(usages) == workers
    winners = [row for row in usages if row["reason_code"] == TASK_STORE_BUDGET_EXCEEDED]
    siblings = [
        row
        for row in usages
        if row["reason_code"] == TASK_STORE_BUDGET_ALREADY_EXCEEDED
    ]
    assert len(winners) == 1
    assert len(siblings) == workers - 1
    winner = winners[0]
    assert all(row["reservation_version_before"] == 1 for row in siblings)
    assert all(row["reservation_version_after"] == 1 for row in siblings)
    assert all(row["outbox_id"] == winner["outbox_id"] for row in siblings)
    terminal_outboxes = [row for row in outboxes if row["event_type"] == "BUDGET_EXCEEDED"]
    assert len(terminal_outboxes) == 1
    assert terminal_outboxes[0]["outbox_id"] == winner["outbox_id"]
    assert len(outboxes) == 2  # reservation creation + the sole terminal transition


def test_exceeded_winner_keeps_consumed_state_across_accepted_denied_interleave(
    tmp_path: Path,
) -> None:
    db_path = _migrated_budget_db(tmp_path, name="interleave.sqlite3", limit=10)
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    accepted_usage = "budget-usage://interleave/accepted"
    accepted = store.consume_budget_once(
        BUDGET_ID,
        usage_id=accepted_usage,
        charge_kind="WORKER_USAGE",
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id=NODE_ID,
        attempt_id=ATTEMPT_ID,
        delta=BudgetAmount(token_count=4),
        expected_version=0,
        outbox=_budget_candidate(
            usage_id=accepted_usage,
            aggregate_version=1,
            event_type="BUDGET_CONSUMED",
        ),
    )
    assert accepted.accepted is True

    winner_usage = "budget-usage://interleave/winner"
    winner = store.consume_budget_once(
        BUDGET_ID,
        usage_id=winner_usage,
        charge_kind="WORKER_USAGE",
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id=NODE_ID,
        attempt_id=ATTEMPT_ID,
        delta=BudgetAmount(token_count=7),
        expected_version=1,
        outbox=_budget_candidate(
            usage_id=winner_usage,
            aggregate_version=2,
            event_type="BUDGET_EXCEEDED",
        ),
    )
    assert winner.reason_code == TASK_STORE_BUDGET_EXCEEDED

    sibling_specs = (
        ("budget-usage://interleave/small", 1, "BUDGET_CONSUMED"),
        ("budget-usage://interleave/large", 20, "BUDGET_EXCEEDED"),
    )
    for usage_id, tokens, event_type in sibling_specs:
        sibling = DurableTaskStore(db_path, clock=lambda: NOW).consume_budget_once(
            BUDGET_ID,
            usage_id=usage_id,
            charge_kind="WORKER_USAGE",
            graph_id=GRAPH_ID,
            graph_revision=1,
            node_id=NODE_ID,
            attempt_id=ATTEMPT_ID,
            delta=BudgetAmount(token_count=tokens),
            expected_version=2,
            outbox=_budget_candidate(
                usage_id=usage_id,
                aggregate_version=3,
                event_type=event_type,
            ),
        )
        assert sibling.reason_code == TASK_STORE_BUDGET_ALREADY_EXCEEDED
        assert sibling.exceeded_dimensions == ("token_count",)

    reconciled = DurableTaskStore(db_path, clock=lambda: NOW).reconcile_budget(BUDGET_ID)
    assert reconciled["status"] == "EXCEEDED"
    assert reconciled["status_version"] == 2
    assert reconciled["consumed"]["token_count"] == 4
    assert reconciled["attempted_consumed"]["token_count"] == 11
    budget, usages, outboxes = _sql_budget_truth(db_path)
    assert budget["status_version"] == 2
    assert len([row for row in usages if row["accepted"] == 1]) == 1
    assert len(
        [row for row in usages if row["reason_code"] == TASK_STORE_BUDGET_EXCEEDED]
    ) == 1
    assert len([row for row in outboxes if row["event_type"] == "BUDGET_EXCEEDED"]) == 1


def test_restart_response_loss_replay_and_usage_drift_preserve_terminal_truth(
    tmp_path: Path,
) -> None:
    db_path = _migrated_budget_db(tmp_path, name="replay.sqlite3")
    winner_usage = "budget-usage://replay/winner"
    # Discarding this return value simulates loss of the committed response.
    _charge_with_bounded_refresh(str(db_path), winner_usage, 2)
    sibling_usage = "budget-usage://replay/sibling"
    _charge_with_bounded_refresh(
        str(db_path),
        sibling_usage,
        3,
        initial_expected_version=1,
    )
    before = _sql_budget_truth(db_path)

    reopened = DurableTaskStore(db_path, clock=lambda: NOW)
    replay = reopened.consume_budget_once(
        BUDGET_ID,
        usage_id=sibling_usage,
        charge_kind="WORKER_USAGE",
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id=NODE_ID,
        attempt_id=ATTEMPT_ID,
        delta=BudgetAmount(token_count=3),
        expected_version=0,
        # Exact replay intentionally does not consume this new candidate.
        outbox=_budget_candidate(
            usage_id=sibling_usage,
            aggregate_version=1,
            event_type="BUDGET_EXCEEDED",
        ),
    )
    assert replay.replayed is True
    assert replay.reason_code == TASK_STORE_BUDGET_ALREADY_EXCEEDED
    assert _sql_budget_truth(db_path) == before

    with pytest.raises(TaskStoreError) as drifted:
        DurableTaskStore(db_path, clock=lambda: NOW).consume_budget_once(
            BUDGET_ID,
            usage_id=sibling_usage,
            charge_kind="WORKER_USAGE",
            graph_id=GRAPH_ID,
            graph_revision=1,
            node_id=NODE_ID,
            attempt_id=ATTEMPT_ID,
            delta=BudgetAmount(token_count=4),
            expected_version=1,
            outbox=_budget_candidate(
                usage_id=sibling_usage,
                aggregate_version=2,
                event_type="BUDGET_EXCEEDED",
            ),
        )
    assert drifted.value.reason_code == TASK_STORE_OUTBOX_CONFLICT
    assert _sql_budget_truth(db_path) == before


def test_terminal_sibling_requires_refreshed_version_and_valid_candidate_shape(
    tmp_path: Path,
) -> None:
    db_path = _migrated_budget_db(tmp_path, name="terminal-guard.sqlite3")
    _charge_with_bounded_refresh(
        str(db_path),
        "budget-usage://terminal-guard/winner",
        2,
    )
    baseline = _sql_budget_truth(db_path)
    sibling_usage = "budget-usage://terminal-guard/sibling"
    store = DurableTaskStore(db_path, clock=lambda: NOW)

    with pytest.raises(TaskStoreError) as stale:
        store.consume_budget_once(
            BUDGET_ID,
            usage_id=sibling_usage,
            charge_kind="WORKER_USAGE",
            graph_id=GRAPH_ID,
            graph_revision=1,
            node_id=NODE_ID,
            attempt_id=ATTEMPT_ID,
            delta=BudgetAmount(token_count=3),
            expected_version=0,
            outbox=_budget_candidate(
                usage_id=sibling_usage,
                aggregate_version=1,
                event_type="BUDGET_EXCEEDED",
            ),
        )
    assert stale.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert _sql_budget_truth(db_path) == baseline

    malformed = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        2,
        "BUDGET_EXCEEDED",
        attempt_id=ATTEMPT_ID,
        # Missing the exact usage evidence binding.
        discriminator="malformed-terminal-sibling",
    )
    with pytest.raises(TaskStoreError) as invalid:
        store.consume_budget_once(
            BUDGET_ID,
            usage_id=sibling_usage,
            charge_kind="WORKER_USAGE",
            graph_id=GRAPH_ID,
            graph_revision=1,
            node_id=NODE_ID,
            attempt_id=ATTEMPT_ID,
            delta=BudgetAmount(token_count=3),
            expected_version=1,
            outbox=malformed,
        )
    assert invalid.value.reason_code == TASK_STORE_INVALID
    assert _sql_budget_truth(db_path) == baseline

    settled = store.consume_budget_once(
        BUDGET_ID,
        usage_id=sibling_usage,
        charge_kind="WORKER_USAGE",
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id=NODE_ID,
        attempt_id=ATTEMPT_ID,
        delta=BudgetAmount(token_count=3),
        expected_version=1,
        outbox=_budget_candidate(
            usage_id=sibling_usage,
            aggregate_version=2,
            event_type="BUDGET_EXCEEDED",
        ),
    )
    assert settled.reason_code == TASK_STORE_BUDGET_ALREADY_EXCEEDED


def test_cross_process_terminal_siblings_share_one_winner_outbox(tmp_path: Path) -> None:
    db_path = _migrated_budget_db(tmp_path, name="cross-process.sqlite3")
    specs = tuple(
        (str(db_path), f"budget-usage://process/{index}", 2 + index)
        for index in range(8)
    )
    with ProcessPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(_process_charge, specs))
    assert len(outcomes) == 8
    assert max(outcome["calls"] for outcome in outcomes) <= 2
    assert [outcome["reason_code"] for outcome in outcomes].count(
        TASK_STORE_BUDGET_EXCEEDED
    ) == 1
    assert [outcome["reason_code"] for outcome in outcomes].count(
        TASK_STORE_BUDGET_ALREADY_EXCEEDED
    ) == 7
    _, usages, outboxes = _sql_budget_truth(db_path)
    winner = next(row for row in usages if row["reason_code"] == TASK_STORE_BUDGET_EXCEEDED)
    assert all(
        row["outbox_id"] == winner["outbox_id"]
        for row in usages
        if row["reason_code"] == TASK_STORE_BUDGET_ALREADY_EXCEEDED
    )
    assert len([row for row in outboxes if row["event_type"] == "BUDGET_EXCEEDED"]) == 1


def _process_charge(spec: tuple[str, str, int]) -> dict[str, Any]:
    db_path, usage_id, token_count = spec
    return _charge_with_bounded_refresh(db_path, usage_id, token_count)
