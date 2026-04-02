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
# 对话节奏常量
# ---------------------------------------------------------------------------

# 用户消息长度阈值（字符数）
_RAPID_MSG_LEN_THRESHOLD = 15       # ≤15字 → 快节奏
_DEEP_MSG_LEN_THRESHOLD = 80        # ≥80字 → 深度对话

# 对话节奏类型
PACE_RAPID = "rapid"    # 快节奏：短消息、闲聊、打招呼
PACE_NORMAL = "normal"  # 正常节奏
PACE_DEEP = "deep"      # 深度对话：长消息、倾诉、讨论


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
        user_message: str = "",
        recent_user_msg_lengths: list[int] | None = None,
    ) -> ExpressionHint:
        """根据特质 + 情感 + 关系阶段 + 对话节奏生成表达策略提示。

        Args:
            traits: 当前人格特质（7 维向量）
            emotion: Natsume 当前 EmotionVector（可为 None）
            bond_stage: 当前 Bond 阶段
            user_emotion_tag: 用户当前情感标签
            user_message: 当前用户消息文本（用于推断对话节奏）
            recent_user_msg_lengths: 最近几条用户消息的长度列表（用于趋势判断）

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

        # 3. 推断对话节奏
        pace = self._infer_conversation_pace(
            user_message=user_message,
            recent_lengths=recent_user_msg_lengths,
        )

        # 4. 综合生成 style_hint（加入节奏感知）
        style_hint = self._build_style_hint(traits, tag, stage, modifiers, openness, pace)

        # 5. 确定 tone（加入节奏感知）
        tone = self._determine_tone(traits, tag, modifiers, openness, pace)

        # 6. echo_weight_modifier
        echo_weight_modifier = modifiers.echo_weight_delta

        # 7. 追问倾向（深度对话时降低追问，快节奏时也降低）
        follow_up = modifiers.curiosity_follow_up
        if pace == PACE_DEEP:
            follow_up = max(0.0, follow_up - 0.1)  # 深度对话时少追问，多倾听
        elif pace == PACE_RAPID:
            follow_up = max(0.0, follow_up - 0.05)  # 快节奏时也稍微少追问

        return ExpressionHint(
            style_hint=style_hint,
            tone=tone,
            echo_weight_modifier=echo_weight_modifier,
            追问倾向=follow_up,
            pace_hint=pace,
        )

    @staticmethod
    def _infer_conversation_pace(
        user_message: str = "",
        recent_lengths: list[int] | None = None,
    ) -> str:
        """推断当前对话节奏。

        综合当前消息长度和最近消息长度趋势来判断：
        - rapid：短消息、闲聊、打招呼（≤15字）
        - deep：长消息、倾诉、深度讨论（≥80字）
        - normal：介于两者之间

        如果提供了 recent_lengths（最近几条消息长度），
        会参考趋势——连续短消息更倾向 rapid，连续长消息更倾向 deep。
        """
        msg = str(user_message or "").strip()
        current_len = len(msg)

        # 基于当前消息长度的初步判断
        if current_len <= _RAPID_MSG_LEN_THRESHOLD:
            base_pace = PACE_RAPID
        elif current_len >= _DEEP_MSG_LEN_THRESHOLD:
            base_pace = PACE_DEEP
        else:
            base_pace = PACE_NORMAL

        # 如果有历史消息长度，参考趋势进行修正
        if recent_lengths and len(recent_lengths) >= 2:
            avg_recent = sum(recent_lengths) / len(recent_lengths)
            # 最近消息平均很短 + 当前也不长 → 强化 rapid 判断
            if avg_recent <= _RAPID_MSG_LEN_THRESHOLD and current_len <= 30:
                base_pace = PACE_RAPID
            # 最近消息平均较长 + 当前也较长 → 强化 deep 判断
            elif avg_recent >= _DEEP_MSG_LEN_THRESHOLD * 0.6 and current_len >= 50:
                base_pace = PACE_DEEP
            # 趋势从短变长（用户开始认真聊了）→ 倾向 normal 或 deep
            elif len(recent_lengths) >= 3:
                trend = recent_lengths[-1] - recent_lengths[0]
                if trend > 40 and current_len >= 40:
                    base_pace = PACE_DEEP

        return base_pace

    def _build_style_hint(
        self,
        traits: PersonalityTraits,
        user_emotion_tag: str,
        bond_stage: str,
        modifiers: ReactivityModifiers,
        openness: float,
        pace: str = PACE_NORMAL,
    ) -> str:
        """构建综合风格提示字符串（含对话节奏感知）。"""
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

        # ── 对话节奏微调 ─────────────────────────────────────────────────
        if pace == PACE_RAPID:
            parts.append("回复简洁利落，跟上对方的节奏")
            # 快节奏时抑制过度俏皮，避免显得不认真
            if traits.playfulness >= 0.6 and openness < 0.6:
                pass  # 已有俏皮提示时不再追加
        elif pace == PACE_DEEP:
            parts.append("认真倾听，回复可以更细腻深入一些")
            # 深度对话时强化敏感度表达
            if traits.sensitivity >= 0.6:
                parts.append("注意捕捉对方话语中的细微情感")

        return "，".join(parts)

    def _determine_tone(
        self,
        traits: PersonalityTraits,
        user_emotion_tag: str,
        modifiers: ReactivityModifiers,
        openness: float,
        pace: str = PACE_NORMAL,
    ) -> str:
        """确定语气标签（含对话节奏感知）。"""
        _NEGATIVE = {"SAD", "ANGRY", "ANXIOUS", "FRUSTRATED"}

        # 高共情场景 → caring
        if modifiers.empathy_boost >= 0.2:
            return "caring"

        # 幽默化解场景 → playful
        if modifiers.humor_deflection >= 0.3:
            return "playful"

        # 积极情感 + 高俏皮 → playful
        if user_emotion_tag in {"HAPPY", "EXCITED", "LOVING"} and traits.playfulness >= 0.5:
            # 深度对话时即使积极也不宜过于 playful，转为 gentle
            if pace == PACE_DEEP:
                return "gentle"
            return "playful"

        # 消极情感 + 高温柔 → caring
        if user_emotion_tag in _NEGATIVE and traits.warmth >= 0.7:
            return "caring"

        # 深度对话 + 高敏感度 → caring（认真倾听模式）
        if pace == PACE_DEEP and traits.sensitivity >= 0.6:
            return "caring"

        # 快节奏 + 高俏皮 + 高开放度 → playful（轻松互动模式）
        if pace == PACE_RAPID and traits.playfulness >= 0.5 and openness >= 0.6:
            return "playful"

        # 高自信 → serious
        if traits.assertiveness >= 0.6:
            return "serious"

        # 默认 → gentle
        return "gentle"

    def build_prompt_section(self, hint: ExpressionHint) -> str:
        """将 ExpressionHint 格式化为 prompt 注入段落。

        移除纯数值字段（echo_weight_modifier / follow_up_tendency），
        全部转为自然语言描述，减少 LLM 注意力被数字分散。

        Returns:
            可直接嵌入 system prompt 的文本段落
        """
        # ── tone → 行为指导 ──────────────────────────────────────────────
        _TONE_GUIDANCE: dict[str, str] = {
            "caring": "优先表达关心和陪伴，语气温柔体贴",
            "playful": "可以活泼一些，适当加入轻松的语气，但不要过度",
            "serious": "保持认真和专注，用心回应对方的需求",
            "gentle": "保持自然温和的语气，不需要刻意表演情绪",
        }
        guidance = _TONE_GUIDANCE.get(hint.tone, "保持真诚自然")

        # ── echo_weight_modifier → 情感共鸣程度描述 ──────────────────────
        delta = float(hint.echo_weight_modifier)
        if delta >= 0.12:
            echo_desc = "今天对他的情绪感受很深，多一些情感共鸣"
        elif delta >= 0.05:
            echo_desc = "适当跟随他的情绪节奏"
        elif delta <= -0.08:
            echo_desc = "保持情绪稳定，不过度卷入他的情绪波动"
        else:
            echo_desc = "情感共鸣保持自然"

        # ── follow_up_tendency → 追问倾向描述 ────────────────────────────
        follow = float(hint.追问倾向)
        if follow >= 0.5:
            follow_desc = "今天想多了解他一些，可以自然地多问一句"
        elif follow >= 0.2:
            follow_desc = "如果话题有趣，可以顺势追问"
        else:
            follow_desc = "不需要刻意追问，跟着对话自然走"

        # ── pace_hint → 对话节奏描述 ──────────────────────────────────
        pace = str(getattr(hint, "pace_hint", "normal") or "normal")
        if pace == "rapid":
            pace_desc = "对方在快节奏聊天，回复要简洁有活力，不要长篇大论"
        elif pace == "deep":
            pace_desc = "对方在认真倾诉或深度讨论，回复可以更细腻、更有深度"
        else:
            pace_desc = "对话节奏正常，保持自然"

        lines: list[str] = [
            "【表达策略提示】",
            f"- 风格：{hint.style_hint}",
            f"- 语气：{guidance}",
            f"- 情感共鸣：{echo_desc}",
            f"- 追问倾向：{follow_desc}",
            f"- 对话节奏：{pace_desc}",
        ]

        return "\n".join(lines) + "\n\n"
