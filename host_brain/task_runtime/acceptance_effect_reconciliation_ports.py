"""Ports for signed general Acceptance Effect reconciliation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class AcceptanceEffectReconciliationStorePort(Protocol):
    def read_acceptance_effect_reconciliation(
        self,
        effect_id: str,
        *,
        case_id: str | None = None,
    ) -> Mapping[str, Any]: ...

    def record_acceptance_effect_reconciliation_proof(
        self,
        effect_id: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...

    def stage_acceptance_effect_reconciliation_evidence(
        self,
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...

    def get_acceptance_effect_reconciliation_evidence(
        self,
        evidence_ref: str,
    ) -> Mapping[str, Any]: ...


__all__ = ["AcceptanceEffectReconciliationStorePort"]
