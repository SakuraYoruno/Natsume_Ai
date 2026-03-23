"""PersonaExpressionAdapter：人格化表达适配器。

Phase II 核心模块——根据 PersonalityTraits + EmotionVector + Bond 阶段，
生成 ExpressionHint（表达策略提示），注入到 prompt_builder 中影响 LLM 输出风格。

职责：
  1. 综合 traits + emotion + bond_stage 生成表达策略
  2. 根据 EmotionReactivityProfile 的调制系数微调策略
  3. 输出 ExpressionHint 供 prompt_builder 使用

替代 Phase I 中 PersonaEngine.get_expression_adapter() 的简单实现。
"""

from __future__ import annotations

import logging
from typing import Any

from host_brain.persona.personality_traits import (
    ExpressionHint,
    PersonalityTraits,
)
from host_brain.persona.emotion_reactivity import (
    EmotionReactivityProfile,
    ReactivityModifiers,
)

_logger = logging.getLogger("Natsume.expression_adapter")

# ---------------------------------------------------------------------------
# Bond 阶段 → 表达开放度
# ---------------------------------------------------------------------------

_STAGE_OPENNESS: dict[str, float] = {
    "STRANGER": 0.2,
    "ACQUAINTANCE": 0.4,
    "FRIEND": 0.6,
    "CLOSE_FRIEND": 0.8,
    "CONFIDANT": 0.9,
    "SOULMATE": 1.0,
}


# ---------------------------------------------------------------------------
# PersonaExpressionAdapter
# ---------------------------------------------------------------------------

class PersonaExpressionAdapter:
    """人格化表达适配器——Phase II 核心。

    用法：
        adapter = PersonaExpressionAdapter()
        hint = adapter.adapt(traits, emotion_vector, bond_stage)
    """

    def adapt(
        self,
        traits: PersonalityTraits,
        emotion: Any,  # EmotionVector（用 Any 避免循环导入）
        bond_stage: str,
        *,
        user_emotion_tag: str = "NEUTRAL",
    ) -> ExpressionHint:
        """根据特质 + 情感 + 关系阶段生成表达策略提示。

        Args:
            traits: 当前人格特质（7 维向量）
            emotion: Natsume 当前 EmotionVector（可为 None）
            bond_stage: 当前 Bond 阶段
            user_emotion_tag: 用户当前情感标签

        Returns:
            ExpressionHint 表达策略提示
        """
        stage = str(bond_stage or "").strip().upper()
        tag = str(user_emotion_tag or "").strip().upper() or "NEUTRAL"

        # 1. 计算情感反应调制系数
        modifiers = EmotionReactivityProfile.compute_modifiers(
            traits, tag, stage,
        )

        # 2. 根据关系阶段确定表达开放度
        openness = _STAGE_OPENNESS.get(stage, 0.5)

        # 3. 综合生成 style_hint
        style_hint = self._build_style_hint(traits, tag, stage, modifiers, openness)

        # 4. 确定 tone
        tone = self._determine_tone(traits, tag, modifiers, openness)

        # 5. echo_weight_modifier
        echo_weight_modifier = modifiers.echo_weight_delta

        # 6. 追问倾向
        follow_up = modifiers.curiosity_follow_up

        return ExpressionHint(
            style_hint=style_hint,
            tone=tone,
            echo_weight_modifier=echo_weight_modifier,
            追问倾向=follow_up,
        )

    def _build_style_hint(
        self,
        traits: PersonalityTraits,
        user_emotion_tag: str,
        bond_stage: str,
        modifiers: ReactivityModifiers,
        openness: float,
    ) -> str:
        """构建综合风格提示字符串。"""
        parts: list[str] = []

        # ── 核心表达策略（基于特质 × 情感场景）──────────────────────────
        _NEGATIVE = {"SAD", "ANGRY", "ANXIOUS", "FRUSTRATED", "CONFUSED"}
        _MILD_NEG = {"BORED", "FRUSTRATED", "CONFUSED"}
        _POSITIVE = {"HAPPY", "EXCITED", "LOVING", "PROUD", "GRATEFUL", "SURPRISED"}

        if modifiers.empathy_boost >= 0.2:
            if openness >= 0.7:
                parts.append("深度共情，用亲近的人才会说的话去安慰")
            else:
                parts.append("温柔共情，表达关心但保持分寸")

        if modifiers.humor_deflection >= 0.3:
            parts.append("可以用轻松幽默的方式化解气氛")

        if modifiers.curiosity_follow_up >= 0.2:
            parts.append("保持好奇心，自然地追问了解更多")

        # ── 特质驱动的基础风格 ────────────────────────────────────────────
        if not parts:
            if traits.warmth >= 0.7:
                parts.append("温柔真诚")
            if traits.playfulness >= 0.6 and openness >= 0.5:
                parts.append("可以适当俏皮")
            if traits.assertiveness >= 0.6:
                parts.append("语气自信")
            if not parts:
                parts.append("自然温和")

        # ── 关系阶段微调 ─────────────────────────────────────────────────
        if openness >= 0.8 and user_emotion_tag in _POSITIVE:
            parts.append("可以更自在地表达喜悦")
        if openness >= 0.8 and traits.playfulness >= 0.5:
            parts.append("偶尔撒个小娇也没关系")
        if openness < 0.4:
            parts.append("注意保持适当距离")

        return "，".join(parts)

    def _determine_tone(
        self,
        traits: PersonalityTraits,
        user_emotion_tag: str,
        modifiers: ReactivityModifiers,
        openness: float,
    ) -> str:
        """确定语气标签。"""
        _NEGATIVE = {"SAD", "ANGRY", "ANXIOUS", "FRUSTRATED"}

        # 高共情场景 → caring
        if modifiers.empathy_boost >= 0.2:
            return "caring"

        # 幽默化解场景 → playful
        if modifiers.humor_deflection >= 0.3:
            return "playful"

        # 积极情感 + 高俏皮 → playful
        if user_emotion_tag in {"HAPPY", "EXCITED", "LOVING"} and traits.playfulness >= 0.5:
            return "playful"

        # 消极情感 + 高温柔 → caring
        if user_emotion_tag in _NEGATIVE and traits.warmth >= 0.7:
            return "caring"

        # 高自信 → serious
        if traits.assertiveness >= 0.6:
            return "serious"

        # 默认 → gentle
        return "gentle"

    def build_prompt_section(self, hint: ExpressionHint) -> str:
        """将 ExpressionHint 格式化为 prompt 注入段落。

        Returns:
            可直接嵌入 system prompt 的文本段落
        """
        lines: list[str] = [
            "PERSONA_EXPRESSION_HINT:",
            f"- style_hint: {hint.style_hint}",
            f"- tone: {hint.tone}",
            f"- echo_weight_modifier: {hint.echo_weight_modifier:+.3f}",
            f"- follow_up_tendency: {hint.追问倾向:.3f}",
        ]

        # 追加行为指导（根据 tone 生成自然语言指导）
        _TONE_GUIDANCE: dict[str, str] = {
            "caring": "优先表达关心和陪伴，语气温柔体贴",
            "playful": "可以活泼一些，适当加入轻松的语气，但不要过度",
            "serious": "保持认真和专注，用心回应对方的需求",
            "gentle": "保持自然温和的语气，不需要刻意表演情绪",
        }
        guidance = _TONE_GUIDANCE.get(hint.tone, "保持真诚自然")
        lines.append(f"- guidance: {guidance}")

        return "\n".join(lines) + "\n\n"
