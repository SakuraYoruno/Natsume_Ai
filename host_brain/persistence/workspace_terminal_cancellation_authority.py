"""Read-only cancellation proof for abandoning one Workspace TaskNode."""

from __future__ import annotations

from collections.abc import Mapping
import json
import sqlite3

from host_brain.contracts.agent_runtime.graph_cancellation import (
    GraphCancellationContractError,
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)


_WORKSPACE_TERMINAL_NODE_STATUSES = frozenset({"ACCEPTED", "CANCELLED"})


def finalized_cancellation_preserves_node_state(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    expected_status: str,
) -> bool:
    """Verify one finalized cancellation without acquiring mutation authority."""

    status = str(expected_status or "").strip().upper()
    if status not in _WORKSPACE_TERMINAL_NODE_STATUSES:
        return False
    rows = conn.execute(
        """
        SELECT intent.intent_ref, intent.status, intent.status_version,
               intent.payload_sha256 AS intent_payload_sha256,
               intent.payload_json AS intent_payload_json,
               decision.decision_ref,
               decision.payload_sha256 AS decision_payload_sha256,
               decision.payload_json AS decision_payload_json,
               graph.status AS current_graph_status,
               graph.status_version AS current_graph_status_version,
               node.status AS current_node_status,
               node.status_version AS current_node_status_version
        FROM task_runtime_graph_cancellation_intent AS intent
        JOIN task_runtime_graph_cancellation_decision AS decision
          ON decision.intent_ref = intent.intent_ref
         AND decision.graph_id = intent.graph_id
         AND decision.graph_revision = intent.graph_revision
        JOIN task_runtime_graph AS graph
          ON graph.graph_id = intent.graph_id
         AND graph.revision = intent.graph_revision
        JOIN task_runtime_node AS node
          ON node.graph_id = intent.graph_id
         AND node.graph_revision = intent.graph_revision
         AND node.node_id = ?
        WHERE intent.graph_id = ?
          AND intent.graph_revision = ?
        """,
        (node_id, graph_id, graph_revision),
    ).fetchall()
    row = rows[0] if len(rows) == 1 else None
    if (
        row is None
        or str(row["status"]) != "FINALIZED"
        or type(row["status_version"]) is not int
        or int(row["status_version"]) != 1
    ):
        return False
    try:
        intent = normalize_graph_cancellation_intent(
            json.loads(str(row["intent_payload_json"]))
        )
        decision = normalize_graph_cancellation_decision(
            json.loads(str(row["decision_payload_json"]))
        )
    except (
        GraphCancellationContractError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        return False
    intent_state = intent.payload["node_states_after"].get(node_id)
    decision_before = decision.payload["node_states_before"].get(node_id)
    decision_after = decision.payload["node_states_after"].get(node_id)
    current_node_state = {
        "status": str(row["current_node_status"]),
        "status_version": int(row["current_node_status_version"]),
    }
    return bool(
        str(row["intent_ref"]) == intent.intent_ref
        and str(row["intent_payload_sha256"]) == intent.payload_sha256
        and str(row["decision_ref"]) == decision.decision_ref
        and str(row["decision_payload_sha256"]) == decision.payload_sha256
        and decision.payload["intent_ref"] == intent.intent_ref
        and intent.payload["graph_id"] == graph_id
        and intent.payload["graph_revision"] == graph_revision
        and intent.payload["graph_target_status"] == "BLOCKED"
        and decision.payload["graph_id"] == graph_id
        and decision.payload["graph_revision"] == graph_revision
        and decision.payload["graph_target_status"] == "CANCELLED"
        and str(row["current_graph_status"]) == "CANCELLED"
        and int(row["current_graph_status_version"])
        == int(decision.payload["graph_status_version_before"]) + 1
        and isinstance(intent_state, Mapping)
        and intent_state.get("status") == status
        and decision_before == intent_state
        and decision_after == intent_state
        and current_node_state == intent_state
    )


def finalized_post_review_cancellation_allows_workspace_abandonment(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
) -> bool:
    """Prove a committed write was cancelled from Acceptance Review."""

    if not finalized_cancellation_preserves_node_state(
        conn,
        graph_id=graph_id,
        graph_revision=graph_revision,
        node_id=node_id,
        expected_status="CANCELLED",
    ):
        return False
    rows = conn.execute(
        """
        SELECT acceptance.acceptance_ref
        FROM task_runtime_node_acceptance_decision AS acceptance
        JOIN agent_runtime_review AS review
          ON review.review_id = acceptance.review_id
         AND review.review_type = 'POST_EXECUTION_ACCEPTANCE'
         AND review.status = 'CANCELLED'
         AND review.subject_version = acceptance.node_version_after
         AND review.task_graph_id = acceptance.graph_id
         AND review.task_node_id = acceptance.node_id
        JOIN task_runtime_idempotency_reservation AS operation
          ON operation.attempt_id = acceptance.attempt_id
         AND operation.status = 'COMMITTED'
         AND operation.receipt_ref IS NOT NULL
        WHERE acceptance.graph_id = ?
          AND acceptance.graph_revision = ?
          AND acceptance.node_id = ?
          AND acceptance.attempt_id = ?
          AND acceptance.target_status = 'WAITING_ACCEPTANCE_REVIEW'
          AND NOT EXISTS (
              SELECT 1
              FROM task_runtime_acceptance_effect_intent AS effect
              WHERE effect.graph_id = acceptance.graph_id
                AND effect.graph_revision = acceptance.graph_revision
                AND effect.node_id = acceptance.node_id
                AND effect.attempt_id = acceptance.attempt_id
          )
        """,
        (graph_id, graph_revision, node_id, attempt_id),
    ).fetchall()
    return len(rows) == 1


__all__ = [
    "finalized_cancellation_preserves_node_state",
    "finalized_post_review_cancellation_allows_workspace_abandonment",
]
