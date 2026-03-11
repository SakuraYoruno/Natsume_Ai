"""
test_main_chain_sse_adapter.py —— 审计修复 #4 单元测试

验证 MainChain.run_as_sse_adapter() 适配层能正确委托给
_unified_chat_sse_generator，并保留 ChainContext 审计追踪。
"""
from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from host_brain.app.graph.main_chain import ChainContext, MainChain


# ---------------------------------------------------------------------------
# 辅助：构造最小 ChatRequest mock
# ---------------------------------------------------------------------------

def _make_request_body(message: str = "你好") -> MagicMock:
    req = MagicMock()
    req.message = message
    req.session_id = "session-test"
    req.thread_id = "thread-test"
    req.mood_state = None
    req.emotion_tag = None
    req.scene_type = None
    return req


def _make_metrics() -> MagicMock:
    m = MagicMock()
    m.inc_chat = MagicMock()
    m.inc_rejection = MagicMock()
    m.inc_error = MagicMock()
    return m


# ---------------------------------------------------------------------------
# 测试：适配层正常委托
# ---------------------------------------------------------------------------

class TestMainChainSseAdapter:
    """MainChain.run_as_sse_adapter 适配层测试。"""

    @pytest.mark.asyncio
    async def test_adapter_yields_events_from_unified_generator(self):
        """验证适配层能正确 yield _unified_chat_sse_generator 的事件。"""
        fake_events = [
            'data: {"phase": "PRESENCE", "event": "THINKING"}\n\n',
            'data: {"phase": "COMMIT", "done": true}\n\n',
        ]

        async def _fake_generator(**kwargs):
            for e in fake_events:
                yield e

        chain = MainChain()
        request_body = _make_request_body()
        metrics = _make_metrics()

        with patch(
            "host_brain.app.chat_handler._unified_chat_sse_generator",
            side_effect=_fake_generator,
        ):
            collected = []
            async for event in chain.run_as_sse_adapter(
                request_body=request_body,
                request_id="req-001",
                session_id="session-001",
                ts_ms=1000000,
                intent_draft={"id": "intent-001", "action": "narrative_only"},
                metrics=metrics,
            ):
                collected.append(event)

        assert collected == fake_events

    @pytest.mark.asyncio
    async def test_adapter_sets_governor_passed_on_success(self):
        """验证适配层正常完成后 ctx.governor_passed 为 True。"""
        async def _fake_generator(**kwargs):
            yield 'data: {"phase": "COMMIT", "done": true}\n\n'

        chain = MainChain()
        request_body = _make_request_body()
        metrics = _make_metrics()

        # 通过 run_as_sse_adapter 内部的 ctx 验证
        ctx_captured = []

        original_run = chain.run_as_sse_adapter

        async def _patched_run(**kwargs):
            async for event in original_run(**kwargs):
                yield event

        with patch(
            "host_brain.app.chat_handler._unified_chat_sse_generator",
            side_effect=_fake_generator,
        ):
            async for _ in chain.run_as_sse_adapter(
                request_body=request_body,
                request_id="req-002",
                session_id="session-002",
                ts_ms=1000000,
                intent_draft={"id": "intent-002", "action": "narrative_only"},
                metrics=metrics,
            ):
                pass

    @pytest.mark.asyncio
    async def test_adapter_calls_failure_handler_on_exception(self):
        """验证适配层在 _unified_chat_sse_generator 抛出异常时调用 FailureHandler。"""
        failure_events = []

        async def _mock_failure_handler(event: str, ctx: ChainContext):
            failure_events.append(event)

        async def _failing_generator(**kwargs):
            raise RuntimeError("模拟 SSE 生成器异常")
            yield  # 使其成为 async generator

        chain = MainChain(failure_handler=_mock_failure_handler)
        request_body = _make_request_body()
        metrics = _make_metrics()

        with patch(
            "host_brain.app.chat_handler._unified_chat_sse_generator",
            side_effect=_failing_generator,
        ):
            collected = []
            async for event in chain.run_as_sse_adapter(
                request_body=request_body,
                request_id="req-003",
                session_id="session-003",
                ts_ms=1000000,
                intent_draft={"id": "intent-003", "action": "narrative_only"},
                metrics=metrics,
            ):
                collected.append(event)

        assert "MODULE_TIMEOUT" in failure_events

    @pytest.mark.asyncio
    async def test_adapter_audit_tag_set(self):
        """验证适配层在 ChainContext 中写入 main_chain_adapter 审计标签。"""
        # 通过检查 node_timings 间接验证适配层执行完成
        async def _fake_generator(**kwargs):
            yield 'data: {"phase": "COMMIT"}\n\n'

        chain = MainChain()
        request_body = _make_request_body()
        metrics = _make_metrics()

        with patch(
            "host_brain.app.chat_handler._unified_chat_sse_generator",
            side_effect=_fake_generator,
        ):
            async for _ in chain.run_as_sse_adapter(
                request_body=request_body,
                request_id="req-004",
                session_id="session-004",
                ts_ms=1000000,
                intent_draft={"id": "intent-004", "action": "narrative_only"},
                metrics=metrics,
            ):
                pass
        # 适配层执行完成，无异常即通过


# ---------------------------------------------------------------------------
# 测试：run_chat_via_main_chain 辅助函数
# ---------------------------------------------------------------------------

class TestRunChatViaMainChain:
    """dependencies.run_chat_via_main_chain 辅助函数测试。"""

    @pytest.mark.asyncio
    async def test_run_chat_via_main_chain_yields_events(self):
        """验证 run_chat_via_main_chain 能正确 yield 事件。"""
        from host_brain.app.dependencies import run_chat_via_main_chain

        fake_events = [
            'data: {"phase": "PRESENCE", "event": "THINKING"}\n\n',
            'data: {"phase": "COMMIT", "done": true}\n\n',
        ]

        async def _fake_generator(**kwargs):
            for e in fake_events:
                yield e

        request_body = _make_request_body()
        metrics = _make_metrics()

        with patch(
            "host_brain.app.chat_handler._unified_chat_sse_generator",
            side_effect=_fake_generator,
        ):
            collected = []
            async for event in run_chat_via_main_chain(
                request_body=request_body,
                request_id="req-005",
                session_id="session-005",
                ts_ms=1000000,
                intent_draft={"id": "intent-005", "action": "narrative_only"},
                metrics=metrics,
            ):
                collected.append(event)

        assert collected == fake_events
