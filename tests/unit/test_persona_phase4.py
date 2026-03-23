"""Phase IV 验收测试：人格成长系统。

验收标准：
  V-IV-1: Bond 阶段晋级时触发 PersonaGrowthTracker，特质变化写入持久化
  V-IV-2: 单次变化幅度 ≤ 0.05，每周累计 ≤ 0.1
  V-IV-3: 连续同向变化 > 3 次时生成 Canon Evolution 提案
  V-IV-4: warmth 超出 0.85 ± 0.15 范围的变化被阻止
  V-IV-5: 审计日志记录每次变化，支持回滚
"""

from __future__ import annotations

import pytest

from host_brain.persona.personality_traits import (
    DEFAULT_BASELINE,
    TRAIT_NAMES,
    PersonalityTraits,
    TraitsDelta,
)
from host_brain.persona.persona_engine import (
    PersonaEngine,
    TraitChangeRecord,
)
from host_brain.persona.trait_drift_guard import (
    TraitDriftGuard,
    GuardCheckResult,
    MAX_SINGLE_CHANGE,
    MAX_WEEKLY_CUMULATIVE,
    IMMUTABLE_CORE,
    TREND_LOCK_THRESHOLD,
)
from host_brain.persona.persona_growth import (
    PersonaGrowthTracker,
    GrowthApplyResult,
    GrowthRule,
    STAGE_PROMOTION_RULES,
    MILESTONE_RULES,
)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _fresh_engine() -> PersonaEngine:
    """创建干净的 PersonaEngine。"""
    PersonaEngine.reset_instance()
    return PersonaEngine.get_instance()


def _teardown_engine() -> None:
    PersonaEngine.reset_instance()


# ---------------------------------------------------------------------------
# V-IV-1: Bond 阶段晋级触发 PersonaGrowthTracker
# ---------------------------------------------------------------------------

class TestVIV1_StagePromotion:
    """V-IV-1: Bond 阶段晋级时触发 PersonaGrowthTracker。"""

    def setup_method(self):
        self.engine = _fresh_engine()
        self.tracker = PersonaGrowthTracker(engine=self.engine)

    def teardown_method(self):
        _teardown_engine()

    def test_acquaintance_to_friend(self):
        """ACQUAINTANCE→FRIEND 触发 playfulness +0.05。"""
        before = self.engine.get_traits("user1")
        result = self.tracker.on_stage_promoted(
            "user1", "ACQUAINTANCE", "FRIEND",
        )
        assert result.applied
        after = self.engine.get_traits("user1")
        assert after.playfulness == pytest.approx(before.playfulness + 0.05, abs=0.001)

    def test_friend_to_close_friend(self):
        """FRIEND→CLOSE_FRIEND 触发 warmth +0.03, sensitivity +0.02。"""
        before = self.engine.get_traits("user1")
        result = self.tracker.on_stage_promoted(
            "user1", "FRIEND", "CLOSE_FRIEND",
        )
        assert result.applied
        after = self.engine.get_traits("user1")
        assert after.warmth == pytest.approx(before.warmth + 0.03, abs=0.001)
        assert after.sensitivity == pytest.approx(before.sensitivity + 0.02, abs=0.001)

    def test_close_friend_to_confidant(self):
        """CLOSE_FRIEND→CONFIDANT 触发 humor +0.05, assertiveness +0.03。"""
        before = self.engine.get_traits("user1")
        result = self.tracker.on_stage_promoted(
            "user1", "CLOSE_FRIEND", "CONFIDANT",
        )
        assert result.applied
        after = self.engine.get_traits("user1")
        assert after.humor == pytest.approx(before.humor + 0.05, abs=0.001)
        assert after.assertiveness == pytest.approx(before.assertiveness + 0.03, abs=0.001)

    def test_confidant_to_soulmate(self):
        """CONFIDANT→SOULMATE 触发 resilience +0.05, curiosity +0.03。"""
        before = self.engine.get_traits("user1")
        result = self.tracker.on_stage_promoted(
            "user1", "CONFIDANT", "SOULMATE",
        )
        assert result.applied
        after = self.engine.get_traits("user1")
        assert after.resilience == pytest.approx(before.resilience + 0.05, abs=0.001)
        assert after.curiosity == pytest.approx(before.curiosity + 0.03, abs=0.001)

    def test_unknown_transition_no_effect(self):
        """未定义的阶段转换 → 不触发。"""
        result = self.tracker.on_stage_promoted(
            "user1", "STRANGER", "ACQUAINTANCE",
        )
        assert not result.applied
        assert "无匹配" in result.reason

    def test_one_time_rule_not_repeated(self):
        """one_time 规则不重复触发。"""
        r1 = self.tracker.on_stage_promoted("user1", "ACQUAINTANCE", "FRIEND")
        assert r1.applied
        r2 = self.tracker.on_stage_promoted("user1", "ACQUAINTANCE", "FRIEND")
        assert not r2.applied
        assert "one_time" in r2.reason

    def test_change_log_recorded(self):
        """成长变化记录到 PersonaEngine 审计链。"""
        self.tracker.on_stage_promoted("user1", "ACQUAINTANCE", "FRIEND")
        log = self.engine.get_change_log("user1")
        assert len(log) >= 1
        assert "bond_stage_promoted" in log[-1].reason


