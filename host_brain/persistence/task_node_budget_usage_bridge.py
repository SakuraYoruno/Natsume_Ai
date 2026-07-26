"""Transaction-local bridge from graph BudgetUsage to child reservations."""

from __future__ import annotations

import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount


def node_budget_ready(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table'
              AND name = 'task_runtime_node_budget_reservation'
            """
        ).fetchone()
        is not None
    )


def node_budget_exceeded_dimensions(
    store: Any,
    conn: sqlite3.Connection,
    *,
    parent_budget_id: str,
    attempt_id: str,
    delta: BudgetAmount,
) -> tuple[str, ...]:
    child = store._require_node_budget(  # noqa: SLF001
        conn,
        parent_budget_id=parent_budget_id,
        attempt_id=attempt_id,
    )
    return store._node_budget_exceeded_dimensions(child, delta)  # noqa: SLF001


def apply_node_budget_usage(
    store: Any,
    conn: sqlite3.Connection,
    **kwargs: Any,
) -> None:
    store._apply_node_budget_usage_in_transaction(conn, **kwargs)  # noqa: SLF001


def assert_node_budget_usage_replay(
    store: Any,
    conn: sqlite3.Connection,
    **kwargs: Any,
) -> None:
    store._assert_node_budget_usage_replay_in_transaction(  # noqa: SLF001
        conn,
        **kwargs,
    )


__all__ = [
    "apply_node_budget_usage",
    "assert_node_budget_usage_replay",
    "node_budget_exceeded_dimensions",
    "node_budget_ready",
]
