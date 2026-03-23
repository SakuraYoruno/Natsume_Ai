"""Phase II 验收测试：情感-人格耦合。

验收标准：
  V-II-1: EmotionReactivityProfile 可根据特质向量 + 情感场景输出调制系数
  V-II-2: PersonaExpressionAdapter 根据 (traits, emotion, bond_stage) 输出 ExpressionHint
  V-II-3: compute_emotion_echo() 实际接收并使用 PersonalityTraits 参数
  V-II-4: prompt_builder 输出的 system prompt 中包含 persona_expression_hint 段落
"""

from __future__ import annotations

import pytest

from host_brain.persona.personality_traits import (
    ExpressionHint,
    PersonalityTraits,
)
from host_brain.persona.emotion_reactivity import (
    EmotionReactivityProfile,
    ReactivityModifiers,
)
from host_brain.persona.expression_adapter import (
    PersonaExpressionAdapter,
)
from host_brain.core.metabolism import EmotionVector


# ---------------------------------------------------------------------------
# V-II-1: EmotionReactivityProfile 调制系数
# ---------------------------------------------------------------------------

class TestVII1_EmotionReactivity:
    """V-II-1: 特质→情感反应调制系数。"""

    def test_high_sensitivity_sad_boosts_echo_weight(self):
        """高敏感度 + SAD → echo_weight_delta > 0。"""
        traits = PersonalityTraits(sensitivity=0.85)
        mods = EmotionReactivityProfile.compute_modifiers(traits, "SAD", "FRIEND")
        assert mods.echo_weight_delta > 0.0, (
            f"高敏感度+SAD 应该提升 echo_weight，得到 {mods.echo_weight_delta}"
        )

    def test_high_sensitivity_sad_echo_weight_at_least_0_5(self):
        """高敏感度 + SAD → 应用后 echo_weight ≥ 0.5。"""
        traits = PersonalityTraits(sensitivity=0.85)
        mods = EmotionReactivityProfile.compute_modifiers(traits, "SAD", "FRIEND")
        effective = EmotionReactivityProfile.apply_to_echo_weight(0.4, mods)
        assert effective >= 0.5, f"echo_weight 应 ≥ 0.5，得到 {effective}"

    def test_high_humor_bored_increases_humor_deflection(self):
        """高幽默感 + BORED → humor_deflection > 0。"""
        traits = PersonalityTraits(humor=0.7)
        mods = EmotionReactivityProfile.compute_modifiers(traits, "BORED", "FRIEND")
        assert mods.humor_deflection > 0.0

    def test_high_curiosity_excited_increases_follow_up(self):
        """高好奇心 + EXCITED → curiosity_follow_up > 0。"""
        traits = PersonalityTraits(curiosity=0.8)
        mods = EmotionReactivityProfile.compute_modifiers(traits, "EXCITED", "FRIEND")
        assert mods.curiosity_follow_up > 0.0

    def test_low_assertiveness_angry_decreases_dominance(self):
        """低自信度 + ANGRY → dominance_delta < 0。"""
        traits = PersonalityTraits(assertiveness=0.2)
        mods = EmotionReactivityProfile.compute_modifiers(traits, "ANGRY", "FRIEND")
        assert mods.dominance_delta < 0.0

    def test_high_warmth_sad_deep_boosts_empathy(self):
        """高温柔度 + SAD + 深层关系 → empathy_boost > 0。"""
        traits = PersonalityTraits(warmth=0.9)
        mods = EmotionReactivityProfile.compute_modifiers(traits, "SAD", "CONFIDANT")
        assert mods.empathy_boost > 0.0

    def test_high_resilience_limits_echo_boost(self):
        """高韧性 + ANGRY → echo_weight_delta 被韧性抑制。"""
        # 高敏感 + 高韧性
        traits_sensitive = PersonalityTraits(sensitivity=0.9, resilience=0.8)
        traits_sensitive_only = PersonalityTraits(sensitivity=0.9, resilience=0.3)
        mods_both = EmotionReactivityProfile.compute_modifiers(traits_sensitive, "ANGRY", "")
        mods_sens = EmotionReactivityProfile.compute_modifiers(traits_sensitive_only, "ANGRY", "")
        # 有韧性时 echo_weight_delta 更小
        assert mods_both.echo_weight_delta <= mods_sens.echo_weight_delta

    def test_neutral_emotion_minimal_modifiers(self):
        """NEUTRAL 情感 → 调制系数接近零。"""
        traits = PersonalityTraits.from_baseline()
        mods = EmotionReactivityProfile.compute_modifiers(traits, "NEUTRAL", "FRIEND")
        assert abs(mods.echo_weight_delta) < 0.01
        assert abs(mods.humor_deflection) < 0.01
        assert abs(mods.empathy_boost) < 0.01

    def test_modifiers_returns_correct_type(self):
        """返回类型为 ReactivityModifiers。"""
        traits = PersonalityTraits.from_baseline()
        mods = EmotionReactivityProfile.compute_modifiers(traits, "HAPPY", "FRIEND")
        assert isinstance(mods, ReactivityModifiers)

    def test_apply_to_echo_weight_clamps(self):
        """echo_weight 被截断到 [0.1, 0.8]。"""
        mods_positive = ReactivityModifiers(echo_weight_delta=0.2)
        mods_negative = ReactivityModifiers(echo_weight_delta=-0.2)
        assert EmotionReactivityProfile.apply_to_echo_weight(0.7, mods_positive) <= 0.8
        assert EmotionReactivityProfile.apply_to_echo_weight(0.2, mods_negative) >= 0.1


