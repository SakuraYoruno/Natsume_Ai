from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.node_budget_reservation import (
    NodeBudgetReservationV1,
)
from host_brain.persistence import task_repository
from host_brain.persistence.task_node_budget_store import (
    TaskNodeBudgetStoreMixin,
)


def _record() -> dict[str, object]:
    amount = {
        "token_count": 10,
        "tool_call_count": 2,
        "time_ms": 100,
        "concurrency": 1,
        "retry_count": 1,
        "model_cost_micros": 20,
    }
    zero = {key: 0 for key in amount}
    return {
        "schema_version": "Natsume.task_runtime.node_budget_reservation.v1",
        "node_budget_id": "node-budget::contract",
        "parent_budget_id": "budget::graph",
        "graph_id": "graph::contract",
        "graph_revision": 1,
        "node_id": "node-a",
        "attempt_id": "attempt::node-a",
        "scope": "work-order:contract",
        "limits": amount,
        "reserved": amount,
        "consumed": zero,
        "attempted_consumed": zero,
        "usage_version": 0,
        "parent_version_at_claim": 0,
        "claim_origin": "ATOMIC_READY_CLAIM",
        "attempt_status": "LEASED",
        "status": "ACTIVE",
    }


def test_node_budget_contract_is_attempt_and_parent_bound() -> None:
    reservation = NodeBudgetReservationV1.from_mapping(_record())
    assert reservation.attempt_id == "attempt::node-a"
    assert reservation.parent_budget_id == "budget::graph"
    assert reservation.to_dict()["remaining"]["token_count"] == 10

    drifted = _record()
    drifted["reserved"] = {
        **dict(drifted["reserved"]),
        "token_count": 11,
    }
    with pytest.raises(ContractViolation):
        NodeBudgetReservationV1.from_mapping(drifted)


def test_node_budget_mixin_is_one_store_authority_without_second_uow() -> None:
    assert task_repository.DurableTaskStore.__bases__[-4] is (
        TaskNodeBudgetStoreMixin
    )
    for name in (
        "assert_node_budget_ready",
        "get_node_budget_reservation",
        "get_node_budget_usage",
    ):
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskNodeBudgetStoreMixin,
            name,
        )
    source = inspect.getsource(TaskNodeBudgetStoreMixin)
    assert "def _transaction" not in source
    assert len(inspect.getsource(inspect.getmodule(TaskNodeBudgetStoreMixin)).splitlines()) < 700


def test_0132_schema_has_atomic_lineage_and_immutability_guards() -> None:
    sql = Path(
        "host_brain/audit/migrations/0132_task_node_budget_reservation.sql"
    ).read_text(encoding="utf-8")
    for required in (
        "task_runtime_node_budget_reservation",
        "task_runtime_node_budget_usage",
        "UNIQUE(parent_budget_id, graph_id, graph_revision, node_id, attempt_id)",
        "FOREIGN KEY(parent_budget_id, usage_id)",
        "trg_task_runtime_node_budget_identity_update",
        "trg_task_runtime_node_budget_usage_no_update",
        "BACKFILL_0132",
    ):
        assert required in sql
