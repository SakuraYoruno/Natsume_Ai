"""Strict cancellation-only reads used by terminal reconciliation."""

from __future__ import annotations

import sqlite3

from host_brain.persistence.task_record_codec import (
    _graph_cancellation_decision_record,
    _graph_cancellation_intent_record,
)
from host_brain.persistence.task_store_types import TaskStoreError

from .task_terminal_reconciliation_decision_authority import (
    canonical_parallel_terminal_decision,
)


def cancelled_effects_are_exactly_fenced(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    intent_ref: str,
) -> bool:
    """Accept only virgin Effects permanently fenced by this cancellation.

    A post-execution Review can leave an Internal Agent's acceptance effect in
    its original ``PENDING`` row.  It is not a live external effect only when
    the 0092 transaction recorded the same initial epoch as ``CANCELLED`` for
    the exact final intent.  Claimed, retried, or failed rows remain ambiguous
    and must continue through Effect reconciliation.
    """

    rows = conn.execute(
        """
        SELECT effect.effect_id, effect.status, effect.status_version,
               effect.attempt_count, effect.fencing_token,
               effect.reason_code, effect.last_error,
               fence.intent_ref AS fence_intent_ref,
               fence.observed_status, fence.observed_status_version,
               fence.resolution_status, fence.resolved_ts_ms
        FROM task_runtime_acceptance_effect_intent AS effect
        LEFT JOIN task_runtime_graph_cancellation_effect_fence AS fence
          ON fence.effect_id = effect.effect_id
         AND fence.intent_ref = ?
        WHERE effect.graph_id = ? AND effect.graph_revision = ?
        ORDER BY effect.effect_id
        """,
        (intent_ref, graph_id, graph_revision),
    ).fetchall()
    for row in rows:
        if (
            str(row["status"]) != "PENDING"
            or int(row["status_version"]) != 0
            or int(row["attempt_count"]) != 0
            or int(row["fencing_token"]) != 0
            or row["reason_code"] is not None
            or row["last_error"] is not None
            or str(row["fence_intent_ref"] or "") != intent_ref
            or str(row["observed_status"] or "") != "PENDING"
            or row["observed_status_version"] is None
            or int(row["observed_status_version"]) != 0
            or str(row["resolution_status"] or "") != "CANCELLED"
            or row["resolved_ts_ms"] is None
        ):
            return False
    return True


def cancelled_parallel_predecessor_is_exact(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    graph_revision: int,
    intent_ref: str,
) -> bool:
    """Allow one exact Review/failure BLOCKED epoch before cancellation.

    Cancellation is allowed from a durable ``BLOCKED`` Graph.  A prior 0090
    decision is therefore valid only when it is the authoritative BLOCKED
    epoch immediately preceding the cancellation intent's fence.  Any extra,
    malformed, or unchained decision remains fail-closed.
    """

    intent_row = conn.execute(
        """
        SELECT * FROM task_runtime_graph_cancellation_intent
        WHERE intent_ref = ? AND graph_id = ? AND graph_revision = ?
        """,
        (intent_ref, graph_id, graph_revision),
    ).fetchone()
    decision_row = conn.execute(
        """
        SELECT * FROM task_runtime_graph_cancellation_decision
        WHERE intent_ref = ? AND graph_id = ? AND graph_revision = ?
        """,
        (intent_ref, graph_id, graph_revision),
    ).fetchone()
    if intent_row is None or decision_row is None:
        return False
    try:
        intent = _graph_cancellation_intent_record(intent_row)
        decision = _graph_cancellation_decision_record(decision_row)
    except TaskStoreError:
        return False
    if (
        str(intent.get("status") or "") != "FINALIZED"
        or str(intent.get("graph_id") or "") != graph_id
        or int(intent.get("graph_revision") or 0) != graph_revision
        or str(decision.get("intent_ref") or "") != intent_ref
        or int(intent.get("graph_version_after") or -1)
        != int(decision.get("graph_version_before") or -2)
    ):
        return False
    rows = conn.execute(
        """
        SELECT decision_ref FROM task_runtime_parallel_settlement_decision
        WHERE graph_id = ? AND graph_revision = ?
        ORDER BY graph_version_after, decision_ref
        """,
        (graph_id, graph_revision),
    ).fetchall()
    if not rows:
        return True
    if (
        len(rows) != 1
        or str(intent["payload"].get("graph_status_before")) != "BLOCKED"
    ):
        return False
    predecessor = canonical_parallel_terminal_decision(
        conn,
        {
            "graph_id": graph_id,
            "revision": graph_revision,
            "status": "BLOCKED",
            "status_version": int(intent["graph_version_before"]),
        },
    )
    if predecessor is None:
        return False
    return bool(
        str(predecessor.get("graph_target_status") or "") == "BLOCKED"
        and int(predecessor.get("graph_status_version_before") or -1) + 1
        == int(intent["graph_version_before"])
    )


__all__ = [
    "cancelled_effects_are_exactly_fenced",
    "cancelled_parallel_predecessor_is_exact",
]
