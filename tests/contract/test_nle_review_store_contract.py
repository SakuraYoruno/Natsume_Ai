from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

import pytest

from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
)
from host_brain.persistence.review_repository import (
    REVIEW_GRAPH_CANCELLATION_CONFLICT,
    REVIEW_RESUME_ALREADY_CONSUMED,
    REVIEW_STORE_INVALID,
    REVIEW_STORE_SCHEMA_NOT_READY,
    REVIEW_TOKEN_EXPIRED,
    REVIEW_TOKEN_INVALID,
    REVIEW_TOKEN_SUBJECT_MISMATCH,
    ReviewStore,
    ReviewStoreError,
)
from host_brain.persistence.task_graph_admission_authority import (
    canonical_task_graph_admission_scope,
    register_task_graph_admission_authority,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.agent_runtime.workspace_policy import WorkspacePolicy, WorkspaceResolution


ROOT = Path(__file__).resolve().parents[2]
REVIEW_STORE_MIGRATION = ROOT / "host_brain" / "audit" / "migrations" / "0055_agent_runtime_review_store.sql"


@dataclass
class MutableClock:
    value: int = 1_000

    def __call__(self) -> int:
        return self.value

    def set(self, value: int) -> None:
        self.value = int(value)


def _apply_review_migration(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(REVIEW_STORE_MIGRATION.read_text(encoding="utf-8"))


def _store(tmp_path: Path, clock: MutableClock | None = None) -> ReviewStore:
    db_path = tmp_path / "review-store.sqlite3"
    _apply_review_migration(db_path)
    return ReviewStore(db_path, clock=clock or MutableClock())


def _store_with_cancellation_schema(
    tmp_path: Path,
    clock: MutableClock | None = None,
) -> ReviewStore:
    db_path = tmp_path / "review-cancellation-store.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="migration-review-cancellation-fence",
        ts_ms=1_000,
    )
    assert result.success, result.as_dict()
    return ReviewStore(db_path, clock=clock or MutableClock())


def _execute_task_admission_insert(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    sql: str,
    parameters: tuple[object, ...],
) -> None:
    register_task_graph_admission_authority(conn)
    with canonical_task_graph_admission_scope(graph_id, graph_revision):
        conn.execute(sql, parameters)


def _insert_graph_cancellation(
    store: ReviewStore,
    *,
    graph_id: str,
    graph_revision: int,
) -> None:
    intent_ref = f"taskcancel-intent://test/{graph_id}/{graph_revision}"
    request_id = f"request-review-cancel-{graph_id}-{graph_revision}"
    outbox_id = f"outbox-review-cancel-{graph_id}-{graph_revision}"
    payload_json = json.dumps(
        {"graph_id": graph_id, "graph_revision": graph_revision},
        sort_keys=True,
        separators=(",", ":"),
    )
    payload_sha256 = "sha256:" + hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _execute_task_admission_insert(
            conn,
            graph_id=graph_id,
            graph_revision=graph_revision,
            sql="""
            INSERT OR IGNORE INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref,
                status, status_version, graph_json, source_kind, source_ref,
                legacy_snapshot_json, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, 'BLOCKED', 1, '{}', 'CANONICAL', NULL, NULL, 1000, 1000)
            """,
            parameters=(
                graph_id,
                graph_revision,
                f"work-order-{graph_id}-{graph_revision}",
                "planner://review-cancellation/test",
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_outbox(
                outbox_id, aggregate_type, aggregate_id, aggregate_version,
                graph_revision, event_type, payload_ref, evidence_refs_json, attempt_id,
                idempotency_key, status, available_ts_ms, created_ts_ms,
                published_ts_ms, status_version
            ) VALUES (?, 'TASK_GRAPH', ?, 1, ?, 'TASK_GRAPH_CANCELLATION_REQUESTED',
                      ?, '[]', NULL, ?, 'PENDING', 1000, 1000, NULL, 0)
            """,
            (
                outbox_id,
                graph_id,
                graph_revision,
                (
                    f"taskstore://event/TASK_GRAPH/"
                    f"{graph_id}@{graph_revision}/1"
                ),
                f"idempotency:{outbox_id}",
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_graph_cancellation_intent(
                intent_ref, schema_version, request_id, request_identity,
                source, reason_code, graph_id, graph_revision,
                graph_version_before, graph_version_after, budget_id,
                budget_version_at_request, payload_json, payload_sha256,
                graph_outbox_id, status, status_version,
                requested_ts_ms, finalized_ts_ms
            ) VALUES (?, ?, ?,
                      'principal://review/test', 'OPERATOR', 'OPERATOR_CANCELLED_TASK',
                      ?, ?, 0, 1, NULL, NULL, ?, ?, ?, 'REQUESTED', 0, 1000, NULL)
            """,
            (
                intent_ref,
                GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
                request_id,
                graph_id,
                graph_revision,
                payload_json,
                payload_sha256,
                outbox_id,
            ),
        )


def _insert_review_checkpoint(
    store: ReviewStore,
    *,
    graph_id: str,
    graph_revision: int,
) -> None:
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _execute_task_admission_insert(
            conn,
            graph_id=graph_id,
            graph_revision=graph_revision,
            sql="""
            INSERT OR IGNORE INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref,
                status, status_version, graph_json, source_kind, source_ref,
                legacy_snapshot_json, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, 'BLOCKED', 1, '{}', 'CANONICAL', NULL, NULL,
                      1000, 1000)
            """,
            parameters=(
                graph_id,
                graph_revision,
                f"work-order-{graph_id}-{graph_revision}",
                "planner://review/checkpoint",
            ),
        )
        _execute_task_admission_insert(
            conn,
            graph_id=graph_id,
            graph_revision=graph_revision,
            sql="""
            INSERT INTO task_runtime_node(
                graph_id, graph_revision, node_id, status, status_version,
                node_json, cancellation_requested_ts_ms,
                created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, 'node-1', 'WAITING_APPROVAL', 0, '{}', NULL,
                      1000, 1000)
            """,
            parameters=(graph_id, graph_revision),
        )


def _task_node_review(
    review_id: str,
    *,
    graph_id: str,
    graph_revision: int,
) -> ReviewItem:
    node_id = "node-1"
    subject_ref = (
        f"task-node://{quote(graph_id, safe='')}/{graph_revision}/"
        f"{quote(node_id, safe='')}"
        "?root=root-work-order&attempt=not-started"
    )
    return ReviewItem(
        review_id=review_id,
        review_type=ReviewType.PRE_EXECUTION_APPROVAL,
        subject_ref=subject_ref,
        subject_version=0,
        requester="agent-runtime",
        reason="TaskNode requires approval",
        risk_level=4,
        side_effects=("write_files",),
        evidence_refs=("evidence://review/task-node/1",),
        status=ReviewStatus.PENDING,
        expires_at=60_000,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
        checkpoint_id=(
            f"task-checkpoint://{quote(graph_id, safe='')}/{graph_revision}/"
            f"{quote(node_id, safe='')}/pre_execution"
        ),
    )


def _review(
    review_id: str,
    *,
    review_type: ReviewType = ReviewType.PRE_EXECUTION_APPROVAL,
    checkpoint_id: str = "checkpoint://pre/graph-1/node-1/v0",
    expires_at: int = 60_000,
) -> ReviewItem:
    resume_phase = (
        ReviewResumePhase.PRE_EXECUTION
        if review_type is ReviewType.PRE_EXECUTION_APPROVAL
        else ReviewResumePhase.POST_EXECUTION_ACCEPTANCE
    )
    return ReviewItem(
        review_id=review_id,
        review_type=review_type,
        subject_ref="work-order://graph-1/node-1",
        subject_version=3,
        requester="agent-runtime",
        reason="write side effect requires durable owner review",
        risk_level=4,
        side_effects=("write_files",),
        evidence_refs=("evidence://review/input/1",),
        status=ReviewStatus.PENDING,
        expires_at=expires_at,
        resume_phase=resume_phase,
        diff_ref="diff://workspace/pre/1",
        checkpoint_id=checkpoint_id,
    )


def test_t_nle_10_review_store_requires_migration_and_persists_after_restart(tmp_path: Path) -> None:
    empty_db = tmp_path / "empty.sqlite3"
    sqlite3.connect(str(empty_db)).close()
    with pytest.raises(ReviewStoreError) as missing:
        ReviewStore(empty_db)
    assert missing.value.reason_code == REVIEW_STORE_SCHEMA_NOT_READY

    clock = MutableClock(1_000)
    store = _store(tmp_path, clock)
    created = store.create_review(_review("review-pre-persist"))
    assert created["review"]["status"] == "PENDING"

    reopened = ReviewStore(store.db_path, clock=MutableClock(1_500))
    persisted = reopened.get_review("review-pre-persist")

    assert persisted["review"]["review_id"] == "review-pre-persist"
    assert persisted["review"]["checkpoint_id"] == "checkpoint://pre/graph-1/node-1/v0"
    assert persisted["status_version"] == 0


def test_t_nle_10_pre_resume_is_token_bound_and_one_time(tmp_path: Path) -> None:
    clock = MutableClock(1_000)
    store = _store(tmp_path, clock)
    store.create_review(_review("review-pre-once"))

    approved = store.approve_review(
        "review-pre-once",
        actor_id="owner",
        expected_subject_ref="work-order://graph-1/node-1",
        expected_subject_version=3,
        expected_resume_phase=ReviewResumePhase.PRE_EXECUTION,
        expected_checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
    )
    token = approved["resume_token_ref"]
    reopened = ReviewStore(store.db_path, clock=clock)
    resumed = reopened.resume_review(
        "review-pre-once",
        resume_token_ref=token,
        subject_ref="work-order://graph-1/node-1",
        subject_version=3,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
        checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
        resume_result_ref="review-resume://review-pre-once/pre/1",
    )

    assert resumed["resume_action"] == "START_EXECUTION"
    assert resumed["replay_side_effects"] is False
    assert resumed["review"]["review"]["status"] == "CONSUMED"
    assert resumed["review"]["review"]["resume_count"] == 1

    with pytest.raises(ReviewStoreError) as duplicate:
        reopened.resume_review(
            "review-pre-once",
            resume_token_ref=token,
            subject_ref="work-order://graph-1/node-1",
            subject_version=3,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
            resume_result_ref="review-resume://review-pre-once/pre/duplicate",
        )
    assert duplicate.value.reason_code == REVIEW_RESUME_ALREADY_CONSUMED


def test_operator_public_token_resume_is_separate_one_time_and_restart_safe(
    tmp_path: Path,
) -> None:
    clock = MutableClock(1_000)
    store = _store(tmp_path, clock)
    store.create_review(_review("review-operator-public-token"))
    approved = store.approve_review(
        "review-operator-public-token",
        actor_id="principal.operator",
    )
    bearer = str(approved["resume_token_ref"])
    public_ref = str(approved["resume_token"]["token_ref_public"])
    assert public_ref == approved["review"]["review"]["token_ref"]
    assert public_ref.startswith("review-token://")
    assert bearer.startswith("review_resume::")
    assert bearer.encode("utf-8") not in store.db_path.read_bytes()

    with pytest.raises(ReviewStoreError) as ordinary_api:
        store.resume_review(
            "review-operator-public-token",
            resume_token_ref=public_ref,
            subject_ref="work-order://graph-1/node-1",
            subject_version=3,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
        )
    assert ordinary_api.value.reason_code == REVIEW_TOKEN_INVALID

    reopened = ReviewStore(store.db_path, clock=clock)
    resumed = reopened.resume_review_by_operator_token_ref(
        "review-operator-public-token",
        token_ref_public=public_ref,
        subject_ref="work-order://graph-1/node-1",
        subject_version=3,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
        checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
        resume_result_ref=(
            "review-resume://operator/review-operator-public-token"
        ),
    )
    assert resumed["review"]["review"]["status"] == "CONSUMED"
    assert resumed["review"]["review"]["resume_count"] == 1

    restarted = ReviewStore(store.db_path, clock=clock)
    with pytest.raises(ReviewStoreError) as duplicate:
        restarted.resume_review_by_operator_token_ref(
            "review-operator-public-token",
            token_ref_public=public_ref,
            subject_ref="work-order://graph-1/node-1",
            subject_version=3,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
            resume_result_ref=(
                "review-resume://operator/review-operator-public-token"
            ),
        )
    assert duplicate.value.reason_code == REVIEW_RESUME_ALREADY_CONSUMED


def test_t_nle_10_expired_and_cross_subject_resume_are_rejected(tmp_path: Path) -> None:
    clock = MutableClock(1_000)
    store = _store(tmp_path, clock)
    store.create_review(_review("review-expiring", expires_at=2_000))
    expiring_token = store.approve_review("review-expiring", actor_id="owner")["resume_token_ref"]

    clock.set(2_000)
    with pytest.raises(ReviewStoreError) as expired:
        store.resume_review(
            "review-expiring",
            resume_token_ref=expiring_token,
            subject_ref="work-order://graph-1/node-1",
            subject_version=3,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
        )
    assert expired.value.reason_code == REVIEW_TOKEN_EXPIRED

    clock.set(3_000)
    store.create_review(_review("review-a", expires_at=60_000))
    store.create_review(_review("review-b", expires_at=60_000))
    token_a = store.approve_review("review-a", actor_id="owner")["resume_token_ref"]
    with pytest.raises(ReviewStoreError) as mismatch:
        store.resume_review(
            "review-b",
            resume_token_ref=token_a,
            subject_ref="work-order://graph-1/node-1",
            subject_version=3,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
        )
    assert mismatch.value.reason_code == REVIEW_TOKEN_SUBJECT_MISMATCH


def test_t_nle_10_post_resume_returns_checkpoint_without_side_effect_replay(tmp_path: Path) -> None:
    store = _store(tmp_path, MutableClock(1_000))
    store.create_review(
        _review(
            "review-post",
            review_type=ReviewType.POST_EXECUTION_ACCEPTANCE,
            checkpoint_id="checkpoint://post/graph-1/node-1/v4",
        )
    )
    token = store.approve_review(
        "review-post",
        actor_id="owner",
        expected_resume_phase=ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
        expected_checkpoint_id="checkpoint://post/graph-1/node-1/v4",
    )["resume_token_ref"]

    resumed = store.resume_review(
        "review-post",
        resume_token_ref=token,
        subject_ref="work-order://graph-1/node-1",
        subject_version=3,
        resume_phase=ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
        checkpoint_id="checkpoint://post/graph-1/node-1/v4",
        resume_result_ref="review-resume://review-post/post/1",
    )

    assert resumed["resume_action"] == "RETURN_POST_EXECUTION_CHECKPOINT"
    assert resumed["checkpoint_id"] == "checkpoint://post/graph-1/node-1/v4"
    assert resumed["replay_side_effects"] is False


def test_t_nle_10_task_node_review_remains_compatible_without_0092(tmp_path: Path) -> None:
    store = _store(tmp_path, MutableClock(1_000))
    review = _task_node_review(
        "review-task-node-legacy-schema",
        graph_id="graph-legacy-review",
        graph_revision=1,
    )

    created = store.create_review(
        review,
        task_graph_id="graph-legacy-review",
        task_node_id="node-1",
    )
    approved = store.approve_review(review.review_id, actor_id="owner")
    resumed = store.resume_review(
        review.review_id,
        resume_token_ref=approved["resume_token_ref"],
        subject_ref=review.subject_ref,
        subject_version=review.subject_version,
        resume_phase=review.resume_phase,
        checkpoint_id=str(review.checkpoint_id),
    )

    assert created["review"]["status"] == "PENDING"
    assert resumed["review"]["review"]["status"] == "CONSUMED"


def test_t_nle_10_list_reviews_filters_exact_encoded_graph_revision(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, MutableClock(1_000))
    graph_id = "graph/review scope"
    for revision in (1, 2):
        review = _task_node_review(
            f"review-revision-{revision}",
            graph_id=graph_id,
            graph_revision=revision,
        )
        store.create_review(
            review,
            task_graph_id=graph_id,
            task_node_id="node-1",
        )

    revision_one = store.list_reviews(
        task_graph_id=graph_id,
        graph_revision=1,
        limit=10,
    )

    assert [record["review"]["review_id"] for record in revision_one] == [
        "review-revision-1"
    ]
    assert revision_one[0]["review"]["subject_ref"].startswith(
        "task-node://graph%2Freview%20scope/1/"
    )


@pytest.mark.parametrize(
    ("task_graph_id", "graph_revision"),
    (
        (None, 1),
        ("graph-review-invalid", 0),
        ("graph-review-invalid", -1),
        ("graph-review-invalid", True),
        ("graph-review-invalid", "not-an-integer"),
    ),
)
def test_t_nle_10_list_reviews_rejects_invalid_revision_scope(
    tmp_path: Path,
    task_graph_id: str | None,
    graph_revision: object,
) -> None:
    store = _store(tmp_path, MutableClock(1_000))

    with pytest.raises(ReviewStoreError) as error:
        store.list_reviews(
            task_graph_id=task_graph_id,
            graph_revision=graph_revision,  # type: ignore[arg-type]
            limit=10,
        )

    assert error.value.reason_code == REVIEW_STORE_INVALID


def test_t_nle_10_graph_cancellation_fences_create_approve_and_resume(
    tmp_path: Path,
) -> None:
    store = _store_with_cancellation_schema(tmp_path, MutableClock(1_000))

    encoded_graph_id = "graph/create fenced"
    _insert_graph_cancellation(store, graph_id=encoded_graph_id, graph_revision=1)
    create_review = _task_node_review(
        "review-create-fenced",
        graph_id=encoded_graph_id,
        graph_revision=1,
    )
    with pytest.raises(ReviewStoreError) as create_error:
        store.create_review(
            create_review,
            task_graph_id=encoded_graph_id,
            task_node_id="node-1",
        )
    assert create_error.value.reason_code == REVIEW_GRAPH_CANCELLATION_CONFLICT

    approve_review = _task_node_review(
        "review-approve-fenced",
        graph_id="graph-approve-fenced",
        graph_revision=1,
    )
    _insert_review_checkpoint(
        store,
        graph_id="graph-approve-fenced",
        graph_revision=1,
    )
    store.create_review(
        approve_review,
        task_graph_id="graph-approve-fenced",
        task_node_id="node-1",
    )
    _insert_graph_cancellation(store, graph_id="graph-approve-fenced", graph_revision=1)
    with pytest.raises(ReviewStoreError) as approve_error:
        store.approve_review(approve_review.review_id, actor_id="owner")
    assert approve_error.value.reason_code == REVIEW_GRAPH_CANCELLATION_CONFLICT
    assert store.get_review(approve_review.review_id)["review"]["status"] == "PENDING"

    resume_review = _task_node_review(
        "review-resume-fenced",
        graph_id="graph-resume-fenced",
        graph_revision=1,
    )
    _insert_review_checkpoint(
        store,
        graph_id="graph-resume-fenced",
        graph_revision=1,
    )
    store.create_review(
        resume_review,
        task_graph_id="graph-resume-fenced",
        task_node_id="node-1",
    )
    approved = store.approve_review(resume_review.review_id, actor_id="owner")
    _insert_graph_cancellation(store, graph_id="graph-resume-fenced", graph_revision=1)
    with pytest.raises(ReviewStoreError) as resume_error:
        store.resume_review(
            resume_review.review_id,
            resume_token_ref=approved["resume_token_ref"],
            subject_ref=resume_review.subject_ref,
            subject_version=resume_review.subject_version,
            resume_phase=resume_review.resume_phase,
            checkpoint_id=str(resume_review.checkpoint_id),
        )
    assert resume_error.value.reason_code == REVIEW_GRAPH_CANCELLATION_CONFLICT
    assert store.get_review(resume_review.review_id)["review"]["status"] == "APPROVED"

    _insert_graph_cancellation(store, graph_id="graph-adjacent", graph_revision=1)
    adjacent_review = _task_node_review(
        "review-adjacent-revision",
        graph_id="graph-adjacent",
        graph_revision=2,
    )
    _insert_review_checkpoint(
        store,
        graph_id="graph-adjacent",
        graph_revision=2,
    )
    adjacent_created = store.create_review(
        adjacent_review,
        task_graph_id="graph-adjacent",
        task_node_id="node-1",
    )
    adjacent_approved = store.approve_review(adjacent_review.review_id, actor_id="owner")
    adjacent_resumed = store.resume_review(
        adjacent_review.review_id,
        resume_token_ref=adjacent_approved["resume_token_ref"],
        subject_ref=adjacent_review.subject_ref,
        subject_version=adjacent_review.subject_version,
        resume_phase=adjacent_review.resume_phase,
        checkpoint_id=str(adjacent_review.checkpoint_id),
    )

    assert adjacent_created["review"]["status"] == "PENDING"
    assert adjacent_resumed["review"]["review"]["status"] == "CONSUMED"


def test_t_nle_10_confirmed_true_cannot_bypass_declared_durable_review(tmp_path: Path) -> None:
    work_order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "wo-durable-review",
            "domain": "code",
            "run_mode": "worker",
            "objective": "write after durable review",
            "workspace": {"path": str(tmp_path)},
            "allowed_actions": ["write_files"],
            "forbidden_actions": ["modify_secret_files"],
            "approval_policy": {
                "require_confirm": True,
                "confirmed": True,
                "require_review_token": True,
            },
            "review_policy": {
                "review_required": True,
                "durable_review_required": True,
            },
        }
    )

    allowed, reason = WorkspacePolicy().enforce_write_policy(
        work_order,
        WorkspaceResolution(path=tmp_path, source="path"),
    )

    assert allowed is False
    assert reason is not None
    assert "confirmed=true is not sufficient" in reason
