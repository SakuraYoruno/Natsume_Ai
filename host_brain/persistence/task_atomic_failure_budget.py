"""Validated graph-budget release inputs for direct terminal failure UoWs."""

from __future__ import annotations

import sqlite3

from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_INVALID,
    TaskStoreError,
)


def validated_terminal_failure_budget_request(
    *,
    budget_id: str | None,
    expected_budget_version: int | None,
    budget_outbox: OutboxMessage | None,
) -> tuple[str, int, OutboxMessage] | None:
    """Accept exactly one graph-budget release event or no budget mutation."""

    supplied = (
        budget_id is not None,
        expected_budget_version is not None,
        budget_outbox is not None,
    )
    if not any(supplied):
        return None
    if not all(supplied):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "terminal failure BudgetReservation release requires id, version, and OutboxMessage",
        )
    budget_ref = str(budget_id or "").strip()
    try:
        budget_version = int(expected_budget_version)
    except (TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "terminal failure BudgetReservation version must be a non-negative integer",
        ) from exc
    if not budget_ref or budget_version < 0 or not isinstance(budget_outbox, OutboxMessage):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "terminal failure BudgetReservation release request is invalid",
        )
    budget_outbox.validate()
    if (
        budget_outbox.aggregate_type,
        budget_outbox.aggregate_id,
        budget_outbox.aggregate_version,
    ) != ("BUDGET_RESERVATION", budget_ref, budget_version + 1):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "terminal failure BudgetReservation outbox does not bind the requested version",
        )
    if (
        budget_outbox.event_type != "BUDGET_RESERVATION_RELEASED"
        or budget_outbox.attempt_id is not None
        or budget_outbox.graph_revision is not None
        or budget_outbox.evidence_refs
    ):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "terminal failure BudgetReservation outbox has unsupported lineage",
        )
    return budget_ref, budget_version, budget_outbox


def assert_terminal_failure_budget_binding(
    budget: sqlite3.Row,
    *,
    budget_id: str,
    graph_id: str,
    graph_revision: int,
) -> None:
    """Require the exact graph-scoped BudgetReservation for direct closeout."""

    if (
        str(budget["budget_id"] or "") != budget_id
        or str(budget["graph_id"] or "") != graph_id
        or int(budget["graph_revision"] or 0) != int(graph_revision)
        or budget["node_id"] is not None
        or budget["attempt_id"] is not None
    ):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "terminal failure requires the exact graph-scoped BudgetReservation",
        )


__all__ = [
    "assert_terminal_failure_budget_binding",
    "validated_terminal_failure_budget_request",
]
