"""EmotionReactivityProfile：特质→情感反应倾向映射。

Phase II 核心模块——根据 PersonalityTraits 和情感场景，
计算情感反应的调制系数（echo_weight、arousal 偏移、dominance 偏移等）。

设计原则：
  - 纯函数式：输入 (traits, emotion_tag, bond_stage) → 输出调制系数
  - 无副作用，无状态
  - 所有映射规则可审计、可测试
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.persona.personality_traits import PersonalityTraits


# ---------------------------------------------------------------------------
# 常量：情感场景分类
# ---------------------------------------------------------------------------

# 消极情感标签
_NEGATIVE_EMOTIONS: frozenset[str] = frozenset({
    "SAD", "ANGRY", "ANXIOUS", "FRUSTRATED", "CONFUSED", "BORED",
})

# 轻度消极（幽默可介入）
_MILD_NEGATIVE_EMOTIONS: frozenset[str] = frozenset({
    "BORED", "FRUSTRATED", "CONFUSED",
})

# 积极情感标签
_POSITIVE_EMOTIONS: frozenset[str] = frozenset({
    "HAPPY", "EXCITED", "LOVING", "PROUD", "GRATEFUL", "SURPRISED",
})

# 探索/思考类
_EXPLORATORY_EMOTIONS: frozenset[str] = frozenset({
    "EXCITED", "THINKING", "SURPRISED",
})

# 深层关系阶段
_DEEP_STAGES: frozenset[str] = frozenset({
    "CLOSE_FRIEND", "CONFIDANT", "SOULMATE",
})


# ---------------------------------------------------------------------------
# ReactivtyModifiers — 调制系数数据结构
# ---------------------------------------------------------------------------

@dataclass
class ReactivityModifiers:
    """情感反应调制系数。

    由 EmotionReactivityProfile 根据特质 + 情感场景计算得出，
    供 compute_emotion_echo() 和 ExpressionAdapter 使用。
    """

    echo_weight_delta: float = 0.0       # echo_weight 调整量（叠加到基础 0.4 上）
    arousal_delta: float = 0.0           # arousal 偏移量
    dominance_delta: float = 0.0         # dominance 偏移量
    empathy_boost: float = 0.0           # 共情提升（0-1）
    humor_deflection: float = 0.0        # 幽默化解倾向（0-1）
    curiosity_follow_up: float = 0.0     # 追问倾向（0-1）

    def as_dict(self) -> dict[str, float]:
        return {
            "echo_weight_delta": round(self.echo_weight_delta, 4),
            "arousal_delta": round(self.arousal_delta, 4),
            "dominance_delta": round(self.dominance_delta, 4),
            "empathy_boost": round(self.empathy_boost, 4),
            "humor_deflection": round(self.humor_deflection, 4),
            "curiosity_follow_up": round(self.curiosity_follow_up, 4),
        }


# ---------------------------------------------------------------------------
# EmotionReactivityProfile — 核心计算逻辑
# ---------------------------------------------------------------------------

class EmotionReactivityProfile:
    """根据人格特质计算情感反应调制系数。

    映射规则（来自设计文档 Phase II §耦合逻辑示例）：
      - 高敏感度 + 用户消极情感 → echo_weight 提高
      - 高幽默感 + 用户轻度消极 → humor_deflection 提高
      - 高好奇心 + 用户分享新话题 → curiosity_follow_up 提高
      - 低自信度 + 用户质疑    → dominance 降低
      - 高温柔度 + 用户悲伤    → empathy_boost 提高
    """

    @staticmethod
    def compute_modifiers(
        traits: PersonalityTraits,
        user_emotion_tag: str,
        bond_stage: str = "",
    ) -> ReactivityModifiers:
        """计算情感反应调制系数。

        Args:
            traits: 当前人格特质（7 维向量）
            user_emotion_tag: 用户当前情感标签
            bond_stage: 当前 Bond 阶段

        Returns:
            ReactivityModifiers 调制系数
        """
        tag = str(user_emotion_tag or "").strip().upper()
        stage = str(bond_stage or "").strip().upper()

        echo_weight_delta = 0.0
        arousal_delta = 0.0
        dominance_delta = 0.0
        empathy_boost = 0.0
        humor_deflection = 0.0
        curiosity_follow_up = 0.0

        # ── 规则 1：高敏感度 + 用户消极情感 → echo_weight 提高 ──────────
        if traits.sensitivity >= 0.7 and tag in _NEGATIVE_EMOTIONS:
            # 敏感度越高，echo_weight 提升越多（最大 +0.2）
            echo_weight_delta += (traits.sensitivity - 0.5) * 0.4
            # 高敏感度遇到消极情感，arousal 微降（更加安静地陪伴）
            arousal_delta -= 0.05

        # ── 规则 2：高幽默感 + 用户轻度消极 → humor_deflection ──────────
        if traits.humor >= 0.5 and tag in _MILD_NEGATIVE_EMOTIONS:
            humor_deflection = (traits.humor - 0.3) * 0.8
            # 幽默化解时 arousal 微升
            arousal_delta += 0.03

        # ── 规则 3：高好奇心 + 用户分享新话题 → curiosity_follow_up ─────
        if traits.curiosity >= 0.6 and tag in _EXPLORATORY_EMOTIONS:
            curiosity_follow_up = (traits.curiosity - 0.4) * 0.6
            arousal_delta += 0.05

        # ── 规则 4：低自信度 + 用户质疑 → dominance 降低 ────────────────
        if traits.assertiveness <= 0.4 and tag == "ANGRY":
            dominance_delta -= (0.5 - traits.assertiveness) * 0.5

        # ── 规则 5：高温柔度 + 用户悲伤 → empathy_boost ────────────────
        if traits.warmth >= 0.7 and tag in {"SAD", "ANXIOUS", "FRUSTRATED"}:
            empathy_boost = (traits.warmth - 0.5) * 0.6
            # 深层关系阶段 → 共情加强
            if stage in _DEEP_STAGES:
                empathy_boost += 0.1

        # ── 规则 6：高俏皮度 + 积极情感 → arousal 微升 ─────────────────
        if traits.playfulness >= 0.5 and tag in _POSITIVE_EMOTIONS:
            arousal_delta += (traits.playfulness - 0.3) * 0.15

        # ── 规则 7：高韧性 + 强烈消极 → 情绪稳定，echo_weight 不过度放大 ──
        if traits.resilience >= 0.6 and tag in {"ANGRY", "SAD"}:
            # 韧性抵消部分 echo_weight 增量
            echo_weight_delta -= (traits.resilience - 0.5) * 0.15

        return ReactivityModifiers(
            echo_weight_delta=max(-0.2, min(0.2, echo_weight_delta)),
            arousal_delta=max(-0.15, min(0.15, arousal_delta)),
            dominance_delta=max(-0.3, min(0.3, dominance_delta)),
            empathy_boost=max(0.0, min(1.0, empathy_boost)),
            humor_deflection=max(0.0, min(1.0, humor_deflection)),
            curiosity_follow_up=max(0.0, min(1.0, curiosity_follow_up)),
        )

    @staticmethod
    def apply_to_echo_weight(
        base_echo_weight: float,
        modifiers: ReactivityModifiers,
    ) -> float:
        """将调制系数应用到基础 echo_weight。

        Args:
            base_echo_weight: 基础回声权重（默认 0.4）
            modifiers: 调制系数

        Returns:
            调制后的 echo_weight，范围 [0.1, 0.8]
        """
        adjusted = float(base_echo_weight) + modifiers.echo_weight_delta
        return max(0.1, min(0.8, adjusted))
