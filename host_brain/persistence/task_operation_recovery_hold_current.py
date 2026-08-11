"""Current-version projection checks for durable Operation recovery holds."""

from __future__ import annotations

from collections.abc import Mapping
import sqlite3
from typing import Any


def _active_leases(
    conn: sqlite3.Connection,
    attempt_id: str,
) -> list[dict[str, Any]]:
    return [
        {
            field: row[field]
            for field in (
                "lease_id",
                "status",
                "status_version",
                "owner_id",
                "fencing_token",
                "expires_ts_ms",
            )
        }
        for row in conn.execute(
            """
            SELECT * FROM task_runtime_lease
            WHERE attempt_id = ? AND status = 'ACTIVE'
            ORDER BY lease_id
            """,
            (attempt_id,),
        ).fetchall()
    ]


def hold_is_current(
    conn: sqlite3.Connection,
    record: Mapping[str, Any],
) -> bool:
    graph = conn.execute(
        """
        SELECT status, status_version
        FROM task_runtime_graph
        WHERE graph_id = ? AND revision = ?
        """,
        (record["graph_id"], record["graph_revision"]),
    ).fetchone()
    node = conn.execute(
        """
        SELECT status, status_version
        FROM task_runtime_node
        WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
        """,
        (record["graph_id"], record["graph_revision"], record["node_id"]),
    ).fetchone()
    attempt = conn.execute(
        """
        SELECT status, status_version
        FROM task_runtime_attempt
        WHERE attempt_id = ?
        """,
        (record["attempt_id"],),
    ).fetchone()
    operation = conn.execute(
        """
        SELECT status, status_version
        FROM task_runtime_idempotency_reservation
        WHERE reservation_id = ?
        """,
        (record["reservation_id"],),
    ).fetchone()
    budget = conn.execute(
        """
        SELECT status, status_version
        FROM task_runtime_budget_reservation
        WHERE budget_id = ?
        """,
        (record["budget_id"],),
    ).fetchone()
    cancellation = conn.execute(
        """
        SELECT intent_ref, status, status_version
        FROM task_runtime_graph_cancellation_intent
        WHERE graph_id = ? AND graph_revision = ?
        """,
        (record["graph_id"], record["graph_revision"]),
    ).fetchone()
    operation_matches = bool(
        operation is not None
        and operation["status"] == record["operation_status"]
        and operation["status_version"] == record["operation_status_version"]
    )
    if not operation_matches:
        return False

    # Nonterminal Operation ambiguity remains open until that exact Operation
    # version is reconciled.  Budget settlement, Lease expiry, and lifecycle
    # fencing may legitimately advance after the interruption and must not
    # make the only recovery inventory disappear.
    case_kind = str(record.get("case_kind") or "")
    if case_kind == "UNKNOWN_SIDE_EFFECT":
        return cancellation is None
    if case_kind == "CANCELLATION_CROSSING":
        return bool(
            cancellation is not None
            and cancellation["intent_ref"] == record["cancellation_intent_ref"]
        )

    # Checkpoint quarantine is different: it freezes a conflicting assembled
    # checkpoint and remains usable only while every observed version matches.
    expected_leases = (
        record.get("subject", {}).get("active_leases")
        if isinstance(record.get("subject"), Mapping)
        else None
    )
    return bool(
        graph is not None
        and node is not None
        and attempt is not None
        and budget is not None
        and graph["status"] == record["graph_status"]
        and graph["status_version"] == record["graph_status_version"]
        and node["status"] == record["node_status"]
        and node["status_version"] == record["node_status_version"]
        and attempt["status"] == record["attempt_status"]
        and attempt["status_version"] == record["attempt_status_version"]
        and budget["status"] == record["budget_status"]
        and budget["status_version"] == record["budget_status_version"]
        and _active_leases(conn, record["attempt_id"]) == expected_leases
        and (
            (
                cancellation is None
                and record["cancellation_intent_ref"] is None
            )
            or (
                cancellation is not None
                and cancellation["intent_ref"] == record["cancellation_intent_ref"]
                and cancellation["status"] == record["cancellation_status"]
                and cancellation["status_version"]
                == record["cancellation_status_version"]
            )
        )
    )


__all__ = ["hold_is_current"]