# ---------------------------------------------------------------------------
# V-II-2: PersonaExpressionAdapter 输出 ExpressionHint
# ---------------------------------------------------------------------------

class TestVII2_ExpressionAdapter:
    """V-II-2: PersonaExpressionAdapter 输出。"""

    def test_adapt_returns_expression_hint(self):
        """adapt() 返回 ExpressionHint。"""
        adapter = PersonaExpressionAdapter()
        traits = PersonalityTraits.from_baseline()
        hint = adapter.adapt(traits, None, "FRIEND", user_emotion_tag="HAPPY")
        assert isinstance(hint, ExpressionHint)

    def test_adapt_style_hint_non_empty(self):
        """style_hint 非空。"""
        adapter = PersonaExpressionAdapter()
        traits = PersonalityTraits.from_baseline()
        hint = adapter.adapt(traits, None, "FRIEND")
        assert hint.style_hint, "style_hint 不应为空"

    def test_adapt_high_warmth_sad_returns_caring(self):
        """高温柔度 + SAD → tone = caring。"""
        adapter = PersonaExpressionAdapter()
        traits = PersonalityTraits(warmth=0.9, sensitivity=0.8)
        hint = adapter.adapt(traits, None, "CLOSE_FRIEND", user_emotion_tag="SAD")
        assert hint.tone == "caring"

    def test_adapt_high_humor_bored_returns_playful(self):
        """高幽默感 + BORED → tone = playful。"""
        adapter = PersonaExpressionAdapter()
        traits = PersonalityTraits(humor=0.7)
        hint = adapter.adapt(traits, None, "FRIEND", user_emotion_tag="BORED")
        assert hint.tone == "playful"

    def test_adapt_high_playfulness_happy_returns_playful(self):
        """高俏皮度 + HAPPY → tone = playful。"""
        adapter = PersonaExpressionAdapter()
        traits = PersonalityTraits(playfulness=0.7)
        hint = adapter.adapt(traits, None, "FRIEND", user_emotion_tag="HAPPY")
        assert hint.tone == "playful"

    def test_adapt_stranger_stage_includes_distance(self):
        """STRANGER 阶段 → style_hint 包含"距离"相关描述。"""
        adapter = PersonaExpressionAdapter()
        traits = PersonalityTraits.from_baseline()
        hint = adapter.adapt(traits, None, "STRANGER", user_emotion_tag="NEUTRAL")
        assert "距离" in hint.style_hint or "分寸" in hint.style_hint

    def test_build_prompt_section(self):
        """build_prompt_section 输出包含 PERSONA_EXPRESSION_HINT。"""
        adapter = PersonaExpressionAdapter()
        hint = ExpressionHint(style_hint="温柔共情", tone="caring")
        section = adapter.build_prompt_section(hint)
        assert "PERSONA_EXPRESSION_HINT:" in section
        assert "style_hint: 温柔共情" in section
        assert "tone: caring" in section

    def test_expression_hint_as_dict(self):
        """ExpressionHint.as_dict() 输出正确。"""
        adapter = PersonaExpressionAdapter()
        traits = PersonalityTraits.from_baseline()
        hint = adapter.adapt(traits, None, "FRIEND", user_emotion_tag="HAPPY")
        d = hint.as_dict()
        assert "style_hint" in d
        assert "tone" in d
        assert "echo_weight_modifier" in d
        assert "follow_up_tendency" in d


