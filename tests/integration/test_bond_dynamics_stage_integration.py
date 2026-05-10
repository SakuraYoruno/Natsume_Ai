"""T-BOND-INTEG-01~08: BondDynamicsWriter + BondStageEngine 端到端集成测试（M13-C）。"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from host_brain.bond.bond_dynamics import BondDynamicsWriter, BondUpdateResult
from host_brain.bond.bond_stage_engine import (
    BondStageEngine,
    STAGE_STRANGER,
    STAGE_ACQUAINTANCE,
    STAGE_FRIEND,
    STAGE_CLOSE_FRIEND,
    STAGE_CONFIDANT,
    STAGE_SOULMATE,
    TRANSITION_STATUS_PROMOTED,
    TRANSITION_STATUS_STABLE,
)
from host_brain.bond.neo4j_client import InMemoryNeo4jBondClient


# ── 夹具 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def neo4j_client():
    return InMemoryNeo4jBondClient()


@pytest.fixture
def stage_engine():
    return BondStageEngine()


@pytest.fixture
def writer_with_engine(neo4j_client, stage_engine):
    return BondDynamicsWriter(
        neo4j_client=neo4j_client,
        stage_engine=stage_engine,
    )


@pytest.fixture
def writer_without_engine(neo4j_client):
    """向后兼容：不注入 stage_engine 的 writer。"""
    return BondDynamicsWriter(neo4j_client=neo4j_client)


def _apply(writer, user_id="user_001", update_kind="INTERACTION", n=1):
    results = []
    for i in range(n):
        r = writer.apply_update(
            request_id=f"req_{i:04d}",
            intent_id=f"intent_{i:04d}",
            session_id="sess_001",
            seq=i,
            user_id=user_id,
            topic_id="topic_daily",
            capability_hint="test",
            evidence_refs=[f"evt::test_{i}"],
            update_kind=update_kind,
        )
        results.append(r)
    return results


# ── T-BOND-INTEG-01: 基本联动 ─────────────────────────────────────────────────

class TestBasicIntegration:
    """T-BOND-INTEG-01: apply_update 成功后自动触发 BondStageEngine.apply_event。"""

    def test_apply_update_triggers_stage_engine(self, writer_with_engine, stage_engine):
        result = writer_with_engine.apply_update(
            request_id="req_0001",
            intent_id="intent_0001",
            session_id="sess_001",
            seq=0,
            user_id="user_001",
            topic_id="topic_daily",
            capability_hint="test",
            evidence_refs=["evt::test_001"],
            update_kind="INTERACTION",
        )
        assert result.success
        # BondStageEngine 应已记录该用户
        assert "user_001" in stage_engine.list_users()

    def test_stage_transition_in_event(self, writer_with_engine, stage_engine):
        result = writer_with_engine.apply_update(
            request_id="req_0001",
            intent_id="intent_0001",
            session_id="sess_001",
            seq=0,
            user_id="user_001",
            topic_id="topic_daily",
            capability_hint="test",
            evidence_refs=["evt::test_001"],
            update_kind="INTERACTION",
        )
        assert result.success
        # event 中应包含 stage_transition 字段
        assert "stage_transition" in result.event
        st = result.event["stage_transition"]
        assert st is not None
        assert "from_stage" in st
        assert "to_stage" in st
        assert "transition_status" in st
        assert "score_delta" in st
        assert "total_score" in st

    def test_stage_transition_score_delta_correct(self, writer_with_engine, stage_engine):
        result = writer_with_engine.apply_update(
            request_id="req_0001",
            intent_id="intent_0001",
            session_id="sess_001",
            seq=0,
            user_id="user_001",
            topic_id="topic_daily",
            capability_hint="test",
            evidence_refs=["evt::test_001"],
            update_kind="DEEP_TALK",  # 积分 = 5
        )
        st = result.event["stage_transition"]
        assert st["score_delta"] == 5


# ── T-BOND-INTEG-02: 向后兼容（无 stage_engine）────────────────────────────────

class TestBackwardCompatibility:
    """T-BOND-INTEG-02: 不注入 stage_engine 时，apply_update 正常工作，event 中 stage_transition=None。"""

    def test_no_stage_engine_still_works(self, writer_without_engine):
        result = writer_without_engine.apply_update(
            request_id="req_0001",
            intent_id="intent_0001",
            session_id="sess_001",
            seq=0,
            user_id="user_001",
            topic_id="topic_daily",
            capability_hint="test",
            evidence_refs=["evt::test_001"],
            update_kind="INTERACTION",
        )
        assert result.success

    def test_no_stage_engine_event_has_null_transition(self, writer_without_engine):
        result = writer_without_engine.apply_update(
            request_id="req_0001",
            intent_id="intent_0001",
            session_id="sess_001",
            seq=0,
            user_id="user_001",
            topic_id="topic_daily",
            capability_hint="test",
            evidence_refs=["evt::test_001"],
            update_kind="INTERACTION",
        )
        assert result.event.get("stage_transition") is None


# ── T-BOND-INTEG-03: 自动晋级端到端 ──────────────────────────────────────────

class TestAutoPromotion:
    """T-BOND-INTEG-03: 积分累积到阈值时自动晋级（STRANGER→ACQUAINTANCE 需 10 分）。"""

    def test_promotion_triggered_after_threshold(self, writer_with_engine, stage_engine):
        # INTERACTION=1 分，需要 10 次才能晋级到 ACQUAINTANCE
        results = _apply(writer_with_engine, user_id="user_promo", update_kind="INTERACTION", n=10)
        state = stage_engine.get_state("user_promo")
        assert state.current_stage == STAGE_ACQUAINTANCE
        assert state.total_score == 10

    def test_promotion_event_has_promoted_status(self, writer_with_engine, stage_engine):
        # 前 9 次 STABLE，第 10 次 PROMOTED
        results = _apply(writer_with_engine, user_id="user_promo2", update_kind="INTERACTION", n=10)
        last_st = results[-1].event["stage_transition"]
        assert last_st["transition_status"] == TRANSITION_STATUS_PROMOTED
        assert last_st["from_stage"] == STAGE_STRANGER
        assert last_st["to_stage"] == STAGE_ACQUAINTANCE

    def test_deep_talk_accelerates_promotion(self, writer_with_engine, stage_engine):
        # DEEP_TALK=5 分，2 次 = 10 分，直接晋级
        results = _apply(writer_with_engine, user_id="user_deep", update_kind="DEEP_TALK", n=2)
        state = stage_engine.get_state("user_deep")
        assert state.current_stage == STAGE_ACQUAINTANCE

    def test_milestone_triggers_fast_promotion(self, writer_with_engine, stage_engine):
        # MILESTONE=10 分，1 次直接晋级到 ACQUAINTANCE
        results = _apply(writer_with_engine, user_id="user_mile", update_kind="MILESTONE", n=1)
        state = stage_engine.get_state("user_mile")
        assert state.current_stage == STAGE_ACQUAINTANCE


# ── T-BOND-INTEG-04: 拒绝事件不触发 stage_engine ─────────────────────────────

class TestRejectedEventNoStageUpdate:
    """T-BOND-INTEG-04: evidence_refs 为空时 apply_update 被拒绝，不触发 BondStageEngine。"""

    def test_rejected_event_no_stage_update(self, writer_with_engine, stage_engine):
        result = writer_with_engine.apply_update(
            request_id="req_0001",
            intent_id="intent_0001",
            session_id="sess_001",
            seq=0,
            user_id="user_reject",
            topic_id="topic_daily",
            capability_hint="test",
            evidence_refs=[],  # 空 → 拒绝
            update_kind="INTERACTION",
        )
        assert not result.success
        # stage_engine 不应记录该用户
        assert "user_reject" not in stage_engine.list_users()

    def test_rejected_event_has_no_stage_transition(self, writer_with_engine):
        result = writer_with_engine.apply_update(
            request_id="req_0001",
            intent_id="intent_0001",
            session_id="sess_001",
            seq=0,
            user_id="user_reject2",
            topic_id="topic_daily",
            capability_hint="test",
            evidence_refs=[],
            update_kind="INTERACTION",
        )
        assert not result.success
        # 拒绝事件的 event 中不含 stage_transition
        assert "stage_transition" not in result.event or result.event.get("stage_transition") is None


# ── T-BOND-INTEG-05: stage_engine 异常不影响主流程 ───────────────────────────

class TestStageEngineExceptionIsolation:
    """T-BOND-INTEG-05: BondStageEngine.apply_event 抛出异常时，apply_update 仍返回成功。"""

    def test_stage_engine_exception_non_fatal(self, neo4j_client):
        broken_engine = MagicMock(spec=BondStageEngine)
        broken_engine.apply_event.side_effect = RuntimeError("模拟 stage engine 崩溃")
        writer = BondDynamicsWriter(
            neo4j_client=neo4j_client,
            stage_engine=broken_engine,
        )
        result = writer.apply_update(
            request_id="req_0001",
            intent_id="intent_0001",
            session_id="sess_001",
            seq=0,
            user_id="user_001",
            topic_id="topic_daily",
            capability_hint="test",
            evidence_refs=["evt::test_001"],
            update_kind="INTERACTION",
        )
        # 主流程不受影响
        assert result.success

    def test_stage_engine_exception_event_has_null_transition(self, neo4j_client):
        broken_engine = MagicMock(spec=BondStageEngine)
        broken_engine.apply_event.side_effect = RuntimeError("崩溃")
        writer = BondDynamicsWriter(
            neo4j_client=neo4j_client,
            stage_engine=broken_engine,
        )
        result = writer.apply_update(
            request_id="req_0001",
            intent_id="intent_0001",
            session_id="sess_001",
            seq=0,
            user_id="user_001",
            topic_id="topic_daily",
            capability_hint="test",
            evidence_refs=["evt::test_001"],
            update_kind="INTERACTION",
        )
        assert result.event.get("stage_transition") is None


# ── T-BOND-INTEG-06: 多用户独立状态 ──────────────────────────────────────────

class TestMultiUserIsolation:
    """T-BOND-INTEG-06: 多用户 Bond 状态相互独立。"""

    def test_two_users_independent_scores(self, writer_with_engine, stage_engine):
        _apply(writer_with_engine, user_id="alice", update_kind="MILESTONE", n=3)  # 30 分
        _apply(writer_with_engine, user_id="bob", update_kind="INTERACTION", n=5)  # 5 分

        alice_state = stage_engine.get_state("alice")
        bob_state = stage_engine.get_state("bob")

        assert alice_state.total_score == 30
        assert bob_state.total_score == 5
        assert alice_state.current_stage != bob_state.current_stage

    def test_user_stages_independent(self, writer_with_engine, stage_engine):
        _apply(writer_with_engine, user_id="user_a", update_kind="EMOTIONAL_SUPPORT", n=10)  # 80 分
        _apply(writer_with_engine, user_id="user_b", update_kind="INTERACTION", n=1)  # 1 分

        a_stage = stage_engine.get_state("user_a").current_stage
        b_stage = stage_engine.get_state("user_b").current_stage

        assert a_stage in [STAGE_CLOSE_FRIEND, STAGE_CONFIDANT]
        assert b_stage == STAGE_STRANGER


# ── T-BOND-INTEG-07: Bond Summary 端到端 ─────────────────────────────────────

class TestBondSummaryEndToEnd:
    """T-BOND-INTEG-07: apply_update 后 BondStageEngine.get_summary 返回正确 Summary。"""

    def test_summary_stage_matches_state(self, writer_with_engine, stage_engine):
        _apply(writer_with_engine, user_id="user_sum", update_kind="INTERACTION", n=10)
        summary = stage_engine.get_summary("user_sum")
        # M28-H：to_summary() 返回中文阶段名，ACQUAINTANCE 对应“相处中的义妹”
        assert summary["stage"] == "相处中的义妹"
        assert "trend" in summary
        assert "capability_hint" in summary

    def test_summary_no_numeric_values(self, writer_with_engine, stage_engine):
        _apply(writer_with_engine, user_id="user_sum2", update_kind="DEEP_TALK", n=5)
        summary = stage_engine.get_summary("user_sum2")
        # Summary 不含数值字段（FR-BOND-003）
        assert "total_score" not in summary
        assert "score_delta" not in summary
        assert "event_count" not in summary


# ── T-BOND-INTEG-08: update_kind 传递正确 ────────────────────────────────────

class TestUpdateKindPropagation:
    """T-BOND-INTEG-08: apply_update 的 update_kind 正确传递给 BondStageEngine。"""

    def test_update_kind_propagated(self, neo4j_client):
        mock_engine = MagicMock(spec=BondStageEngine)
        mock_engine.apply_event.return_value = MagicMock(
            from_stage=STAGE_STRANGER,
            to_stage=STAGE_STRANGER,
            transition_status=TRANSITION_STATUS_STABLE,
            score_delta=1,
            total_score=1,
        )
        writer = BondDynamicsWriter(
            neo4j_client=neo4j_client,
            stage_engine=mock_engine,
        )
        writer.apply_update(
            request_id="req_0001",
            intent_id="intent_0001",
            session_id="sess_001",
            seq=0,
            user_id="user_001",
            topic_id="topic_daily",
            capability_hint="test",
            evidence_refs=["evt::test_001"],
            update_kind="EMOTIONAL_SUPPORT",
        )
        call_kwargs = mock_engine.apply_event.call_args.kwargs
        assert call_kwargs["update_kind"] == "EMOTIONAL_SUPPORT"
        assert call_kwargs["user_id"] == "user_001"
        assert "evt::test_001" in call_kwargs["evidence_refs"]
