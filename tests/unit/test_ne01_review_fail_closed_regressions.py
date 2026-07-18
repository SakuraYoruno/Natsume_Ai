from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.persistence.review_continuation_store import (
    REVIEW_CONTINUATION_CONFLICT,
    ReviewContinuationStore,
)
from host_brain.persistence.review_repository import (
    REVIEW_TOKEN_EXPIRED,
    ReviewStore,
    ReviewStoreError,
)


@dataclass
class _Clock:
    value: int = 1_000

    def __call__(self) -> int:
        return self.value


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "review-fail-closed.sqlite3"
    result = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id="test-review-fail-closed",
        ts_ms=1_000,
    )
    assert result.success, result.as_dict()
    return path


def _insert_graph(path: Path, *, graph_id: str = "graph-review") -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref,
                status, status_version, graph_json, source_kind, source_ref,
                legacy_snapshot_json, created_ts_ms, updated_ts_ms
            ) VALUES (?, 1, 'work-review', 'planner://review/test',
                      'BLOCKED', 1, '{}', 'CANONICAL', NULL, NULL, 1000, 1000)
            """,
            (graph_id,),
        )


def _insert_node(
    path: Path,
    *,
    graph_id: str = "graph-review",
    node_id: str = "node-review",
    status: str = "WAITING_APPROVAL",
    status_version: int = 1,
) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO task_runtime_node(
                graph_id, graph_revision, node_id, status, status_version,
                node_json, cancellation_requested_ts_ms,
                created_ts_ms, updated_ts_ms
            ) VALUES (?, 1, ?, ?, ?, '{}', NULL, 1000, 1000)
            """,
            (graph_id, node_id, status, status_version),
        )


