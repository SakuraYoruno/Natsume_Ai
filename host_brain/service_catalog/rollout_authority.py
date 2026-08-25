"""Runtime Guardian observer for catalog rollout.

All stable DTOs, validation helpers and repository ports are owned by the
runtime-independent contract module.  This application module retains only
the adapter that projects a durable terminal receipt into Runtime Guardian;
callers must import the contract types from their canonical owner.
"""

from __future__ import annotations

from typing import Any, Mapping

from host_brain.core.ids import now_ms as _now_ms
from host_brain.contracts.service_catalog import (
    ServiceCatalogRolloutError as _ServiceCatalogRolloutError,
    ServiceCatalogRolloutRepository as _ServiceCatalogRolloutRepository,
    _text,
)


class ServiceCatalogRolloutGuardianObserver:
    """App-local adapter from a durable rollout receipt to Runtime Guardian."""

    def __init__(
        self,
        *,
        store: _ServiceCatalogRolloutRepository,
        guardian: Any,
    ) -> None:
        self._store = store
        self._guardian = guardian

    def observe_latest(
        self,
        *,
        runtime_scope_id: str,
        candidate_owner_id: str,
    ) -> Any:
        receipt = self._store.latest_terminal_receipt(
            runtime_scope_id=runtime_scope_id,
            candidate_owner_id=candidate_owner_id,
        )
        if receipt is None:
            raise _ServiceCatalogRolloutError("rollout terminal receipt is unavailable")
        return self._observe(receipt, candidate_owner_id=candidate_owner_id)

    def observe_operation(self, operation_id: str) -> Any:
        receipt = self._store.terminal_receipt(operation_id)
        if receipt is None:
            raise _ServiceCatalogRolloutError("rollout terminal receipt is unavailable")
        payload = receipt.get("payload")
        if not isinstance(payload, Mapping):
            raise _ServiceCatalogRolloutError("rollout terminal payload is invalid")
        candidate_owner_id = _text(
            payload.get("candidate_owner_id"), "candidate_owner_id"
        )
        return self._observe(receipt, candidate_owner_id=candidate_owner_id)

    def _observe(
        self,
        receipt: Mapping[str, Any],
        *,
        candidate_owner_id: str,
    ) -> Any:
        status = "UP" if receipt["event_type"] == "ACTIVATION_STARTED" else "DOWN"
        receipt_ref = (
            f"service-catalog-rollout://receipt/{receipt['receipt_digest']}"
        )
        return self._guardian.observe_dependency(
            dependency_id="SSE",
            status=status,
            evidence_refs=(receipt_ref,),
            source_ref=receipt_ref,
            observed_at_ms=max(int(receipt["created_at_ms"]), _now_ms()),
            detail=str(receipt["status"]),
        )


__all__ = ["ServiceCatalogRolloutGuardianObserver"]
