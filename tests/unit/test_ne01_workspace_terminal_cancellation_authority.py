from __future__ import annotations

import sqlite3

import pytest

from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)
from host_brain.persistence.workspace_terminal_cancellation_authority import (
    finalized_cancellation_preserves_node_state,
    finalized_post_review_cancellation_allows_workspace_abandonment,
)


def _authority_database(
    *,
    node_status: str = "ACCEPTED",
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE task_runtime_graph_cancellation_intent(
            intent_ref TEXT,
            status TEXT,
            status_version INTEGER,
            payload_sha256 TEXT,
            payload_json TEXT,
            graph_id TEXT,
            graph_revision INTEGER
        );
        CREATE TABLE task_runtime_graph_cancellation_decision(
            decision_ref TEXT,
            intent_ref TEXT,
            payload_sha256 TEXT,
            payload_json TEXT,
            graph_id TEXT,
            graph_revision INTEGER
        );
        CREATE TABLE task_runtime_graph(
            graph_id TEXT,
            revision INTEGER,
            status TEXT,
            status_version INTEGER
        );
        CREATE TABLE task_runtime_node(
            graph_id TEXT,
            graph_revision INTEGER,
            node_id TEXT,
            status TEXT,
            status_version INTEGER
        );
        CREATE TABLE task_runtime_node_acceptance_decision(
            acceptance_ref TEXT,
            graph_id TEXT,
            graph_revision INTEGER,
            node_id TEXT,
            attempt_id TEXT,
            node_version_after INTEGER,
            target_status TEXT,
            review_id TEXT
        );
        CREATE TABLE agent_runtime_review(
            review_id TEXT,
            review_type TEXT,
            status TEXT,
            subject_version INTEGER,
            task_graph_id TEXT,
            task_node_id TEXT
        );
        CREATE TABLE task_runtime_idempotency_reservation(
            attempt_id TEXT,
            status TEXT,
            receipt_ref TEXT
        );
        CREATE TABLE task_runtime_acceptance_effect_intent(
            graph_id TEXT,
            graph_revision INTEGER,
            node_id TEXT,
            attempt_id TEXT
        );
        """
    )
    terminal_status = str(node_status).upper()
    if terminal_status == "ACCEPTED":
        node_states_before = {
            "node-result": {
                "status": "WAITING_ACCEPTANCE_REVIEW",
                "status_version": 6,
            },
            "node-write": {"status": "ACCEPTED", "status_version": 7},
        }
        node_states_after = {
            "node-result": {"status": "CANCELLED", "status_version": 7},
            "node-write": {"status": "ACCEPTED", "status_version": 7},
        }
        current_version = 7
    else:
        assert terminal_status == "CANCELLED"
        node_states_before = {
            "node-write": {
                "status": "WAITING_ACCEPTANCE_REVIEW",
                "status_version": 6,
            },
        }
        node_states_after = {
            "node-write": {"status": "CANCELLED", "status_version": 7},
        }
        current_version = 7
    intent = normalize_graph_cancellation_intent(
        {
            "schema_version": GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
            "request_id": "request-plan-workspace-cancel",
            "request_identity": "secure-ingress://audit/plan-workspace-cancel",
            "source": "OPERATOR",
            "reason_code": "OPERATOR_REQUEST",
            "graph_id": "graph-plan-workspace",
            "graph_revision": 3,
            "graph_status_before": "BLOCKED",
            "graph_status_version_before": 8,
            "graph_target_status": "BLOCKED",
            "budget_id": None,
            "budget_status_before": None,
            "budget_status_version_before": None,
            "node_states_before": node_states_before,
            "node_states_after": node_states_after,
            "graph_outbox_id": "outbox://plan-workspace/cancel-requested",
        }
    )
    decision = normalize_graph_cancellation_decision(
        {
            "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
            "intent_ref": intent.intent_ref,
            "request_id": "request-plan-workspace-cancel",
            "request_identity": "secure-ingress://audit/plan-workspace-cancel",
            "graph_id": "graph-plan-workspace",
            "graph_revision": 3,
            "graph_status_before": "BLOCKED",
            "graph_status_version_before": 9,
            "graph_target_status": "CANCELLED",
            "budget_id": None,
            "budget_status_before": None,
            "budget_status_version_before": None,
            "budget_target_status": None,
            "node_states_before": intent.payload["node_states_after"],
            "node_states_after": intent.payload["node_states_after"],
            "reconciliation_evidence_refs": [
                intent.intent_ref,
                "reconciliation://plan-workspace/cancelled",
            ],
            "graph_outbox_id": "outbox://plan-workspace/cancelled",
            "budget_outbox_id": None,
        }
    )
    conn.execute(
        """
        INSERT INTO task_runtime_graph_cancellation_intent
        VALUES (?, 'FINALIZED', 1, ?, ?, 'graph-plan-workspace', 3)
        """,
        (intent.intent_ref, intent.payload_sha256, intent.payload_json),
    )
    conn.execute(
        """
        INSERT INTO task_runtime_graph_cancellation_decision
        VALUES (?, ?, ?, ?, 'graph-plan-workspace', 3)
        """,
        (
            decision.decision_ref,
            intent.intent_ref,
            decision.payload_sha256,
            decision.payload_json,
        ),
    )
    conn.execute(
        """
        INSERT INTO task_runtime_graph
        VALUES ('graph-plan-workspace', 3, 'CANCELLED', 10)
        """
    )
    conn.execute(
        """
        INSERT INTO task_runtime_node
        VALUES ('graph-plan-workspace', 3, 'node-write', ?, ?)
        """,
        (terminal_status, current_version),
    )
    if terminal_status == "CANCELLED":
        conn.execute(
            """
            INSERT INTO task_runtime_node_acceptance_decision
            VALUES (
                'acceptance://direct-skill/review', 'graph-plan-workspace', 3,
                'node-write', 'attempt-direct-skill', 6,
                'WAITING_ACCEPTANCE_REVIEW', 'review-direct-skill'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO agent_runtime_review
            VALUES (
                'review-direct-skill', 'POST_EXECUTION_ACCEPTANCE', 'CANCELLED',
                6, 'graph-plan-workspace', 'node-write'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO task_runtime_idempotency_reservation
            VALUES (
                'attempt-direct-skill', 'COMMITTED',
                'worker-projection://direct-skill/review'
            )
            """
        )
    return conn


def test_exact_finalized_cancellation_preserves_accepted_writer() -> None:
    conn = _authority_database()
    try:
        assert finalized_cancellation_preserves_node_state(
            conn,
            graph_id="graph-plan-workspace",
            graph_revision=3,
            node_id="node-write",
            expected_status="ACCEPTED",
        )
    finally:
        conn.close()


def test_exact_finalized_cancellation_matches_cancelled_writer() -> None:
    conn = _authority_database(node_status="CANCELLED")
    try:
        assert finalized_cancellation_preserves_node_state(
            conn,
            graph_id="graph-plan-workspace",
            graph_revision=3,
            node_id="node-write",
            expected_status="CANCELLED",
        )
        assert not finalized_cancellation_preserves_node_state(
            conn,
            graph_id="graph-plan-workspace",
            graph_revision=3,
            node_id="node-write",
            expected_status="ACCEPTED",
        )
    finally:
        conn.close()


def test_post_review_cancellation_proves_committed_zero_effect_workspace() -> None:
    conn = _authority_database(node_status="CANCELLED")
    try:
        assert finalized_post_review_cancellation_allows_workspace_abandonment(
            conn,
            graph_id="graph-plan-workspace",
            graph_revision=3,
            node_id="node-write",
            attempt_id="attempt-direct-skill",
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "mutation",
    (
        "UPDATE agent_runtime_review SET status = 'PENDING'",
        "UPDATE agent_runtime_review SET subject_version = 7",
        "UPDATE task_runtime_idempotency_reservation SET status = 'RESERVED'",
        "UPDATE task_runtime_idempotency_reservation SET receipt_ref = NULL",
        "INSERT INTO task_runtime_acceptance_effect_intent "
        "VALUES ('graph-plan-workspace', 3, 'node-write', 'attempt-direct-skill')",
    ),
)
def test_post_review_cancellation_rejects_review_operation_or_effect_drift(
    mutation: str,
) -> None:
    conn = _authority_database(node_status="CANCELLED")
    try:
        conn.execute(mutation)
        assert not finalized_post_review_cancellation_allows_workspace_abandonment(
            conn,
            graph_id="graph-plan-workspace",
            graph_revision=3,
            node_id="node-write",
            attempt_id="attempt-direct-skill",
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "mutation",
    (
        "UPDATE task_runtime_graph_cancellation_intent "
        "SET payload_sha256 = 'sha256:' || printf('%064d', 0)",
        "UPDATE task_runtime_graph_cancellation_decision "
        "SET payload_sha256 = 'sha256:' || printf('%064d', 0)",
        "UPDATE task_runtime_graph_cancellation_decision "
        "SET decision_ref = 'taskcancel://sha256/' || printf('%064d', 0)",
        "UPDATE task_runtime_graph_cancellation_intent SET status_version = 2",
        "DELETE FROM task_runtime_graph_cancellation_decision",
        "UPDATE task_runtime_graph SET status_version = 11",
        "UPDATE task_runtime_node SET status_version = 8",
        "UPDATE task_runtime_node SET status = 'CANCELLED'",
    ),
)
def test_tampered_or_incomplete_cancellation_is_not_cleanup_authority(
    mutation: str,
) -> None:
    conn = _authority_database()
    try:
        conn.execute(mutation)
        assert not finalized_cancellation_preserves_node_state(
            conn,
            graph_id="graph-plan-workspace",
            graph_revision=3,
            node_id="node-write",
            expected_status="ACCEPTED",
        )
    finally:
        conn.close()
