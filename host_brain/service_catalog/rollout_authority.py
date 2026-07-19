"""Runtime Guardian observer and compatibility exports for catalog rollout.

All stable DTOs, validation helpers and repository ports are owned by the
runtime-independent contract module.  This application module retains only
the adapter that projects a durable terminal receipt into Runtime Guardian.
"""

from __future__ import annotations

from typing import Any, Mapping

from host_brain.contracts.service_catalog import (
    CatalogCoordinatorAuthorityV1,
    CatalogRolloutOperationV1,
    ServiceCatalogRolloutError,
    ServiceCatalogRolloutRepository,
    _now_ms,
    _text,
    catalog_operation_identity_digest,
)


class ServiceCatalogRolloutGuardianObserver:
    """App-local adapter from a durable rollout receipt to Runtime Guardian."""

    def __init__(
        self,
        *,
        store: ServiceCatalogRolloutRepository,
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
            raise ServiceCatalogRolloutError("rollout terminal receipt is unavailable")
        return self._observe(receipt, candidate_owner_id=candidate_owner_id)

    def observe_operation(self, operation_id: str) -> Any:
        receipt = self._store.terminal_receipt(operation_id)
        if receipt is None:
            raise ServiceCatalogRolloutError("rollout terminal receipt is unavailable")
        payload = receipt.get("payload")
        if not isinstance(payload, Mapping):
            raise ServiceCatalogRolloutError("rollout terminal payload is invalid")
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


__all__ = [
    "CatalogCoordinatorAuthorityV1",
    "CatalogRolloutOperationV1",
    "ServiceCatalogRolloutError",
    "ServiceCatalogRolloutGuardianObserver",
    "ServiceCatalogRolloutRepository",
    "catalog_operation_identity_digest",
]
