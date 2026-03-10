"""
test_failure_handler.py —— FailureHandler 单元测试（需求 6.1-6.7）
"""
from __future__ import annotations

import pytest

from host_brain.app.failure_matrix import (
    FailureEvent,
    FailureHandler,
    ABORT_FLAGS,
    DEGRADED_MODES,
)


class TestFailureHandlerEvents:
    """各失败事件处理测试。"""

    @pytest.mark.asyncio
    async def test_governor_block_aborts_chain(self):
        """验证 GOVERNOR_BLOCK 中止链路（需求 6.1）。"""
        handler = FailureHandler()
        result = await handler.handle(FailureEvent.GOVERNOR_BLOCK.value, {})
        assert result.should_abort is True
        assert result.audit_tag == "GOVERNOR_BLOCK"
        # 不暴露内部规则细节
        assert "R" not in result.user_response or "规则" not in result.user_response

    @pytest.mark.asyncio
    async def test_evidence_empty_marks_meta(self):
        """验证 EVIDENCE_EMPTY 标注 evidence_empty=True（需求 6.2）。"""
        handler = FailureHandler()
        result = await handler.handle(FailureEvent.EVIDENCE_EMPTY.value, {})
        assert result.meta.get("evidence_empty") is True
        assert result.should_abort is False  # 可以继续，使用保守回答

    @pytest.mark.asyncio
    async def test_model_fallback_marks_meta(self):
        """验证 MODEL_FALLBACK 标注 model_fallback=True（需求 6.3）。"""
        handler = FailureHandler()
        result = await handler.handle(
            FailureEvent.MODEL_FALLBACK.value,
            {},
            {"fallback_reason": "主模型超时"},
        )
        assert result.meta.get("model_fallback") is True
        assert result.should_abort is False

    @pytest.mark.asyncio
    async def test_qa_rewrite_preserves_both_outputs(self):
        """验证 QA_REWRITE 同时保留原始和重写输出（需求 6.4）。"""
        handler = FailureHandler()
        result = await handler.handle(
            FailureEvent.QA_REWRITE.value,
            {},
            {
                "original_output": "原始违规内容",
                "rewritten_output": "重写后的安全内容",
            },
        )
        assert result.meta.get("original_output") == "原始违规内容"
        assert result.meta.get("rewritten_output") == "重写后的安全内容"
        assert result.should_abort is False

    @pytest.mark.asyncio
    async def test_edge_unreachable_disables_tts(self):
        """验证 EDGE_UNREACHABLE 禁用 TTS/VTS（需求 6.5）。"""
        tts_disabled = []

        async def mock_tts_disabler():
            tts_disabled.append(True)

        handler = FailureHandler(tts_disabler=mock_tts_disabler)
        result = await handler.handle(FailureEvent.EDGE_UNREACHABLE.value, {})

        assert len(tts_disabled) == 1
        assert result.meta.get("tts_disabled") is True
        assert result.should_abort is False
        assert "语音" in result.user_response

    @pytest.mark.asyncio
    async def test_vision_low_confidence_aborts(self):
        """验证 VISION_LOW_CONFIDENCE 中止操作（需求 6.6）。"""
        handler = FailureHandler()
        result = await handler.handle(
            FailureEvent.VISION_LOW_CONFIDENCE.value,
            {},
            {"confidence": 0.65, "threshold": 0.80},
        )
        assert result.should_abort is True
        assert result.meta.get("confidence") == 0.65

    @pytest.mark.asyncio
    async def test_module_timeout_aborts(self):
        """验证 MODULE_TIMEOUT 中止链路（需求 6.7）。"""
        handler = FailureHandler()
        result = await handler.handle(
            FailureEvent.MODULE_TIMEOUT.value,
            {},
            {"module": "recall_fusion", "timeout": 5.0},
        )
        assert result.should_abort is True
        assert result.meta.get("timed_out_module") == "recall_fusion"

    @pytest.mark.asyncio
    async def test_audit_writer_called(self):
        """验证审计写入回调被调用。"""
        audit_calls = []

        async def mock_audit(tag, detail):
            audit_calls.append((tag, detail))

        handler = FailureHandler(audit_writer=mock_audit)
        await handler.handle(FailureEvent.GOVERNOR_BLOCK.value, {})

        assert len(audit_calls) == 1
        assert audit_calls[0][0] == "GOVERNOR_BLOCK"

    @pytest.mark.asyncio
    async def test_unknown_event_falls_back_to_timeout(self):
        """验证未知事件类型使用 MODULE_TIMEOUT 兜底。"""
        handler = FailureHandler()
        result = await handler.handle("UNKNOWN_EVENT_XYZ", {})
        assert result.event == FailureEvent.MODULE_TIMEOUT


class TestAbortFlags:
    """中止标志测试。"""

    def test_abort_flags_defined_for_all_events(self):
        """验证所有事件都有中止标志定义。"""
        for event in FailureEvent:
            assert event in ABORT_FLAGS, f"事件 {event} 缺少中止标志定义"

    def test_degraded_modes_defined_for_all_events(self):
        """验证所有事件都有降级模式定义。"""
        for event in FailureEvent:
            assert event in DEGRADED_MODES, f"事件 {event} 缺少降级模式定义"
