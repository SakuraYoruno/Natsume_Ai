"""M27-J：ToolBroker 情感状态传递测试（T-TOOL-EMO-01~02）"""

from __future__ import annotations

import pytest
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog, ToolMeta
from host_brain.governor.tokens import TokenService
from host_brain.audit.sqlite_ledger import SQLiteLedger


def _make_broker(capture_context: list) -> ToolBroker:
    """创建一个捕获 context 的测试 ToolBroker"""
    def handler(args, context):
        capture_context.append(dict(context or {}))
        return {"result": "ok"}

    catalog = ToolCatalog()
    catalog.register(ToolMeta(
        id="test_tool",
        name="测试工具",
        desc="测试用工具",
        schema_hash="abc123",
        side_effect_level="READ",
        auth_level="NONE",
        domain_tags=["test"],
        version="1.0",
    ))
    ledger = SQLiteLedger()
    token_service = TokenService()
    broker = ToolBroker(catalog=catalog, token_service=token_service, ledger=ledger)
    broker.register_handler("test_tool", handler)
    return broker


class TestToolBrokerEmotionContext:
    """T-TOOL-EMO-01~02：ToolBroker 情感状态传递测试"""

    def test_execute_with_emotion_context(self):
        """T-TOOL-EMO-01：execute(context={"emotion_tag": "SAD", "mood_state": "tired"}) 正常调用不报错"""
        captured = []
        broker = _make_broker(captured)
        result = broker.execute(
            request_id="req-001",
            intent_id="intent-001",
            session_id="sess-001",
            seq=1,
            tool_id="test_tool",
            context={"emotion_tag": "SAD", "mood_state": "tired"},
        )
        assert result.status == "SUCCESS"
        assert len(captured) == 1
        assert captured[0]["emotion_tag"] == "SAD"
        assert captured[0]["mood_state"] == "tired"

    def test_execute_without_emotion_context_uses_defaults(self):
        """T-TOOL-EMO-02：context 无情感字段时，使用默认值，不报错"""
        captured = []
        broker = _make_broker(captured)
        result = broker.execute(
            request_id="req-002",
            intent_id="intent-002",
            session_id="sess-001",
            seq=2,
            tool_id="test_tool",
            context={},
        )
        assert result.status == "SUCCESS"
        assert len(captured) == 1
        assert captured[0]["emotion_tag"] == "NEUTRAL"
        assert captured[0]["mood_state"] == "steady"
