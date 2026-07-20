from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from host_brain.task_runtime.admission import AdmittedExecution, AdmittedTaskNode
from host_brain.task_runtime.errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)
from host_brain.task_runtime.node_execution_projection import NodeExecutionProjection
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)


ATTEMPT_ID = "attempt-checkpoint-budget-1"


def _admitted() -> AdmittedExecution:
    return AdmittedExecution(
        command_id="command-checkpoint-budget-1",
        work_order_id="work-order-checkpoint-budget-1",
        assignment_id="assignment-checkpoint-budget-1",
        executor="canonical",
        graph_id="graph-checkpoint-budget-1",
        graph_revision=3,
        nodes=(
            AdmittedTaskNode(
                node_id="node-checkpoint-budget-1",
                worker_route="skill:checkpoint-budget",
                dependencies=(),
                acceptance_criteria=("verified",),
                operation_fingerprint="fingerprint-checkpoint-budget-1",
                reservation_id="reservation-checkpoint-budget-1",
            ),
        ),
        business_scope="scope-checkpoint-budget-1",
    )


def _graph() -> dict[str, Any]:
    return {
        "graph_id": "graph-checkpoint-budget-1",
        "revision": 3,
        "status": "BLOCKED",
        "status_version": 7,
    }


def _node() -> dict[str, Any]:
    return {
        "graph_id": "graph-checkpoint-budget-1",
        "graph_revision": 3,
        "node_id": "node-checkpoint-budget-1",
        "status": "FAILED",
        "status_version": 5,
        "evidence_refs": ["evidence://node-checkpoint-budget"],
    }


def _attempt() -> dict[str, Any]:
    return {
        "graph_id": "graph-checkpoint-budget-1",
        "graph_revision": 3,
        "node_id": "node-checkpoint-budget-1",
        "attempt_id": ATTEMPT_ID,
        "attempt_no": 1,
        "status": "FAILED",
        "status_version": 6,
        "error_ref": "taskerror://checkpoint-budget-exceeded",
        "evidence_refs": ["evidence://attempt-checkpoint-budget"],
    }


def _terminal(*, authority_kind: str) -> dict[str, Any]:
    return {
        "authority_kind": authority_kind,
        "graph": _graph(),
        "node": _node(),
        "attempt": _attempt(),
        "budget": {
            "budget_id": "budget-checkpoint-budget-1",
            "graph_id": "graph-checkpoint-budget-1",
            "graph_revision": 3,
            "node_id": None,
            "attempt_id": None,
            "status": "RELEASED",
            "status_version": 4,
        },
        "reservation": {
            "reservation_id": "reservation-checkpoint-budget-1",
            "status": "COMMITTED",
            "receipt_ref": "worker-projection://checkpoint-budget-1",
        },
        "usage": {
            "usage_id": "budget-usage://checkpoint-budget-1",
            "budget_id": "budget-checkpoint-budget-1",
            "graph_id": "graph-checkpoint-budget-1",
            "graph_revision": 3,
            "node_id": "node-checkpoint-budget-1",
            "attempt_id": ATTEMPT_ID,
            "accepted": False,
            "reason_code": "TASK_STORE_BUDGET_EXCEEDED",
            "exceeded_dimensions": ["token_count"],
            "outbox_id": "outbox::budget-exceeded-checkpoint-budget-1",
        },
    }


