"""Single public NE-06 SQLite authority composed from narrow owners."""

from __future__ import annotations

from .evolution_activation_v2_base import (
    EVOLUTION_ACTIVATION_V2_MIGRATION_ASSET_KEY,
    EvolutionActivationRepositoryBase,
)
from .evolution_activation_v2_evidence import (
    EvolutionActivationV2Error,
    feedback_subject_ref,
    feedback_target_kind,
    identity_target_kind,
    knowledge_subject_ref,
    knowledge_target_kind,
)
from .evolution_activation_v2_feedback import (
    EvolutionActivationFeedbackMixin,
)
from .evolution_activation_v2_decision import (
    EvolutionActivationDecisionMixin,
)
from .evolution_activation_v2_identity import (
    EvolutionActivationIdentityMixin,
)
from .evolution_activation_v2_knowledge import (
    EvolutionActivationKnowledgeMixin,
)


class SQLiteEvolutionActivationV2Repository(
    EvolutionActivationFeedbackMixin,
    EvolutionActivationDecisionMixin,
    EvolutionActivationKnowledgeMixin,
    EvolutionActivationIdentityMixin,
    EvolutionActivationRepositoryBase,
):
    """One transactional authority assembled from single-purpose mixins."""


__all__ = [
    "EVOLUTION_ACTIVATION_V2_MIGRATION_ASSET_KEY",
    "EvolutionActivationV2Error",
    "SQLiteEvolutionActivationV2Repository",
    "feedback_subject_ref",
    "feedback_target_kind",
    "identity_target_kind",
    "knowledge_subject_ref",
    "knowledge_target_kind",
]
