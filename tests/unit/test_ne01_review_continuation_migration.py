from __future__ import annotations

import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.review_continuation_store import (
    REVIEW_CONTINUATION_SCHEMA_NOT_READY,
    ReviewContinuationStore,
    ReviewContinuationStoreError,
)
from host_brain.persistence.task_graph_admission_authority import (
    canonical_task_graph_admission_scope,
    register_task_graph_admission_authority,
)


def test_0096_fresh_schema_is_coordination_only_and_checksum_replays(tmp_path) -> None:
    db_path = tmp_path / "0096-review-continuation.sqlite3"
    first = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0096-review-continuation-first",
        ts_ms=1_000,
    )

    assert first.success is True, first.as_dict()
    assert first.summary["current_version"] == first.summary["latest_version"]
    assert first.summary["current_version"] >= 96
    assert any(
        record.migration_id == "0096_task_runtime_review_continuation"
        for record in first.records
    )
    with sqlite3.connect(str(db_path)) as conn:
        columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(task_runtime_review_continuation)"
            ).fetchall()
        }
        indexes = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA index_list(task_runtime_review_continuation)"
            ).fetchall()
        }
        foreign_keys = {
            (str(row[2]), str(row[3]), str(row[4]))
            for row in conn.execute(
                "PRAGMA foreign_key_list(task_runtime_review_continuation)"
            ).fetchall()
        }

    assert {
        "review_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "subject_version",
        "resume_phase",
        "claim_owner",
        "claim_token",
        "claim_expires_ts_ms",
        "claim_fencing_token",
        "attempt_count",
        "available_ts_ms",
        "last_error",
        "status_version",
        "created_ts_ms",
        "updated_ts_ms",
    } == columns
    assert "status" not in columns
    assert "review_status" not in columns
    assert {
        "uq_task_runtime_review_continuation_checkpoint",
        "idx_task_runtime_review_continuation_ready",
    } <= indexes
    assert (
        "agent_runtime_review",
        "review_id",
        "review_id",
    ) in foreign_keys

    replay = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0096-review-continuation-replay",
        ts_ms=1_001,
    )
    assert replay.success is True, replay.as_dict()
    assert replay.summary["current_version"] == first.summary["current_version"]
    assert replay.summary["latest_version"] == first.summary["latest_version"]


def test_0096_rejects_partial_claim_and_fence_attempt_mismatch(tmp_path) -> None:
    db_path = tmp_path / "0096-review-continuation-guards.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0096-review-continuation-guards",
        ts_ms=1_000,
    )
    assert result.success is True, result.as_dict()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        register_task_graph_admission_authority(conn)
        with canonical_task_graph_admission_scope("graph-guard", 1):
            conn.execute(
                """
                INSERT INTO task_runtime_graph(
                    graph_id, revision, root_work_order_id, planner_ref, status,
                    status_version, graph_json, source_kind, source_ref,
                    legacy_snapshot_json, created_ts_ms, updated_ts_ms
                ) VALUES ('graph-guard', 1, 'work-guard', 'planner://guard',
                          'BLOCKED', 1, '{}', 'CANONICAL', NULL, NULL, 1000, 1000)
                """
            )
            conn.execute(
                """
                INSERT INTO task_runtime_node(
                    graph_id, graph_revision, node_id, status, status_version,
                    node_json, cancellation_requested_ts_ms,
                    created_ts_ms, updated_ts_ms
                ) VALUES ('graph-guard', 1, 'node-guard', 'WAITING_APPROVAL',
                          1, '{}', NULL, 1000, 1000)
                """
            )
        conn.execute(
            """
            INSERT INTO agent_runtime_review(
                review_id, schema_version, review_type, subject_ref,
                subject_version, requester, reason, risk_level,
                side_effects_json, evidence_refs_json, status, status_version,
                expires_at, resume_phase, diff_ref, checkpoint_id,
                decision_actor, token_ref, resume_count, resume_result_ref,
                work_order_id, task_graph_id, task_node_id, review_json,
                created_ts_ms, updated_ts_ms
            ) VALUES (
                'review-guard', 'Natsume.agent.review_item.v1',
                'PRE_EXECUTION_APPROVAL',
                'task-node://graph-guard/1/node-guard?root=work-guard&attempt=not-started',
                1, 'owner', 'guard', 3, '[]', '[]', 'REJECTED', 1,
                50000, 'PRE_EXECUTION', NULL,
                'task-checkpoint://graph-guard/1/node-guard/pre',
                'owner', NULL, 0, NULL, 'work-guard', 'graph-guard',
                'node-guard',
                '{"schema_version":"Natsume.agent.review_item.v1"}', 1000, 1000
            )
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO task_runtime_review_continuation(
                    review_id, graph_id, graph_revision, node_id,
                    subject_version, resume_phase, claim_owner, claim_token,
                    claim_expires_ts_ms, claim_fencing_token, attempt_count,
                    available_ts_ms, last_error, status_version,
                    created_ts_ms, updated_ts_ms
                ) VALUES ('review-guard', 'graph-guard', 1, 'node-guard', 1,
                          'PRE_EXECUTION', 'worker', NULL, 2000, 1, 1,
                          1000, NULL, 0, 1000, 1000)
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO task_runtime_review_continuation(
                    review_id, graph_id, graph_revision, node_id,
                    subject_version, resume_phase, claim_owner, claim_token,
                    claim_expires_ts_ms, claim_fencing_token, attempt_count,
                    available_ts_ms, last_error, status_version,
                    created_ts_ms, updated_ts_ms
                ) VALUES ('review-guard', 'graph-guard', 1, 'node-guard', 1,
                          'PRE_EXECUTION', NULL, NULL, NULL, 2, 1,
                          1000, NULL, 0, 1000, 1000)
                """
            )


def test_0096_readiness_rejects_extra_business_status_column(tmp_path) -> None:
    db_path = tmp_path / "0096-review-continuation-extra-status.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0096-extra-status",
        ts_ms=1_000,
    )
    assert result.success is True, result.as_dict()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "ALTER TABLE task_runtime_review_continuation ADD COLUMN status TEXT"
        )

    with pytest.raises(ReviewContinuationStoreError) as error:
        ReviewContinuationStore(db_path)

    assert error.value.reason_code == REVIEW_CONTINUATION_SCHEMA_NOT_READY


def test_0096_readiness_rejects_weakened_table_guard(tmp_path) -> None:
    db_path = tmp_path / "0096-review-continuation-weakened.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0096-weakened",
        ts_ms=1_000,
    )
    assert result.success is True, result.as_dict()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute(
            """
            UPDATE sqlite_master
            SET sql = replace(
                sql,
                'CHECK(length(trim(review_id)) > 0)',
                'CHECK(1)'
            )
            WHERE type = 'table'
              AND name = 'task_runtime_review_continuation'
            """
        )
        conn.execute("PRAGMA writable_schema = OFF")

    with pytest.raises(ReviewContinuationStoreError) as error:
        ReviewContinuationStore(db_path)

    assert error.value.reason_code == REVIEW_CONTINUATION_SCHEMA_NOT_READY
