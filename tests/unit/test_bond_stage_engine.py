"""T-BOND-STAGE-01~12: Bond 阶段积分 + 自动晋级引擎单元测试（M13-A）。"""

from __future__ import annotations

import pytest

from host_brain.bond.bond_stage_engine import (
    DEFAULT_SCORE_MAP,
    DEFAULT_STAGE_THRESHOLDS,
    STAGE_ACQUAINTANCE,
    STAGE_CLOSE_FRIEND,
    STAGE_CONFIDANT,
    STAGE_FRIEND,
    STAGE_ORDER,
    STAGE_SOULMATE,
    STAGE_STRANGER,
    TRANSITION_STATUS_DEMOTED,
    TRANSITION_STATUS_PROMOTED,
    TRANSITION_STATUS_STABLE,
    UPDATE_KIND_INIT,
    UPDATE_KIND_STAGE_TRANSITION,
    BondScoreState,
    BondStageEngine,
    StageTransitionResult,
)


# ── 夹具 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def engine() -> BondStageEngine:
    return BondStageEngine()


@pytest.fixture
def engine_with_ledger():
    from unittest.mock import MagicMock
    ledger = MagicMock()
    ledger.log_bond_stage_transition = MagicMock()
    return BondStageEngine(ledger=ledger), ledger


@pytest.fixture
def engine_with_update_ledger():
    class BondUpdateOnlyLedger:
        def __init__(self):
            self.rows = []

        def log_bond_update(self, row):
            self.rows.append(row)

    ledger = BondUpdateOnlyLedger()
    return BondStageEngine(ledger=ledger), ledger


# ── T-BOND-STAGE-01: 初始状态为 STRANGER ─────────────────────────────────────

class TestInitialState:
    """T-BOND-STAGE-01: 新用户初始状态为 STRANGER，积分为 0。"""

    def test_initial_stage_is_stranger(self, engine):
        state = engine.get_state("user_001")
        assert state.current_stage == STAGE_STRANGER

    def test_initial_score_is_zero(self, engine):
        state = engine.get_state("user_001")
        assert state.total_score == 0

    def test_initial_event_count_is_zero(self, engine):
        state = engine.get_state("user_001")
        assert state.event_count == 0

    def test_initial_trend_is_stable(self, engine):
        state = engine.get_state("user_001")
        assert state.trend() == "STABLE"

    def test_initial_state_persisted_to_bond_update_log(self, engine_with_update_ledger):
        engine, ledger = engine_with_update_ledger
        engine.get_state("user_init")
        assert len(ledger.rows) == 1
        row = ledger.rows[0]
        assert row["user_id"] == "user_init"
        assert row["update_kind"] == UPDATE_KIND_INIT
        assert row["payload"]["total_score"] == 0
        assert row["payload"]["current_stage"] == STAGE_STRANGER

    def test_initial_state_persisted_only_once_per_user(self, engine_with_update_ledger):
        engine, ledger = engine_with_update_ledger
        engine.get_state("user_once")
        engine.get_state("user_once")
        assert len(ledger.rows) == 1


# ── T-BOND-STAGE-02: 积分累积与阶段晋级 ──────────────────────────────────────

