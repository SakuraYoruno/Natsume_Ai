"""Stateless public retry scheduling facades over one canonical writer."""

from __future__ import annotations

from typing import Any

from host_brain.persistence.task_store_types import OutboxMessage


class TaskRetryScheduleFacadeMixin:
    """Select graph-terminal or node-only semantics without owning a UoW."""

    def schedule_retry(
        self,
        attempt_id: str,
        *,
        budget_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        error_ref: str,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        """Schedule the legacy retry decision, including graph blocking."""

        return self._schedule_retry(
            attempt_id,
            budget_id=budget_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            expected_attempt_version=expected_attempt_version,
            expected_graph_version=None,
            error_ref=error_ref,
            outbox=outbox,
            terminalize_graph_on_budget_rejection=True,
        )

    def schedule_retry_node_outcome(
        self,
        attempt_id: str,
        *,
        budget_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_graph_version: int,
        error_ref: str,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        """Schedule a child retry without owning Graph terminalization."""

        return self._schedule_retry(
            attempt_id,
            budget_id=budget_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            expected_attempt_version=expected_attempt_version,
            expected_graph_version=expected_graph_version,
            error_ref=error_ref,
            outbox=outbox,
            terminalize_graph_on_budget_rejection=False,
        )


__all__ = ["TaskRetryScheduleFacadeMixin"]
