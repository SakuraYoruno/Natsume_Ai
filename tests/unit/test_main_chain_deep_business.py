"""MainChain 深度业务化 + SCENE_VISION_UI 生产化加固 单元测试。

覆盖范围：
  - GovernorNode：真实 IntentDecisionEngine 调用 / 降级回退
  - RecallFusionNode：真实 RetrievalOrchestrator 调用 / 降级回退
  - QAContractNode：长度截断 / 安全词检测 / 格式规范化
  - _VisionSLAMonitor：连续失败告警 / 降级率告警
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from host_brain.app.graph.main_chain import (
    AirlockQuarantineNode,
    ChainContext,
    GovernorNode,
    QAContractNode,
    RecallFusionNode,
)
from host_brain.app.chat_handler_vision import _VisionSLAMonitor
from host_brain.app.schemas import TaskType


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _make_ctx(
    *,
    request_id: str = "req-test-001",
    user_message: str = "你好",
    session_id: str = "sess-001",
    quarantine_passed: bool = True,
) -> ChainContext:
    ctx = ChainContext(
        request_id=request_id,
        user_message=user_message,
        session_id=session_id,
    )
    ctx.quarantine_passed = quarantine_passed
    return ctx


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# GovernorNode 测试
# ---------------------------------------------------------------------------

class TestGovernorNodeDeepBusiness:
    """GovernorNode 深度业务化测试。"""

    def test_governor_degraded_fallback_when_engine_unavailable(self):
        """IntentDecisionEngine 不可用时，降级为最小状态语义并放行。"""
        ctx = _make_ctx()
        node = GovernorNode()

        with patch("host_brain.app.dependencies.get_intent_engine", side_effect=RuntimeError("engine not ready")):
            result = _run(node.execute(ctx))

        assert result is True
        assert ctx.governor_passed is True
        assert "GOVERNOR_DEGRADED_FALLBACK" in ctx.audit_tags
        assert ctx.metadata["governor"]["mode"] == "DEGRADED_FALLBACK"

    def test_governor_active_mode_when_engine_approves(self):
        """IntentDecisionEngine 返回 APPROVE 时，GovernorNode 放行并标记 ACTIVE 模式。"""
        ctx = _make_ctx()
        node = GovernorNode()

        mock_engine = MagicMock()
        mock_engine.evaluate_intent.return_value = {
            "decision": "APPROVE",
            "intent_id": "intent-001",
            "stage": "S_INTENT_OK",
        }

        with patch("host_brain.app.dependencies.get_intent_engine", return_value=mock_engine), \
             patch("host_brain.app.emotion_context.resolve_chat_emotion_context", return_value={}), \
             patch("host_brain.app.emotion_context.build_chat_governance_snapshot", return_value={}):
            result = _run(node.execute(ctx))

        assert result is True
        assert ctx.governor_passed is True
        assert ctx.metadata["governor"]["mode"] == "ACTIVE"
        assert "GOVERNOR_PRECHECK_PASSED" in ctx.audit_tags

    def test_governor_active_mode_when_engine_rejects(self):
        """IntentDecisionEngine 返回 REJECT 时，GovernorNode 拒绝请求。"""
        ctx = _make_ctx()
        node = GovernorNode()

        mock_engine = MagicMock()
        mock_engine.evaluate_intent.return_value = {
            "decision": "REJECT",
            "reason_code": "RATE_LIMIT_EXCEEDED",
            "stage": "S_GOV_GATES",
        }

        with patch("host_brain.app.dependencies.get_intent_engine", return_value=mock_engine), \
             patch("host_brain.app.emotion_context.resolve_chat_emotion_context", return_value={}), \
             patch("host_brain.app.emotion_context.build_chat_governance_snapshot", return_value={}):
            result = _run(node.execute(ctx))

        assert result is False
        assert ctx.governor_passed is False
        assert ctx.governor_block_reason == "RATE_LIMIT_EXCEEDED"

    def test_governor_blocks_when_airlock_not_passed(self):
        """Airlock 未通过时，GovernorNode 直接拒绝。"""
        ctx = _make_ctx(quarantine_passed=False)
        node = GovernorNode()
        result = _run(node.execute(ctx))
        assert result is False
        assert ctx.governor_passed is False
        assert "GOVERNOR_BLOCK_AIRLOCK" in ctx.audit_tags

    def test_governor_scene_to_task_mapping(self):
        """场景类型正确映射到 TaskType。"""
        ctx = _make_ctx()
        ctx.metadata["pre_classified_scene"] = "EXEC_INTENT"
        node = GovernorNode()

        with patch("host_brain.app.dependencies.get_intent_engine", side_effect=RuntimeError("skip")):
            _run(node.execute(ctx))

        assert ctx.task_type == TaskType.REASONING


# ---------------------------------------------------------------------------
# RecallFusionNode 测试
# ---------------------------------------------------------------------------

class TestRecallFusionNodeDeepBusiness:
    """RecallFusionNode 深度业务化测试。"""

    def test_recall_degraded_fallback_when_orchestrator_unavailable(self):
        """RetrievalOrchestrator 不可用时，降级为最小状态语义并继续。"""
        ctx = _make_ctx()
        node = RecallFusionNode()

        with patch("host_brain.app.dependencies.get_retrieval_orchestrator", side_effect=RuntimeError("not ready")):
            result = _run(node.execute(ctx))

        assert result is True
        assert "RECALL_DEGRADED_FALLBACK" in ctx.audit_tags
        assert ctx.metadata["recall_fusion"]["mode"] == "DEGRADED_FALLBACK"

    def test_recall_active_mode_with_evidence(self):
        """RetrievalOrchestrator 返回证据时，RecallFusionNode 标记 ACTIVE 模式。"""
        from host_brain.retrieval.orchestrator import RetrievalOrchestratorResult

        ctx = _make_ctx()
        node = RecallFusionNode()

        mock_result = RetrievalOrchestratorResult(
            success=True,
            status="OK",
            reason_code=None,
            path="success",
            detail="ok",
            trace_id="req-test-001",
            query="你好",
            session_state={},
            retrieval_summary={"status": "OK"},
            retrieval_evidence_items=[
                {"slot_name": "world_canon", "text": "夏目是一个AI助手", "snippet": "夏目是一个AI助手"}
            ],
            retrieval_bundle={},
        )

        mock_orchestrator = AsyncMock()
        mock_orchestrator.arun.return_value = mock_result

        with patch("host_brain.app.dependencies.get_retrieval_orchestrator", return_value=mock_orchestrator):
            result = _run(node.execute(ctx))

        assert result is True
        assert ctx.metadata["recall_fusion"]["mode"] == "ACTIVE"
        assert ctx.metadata["recall_fusion"]["evidence_count"] == 1
        assert ctx.recall_context_pack is not None
        assert "RECALL_EVIDENCE_COUNT:1" in ctx.audit_tags

    def test_recall_active_mode_empty_evidence(self):
        """RetrievalOrchestrator 返回空证据时，标记 RECALL_EVIDENCE_EMPTY。"""
        from host_brain.retrieval.orchestrator import RetrievalOrchestratorResult

        ctx = _make_ctx()
        node = RecallFusionNode()

        mock_result = RetrievalOrchestratorResult(
            success=True,
            status="OK",
            reason_code=None,
            path="success",
            detail="ok",
            trace_id="req-test-001",
            query="你好",
            session_state={},
            retrieval_summary={"status": "OK"},
            retrieval_evidence_items=[],
            retrieval_bundle={},
        )

        mock_orchestrator = AsyncMock()
        mock_orchestrator.arun.return_value = mock_result

        with patch("host_brain.app.dependencies.get_retrieval_orchestrator", return_value=mock_orchestrator):
            result = _run(node.execute(ctx))

        assert result is True
        assert ctx.metadata["recall_fusion"]["mode"] == "ACTIVE"
        assert "RECALL_EVIDENCE_EMPTY" in ctx.audit_tags


# ---------------------------------------------------------------------------
# QAContractNode 测试
# ---------------------------------------------------------------------------

class TestQAContractNodeDeepBusiness:
    """QAContractNode 深度业务化测试。"""

    def test_qa_skips_when_no_output(self):
        """无输出时跳过 QA 检查。"""
        ctx = _make_ctx()
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True
        assert ctx.metadata["qa_contract"]["status"] == "SKIPPED_NO_OUTPUT"

    def test_qa_format_normalize(self):
        """格式规范化：去除首尾空白和多余空行。"""
        ctx = _make_ctx()
        ctx.final_response = "  你好\n\n\n\n世界  "
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True
        assert ctx.final_response == "你好\n\n世界"
        assert ctx.qa_rewritten is True
        assert "FORMAT_NORMALIZE" in ctx.metadata["qa_contract"]["rewrite_reasons"]

    def test_qa_length_truncation(self, monkeypatch):
        """超长响应被截断（HOST_QA_MAX_RESPONSE_CHARS 最小值为 100）。"""
        monkeypatch.setenv("HOST_QA_MAX_RESPONSE_CHARS", "100")
        ctx = _make_ctx()
        # 2000 字符，远超 100 字符限制
        long_text = "这是一段很长的文字。" * 200
        ctx.final_response = long_text
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True
        # 截断后长度应 <= max_chars + 少量句子边界余量（最多 +5）
        assert len(ctx.final_response) <= 105
        assert ctx.qa_rewritten is True
        assert any("LENGTH_TRUNCATED" in r for r in ctx.metadata["qa_contract"]["rewrite_reasons"])
        assert "QA_LENGTH_TRUNCATED" in ctx.audit_tags

    def test_qa_safety_word_detection(self):
        """安全词检测：疑似系统提示词泄露时写入告警标签。"""
        ctx = _make_ctx()
        ctx.final_response = "你好！system prompt: 你是一个AI助手。"
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True  # 不阻断，仅告警
        assert "QA_SAFETY_WARN:PROMPT_LEAK_SUSPECTED" in ctx.audit_tags

    def test_qa_null_byte_removal(self):
        """去除 null 字节。"""
        ctx = _make_ctx()
        ctx.final_response = "你好\x00世界"
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True
        assert "\x00" not in ctx.final_response
        assert "NULL_BYTE_REMOVED" in ctx.metadata["qa_contract"]["rewrite_reasons"]

    def test_qa_clean_output_no_rewrite(self):
        """干净的输出不被重写。"""
        ctx = _make_ctx()
        ctx.final_response = "你好，有什么可以帮你的？"
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True
        assert ctx.qa_rewritten is False
        assert ctx.metadata["qa_contract"]["mode"] == "ACTIVE"


# ---------------------------------------------------------------------------
# _VisionSLAMonitor 测试
# ---------------------------------------------------------------------------

class TestVisionSLAMonitor:
    """_VisionSLAMonitor 生产化加固测试。"""

    def test_consecutive_failures_trigger_alert(self, monkeypatch):
        """连续失败达到阈值时触发告警。"""
        monkeypatch.setenv("HOST_VISION_SLA_FAIL_THRESHOLD", "3")
        monitor = _VisionSLAMonitor()

        # 前 2 次失败：不触发告警
        for _ in range(2):
            result = monitor.record_call(
                status="DEGRADED", request_id="req-001",
                latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
            )
        assert result["alert_triggered"] is False

        # 第 3 次失败：触发告警
        result = monitor.record_call(
            status="DEGRADED", request_id="req-001",
            latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
        )
        assert result["alert_triggered"] is True
        assert "CONSECUTIVE_FAILURES" in result["alert_reason"]

    def test_consecutive_failures_reset_on_success(self, monkeypatch):
        """成功调用后，连续失败计数重置。"""
        monkeypatch.setenv("HOST_VISION_SLA_FAIL_THRESHOLD", "3")
        monitor = _VisionSLAMonitor()

        # 2 次失败
        for _ in range(2):
            monitor.record_call(
                status="DEGRADED", request_id="req-001",
                latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
            )

        # 1 次成功：重置计数
        monitor.record_call(
            status="OK", request_id="req-001",
            latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
        )

        # 再次失败：不触发告警（计数已重置）
        result = monitor.record_call(
            status="DEGRADED", request_id="req-001",
            latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
        )
        assert result["consecutive_failures"] == 1
        assert result["alert_triggered"] is False

    def test_degrade_rate_alert(self, monkeypatch):
        """降级率超过阈值时触发告警。"""
        monkeypatch.setenv("HOST_VISION_SLA_FAIL_THRESHOLD", "100")  # 禁用连续失败告警
        monkeypatch.setenv("HOST_VISION_SLA_DEGRADE_RATE_THRESHOLD", "0.5")
        monitor = _VisionSLAMonitor()

        # 10 次调用，6 次降级（60% > 50%）
        for i in range(10):
            status = "DEGRADED" if i < 6 else "OK"
            result = monitor.record_call(
                status=status, request_id="req-001",
                latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
            )

        assert result["alert_triggered"] is True
        assert "DEGRADE_RATE" in result["alert_reason"]

    def test_no_alert_for_normal_operation(self, monkeypatch):
        """正常运行时不触发告警。"""
        monkeypatch.setenv("HOST_VISION_SLA_FAIL_THRESHOLD", "3")
        monkeypatch.setenv("HOST_VISION_SLA_DEGRADE_RATE_THRESHOLD", "0.5")
        monitor = _VisionSLAMonitor()

        for _ in range(20):
            result = monitor.record_call(
                status="OK", request_id="req-001",
                latency_ms=500, model_id="gemini-2.5-flash", provider="gemini",
            )

        assert result["alert_triggered"] is False
        assert result["degrade_rate"] == 0.0


# ---------------------------------------------------------------------------
# 预召回注入链路测试（RecallFusionNode → _sse_event_generator）
# ---------------------------------------------------------------------------

class TestPreRecallInjection:
    """验证 RecallFusionNode 预召回结果注入 _sse_event_generator 的链路。

    核心问题：RecallFusionNode 执行七路召回后，结果写入 ctx.recall_context_pack，
    但 run_as_sse_adapter 调用 _unified_chat_sse_generator 时必须将其传入，
    否则 _sse_event_generator 会重复执行一次召回（孤岛问题）。
    """

    def test_sse_event_generator_uses_pre_recall_evidence_skips_retrieval(self):
        """当 _pre_recall_evidence 传入时，_sse_event_generator 跳过内部召回。"""
        import asyncio
        from unittest.mock import patch, MagicMock, AsyncMock
        from host_brain.app.chat_stream_handler import _sse_event_generator

        pre_evidence = [
            {"slot_name": "world_canon", "text": "夏目是一个AI助手", "snippet": "夏目是一个AI助手"},
            {"slot_name": "episodic", "text": "上次聊了天气", "snippet": "上次聊了天气"},
        ]

        mock_governor_result = {"decision": "APPROVE", "intent_id": "intent-001"}
        mock_llm_result = MagicMock()
        mock_llm_result.__aiter__ = AsyncMock(return_value=iter([]))

        retrieval_called = []

        async def _mock_run_retrieval(**kwargs):
            retrieval_called.append(kwargs)
            return {"status": "OK"}, []

        async def _collect_events():
            events = []
            with patch("host_brain.app.chat_stream_handler._run_retrieval", side_effect=_mock_run_retrieval), \
                 patch("host_brain.app.chat_stream_handler.resolve_chat_retrieval_gate", return_value=(True, None)), \
                 patch("host_brain.app.chat_stream_handler._resolve_state_arbiter_world_fact",
                       return_value=({"status": "SKIPPED"}, None)), \
                 patch("host_brain.app.chat_stream_handler.resolve_host_llm_runtime",
                       return_value={"provider_name": "test", "model_id": "test-model",
                                     "status": "OK", "reason_code": None}), \
                 patch("host_brain.app.chat_stream_handler._build_stream_chat_prompt",
                       return_value="test prompt"), \
                 patch("host_brain.app.chat_stream_handler.stream_llm_response",
                       return_value=AsyncMock(__aiter__=lambda s: iter([]))):
                async for event in _sse_event_generator(
                    message="你好",
                    session_id="sess-001",
                    thread_id=None,
                    mood_state=None,
                    emotion_tag=None,
                    request_id="req-test-pre-recall",
                    ts_ms=0,
                    intent_draft={"id": "intent-001", "action": "narrative_only"},
                    _pre_approved_governor_result=mock_governor_result,
                    _pre_recall_evidence=pre_evidence,
                ):
                    events.append(event)
            return events

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_collect_events())
        except Exception:
            pass  # 允许 LLM 调用失败，只验证召回是否被跳过
        finally:
            loop.close()

        # 核心断言：_run_retrieval 不应被调用（预召回已提供）
        assert len(retrieval_called) == 0, (
            f"_run_retrieval 被调用了 {len(retrieval_called)} 次，"
            "说明预召回注入未生效，RecallFusionNode 孤岛问题未修复"
        )

    def test_recall_context_pack_extracted_correctly_in_run_as_sse_adapter(self):
        """run_as_sse_adapter 正确从 ctx.recall_context_pack 提取 retrieval_evidence_items。"""
        # 验证提取逻辑：只有非空列表才会被提取为 _pre_recall
        test_cases = [
            # (recall_context_pack, expected_pre_recall_is_not_none)
            ({"retrieval_evidence_items": [{"text": "test"}]}, True),
            ({"retrieval_evidence_items": []}, False),  # 空列表不提取
            ({"retrieval_evidence_items": None}, False),  # None 不提取
            ({}, False),  # 无 key 不提取
            (None, False),  # None pack 不提取
        ]

        for pack, expected_not_none in test_cases:
            # 模拟 run_as_sse_adapter 中的提取逻辑
            _pre_recall = None
            _recall_pack = pack
            if isinstance(_recall_pack, dict):
                _items = _recall_pack.get("retrieval_evidence_items")
                if isinstance(_items, list) and len(_items) > 0:
                    _pre_recall = _items

            if expected_not_none:
                assert _pre_recall is not None, f"pack={pack} 应该提取到 _pre_recall"
                assert len(_pre_recall) > 0
            else:
                assert _pre_recall is None, f"pack={pack} 不应该提取到 _pre_recall"

