from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetExceededAction,
    BudgetPolicy,
)
from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.review_continuation_binding import (
    REVIEW_CONTINUATION_CONFLICT,
)
from host_brain.persistence.review_repository import ReviewStore, ReviewStoreError
from host_brain.task_runtime.review_runtime import (
    TASK_NODE_REVIEW_INVALID,
    TaskNodeReviewContext,
    TaskNodeReviewError,
    TaskNodeReviewRuntime,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "host_brain" / "audit" / "migrations" / "0055_agent_runtime_review_store.sql"


class Clock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _store(tmp_path: Path, clock: Clock) -> ReviewStore:
    path = tmp_path / "review.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    return ReviewStore(path, clock=clock)


def _context(*, attempt_id: str | None = None) -> TaskNodeReviewContext:
    return TaskNodeReviewContext(
        work_order_id="wo-root",
        root_work_order_id="wo-root",
        graph_id="graph/alpha",
        graph_revision=3,
        node_id="node:write",
        node_status_version=7,
        attempt_id=attempt_id,
        requester="governed-task-runtime",
        reason="owner acceptance is required",
        risk_level=4,
        side_effects=("write_files",),
        evidence_refs=("taskreceipt://graph/3/node/attempt",),
        diff_ref="diff://workspace/change-1",
    )


def _work_order(*, review_required: bool, waiver: bool = False) -> WorkOrderV3:
    policy = {"review_required": review_required, "durable_review_required": review_required}
    if waiver:
        policy.update(
            {
                "mode": "governor_grant_review_waiver",
                "durable_review_required": False,
            }
        )
    order = WorkOrderV3(
        work_order_id="wo-root",
        root_work_order_id="wo-root",
        domain="code",
        run_mode="worker",
        objective="write one file",
        task_graph_id="graph/alpha",
        task_node_id="node:write",
        graph_revision=3,
        approval_policy={"required": True},
        review_policy=policy,
        budget_policy=BudgetPolicy(
            scope="work-order:wo-root",
            limits=BudgetAmount(tool_call_count=1),
            on_exceeded=BudgetExceededAction.BLOCK,
        ),
    )
    order.validate()
    return order


def test_pre_execution_review_is_durable_idempotent_and_token_bound(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    runtime = TaskNodeReviewRuntime(store, clock=clock)
    context = _context()

    first = runtime.pre_execution_gate(
        work_order=_work_order(review_required=True),
        context=context,
    )
    replay = runtime.pre_execution_gate(
        work_order=_work_order(review_required=True),
        context=context,
    )

    assert first == replay
    assert first.disposition == "WAITING_REVIEW"
    assert first.allowed is False
    assert first.replay_side_effects is False
    assert len(store.list_reviews(task_graph_id="graph/alpha")) == 1

    approved = store.approve_review(
        first.review_id or "",
        actor_id="owner",
        expected_subject_ref=first.subject_ref,
        expected_subject_version=first.subject_version,
        expected_resume_phase=ReviewResumePhase.PRE_EXECUTION,
        expected_checkpoint_id=first.checkpoint_id,
    )
    store.resume_review(
        first.review_id or "",
        resume_token_ref=approved["resume_token_ref"],
        subject_ref=first.subject_ref or "",
        subject_version=first.subject_version or 0,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
        checkpoint_id=first.checkpoint_id or "",
    )

    resumed = runtime.pre_execution_gate(
        work_order=_work_order(review_required=True),
        context=context,
    )
    assert resumed.disposition == "RESUME_FROM_CHECKPOINT"
    assert resumed.allowed is True
    assert resumed.replay_side_effects is False
    assert runtime.lookup_pre_execution_gate(context=context) == resumed

    missing = runtime.lookup_pre_execution_gate(
        context=replace(context, node_status_version=context.node_status_version + 1)
    )
    assert missing is None
    assert len(store.list_reviews(task_graph_id="graph/alpha")) == 1


def test_post_acceptance_review_binds_attempt_and_never_replays_worker(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    runtime = TaskNodeReviewRuntime(store, clock=clock)
    context = _context(attempt_id="attempt-9")

    waiting = runtime.post_execution_acceptance_gate(context=context)
    assert waiting.disposition == "WAITING_REVIEW"
    assert "attempt=attempt-9" in (waiting.subject_ref or "")

    approved = store.approve_review(waiting.review_id or "", actor_id="owner")
    resume = store.resume_review(
        waiting.review_id or "",
        resume_token_ref=approved["resume_token_ref"],
        subject_ref=waiting.subject_ref or "",
        subject_version=waiting.subject_version or 0,
        resume_phase=ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
        checkpoint_id=waiting.checkpoint_id or "",
    )
    assert resume["resume_action"] == "RETURN_POST_EXECUTION_CHECKPOINT"
    assert resume["replay_side_effects"] is False

    resumed = runtime.post_execution_acceptance_gate(context=context)
    assert resumed.allowed is True
    assert resumed.disposition == "RESUME_FROM_CHECKPOINT"
    assert resumed.replay_side_effects is False


def test_explicit_governor_waiver_is_not_materialized_as_review(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    runtime = TaskNodeReviewRuntime(store, clock=clock)

    gate = runtime.pre_execution_gate(
        work_order=_work_order(review_required=False, waiver=True),
        context=_context(),
    )

    assert gate.disposition == "NOT_REQUIRED"
    assert gate.allowed is True
    assert gate.review_id is None
    assert store.list_reviews(task_graph_id="graph/alpha") == []


def test_inconsistent_waiver_and_review_requirement_fails_closed(tmp_path: Path) -> None:
    clock = Clock()
    runtime = TaskNodeReviewRuntime(_store(tmp_path, clock), clock=clock)
    order = _work_order(review_required=True)
    order.review_policy["mode"] = "governor_grant_review_waiver"

    with pytest.raises(TaskNodeReviewError) as raised:
        runtime.pre_execution_gate(work_order=order, context=_context())

    assert raised.value.reason_code == TASK_NODE_REVIEW_INVALID


def test_forged_existing_review_binding_is_rejected_at_persistence_boundary(
    tmp_path: Path,
) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    runtime = TaskNodeReviewRuntime(store, clock=clock)
    context = _context(attempt_id="attempt-9")
    review_id = runtime.review_id(
        context,
        review_type=ReviewType.POST_EXECUTION_ACCEPTANCE,
    )
    forged = ReviewItem(
        review_id=review_id,
        review_type=ReviewType.POST_EXECUTION_ACCEPTANCE,
        subject_ref="task-node://forged/3/node",
        subject_version=7,
        requester=context.requester,
        reason=context.reason,
        risk_level=context.risk_level,
        side_effects=context.side_effects,
        evidence_refs=context.evidence_refs,
        status=ReviewStatus.PENDING,
        expires_at=100_000,
        resume_phase=ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
        diff_ref=context.diff_ref,
        checkpoint_id="task-checkpoint://forged/3/node/post",
    )
    with pytest.raises(ReviewStoreError) as raised:
        store.create_review(
            forged,
            work_order_id=context.work_order_id,
            task_graph_id=context.graph_id,
            task_node_id=context.node_id,
        )

    assert raised.value.reason_code == REVIEW_CONTINUATION_CONFLICT


def test_post_acceptance_review_requires_attempt_id(tmp_path: Path) -> None:
    clock = Clock()
    runtime = TaskNodeReviewRuntime(_store(tmp_path, clock), clock=clock)

    with pytest.raises(TaskNodeReviewError) as raised:
        runtime.post_execution_acceptance_gate(context=_context())

    assert raised.value.reason_code == TASK_NODE_REVIEW_INVALID
