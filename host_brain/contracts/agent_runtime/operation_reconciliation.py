"""Neutral contracts for definite Operation-store reconciliation rejection.

The application and persistence layers both depend on this module so a
transactional, definite store rejection cannot be confused with an unknown
transport/database failure after a mutation may have committed.
"""

from __future__ import annotations


class OperationReconciliationStoreRejected(RuntimeError):
    """A fail-closed store rejection whose transaction did not commit."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "OPERATION_RECONCILIATION_REJECTED")
        self.detail = str(detail or "operation reconciliation was rejected")
        super().__init__(f"{self.reason_code}: {self.detail}")


# Compatibility name retained for the cancellation-bound service. Both names
# intentionally identify the same definite-no-commit exception contract.
GraphCancellationOperationStoreRejected = OperationReconciliationStoreRejected


__all__ = [
    "GraphCancellationOperationStoreRejected",
    "OperationReconciliationStoreRejected",
]
