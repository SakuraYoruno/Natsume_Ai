"""M27-J：ToolBroker 情感状态传递测试（T-TOOL-EMO-01~02）"""

from __future__ import annotations

from host_brain.tools.broker import ToolBroker, ToolRuntimeBoundary, build_tool_runtime_boundary
from host_brain.tools.catalog import ToolCatalog, ToolMeta
from host_brain.governor.tokens import TokenService
from host_brain.audit.sqlite_ledger import SQLiteLedger


def _make_broker(capture_context: list) -> tuple[ToolBroker, ToolRuntimeBoundary]:
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
    ledger = SQLiteLedger(":memory:")
    token_service = TokenService()
    boundary = build_tool_runtime_boundary(
        catalog=catalog,
        token_service=token_service,
        ledger=ledger,
    )
    broker = boundary.broker
    broker.register_handler(
        "test_tool",
        handler,
        replay_safe_local=True,
        adapter_version_id="test.emotion-context.v1",
    )
    return broker, boundary


def _execute_bound(broker: ToolBroker, boundary: ToolRuntimeBoundary, **kwargs):
    task_authority = object()
    port = boundary.bind_execution_port(
        authority_verifier=lambda **values: values["task_authority"] is task_authority
    )
    return port.execute_bound(
        task_authority=task_authority,
        expected_binding=broker.resolve_execution_binding("test_tool"),
        tool_id="test_tool",
        **kwargs,
    )


class TestToolBrokerEmotionContext:
    """T-TOOL-EMO-01~02：ToolBroker 情感状态传递测试"""

    def test_execute_with_emotion_context(self):
        """T-TOOL-EMO-01：bound executor 保留显式情感上下文。"""
        captured = []
        broker, boundary = _make_broker(captured)
        result = _execute_bound(
            broker,
            boundary,
            request_id="req-001",
            intent_id="intent-001",
            session_id="sess-001",
            seq=1,
            context={"emotion_tag": "SAD", "mood_state": "tired"},
        )
        assert result.status == "SUCCESS"
        assert len(captured) == 1
        assert captured[0]["emotion_tag"] == "SAD"
        assert captured[0]["mood_state"] == "tired"

    def test_execute_without_emotion_context_uses_defaults(self):
        """T-TOOL-EMO-02：bound executor 对缺省情感字段使用稳定默认值。"""
        captured = []
        broker, boundary = _make_broker(captured)
        result = _execute_bound(
            broker,
            boundary,
            request_id="req-002",
            intent_id="intent-002",
            session_id="sess-001",
            seq=2,
            context={},
        )
        assert result.status == "SUCCESS"
        assert len(captured) == 1
        assert captured[0]["emotion_tag"] == "NEUTRAL"
        assert captured[0]["mood_state"] == "steady"