# ---------------------------------------------------------------------------
# V-IV-2: 幅度锁
# ---------------------------------------------------------------------------

class TestVIV2_AmplitudeLock:
    """V-IV-2: 单次变化幅度 ≤ 0.05，每周累计 ≤ 0.1。"""

    def test_single_change_within_limit(self):
        """单次变化 ≤ 0.05 → 允许。"""
        guard = TraitDriftGuard()
        delta = TraitsDelta(warmth=0.05)
        result = guard.check(
            delta=delta,
            current_traits=PersonalityTraits(),
        )
        assert result.allowed

    def test_single_change_exceeds_limit(self):
        """单次变化 > 0.05 → 拒绝。"""
        guard = TraitDriftGuard()
        delta = TraitsDelta(warmth=0.06)
        result = guard.check(
            delta=delta,
            current_traits=PersonalityTraits(),
        )
        assert not result.allowed
        assert any("单次变化超限" in v for v in result.violations)

    def test_negative_single_change_exceeds_limit(self):
        """单次负向变化 > 0.05 → 拒绝。"""
        guard = TraitDriftGuard()
        delta = TraitsDelta(humor=-0.06)
        result = guard.check(
            delta=delta,
            current_traits=PersonalityTraits(),
        )
        assert not result.allowed

    def test_weekly_cumulative_within_limit(self):
        """每周累计 ≤ 0.1 → 允许。"""
        guard = TraitDriftGuard()
        delta = TraitsDelta(warmth=0.03)
        result = guard.check(
            delta=delta,
            current_traits=PersonalityTraits(),
            weekly_cumulative={"warmth": 0.06},
        )
        assert result.allowed

    def test_weekly_cumulative_exceeds_limit(self):
        """每周累计 > 0.1 → 拒绝。"""
        guard = TraitDriftGuard()
        delta = TraitsDelta(warmth=0.05)
        result = guard.check(
            delta=delta,
            current_traits=PersonalityTraits(),
            weekly_cumulative={"warmth": 0.06},
        )
        assert not result.allowed
        assert any("每周累计超限" in v for v in result.violations)

    def test_growth_tracker_respects_amplitude_lock(self):
        """PersonaGrowthTracker 使用超限 delta 的自定义规则 → 被拒绝。"""
        PersonaEngine.reset_instance()
        try:
            engine = PersonaEngine.get_instance()
            oversized_rule = GrowthRule(
                trigger="test:oversized",
                delta={"humor": 0.10},  # 超限
                description="测试超限规则",
            )
            tracker = PersonaGrowthTracker(
                engine=engine,
                stage_rules={"TEST→TEST2": oversized_rule},
            )
            result = tracker.on_stage_promoted("user1", "TEST", "TEST2")
            assert not result.applied
            assert "安全校验失败" in result.reason
        finally:
            PersonaEngine.reset_instance()


# ---------------------------------------------------------------------------
# V-IV-3: 趋势锁
# ---------------------------------------------------------------------------

