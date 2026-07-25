from __future__ import annotations

from dataclasses import dataclass
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
    REVIEW_CONTINUATION_CLAIM_LOST,
    REVIEW_CONTINUATION_INCOMPLETE,
    ReviewContinuationStore,
    ReviewContinuationStoreError,
)
from host_brain.persistence.review_repository import ReviewStore, ReviewStoreError
from host_brain.persistence.task_graph_admission_authority import (
    canonical_task_graph_admission_scope,
    register_task_graph_admission_authority,
)


@dataclass
class _Clock:
    value: int = 1_000

    def __call__(self) -> int:
        return self.value


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "review-continuation.sqlite3"
    result = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id="test-review-continuation",
        ts_ms=1_000,
    )
    assert result.success, result.as_dict()
    return path


def _insert_checkpoint(
    path: Path,
    *,
    graph_id: str = "graph-review",
    revision: int = 1,
    node_id: str = "node-review",
    node_status: str,
    node_version: int,
    attempt_id: str | None = None,
) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        register_task_graph_admission_authority(conn)
        with canonical_task_graph_admission_scope(graph_id, revision):
            conn.execute(
                """
                INSERT INTO task_runtime_graph(
                    graph_id, revision, root_work_order_id, planner_ref,
                    status, status_version, graph_json, source_kind, source_ref,
                    legacy_snapshot_json, created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, 'work-review', 'planner://review/test',
                          'BLOCKED', 1, '{}', 'CANONICAL', NULL, NULL, 1000, 1000)
                """,
                (graph_id, revision),
            )
            conn.execute(
                """
                INSERT INTO task_runtime_node(
                    graph_id, graph_revision, node_id, status, status_version,
                    node_json, cancellation_requested_ts_ms,
                    created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, '{}', NULL, 1000, 1000)
                """,
                (graph_id, revision, node_id, node_status, node_version),
            )
        if attempt_id is not None:
            conn.execute(
                """
                INSERT INTO task_runtime_attempt(
                    attempt_id, graph_id, graph_revision, node_id, attempt_no,
                    status, status_version, owner_id, operation_scope,
                    operation_fingerprint, created_ts_ms, acquired_ts_ms,
                    started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
                ) VALUES (?, ?, ?, ?, 1, 'SUCCEEDED', 3, 'worker',
                          'scope', 'fingerprint', 1000, 1000, 1000, 1000, 1000, NULL)
                """,
                (attempt_id, graph_id, revision, node_id),
            )


def _review(
    review_id: str,
    *,
    phase: ReviewResumePhase,
    subject_version: int,
    attempt_id: str | None = None,
    expires_at: int = 50_000,
) -> ReviewItem:
    post = phase is ReviewResumePhase.POST_EXECUTION_ACCEPTANCE
    attempt = attempt_id if post else "not-started"
    return ReviewItem(
        review_id=review_id,
        review_type=(
            ReviewType.POST_EXECUTION_ACCEPTANCE
            if post
            else ReviewType.PRE_EXECUTION_APPROVAL
        ),
        subject_ref=(
            "task-node://graph-review/1/node-review"
            f"?root=work-review&attempt={attempt}"
        ),
        subject_version=subject_version,
        requester="owner",
        reason="durable checkpoint",
        risk_level=3,
        side_effects=("write",),
        evidence_refs=("evidence://review/checkpoint",),
        status=ReviewStatus.PENDING,
        expires_at=expires_at,
        resume_phase=phase,
        checkpoint_id=(
            "task-checkpoint://graph-review/1/node-review/"
            f"{phase.value.lower()}?root=work-review&attempt={attempt}"
        ),
    )


def _create(store: ReviewStore, review: ReviewItem) -> dict:
    return store.create_review(
        review,
        work_order_id="work-review",
        task_graph_id="graph-review",
        task_node_id="node-review",
    )


def _consume(store: ReviewStore, review: ReviewItem) -> dict:
    approved = store.approve_review(review.review_id, actor_id="reviewer")
    return store.resume_review(
        review.review_id,
        resume_token_ref=approved["resume_token_ref"],
        subject_ref=review.subject_ref,
        subject_version=review.subject_version,
        resume_phase=review.resume_phase,
        checkpoint_id=str(review.checkpoint_id),
        resume_result_ref=f"review-resume://{review.review_id}/1",
    )


