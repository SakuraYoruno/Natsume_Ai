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
    finalized_cancellation_preserves_accepted_node,
)


def _authority_database() -> sqlite3.Connection:
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
        """
    )
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
            "node_states_before": {
                "node-result": {
                    "status": "WAITING_ACCEPTANCE_REVIEW",
                    "status_version": 6,
                },
                "node-write": {"status": "ACCEPTED", "status_version": 7},
            },
            "node_states_after": {
                "node-result": {"status": "CANCELLED", "status_version": 7},
                "node-write": {"status": "ACCEPTED", "status_version": 7},
            },
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
    return conn


def test_exact_finalized_cancellation_preserves_accepted_writer() -> None:
    conn = _authority_database()
    try:
        assert finalized_cancellation_preserves_accepted_node(
            conn,
            graph_id="graph-plan-workspace",
            graph_revision=3,
            node_id="node-write",
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
    ),
)
def test_tampered_or_incomplete_cancellation_is_not_cleanup_authority(
    mutation: str,
) -> None:
    conn = _authority_database()
    try:
        conn.execute(mutation)
        assert not finalized_cancellation_preserves_accepted_node(
            conn,
            graph_id="graph-plan-workspace",
            graph_revision=3,
            node_id="node-write",
        )
    finally:
        conn.close()
