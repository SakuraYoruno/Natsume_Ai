"""Phase I 验收测试：PersonaEngine + PersonalityTraits 7 维特质向量。

验收标准：
  V-I-1: PersonaEngine 单例可从 Canon 加载 7 维特质向量
  V-I-2: get_traits(user_id) 返回 PersonalityTraits 对象，字段齐全且值在 [0,1] 范围内
  V-I-3: wc-003-persona-core.json 包含 personality_traits_baseline 字段
  V-I-4: PersonalityTraits 支持向量运算：加、减、缩放、距离计算
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from host_brain.persona.personality_traits import (
    DEFAULT_BASELINE,
    TRAIT_NAMES,
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


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_engine():
    """每个测试后重置 PersonaEngine 单例。"""
    yield
    PersonaEngine.reset_instance()


# ---------------------------------------------------------------------------
# V-I-1: PersonaEngine 单例可从 Canon 加载 7 维特质向量
# ---------------------------------------------------------------------------

class TestVI1_CanonLoad:
    """V-I-1: 从 Canon 加载人格基线。"""

    def test_engine_singleton_loads_baseline(self):
        """单例初始化后，基线特质与 Canon 一致。"""
        engine = PersonaEngine.get_instance()
        baseline = engine.baseline
        for name in TRAIT_NAMES:
            assert hasattr(baseline, name), f"基线缺少字段: {name}"
            value = getattr(baseline, name)
            assert value == pytest.approx(DEFAULT_BASELINE[name], abs=1e-4), (
                f"基线 {name}={value} 与默认值 {DEFAULT_BASELINE[name]} 不一致"
            )

    def test_engine_is_singleton(self):
        """多次 get_instance() 返回同一对象。"""
        a = PersonaEngine.get_instance()
        b = PersonaEngine.get_instance()
        assert a is b

    def test_engine_baseline_has_7_dimensions(self):
        """基线特质恰好有 7 个维度。"""
        engine = PersonaEngine.get_instance()
        baseline_dict = engine.baseline.as_dict()
        assert len(baseline_dict) == 7
        assert set(baseline_dict.keys()) == set(TRAIT_NAMES)


# ---------------------------------------------------------------------------
# V-I-2: get_traits(user_id) 返回完整 PersonalityTraits
# ---------------------------------------------------------------------------

class TestVI2_GetTraits:
    """V-I-2: get_traits 返回合法 PersonalityTraits。"""

    def test_get_traits_returns_personality_traits(self):
        """返回类型为 PersonalityTraits。"""
        engine = PersonaEngine.get_instance()
        traits = engine.get_traits("test_user_001")
        assert isinstance(traits, PersonalityTraits)

    def test_get_traits_all_fields_present(self):
        """所有 7 维字段均存在且在 [0, 1] 范围内。"""
        engine = PersonaEngine.get_instance()
        traits = engine.get_traits("test_user_002")
        for name in TRAIT_NAMES:
            value = getattr(traits, name)
            assert 0.0 <= value <= 1.0, f"{name}={value} 超出 [0,1] 范围"

    def test_get_traits_returns_copy(self):
        """返回的是副本，修改不影响引擎内部状态。"""
        engine = PersonaEngine.get_instance()
        traits1 = engine.get_traits("test_user_003")
        traits1.warmth = 0.0  # 修改副本
        traits2 = engine.get_traits("test_user_003")
        assert traits2.warmth == pytest.approx(DEFAULT_BASELINE["warmth"], abs=1e-4)

    def test_get_traits_first_access_equals_baseline(self):
        """首次访问时，特质与基线一致。"""
        engine = PersonaEngine.get_instance()
        traits = engine.get_traits("new_user")
        baseline = engine.baseline
        for name in TRAIT_NAMES:
            assert getattr(traits, name) == pytest.approx(
                getattr(baseline, name), abs=1e-4
            )


# ---------------------------------------------------------------------------
# V-I-3: Canon 文件包含 personality_traits_baseline
# ---------------------------------------------------------------------------

class TestVI3_CanonFile:
    """V-I-3: wc-003-persona-core.json 中存在 personality_traits_baseline。"""

    def test_canon_file_has_baseline_field(self):
        """Canon 文件包含 personality_traits_baseline 字段。"""
        canon_path = (
            Path(__file__).resolve().parent.parent.parent
            / "host_brain" / "canon" / "world" / "wc-003-persona-core.json"
        )
        assert canon_path.exists(), f"Canon 文件不存在: {canon_path}"
        raw = json.loads(canon_path.read_text(encoding="utf-8"))
        assert "personality_traits_baseline" in raw, (
            "Canon 文件缺少 personality_traits_baseline 字段"
        )

    def test_canon_baseline_has_7_keys(self):
        """Canon baseline 恰好有 7 个特质键。"""
        canon_path = (
            Path(__file__).resolve().parent.parent.parent
            / "host_brain" / "canon" / "world" / "wc-003-persona-core.json"
        )
        raw = json.loads(canon_path.read_text(encoding="utf-8"))
        baseline = raw["personality_traits_baseline"]
        assert set(baseline.keys()) == set(TRAIT_NAMES)

    def test_canon_baseline_values_match_defaults(self):
        """Canon baseline 值与代码中的默认值一致。"""
        canon_path = (
            Path(__file__).resolve().parent.parent.parent
            / "host_brain" / "canon" / "world" / "wc-003-persona-core.json"
        )
        raw = json.loads(canon_path.read_text(encoding="utf-8"))
        baseline = raw["personality_traits_baseline"]
        for name in TRAIT_NAMES:
            assert baseline[name] == pytest.approx(
                DEFAULT_BASELINE[name], abs=1e-4
            ), f"Canon {name}={baseline[name]} ≠ 默认值 {DEFAULT_BASELINE[name]}"


# ---------------------------------------------------------------------------
# V-I-4: PersonalityTraits 向量运算
# ---------------------------------------------------------------------------

class TestVI4_VectorOps:
    """V-I-4: 向量运算——加、减、缩放、距离。"""

    def test_add_traits_delta(self):
        """特质 + delta → 新特质（自动截断到 [0,1]）。"""
        traits = PersonalityTraits(warmth=0.5, curiosity=0.5, humor=0.5,
                                    sensitivity=0.5, assertiveness=0.5,
                                    playfulness=0.5, resilience=0.5)
        delta = TraitsDelta(warmth=0.3, humor=-0.8)
        result = traits + delta
        assert isinstance(result, PersonalityTraits)
        assert result.warmth == pytest.approx(0.8, abs=1e-4)
        assert result.humor == pytest.approx(0.0, abs=1e-4)  # 截断到 0

    def test_add_overflow_clamped(self):
        """加法溢出时截断到 1.0。"""
        traits = PersonalityTraits(warmth=0.9)
        delta = TraitsDelta(warmth=0.5)
        result = traits + delta
        assert result.warmth == pytest.approx(1.0, abs=1e-4)

    def test_sub_traits(self):
        """特质相减 → TraitsDelta。"""
        a = PersonalityTraits(warmth=0.8, curiosity=0.6)
        b = PersonalityTraits(warmth=0.5, curiosity=0.7)
        delta = a - b
        assert isinstance(delta, TraitsDelta)
        assert delta.warmth == pytest.approx(0.3, abs=1e-4)
        assert delta.curiosity == pytest.approx(-0.1, abs=1e-4)

    def test_mul_scalar(self):
        """标量缩放。"""
        traits = PersonalityTraits(warmth=0.5, curiosity=0.4)
        result = traits * 0.5
        assert result.warmth == pytest.approx(0.25, abs=1e-4)
        assert result.curiosity == pytest.approx(0.2, abs=1e-4)

    def test_rmul_scalar(self):
        """反向标量缩放。"""
        traits = PersonalityTraits(warmth=0.5)
        result = 2.0 * traits
        assert result.warmth == pytest.approx(1.0, abs=1e-4)  # 截断

    def test_distance_identical(self):
        """相同特质的距离为 0。"""
        a = PersonalityTraits.from_baseline()
        b = PersonalityTraits.from_baseline()
        assert a.distance(b) == pytest.approx(0.0, abs=1e-6)

    def test_distance_positive(self):
        """不同特质的距离 > 0。"""
        a = PersonalityTraits(warmth=0.0)
        b = PersonalityTraits(warmth=1.0)
        d = a.distance(b)
        assert d > 0.0
        # warmth 差 1.0，其余相同 → 距离 = 1.0
        assert d == pytest.approx(1.0, abs=1e-4)

    def test_to_vector_and_from_dict_roundtrip(self):
        """to_vector → as_dict → from_dict 往返一致。"""
        original = PersonalityTraits(warmth=0.75, curiosity=0.65, humor=0.35,
                                      sensitivity=0.85, assertiveness=0.25,
                                      playfulness=0.55, resilience=0.65)
        d = original.as_dict()
        restored = PersonalityTraits.from_dict(d)
        for name in TRAIT_NAMES:
            assert getattr(original, name) == pytest.approx(
                getattr(restored, name), abs=1e-4
            )

    def test_traits_delta_magnitude(self):
        """TraitsDelta 幅度计算。"""
        delta = TraitsDelta(warmth=0.03, curiosity=0.04)
        mag = delta.magnitude()
        expected = math.sqrt(0.03 ** 2 + 0.04 ** 2)
        assert mag == pytest.approx(expected, abs=1e-6)

    def test_traits_delta_max_abs_change(self):
        """TraitsDelta 最大绝对变化量。"""
        delta = TraitsDelta(warmth=0.05, curiosity=-0.08)
        assert delta.max_abs_change() == pytest.approx(0.08, abs=1e-4)


# ---------------------------------------------------------------------------
# 补充测试：PersonaEngine 成长 & 回滚
# ---------------------------------------------------------------------------

class TestPersonaEngineGrowth:
    """PersonaEngine 成长写入围栏和迁移回滚。"""

    def test_apply_growth_delta(self):
        """旧的直接成长写入必须关闭。"""
        engine = PersonaEngine.get_instance()
        delta = TraitsDelta(humor=0.05, playfulness=0.03)
        with pytest.raises(PersonaGrowthAuthorityError) as caught:
            engine.apply_growth_delta("growth_user", delta, reason="test_growth")
        assert caught.value.code == PERSONA_GROWTH_AUTHORITY_CLOSED

    def test_change_log_recorded(self):
        """显式迁移变化记录必须写入带 authority 的审计链。"""
        engine = PersonaEngine.get_instance()
        delta = TraitsDelta(warmth=0.02)
        engine.apply_migration_growth_delta(
            "log_user",
            delta,
            authority=PersonaGrowthMigrationAuthority(
                migration_id="persona-migration:phase1-log",
                actor_id="test:phase1",
                evidence_ref="test:test_persona_engine_phase1",
            ),
            reason="test_log",
        )
        records = engine.get_change_log("log_user")
        assert len(records) == 1
        assert records[0].reason == "test_log"
        assert records[0].authority_kind == "MIGRATION"
        assert isinstance(records[0], TraitChangeRecord)

    def test_rollback_last_change(self):
        """回滚最近变化后恢复到变化前的特质。"""
        engine = PersonaEngine.get_instance()
        original = engine.get_traits("rollback_user")
        delta = TraitsDelta(warmth=0.1)
        engine.apply_migration_growth_delta(
            "rollback_user",
            delta,
            authority=PersonaGrowthMigrationAuthority(
                migration_id="persona-migration:phase1-rollback",
                actor_id="test:phase1",
                evidence_ref="test:test_persona_engine_phase1",
            ),
            reason="to_rollback",
        )
        # 回滚
        restored = engine.rollback_last_change("rollback_user")
        assert restored is not None
        for name in TRAIT_NAMES:
            assert getattr(restored, name) == pytest.approx(
                getattr(original, name), abs=1e-4
            )

    def test_rollback_no_records(self):
        """无变化记录时回滚返回 None。"""
        engine = PersonaEngine.get_instance()
        result = engine.rollback_last_change("nonexistent_user")
        assert result is None


# ---------------------------------------------------------------------------
# 补充测试：ExpressionHint 基础
# ---------------------------------------------------------------------------

class TestExpressionHint:
    """ExpressionHint 数据结构。"""

    def test_expression_hint_as_dict(self):
        """ExpressionHint.as_dict() 输出合法。"""
        hint = ExpressionHint(
            style_hint="温柔共情",
            tone="caring",
            echo_weight_modifier=0.1,
        )
        d = hint.as_dict()
        assert d["style_hint"] == "温柔共情"
        assert d["tone"] == "caring"
        assert d["echo_weight_modifier"] == pytest.approx(0.1, abs=1e-4)

    def test_engine_get_expression_adapter(self):
        """PersonaEngine.get_expression_adapter() 返回 ExpressionHint。"""
        engine = PersonaEngine.get_instance()
        traits = engine.get_traits("expr_user")
        hint = engine.get_expression_adapter(traits, None, "FRIEND")
        assert isinstance(hint, ExpressionHint)
        assert hint.style_hint  # 非空


# ---------------------------------------------------------------------------
# 补充测试：SelfModelSnapshot 骨架
# ---------------------------------------------------------------------------

class TestSelfModelSnapshot:
    """SelfModelSnapshot Phase I 骨架。"""

    def test_get_self_model_snapshot(self):
        """get_self_model_snapshot 返回 SelfModelSnapshot。"""
        engine = PersonaEngine.get_instance()
        snap = engine.get_self_model_snapshot("snap_user")
        assert isinstance(snap, SelfModelSnapshot)
        assert snap.recent_mood_trend == "平稳"
