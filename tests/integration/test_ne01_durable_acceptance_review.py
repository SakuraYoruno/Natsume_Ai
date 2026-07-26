from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.task_graph import TaskNodeStatus
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_store_types import (
    TASK_STORE_CAS_CONFLICT,
    TaskStoreError,
)
from host_brain.task_runtime.review_runtime import (
    TaskNodeReviewContext,
    TaskNodeReviewRuntime,
)
from tests.integration.test_ne01_durable_verification_terminal import (
    ATTEMPT_ID,
    GRAPH_ID,
    NODE_ID,
    NOW,
    RECEIPT_REF,
    _event,
    _prepared_store,
    _verification,
    _verification_outbox,
)
from host_brain.contracts.agent_runtime.verification_contract import (
    VerificationStatus,
)


class _FailAfterAcceptanceDecisionStore(DurableTaskStore):
    def _insert_node_acceptance_decision_in_transaction(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        super()._insert_node_acceptance_decision_in_transaction(
            *args,
            **kwargs,
        )
        raise RuntimeError("fault after acceptance decision insert")


def _review_judgement() -> dict[str, Any]:
    return {
        "schema_version": "Natsume.agent.acceptance_judgement.v1",
        "judgement_id": "acceptance::durable-review",
        "work_order_id": "work-order::durable-verification",
        "task_graph_id": GRAPH_ID,
        "task_node_id": NODE_ID,
        "attempt_id": ATTEMPT_ID,
        "decision": "REVIEW",
        "reason_code": "ACCEPTANCE_REQUIRES_REVIEW",
        "evidence_refs": [RECEIPT_REF],
        "criteria": [
            {
                "criterion_id": "criterion::durable-review",
                "criterion_text": "output verified",
                "verdict": "FAIL",
                "evidence_refs": [RECEIPT_REF],
                "deterministic": True,
                "reason_code": "ACCEPTANCE_REQUIRES_REVIEW",
                "critic_verdict": None,
            }
        ],
        "critic_candidate": None,
        "started_at_ms": NOW,
        "finished_at_ms": NOW + 1,
    }


def test_review_staging_rolls_back_node_graph_review_and_decision_together(
    tmp_path: Path,
) -> None:
    prepared = _prepared_store(tmp_path, name="acceptance-review.sqlite3")
    verification = _verification(status=VerificationStatus.PASS)
    prepared.commit_node_verification(
        GRAPH_ID,
        1,
        NODE_ID,
        attempt_id=ATTEMPT_ID,
        reservation_id="operation::durable-verification",
        expected_node_version=4,
        target_status=TaskNodeStatus.VERIFIED,
        verification_result=verification,
        node_outbox=_verification_outbox(status=TaskNodeStatus.VERIFIED),
    )
    review_runtime = TaskNodeReviewRuntime(
        ReviewStore(prepared.db_path, clock=lambda: NOW),
        clock=lambda: NOW,
    )
    review_item = review_runtime.prepare_post_execution_acceptance_review(
        context=TaskNodeReviewContext(
            work_order_id="work-order::durable-verification",
            root_work_order_id="work-order::durable-verification",
            graph_id=GRAPH_ID,
            graph_revision=1,
            node_id=NODE_ID,
            node_status_version=6,
            attempt_id=ATTEMPT_ID,
            requester="principal::durable-review",
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
        discriminator="node-acceptance-review",
        attempt_id=ATTEMPT_ID,
        evidence_refs=(RECEIPT_REF,),
    )
    graph_outbox = _event(
        "TASK_GRAPH",
        GRAPH_ID,
        2,
        "TASK_GRAPH_BLOCKED",
        discriminator="graph-acceptance-review",
        graph_revision=1,
    )
    faulting = _FailAfterAcceptanceDecisionStore(
        prepared.db_path,
        clock=lambda: NOW,
    )
    arguments = {
        "attempt_id": ATTEMPT_ID,
        "expected_node_version": 5,
        "acceptance_judgement": _review_judgement(),
        "node_outbox": node_outbox,
        "review_item": review_item,
        "work_order_id": "work-order::durable-verification",
        "expected_graph_version": 1,
        "graph_outbox": graph_outbox,
    }

    with pytest.raises(
        RuntimeError,
        match="fault after acceptance decision insert",
    ):
        faulting.commit_node_acceptance_review(
            GRAPH_ID,
            1,
            NODE_ID,
            **arguments,
        )

    reopened = DurableTaskStore(prepared.db_path, clock=lambda: NOW + 10)
    assert reopened.get_node(GRAPH_ID, 1, NODE_ID)["status"] == "VERIFIED"
    assert reopened.get_graph(GRAPH_ID, 1)["status"] == "ACTIVE"
    with sqlite3.connect(prepared.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_node_acceptance_decision"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM agent_runtime_review"
        ).fetchone()[0] == 0
        assert conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE outbox_id IN (?, ?)
            """,
            (node_outbox.outbox_id, graph_outbox.outbox_id),
        ).fetchone()[0] == 0

    committed = reopened.commit_node_acceptance_review(
        GRAPH_ID,
        1,
        NODE_ID,
        **arguments,
    )
    assert committed["node"]["status"] == "WAITING_ACCEPTANCE_REVIEW"
    assert committed["graph"]["status"] == "BLOCKED"
    assert committed["decision"]["review_id"] == review_item.review_id
    assert committed["review"]["review"]["review_id"] == review_item.review_id
    replay = reopened.commit_node_acceptance_review(
        GRAPH_ID,
        1,
        NODE_ID,
        **arguments,
    )
    assert replay["replayed"] is True
    assert replay["decision"] == committed["decision"]

    with sqlite3.connect(prepared.db_path) as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="acceptance decision is append-only",
        ):
            conn.execute(
                """
                UPDATE task_runtime_node_acceptance_decision
                SET result_sha256 = ?
                WHERE acceptance_ref = ?
                """,
                ("0" * 64, committed["decision"]["acceptance_ref"]),
            )
    with sqlite3.connect(prepared.db_path) as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="acceptance decision cannot be deleted",
        ):
            conn.execute(
                """
                DELETE FROM task_runtime_node_acceptance_decision
                WHERE acceptance_ref = ?
                """,
                (committed["decision"]["acceptance_ref"],),
            )

    drifted_review = replace(
        review_item,
        review_id="review::durable-acceptance-drift",
    )
    assert drifted_review.review_id != review_item.review_id
    with pytest.raises(TaskStoreError) as drifted:
        reopened.commit_node_acceptance_review(
            GRAPH_ID,
            1,
            NODE_ID,
            **{**arguments, "review_item": drifted_review},
        )
    assert drifted.value.reason_code == TASK_STORE_CAS_CONFLICT