class _ProjectionStore:
    def __init__(self, terminal: Any) -> None:
        self.terminal = terminal
        self.resolve_calls: list[str] = []

    def get_operation_reservation(self, reservation_id: str) -> dict[str, Any]:
        assert reservation_id == "reservation-checkpoint-budget-1"
        return {
            "reservation_id": reservation_id,
            "status": "COMMITTED",
            "receipt_ref": "worker-projection://checkpoint-budget-1",
        }

    def read_scheduler_snapshot(self, graph_id: str, revision: int) -> dict[str, Any]:
        assert (graph_id, revision) == ("graph-checkpoint-budget-1", 3)
        return {"attempts": [_attempt()]}

    def try_resolve_checkpoint_budget_rejection_terminal(
        self,
        attempt_id: str,
    ) -> Any:
        self.resolve_calls.append(attempt_id)
        if isinstance(self.terminal, BaseException):
            raise self.terminal
        return deepcopy(self.terminal)

    def get_latest_parallel_settlement_decision(self, *_: Any) -> None:
        raise AssertionError("single-node checkpoint replay cannot use parallel authority")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authority_kind",
    (
        "INTERNAL_AGENT_BUDGET_REJECTED",
        "CANONICAL_CHECKPOINT_BUDGET_REJECTED",
    ),
)
async def test_exact_checkpoint_budget_terminal_replays_blocked_usage_and_error(
    authority_kind: str,
) -> None:
    store = _ProjectionStore(_terminal(authority_kind=authority_kind))
    projection = NodeExecutionProjection(task_store=store, projection_reader=None)

    receipt = await projection.terminal_replay(
        _admitted(),
        graph=_graph(),
        node=_node(),
    )

    assert receipt is not None
    assert receipt.status == "BUDGET_BLOCKED"
    assert receipt.reason_code == "GOVERNED_BUDGET_EXCEEDED"
    assert receipt.verification_status == "NOT_RUN"
    assert receipt.accepted is False
    assert receipt.replayed is True
    assert receipt.attempt_id == ATTEMPT_ID
    assert receipt.projection["budget_usage"]["usage_id"] == (
        "budget-usage://checkpoint-budget-1"
    )
    assert receipt.projection["error_ref"] == (
        "taskerror://checkpoint-budget-exceeded"
    )
    assert receipt.projection["checkpoint_budget_rejection"] == {
        "authority_kind": authority_kind,
        "reservation_id": "reservation-checkpoint-budget-1",
        "usage_id": "budget-usage://checkpoint-budget-1",
    }
    assert set(receipt.evidence_refs) >= {
        "evidence://node-checkpoint-budget",
        "evidence://attempt-checkpoint-budget",
        "taskerror://checkpoint-budget-exceeded",
        "budget-usage://checkpoint-budget-1",
        "outbox::budget-exceeded-checkpoint-budget-1",
    }
    assert store.resolve_calls == [ATTEMPT_ID]


@pytest.mark.asyncio
async def test_missing_checkpoint_budget_authority_falls_back_to_failed_replay() -> None:
    store = _ProjectionStore(None)
    projection = NodeExecutionProjection(task_store=store, projection_reader=None)

    receipt = await projection.terminal_replay(
        _admitted(),
        graph=_graph(),
        node=_node(),
    )

    assert receipt is not None
    assert receipt.status == "FAILED"
    assert receipt.reason_code == "GOVERNED_REPLAY_FAILED"
    assert receipt.accepted is False
    assert receipt.replayed is True


@pytest.mark.asyncio
async def test_checkpoint_budget_terminal_identity_drift_fails_closed() -> None:
    terminal = _terminal(authority_kind="CANONICAL_CHECKPOINT_BUDGET_REJECTED")
    terminal["usage"]["node_id"] = "node-foreign"
    projection = NodeExecutionProjection(
        task_store=_ProjectionStore(terminal),
        projection_reader=None,
    )

    with pytest.raises(GovernedRuntimeError) as exc_info:
        await projection.terminal_replay(
            _admitted(),
            graph=_graph(),
            node=_node(),
        )

    assert exc_info.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT


@pytest.mark.asyncio
async def test_checkpoint_budget_terminal_durable_corruption_is_not_swallowed() -> None:
    corruption = RuntimeError("durable checkpoint budget terminal is corrupt")
    projection = NodeExecutionProjection(
        task_store=_ProjectionStore(corruption),
        projection_reader=None,
    )

    with pytest.raises(RuntimeError, match="durable checkpoint budget terminal is corrupt"):
        await projection.terminal_replay(
            _admitted(),
            graph=_graph(),
            node=_node(),
        )


@pytest.mark.asyncio
async def test_extra_release_outbox_conflict_fails_closed_in_node_replay() -> None:
    corruption = TaskStoreError(
        TASK_STORE_DURABLE_DATA_INVALID,
        "checkpoint budget terminal Outbox authority is ambiguous",
    )
    projection = NodeExecutionProjection(
        task_store=_ProjectionStore(corruption),
        projection_reader=None,
    )

    with pytest.raises(TaskStoreError) as exc_info:
        await projection.terminal_replay(
            _admitted(),
            graph=_graph(),
            node=_node(),
        )

    assert exc_info.value is corruption
