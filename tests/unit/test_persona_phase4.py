"""人格成长旧能力的安全回归与 NE-01 中央写入围栏验收。

验收标准：
  V-IV-1: Bond/里程碑事件只生成候选，不可直接写入人格资产
  V-IV-2: 单次变化幅度 ≤ 0.05，每周累计 ≤ 0.1
  V-IV-3: 连续同向变化 > 3 次时生成 Canon Evolution 提案
  V-IV-4: warmth 超出 0.85 ± 0.15 范围的变化被阻止
  V-IV-5: 仅显式迁移 authority 可写入审计日志并支持回滚
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
    PersonaGrowthMigrationAuthority,
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
    PERSONA_GROWTH_PROPOSAL_REQUIRED,
    PersonaGrowthTracker,
    GrowthApplyResult,
    GrowthRule,
    STAGE_PROMOTION_RULES,
    MILESTONE_RULES,
)


def _migration_authority(name: str) -> PersonaGrowthMigrationAuthority:
    return PersonaGrowthMigrationAuthority(
        migration_id=f"persona-migration:phase4-{name}",
        actor_id="test:persona-phase4",
        evidence_ref="test:test_persona_phase4",
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
    """V-IV-1: Bond 阶段晋级只能形成待审查候选。"""

    def setup_method(self):
        self.engine = _fresh_engine()
        self.tracker = PersonaGrowthTracker(engine=self.engine)

    def teardown_method(self):
        _teardown_engine()

    def test_friend_to_close_friend(self):
        """FRIEND→CLOSE_FRIEND 返回候选 delta，特质保持不变。"""
        before = self.engine.get_traits("user1")
        result = self.tracker.on_stage_promoted(
            "user1", "FRIEND", "CLOSE_FRIEND",
        )
        assert not result.applied
        assert PERSONA_GROWTH_PROPOSAL_REQUIRED in result.reason
        assert result.guard_result["candidate_delta"]["playfulness"] == pytest.approx(0.05)
        after = self.engine.get_traits("user1")
        assert after.as_dict() == before.as_dict()

    def test_close_friend_to_confidant(self):
        """CLOSE_FRIEND→CONFIDANT 只返回候选。"""
        before = self.engine.get_traits("user1")
        result = self.tracker.on_stage_promoted(
            "user1", "CLOSE_FRIEND", "CONFIDANT",
        )
        assert not result.applied
        assert result.guard_result["candidate_delta"]["humor"] == pytest.approx(0.05)
        after = self.engine.get_traits("user1")
        assert after.as_dict() == before.as_dict()

    def test_confidant_to_soulmate(self):
        """CONFIDANT→SOULMATE 只返回候选。"""
        before = self.engine.get_traits("user1")
        result = self.tracker.on_stage_promoted(
            "user1", "CONFIDANT", "SOULMATE",
        )
        assert not result.applied
        assert result.guard_result["candidate_delta"]["resilience"] == pytest.approx(0.05)
        after = self.engine.get_traits("user1")
        assert after.as_dict() == before.as_dict()

    def test_unknown_transition_no_effect(self):
        """未定义的阶段转换 → 不触发。"""
        result = self.tracker.on_stage_promoted(
            "user1", "FRIEND", "SOULMATE",
        )
        assert not result.applied
        assert "无匹配" in result.reason

    def test_one_time_rule_not_repeated(self):
        """未获批准的 one_time 候选不能被标记为已消费。"""
        r1 = self.tracker.on_stage_promoted("user1", "FRIEND", "CLOSE_FRIEND")
        assert not r1.applied
        r2 = self.tracker.on_stage_promoted("user1", "FRIEND", "CLOSE_FRIEND")
        assert not r2.applied
        assert PERSONA_GROWTH_PROPOSAL_REQUIRED in r2.reason
        assert self.tracker.get_triggered_rules("user1") == set()

    def test_change_log_recorded(self):
        """候选不得伪造 PersonaEngine 变更审计。"""
        self.tracker.on_stage_promoted("user1", "CLOSE_FRIEND", "CONFIDANT")
        log = self.engine.get_change_log("user1")
        assert log == []


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
    """V-IV-5: 迁移写入可审计、可回滚，运行时候选不可写。"""

    def setup_method(self):
        self.engine = _fresh_engine()
        self.tracker = PersonaGrowthTracker(engine=self.engine)

    def teardown_method(self):
        _teardown_engine()

    def test_audit_log_created_on_growth(self):
        """运行时成长候选不创建虚假变更日志。"""
        self.tracker.on_stage_promoted("user1", "FRIEND", "CLOSE_FRIEND")
        log = self.engine.get_change_log("user1")
        assert log == []

    def test_rollback_restores_previous_state(self):
        """显式迁移写入后可回滚到先前状态。"""
        before = self.engine.get_traits("user1")
        self.engine.apply_migration_growth_delta(
            "user1",
            TraitsDelta(playfulness=0.05),
            authority=_migration_authority("rollback"),
            reason="legacy state backfill",
        )
        after_growth = self.engine.get_traits("user1")
        assert after_growth.playfulness != before.playfulness

        restored = self.engine.rollback_last_change("user1")
        assert restored is not None
        assert restored.playfulness == pytest.approx(before.playfulness, abs=0.001)

    def test_multiple_growths_and_rollback(self):
        """多次迁移写入后逐次回滚。"""
        before = self.engine.get_traits("user1")
        self.engine.apply_migration_growth_delta(
            "user1",
            TraitsDelta(warmth=0.03),
            authority=_migration_authority("multi-1"),
            reason="legacy state backfill 1",
        )
        self.engine.apply_migration_growth_delta(
            "user1",
            TraitsDelta(humor=0.05),
            authority=_migration_authority("multi-2"),
            reason="legacy state backfill 2",
        )

        # 回滚最后一次（CLOSE_FRIEND→CONFIDANT）
        r1 = self.engine.rollback_last_change("user1")
        assert r1 is not None
        mid = self.engine.get_traits("user1")
        assert mid.warmth == pytest.approx(before.warmth + 0.03, abs=0.001)
        assert mid.humor == pytest.approx(before.humor, abs=0.001)

    def test_tracker_weekly_cumulative_tracks(self):
        """未批准候选不污染每周累计。"""
        self.tracker.on_stage_promoted("user1", "FRIEND", "CLOSE_FRIEND")
        weekly = self.tracker.get_weekly_cumulative("user1")
        assert weekly == {}

    def test_tracker_change_history_tracks(self):
        """未批准候选不污染变化历史。"""
        self.tracker.on_stage_promoted("user1", "FRIEND", "CLOSE_FRIEND")
        history = self.tracker.get_change_history("user1")
        assert history == []

    def test_reset_weekly_cumulative(self):
        """重置每周累计。"""
        self.tracker.on_stage_promoted("user1", "FRIEND", "CLOSE_FRIEND")
        self.tracker.reset_weekly_cumulative("user1")
        weekly = self.tracker.get_weekly_cumulative("user1")
        assert all(v == 0.0 for v in weekly.values())


# ---------------------------------------------------------------------------
# 里程碑触发测试
# ---------------------------------------------------------------------------

class TestPhaseIV_Milestones:
    """里程碑候选生成测试。"""

    def setup_method(self):
        self.engine = _fresh_engine()
        self.tracker = PersonaGrowthTracker(engine=self.engine)

    def teardown_method(self):
        _teardown_engine()

    def test_deep_talk_50_milestone(self):
        """深度对话 50 次形成候选，不直接改变好奇心。"""
        before = self.engine.get_traits("user1")
        result = self.tracker.on_milestone("user1", "deep_talk_50")
        assert not result.applied
        assert result.guard_result["candidate_delta"]["curiosity"] == pytest.approx(0.03)
        after = self.engine.get_traits("user1")
        assert after.as_dict() == before.as_dict()

    def test_emotional_support_20_milestone(self):
        """情感支持 20 次形成候选，不直接改变韧性。"""
        before = self.engine.get_traits("user1")
        result = self.tracker.on_milestone("user1", "emotional_support_20")
        assert not result.applied
        assert result.guard_result["candidate_delta"]["resilience"] == pytest.approx(0.05)
        after = self.engine.get_traits("user1")
        assert after.as_dict() == before.as_dict()

    def test_unknown_milestone_no_effect(self):
        """未知里程碑 → 不触发。"""
        result = self.tracker.on_milestone("user1", "unknown_milestone")
        assert not result.applied

    def test_milestone_one_time(self):
        """未批准的里程碑候选不消耗 one_time 标记。"""
        r1 = self.tracker.on_milestone("user1", "deep_talk_50")
        assert not r1.applied
        r2 = self.tracker.on_milestone("user1", "deep_talk_50")
        assert not r2.applied
        assert PERSONA_GROWTH_PROPOSAL_REQUIRED in r2.reason


# ---------------------------------------------------------------------------
# 端到端集成测试
# ---------------------------------------------------------------------------

class TestPhaseIV_EndToEnd:
    """NE-01 写入围栏端到端集成。"""

    def setup_method(self):
        self.engine = _fresh_engine()

    def teardown_method(self):
        _teardown_engine()

    def test_full_growth_lifecycle(self):
        """晋级事件止于候选，NE-06 接入前不得出现资产副作用。"""
        tracker = PersonaGrowthTracker(engine=self.engine)

        # 1. 晋级生成候选
        before = self.engine.get_traits("e2e_user")
        r1 = tracker.on_stage_promoted("e2e_user", "FRIEND", "CLOSE_FRIEND")
        assert not r1.applied

        # 2. 验证特质不变
        after1 = self.engine.get_traits("e2e_user")
        assert after1.as_dict() == before.as_dict()

        # 3. 再次晋级
        r2 = tracker.on_stage_promoted("e2e_user", "CLOSE_FRIEND", "CONFIDANT")
        assert not r2.applied
        after2 = self.engine.get_traits("e2e_user")
        assert after2.as_dict() == after1.as_dict()

        # 4. 未发生写入，不生成变更审计
        log = self.engine.get_change_log("e2e_user")
        assert log == []
        assert self.engine.rollback_last_change("e2e_user") is None

    def test_guard_protects_immutable_core(self):
        """完整流程中不可变核心保护生效。"""
        # warmth 基线 0.85，当前 0.99
        self.engine.apply_migration_growth_delta(
            "core_user",
            TraitsDelta(warmth=0.05),  # → 0.90
            authority=_migration_authority("core-1"),
            reason="test_setup_1",
        )
        self.engine.apply_migration_growth_delta(
            "core_user",
            TraitsDelta(warmth=0.05),  # → 0.95
            authority=_migration_authority("core-2"),
            reason="test_setup_2",
        )
        self.engine.apply_migration_growth_delta(
            "core_user",
            TraitsDelta(warmth=0.04),  # → 0.99
            authority=_migration_authority("core-3"),
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