def _insert_live_attempt(
    path: Path,
    *,
    attempt_id: str,
    status: str,
) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms, acquired_ts_ms,
                started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
            ) VALUES (?, 'graph-review', 1, 'node-review', 1,
                      ?, 1, 'task-worker', 'scope', 'fingerprint',
                      1000, 1000, 1000, NULL, 1000, NULL)
            """,
            (attempt_id, status),
        )


def test_pre_resume_atomically_enqueues_without_inventing_an_attempt(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _insert_checkpoint(
        path,
        node_status="WAITING_APPROVAL",
        node_version=1,
    )
    clock = _Clock()
    reviews = ReviewStore(path, clock=clock)
    review = _review(
        "review-pre-continuation",
        phase=ReviewResumePhase.PRE_EXECUTION,
        subject_version=1,
    )
    _create(reviews, review)

    resumed = _consume(reviews, review)

    assert resumed["review"]["review"]["status"] == "CONSUMED"
    assert resumed["continuation"]["review_id"] == review.review_id
    with sqlite3.connect(str(path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_attempt"
        ).fetchone() == (0,)


def test_claim_authority_is_secret_free_fenced_and_completion_rechecks_node(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _insert_checkpoint(path, node_status="WAITING_APPROVAL", node_version=1)
    clock = _Clock()
    reviews = ReviewStore(path, clock=clock)
    review = _review(
        "review-pre-claim",
        phase=ReviewResumePhase.PRE_EXECUTION,
        subject_version=1,
    )
    _create(reviews, review)
    _consume(reviews, review)
    store = ReviewContinuationStore(path, clock=clock)
    claim = store.claim_pending_review_continuations(
        worker_id="review-worker",
        lease_ms=30_000,
        limit=1,
    )[0]

    authority = store.validate_review_continuation_claim(claim)
    assert "claim_token" not in authority
    assert authority["claim_token_sha256"].startswith("sha256:")
    assert store.validate_review_continuation_authority(authority) == authority
    with pytest.raises(ReviewContinuationStoreError) as incomplete:
        store.complete_review_continuation(
            review.review_id,
            worker_id="review-worker",
            claim_token=claim["claim_token"],
            fencing_token=claim["claim_fencing_token"],
            expected_version=claim["status_version"],
        )
    assert incomplete.value.reason_code == REVIEW_CONTINUATION_INCOMPLETE

    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = 'READY', status_version = 2, updated_ts_ms = 1001
            WHERE graph_id = 'graph-review' AND graph_revision = 1
              AND node_id = 'node-review'
            """
        )
    completed = store.complete_review_continuation(
        review.review_id,
        worker_id="review-worker",
        claim_token=claim["claim_token"],
        fencing_token=claim["claim_fencing_token"],
        expected_version=claim["status_version"],
    )
    assert completed["completed"] is True
    assert store.get_review_continuation(review.review_id) is None

    with pytest.raises(ReviewContinuationStoreError) as stale:
        store.validate_review_continuation_authority(authority)
    assert stale.value.reason_code in {
        REVIEW_CONTINUATION_CLAIM_LOST,
        "REVIEW_CONTINUATION_INVALID",
    }


