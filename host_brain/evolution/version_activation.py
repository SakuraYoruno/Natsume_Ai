"""Public NE-06 activation service assembled from bounded domain mixins."""

from __future__ import annotations

from host_brain.evolution.version_activation_base import (
    EvolutionVersionActivationBase,
)
from host_brain.evolution.version_activation_feedback import (
    EXPRESSION_PREFERENCE_POLICY_KIND,
    FEEDBACK_SIGNAL_POLICY_KIND,
    PERSONA_POLICY_KIND,
    EvolutionVersionFeedbackMixin,
)
from host_brain.evolution.version_activation_identity import (
    EvolutionVersionIdentityMixin,
)
from host_brain.evolution.version_activation_knowledge import (
    EvolutionVersionKnowledgeMixin,
)


class EvolutionVersionActivationService(
    EvolutionVersionFeedbackMixin,
    EvolutionVersionKnowledgeMixin,
    EvolutionVersionIdentityMixin,
    EvolutionVersionActivationBase,
):
    """Canonical app-local authority for governed evolution activation."""


__all__ = [
    "EXPRESSION_PREFERENCE_POLICY_KIND",
    "EvolutionVersionActivationService",
    "FEEDBACK_SIGNAL_POLICY_KIND",
    "PERSONA_POLICY_KIND",
]
