"""Typed projections exposed by the durable Research shadow ledger."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from host_brain.contracts.planning_search.research_benchmark_contracts import (
    PairedResearchRunReceiptV1,
    ResearchArmPlanV1,
    ResearchCorpusBindingV1,
    ResearchExecutionBudgetV1,
    ResearchExecutionProfileV1,
    ResearchModelExecutionReceiptV1,
    ResearchVerifierReceiptV1,
)


class ResearchShadowLedgerError(RuntimeError):
    """Durable Research evidence failed closed."""


class ResearchArmOperationState(str, Enum):
    """Durable recovery state for one external Research arm identity."""

    RESERVED = "RESERVED"
    CALL_CLAIMED = "CALL_CLAIMED"
    EXTERNAL_CALL_STARTED = "EXTERNAL_CALL_STARTED"
    RETRY_AUTHORIZED_NO_AUDIT = "RETRY_AUTHORIZED_NO_AUDIT"
    EVIDENCE_PERSISTED = "EVIDENCE_PERSISTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class ResearchArmOperationSnapshot:
    """One immutable-operation projection reconstructed from its event chain.

    The snapshot contains no prompt body, model response or source body. It
    exists only to decide whether a caller may issue an external request,
    resume after durable evidence, or must reconcile an ambiguous effect.
    """

    operation_id: str
    operation_digest: str
    run_id: str
    arm_role: str
    arm_id: str
    execution_id: str
    state: ResearchArmOperationState
    event_sequence: int
    claim_id: str | None = None
    claim_owner_id: str | None = None
    claim_fencing_token: int | None = None
    claim_expires_at_ms: int | None = None
    reconciliation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchPairedRunSpecSnapshot:
    """Typed, immutable projection of one preregistered Research run."""

    run_id: str
    registration_id: str
    binding: ResearchCorpusBindingV1
    profile: ResearchExecutionProfileV1
    budget: ResearchExecutionBudgetV1
    baseline: ResearchArmPlanV1
    selected: ResearchArmPlanV1
    arm_order: tuple[str, str]
    owner_randomized: bool
    randomization_algorithm: str | None
    randomization_draw: int | None
    cohort_run_key_digest: str | None


@dataclass(frozen=True, slots=True)
class ResearchArmExecutionSnapshot:
    """One reopened arm receipt, reconstructed from canonical ledger JSON."""

    run_id: str
    role: str
    plan: ResearchArmPlanV1
    model: ResearchModelExecutionReceiptV1
    verifier: ResearchVerifierReceiptV1
    evidence_digest: str


@dataclass(frozen=True, slots=True)
class ResearchTerminalReceiptSnapshot:
    """One reopened immutable paired terminal receipt."""

    run_id: str
    receipt: PairedResearchRunReceiptV1
    receipt_digest: str

    @property
    def receipt_ref(self) -> str:
        return f"research-receipt://sha256/{self.receipt_digest}"


__all__ = [
    "ResearchArmExecutionSnapshot",
    "ResearchArmOperationSnapshot",
    "ResearchArmOperationState",
    "ResearchPairedRunSpecSnapshot",
    "ResearchShadowLedgerError",
    "ResearchTerminalReceiptSnapshot",
]