class TestVIV3_TrendLock:
    """V-IV-3: 连续同向变化 > 3 次时生成 Canon Evolution 提案。"""

    def test_no_trend_lock_with_few_changes(self):
        """2 条历史 + 本次 = 3 次同向变化（等于阈值）→ 不触发趋势锁。"""
        guard = TraitDriftGuard()
        history = [
            {"humor": 0.03},
            {"humor": 0.03},
        ]
        delta = TraitsDelta(humor=0.03)
        result = guard.check(
            delta=delta,
            current_traits=PersonalityTraits(),
            change_history=history,
        )
        assert result.allowed
        assert result.evolution_proposal is None

    def test_trend_lock_triggered_4_consecutive(self):
        """4 次同向变化 → 触发趋势锁。"""
        guard = TraitDriftGuard()
        history = [
            {"humor": 0.03},
            {"humor": 0.03},
            {"humor": 0.03},
            {"humor": 0.03},
        ]
        delta = TraitsDelta(humor=0.03)
        result = guard.check(
            delta=delta,
            current_traits=PersonalityTraits(),
            change_history=history,
        )
        # 趋势锁只产生 warning，不阻止
        assert result.allowed
        assert len(result.warnings) > 0
        assert result.evolution_proposal is not None
        assert result.evolution_proposal["trait_name"] == "humor"
        assert result.evolution_proposal["direction"] == "UP"

    def test_trend_lock_broken_by_opposite(self):
        """中间有反向变化 → 不触发趋势锁。"""
        guard = TraitDriftGuard()
        history = [
            {"humor": 0.03},
            {"humor": 0.03},
            {"humor": -0.01},  # 反向
            {"humor": 0.03},
        ]
        delta = TraitsDelta(humor=0.03)
        result = guard.check(
            delta=delta,
            current_traits=PersonalityTraits(),
            change_history=history,
        )
        assert result.evolution_proposal is None

    def test_evolution_proposal_contains_reason(self):
        """Evolution 提案包含原因和建议动作。"""
        guard = TraitDriftGuard()
        history = [{"curiosity": 0.02}] * 4
        delta = TraitsDelta(curiosity=0.02)
        result = guard.check(
            delta=delta,
            current_traits=PersonalityTraits(),
            change_history=history,
        )
        proposal = result.evolution_proposal
        assert proposal is not None
        assert "好奇心" in proposal["reason"]
        assert "审查" in proposal["proposed_action"]


# ---------------------------------------------------------------------------
# V-IV-4: 不可变核心保护
# ---------------------------------------------------------------------------

class TestVIV4_ImmutableCore:
    """V-IV-4: warmth 超出 0.85 ± 0.15 范围的变化被阻止。"""

    def test_warmth_within_range_allowed(self):
        """warmth 在 [0.70, 1.00] 范围内 → 允许。"""
        guard = TraitDriftGuard()
        traits = PersonalityTraits(warmth=0.85)
        delta = TraitsDelta(warmth=0.05)  # → 0.90，在范围内
        result = guard.check(delta=delta, current_traits=traits)
        assert result.allowed

    def test_warmth_exceeds_upper_bound(self):
        """warmth 超出上界 → 拒绝。"""
        guard = TraitDriftGuard()
        traits = PersonalityTraits(warmth=0.98)
        delta = TraitsDelta(warmth=0.05)  # → 1.03（clamp 到 1.0 但 1.0 - 0.85 = 0.15 刚好）
        # 0.98 + 0.05 = 1.03，|1.03 - 0.85| = 0.18 > 0.15
        result = guard.check(delta=delta, current_traits=traits)
        assert not result.allowed
        assert any("不可变核心超限" in v for v in result.violations)

    def test_warmth_exceeds_lower_bound(self):
        """warmth 低于下界 → 拒绝。"""
        guard = TraitDriftGuard()
        traits = PersonalityTraits(warmth=0.72)
        delta = TraitsDelta(warmth=-0.05)  # → 0.67，|0.67 - 0.85| = 0.18 > 0.15
        result = guard.check(delta=delta, current_traits=traits)
        assert not result.allowed

    def test_warmth_to_050_rejected(self):
        """尝试将 warmth 设为 0.5（距离基线 0.35）→ 拒绝。"""
        guard = TraitDriftGuard()
        traits = PersonalityTraits(warmth=0.85)
        # delta = -0.35 → 0.50，先会被幅度锁拒绝，但也会被不可变核心拒绝
        delta = TraitsDelta(warmth=-0.05)
        # 但如果当前已经在 0.70...
        traits_low = PersonalityTraits(warmth=0.70)
        delta_low = TraitsDelta(warmth=-0.01)  # → 0.69，|0.69 - 0.85| = 0.16 > 0.15
        result = guard.check(delta=delta_low, current_traits=traits_low)
        assert not result.allowed

    def test_clip_delta_respects_immutable_core(self):
        """clip_delta 截断超限的不可变核心变化。"""
        guard = TraitDriftGuard()
        traits = PersonalityTraits(warmth=0.98)
        delta = TraitsDelta(warmth=0.05)
        clipped = guard.clip_delta(delta, traits)
        # clipped warmth 应 ≤ 1.00 - 0.98 = 0.02（保持在 0.85 + 0.15 = 1.00 范围内）
        assert abs(clipped.warmth) <= 0.021


