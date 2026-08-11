"""Compatibility exports for the neutral evolution-governance contracts.

Canonical definitions live in :mod:`host_brain.contracts.evolution`.  This
module intentionally re-exports the same objects so existing application
imports keep class identity and serialization behavior.
"""

from host_brain.contracts.evolution import (
    DecisionRecordV1,
    EvolutionTransitionReceiptV1,
    FeedbackProposalV1,
    IdentityMigrationPlanV2,
    IdentityMigrationPlanV1,
    KnowledgeEntryV2,
    KnowledgeEntryV1,
    KnowledgeEvolutionContractError,
    VersionActivationDecisionV1,
)

__all__ = [
    "DecisionRecordV1",
    "EvolutionTransitionReceiptV1",
    "FeedbackProposalV1",
    "IdentityMigrationPlanV2",
    "IdentityMigrationPlanV1",
    "KnowledgeEntryV2",
    "KnowledgeEntryV1",
    "KnowledgeEvolutionContractError",
    "VersionActivationDecisionV1",
]