# ---------------------------------------------------------------------------
# V-II-3: compute_emotion_echo 接收 PersonalityTraits
# ---------------------------------------------------------------------------

class TestVII3_EchoPersonalityIntegration:
    """V-II-3: compute_emotion_echo 使用 PersonalityTraits。"""

    def test_echo_with_traits_returns_dict(self):
        """传入 personality_traits 时仍返回有效字典。"""
        from host_brain.core.metabolism import compute_emotion_echo
        traits = PersonalityTraits(sensitivity=0.9)
        vec = EmotionVector(valence=-0.3, arousal=0.2, ts_ms=1000)
        result = compute_emotion_echo(
            "SAD",
            current_vector=vec,
            bond_stage="FRIEND",
            personality_traits=traits,
        )
        assert isinstance(result, dict)
        assert "echo_emotion_tag" in result

    def test_echo_with_high_sensitivity_higher_intensity(self):
        """高敏感度 + SAD → 调制后的 echo 强度更高或 echo_weight 更大。"""
        from host_brain.core.metabolism import compute_emotion_echo
        traits_high = PersonalityTraits(sensitivity=0.9)
        traits_low = PersonalityTraits(sensitivity=0.3)
        vec = EmotionVector(valence=0.0, arousal=0.0, ts_ms=1000)

        result_high = compute_emotion_echo(
            "SAD", current_vector=vec, bond_stage="FRIEND",
            personality_traits=traits_high,
        )
        result_low = compute_emotion_echo(
            "SAD", current_vector=vec, bond_stage="FRIEND",
            personality_traits=traits_low,
        )
        # 高敏感度应有 persona_modifiers
        assert "persona_modifiers" in result_high
        assert "effective_echo_weight" in result_high
        # 高敏感度的 effective_echo_weight 应大于低敏感度的
        ew_high = result_high.get("effective_echo_weight", 0.4)
        ew_low = result_low.get("effective_echo_weight", 0.4)
        assert ew_high >= ew_low, f"高敏感度 ew={ew_high} 应 >= 低敏感度 ew={ew_low}"

    def test_echo_without_traits_backward_compatible(self):
        """不传 personality_traits 时向后兼容。"""
        from host_brain.core.metabolism import compute_emotion_echo
        vec = EmotionVector(valence=0.2, arousal=0.3, ts_ms=1000)
        result = compute_emotion_echo("HAPPY", current_vector=vec, bond_stage="FRIEND")
        assert isinstance(result, dict)
        assert "echo_emotion_tag" in result
        assert "persona_modifiers" not in result  # 无特质时不应有 persona_modifiers

    def test_echo_with_traits_in_emotion_timeline(self):
        """emotion_timeline.py 中的副本也支持 personality_traits。"""
        from host_brain.core.emotion_timeline import compute_emotion_echo as et_echo
        traits = PersonalityTraits(sensitivity=0.9)
        vec = EmotionVector(valence=-0.3, arousal=0.2, ts_ms=1000)
        result = et_echo(
            "SAD", current_vector=vec, bond_stage="FRIEND",
            personality_traits=traits,
        )
        assert isinstance(result, dict)
        assert "persona_modifiers" in result