@pytest.mark.parametrize("attempt_status", ("RUNNING", "RECOVERABLE"))
def test_expired_pre_claim_can_be_taken_over_after_execution_crosses_checkpoint(
    tmp_path: Path,
    attempt_status: str,
) -> None:
    path = _db(tmp_path)
    _insert_checkpoint(path, node_status="WAITING_APPROVAL", node_version=1)
    clock = _Clock()
    reviews = ReviewStore(path, clock=clock)
    review = _review(
        f"review-pre-takeover-{attempt_status.lower()}",
        phase=ReviewResumePhase.PRE_EXECUTION,
        subject_version=1,
    )
    _create(reviews, review)
    _consume(reviews, review)
    store = ReviewContinuationStore(path, clock=clock)
    old_claim = store.claim_pending_review_continuations(
        worker_id="old-review-worker",
        lease_ms=25,
        limit=1,
    )[0]

    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = 'RUNNING', status_version = 2, updated_ts_ms = 1001
            WHERE graph_id = 'graph-review' AND graph_revision = 1
              AND node_id = 'node-review'
            """
        )
    _insert_live_attempt(
        path,
        attempt_id=f"attempt-pre-takeover-{attempt_status.lower()}",
        status=attempt_status,
    )
    clock.value = int(old_claim["claim_expires_ts_ms"]) + 1

    new_claim = store.claim_pending_review_continuations(
        worker_id="new-review-worker",
        lease_ms=30_000,
        limit=1,
    )[0]
    authority = store.validate_review_continuation_claim(new_claim)

    assert new_claim["claim_owner"] == "new-review-worker"
    assert new_claim["claim_fencing_token"] == old_claim["claim_fencing_token"] + 1
    assert new_claim["checkpoint_crossed"] is True
    assert authority["claim_owner"] == "new-review-worker"
    assert authority["claim_fencing_token"] == new_claim["claim_fencing_token"]

    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            UPDATE task_runtime_attempt
            SET status = 'SUCCEEDED', status_version = status_version + 1,
                completed_ts_ms = 1002, updated_ts_ms = 1002
            WHERE attempt_id = ?
            """,
            (f"attempt-pre-takeover-{attempt_status.lower()}",),
        )
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = 'ACCEPTED', status_version = status_version + 1,
                updated_ts_ms = 1002
            WHERE graph_id = 'graph-review' AND graph_revision = 1
              AND node_id = 'node-review'
            """
        )

    completed = store.complete_review_continuation(
        review.review_id,
        worker_id="new-review-worker",
        claim_token=new_claim["claim_token"],
        fencing_token=new_claim["claim_fencing_token"],
        expected_version=new_claim["status_version"],
    )

    assert completed["completed"] is True
    assert completed["checkpoint_crossed"] is True
    assert store.get_review_continuation(review.review_id) is None


def test_expired_pre_claim_rejects_takeover_while_waiting_node_has_live_attempt(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _insert_checkpoint(path, node_status="WAITING_APPROVAL", node_version=1)
    clock = _Clock()
    reviews = ReviewStore(path, clock=clock)
    review = _review(
        "review-pre-invalid-waiting-live-attempt",
        phase=ReviewResumePhase.PRE_EXECUTION,
        subject_version=1,
    )
    _create(reviews, review)
    _consume(reviews, review)
    store = ReviewContinuationStore(path, clock=clock)
    old_claim = store.claim_pending_review_continuations(
        worker_id="old-review-worker",
        lease_ms=25,
        limit=1,
    )[0]
    _insert_live_attempt(
        path,
        attempt_id="attempt-pre-invalid-waiting",
        status="RUNNING",
    )
    clock.value = int(old_claim["claim_expires_ts_ms"]) + 1

    claimed = store.claim_pending_review_continuations(
        worker_id="new-review-worker",
        lease_ms=30_000,
        limit=1,
    )

    assert claimed == ()
    with sqlite3.connect(str(path)) as conn:
        durable = conn.execute(
            """
            SELECT claim_owner, claim_fencing_token
            FROM task_runtime_review_continuation
            WHERE review_id = ?
            """,
            (review.review_id,),
        ).fetchone()
    assert durable == (
        "old-review-worker",
        old_claim["claim_fencing_token"],
    )


def test_secret_free_authority_rejects_every_unknown_field(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _insert_checkpoint(path, node_status="WAITING_APPROVAL", node_version=1)
    clock = _Clock()
    reviews = ReviewStore(path, clock=clock)
    review = _review(
        "review-authority-unknown-field",
        phase=ReviewResumePhase.PRE_EXECUTION,
        subject_version=1,
    )
    _create(reviews, review)
    _consume(reviews, review)
    store = ReviewContinuationStore(path, clock=clock)
    claim = store.claim_pending_review_continuations(
        worker_id="review-worker",
        lease_ms=30_000,
        limit=1,
    )[0]
    authority = dict(store.validate_review_continuation_claim(claim))
    authority["future_untrusted_authority"] = "must-not-be-ignored"

    with pytest.raises(ReviewContinuationStoreError) as rejected:
        store.validate_review_continuation_authority(authority)

    assert rejected.value.reason_code == REVIEW_CONTINUATION_CLAIM_LOST


def test_post_review_requires_its_exact_succeeded_attempt_and_never_replays_it(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _insert_checkpoint(
        path,
        node_status="WAITING_ACCEPTANCE_REVIEW",
        node_version=6,
        attempt_id="attempt-post-1",
    )
    reviews = ReviewStore(path, clock=_Clock())
    review = _review(
        "review-post-continuation",
        phase=ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
        subject_version=6,
        attempt_id="attempt-post-1",
    )
    _create(reviews, review)

    resumed = _consume(reviews, review)

    assert resumed["continuation"]["resume_phase"] == "POST_EXECUTION_ACCEPTANCE"
    with sqlite3.connect(str(path)) as conn:
        assert conn.execute(
            "SELECT attempt_id, status FROM task_runtime_attempt"
        ).fetchall() == [("attempt-post-1", "SUCCEEDED")]


def test_resume_and_enqueue_roll_back_together_on_coordination_insert_failure(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _insert_checkpoint(path, node_status="WAITING_APPROVAL", node_version=1)
    reviews = ReviewStore(path, clock=_Clock())
    review = _review(
        "review-atomic-rollback",
        phase=ReviewResumePhase.PRE_EXECUTION,
        subject_version=1,
    )
    _create(reviews, review)
    approved = reviews.approve_review(review.review_id, actor_id="reviewer")
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            CREATE TRIGGER abort_review_continuation_insert
            BEFORE INSERT ON task_runtime_review_continuation
            BEGIN
                SELECT RAISE(ABORT, 'injected continuation failure');
            END
            """
        )

    with pytest.raises(ReviewStoreError):
        reviews.resume_review(
            review.review_id,
            resume_token_ref=approved["resume_token_ref"],
            subject_ref=review.subject_ref,
            subject_version=review.subject_version,
            resume_phase=review.resume_phase,
            checkpoint_id=str(review.checkpoint_id),
        )

    assert reviews.get_review(review.review_id)["review"]["status"] == "APPROVED"
    with sqlite3.connect(str(path)) as conn:
        assert conn.execute(
            "SELECT state FROM agent_runtime_review_token WHERE review_id = ?",
            (review.review_id,),
        ).fetchone() == ("AVAILABLE",)


