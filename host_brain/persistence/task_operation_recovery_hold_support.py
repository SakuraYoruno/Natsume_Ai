"""SQLite observation and Outbox helpers for Operation recovery holds."""

from __future__ import annotations

from collections.abc import Mapping
import sqlite3
from typing import Any

from .task_operation_recovery_hold_codec import (
    OPERATION_RECOVERY_HOLD_SCHEMA,
    build_operation_recovery_hold_subject,
)
from .task_operation_recovery_hold_current import hold_is_current
from .task_store_types import (
    OutboxMessage,
    TASK_STORE_INVALID,
    TaskStoreError,
    build_outbox_payload_ref,
)


EVENT = "OPERATION_RECOVERY_HOLD_RECORDED"
AGGREGATE = "OPERATION_RECOVERY_HOLD"


def normalize_hold_identity(**values: Any) -> dict[str, Any]:
    normalized = {
        key: str(value or "").strip()
        for key, value in values.items()
        if key != "graph_revision"
    }
    revision = values.get("graph_revision")
    if (
        any(not value for value in normalized.values())
        or type(revision) is not int
        or revision < 1
    ):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "Operation recovery hold identity is invalid",
        )
    return {**normalized, "graph_revision": revision}


def load_hold_observations(
    conn: sqlite3.Connection,
    **identity: Any,
) -> dict[str, Any]:
    graph = conn.execute(
        "SELECT * FROM task_runtime_graph WHERE graph_id = ? AND revision = ?",
        (identity["graph_id"], identity["graph_revision"]),
    ).fetchone()
    node = conn.execute(
        """
        SELECT * FROM task_runtime_node
        WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
        """,
        (identity["graph_id"], identity["graph_revision"], identity["node_id"]),
    ).fetchone()
    attempt = conn.execute(
        """
        SELECT * FROM task_runtime_attempt
        WHERE attempt_id = ? AND graph_id = ? AND graph_revision = ?
          AND node_id = ?
        """,
        (
            identity["attempt_id"],
            identity["graph_id"],
            identity["graph_revision"],
            identity["node_id"],
        ),
    ).fetchone()
    operation = conn.execute(
        """
        SELECT * FROM task_runtime_idempotency_reservation
        WHERE reservation_id = ? AND attempt_id = ?
        """,
        (identity["reservation_id"], identity["attempt_id"]),
    ).fetchone()
    budget = conn.execute(
        """
        SELECT * FROM task_runtime_budget_reservation
        WHERE budget_id = ? AND graph_id = ? AND graph_revision = ?
          AND node_id IS NULL AND attempt_id IS NULL
        """,
        (identity["budget_id"], identity["graph_id"], identity["graph_revision"]),
    ).fetchone()
    if any(item is None for item in (graph, node, attempt, operation, budget)):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "Operation recovery hold canonical lineage was not found",
        )
    cancellation = conn.execute(
        """
        SELECT * FROM task_runtime_graph_cancellation_intent
        WHERE graph_id = ? AND graph_revision = ?
        """,
        (identity["graph_id"], identity["graph_revision"]),
    ).fetchone()
    leases = conn.execute(
        """
        SELECT * FROM task_runtime_lease
        WHERE attempt_id = ? AND status = 'ACTIVE'
        ORDER BY lease_id
        """,
        (identity["attempt_id"],),
    ).fetchall()
    return {
        "graph": dict(graph),
        "node": dict(node),
        "attempt": dict(attempt),
        "operation": dict(operation),
        "budget": dict(budget),
        "cancellation": dict(cancellation) if cancellation is not None else None,
        "active_leases": [dict(row) for row in leases],
    }


def hold_row_values(
    *,
    observations: Mapping[str, Any],
    subject_json: str,
    subject_sha256: str,
    case_ref: str,
    outbox_id: str,
    reason_code: str,
    checkpoint_state: str | None,
    created_ts_ms: int,
) -> list[Any]:
    subject = build_operation_recovery_hold_subject(
        **observations,
        reason_code=reason_code,
        checkpoint_state=checkpoint_state,
    )[0]
    graph = observations["graph"]
    node = observations["node"]
    attempt = observations["attempt"]
    operation = observations["operation"]
    budget = observations["budget"]
    cancellation = observations["cancellation"]
    return [
        case_ref,
        OPERATION_RECOVERY_HOLD_SCHEMA,
        subject["case_kind"],
        subject["classification"],
        subject["reason_code"],
        subject["checkpoint_state"],
        graph["graph_id"],
        graph["revision"],
        graph["status"],
        graph["status_version"],
        node["node_id"],
        node["status"],
        node["status_version"],
        attempt["attempt_id"],
        attempt["status"],
        attempt["status_version"],
        operation["reservation_id"],
        operation["status"],
        operation["status_version"],
        budget["budget_id"],
        budget["status"],
        budget["status_version"],
        cancellation["intent_ref"] if cancellation else None,
        cancellation["status"] if cancellation else None,
        cancellation["status_version"] if cancellation else None,
        subject_json,
        subject_sha256,
        outbox_id,
        created_ts_ms,
    ]


def build_hold_outbox(
    *,
    case_ref: str,
    subject_sha256: str,
    attempt_id: str,
) -> OutboxMessage:
    digest = case_ref.rsplit("/", 1)[-1]
    return OutboxMessage.create(
        aggregate_type=AGGREGATE,
        aggregate_id=case_ref,
        aggregate_version=1,
        event_type=EVENT,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=AGGREGATE,
            aggregate_id=case_ref,
            aggregate_version=1,
        ),
        idempotency_key=f"operation-recovery-hold:{digest}",
        attempt_id=attempt_id,
        evidence_refs=(
            "sha256://operation-recovery-hold/"
            + subject_sha256.removeprefix("sha256:"),
        ),
        outbox_id=f"outbox::operation-recovery-hold::{digest}",
    )


__all__ = [
    "AGGREGATE",
    "EVENT",
    "build_hold_outbox",
    "hold_is_current",
    "hold_row_values",
    "load_hold_observations",
    "normalize_hold_identity",
]
