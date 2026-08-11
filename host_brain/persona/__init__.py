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
    CONTEXT_TRAIT_MODIFIERS,
    ExpressionHint,
    PersonalityTraits,
    TraitsDelta,
)
from host_brain.persona.persona_engine import (
    PERSONA_GROWTH_AUTHORITY_CLOSED,
    PersonaEngine,
    PersonaGrowthAuthorityError,
    PersonaGrowthMigrationAuthority,
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
from host_brain.persona.self_model import (
    SelfModel,
)
from host_brain.persona.self_reflection import (
    SelfReflection,
    build_self_reflection_pulse,
)
from host_brain.persona.trait_drift_guard import (
    TraitDriftGuard,
    GuardCheckResult,
)
from host_brain.persona.persona_growth import (
    PersonaGrowthTracker,
    GrowthApplyResult,
    GrowthRule,
)
from host_brain.persona.drift_detector import (
    PersonaDriftDetector,
    DriftCheckResult,
    StyleFingerprint,
    extract_style_fingerprint,
)
from host_brain.persona.emotion_persistence import (
    EmotionPersistence,
    PersistedEmotionState,
    RestoredEmotionState,
    compute_momentum_decay_multiplier,
)
from host_brain.persona.proactive_voice import (
    PersonaProactiveVoice,
    ProactiveExpression,
)
from host_brain.persona.interaction_growth_analyzer import (
    InteractionGrowthAnalyzer,
    InteractionGrowthResult,
    InteractionQualityEvaluator,
    InteractionQualityScore,
    make_interaction_growth_task_config,
)
from host_brain.persona.growth_feedback_collector import (
    GrowthFeedbackCollector,
    GrowthFeedback,
    FeedbackAdjustmentResult,
)

__all__ = [
    # personality_traits.py
    "TRAIT_NAMES",
    "DEFAULT_BASELINE",
    "CONTEXT_TRAIT_MODIFIERS",
    "PersonalityTraits",
    "TraitsDelta",
    "ExpressionHint",
    # persona_engine.py
    "PersonaEngine",
    "PERSONA_GROWTH_AUTHORITY_CLOSED",
    "PersonaGrowthAuthorityError",
    "PersonaGrowthMigrationAuthority",
    "SelfModelSnapshot",
    "TraitChangeRecord",
    # emotion_reactivity.py (Phase II)
    "EmotionReactivityProfile",
    "ReactivityModifiers",
    # expression_adapter.py (Phase II)
    "PersonaExpressionAdapter",
    # self_model.py (Phase III)
    "SelfModel",
    # self_reflection.py (Phase III)
    "SelfReflection",
    "build_self_reflection_pulse",
    # trait_drift_guard.py (Phase IV)
    "TraitDriftGuard",
    "GuardCheckResult",
    # persona_growth.py (Phase IV)
    "PersonaGrowthTracker",
    "GrowthApplyResult",
    "GrowthRule",
    # drift_detector.py (Phase VI)
    "PersonaDriftDetector",
    "DriftCheckResult",
    "StyleFingerprint",
    "extract_style_fingerprint",
    # emotion_persistence.py (Phase VII)
    "EmotionPersistence",
    "PersistedEmotionState",
    "RestoredEmotionState",
    "compute_momentum_decay_multiplier",
    # proactive_voice.py (Phase VIII)
    "PersonaProactiveVoice",
    "ProactiveExpression",
    # interaction_growth_analyzer.py (Phase IX)
    "InteractionGrowthAnalyzer",
    "InteractionGrowthResult",
    "InteractionQualityEvaluator",
    "InteractionQualityScore",
    "make_interaction_growth_task_config",
    # growth_feedback_collector.py (Phase IX-C)
    "GrowthFeedbackCollector",
    "GrowthFeedback",
    "FeedbackAdjustmentResult",
]