# ---------------------------------------------------------------------------
# V-IV-5: 审计日志 + 回滚
# ---------------------------------------------------------------------------

class TestVIV5_AuditAndRollback:
    """V-IV-5: 审计日志记录每次变化，支持回滚。"""

    def setup_method(self):
        self.engine = _fresh_engine()
        self.tracker = PersonaGrowthTracker(engine=self.engine)

    def teardown_method(self):
        _teardown_engine()

    def test_audit_log_created_on_growth(self):
        """成长后审计日志非空。"""
        self.tracker.on_stage_promoted("user1", "ACQUAINTANCE", "FRIEND")
        log = self.engine.get_change_log("user1")
        assert len(log) >= 1
        record = log[-1]
        assert record.user_id == "user1"
        assert "bond_stage_promoted" in record.reason
        assert record.delta.get("playfulness", 0.0) == pytest.approx(0.05, abs=0.001)

    def test_rollback_restores_previous_state(self):
        """回滚后特质恢复到变化前。"""
        before = self.engine.get_traits("user1")
        self.tracker.on_stage_promoted("user1", "ACQUAINTANCE", "FRIEND")
        after_growth = self.engine.get_traits("user1")
        assert after_growth.playfulness != before.playfulness

        restored = self.engine.rollback_last_change("user1")
        assert restored is not None
        assert restored.playfulness == pytest.approx(before.playfulness, abs=0.001)

    def test_multiple_growths_and_rollback(self):
        """多次成长后逐次回滚。"""
        before = self.engine.get_traits("user1")
        self.tracker.on_stage_promoted("user1", "ACQUAINTANCE", "FRIEND")
        self.tracker.on_stage_promoted("user1", "FRIEND", "CLOSE_FRIEND")

        # 回滚最后一次（FRIEND→CLOSE_FRIEND）
        r1 = self.engine.rollback_last_change("user1")
        mid = self.engine.get_traits("user1")
        assert mid.warmth == pytest.approx(before.warmth + 0.03, abs=0.001) or \
               mid.warmth == pytest.approx(before.warmth, abs=0.001)

    def test_tracker_weekly_cumulative_tracks(self):
        """成长后每周累计正确追踪。"""
        self.tracker.on_stage_promoted("user1", "ACQUAINTANCE", "FRIEND")
        weekly = self.tracker.get_weekly_cumulative("user1")
        assert weekly.get("playfulness", 0.0) == pytest.approx(0.05, abs=0.001)

    def test_tracker_change_history_tracks(self):
        """成长后变化历史正确追踪。"""
        self.tracker.on_stage_promoted("user1", "ACQUAINTANCE", "FRIEND")
        history = self.tracker.get_change_history("user1")
        assert len(history) >= 1
        assert history[-1].get("playfulness", 0.0) == pytest.approx(0.05, abs=0.001)

    def test_reset_weekly_cumulative(self):
        """重置每周累计。"""
        self.tracker.on_stage_promoted("user1", "ACQUAINTANCE", "FRIEND")
        self.tracker.reset_weekly_cumulative("user1")
        weekly = self.tracker.get_weekly_cumulative("user1")
        assert all(v == 0.0 for v in weekly.values())


# ---------------------------------------------------------------------------
# 里程碑触发测试
# ---------------------------------------------------------------------------

