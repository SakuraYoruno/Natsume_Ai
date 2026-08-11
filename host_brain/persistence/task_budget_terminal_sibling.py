"""Persist a post-winner BudgetUsage without advancing the parent version."""

from __future__ import annotations

import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount
from host_brain.persistence.task_node_budget_usage_bridge import (
    apply_node_budget_usage,
    node_budget_ready,
)
from host_brain.persistence.task_record_codec import (
    _add_amounts,
    _amount_from_json,
    _budget_usage_record,
    _dumps,
)
from host_brain.persistence.task_store_types import (
    BudgetConsumptionResult,
    OutboxMessage,
    TASK_STORE_BUDGET_ALREADY_EXCEEDED,
)


def record_already_exceeded_usage(
    store: Any,
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    budget_ref: str,
    usage_ref: str,
    kind: str,
    graph_ref: str,
    graph_revision: int,
    node_ref: str,
    attempt_ref: str,
    normalized_delta: BudgetAmount,
    delta_json: str,
    delta_digest: str,
    outbox: OutboxMessage,
    ts_ms: int,
) -> BudgetConsumptionResult:
    terminal_version = int(row["status_version"])
    winner, dimensions = store._terminal_budget_authority(  # noqa: SLF001
        conn,
        budget_row=row,
        terminal_version=terminal_version,
    )
    consumed = _amount_from_json(row["consumed_json"], field_name="consumed_json")
    reserved = _amount_from_json(row["reserved_json"], field_name="reserved_json")
    candidate_dimensions = store._exceeded_dimensions(  # noqa: SLF001
        _add_amounts(consumed, normalized_delta),
        reserved,
    )
    store._validate_terminal_sibling_outbox(  # noqa: SLF001
        outbox,
        budget_ref=budget_ref,
        terminal_version=terminal_version,
        attempt_ref=attempt_ref,
        usage_ref=usage_ref,
        would_exceed=bool(candidate_dimensions),
    )
    conn.execute(
        """
        INSERT INTO task_runtime_budget_usage(
            budget_id, usage_id, graph_id, graph_revision, node_id,
            attempt_id, charge_kind, delta_json, delta_digest, accepted,
            reason_code, exceeded_dimensions_json,
            reservation_version_before, reservation_version_after,
            outbox_id, created_ts_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
        """,
        (
            budget_ref,
            usage_ref,
            graph_ref,
            graph_revision,
            node_ref,
            attempt_ref,
            kind,
            delta_json,
            delta_digest,
            TASK_STORE_BUDGET_ALREADY_EXCEEDED,
            _dumps(list(dimensions)),
            terminal_version,
            terminal_version,
            str(winner["outbox_id"]),
            ts_ms,
        ),
    )
    if node_budget_ready(conn):
        apply_node_budget_usage(
            store,
            conn,
            parent_budget=row,
            attempt_id=attempt_ref,
            usage_id=usage_ref,
            delta=normalized_delta,
            accepted=False,
            exceeded_dimensions=dimensions,
            parent_outbox_id=str(winner["outbox_id"]),
            ts_ms=ts_ms,
        )
    usage_row = conn.execute(
        """
        SELECT * FROM task_runtime_budget_usage
        WHERE budget_id = ? AND usage_id = ?
        """,
        (budget_ref, usage_ref),
    ).fetchone()
    return BudgetConsumptionResult(
        accepted=False,
        reason_code=TASK_STORE_BUDGET_ALREADY_EXCEEDED,
        reservation=store._budget_record(row),  # noqa: SLF001
        exceeded_dimensions=dimensions,
        usage_id=usage_ref,
        replayed=False,
        usage=_budget_usage_record(usage_row),
    )


__all__ = ["record_already_exceeded_usage"]
