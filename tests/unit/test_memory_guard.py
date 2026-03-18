"""
test_memory_guard.py —— MemoryGuard 单元测试
"""
from __future__ import annotations

import pytest

from host_brain.app.memory.memory_guard import MemoryGuard, MemoryGuardError
from host_brain.app.schemas.memory_types import (
    CanonProposal,
    EpisodicRecord,
    EvidenceLevel,
    ProposalType,
    ProposerClass,
)


class TestEpisodicWriteGuard:
    """EpisodicStore 写入校验测试。"""

    def test_passes_with_sufficient_turns_and_tags(self):
        """验证满足条件时通过校验（需求 3.1）。"""
        guard = MemoryGuard()
        record = EpisodicRecord(
            session_id="s1",
            content="今天很开心",
            turn_count=3,
            emotion_tags=["happy"],
        )
        guard.check_episodic_write(record)  # 不应抛出异常

    def test_fails_with_insufficient_turns(self):
        """验证轮次不足时拒绝写入（需求 3.1）。"""
        guard = MemoryGuard()
        record = EpisodicRecord(
            session_id="s1",
            content="只有一轮",
            turn_count=1,
            emotion_tags=["happy"],
        )
        with pytest.raises(MemoryGuardError) as exc_info:
            guard.check_episodic_write(record)
        assert "EPISODIC_TURN_TOO_FEW" in exc_info.value.reason

    def test_fails_without_qualifying_tags(self):
        """验证缺少标签时拒绝写入（需求 3.1）。"""
        guard = MemoryGuard()
        record = EpisodicRecord(
            session_id="s1",
            content="无标签内容",
            turn_count=5,
        )
        with pytest.raises(MemoryGuardError) as exc_info:
            guard.check_episodic_write(record)
        assert "EPISODIC_NO_QUALIFYING_TAGS" in exc_info.value.reason


class TestCanonProposalGuard:
    """Canon 提案校验测试。"""

    def test_passes_valid_proposal(self):
        """验证合法提案通过校验（需求 3.4）。"""
        guard = MemoryGuard()
        proposal = CanonProposal(
            proposal_type=ProposalType.FACT_CANDIDATE,
            content="用户喜欢猫",
            source_hash="abc123",
        )
        guard.check_canon_proposal(proposal)  # 不应抛出异常

    def test_idempotent_protection(self):
        """验证相同 source_hash 拒绝重复写入（需求 3.5）。"""
        guard = MemoryGuard()
        proposal = CanonProposal(
            proposal_type=ProposalType.FACT_CANDIDATE,
            content="用户喜欢猫",
            source_hash="unique_hash_001",
        )
        guard.check_canon_proposal(proposal)
        guard.mark_proposal_processed("unique_hash_001")

        # 第二次提交相同 hash 应被拒绝
        with pytest.raises(MemoryGuardError) as exc_info:
            guard.check_canon_proposal(proposal)
        assert "CANON_DUPLICATE_SOURCE_HASH" in exc_info.value.reason

    def test_dream_content_blocked_from_canon(self):
        """验证 dream 内容不得写入 Canon（需求 3.6）。"""
        guard = MemoryGuard()
        proposal = CanonProposal(
            proposal_type=ProposalType.FACT_CANDIDATE,  # 试图以 FACT 类型写入
            content="梦中的内容",
            source_hash="dream_hash_001",
            evidence_level=EvidenceLevel.NARRATIVE_ONLY,
        )
        with pytest.raises(MemoryGuardError) as exc_info:
            guard.check_dream_canon_proposal(proposal)
        assert "DREAM_CANON_WRITE_BLOCKED" in exc_info.value.reason


class TestDistillationEligibility:
    """蒸馏资格校验测试（需求 3.3）。"""

    def test_milestone_always_eligible(self):
        """验证 MILESTONE 级别内容直接满足蒸馏条件。"""
        guard = MemoryGuard()
        assert guard.check_distillation_eligibility(
            "hash1", occurrence_count=1, evidence_level=EvidenceLevel.MILESTONE
        ) is True

    def test_high_frequency_eligible(self):
        """验证出现频次 ≥ 3 满足蒸馏条件。"""
        guard = MemoryGuard()
        assert guard.check_distillation_eligibility(
            "hash2", occurrence_count=3, evidence_level=EvidenceLevel.CANDIDATE
        ) is True

    def test_low_frequency_not_eligible(self):
        """验证出现频次 = 0 且非 MILESTONE 不满足蒸馏条件。"""
        guard = MemoryGuard()
        assert guard.check_distillation_eligibility(
            "hash3", occurrence_count=0, evidence_level=EvidenceLevel.CANDIDATE
        ) is False


class TestEmotionTagAggregation:
    """情绪标签 session 粒度聚合测试（需求 3.7）。"""

    def test_tags_aggregated_not_overwritten(self):
        """验证情绪标签追加而非覆盖。"""
        guard = MemoryGuard()
        guard.aggregate_emotion_tags("s1", ["happy", "excited"])
        guard.aggregate_emotion_tags("s1", ["calm"])  # 第二轮追加

        tags = guard.get_session_emotion_tags("s1")
        assert "happy" in tags
        assert "excited" in tags
        assert "calm" in tags
        assert len(tags) == 3

    def test_different_sessions_isolated(self):
        """验证不同 session 的情绪标签相互隔离。"""
        guard = MemoryGuard()
        guard.aggregate_emotion_tags("s1", ["happy"])
        guard.aggregate_emotion_tags("s2", ["sad"])

        assert "happy" in guard.get_session_emotion_tags("s1")
        assert "sad" not in guard.get_session_emotion_tags("s1")
        assert "sad" in guard.get_session_emotion_tags("s2")


class TestSourceHashComputation:
    """source_hash 计算测试。"""

    def test_same_content_same_hash(self):
        """验证相同内容产生相同哈希。"""
        h1 = MemoryGuard.compute_source_hash("用户喜欢猫")
        h2 = MemoryGuard.compute_source_hash("用户喜欢猫")
        assert h1 == h2

    def test_different_content_different_hash(self):
        """验证不同内容产生不同哈希。"""
        h1 = MemoryGuard.compute_source_hash("用户喜欢猫")
        h2 = MemoryGuard.compute_source_hash("用户喜欢狗")
        assert h1 != h2
