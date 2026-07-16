"""Neutral contracts for cancellation-bound operation reconciliation.

The application and persistence layers both depend on this module so a
transactional, definite store rejection cannot be confused with an unknown
transport/database failure after a mutation may have committed.
"""

from __future__ import annotations


class GraphCancellationOperationStoreRejected(RuntimeError):
    """A fail-closed store rejection whose transaction did not commit."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "OPERATION_RECONCILIATION_REJECTED")
        self.detail = str(detail or "operation reconciliation was rejected")
        super().__init__(f"{self.reason_code}: {self.detail}")


__all__ = ["GraphCancellationOperationStoreRejected"]
