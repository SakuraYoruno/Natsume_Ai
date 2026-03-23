"""Persona 人格引擎模块（Phase I 基座）。

导出核心类：
  - PersonalityTraits: 7 维人格特质向量
  - TraitsDelta: 特质变化量
  - ExpressionHint: 表达策略提示
  - PersonaEngine: 人格引擎单例
  - SelfModelSnapshot: 自我意识快照（Phase III 骨架）
  - TraitChangeRecord: 特质变化审计记录
"""

from host_brain.persona.personality_traits import (
    DEFAULT_BASELINE,
    TRAIT_NAMES,
    ExpressionHint,
    PersonalityTraits,
    TraitsDelta,
)
from host_brain.persona.persona_engine import (
    PersonaEngine,
    SelfModelSnapshot,
    TraitChangeRecord,
)
from host_brain.persona.emotion_reactivity import (
    EmotionReactivityProfile,
    ReactivityModifiers,
)
from host_brain.persona.expression_adapter import (
    PersonaExpressionAdapter,
)

__all__ = [
    # personality_traits.py
    "TRAIT_NAMES",
    "DEFAULT_BASELINE",
    "PersonalityTraits",
    "TraitsDelta",
    "ExpressionHint",
    # persona_engine.py
    "PersonaEngine",
    "SelfModelSnapshot",
    "TraitChangeRecord",
    # emotion_reactivity.py (Phase II)
    "EmotionReactivityProfile",
    "ReactivityModifiers",
    # expression_adapter.py (Phase II)
    "PersonaExpressionAdapter",
]
