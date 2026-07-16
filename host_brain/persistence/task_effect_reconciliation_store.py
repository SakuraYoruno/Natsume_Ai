"""Compatibility composite for signed Acceptance Effect reconciliation."""

from __future__ import annotations

from .task_effect_reconciliation_common import (
    EFFECT_RECONCILIATION_AGGREGATE_TYPE,
    EFFECT_RECONCILIATION_CASE_SCHEMA,
    EFFECT_RECONCILIATION_EVENT_TYPE,
    EFFECT_RECONCILIATION_LEGACY_PURPOSE,
    EFFECT_RECONCILIATION_PENDING_PURPOSE,
    EFFECT_RECONCILIATION_PROOF_SCHEMA,
    build_effect_reconciliation_attestation_binding,
)
from .task_effect_reconciliation_mutation_store import (
    TaskEffectReconciliationMutationMixin,
)
from .task_effect_reconciliation_read_store import (
    TaskEffectReconciliationReadMixin,
)


class TaskEffectReconciliationStoreMixin(
    TaskEffectReconciliationMutationMixin,
    TaskEffectReconciliationReadMixin,
):
    """Stable Store surface composed from focused read and mutation owners."""


__all__ = [
    "EFFECT_RECONCILIATION_AGGREGATE_TYPE",
    "EFFECT_RECONCILIATION_CASE_SCHEMA",
    "EFFECT_RECONCILIATION_EVENT_TYPE",
    "EFFECT_RECONCILIATION_LEGACY_PURPOSE",
    "EFFECT_RECONCILIATION_PENDING_PURPOSE",
    "EFFECT_RECONCILIATION_PROOF_SCHEMA",
    "TaskEffectReconciliationStoreMixin",
    "build_effect_reconciliation_attestation_binding",
]
