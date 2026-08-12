"""由受限领域 mixin 组装的 NE-06 对外激活服务。"""

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
    """受治理演进激活的应用内唯一权威入口。

    mixin 仅按领域拆分实现；所有可变操作仍经同一基础内核与 V2 repository，
    不允许调用方绕过证据、世代检查或幂等收据直接写入生效版本。
    """


__all__ = [
    "EXPRESSION_PREFERENCE_POLICY_KIND",
    "EvolutionVersionActivationService",
    "FEEDBACK_SIGNAL_POLICY_KIND",
    "PERSONA_POLICY_KIND",
]
