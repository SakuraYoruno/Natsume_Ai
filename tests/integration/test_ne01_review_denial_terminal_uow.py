from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.review_policy import ReviewResumePhase
from host_brain.contracts.agent_runtime.task_graph import TaskNodeStatus
from host_brain.contracts.agent_runtime.verification_contract import (
    VerificationStatus,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_review_denial_common import (
    build_review_denial_settlement_ref,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_CAS_CONFLICT,
    TaskStoreError,
)
from host_brain.task_runtime.review_runtime import (
    TaskNodeReviewContext,
    TaskNodeReviewRuntime,
)
from tests.integration.test_ne01_durable_acceptance_review import (
    _review_judgement,
)
from tests.integration.test_ne01_durable_verification_terminal import (
    ATTEMPT_ID,
    BUDGET_ID,
    GRAPH_ID,
    NODE_ID,
    NOW,
    RECEIPT_REF,
    _event,
    _prepared_store,
    _verification,
    _verification_outbox,
)


class _FailGraphSettlementStore(DurableTaskStore):
    def _insert_outbox(self, conn: Any, outbox: Any, **kwargs: Any) -> None:
        super()._insert_outbox(conn, outbox, **kwargs)
        if outbox.event_type == "TASK_GRAPH_FAILED":
            raise RuntimeError("fault after terminal Graph Outbox insert")


def _waiting_post_review(
    tmp_path: Path,
) -> tuple[DurableTaskStore, ReviewStore, dict[str, Any]]:
    prepared = _prepared_store(tmp_path, name="review-denial-terminal.sqlite3")
    prepared.commit_node_verification(
        GRAPH_ID,
        1,
        NODE_ID,
        attempt_id=ATTEMPT_ID,
        reservation_id="operation::durable-verification",
        expected_node_version=4,
        target_status=TaskNodeStatus.VERIFIED,
        verification_result=_verification(status=VerificationStatus.PASS),
        node_outbox=_verification_outbox(status=TaskNodeStatus.VERIFIED),
    )
    review_store = ReviewStore(prepared.db_path, clock=lambda: NOW)
    review_runtime = TaskNodeReviewRuntime(review_store, clock=lambda: NOW)
    review_item = review_runtime.prepare_post_execution_acceptance_review(
        context=TaskNodeReviewContext(
            work_order_id="work-order::durable-verification",
            root_work_order_id="work-order::durable-verification",
            graph_id=GRAPH_ID,
            graph_revision=1,
            node_id=NODE_ID,
            node_status_version=6,
            attempt_id=ATTEMPT_ID,
            requester="principal::review-denial",
            reason="Acceptance policy requires durable post-execution review",
            risk_level=1,
            evidence_refs=(RECEIPT_REF,),
        )
    )
    node_outbox = _event(
        "TASK_NODE",
        f"{GRAPH_ID}@1/{NODE_ID}",
        6,
        "TASK_NODE_WAITING_ACCEPTANCE_REVIEW",
        discriminator="denial-node-wait",
        attempt_id=ATTEMPT_ID,
        evidence_refs=(RECEIPT_REF,),
    )
    graph_outbox = _event(
        "TASK_GRAPH",
        GRAPH_ID,
        2,
        "TASK_GRAPH_BLOCKED",
        discriminator="denial-graph-block",
        graph_revision=1,
    )
    committed = prepared.commit_node_acceptance_review(
        GRAPH_ID,
        1,
        NODE_ID,
        attempt_id=ATTEMPT_ID,
        expected_node_version=5,
        acceptance_judgement=_review_judgement(),
        node_outbox=node_outbox,
        review_item=review_item,
        work_order_id="work-order::durable-verification",
        expected_graph_version=1,
        graph_outbox=graph_outbox,
    )
    return prepared, review_store, committed


def test_post_review_denial_terminal_bundle_is_atomic_and_replayable(
    tmp_path: Path,
) -> None:
    prepared, reviews, waiting = _waiting_post_review(tmp_path)
    rejected = reviews.reject_review(
        waiting["review"]["review"]["review_id"],
        actor_id="reviewer::review-denial",
    )
    review = rejected["review"]
    assert review["status"] == "REJECTED"
    assert review["resume_phase"] == (
        ReviewResumePhase.POST_EXECUTION_ACCEPTANCE.value
    )
    graph = prepared.get_graph(GRAPH_ID, 1)
    node = prepared.get_node(GRAPH_ID, 1, NODE_ID)
    budget = prepared.read_scheduler_snapshot(GRAPH_ID, 1)["budgets"][0]
    decision = waiting["decision"]
    settlement_ref = build_review_denial_settlement_ref(
        review_id=review["review_id"],
        review_status=review["status"],
        review_status_version=int(rejected["status_version"]),
        resume_phase=review["resume_phase"],
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id=NODE_ID,
        node_version_before=int(node["status_version"]),
        node_target_status="FAILED",
        graph_status_before=str(graph["status"]),
        graph_version_before=int(graph["status_version"]),
        graph_target_status="FAILED",
        budget_id=BUDGET_ID,
        budget_version_before=int(budget["status_version"]),
        acceptance_ref=decision["acceptance_ref"],
        attempt_id=ATTEMPT_ID,
    )
    evidence = (
        RECEIPT_REF,
        f"review://{review['review_id']}",
        settlement_ref,
    )
    arguments = {
        "settlement_ref": settlement_ref,
        "review_id": review["review_id"],
        "expected_node_version": int(node["status_version"]),
        "expected_graph_version": int(graph["status_version"]),
        "budget_id": BUDGET_ID,
        "expected_budget_version": int(budget["status_version"]),
        "node_outbox": _event(
            "TASK_NODE",
            f"{GRAPH_ID}@1/{NODE_ID}",
            int(node["status_version"]) + 1,
            "TASK_NODE_FAILED",
            discriminator="denial-node-failed",
            attempt_id=ATTEMPT_ID,
            evidence_refs=evidence,
        ),
        "graph_outbox": _event(
            "TASK_GRAPH",
            GRAPH_ID,
            int(graph["status_version"]) + 1,
            "TASK_GRAPH_FAILED",
            discriminator="denial-graph-failed",
            attempt_id=ATTEMPT_ID,
            graph_revision=1,
            evidence_refs=evidence,
        ),
        "budget_outbox": _event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            int(budget["status_version"]) + 1,
            "BUDGET_RESERVATION_RELEASED",
            discriminator="denial-budget-released",
            evidence_refs=evidence,
        ),
        "acceptance_ref": decision["acceptance_ref"],
        "attempt_id": ATTEMPT_ID,
    }

    faulting = _FailGraphSettlementStore(
        prepared.db_path,
        clock=lambda: NOW,
    )
    with pytest.raises(
        RuntimeError,
        match="fault after terminal Graph Outbox insert",
    ):
        faulting.settle_review_denial(
            GRAPH_ID,
            1,
            NODE_ID,
            **arguments,
        )
    rolled_back = DurableTaskStore(prepared.db_path, clock=lambda: NOW + 1)
    assert rolled_back.get_node(GRAPH_ID, 1, NODE_ID)["status"] == (
        "WAITING_ACCEPTANCE_REVIEW"
    )
    assert rolled_back.get_graph(GRAPH_ID, 1)["status"] == "BLOCKED"
    assert rolled_back.read_scheduler_snapshot(GRAPH_ID, 1)["budgets"][0][
        "status"
    ] == "ACTIVE"
    with sqlite3.connect(prepared.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_review_denial_settlement"
        ).fetchone()[0] == 0
        assert conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE outbox_id IN (?, ?, ?)
            """,
            (
                arguments["node_outbox"].outbox_id,
                arguments["graph_outbox"].outbox_id,
                arguments["budget_outbox"].outbox_id,
            ),
        ).fetchone()[0] == 0

    committed = rolled_back.settle_review_denial(
        GRAPH_ID,
        1,
        NODE_ID,
        **arguments,
    )
    assert committed["replayed"] is False
    assert committed["node"]["status"] == "FAILED"
    assert committed["graph"]["status"] == "FAILED"
    assert committed["budget"]["status"] == "RELEASED"
    assert committed["settlement"]["settlement_ref"] == settlement_ref
    assert committed["settlement"]["acceptance_ref"] == (
        decision["acceptance_ref"]
    )
    replay = DurableTaskStore(
        prepared.db_path,
        clock=lambda: NOW + 2,
    ).settle_review_denial(
        GRAPH_ID,
        1,
        NODE_ID,
        **arguments,
    )
    assert replay["replayed"] is True
    assert replay["settlement"] == committed["settlement"]
    snapshot = rolled_back.read_scheduler_snapshot(GRAPH_ID, 1)
    assert snapshot["review_denial_settlements"] == [committed["settlement"]]

    with sqlite3.connect(prepared.db_path) as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="review denial settlement is append-only",
        ):
            conn.execute(
                """
                UPDATE task_runtime_review_denial_settlement
                SET review_sha256 = ?
                WHERE settlement_ref = ?
                """,
                ("0" * 64, settlement_ref),
            )
    with sqlite3.connect(prepared.db_path) as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="settled review decision is immutable",
        ):
            conn.execute(
                """
                UPDATE agent_runtime_review
                SET decision_actor = 'forged'
                WHERE review_id = ?
                """,
                (review["review_id"],),
            )
    with pytest.raises(TaskStoreError) as drifted:
        rolled_back.settle_review_denial(
            GRAPH_ID,
            1,
            NODE_ID,
            **{
                **arguments,
                "settlement_ref": (
                    "review-denial://sha256/" + "f" * 64
                ),
            },
        )
    assert drifted.value.reason_code == TASK_STORE_CAS_CONFLICT
