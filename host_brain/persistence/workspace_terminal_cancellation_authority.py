"""Read-only proof for abandoning an accepted Workspace node after cancellation."""

from __future__ import annotations

from collections.abc import Mapping
import json
import sqlite3

from host_brain.contracts.agent_runtime.graph_cancellation import (
    GraphCancellationContractError,
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)


def finalized_cancellation_preserves_accepted_node(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
) -> bool:
    """Verify one finalized cancellation without acquiring mutation authority."""

    row = conn.execute(
        """
        SELECT intent.intent_ref, intent.status, intent.status_version,
               intent.payload_sha256 AS intent_payload_sha256,
               intent.payload_json AS intent_payload_json,
               decision.decision_ref,
               decision.payload_sha256 AS decision_payload_sha256,
               decision.payload_json AS decision_payload_json
        FROM task_runtime_graph_cancellation_intent AS intent
        JOIN task_runtime_graph_cancellation_decision AS decision
          ON decision.intent_ref = intent.intent_ref
         AND decision.graph_id = intent.graph_id
         AND decision.graph_revision = intent.graph_revision
        WHERE intent.graph_id = ?
          AND intent.graph_revision = ?
        """,
        (graph_id, graph_revision),
    ).fetchone()
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
        and isinstance(intent_state, Mapping)
        and intent_state.get("status") == "ACCEPTED"
        and decision_before == intent_state
        and decision_after == intent_state
    )


__all__ = ["finalized_cancellation_preserves_accepted_node"]