def _insert_attempt(
    path: Path,
    *,
    attempt_id: str,
    node_id: str = "node-review",
    status: str,
) -> None:
    completed_ts_ms = 1_000 if status in {"SUCCEEDED", "FAILED"} else None
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms, acquired_ts_ms,
                started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
            ) VALUES (?, 'graph-review', 1, ?, 1, ?, 3, 'worker',
                      'scope', 'fingerprint', 1000, 1000, 1000, ?, 1000, NULL)
            """,
            (attempt_id, node_id, status, completed_ts_ms),
        )


def _review(
    review_id: str,
    *,
    node_id: str = "node-review",
    subject_graph_id: str = "graph-review",
    subject_version: int = 1,
    phase: ReviewResumePhase = ReviewResumePhase.PRE_EXECUTION,
    attempt: str = "not-started",
    expires_at: int = 50_000,
) -> ReviewItem:
    post = phase is ReviewResumePhase.POST_EXECUTION_ACCEPTANCE
    return ReviewItem(
        review_id=review_id,
        review_type=(
            ReviewType.POST_EXECUTION_ACCEPTANCE
            if post
            else ReviewType.PRE_EXECUTION_APPROVAL
        ),
        subject_ref=(
            f"task-node://{subject_graph_id}/1/{node_id}"
            f"?root=work-review&attempt={attempt}"
        ),
        subject_version=subject_version,
        requester="owner",
        reason="fail-closed regression",
        risk_level=3,
        side_effects=("write",),
        evidence_refs=("evidence://review/fail-closed",),
        status=ReviewStatus.PENDING,
        expires_at=expires_at,
        resume_phase=phase,
        checkpoint_id=(
            f"task-checkpoint://{subject_graph_id}/1/{node_id}/"
            f"{phase.value.lower()}?root=work-review&attempt={attempt}"
        ),
    )


def _create(
    store: ReviewStore,
    review: ReviewItem,
    *,
    graph_id: str = "graph-review",
    node_id: str = "node-review",
) -> dict:
    return store.create_review(
        review,
        work_order_id="work-review",
        task_graph_id=graph_id,
        task_node_id=node_id,
    )


def _assert_review_was_not_persisted(path: Path, review_id: str) -> None:
    with sqlite3.connect(str(path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM agent_runtime_review WHERE review_id = ?",
            (review_id,),
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("outer_graph_id", "outer_node_id"),
    (
        ("graph-other", "node-review"),
        ("graph-review", "node-other"),
    ),
)
def test_create_review_rejects_subject_outer_lineage_mismatch(
    tmp_path: Path,
    outer_graph_id: str,
    outer_node_id: str,
) -> None:
    path = _db(tmp_path)
    _insert_graph(path)
    _insert_node(path)
    store = ReviewStore(path, clock=_Clock())
    review = _review("review-lineage-mismatch")

    with pytest.raises(ReviewStoreError) as rejected:
        _create(
            store,
            review,
            graph_id=outer_graph_id,
            node_id=outer_node_id,
        )

    assert rejected.value.reason_code == REVIEW_CONTINUATION_CONFLICT
    _assert_review_was_not_persisted(path, review.review_id)


def test_create_review_rejects_task_subject_without_exact_durable_node(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _insert_graph(path)
    store = ReviewStore(path, clock=_Clock())
    review = _review("review-node-missing")

    with pytest.raises(ReviewStoreError) as rejected:
        _create(store, review)

    assert rejected.value.reason_code == REVIEW_CONTINUATION_CONFLICT
    _assert_review_was_not_persisted(path, review.review_id)


@pytest.mark.parametrize(
    ("node_status", "node_version", "review_version"),
    (
        ("READY", 1, 1),
        ("WAITING_APPROVAL", 2, 1),
    ),
)
def test_create_review_rejects_wrong_waiting_status_or_subject_version(
    tmp_path: Path,
    node_status: str,
    node_version: int,
    review_version: int,
) -> None:
    path = _db(tmp_path)
    _insert_graph(path)
    _insert_node(
        path,
        status=node_status,
        status_version=node_version,
    )
    store = ReviewStore(path, clock=_Clock())
    review = _review(
        f"review-checkpoint-{node_status.lower()}-{node_version}",
        subject_version=review_version,
    )

    with pytest.raises(ReviewStoreError) as rejected:
        _create(store, review)

    assert rejected.value.reason_code == REVIEW_CONTINUATION_CONFLICT
    _assert_review_was_not_persisted(path, review.review_id)


@pytest.mark.parametrize(
    ("phase", "attempt", "node_status", "attempt_status"),
    (
        (
            ReviewResumePhase.PRE_EXECUTION,
            "attempt-pre-bound",
            "WAITING_APPROVAL",
            None,
        ),
        (
            ReviewResumePhase.PRE_EXECUTION,
            "not-started",
            "WAITING_APPROVAL",
            "RUNNING",
        ),
        (
            ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
            "not-started",
            "WAITING_ACCEPTANCE_REVIEW",
            None,
        ),
        (
            ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
            "attempt-post-failed",
            "WAITING_ACCEPTANCE_REVIEW",
            "FAILED",
        ),
    ),
)
def test_create_review_rejects_phase_attempt_mismatch(
    tmp_path: Path,
    phase: ReviewResumePhase,
    attempt: str,
    node_status: str,
    attempt_status: str | None,
) -> None:
    path = _db(tmp_path)
    _insert_graph(path)
    _insert_node(path, status=node_status)
    if attempt_status is not None:
        attempt_id = (
            "attempt-pre-live"
            if phase is ReviewResumePhase.PRE_EXECUTION
            else attempt
        )
        _insert_attempt(path, attempt_id=attempt_id, status=attempt_status)
    store = ReviewStore(path, clock=_Clock())
    review = _review(
        f"review-phase-attempt-{phase.value.lower()}-{attempt}",
        phase=phase,
        attempt=attempt,
    )

    with pytest.raises(ReviewStoreError) as rejected:
        _create(store, review)

    assert rejected.value.reason_code == REVIEW_CONTINUATION_CONFLICT
    _assert_review_was_not_persisted(path, review.review_id)


def test_expire_due_uses_earliest_token_ttl_and_atomically_queues_denial(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _insert_graph(path)
    _insert_node(path)
    clock = _Clock()
    store = ReviewStore(path, clock=clock)
    review = _review("review-token-ttl-first", expires_at=50_000)
    _create(store, review)
    approved = store.approve_review(
        review.review_id,
        actor_id="reviewer",
        ttl_ms=100,
    )
    assert approved["resume_token"]["expires_ts_ms"] == 1_100
    clock.value = 1_100

    result = store.expire_due()

    assert result == {
        "expired_review_ids": (review.review_id,),
        "expired_token_count": 0,
        "failed_reviews": (),
    }
    assert store.get_review(review.review_id)["review"]["status"] == "EXPIRED"
    continuation = ReviewContinuationStore(path, clock=clock).get_review_continuation(
        review.review_id
    )
    assert continuation is not None
    assert continuation["review"]["status"] == "EXPIRED"
    with sqlite3.connect(str(path)) as conn:
        assert conn.execute(
            """
            SELECT r.expires_at, t.expires_ts_ms, t.state
            FROM agent_runtime_review AS r
            JOIN agent_runtime_review_token AS t ON t.review_id = r.review_id
            WHERE r.review_id = ?
            """,
            (review.review_id,),
        ).fetchone() == (50_000, 1_100, "EXPIRED")


def test_approve_expired_review_returns_error_after_committing_expiry_and_queue(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _insert_graph(path)
    _insert_node(path)
    clock = _Clock()
    store = ReviewStore(path, clock=clock)
    review = _review("review-expired-on-approve", expires_at=1_100)
    _create(store, review)
    clock.value = 1_100

    with pytest.raises(ReviewStoreError) as expired:
        store.approve_review(review.review_id, actor_id="reviewer")

    assert expired.value.reason_code == REVIEW_TOKEN_EXPIRED
    assert store.get_review(review.review_id)["review"]["status"] == "EXPIRED"
    continuation = ReviewContinuationStore(path, clock=clock).get_review_continuation(
        review.review_id
    )
    assert continuation is not None
    assert continuation["review"]["status"] == "EXPIRED"


def test_resume_expired_token_returns_error_after_committing_expiry_and_queue(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _insert_graph(path)
    _insert_node(path)
    clock = _Clock()
    store = ReviewStore(path, clock=clock)
    review = _review("review-expired-on-resume", expires_at=50_000)
    _create(store, review)
    approved = store.approve_review(
        review.review_id,
        actor_id="reviewer",
        ttl_ms=100,
    )
    clock.value = 1_100

    with pytest.raises(ReviewStoreError) as expired:
        store.resume_review(
            review.review_id,
            resume_token_ref=approved["resume_token_ref"],
            subject_ref=review.subject_ref,
            subject_version=review.subject_version,
            resume_phase=review.resume_phase,
            checkpoint_id=str(review.checkpoint_id),
        )

    assert expired.value.reason_code == REVIEW_TOKEN_EXPIRED
    assert store.get_review(review.review_id)["review"]["status"] == "EXPIRED"
    continuation = ReviewContinuationStore(path, clock=clock).get_review_continuation(
        review.review_id
    )
    assert continuation is not None
    assert continuation["review"]["status"] == "EXPIRED"
    with sqlite3.connect(str(path)) as conn:
        assert conn.execute(
            "SELECT state FROM agent_runtime_review_token WHERE review_id = ?",
            (review.review_id,),
        ).fetchone() == ("EXPIRED",)


def test_historical_poison_review_does_not_block_healthy_expiry_or_reconciliation(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _insert_graph(path)
    _insert_node(path, node_id="node-poison")
    _insert_node(path, node_id="node-healthy")
    clock = _Clock()
    reviews = ReviewStore(path, clock=clock)
    poison = _review(
        "review-00-poison",
        node_id="node-poison",
        expires_at=2_000,
    )
    healthy = _review(
        "review-10-healthy",
        node_id="node-healthy",
        expires_at=2_000,
    )
    _create(reviews, poison, node_id="node-poison")
    _create(reviews, healthy, node_id="node-healthy")
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            "UPDATE agent_runtime_review SET review_json = '{' WHERE review_id = ?",
            (poison.review_id,),
        )
    clock.value = 2_000

    expired = reviews.expire_due()

    assert expired["expired_review_ids"] == (healthy.review_id,)
    assert expired["failed_reviews"] == (
        {
            "review_id": poison.review_id,
            "reason_code": "REVIEW_STORE_INVALID",
        },
    )
    continuation_store = ReviewContinuationStore(path, clock=clock)
    assert continuation_store.get_review_continuation(healthy.review_id) is not None
    assert continuation_store.get_review_continuation(poison.review_id) is None

    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            UPDATE agent_runtime_review
            SET status = 'EXPIRED', status_version = status_version + 1
            WHERE review_id = ?
            """,
            (poison.review_id,),
        )
        conn.execute(
            "DELETE FROM task_runtime_review_continuation WHERE review_id = ?",
            (healthy.review_id,),
        )

    reconciled = continuation_store.reconcile_terminal_reviews()

    assert reconciled == {
        "scanned_count": 2,
        "enqueued_count": 1,
        "already_crossed_count": 0,
        "failed_count": 1,
    }
    assert continuation_store.get_review_continuation(healthy.review_id) is not None
    assert continuation_store.get_review_continuation(poison.review_id) is None