def test_startup_reconciliation_recovers_missing_terminal_queue_and_expiry(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _insert_checkpoint(path, node_status="WAITING_APPROVAL", node_version=1)
    clock = _Clock()
    reviews = ReviewStore(path, clock=clock)
    review = _review(
        "review-reconcile",
        phase=ReviewResumePhase.PRE_EXECUTION,
        subject_version=1,
    )
    _create(reviews, review)
    _consume(reviews, review)
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            "DELETE FROM task_runtime_review_continuation WHERE review_id = ?",
            (review.review_id,),
        )

    store = ReviewContinuationStore(path, clock=clock)
    result = store.reconcile_terminal_reviews()

    assert result["enqueued_count"] == 1
    assert store.get_review_continuation(review.review_id) is not None


def test_expire_due_enqueues_denial_delivery_for_a_waiting_task_node(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _insert_checkpoint(path, node_status="WAITING_APPROVAL", node_version=1)
    clock = _Clock(1_000)
    reviews = ReviewStore(path, clock=clock)
    review = _review(
        "review-expire-continuation",
        phase=ReviewResumePhase.PRE_EXECUTION,
        subject_version=1,
        expires_at=2_000,
    )
    _create(reviews, review)
    clock.value = 2_000

    expired = reviews.expire_due()

    assert expired["expired_review_ids"] == (review.review_id,)
    continuation = ReviewContinuationStore(path, clock=clock).get_review_continuation(
        review.review_id
    )
    assert continuation is not None
    assert continuation["review"]["status"] == "EXPIRED"


def _post_downstream_execution_authority(
    tmp_path: Path,
) -> tuple[Path, _Clock, ReviewContinuationStore, dict, dict]:
    path = _db(tmp_path)
    clock = _Clock()
    _insert_checkpoint(
        path,
        node_status="WAITING_ACCEPTANCE_REVIEW",
        node_version=6,
        attempt_id="attempt-post-reviewed",
    )
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        register_task_graph_admission_authority(conn)
        with canonical_task_graph_admission_scope("graph-review", 1):
            conn.execute(
                """
                INSERT INTO task_runtime_node(
                    graph_id, graph_revision, node_id, status, status_version,
                    node_json, cancellation_requested_ts_ms,
                    created_ts_ms, updated_ts_ms
                ) VALUES ('graph-review', 1, 'node-downstream', 'RUNNING', 3,
                          '{}', NULL, 1000, 1000)
                """
            )
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms, acquired_ts_ms,
                started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
            ) VALUES ('attempt-post-downstream', 'graph-review', 1,
                      'node-downstream', 1, 'RUNNING', 2, 'downstream-worker',
                      'scope', 'fingerprint', 1000, 1000, 1000, NULL, 1000, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO task_runtime_lease(
                lease_id, attempt_id, owner_id, fencing_token,
                acquired_ts_ms, heartbeat_ts_ms, expires_ts_ms,
                released_ts_ms, status, status_version
            ) VALUES ('lease-post-downstream', 'attempt-post-downstream',
                      'downstream-worker', 7, 1000, 1000, 50000,
                      NULL, 'ACTIVE', 0)
            """
        )
    reviews = ReviewStore(path, clock=clock)
    review = _review(
        "review-post-execution-authority",
        phase=ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
        subject_version=6,
        attempt_id="attempt-post-reviewed",
    )
    _create(reviews, review)
    _consume(reviews, review)
    store = ReviewContinuationStore(path, clock=clock)
    claim = store.claim_pending_review_continuations(
        worker_id="post-review-worker",
        lease_ms=30_000,
        limit=1,
    )[0]
    authority = dict(store.validate_review_continuation_claim(claim))
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = 'ACCEPTED', status_version = status_version + 1,
                updated_ts_ms = 1001
            WHERE graph_id = 'graph-review' AND graph_revision = 1
              AND node_id = 'node-review'
            """
        )
    execution = {
        "attempt_id": "attempt-post-downstream",
        "lease_id": "lease-post-downstream",
        "lease_owner_id": "downstream-worker",
        "fencing_token": 7,
        "node_id": "node-downstream",
    }
    return path, clock, store, authority, execution


