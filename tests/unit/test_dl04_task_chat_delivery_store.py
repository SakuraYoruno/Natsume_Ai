from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.task_chat_delivery_store import (
    TASK_CHAT_DELIVERY_FENCE_LOST,
    TASK_CHAT_DELIVERY_RECOVERY_REQUIRED,
    TASK_CHAT_DELIVERY_REPLAY_DRIFT,
    TaskChatDeliveryContext,
    TaskChatDeliveryStore,
    TaskChatDeliveryStoreError,
)
from host_brain.persistence.task_graph_admission_authority import (
    canonical_task_graph_admission_scope,
    register_task_graph_admission_authority,
)
from host_brain.task_runtime.chat_delivery_authority import (
    TaskBoundChatDeliveryAuthority,
    TaskBoundChatDeliveryAuthorityError,
)


def _migrated_database(tmp_path: Path) -> Path:
    db_path = tmp_path / "task-chat-delivery.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="test-task-chat-delivery"
    )
    assert result.success
    return db_path


def _insert_live_task_node(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        register_task_graph_admission_authority(conn)
        with canonical_task_graph_admission_scope("graph-chat-delivery", 1):
            conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref, status,
                status_version, graph_json, source_kind, source_ref,
                legacy_snapshot_json, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "graph-chat-delivery",
                1,
                "work-order-chat-delivery",
                "planner://test",
                "ACTIVE",
                0,
                "{}",
                "CANONICAL",
                "test-task-chat-delivery",
                None,
                1,
                1,
            ),
        )
            conn.execute(
            """
            INSERT INTO task_runtime_node(
                graph_id, graph_revision, node_id, status, status_version,
                node_json, cancellation_requested_ts_ms, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "graph-chat-delivery",
                1,
                "node-chat-delivery",
                "RUNNING",
                0,
                "{}",
                None,
                1,
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms, acquired_ts_ms,
                started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "attempt-chat-delivery",
                "graph-chat-delivery",
                1,
                "node-chat-delivery",
                1,
                "RUNNING",
                0,
                "worker-chat-delivery",
                "chat-delivery",
                "sha256:" + "b" * 64,
                1,
                1,
                1,
                None,
                1,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_lease(
                lease_id, attempt_id, owner_id, fencing_token, acquired_ts_ms,
                heartbeat_ts_ms, expires_ts_ms, released_ts_ms, status, status_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "lease-chat-delivery",
                "attempt-chat-delivery",
                "worker-chat-delivery",
                3,
                1,
                1,
                10_000,
                None,
                "ACTIVE",
                0,
            ),
        )


def _insert_replacement_attempt(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        # A retry may only claim a replacement after the predecessor has
        # become non-live.  Its immutable Chat delivery rows deliberately
        # remain, which is the recovery boundary under test.
        conn.execute(
            "UPDATE task_runtime_lease SET status = 'EXPIRED', released_ts_ms = 2 "
            "WHERE lease_id = 'lease-chat-delivery'"
        )
        conn.execute(
            "UPDATE task_runtime_attempt SET status = 'FAILED', completed_ts_ms = 2, "
            "updated_ts_ms = 2 WHERE attempt_id = 'attempt-chat-delivery'"
        )
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms, acquired_ts_ms,
                started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "attempt-chat-delivery-replacement",
                "graph-chat-delivery",
                1,
                "node-chat-delivery",
                2,
                "RUNNING",
                0,
                "worker-chat-delivery-replacement",
                "chat-delivery",
                "sha256:" + "c" * 64,
                2,
                2,
                2,
                None,
                2,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_lease(
                lease_id, attempt_id, owner_id, fencing_token, acquired_ts_ms,
                heartbeat_ts_ms, expires_ts_ms, released_ts_ms, status, status_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "lease-chat-delivery-replacement",
                "attempt-chat-delivery-replacement",
                "worker-chat-delivery-replacement",
                4,
                2,
                2,
                10_000,
                None,
                "ACTIVE",
                0,
            ),
        )


def _context() -> TaskChatDeliveryContext:
    return TaskChatDeliveryContext(
        graph_id="graph-chat-delivery",
        graph_revision=1,
        node_id="node-chat-delivery",
        attempt_id="attempt-chat-delivery",
        lease_id="lease-chat-delivery",
        lease_owner_id="worker-chat-delivery",
        fencing_token=3,
        node_grant_ref="governor-grant://child/chat-delivery",
        request_id="request-chat-delivery",
        session_id="session-chat-delivery",
        principal_id="person-chat-delivery",
        self_id="natsume_ai",
        relationship_subject_id="person-chat-delivery",
        workspace_id="workspace-chat-delivery",
        plan_id="affective-plan-chat-delivery",
        plan_digest="a" * 64,
        synchronization_id="sync-chat-delivery",
        self_affect_revision=7,
    )


def _plan() -> dict[str, object]:
    return {
        "schema_version": "affective_turn_plan.v1",
        "self_id": "natsume_ai",
        "relationship_subject_id": "person-chat-delivery",
        "workspace_id": "workspace-chat-delivery",
        "session_id": "session-chat-delivery",
        "plan_id": "affective-plan-chat-delivery",
        "self_affect_revision": 7,
        "expression_plan": {
            "synchronization_id": "sync-chat-delivery",
            "self_affect_revision": 7,
        },
    }


def test_delivery_ledger_is_exact_replay_immutable_and_scope_fenced(tmp_path: Path) -> None:
    db_path = _migrated_database(tmp_path)
    _insert_live_task_node(db_path)
    store = TaskChatDeliveryStore(db_path, clock=lambda: 100)
    context = _context()

    plan_receipt = store.stage_plan(context)
    staged = store.stage_sentence(context, sentence="我在，慢慢说。", sentence_index=0)
    replayed = store.stage_sentence(context, sentence="我在，慢慢说。", sentence_index=0)

    assert plan_receipt["event_kind"] == "PLAN_FROZEN"
    assert staged["delivery_status"] == "TASK_NODE_SENTENCE_STAGED"
    assert staged["self_id"] == "natsume_ai"
    assert staged["relationship_subject_id"] == context.relationship_subject_id
    assert staged["lease_id"] == context.lease_id
    assert staged["node_grant_ref"] == context.node_grant_ref
    assert replayed["delivery_id"] == staged["delivery_id"]
    assert replayed["replayed"] is True
    assert store.list_sentences_for_request(
        request_id=context.request_id,
        session_id=context.session_id,
        principal_id=context.principal_id,
        workspace_id=context.workspace_id,
    ) == [replayed]

    with pytest.raises(TaskChatDeliveryStoreError) as drift:
        store.stage_sentence(context, sentence="不同的句子。", sentence_index=0)
    assert drift.value.reason_code == TASK_CHAT_DELIVERY_REPLAY_DRIFT

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE task_runtime_chat_delivery_event SET sentence_text = 'tampered'"
            )
        conn.execute(
            "UPDATE task_runtime_lease SET status = 'EXPIRED', expires_ts_ms = 1 "
            "WHERE lease_id = 'lease-chat-delivery'"
        )

    with pytest.raises(TaskChatDeliveryStoreError) as fenced:
        store.stage_sentence(context, sentence="不能在过期租约写入。", sentence_index=1)
    assert fenced.value.reason_code == TASK_CHAT_DELIVERY_FENCE_LOST
    interrupted = store.record_stream_control(
        context,
        event_kind="STREAM_INTERRUPTED",
        sent_sentence_count=1,
        reason_code="TEST_INTERRUPT",
    )
    assert interrupted["event_kind"] == "STREAM_INTERRUPTED"


def test_delivery_plan_cannot_be_reopened_under_a_different_lease(
    tmp_path: Path,
) -> None:
    """A recovery owner must reconcile, never regenerate visible text."""

    db_path = _migrated_database(tmp_path)
    _insert_live_task_node(db_path)
    store = TaskChatDeliveryStore(db_path, clock=lambda: 100)
    context = _context()
    store.stage_plan(context)

    changed_fence = TaskChatDeliveryContext(
        **{
            **context.identity_payload(),
            "lease_id": "lease-chat-delivery-reacquired",
            "lease_owner_id": "worker-chat-delivery-reacquired",
            "fencing_token": 4,
        }
    )
    with pytest.raises(TaskChatDeliveryStoreError) as raised:
        store.stage_plan(changed_fence)

    assert raised.value.reason_code == TASK_CHAT_DELIVERY_REPLAY_DRIFT


def test_replacement_attempt_must_reconcile_prior_delivery_before_model_rerun(
    tmp_path: Path,
) -> None:
    """A new attempt cannot turn a visible prefix into a second provider call."""

    db_path = _migrated_database(tmp_path)
    _insert_live_task_node(db_path)
    store = TaskChatDeliveryStore(db_path, clock=lambda: 100)
    context = _context()
    store.stage_plan(context)
    store.stage_sentence(context, sentence="这句话已经可见。", sentence_index=0)
    _insert_replacement_attempt(db_path)
    replacement = TaskChatDeliveryContext(
        **{
            **context.identity_payload(),
            "attempt_id": "attempt-chat-delivery-replacement",
            "lease_id": "lease-chat-delivery-replacement",
            "lease_owner_id": "worker-chat-delivery-replacement",
            "fencing_token": 4,
        }
    )

    with pytest.raises(TaskChatDeliveryStoreError) as raised:
        store.stage_plan(replacement)

    assert raised.value.reason_code == TASK_CHAT_DELIVERY_RECOVERY_REQUIRED


def test_task_bound_authority_rejects_reentered_plan_before_provider_work(
    tmp_path: Path,
) -> None:
    db_path = _migrated_database(tmp_path)
    _insert_live_task_node(db_path)
    store = TaskChatDeliveryStore(db_path, clock=lambda: 100)
    frozen = SimpleNamespace(
        route="chat",
        graph_id="graph-chat-delivery",
        graph_revision=1,
        node_id="node-chat-delivery",
        attempt_id="attempt-chat-delivery",
        lease_id="lease-chat-delivery",
        lease_owner_id="worker-chat-delivery",
        fencing_token=3,
        node_grant_ref="governor-grant://child/chat-delivery",
        request_id="request-chat-delivery",
        session_id="session-chat-delivery",
        principal_id="person-chat-delivery",
    )
    authority = TaskBoundChatDeliveryAuthority(
        store=store,
        frozen=frozen,
        workspace_id="workspace-chat-delivery",
        relationship_subject_id="person-chat-delivery",
    )
    authority.stage_plan(_plan())

    with pytest.raises(TaskChatDeliveryStoreError) as raised:
        authority.stage_plan(_plan())

    assert raised.value.reason_code == TASK_CHAT_DELIVERY_RECOVERY_REQUIRED


def test_task_bound_authority_rejects_cross_subject_plan_before_visibility(
    tmp_path: Path,
) -> None:
    db_path = _migrated_database(tmp_path)
    _insert_live_task_node(db_path)
    store = TaskChatDeliveryStore(db_path, clock=lambda: 100)
    frozen = SimpleNamespace(
        route="chat",
        graph_id="graph-chat-delivery",
        graph_revision=1,
        node_id="node-chat-delivery",
        attempt_id="attempt-chat-delivery",
        lease_id="lease-chat-delivery",
        lease_owner_id="worker-chat-delivery",
        fencing_token=3,
        node_grant_ref="governor-grant://child/chat-delivery",
        request_id="request-chat-delivery",
        session_id="session-chat-delivery",
        principal_id="person-chat-delivery",
    )
    authority = TaskBoundChatDeliveryAuthority(
        store=store,
        frozen=frozen,
        workspace_id="workspace-chat-delivery",
        relationship_subject_id="person-chat-delivery",
    )

    receipt = authority.stage_plan(_plan())
    assert receipt["self_id"] == "natsume_ai"
    assert receipt["workspace_id"] == "workspace-chat-delivery"

    cross_subject = _plan()
    cross_subject["relationship_subject_id"] = "other-person"
    with pytest.raises(TaskBoundChatDeliveryAuthorityError):
        authority.stage_sentence(
            plan=cross_subject,
            sentence="不应该可见。",
            sentence_index=0,
        )
