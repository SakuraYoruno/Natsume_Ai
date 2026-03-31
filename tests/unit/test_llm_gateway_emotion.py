"""M28-Q：LlmGatewayContract 情感上下文测试（T-LLM-GW-EMO-01~02）"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


class TestLlmGatewayEmotion:
    """T-LLM-GW-EMO-01~02：LlmGatewayContract 情感上下文测试"""

    def _make_gateway(self, captured_payload: dict):
        """创建带 mock provider 的 LlmGatewayContract"""
        from host_brain.integrations.llm_gateway import LlmGatewayContract

        mock_egress = MagicMock()
        mock_egress.allowed = True
        mock_egress.reason_code = None
        mock_egress.detail = "ok"
        mock_egress.payload = {"task": "chat"}

        def mock_egress_gate(prompt_payload, **kwargs):
            # 将 prompt_payload 合并到 egress.payload 中，以便测试验证
            mock_egress.payload = dict(prompt_payload)
            # 同时记录到 captured_payload 供测试断言
            captured_payload.update(prompt_payload)
            return mock_egress

        def mock_provider(prompt_payload, **kwargs):
            return MagicMock(
                success=True,
                status="OK",
                reason_code=None,
                output={"payload": "mock response"},
            )

        return LlmGatewayContract(
            egress_gate_fn=mock_egress_gate,
            provider=mock_provider,
        )

    def test_emotion_context_injected_into_prompt_payload(self):
        """T-LLM-GW-EMO-01：generate() 的 prompt_payload 含 emotion_context 字段"""
        captured = {}
        gateway = self._make_gateway(captured)
        gateway.generate(
            request_id="req-001",
            model_profile_id="default",
            prompt_payload={"task": "chat"},
            emotion_context={
                "mood_state": "engaged",
                "emotion_tag": "HAPPY",
                "emotion_range_hint": "可以表达积极情感",
            },
        )
        assert "emotion_context" in captured
        assert captured["emotion_context"]["emotion_tag"] == "HAPPY"
        assert captured["emotion_context"]["mood_state"] == "engaged"

    def test_no_emotion_context_no_error(self):
        """T-LLM-GW-EMO-02：emotion_context 缺失时使用默认值，不报错"""
        captured = {}
        gateway = self._make_gateway(captured)
        result = gateway.generate(
            request_id="req-001",
            model_profile_id="default",
            prompt_payload={"task": "chat"},
        )
        # 不传 emotion_context 时，prompt_payload 中不含 emotion_context 字段
        assert "emotion_context" not in captured
        assert result is not None
