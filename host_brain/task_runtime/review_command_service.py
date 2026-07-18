"""Composition-owned command boundary for durable Review mutations.

The service deliberately contains no execution authority.  ``ReviewStore``
commits the canonical decision/token state and the continuation worker remains
the only background liveness mechanism.  ``wake`` is therefore a best-effort
latency hint after the durable transaction has completed, never part of the
decision commit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from host_brain.contracts.agent_runtime.review_policy import ReviewItem
from host_brain.persistence.review_repository import ReviewStore


class TaskReviewCommandService:
    """Route all process Review writes through one canonical Store instance."""

    def __init__(
        self,
        *,
        review_store: ReviewStore,
        wake_continuation: Callable[[], None] | None = None,
    ) -> None:
        required = (
            "create_review",
            "approve_review",
            "reject_review",
            "resume_review",
        )
        if any(not callable(getattr(review_store, name, None)) for name in required):
            raise TypeError("review_store does not expose the durable Review commands")
        if wake_continuation is not None and not callable(wake_continuation):
            raise TypeError("wake_continuation must be callable")
        self._store = review_store
        self._wake_continuation = wake_continuation

    @property
    def review_store(self) -> ReviewStore:
        return self._store

    def create_review(
        self,
        review: ReviewItem | Mapping[str, Any],
        *,
        work_order_id: str | None = None,
        task_graph_id: str | None = None,
        task_node_id: str | None = None,
    ) -> dict[str, Any]:
        return dict(
            self._store.create_review(
                review,
                work_order_id=work_order_id,
                task_graph_id=task_graph_id,
                task_node_id=task_node_id,
            )
        )

    def approve_review(self, review_id: str, **kwargs: Any) -> dict[str, Any]:
        result = dict(self._store.approve_review(review_id, **kwargs))
        self._wake()
        return result

    def reject_review(self, review_id: str, **kwargs: Any) -> dict[str, Any]:
        result = dict(self._store.reject_review(review_id, **kwargs))
        self._wake()
        return result

    def resume_review(self, review_id: str, **kwargs: Any) -> dict[str, Any]:
        result = dict(self._store.resume_review(review_id, **kwargs))
        self._wake()
        return result

    def _wake(self) -> None:
        wake = self._wake_continuation
        if wake is None:
            return
        try:
            wake()
        except Exception:
            # The Store transaction is already authoritative.  Poll/takeover
            # provides liveness even if the process-local latency hint fails.
            return


__all__ = ["TaskReviewCommandService"]