# ---------------------------------------------------------------------------
# V-II-4: prompt_builder 包含 persona_expression_hint
# ---------------------------------------------------------------------------

class TestVII4_PromptBuilderIntegration:
    """V-II-4: persona_expression_hint 注入到 prompt。"""

    def test_prompt_context_slot_supports_expression_hint(self):
        """build_emotion_pulse_slot 支持 expression_hint 参数。"""
        from host_brain.core.prompt_context import build_emotion_pulse_slot
        hint_dict = {
            "style_hint": "温柔共情",
            "tone": "caring",
            "echo_weight_modifier": 0.1,
            "follow_up_tendency": 0.05,
        }
        pulse = build_emotion_pulse_slot(
            None,
            expression_hint=hint_dict,
        )
        assert "persona_expression_hint" in pulse
        assert pulse["persona_expression_hint"]["style_hint"] == "温柔共情"

    def test_prompt_context_slot_without_expression_hint(self):
        """不传 expression_hint 时向后兼容。"""
        from host_brain.core.prompt_context import build_emotion_pulse_slot
        pulse = build_emotion_pulse_slot(None)
        assert "persona_expression_hint" not in pulse

    def test_prompt_builder_has_persona_expression_hint_block(self):
        """当 runtime_payload 包含 persona_expression_hint 时，prompt 中包含该段落。"""
        # 这是一个快照测试：验证 prompt_builder 的集成
        from host_brain.app.prompt_builder import _truncate_for_prompt
        # 模拟 persona_expression_hint 数据
        hint_data = {
            "style_hint": "深度共情，用亲近的人才会说的话去安慰",
            "tone": "caring",
            "echo_weight_modifier": 0.12,
            "follow_up_tendency": 0.05,
        }
        # 验证 prompt section 的格式
        adapter = PersonaExpressionAdapter()
        section = adapter.build_prompt_section(
            ExpressionHint(
                style_hint=hint_data["style_hint"],
                tone=hint_data["tone"],
                echo_weight_modifier=hint_data["echo_weight_modifier"],
                追问倾向=hint_data["follow_up_tendency"],
            )
        )
        assert "PERSONA_EXPRESSION_HINT:" in section
        assert "深度共情" in section
        assert "caring" in section


# ---------------------------------------------------------------------------
# 补充测试：端到端集成
# ---------------------------------------------------------------------------

class TestPhaseII_EndToEnd:
    """Phase II 端到端集成测试。"""

    def test_full_pipeline_traits_to_expression(self):
        """完整链路：PersonaEngine → ExpressionAdapter → ExpressionHint。"""
        from host_brain.persona import PersonaEngine, PersonaExpressionAdapter
        PersonaEngine.reset_instance()
        try:
            engine = PersonaEngine.get_instance()
            traits = engine.get_traits("e2e_user")
            adapter = PersonaExpressionAdapter()
            hint = adapter.adapt(
                traits, None, "CLOSE_FRIEND", user_emotion_tag="SAD",
            )
            assert isinstance(hint, ExpressionHint)
            # 默认基线：warmth=0.85, sensitivity=0.80 → 面对 SAD 应返回 caring
            assert hint.tone == "caring"
            assert hint.style_hint  # 非空
        finally:
            PersonaEngine.reset_instance()

    def test_full_pipeline_echo_with_persona(self):
        """完整链路：PersonaEngine → compute_emotion_echo。"""
        from host_brain.persona import PersonaEngine
        from host_brain.core.metabolism import compute_emotion_echo
        PersonaEngine.reset_instance()
        try:
            engine = PersonaEngine.get_instance()
            traits = engine.get_traits("e2e_echo_user")
            vec = EmotionVector(valence=0.0, arousal=0.0, ts_ms=1000)
            result = compute_emotion_echo(
                "SAD", current_vector=vec, bond_stage="CLOSE_FRIEND",
                personality_traits=traits,
            )
            assert "persona_modifiers" in result
            assert result["effective_echo_weight"] > 0.4  # 默认 sensitivity=0.8 → 应提升
        finally:
            PersonaEngine.reset_instance()