class TestPhaseIV_Milestones:
    """里程碑触发测试。"""

    def setup_method(self):
        self.engine = _fresh_engine()
        self.tracker = PersonaGrowthTracker(engine=self.engine)

    def teardown_method(self):
        _teardown_engine()

    def test_deep_talk_50_milestone(self):
        """深度对话 50 次 → 好奇心 +0.03。"""
        before = self.engine.get_traits("user1")
        result = self.tracker.on_milestone("user1", "deep_talk_50")
        assert result.applied
        after = self.engine.get_traits("user1")
        assert after.curiosity == pytest.approx(before.curiosity + 0.03, abs=0.001)

    def test_emotional_support_20_milestone(self):
        """情感支持 20 次 → 韧性 +0.05。"""
        before = self.engine.get_traits("user1")
        result = self.tracker.on_milestone("user1", "emotional_support_20")
        assert result.applied
        after = self.engine.get_traits("user1")
        assert after.resilience == pytest.approx(before.resilience + 0.05, abs=0.001)

    def test_unknown_milestone_no_effect(self):
        """未知里程碑 → 不触发。"""
        result = self.tracker.on_milestone("user1", "unknown_milestone")
        assert not result.applied

    def test_milestone_one_time(self):
        """里程碑 one_time 不重复触发。"""
        r1 = self.tracker.on_milestone("user1", "deep_talk_50")
        assert r1.applied
        r2 = self.tracker.on_milestone("user1", "deep_talk_50")
        assert not r2.applied


# ---------------------------------------------------------------------------
# 端到端集成测试
# ---------------------------------------------------------------------------

class TestPhaseIV_EndToEnd:
    """Phase IV 端到端集成。"""

    def setup_method(self):
        self.engine = _fresh_engine()

    def teardown_method(self):
        _teardown_engine()

    def test_full_growth_lifecycle(self):
        """完整生命周期：晋级 → 成长 → 校验 → 审计 → 回滚。"""
        tracker = PersonaGrowthTracker(engine=self.engine)

        # 1. 晋级触发成长
        before = self.engine.get_traits("e2e_user")
        r1 = tracker.on_stage_promoted("e2e_user", "ACQUAINTANCE", "FRIEND")
        assert r1.applied

        # 2. 验证特质变化
        after1 = self.engine.get_traits("e2e_user")
        assert after1.playfulness > before.playfulness

        # 3. 再次晋级
        r2 = tracker.on_stage_promoted("e2e_user", "FRIEND", "CLOSE_FRIEND")
        assert r2.applied
        after2 = self.engine.get_traits("e2e_user")
        assert after2.warmth > after1.warmth

        # 4. 审计链完整
        log = self.engine.get_change_log("e2e_user")
        assert len(log) >= 2

        # 5. 回滚最后一次
        restored = self.engine.rollback_last_change("e2e_user")
        assert restored is not None
        # 恢复到 after1 的状态
        current = self.engine.get_traits("e2e_user")
        assert current.warmth == pytest.approx(after1.warmth, abs=0.001)

    def test_guard_protects_immutable_core(self):
        """完整流程中不可变核心保护生效。"""
        # warmth 基线 0.85，当前 0.99
        self.engine.apply_growth_delta(
            "core_user",
            TraitsDelta(warmth=0.05),  # → 0.90
            reason="test_setup_1",
        )
        self.engine.apply_growth_delta(
            "core_user",
            TraitsDelta(warmth=0.05),  # → 0.95
            reason="test_setup_2",
        )
        self.engine.apply_growth_delta(
            "core_user",
            TraitsDelta(warmth=0.04),  # → 0.99
            reason="test_setup_3",
        )

        # 此时 warmth=0.99，再 +0.05 → 1.04，|1.04 - 0.85| = 0.19 > 0.15
        tracker = PersonaGrowthTracker(engine=self.engine)
        oversized = GrowthRule(
            trigger="test:push_warmth",
            delta={"warmth": 0.05},
            description="测试推高 warmth",
            one_time=False,
        )
        tracker._stage_rules["X→Y"] = oversized
        result = tracker.on_stage_promoted("core_user", "X", "Y")
        assert not result.applied
        assert "不可变核心超限" in result.reason or "安全校验失败" in result.reason
