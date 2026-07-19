"""Compatibility exports for the neutral evolution-governance contracts.

Canonical definitions live in :mod:`host_brain.contracts.evolution`.  This
module intentionally re-exports the same objects so existing application
imports keep class identity and serialization behavior.
"""

from host_brain.contracts.evolution import (
    FeedbackProposalV1,
    IdentityMigrationPlanV1,
    KnowledgeEntryV1,
    KnowledgeEvolutionContractError,
)

__all__ = [
    "FeedbackProposalV1",
    "IdentityMigrationPlanV1",
    "KnowledgeEntryV1",
    "KnowledgeEvolutionContractError",
]