class TestScoreAccumulation:
    """T-BOND-STAGE-02: 积分累积到阈值时触发阶段晋级。"""

    def test_interaction_adds_score(self, engine):
        result = engine.apply_event(
            user_id="user_002",
            update_kind="INTERACTION",
            evidence_refs=["evt::001"],
        )
        assert result.score_delta == DEFAULT_SCORE_MAP["INTERACTION"]
        assert result.total_score == DEFAULT_SCORE_MAP["INTERACTION"]

    def test_deep_talk_adds_more_score(self, engine):
        result = engine.apply_event(
            user_id="user_003",
            update_kind="DEEP_TALK",
            evidence_refs=["evt::002"],
        )
        assert result.score_delta == DEFAULT_SCORE_MAP["DEEP_TALK"]

    def test_score_accumulates_across_events(self, engine):
        for i in range(5):
            engine.apply_event(
                user_id="user_004",
                update_kind="INTERACTION",
                evidence_refs=[f"evt::{i}"],
            )
        state = engine.get_state("user_004")
        assert state.total_score == 5 * DEFAULT_SCORE_MAP["INTERACTION"]
        assert state.event_count == 5

    def test_promotion_to_acquaintance(self, engine):
        """积分达到 10 时晋级到 ACQUAINTANCE。"""
        threshold = DEFAULT_STAGE_THRESHOLDS[STAGE_ACQUAINTANCE]
        # 用 DEEP_TALK(5) × 2 = 10 分
        for i in range(threshold // DEFAULT_SCORE_MAP["DEEP_TALK"]):
            result = engine.apply_event(
                user_id="user_005",
                update_kind="DEEP_TALK",
                evidence_refs=[f"evt::{i}"],
            )
        state = engine.get_state("user_005")
        assert state.current_stage == STAGE_ACQUAINTANCE

    def test_promotion_result_status(self, engine):
        """晋级时 transition_status 为 PROMOTED。"""
        threshold = DEFAULT_STAGE_THRESHOLDS[STAGE_ACQUAINTANCE]
        last_result = None
        for i in range(threshold // DEFAULT_SCORE_MAP["DEEP_TALK"]):
            last_result = engine.apply_event(
                user_id="user_006",
                update_kind="DEEP_TALK",
                evidence_refs=[f"evt::{i}"],
            )
        # 最后一次触发晋级
        assert last_result is not None
        assert last_result.to_stage == STAGE_ACQUAINTANCE
        assert last_result.transition_status == TRANSITION_STATUS_PROMOTED


# ── T-BOND-STAGE-03: 无晋级时 STABLE ─────────────────────────────────────────

class TestStableTransition:
    """T-BOND-STAGE-03: 未达到晋级阈值时 transition_status 为 STABLE。"""

    def test_single_interaction_is_stable(self, engine):
        result = engine.apply_event(
            user_id="user_007",
            update_kind="INTERACTION",
            evidence_refs=["evt::001"],
        )
        assert result.transition_status == TRANSITION_STATUS_STABLE
        assert result.from_stage == result.to_stage == STAGE_STRANGER

    def test_stable_result_fields(self, engine):
        result = engine.apply_event(
            user_id="user_008",
            update_kind="INTERACTION",
            evidence_refs=["evt::001"],
        )
        assert result.user_id == "user_008"
        assert result.transition_id.startswith("bond_trans_")
        assert result.ts_ms > 0


# ── T-BOND-STAGE-04: 积分衰减与降级 ──────────────────────────────────────────

class TestScoreDecay:
    """T-BOND-STAGE-04: IDLE_DECAY 事件减少积分，积分不低于 0。"""

    def test_idle_decay_reduces_score(self, engine):
        # 先积累积分
        engine.apply_event(
            user_id="user_009",
            update_kind="DEEP_TALK",
            evidence_refs=["evt::001"],
        )
        result = engine.apply_event(
            user_id="user_009",
            update_kind="IDLE_DECAY",
            evidence_refs=["evt::002"],
        )
        assert result.score_delta == DEFAULT_SCORE_MAP["IDLE_DECAY"]

    def test_score_never_below_zero(self, engine):
        """积分不会低于 0。"""
        result = engine.apply_event(
            user_id="user_010",
            update_kind="IDLE_DECAY",
            evidence_refs=["evt::001"],
        )
        state = engine.get_state("user_010")
        assert state.total_score == 0

    def test_demotion_when_score_drops(self, engine):
        """积分下降到阶段阈值以下时触发降级。"""
        # 先晋级到 ACQUAINTANCE（需要 10 分）
        for i in range(2):
            engine.apply_event(
                user_id="user_011",
                update_kind="DEEP_TALK",
                evidence_refs=[f"evt::{i}"],
            )
        state = engine.get_state("user_011")
        assert state.current_stage == STAGE_ACQUAINTANCE

        # 自定义低阈值引擎测试降级
        custom_engine = BondStageEngine(
            stage_thresholds={
                STAGE_STRANGER: 0,
                STAGE_ACQUAINTANCE: 5,
                STAGE_FRIEND: 20,
                STAGE_CLOSE_FRIEND: 50,
                STAGE_CONFIDANT: 100,
                STAGE_SOULMATE: 200,
            },
            score_map={
                "INTERACTION": 6,
                "IDLE_DECAY": -10,
            },
        )
        # 先晋级
        custom_engine.apply_event(
            user_id="user_011b",
            update_kind="INTERACTION",
            evidence_refs=["evt::001"],
        )
        state = custom_engine.get_state("user_011b")
        assert state.current_stage == STAGE_ACQUAINTANCE

        # 衰减到 0，降级回 STRANGER
        result = custom_engine.apply_event(
            user_id="user_011b",
            update_kind="IDLE_DECAY",
            evidence_refs=["evt::002"],
        )
        assert result.transition_status == TRANSITION_STATUS_DEMOTED
        assert result.to_stage == STAGE_STRANGER


# ── T-BOND-STAGE-05: 趋势判断 ────────────────────────────────────────────────

class TestTrend:
    """T-BOND-STAGE-05: 趋势根据最近事件积分判断 RISING/STABLE/DECLINING。"""

    def test_rising_trend_after_positive_events(self, engine):
        for i in range(5):
            engine.apply_event(
                user_id="user_012",
                update_kind="DEEP_TALK",
                evidence_refs=[f"evt::{i}"],
            )
        state = engine.get_state("user_012")
        assert state.trend() == "RISING"

    def test_declining_trend_after_decay(self):
        custom_engine = BondStageEngine(
            score_map={"IDLE_DECAY": -5, "INTERACTION": 1},
        )
        # 先积累一些积分
        for i in range(3):
            custom_engine.apply_event(
                user_id="user_013",
                update_kind="INTERACTION",
                evidence_refs=[f"evt::{i}"],
            )
        # 然后连续衰减
        for i in range(5):
            custom_engine.apply_event(
                user_id="user_013",
                update_kind="IDLE_DECAY",
                evidence_refs=[f"evt::d{i}"],
            )
        state = custom_engine.get_state("user_013")
        assert state.trend() == "DECLINING"


# ── T-BOND-STAGE-06: Bond Summary 不含数值 ───────────────────────────────────

class TestBondSummary:
    """T-BOND-STAGE-06: Bond Summary 不含数值（FR-BOND-003）。"""

    def test_summary_has_no_numeric_values(self, engine):
        engine.apply_event(
            user_id="user_014",
            update_kind="DEEP_TALK",
            evidence_refs=["evt::001"],
        )
        summary = engine.get_summary("user_014")
        for key, value in summary.items():
            assert not isinstance(value, (int, float)), f"summary[{key}]={value} is numeric"

    def test_summary_has_required_fields(self, engine):
        summary = engine.get_summary("user_015")
        assert "stage" in summary
        assert "trend" in summary
        assert "capability_hint" in summary

    def test_summary_stage_is_valid(self, engine):
        summary = engine.get_summary("user_016")
        assert summary["stage"] in STAGE_ORDER

    def test_summary_trend_is_valid(self, engine):
        summary = engine.get_summary("user_017")
        assert summary["trend"] in ("RISING", "STABLE", "DECLINING")


# ── T-BOND-STAGE-07: 审计链写入 ──────────────────────────────────────────────

class TestAuditLogging:
    """T-BOND-STAGE-07: 晋级/降级事件写入审计链。"""

    def test_transition_logged_on_promotion(self, engine_with_ledger):
        engine, ledger = engine_with_ledger
        # 触发晋级
        for i in range(2):
            engine.apply_event(
                user_id="user_018",
                update_kind="DEEP_TALK",
                evidence_refs=[f"evt::{i}"],
            )
        # 晋级时应调用 log_bond_stage_transition
        assert ledger.log_bond_stage_transition.called

    def test_stable_event_also_logged(self, engine_with_ledger):
        engine, ledger = engine_with_ledger
        engine.apply_event(
            user_id="user_019",
            update_kind="INTERACTION",
            evidence_refs=["evt::001"],
        )
        assert ledger.log_bond_stage_transition.called

    def test_log_record_has_required_fields(self, engine_with_ledger):
        engine, ledger = engine_with_ledger
        engine.apply_event(
            user_id="user_020",
            update_kind="INTERACTION",
            evidence_refs=["evt::001"],
        )
        call_args = ledger.log_bond_stage_transition.call_args[0][0]
        for field in ("user_id", "from_stage", "to_stage", "transition_status",
                      "score_delta", "total_score", "ts_ms"):
            assert field in call_args, f"missing field: {field}"

    def test_transition_falls_back_to_bond_update_log(self, engine_with_update_ledger):
        engine, ledger = engine_with_update_ledger
        engine.apply_event(
            user_id="user_021",
            update_kind="INTERACTION",
            evidence_refs=["evt::001"],
        )
        assert len(ledger.rows) == 2
        row = ledger.rows[-1]
        assert row["update_kind"] == UPDATE_KIND_STAGE_TRANSITION
        assert row["payload"]["transition_status"] == TRANSITION_STATUS_STABLE
        assert row["payload"]["total_score"] == DEFAULT_SCORE_MAP["INTERACTION"]


# ── T-BOND-STAGE-08: 多用户隔离 ──────────────────────────────────────────────

class TestMultiUserIsolation:
    """T-BOND-STAGE-08: 多用户 Bond 状态相互隔离。"""

    def test_users_have_independent_states(self, engine):
        engine.apply_event(
            user_id="alice",
            update_kind="DEEP_TALK",
            evidence_refs=["evt::001"],
        )
        engine.apply_event(
            user_id="bob",
            update_kind="INTERACTION",
            evidence_refs=["evt::002"],
        )
        alice_state = engine.get_state("alice")
        bob_state = engine.get_state("bob")
        assert alice_state.total_score != bob_state.total_score

    def test_list_users_returns_all(self, engine):
        engine.apply_event(user_id="user_a", update_kind="INTERACTION", evidence_refs=["e1"])
        engine.apply_event(user_id="user_b", update_kind="INTERACTION", evidence_refs=["e2"])
        users = engine.list_users()
        assert "user_a" in users
        assert "user_b" in users

    def test_reset_state_clears_user(self, engine):
        engine.apply_event(user_id="user_c", update_kind="DEEP_TALK", evidence_refs=["e1"])
        engine.reset_state("user_c")
        state = engine.get_state("user_c")
        assert state.total_score == 0
        assert state.current_stage == STAGE_STRANGER


# ── T-BOND-STAGE-09: 阶段顺序完整性 ─────────────────────────────────────────

class TestStageOrder:
    """T-BOND-STAGE-09: 阶段顺序完整且阈值单调递增。"""

    def test_stage_order_has_six_stages(self):
        assert len(STAGE_ORDER) == 6

    def test_stage_thresholds_monotonically_increasing(self):
        prev = -1
        for stage in STAGE_ORDER:
            threshold = DEFAULT_STAGE_THRESHOLDS[stage]
            assert threshold > prev, f"{stage} threshold {threshold} not > {prev}"
            prev = threshold

    def test_stranger_threshold_is_zero(self):
        assert DEFAULT_STAGE_THRESHOLDS[STAGE_STRANGER] == 0

    def test_soulmate_is_highest_stage(self):
        assert STAGE_ORDER[-1] == STAGE_SOULMATE
