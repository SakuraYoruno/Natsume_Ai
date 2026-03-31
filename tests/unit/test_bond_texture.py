"""M32-H：Bond 质感层专项测试（T-BOND-TEX-01~04）"""

from __future__ import annotations

import pytest
from host_brain.bond.bond_stage_engine import BondEmotionalTexture, BondScoreState


class TestBondTexture:
    """T-BOND-TEX-01~04：BondEmotionalTexture 情感质感层测试"""

    def test_short_response_gap_increases_intimacy(self):
        """T-BOND-TEX-01：response_gap_ms < 5000 时，intimacy_temperature 上升"""
        texture = BondEmotionalTexture(
            avg_response_gap_ms=30000.0,
            recent_emotion_valence=0.0,
            intimacy_temperature=0.5,
        )
        updated = texture.update(response_gap_ms=2000.0)  # 2秒，非常快
        # 快速回复应提升 intimacy_temperature
        assert updated.intimacy_temperature >= texture.intimacy_temperature

    def test_positive_valence_returns_warm_style_hint(self):
        """T-BOND-TEX-02：recent_emotion_valence > 0.5 时，style_hint() 含温暖描述"""
        texture = BondEmotionalTexture(
            avg_response_gap_ms=5000.0,
            recent_emotion_valence=0.8,
            intimacy_temperature=0.8,
        )
        hint = texture.style_hint()
        assert isinstance(hint, str)
        assert len(hint) > 0
        # 高 intimacy_temperature 应返回温暖描述
        assert "温暖" in hint or "亲密" in hint or "主动" in hint

    def test_texture_does_not_affect_bond_score(self):
        """T-BOND-TEX-03：BondEmotionalTexture 不影响 Bond 积分计算"""
        state = BondScoreState(user_id="user_tex_test")
        initial_score = state.total_score
        # 更新质感层
        state.texture = state.texture.update(response_gap_ms=1000.0, emotion_valence=0.9)
        # Bond 积分不变
        assert state.total_score == initial_score

    def test_default_texture_no_error(self):
        """T-BOND-TEX-04：texture 字段缺失时使用默认值，不报错"""
        state = BondScoreState(user_id="user_default_tex")
        # 默认 texture 应该存在且有合理值
        assert hasattr(state, "texture")
        assert isinstance(state.texture, BondEmotionalTexture)
        assert 0.0 <= state.texture.intimacy_temperature <= 1.0

    def test_style_hint_low_temperature(self):
        """低 intimacy_temperature 时，style_hint() 返回疏远描述"""
        texture = BondEmotionalTexture(
            avg_response_gap_ms=120000.0,  # 2分钟，很慢
            recent_emotion_valence=-0.5,
            intimacy_temperature=0.1,
        )
        hint = texture.style_hint()
        assert "疏远" in hint or "礼貌" in hint or "距离" in hint

    def test_update_returns_new_instance(self):
        """update() 返回新实例，不修改原实例"""
        texture = BondEmotionalTexture()
        updated = texture.update(response_gap_ms=1000.0)
        assert updated is not texture
        # 原实例不变
        assert texture.avg_response_gap_ms == 30000.0

    def test_intimacy_temperature_in_valid_range(self):
        """intimacy_temperature 始终在 [0.0, 1.0] 范围内"""
        texture = BondEmotionalTexture()
        # 极端情况：非常快的回复 + 非常积极的情感
        updated = texture.update(response_gap_ms=100.0, emotion_valence=1.0)
        assert 0.0 <= updated.intimacy_temperature <= 1.0
        # 极端情况：非常慢的回复 + 非常消极的情感
        updated2 = texture.update(response_gap_ms=600000.0, emotion_valence=-1.0)
        assert 0.0 <= updated2.intimacy_temperature <= 1.0
