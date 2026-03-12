"""TODO-15/16/17 单元测试。

覆盖范围：
  - TODO-15 EpisodicStoreNode：真实 persist_chat_episodic_memory 调用 / 降级回退
  - TODO-16 AuditNode：真实 ledger 写入 / 降级回退
  - TODO-17 StateArbiter：style_canon_facts / episodic_latest 查询函数
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from host_brain.app.graph.main_chain import (
    AuditNode,
    ChainContext,
    EpisodicStoreNode,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _make_ctx(
    *,
    request_id: str = "req-test-001",
    user_message: str = "你好",
    session_id: str = "sess-001",
    final_response: str = "你好！今天天气不错。",
) -> ChainContext:
    ctx = ChainContext(
        request_id=request_id,
        user_message=user_message,
        session_id=session_id,
    )
    ctx.quarantine_passed = True
    ctx.governor_passed = True
    ctx.final_response = final_response
    return ctx


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# TODO-15：EpisodicStoreNode 真实业务化
# ---------------------------------------------------------------------------

class TestEpisodicStoreNode:
    """TODO-15：EpisodicStoreNode 接入真实 persist_chat_episodic_memory。"""

    def test_episodic_store_writes_when_response_available(self):
        """有最终响应时，应调用 persist_chat_episodic_memory 并写入 EPISODIC_WRITTEN 标签。"""
        ctx = _make_ctx(final_response="今天天气很好！")

        mock_result = {"status": "OK", "memory_id": "mem-001", "emotion_tag": "JOY"}
        mock_store = MagicMock()

        async def _fake_to_thread(fn, **kwargs):
            return fn(**kwargs)

        def _mock_persist(**kwargs):
            return mock_result

        async def _run_async():
            node = EpisodicStoreNode()
            with patch("host_brain.app.dependencies.get_episodic_store", return_value=mock_store), \
                 patch.object(asyncio, "to_thread", side_effect=_fake_to_thread), \
                 patch(
                     "host_brain.app.observability_persist.persist_chat_episodic_memory",
                     side_effect=_mock_persist,
                 ):
                return await node.execute(ctx)

        result = _run(_run_async())
        assert result is True
        assert ctx.metadata["episodic_store"]["status"] == "OK"
        assert ctx.metadata["episodic_store"]["memory_id"] == "mem-001"
        assert "EPISODIC_WRITTEN" in ctx.audit_tags

    def test_episodic_store_skips_when_no_response(self):
        """无最终响应时，应跳过写入并标记 SKIPPED_NO_RESPONSE。"""
        ctx = _make_ctx(final_response="")
        node = EpisodicStoreNode()
        result = _run(node.execute(ctx))
        assert result is True
        assert ctx.metadata["episodic_store"]["status"] == "SKIPPED_NO_RESPONSE"

    def test_episodic_store_degrades_when_store_unavailable(self):
        """EpisodicStore 不可用时，应降级并标记 SKIPPED_NO_STORE。"""
        ctx = _make_ctx(final_response="有响应")
        node = EpisodicStoreNode()

        with patch("host_brain.app.dependencies.get_episodic_store", return_value=None):
            result = _run(node.execute(ctx))

        assert result is True
        assert ctx.metadata["episodic_store"]["status"] == "SKIPPED_NO_STORE"

    def test_episodic_store_degrades_on_exception(self):
        """persist_chat_episodic_memory 抛出异常时，应降级并标记 EPISODIC_DEGRADED。"""
        ctx = _make_ctx(final_response="有响应")
        node = EpisodicStoreNode()

        mock_store = MagicMock()

        async def _fake_to_thread(fn, **kwargs):
            raise RuntimeError("DB unavailable")

        with patch("host_brain.app.dependencies.get_episodic_store", return_value=mock_store), \
             patch.object(asyncio, "to_thread", side_effect=_fake_to_thread):
            result = _run(node.execute(ctx))

        assert result is True
        assert ctx.metadata["episodic_store"]["status"] == "DEGRADED"
        assert "EPISODIC_DEGRADED" in ctx.audit_tags


# ---------------------------------------------------------------------------
# TODO-16：AuditNode 真实写入
# ---------------------------------------------------------------------------

class TestAuditNode:
    """TODO-16：AuditNode 接入真实 ledger 写入。"""

    def test_audit_node_writes_to_ledger(self):
        """AuditNode 应调用 ledger.log_error 写入审计快照。"""
        ctx = _make_ctx()
        ctx.audit_tags.append("TEST_TAG")
        node = AuditNode()

        mock_ledger = MagicMock()

        async def _fake_to_thread(fn, **kwargs):
            return fn(**kwargs)

        with patch("host_brain.app.dependencies.get_ledger", return_value=mock_ledger), \
             patch.object(asyncio, "to_thread", side_effect=_fake_to_thread):
            result = _run(node.execute(ctx))

        assert result is True
        assert "AUDIT_SNAPSHOT_WRITTEN" in ctx.audit_tags
        assert "audit_id" in ctx.metadata["audit"]
        # 验证 ledger.log_error 被调用
        mock_ledger.log_error.assert_called_once()
        call_kwargs = mock_ledger.log_error.call_args[1]
        assert call_kwargs["stage"] == "AUDIT_SNAPSHOT"
        assert call_kwargs["reason_code"] == "AUDIT_SNAPSHOT_WRITTEN"
        assert ctx.request_id in call_kwargs["detail"]

    def test_audit_node_degrades_when_ledger_unavailable(self):
        """ledger 不可用时，AuditNode 应降级但不阻断主链路。"""
        ctx = _make_ctx()
        node = AuditNode()

        async def _fake_to_thread(fn, **kwargs):
            raise RuntimeError("Ledger unavailable")

        with patch("host_brain.app.dependencies.get_ledger", return_value=MagicMock()), \
             patch.object(asyncio, "to_thread", side_effect=_fake_to_thread):
            result = _run(node.execute(ctx))

        assert result is True  # 降级不阻断
        assert "AUDIT_SNAPSHOT_WRITTEN" in ctx.audit_tags

    def test_audit_node_snapshot_contains_key_fields(self):
        """审计快照应包含 request_id、session_id、audit_tags、governor_passed 等关键字段。"""
        ctx = _make_ctx()
        ctx.governor_passed = True
        ctx.audit_tags.extend(["TAG_A", "TAG_B"])
        node = AuditNode()

        captured_detail = {}

        def _mock_log_error(*, request_id, stage, reason_code, detail):
            captured_detail.update(json.loads(detail))

        mock_ledger = MagicMock()
        mock_ledger.log_error.side_effect = _mock_log_error

        async def _fake_to_thread(fn, **kwargs):
            return fn(**kwargs)

        with patch("host_brain.app.dependencies.get_ledger", return_value=mock_ledger), \
             patch.object(asyncio, "to_thread", side_effect=_fake_to_thread):
            _run(node.execute(ctx))

        assert captured_detail.get("request_id") == ctx.request_id
        assert captured_detail.get("session_id") == ctx.session_id
        assert captured_detail.get("governor_passed") is True
        assert "TAG_A" in captured_detail.get("audit_tags", [])


# ---------------------------------------------------------------------------
# TODO-17：StateArbiter style/episodic 查询函数
# ---------------------------------------------------------------------------

class TestStateArbiterStyleEpisodicResolvers:
    """TODO-17：_resolve_state_arbiter_style_fact / _resolve_state_arbiter_episodic_fact。"""

    def _run(self, coro):
        return _run(coro)

    def test_resolve_style_fact_returns_evidence_item_when_available(self):
        """style_canon_facts 有值时，应返回 style_canon 证据条目。"""
        from host_brain.app.chat_stream_handler import _resolve_state_arbiter_style_fact

        mock_result = MagicMock()
        mock_result.value = "说话要温柔，语气要亲切"
        mock_result.source.value = "StyleCanonStore"
        mock_result.is_fallback = False
        mock_result.conflict_detected = False

        mock_arbiter = MagicMock()
        mock_arbiter.resolve = AsyncMock(return_value=mock_result)

        with patch("host_brain.app.dependencies.get_state_arbiter", return_value=mock_arbiter):
            summary, evidence_item = self._run(
                _resolve_state_arbiter_style_fact(session_id="sess-001", request_id="req-001")
            )

        assert summary["status"] == "APPLIED"
        assert evidence_item is not None
        assert evidence_item["slot_name"] == "style_canon"
        assert "温柔" in evidence_item["snippet"]

    def test_resolve_style_fact_degrades_when_arbiter_fails(self):
        """StateArbiter 不可用时，style_canon 查询应降级。"""
        from host_brain.app.chat_stream_handler import _resolve_state_arbiter_style_fact

        mock_arbiter = MagicMock()
        mock_arbiter.resolve = AsyncMock(side_effect=RuntimeError("arbiter down"))

        with patch("host_brain.app.dependencies.get_state_arbiter", return_value=mock_arbiter):
            summary, evidence_item = self._run(
                _resolve_state_arbiter_style_fact(session_id="sess-001", request_id="req-001")
            )

        assert summary["status"] == "DEGRADED"
        assert evidence_item is None

    def test_resolve_episodic_fact_returns_evidence_item_when_available(self):
        """episodic_latest 有值时，应返回 episodic_latest 证据条目。"""
        from host_brain.app.chat_stream_handler import _resolve_state_arbiter_episodic_fact

        mock_result = MagicMock()
        mock_result.value = "上次聊到了天气，用户喜欢晴天"
        mock_result.source.value = "EpisodicStore"
        mock_result.is_fallback = False
        mock_result.conflict_detected = False

        mock_arbiter = MagicMock()
        mock_arbiter.resolve = AsyncMock(return_value=mock_result)

        with patch("host_brain.app.dependencies.get_state_arbiter", return_value=mock_arbiter):
            summary, evidence_item = self._run(
                _resolve_state_arbiter_episodic_fact(session_id="sess-001", request_id="req-001")
            )

        assert summary["status"] == "APPLIED"
        assert evidence_item is not None
        assert evidence_item["slot_name"] == "episodic_latest"
        assert "天气" in evidence_item["snippet"]

    def test_resolve_episodic_fact_returns_none_when_no_match(self):
        """StateArbiter 返回 None 时，episodic_latest 查询应返回 NO_MATCH。"""
        from host_brain.app.chat_stream_handler import _resolve_state_arbiter_episodic_fact

        mock_arbiter = MagicMock()
        mock_arbiter.resolve = AsyncMock(return_value=None)

        with patch("host_brain.app.dependencies.get_state_arbiter", return_value=mock_arbiter):
            summary, evidence_item = self._run(
                _resolve_state_arbiter_episodic_fact(session_id="sess-001", request_id="req-001")
            )

        assert summary["reason_code"] == "NO_MATCH"
        assert evidence_item is None
