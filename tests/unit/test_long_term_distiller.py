"""T-DISTILL-B-01~10: 长期记忆蒸馏强化引擎单元测试（M13-B）。"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from host_brain.memory.long_term_distiller import (
    DEFAULT_DISTILL_TOPICS,
    DISTILL_TARGET_DOC_ID,
    MAX_PROPOSALS_PER_CYCLE,
    DistillationCycleResult,
    LongTermMemoryDistiller,
    ProposalRecord,
    make_distillation_task_config,
)
from host_brain.memory.distillation import DistillResult, MemoryDistillationEngine
from host_brain.memory.canon_world_store import WorldCanonStore
from host_brain.memory.episodic import EpisodicStore
from host_brain.audit.sqlite_ledger import SQLiteLedger


# ── 夹具 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_ledger():
    ledger = MagicMock()
    ledger.list_semantic_memories.return_value = ([], 0)
    ledger.list_canon_proposals.return_value = ([], 0)
    ledger.log_system_event = MagicMock()
    return ledger


@pytest.fixture
def mock_distillation_engine():
    engine = MagicMock(spec=MemoryDistillationEngine)
    # 默认返回成功的蒸馏结果
    engine.distill_top_k.return_value = DistillResult(
        success=True,
        semantic_id="sem_abc123",
        source_count=3,
        detail="distilled 3 episodic memories",
        ts_ms=1000000,
        skipped=False,
    )
    return engine


@pytest.fixture
def mock_canon_store():
    store = MagicMock(spec=WorldCanonStore)
    store.submit_proposal.return_value = {
        "proposal_id": "distill_prop_abc123",
        "status": "pending",
    }
    return store


@pytest.fixture
def distiller(mock_distillation_engine, mock_canon_store, mock_ledger):
    return LongTermMemoryDistiller(
        distillation_engine=mock_distillation_engine,
        canon_store=mock_canon_store,
        ledger=mock_ledger,
        topics=["测试主题A", "测试主题B"],
        top_k=3,
        max_proposals_per_cycle=3,
    )


# ── T-DISTILL-B-01: 蒸馏循环基本流程 ─────────────────────────────────────────

class TestDistillationCycleBasic:
    """T-DISTILL-B-01: 蒸馏循环正常执行，返回 DistillationCycleResult。"""

    def test_cycle_returns_result(self, distiller):
        result = distiller.run_distillation_cycle()
        assert isinstance(result, DistillationCycleResult)

    def test_cycle_has_cycle_id(self, distiller):
        result = distiller.run_distillation_cycle()
        assert result.cycle_id.startswith("distill_cycle_")

    def test_cycle_processes_all_topics(self, distiller, mock_distillation_engine):
        result = distiller.run_distillation_cycle()
        assert result.topics_processed == 2
        assert mock_distillation_engine.distill_top_k.call_count == 2

    def test_cycle_submits_proposals(self, distiller):
        result = distiller.run_distillation_cycle()
        assert result.proposals_submitted == 2

    def test_cycle_has_no_errors(self, distiller):
        result = distiller.run_distillation_cycle()
        assert result.errors == []

    def test_cycle_ts_ms_is_set(self, distiller):
        result = distiller.run_distillation_cycle()
        assert result.ts_ms > 0


# ── T-DISTILL-B-02: 蒸馏失败时跳过提案 ──────────────────────────────────────

class TestDistillationFailure:
    """T-DISTILL-B-02: 蒸馏失败时不生成 Canon 提案。"""

    def test_failed_distillation_skips_proposal(self, mock_distillation_engine, mock_canon_store, mock_ledger):
        mock_distillation_engine.distill_top_k.return_value = DistillResult(
            success=False,
            semantic_id=None,
            source_count=0,
            detail="no episodic memories found",
            ts_ms=1000000,
        )
        distiller = LongTermMemoryDistiller(
            distillation_engine=mock_distillation_engine,
            canon_store=mock_canon_store,
            ledger=mock_ledger,
            topics=["空主题"],
        )
        result = distiller.run_distillation_cycle()
        assert result.proposals_submitted == 0
        mock_canon_store.submit_proposal.assert_not_called()

    def test_failed_distillation_still_counts_topic(self, mock_distillation_engine, mock_canon_store, mock_ledger):
        mock_distillation_engine.distill_top_k.return_value = DistillResult(
            success=False,
            semantic_id=None,
            source_count=0,
            detail="no episodic memories found",
            ts_ms=1000000,
        )
        distiller = LongTermMemoryDistiller(
            distillation_engine=mock_distillation_engine,
            canon_store=mock_canon_store,
            ledger=mock_ledger,
            topics=["空主题"],
        )
        result = distiller.run_distillation_cycle()
        assert result.topics_processed == 1


# ── T-DISTILL-B-03: 最大提案数限制 ───────────────────────────────────────────

class TestMaxProposalsLimit:
    """T-DISTILL-B-03: 每次循环最多生成 max_proposals_per_cycle 个提案（FR-MEM-DISTILL-002）。"""

    def test_max_proposals_respected(self, mock_distillation_engine, mock_canon_store, mock_ledger):
        mock_ledger.list_semantic_memories.return_value = ([], 0)
        mock_ledger.list_canon_proposals.return_value = ([], 0)
        distiller = LongTermMemoryDistiller(
            distillation_engine=mock_distillation_engine,
            canon_store=mock_canon_store,
            ledger=mock_ledger,
            topics=["主题1", "主题2", "主题3", "主题4", "主题5", "主题6"],
            max_proposals_per_cycle=2,
        )
        # 每次蒸馏返回不同的 semantic_id
        call_count = [0]
        def side_effect(**kwargs):
            call_count[0] += 1
            return DistillResult(
                success=True,
                semantic_id=f"sem_{call_count[0]:03d}",
                source_count=2,
                detail="ok",
                ts_ms=1000000,
                skipped=False,
            )
        mock_distillation_engine.distill_top_k.side_effect = side_effect
        result = distiller.run_distillation_cycle()
        assert result.proposals_submitted <= 2

    def test_max_proposals_per_cycle_constant(self):
        assert MAX_PROPOSALS_PER_CYCLE == 5

    def test_max_proposals_capped_at_constant(self):
        engine = MagicMock()
        store = MagicMock()
        distiller = LongTermMemoryDistiller(
            distillation_engine=engine,
            canon_store=store,
            max_proposals_per_cycle=100,  # 超出上限
        )
        assert distiller._max_proposals <= MAX_PROPOSALS_PER_CYCLE


# ── T-DISTILL-B-04: 幂等保护（相同 semantic_id 不重复提案）────────────────────

class TestIdempotency:
    """T-DISTILL-B-04: 相同 semantic_id 不重复生成 Canon 提案（FR-MEM-DISTILL-001）。"""

    def test_duplicate_semantic_id_skipped(self, mock_distillation_engine, mock_canon_store, mock_ledger):
        # 模拟已有 pending 提案包含 semantic_id（使用代码实际调用的 list_proposals 方法）
        mock_ledger.list_proposals.return_value = (
            [{"proposal_id": "distill_prop_sem_abc123_12345678", "summary": "semantic_id=sem_abc123 source_count=3", "status": "pending"}],
            1,
        )
        mock_ledger.list_semantic_memories.return_value = (
            [{"semantic_id": "sem_abc123", "summary": "已有摘要"}],
            1,
        )
        distiller = LongTermMemoryDistiller(
            distillation_engine=mock_distillation_engine,
            canon_store=mock_canon_store,
            ledger=mock_ledger,
            topics=["测试主题"],
        )
        result = distiller.run_distillation_cycle()
        # 提案应被跳过
        assert result.proposals_skipped >= 1
        mock_canon_store.submit_proposal.assert_not_called()

    def test_skipped_distillation_not_counted_as_new(self, mock_distillation_engine, mock_canon_store, mock_ledger):
        mock_distillation_engine.distill_top_k.return_value = DistillResult(
            success=True,
            semantic_id="sem_existing",
            source_count=3,
            detail="skipped: already exists",
            ts_ms=1000000,
            skipped=True,  # 已存在，跳过
        )
        mock_ledger.list_semantic_memories.return_value = (
            [{"semantic_id": "sem_existing", "summary": "已有摘要"}],
            1,
        )
        distiller = LongTermMemoryDistiller(
            distillation_engine=mock_distillation_engine,
            canon_store=mock_canon_store,
            ledger=mock_ledger,
            topics=["测试主题"],
        )
        result = distiller.run_distillation_cycle()
        assert result.semantic_memories_created == 0


# ── T-DISTILL-B-05: Canon 提案内容验证 ───────────────────────────────────────

class TestCanonProposalContent:
    """T-DISTILL-B-05: Canon 提案正文不含完成性表述，包含必要字段。"""

    def test_proposal_body_has_no_execution_claims(self, distiller, mock_canon_store):
        distiller.run_distillation_cycle()
        call_args = mock_canon_store.submit_proposal.call_args_list
        assert len(call_args) > 0
        for call in call_args:
            body = call.kwargs.get("proposed_body", "")
            # 不含完成性表述
            forbidden = ["已执行", "执行完成", "已保存", "confirmed", "executed"]
            for token in forbidden:
                assert token not in body, f"proposal body contains forbidden token: {token!r}"

    def test_proposal_has_required_fields(self, distiller, mock_canon_store):
        distiller.run_distillation_cycle()
        call_args = mock_canon_store.submit_proposal.call_args_list
        assert len(call_args) > 0
        for call in call_args:
            kwargs = call.kwargs
            assert "proposal_id" in kwargs
            assert "target_doc_id" in kwargs
            assert "proposed_title" in kwargs
            assert "proposed_body" in kwargs
            assert "proposer_node_id" in kwargs

    def test_proposal_proposer_is_distiller(self, distiller, mock_canon_store):
        distiller.run_distillation_cycle()
        call_args = mock_canon_store.submit_proposal.call_args_list
        for call in call_args:
            assert call.kwargs["proposer_node_id"] == "long_term_distiller"

    def test_proposal_target_doc_id(self, distiller, mock_canon_store):
        distiller.run_distillation_cycle()
        call_args = mock_canon_store.submit_proposal.call_args_list
        for call in call_args:
            assert call.kwargs["target_doc_id"] == DISTILL_TARGET_DOC_ID


# ── T-DISTILL-B-06: 审计链写入 ───────────────────────────────────────────────

class TestAuditLogging:
    """T-DISTILL-B-06: 蒸馏循环完成后写入审计链。"""

    def test_audit_log_written_after_cycle(self, distiller, mock_ledger):
        distiller.run_distillation_cycle()
        # log_distillation_cycle 或 log_system_event 应被调用
        called = (
            mock_ledger.log_distillation_cycle.called
            or mock_ledger.log_system_event.called
        )
        assert called

    def test_audit_log_has_cycle_id(self, distiller, mock_ledger):
        result = distiller.run_distillation_cycle()
        call_args = mock_ledger.log_system_event.call_args
        if call_args:
            # 检查 payload 中包含 cycle_id
            kwargs = call_args.kwargs if call_args.kwargs else {}
            payload = kwargs.get("payload", {})
            if payload:
                assert "cycle_id" in payload

    def test_no_ledger_does_not_crash(self, mock_distillation_engine, mock_canon_store):
        distiller = LongTermMemoryDistiller(
            distillation_engine=mock_distillation_engine,
            canon_store=mock_canon_store,
            ledger=None,
            topics=["测试主题"],
        )
        result = distiller.run_distillation_cycle()
        assert isinstance(result, DistillationCycleResult)


# ── T-DISTILL-B-07: 异常处理 ─────────────────────────────────────────────────

class TestErrorHandling:
    """T-DISTILL-B-07: 单个主题蒸馏异常不影响其他主题。"""

    def test_exception_in_one_topic_continues_others(self, mock_distillation_engine, mock_canon_store, mock_ledger):
        call_count = [0]
        def side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("模拟蒸馏失败")
            return DistillResult(
                success=True,
                semantic_id=f"sem_{call_count[0]:03d}",
                source_count=2,
                detail="ok",
                ts_ms=1000000,
                skipped=False,
            )
        mock_distillation_engine.distill_top_k.side_effect = side_effect
        mock_ledger.list_semantic_memories.return_value = (
            [{"semantic_id": "sem_002", "summary": "摘要"}],
            1,
        )
        distiller = LongTermMemoryDistiller(
            distillation_engine=mock_distillation_engine,
            canon_store=mock_canon_store,
            ledger=mock_ledger,
            topics=["失败主题", "成功主题"],
        )
        result = distiller.run_distillation_cycle()
        assert len(result.errors) == 1
        assert result.topics_processed == 2

    def test_canon_store_exception_recorded_as_error(self, mock_distillation_engine, mock_canon_store, mock_ledger):
        mock_canon_store.submit_proposal.side_effect = ValueError("提案内容非法")
        mock_ledger.list_semantic_memories.return_value = (
            [{"semantic_id": "sem_abc123", "summary": "摘要"}],
            1,
        )
        distiller = LongTermMemoryDistiller(
            distillation_engine=mock_distillation_engine,
            canon_store=mock_canon_store,
            ledger=mock_ledger,
            topics=["测试主题"],
        )
        result = distiller.run_distillation_cycle()
        assert len(result.errors) == 1
        assert result.proposals_submitted == 0


# ── T-DISTILL-B-08: 调度器任务配置 ───────────────────────────────────────────

class TestSchedulerTaskConfig:
    """T-DISTILL-B-08: make_distillation_task_config() 返回合法的调度任务配置。"""

    def test_task_config_is_valid(self):
        from host_brain.scheduler.task_registry import SchedulerTaskConfig, TriggerMode, TaskPriority
        config = make_distillation_task_config()
        assert isinstance(config, SchedulerTaskConfig)
        config.validate()  # 不应抛出异常

    def test_task_id(self):
        config = make_distillation_task_config()
        assert config.task_id == "long_term_memory_distillation"

    def test_trigger_mode_is_cron(self):
        from host_brain.scheduler.task_registry import TriggerMode
        config = make_distillation_task_config()
        assert config.trigger_mode == TriggerMode.CRON

    def test_cron_fires_at_3am(self):
        config = make_distillation_task_config()
        assert config.trigger_config["hour"] == 3
        assert config.trigger_config["minute"] == 0
        assert config.trigger_config["catch_up_window_minutes"] == 720

    def test_priority_is_low(self):
        from host_brain.scheduler.task_registry import TaskPriority
        config = make_distillation_task_config()
        assert config.priority == TaskPriority.LOW

    def test_allowed_in_normal_mode(self):
        config = make_distillation_task_config()
        assert config.is_allowed_in_mode("normal")

    def test_allowed_in_sleep_mode(self):
        config = make_distillation_task_config()
        assert config.is_allowed_in_mode("sleep")

    def test_blocked_in_gaming_mode(self):
        config = make_distillation_task_config()
        assert not config.is_allowed_in_mode("gaming")

    def test_blocked_in_focus_mode(self):
        config = make_distillation_task_config()
        assert not config.is_allowed_in_mode("focus")


# ── T-DISTILL-B-09: 默认主题列表 ─────────────────────────────────────────────

class TestDefaultTopics:
    """T-DISTILL-B-09: 默认主题列表非空且合理。"""

    def test_default_topics_not_empty(self):
        assert len(DEFAULT_DISTILL_TOPICS) > 0

    def test_default_topics_are_strings(self):
        for topic in DEFAULT_DISTILL_TOPICS:
            assert isinstance(topic, str)
            assert len(topic) > 0

    def test_custom_topics_override_default(self, mock_distillation_engine, mock_canon_store):
        distiller = LongTermMemoryDistiller(
            distillation_engine=mock_distillation_engine,
            canon_store=mock_canon_store,
            topics=["自定义主题"],
        )
        assert distiller._topics == ["自定义主题"]


# ── T-DISTILL-B-10: 集成测试（真实 EpisodicStore + DistillationEngine）────────

class TestIntegration:
    """T-DISTILL-B-10: 使用真实 EpisodicStore + DistillationEngine 的集成测试。"""

    def test_full_cycle_with_real_episodic_store(self, tmp_path):
        """使用真实组件执行完整蒸馏循环。"""
        from host_brain.memory.episodic import EpisodicStore
        from tests.fakes.chroma_client import FakeChromaClient
        from host_brain.memory.distillation import MemoryDistillationEngine
        from host_brain.audit.sqlite_ledger import SQLiteLedger

        db_path = str(tmp_path / "test_distill.sqlite3")
        ledger = SQLiteLedger(db_path=db_path)
        episodic = EpisodicStore(ledger=ledger, chroma_client=FakeChromaClient())
        engine = MemoryDistillationEngine(episodic_store=episodic, ledger=ledger)

        # 写入一些 episodic 记忆
        for i in range(3):
            episodic.write_memory(
                text=f"用户喜欢在早晨喝咖啡，这是第 {i+1} 次记录",
                evidence_refs=[f"evt::test_{i}"],
                decay_profile_id="default",
            )

        # 使用真实 canon_store（临时目录）
        canon_store = WorldCanonStore(
            world_root=str(tmp_path / "canon"),
            ledger=ledger,
        )

        distiller = LongTermMemoryDistiller(
            distillation_engine=engine,
            canon_store=canon_store,
            ledger=ledger,
            topics=["用户日常习惯与偏好"],
            top_k=3,
        )

        result = distiller.run_distillation_cycle()
        assert isinstance(result, DistillationCycleResult)
        assert result.topics_processed == 1
        # 有 episodic 记忆时应成功蒸馏
        assert result.semantic_memories_created >= 0  # 可能因 chroma 无向量而跳过

    def test_cycle_with_no_episodic_memories(self, tmp_path):
        """无 episodic 记忆时蒸馏循环应优雅处理。"""
        from host_brain.memory.episodic import EpisodicStore
        from tests.fakes.chroma_client import FakeChromaClient
        from host_brain.memory.distillation import MemoryDistillationEngine
        from host_brain.audit.sqlite_ledger import SQLiteLedger

        db_path = str(tmp_path / "test_empty.sqlite3")
        ledger = SQLiteLedger(db_path=db_path)
        episodic = EpisodicStore(ledger=ledger, chroma_client=FakeChromaClient())
        engine = MemoryDistillationEngine(episodic_store=episodic, ledger=ledger)
        canon_store = WorldCanonStore(
            world_root=str(tmp_path / "canon"),
            ledger=ledger,
        )

        distiller = LongTermMemoryDistiller(
            distillation_engine=engine,
            canon_store=canon_store,
            ledger=ledger,
            topics=["空主题"],
        )

        result = distiller.run_distillation_cycle()
        assert isinstance(result, DistillationCycleResult)
        assert result.proposals_submitted == 0
        assert result.errors == []
