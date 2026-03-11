"""
test_main_chain_sse_adapter.py —— MainChain SSE 适配层测试（同步入口）

说明：
- 当前测试环境未安装 pytest-asyncio，因此所有测试使用 asyncio.run 驱动异步代码。
- 覆盖点聚焦 TODO-6：
  1. 适配层透传 unified SSE 事件
  2. 适配层执行前后置节点编排
  3. 委托异常时触发 FailureHandler 并补发 REJECT COMMIT
  4. 前置节点失败时短路 unified 生成器
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

from host_brain.app.graph.main_chain import ChainContext, ChainNode, MainChain


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


async def _collect_events(async_iterable) -> list[str]:
    result: list[str] = []
    async for item in async_iterable:
        result.append(item)
    return result


class _TrackingNode(ChainNode):
    def __init__(self, name: str, executed: list[str], *, should_continue: bool = True):
        self.name = name
        self.timeout = 3.0
        self._executed = executed
        self._should_continue = should_continue

    async def execute(self, ctx: ChainContext) -> bool:
        self._executed.append(self.name)
        if self.name == "governor":
            ctx.governor_passed = True
        if not self._should_continue:
            ctx.failure_event = "MODULE_TIMEOUT"
            ctx.failure_detail = f"节点 {self.name} 模拟失败"
            return False
        return True


class TestMainChainSseAdapter:
    def test_adapter_yields_events_from_unified_generator(self):
        fake_events = [
            'data: {"phase": "PRESENCE", "event": "THINKING"}\n\n',
            'data: {"phase": "COMMIT", "done": true, "decision": "APPROVE"}\n\n',
        ]

        async def _fake_generator(**kwargs):
            for event in fake_events:
                yield event

        chain = MainChain()
        request_body = _make_request_body()
        metrics = _make_metrics()

        with patch(
            "host_brain.app.chat_handler._unified_chat_sse_generator",
            side_effect=_fake_generator,
        ):
            collected = asyncio.run(_collect_events(chain.run_as_sse_adapter(
                request_body=request_body,
                request_id="req-001",
                session_id="session-001",
                ts_ms=1000,
                intent_draft={"id": "intent-001", "action": "narrative_only"},
                metrics=metrics,
            )))

        assert collected == fake_events

    def test_adapter_executes_pre_and_post_nodes(self):
        fake_events = [
            'data: {"phase": "PRESENCE", "event": "THINKING"}\n\n',
            'data: {"phase": "COMMIT", "done": true, "decision": "APPROVE", "narrative": "ok"}\n\n',
        ]

        async def _fake_generator(**kwargs):
            for event in fake_events:
                yield event

        executed: list[str] = []
        chain = MainChain()
        chain.MAIN_NODES = [
            _TrackingNode("airlock_quarantine", executed),
            _TrackingNode("governor", executed),
            _TrackingNode("graph_executor", executed),
            _TrackingNode("recall_fusion", executed),
            _TrackingNode("llm_gateway", executed),
            _TrackingNode("qa_contract", executed),
            _TrackingNode("episodic_store", executed),
            _TrackingNode("audit", executed),
            _TrackingNode("airlock_sanitizer", executed),
        ]
        request_body = _make_request_body()
        metrics = _make_metrics()

        with patch(
            "host_brain.app.chat_handler._unified_chat_sse_generator",
            side_effect=_fake_generator,
        ):
            collected = asyncio.run(_collect_events(chain.run_as_sse_adapter(
                request_body=request_body,
                request_id="req-002",
                session_id="session-002",
                ts_ms=2000,
                intent_draft={"id": "intent-002", "action": "narrative_only"},
                metrics=metrics,
            )))

        assert collected == fake_events
        assert executed == [
            "airlock_quarantine",
            "governor",
            "graph_executor",
            "recall_fusion",
            "llm_gateway",
            "qa_contract",
            "episodic_store",
            "audit",
            "airlock_sanitizer",
        ]

    def test_adapter_calls_failure_handler_and_emits_reject_on_delegate_error(self):
        failure_events: list[str] = []

        async def _failure_handler(event: str, ctx: ChainContext):
            failure_events.append(event)

        async def _failing_generator(**kwargs):
            raise RuntimeError("模拟 unified SSE 异常")
            yield

        chain = MainChain(failure_handler=_failure_handler)
        request_body = _make_request_body()
        metrics = _make_metrics()

        with patch(
            "host_brain.app.chat_handler._unified_chat_sse_generator",
            side_effect=_failing_generator,
        ):
            collected = asyncio.run(_collect_events(chain.run_as_sse_adapter(
                request_body=request_body,
                request_id="req-003",
                session_id="session-003",
                ts_ms=3000,
                intent_draft={"id": "intent-003", "action": "narrative_only"},
                metrics=metrics,
            )))

        assert "MODULE_TIMEOUT" in failure_events
        assert len(collected) == 1
        payload = json.loads(collected[0].removeprefix("data: ").strip())
        assert payload.get("phase") == "COMMIT"
        assert payload.get("decision") == "REJECT"
        assert payload.get("reason_code") == "INTERNAL_SERVER_ERROR"

    def test_adapter_precheck_failure_short_circuits_unified_generator(self):
        failure_events: list[str] = []
        delegate_called = {"value": False}

        async def _failure_handler(event: str, ctx: ChainContext):
            failure_events.append(event)

        async def _should_not_run(**kwargs):
            delegate_called["value"] = True
            yield 'data: {"phase":"COMMIT","done":true}\n\n'

        executed: list[str] = []
        chain = MainChain(failure_handler=_failure_handler)
        chain.MAIN_NODES = [
            _TrackingNode("airlock_quarantine", executed, should_continue=False),
            _TrackingNode("governor", executed),
            _TrackingNode("graph_executor", executed),
            _TrackingNode("recall_fusion", executed),
            _TrackingNode("llm_gateway", executed),
            _TrackingNode("qa_contract", executed),
            _TrackingNode("episodic_store", executed),
            _TrackingNode("audit", executed),
            _TrackingNode("airlock_sanitizer", executed),
        ]
        request_body = _make_request_body(message="  ")
        metrics = _make_metrics()

        with patch(
            "host_brain.app.chat_handler._unified_chat_sse_generator",
            side_effect=_should_not_run,
        ):
            collected = asyncio.run(_collect_events(chain.run_as_sse_adapter(
                request_body=request_body,
                request_id="req-004",
                session_id="session-004",
                ts_ms=4000,
                intent_draft={"id": "intent-004", "action": "narrative_only"},
                metrics=metrics,
            )))

        assert delegate_called["value"] is False
        assert executed == ["airlock_quarantine"]
        assert "MODULE_TIMEOUT" in failure_events
        payload = json.loads(collected[0].removeprefix("data: ").strip())
        assert payload.get("reason_code") == "MAIN_CHAIN_PRECHECK_FAILED"


class TestRunChatViaMainChain:
    def test_run_chat_via_main_chain_yields_events(self):
        from host_brain.app.dependencies import run_chat_via_main_chain

        fake_events = [
            'data: {"phase": "PRESENCE", "event": "THINKING"}\n\n',
            'data: {"phase": "COMMIT", "done": true, "decision": "APPROVE"}\n\n',
        ]

        async def _fake_generator(**kwargs):
            for event in fake_events:
                yield event

        request_body = _make_request_body()
        metrics = _make_metrics()

        with patch(
            "host_brain.app.chat_handler._unified_chat_sse_generator",
            side_effect=_fake_generator,
        ):
            collected = asyncio.run(_collect_events(run_chat_via_main_chain(
                request_body=request_body,
                request_id="req-005",
                session_id="session-005",
                ts_ms=5000,
                intent_draft={"id": "intent-005", "action": "narrative_only"},
                metrics=metrics,
            )))

        assert collected == fake_events
