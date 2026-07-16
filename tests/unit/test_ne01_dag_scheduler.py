from __future__ import annotations

from dataclasses import replace

import pytest

from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.task_graph import (
    TASK_NODE_SCHEMA_VERSION,
    TaskNode,
    TaskNodeStatus,
    normalize_task_retry_policy,
)
from host_brain.task_runtime.dag_scheduler import (
    DagNodeScheduleCandidate,
    DagScheduleInputError,
    compute_dag_schedule,
)


def _candidate(
    node_id: str,
    status: TaskNodeStatus,
    *,
    dependencies: tuple[str, ...] = (),
    retry_not_before_ms: int | None = None,
) -> DagNodeScheduleCandidate:
    return DagNodeScheduleCandidate(
        node_id=node_id,
        dependencies=dependencies,
        status=status,
        retry_not_before_ms=retry_not_before_ms,
    )


def test_b0_fan_out_selection_is_stable_and_fan_in_waits_for_all_dependencies() -> None:
    candidates = (
        _candidate(
            "join",
            TaskNodeStatus.BLOCKED,
            dependencies=("left", "right"),
        ),
        _candidate("right", TaskNodeStatus.BLOCKED, dependencies=("root",)),
        _candidate("root", TaskNodeStatus.ACCEPTED),
        _candidate("left", TaskNodeStatus.BLOCKED, dependencies=("root",)),
    )
    snapshot_before = tuple(candidates)

    decision = compute_dag_schedule(
        candidates,
        now_ms=1_000,
        concurrency_limit=2,
    )
    reordered = compute_dag_schedule(
        tuple(reversed(candidates)),
        now_ms=1_000,
        concurrency_limit=2,
    )

    assert decision == reordered
    assert candidates == snapshot_before
    assert decision.dependency_ready_node_ids == ("left", "right")
    assert decision.transition_to_ready_node_ids == ("left", "right")
    assert decision.dependency_blocked_node_ids == ("join",)
    assert decision.selected_node_ids == ("left", "right")


def test_b0_fan_in_becomes_eligible_only_after_every_predecessor_is_accepted() -> None:
    candidates = (
        _candidate("left", TaskNodeStatus.ACCEPTED),
        _candidate("right", TaskNodeStatus.RUNNING),
        _candidate(
            "join",
            TaskNodeStatus.BLOCKED,
            dependencies=("left", "right"),
        ),
    )

    waiting = compute_dag_schedule(
        candidates,
        now_ms=1_000,
        concurrency_limit=1,
    )
    ready = compute_dag_schedule(
        tuple(
            replace(candidate, status=TaskNodeStatus.ACCEPTED)
            if candidate.node_id == "right"
            else candidate
            for candidate in candidates
        ),
        now_ms=1_000,
        concurrency_limit=1,
    )

    assert waiting.selected_node_ids == ()
    assert waiting.dependency_blocked_node_ids == ("join",)
    assert ready.dependency_ready_node_ids == ("join",)
    assert ready.selected_node_ids == ("join",)


def test_b0_ready_snapshot_with_unaccepted_dependency_fails_closed() -> None:
    decision = compute_dag_schedule(
        (
            _candidate("root", TaskNodeStatus.RUNNING),
            _candidate("child", TaskNodeStatus.READY, dependencies=("root",)),
        ),
        now_ms=10,
        concurrency_limit=4,
    )

    assert decision.ready_node_ids == ()
    assert decision.selected_node_ids == ()
    assert decision.dependency_blocked_node_ids == ("child",)


def test_b0_concurrency_limit_accounts_for_occupied_slots_and_defers_stably() -> None:
    decision = compute_dag_schedule(
        (
            _candidate("zeta", TaskNodeStatus.READY),
            _candidate("alpha", TaskNodeStatus.READY),
            _candidate("beta", TaskNodeStatus.READY),
        ),
        now_ms=10,
        concurrency_limit=2,
        occupied_slots=1,
    )

    assert decision.available_slots == 1
    assert decision.eligible_node_ids == ("alpha", "beta", "zeta")
    assert decision.selected_node_ids == ("alpha",)
    assert decision.deferred_node_ids == ("beta", "zeta")


