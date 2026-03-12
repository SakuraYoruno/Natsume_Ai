"""
tests/unit/test_new_features_todo18_20.py

覆盖：
  TODO-18：BondGraph StateArbiter 查询（_resolve_state_arbiter_bond_fact）
  TODO-20：CanonProposalNode 自动提交 Canon 提案
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, patch


def _run(coro):
    """在新事件循环中运行协程（与项目其他测试保持一致）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============================================================
# TODO-18：BondGraph StateArbiter 查询
# ============================================================

class TestBondGraphStateArbiter:
    """_resolve_state_arbiter_bond_fact 单元测试。"""

    def _make_bond_state(self, stage="CLOSE", score=0.8, label="亲密", interaction_count=42):
        state = MagicMock()
        state.stage = stage
        state.score = score
        state.label = label
        state.interaction_count = interaction_count
        return state

    def test_bond_fact_applied_when_engine_available(self):
        """BondStageEngine 可用时，应返回 APPLIED 状态和证据条目。"""
        import host_brain.app.chat_stream_handler as stream_mod

        mock_engine = MagicMock()
        mock_engine.get_bond_state = MagicMock(return_value=self._make_bond_state())

        # asyncio.to_thread 在测试中直接同步执行
        async def _fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("host_brain.app.dependencies.get_bond_stage_engine", return_value=mock_engine), \
             patch("host_brain.app.chat_stream_handler.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = _fake_to_thread
            summary, evidence = _run(stream_mod._resolve_state_arbiter_bond_fact(
                session_id="test_session",
                request_id="test_req",
            ))

        assert summary["status"] == "APPLIED"
        assert summary["bond_stage"] == "CLOSE"
        assert summary["bond_score"] == pytest.approx(0.8)
        assert evidence is not None
        assert evidence["slot_name"] == "bond_graph"
        assert evidence["score"] == pytest.approx(0.65)
        assert "CLOSE" in evidence["snippet"]

    def test_bond_fact_skipped_when_engine_unavailable(self):
        """BondStageEngine 不可用时，应返回 SKIPPED 状态。"""
        import host_brain.app.chat_stream_handler as stream_mod

        with patch("host_brain.app.dependencies.get_bond_stage_engine", return_value=None):
            summary, evidence = _run(stream_mod._resolve_state_arbiter_bond_fact(
                session_id="test_session",
                request_id="test_req",
            ))

        assert summary["status"] == "SKIPPED"
        assert summary["reason_code"] == "BOND_ENGINE_NOT_AVAILABLE"
        assert evidence is None

    def test_bond_fact_degraded_on_exception(self):
        """BondStageEngine 抛出异常时，应返回 DEGRADED 状态，不阻断链路。"""
        import host_brain.app.chat_stream_handler as stream_mod

        mock_engine = MagicMock()
        mock_engine.get_bond_state = MagicMock(side_effect=RuntimeError("DB error"))

        async def _fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("host_brain.app.dependencies.get_bond_stage_engine", return_value=mock_engine), \
             patch("host_brain.app.chat_stream_handler.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = _fake_to_thread
            summary, evidence = _run(stream_mod._resolve_state_arbiter_bond_fact(
                session_id="test_session",
                request_id="test_req",
            ))

        assert summary["status"] == "DEGRADED"
        assert summary["reason_code"] == "BOND_GRAPH_FAILED"
        assert evidence is None

    def test_bond_fact_skipped_when_no_bond_state(self):
        """BondStageEngine 返回 None 时，应返回 SKIPPED 状态。"""
        import host_brain.app.chat_stream_handler as stream_mod

        mock_engine = MagicMock()
        mock_engine.get_bond_state = MagicMock(return_value=None)

        async def _fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("host_brain.app.dependencies.get_bond_stage_engine", return_value=mock_engine), \
             patch("host_brain.app.chat_stream_handler.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = _fake_to_thread
            summary, evidence = _run(stream_mod._resolve_state_arbiter_bond_fact(
                session_id="test_session",
                request_id="test_req",
            ))

        assert summary["status"] == "SKIPPED"
        assert summary["reason_code"] == "NO_BOND_STATE"
        assert evidence is None


# ============================================================
# TODO-20：CanonProposalNode
# ============================================================

class TestCanonProposalNode:
    """CanonProposalNode 单元测试。"""

    def _make_ctx(self, final_response="", audit_tags=None, metadata=None):
        from host_brain.app.graph.main_chain import ChainContext
        ctx = ChainContext(
            request_id="test_req_001",
            user_message="test message",
            session_id="test_session",
        )
        ctx.final_response = final_response
        ctx.audit_tags = list(audit_tags or [])
        ctx.metadata = dict(metadata or {})
        return ctx

    def test_no_trigger_skips_proposal(self):
        """无触发信号时，节点应跳过提案提交。"""
        from host_brain.app.graph.main_chain import CanonProposalNode
        node = CanonProposalNode()
        ctx = self._make_ctx(final_response="普通回复，无世界观更新")

        result = _run(node.execute(ctx))

        assert result is True
        assert ctx.metadata["canon_proposal_node"]["status"] == "SKIPPED"
        assert ctx.metadata["canon_proposal_node"]["reason_code"] == "NO_CANON_SIGNAL"

    def test_llm_marker_triggers_proposal(self):
        """LLM 输出包含 [CANON_UPDATE] 标记时，应触发提案提交（降级也可接受）。"""
        from host_brain.app.graph.main_chain import CanonProposalNode

        mock_store = MagicMock()
        mock_store.submit_proposal = MagicMock(return_value={"proposal_id": "test_proposal_001"})

        def _fake_to_thread(fn, **kwargs):
            async def _wrap():
                return fn(**kwargs)
            return _wrap()

        node = CanonProposalNode()
        ctx = self._make_ctx(
            final_response="这是一段回复。[CANON_UPDATE] 夏目的家乡是京都，这是新的世界观设定。"
        )

        with patch("host_brain.app.dependencies.get_ledger", return_value=MagicMock()), \
             patch("host_brain.memory.canon_world_store.WorldCanonStore", return_value=mock_store), \
             patch.object(asyncio, "to_thread", side_effect=_fake_to_thread):
            result = _run(node.execute(ctx))

        assert result is True
        status = ctx.metadata["canon_proposal_node"]["status"]
        assert status in ("SUBMITTED", "DEGRADED", "SKIPPED_DUPLICATE")

    def test_audit_tag_triggers_proposal(self):
        """audit_tags 包含 CANON_PROPOSAL_REQUESTED 时，应触发提案提交。"""
        from host_brain.app.graph.main_chain import CanonProposalNode

        mock_store = MagicMock()
        mock_store.submit_proposal = MagicMock(return_value={"proposal_id": "test_proposal_002"})

        def _fake_to_thread(fn, **kwargs):
            async def _wrap():
                return fn(**kwargs)
            return _wrap()

        node = CanonProposalNode()
        ctx = self._make_ctx(
            final_response="普通回复",
            audit_tags=["CANON_PROPOSAL_REQUESTED"],
        )

        with patch("host_brain.app.dependencies.get_ledger", return_value=MagicMock()), \
             patch("host_brain.memory.canon_world_store.WorldCanonStore", return_value=mock_store), \
             patch.object(asyncio, "to_thread", side_effect=_fake_to_thread):
            result = _run(node.execute(ctx))

        assert result is True
        status = ctx.metadata["canon_proposal_node"]["status"]
        assert status in ("SUBMITTED", "DEGRADED", "SKIPPED_DUPLICATE")

    def test_proposal_degraded_on_store_error(self):
        """WorldCanonStore 抛出异常时，应降级处理，不阻断主链路。"""
        from host_brain.app.graph.main_chain import CanonProposalNode
        import host_brain.app.graph.main_chain as chain_mod

        mock_store = MagicMock()
        mock_store.submit_proposal = MagicMock(side_effect=RuntimeError("DB write error"))

        async def _fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        node = CanonProposalNode()
        ctx = self._make_ctx(
            final_response="[CANON_UPDATE] 新的世界观设定内容"
        )

        with patch("host_brain.app.dependencies.get_ledger", return_value=MagicMock()), \
             patch("host_brain.memory.canon_world_store.WorldCanonStore", return_value=mock_store), \
             patch("host_brain.app.graph.main_chain.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = _fake_to_thread
            result = _run(node.execute(ctx))

        # 即使失败也不阻断链路
        assert result is True
        assert ctx.metadata["canon_proposal_node"]["status"] == "DEGRADED"
        assert "CANON_PROPOSAL_DEGRADED" in ctx.audit_tags

    def test_canon_proposal_node_in_main_chain(self):
        """CanonProposalNode 应出现在 MainChain._NODE_FACTORIES 中，且顺序正确。"""
        from host_brain.app.graph.main_chain import MainChain

        node_names = [factory.name for factory in MainChain._NODE_FACTORIES]
        assert "canon_proposal" in node_names

        # 验证节点顺序：audit 之后，airlock_sanitizer 之前
        audit_idx = node_names.index("audit")
        canon_idx = node_names.index("canon_proposal")
        sanitizer_idx = node_names.index("airlock_sanitizer")
        assert audit_idx < canon_idx < sanitizer_idx