@pytest.mark.parametrize(
    "drift",
    (
        "forged_attempt",
        "forged_lease",
        "wrong_owner",
        "wrong_fence",
        "terminal_attempt",
        "released_lease",
        "expired_lease",
    ),
)
def test_post_authority_rejects_forged_or_stale_current_execution(
    tmp_path: Path,
    drift: str,
) -> None:
    path, clock, store, authority, execution = _post_downstream_execution_authority(
        tmp_path
    )
    if drift == "forged_attempt":
        execution["attempt_id"] = "attempt-post-forged"
    elif drift == "forged_lease":
        execution["lease_id"] = "lease-post-forged"
    elif drift == "wrong_owner":
        execution["lease_owner_id"] = "other-worker"
    elif drift == "wrong_fence":
        execution["fencing_token"] = 8
    else:
        with sqlite3.connect(str(path)) as conn:
            if drift == "terminal_attempt":
                conn.execute(
                    """
                    UPDATE task_runtime_attempt
                    SET status = 'SUCCEEDED', status_version = status_version + 1,
                        completed_ts_ms = 1000, updated_ts_ms = 1000
                    WHERE attempt_id = 'attempt-post-downstream'
                    """
                )
            elif drift == "released_lease":
                conn.execute(
                    """
                    UPDATE task_runtime_lease
                    SET status = 'RELEASED', status_version = status_version + 1,
                        released_ts_ms = 1000
                    WHERE lease_id = 'lease-post-downstream'
                    """
                )
            elif drift == "expired_lease":
                conn.execute(
                    """
                    UPDATE task_runtime_lease
                    SET expires_ts_ms = ?
                    WHERE lease_id = 'lease-post-downstream'
                    """,
                    (clock.value,),
                )

    with pytest.raises(ReviewContinuationStoreError) as rejected:
        store.validate_review_continuation_execution_authority(
            authority,
            **execution,
        )

    assert rejected.value.reason_code == REVIEW_CONTINUATION_CLAIM_LOST


def test_post_authority_allows_exact_active_downstream_attempt_and_lease(
    tmp_path: Path,
) -> None:
    _path, _clock, store, authority, execution = (
        _post_downstream_execution_authority(tmp_path)
    )

    validated = store.validate_review_continuation_execution_authority(
        authority,
        **execution,
    )

    assert validated == authority