def test_b0_retry_due_is_eligible_while_future_retry_remains_waiting() -> None:
    decision = compute_dag_schedule(
        (
            _candidate("root", TaskNodeStatus.ACCEPTED),
            _candidate(
                "retry-due",
                TaskNodeStatus.RETRY_PENDING,
                dependencies=("root",),
                retry_not_before_ms=1_000,
            ),
            _candidate(
                "retry-later",
                TaskNodeStatus.RETRY_PENDING,
                dependencies=("root",),
                retry_not_before_ms=1_001,
            ),
        ),
        now_ms=1_000,
        concurrency_limit=1,
    )

    assert decision.retry_due_node_ids == ("retry-due",)
    assert decision.retry_waiting_node_ids == ("retry-later",)
    assert decision.transition_to_ready_node_ids == ("retry-due",)
    assert decision.selected_node_ids == ("retry-due",)


def test_b0_retry_pending_requires_an_explicit_due_timestamp() -> None:
    with pytest.raises(DagScheduleInputError, match="requires retry_not_before_ms"):
        compute_dag_schedule(
            (_candidate("retry", TaskNodeStatus.RETRY_PENDING),),
            now_ms=1_000,
            concurrency_limit=1,
        )


def test_b0_unknown_dependency_snapshot_fails_closed() -> None:
    with pytest.raises(DagScheduleInputError, match="unknown dependencies: missing"):
        compute_dag_schedule(
            (
                _candidate(
                    "child",
                    TaskNodeStatus.READY,
                    dependencies=("missing",),
                ),
            ),
            now_ms=1_000,
            concurrency_limit=1,
        )


@pytest.mark.parametrize(
    ("concurrency_limit", "occupied_slots"),
    [(0, 0), (True, 0), (1, -1), (1, True)],
)
def test_b0_invalid_concurrency_inputs_fail_closed(
    concurrency_limit: int,
    occupied_slots: int,
) -> None:
    with pytest.raises(DagScheduleInputError):
        compute_dag_schedule(
            (_candidate("root", TaskNodeStatus.READY),),
            now_ms=1_000,
            concurrency_limit=concurrency_limit,
            occupied_slots=occupied_slots,
        )


def test_b0_retry_policy_canonicalizes_only_the_zero_backoff_default() -> None:
    payload = {
        "schema_version": TASK_NODE_SCHEMA_VERSION,
        "node_id": "node-1",
        "dependencies": [],
        "route": "skill:test",
        "input_refs": [],
        "output_contract": {"type": "result"},
        "retry_policy": {"max_attempts": 2},
        "risk_level": 1,
        "acceptance_criteria": ["accepted"],
        "status": "READY",
        "status_version": 0,
    }

    node = TaskNode.from_mapping(payload)

    assert node.retry_policy == {"max_attempts": 2, "retry_backoff_ms": 0}
    assert node.to_dict()["retry_policy"] == {
        "max_attempts": 2,
        "retry_backoff_ms": 0,
    }


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {"retry_backoff_ms": 10},
        {"max_attempts": 2, "backoff_ms": 10},
        {"max_attempts": 2, "retry_limit": 1},
        {"max_attempts": 0},
        {"max_attempts": True},
        {"max_attempts": "2"},
        {"max_attempts": 2, "retry_backoff_ms": -1},
        {"max_attempts": 2, "retry_backoff_ms": True},
        {"max_attempts": 2, "retry_backoff_ms": "10"},
    ],
)
def test_b0_retry_policy_rejects_aliases_unknown_fields_and_non_integer_bounds(
    policy: dict[str, object],
) -> None:
    with pytest.raises(ContractViolation):
        normalize_task_retry_policy(policy)