def test_contract_invalid_json_review_does_not_block_healthy_expiry(
    tmp_path: Path,
) -> None:
    """Syntactically valid historical JSON can still violate ReviewItem."""

    path = _db(tmp_path)
    _insert_graph(path)
    _insert_node(path, node_id="node-contract-poison")
    _insert_node(path, node_id="node-contract-healthy")
    clock = _Clock()
    reviews = ReviewStore(path, clock=clock)
    poison = _review(
        "review-00-contract-poison",
        node_id="node-contract-poison",
        expires_at=2_000,
    )
    healthy = _review(
        "review-10-contract-healthy",
        node_id="node-contract-healthy",
        expires_at=2_000,
    )
    _create(reviews, poison, node_id="node-contract-poison")
    _create(reviews, healthy, node_id="node-contract-healthy")
    with sqlite3.connect(str(path)) as conn:
        raw = conn.execute(
            "SELECT review_json FROM agent_runtime_review WHERE review_id = ?",
            (poison.review_id,),
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["risk_level"] = 99
        conn.execute(
            "UPDATE agent_runtime_review SET review_json = ? WHERE review_id = ?",
            (json.dumps(payload, sort_keys=True), poison.review_id),
        )
    clock.value = 2_000

    expired = reviews.expire_due()

    assert expired == {
        "expired_review_ids": (healthy.review_id,),
        "expired_token_count": 0,
        "failed_reviews": (
            {
                "review_id": poison.review_id,
                "reason_code": "REVIEW_ITEM_INVALID",
            },
        ),
    }
    continuation_store = ReviewContinuationStore(path, clock=clock)
    assert continuation_store.get_review_continuation(healthy.review_id) is not None
    assert continuation_store.get_review_continuation(poison.review_id) is None


def test_claim_batch_skips_cross_table_drift_and_claims_healthy_row(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _insert_graph(path)
    _insert_node(path, node_id="node-00-claim-poison")
    _insert_node(path, node_id="node-10-claim-healthy")
    clock = _Clock()
    reviews = ReviewStore(path, clock=clock)
    poison = _review("review-00-claim-poison", node_id="node-00-claim-poison")
    healthy = _review("review-10-claim-healthy", node_id="node-10-claim-healthy")
    _create(reviews, poison, node_id="node-00-claim-poison")
    _create(reviews, healthy, node_id="node-10-claim-healthy")
    reviews.reject_review(poison.review_id, actor_id="reviewer")
    reviews.reject_review(healthy.review_id, actor_id="reviewer")
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            UPDATE task_runtime_review_continuation
            SET subject_version = subject_version + 1
            WHERE review_id = ?
            """,
            (poison.review_id,),
        )

    continuation_store = ReviewContinuationStore(path, clock=clock)
    claimed = continuation_store.claim_pending_review_continuations(
        worker_id="review-claim-worker",
        lease_ms=30_000,
        limit=1,
    )

    assert tuple(row["review_id"] for row in claimed) == (healthy.review_id,)
    with sqlite3.connect(str(path)) as conn:
        rows = conn.execute(
            """
            SELECT review_id, claim_owner
            FROM task_runtime_review_continuation
            WHERE review_id IN (?, ?)
            ORDER BY review_id
            """,
            (poison.review_id, healthy.review_id),
        ).fetchall()
    assert rows == [
        (poison.review_id, None),
        (healthy.review_id, "review-claim-worker"),
    ]
