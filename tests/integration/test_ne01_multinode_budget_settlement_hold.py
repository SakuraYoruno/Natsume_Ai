from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore
from host_brain.task_runtime import AdmittedExecution
from tests.integration.test_gr03a_governed_durable_dag import (
    _migrated_db,
)
from tests.integration.test_ne01_operation_online_takeover import (
    _Clock,
    _ProjectedWorker,
    _coordinator,
    _contracts,
    _events,
)


class _SelectedBudgetSettlementDoubleLossStore:
    """Commit one worker usage, then lose its response and first exact read."""

    def __init__(
        self,
        delegate: DurableTaskStore,
        *,
        node_id: str,
    ) -> None:
        self._delegate = delegate
        self._node_id = node_id
        self.consume_failed = False
        self.read_failed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def consume_budget_once(self, *args: Any, **kwargs: Any) -> Any:
        if (
            not self.consume_failed
            and str(kwargs.get("node_id") or "") == self._node_id
            and str(kwargs.get("charge_kind") or "").startswith("WORKER_USAGE")
        ):
            self.consume_failed = True
            self._delegate.consume_budget_once(*args, **kwargs)
            raise TimeoutError("simulated worker BudgetUsage response loss")
        return self._delegate.consume_budget_once(*args, **kwargs)

    def get_budget_usage(self, *args: Any, **kwargs: Any) -> Any:
        if self.consume_failed and not self.read_failed:
            self.read_failed = True
            raise TimeoutError("simulated first exact BudgetUsage read-back loss")
        return self._delegate.get_budget_usage(*args, **kwargs)


_FORBIDDEN_HOLD_OUTBOX_EVENTS = frozenset(
    {
        "TASK_ATTEMPT_SUCCEEDED",
        "TASK_ATTEMPT_FAILED",
        "TASK_ATTEMPT_CANCELLED",
        "TASK_NODE_EXECUTED",
        "TASK_NODE_VERIFYING",
        "TASK_NODE_VERIFIED",
        "TASK_NODE_ACCEPTED",
        "TASK_NODE_FAILED",
        "TASK_NODE_CANCELLED",
        "TASK_GRAPH_COMPLETED",
        "TASK_GRAPH_FAILED",
        "TASK_GRAPH_BLOCKED",
        "TASK_GRAPH_CANCELLED",
        "TASK_LEASE_RELEASED",
        "TASK_LEASE_RELEASED_TERMINAL",
        "TASK_LEASE_RELEASED_RECOVERY",
        "BUDGET_RESERVATION_CONSUMED",
        "BUDGET_RESERVATION_RELEASED",
    }
)


def _forbidden_hold_outbox_events(store: DurableTaskStore) -> set[str]:
    with store._connect() as conn:
        persisted = {
            str(row[0])
            for row in conn.execute(
                "SELECT event_type FROM task_runtime_outbox"
            ).fetchall()
        }
    # BUDGET_CONSUMED is deliberately allowed: the exact immutable usage row
    # is the write whose response became ambiguous; it is not a lifecycle
    # terminal transition.
    return persisted & _FORBIDDEN_HOLD_OUTBOX_EVENTS


@pytest.mark.parametrize(
    ("token_limit", "expected_budget_status", "usage_accepted"),
    (
        (100, "ACTIVE", True),
        (5, "EXCEEDED", False),
    ),
)
@pytest.mark.asyncio
async def test_multinode_post_commit_budget_ambiguity_stays_batch_recovery_pending(
    tmp_path: Path,
    token_limit: int,
    expected_budget_status: str,
    usage_accepted: bool,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("root-a", ()), ("root-b", ())),
        concurrency=2,
        token_limit=token_limit,
    )
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    root_a = admitted.for_node("root-a")
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    durable = DurableTaskStore(db_path, clock=clock)
    uncertain_store = _SelectedBudgetSettlementDoubleLossStore(
        durable,
        node_id="root-a",
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=uncertain_store,
            projections=projections,
            worker=worker,
            owner_id="batch-budget-owner-a",
            bounded_fanout_enabled=True,
        ),
    )

    first = await _events(first_app, contracts)
    first_receipt = first[-1]["execution_receipt"]

    assert first_receipt["status"] == "RECOVERY_REQUIRED"
    assert first_receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert first_receipt["projection"]["budget_settlement_state"] == "UNKNOWN"
    assert first_receipt["projection"]["operation_status"] == "COMMITTED"
    assert first_receipt["projection"]["worker_replayed"] is False
    assert uncertain_store.consume_failed is True
    assert uncertain_store.read_failed is True
    assert calls == ["root-a"]

    held = durable.read_scheduler_snapshot("graph-gr03a", 1)
    original_attempt = held["attempts"][0]
    original_lease = held["active_leases"][0]
    original_operation = durable.get_operation_reservation(
        root_a.reservation_id
    )
    worker_usages = [
        row
        for row in held["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]

    assert original_operation["status"] == "COMMITTED"
    assert held["graph_record"]["status"] == "ACTIVE"
    assert durable.get_node("graph-gr03a", 1, "root-a")["status"] == "RUNNING"
    assert durable.get_node("graph-gr03a", 1, "root-b")["status"] == "READY"
    assert original_attempt["node_id"] == "root-a"
    assert original_attempt["status"] == "RUNNING"
    assert original_lease["owner_id"] == "batch-budget-owner-a"
    assert held["budgets"][0]["status"] == expected_budget_status
    assert len(worker_usages) == 1
    assert worker_usages[0]["usage_id"] == "worker-usage://root-a/online-takeover"
    assert worker_usages[0]["attempt_id"] == original_attempt["attempt_id"]
    assert worker_usages[0]["accepted"] is usage_accepted
    assert _forbidden_hold_outbox_events(durable) == set()

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=reopened,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="batch-budget-owner-b",
            bounded_fanout_enabled=True,
        ),
    )

    second = await _events(second_app, contracts)
    second_receipt = second[-1]["execution_receipt"]

    assert second_receipt["status"] == "RECOVERY_REQUIRED"
    assert second_receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert second_receipt["projection"]["online_takeover_state"] == (
        "BATCH_RECOVERY_PENDING"
    )
    assert second_receipt["projection"]["worker_replayed"] is False
    assert calls == ["root-a"]

    final = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    final_operation = reopened.get_operation_reservation(root_a.reservation_id)
    final_worker_usages = [
        row
        for row in final["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]
    with reopened._connect() as conn:
        owner_b_leases = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_lease WHERE owner_id = ?",
            ("batch-budget-owner-b",),
        ).fetchone()[0]
        operation_commits = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox "
            "WHERE event_type = 'OPERATION_COMMITTED'"
        ).fetchone()[0]

    assert len(final["attempts"]) == 1
    assert final["attempts"][0] == original_attempt
    assert final["active_leases"][0] == original_lease
    assert final_operation == original_operation
    assert final_worker_usages == worker_usages
    assert final["graph_record"]["status"] == "ACTIVE"
    assert reopened.get_node("graph-gr03a", 1, "root-a")["status"] == "RUNNING"
    assert reopened.get_node("graph-gr03a", 1, "root-b")["status"] == "READY"
    assert final["budgets"][0]["status"] == expected_budget_status
    assert owner_b_leases == 0
    assert operation_commits == 1
    assert _forbidden_hold_outbox_events(reopened) == set()
