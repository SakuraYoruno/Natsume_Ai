"""M28-R：GraphExecutor 节点执行携带情感状态测试（T-GRAPH-EMO-01~02）"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from host_brain.integrations.graph_executor import GraphExecutor
from host_brain.integrations.graph_contract import STATUS_OK


class TestGraphExecutorEmotion:
    """T-GRAPH-EMO-01~02：GraphExecutor 节点执行携带情感状态测试"""

    def _make_executor(self, captured_context: dict) -> GraphExecutor:
        """创建带 mock handler 的 GraphExecutor"""
        def mock_handler(payload, context):
            captured_context.update(context)
            return {"result": "ok"}

        return GraphExecutor(
            handlers={"test_handler": mock_handler},
            gateway_factory=lambda **kwargs: None,
        )

    def test_handler_receives_emotion_tag(self):
        """T-GRAPH-EMO-01：handler 接收到的 context 含 emotion_tag 字段"""
        captured = {}
        executor = _make_executor_with_capture(captured)
        state = {
            "request_id": "req-001",
            "graph_id": "graph-001",
            "emotion_context": {"emotion_tag": "HAPPY", "mood_state": "engaged"},
            "payload": {},
        }
        executor._invoke_handler(handler_id="test_handler", node_id="node1", state=state)
        assert "emotion_tag" in captured
        assert captured["emotion_tag"] == "HAPPY"

    def test_handler_receives_mood_state(self):
        """T-GRAPH-EMO-01b：handler 接收到的 context 含 mood_state 字段"""
        captured = {}
        executor = _make_executor_with_capture(captured)
        state = {
            "request_id": "req-001",
            "graph_id": "graph-001",
            "emotion_context": {"emotion_tag": "SAD", "mood_state": "tired"},
            "payload": {},
        }
        executor._invoke_handler(handler_id="test_handler", node_id="node1", state=state)
        assert captured["mood_state"] == "tired"

    def test_no_emotion_context_uses_defaults(self):
        """T-GRAPH-EMO-02：context 无情感字段时使用默认值，不报错"""
        captured = {}
        executor = _make_executor_with_capture(captured)
        state = {
            "request_id": "req-001",
            "graph_id": "graph-001",
            "payload": {},
        }
        executor._invoke_handler(handler_id="test_handler", node_id="node1", state=state)
        assert captured.get("emotion_tag") == "NEUTRAL"
        assert captured.get("mood_state") == "steady"


def _make_executor_with_capture(captured: dict) -> GraphExecutor:
    """创建带 mock handler 的 GraphExecutor"""
    def mock_handler(payload, context):
        captured.update(context)
        return {"result": "ok"}

    return GraphExecutor(
        handlers={"test_handler": mock_handler},
        gateway_factory=lambda **kwargs: None,
    )


def test_model_node_passes_emotion_context_to_gateway() -> None:
    captured: dict = {}

    class _Gateway:
        def generate(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return SimpleNamespace(
                success=True,
                status="OK",
                reason_code=None,
                output={
                    "payload": {
                        "narrative": {"text": "ok"},
                        "intent_draft": {},
                        "meta": {"draft_only": True},
                    }
                },
            )

    executor = GraphExecutor(
        handlers={},
        gateway_factory=lambda **kwargs: _Gateway(),
    )
    state = {
        "request_id": "req-graph-emo-model",
        "graph_id": "graph-emo-model",
        "timeout_ms": 5000,
        "payload": {},
        "edge_map": {},
        "model_profile_id": "chat_ja_24x7_v1",
        "model_calls": [],
        "model_nodes": {
            "core_reasoner_primary": {
                "provider": "mock",
                "model_id": "mock-model",
                "timeout_ms": 3000,
                "available": True,
            }
        },
        "emotion_context": {
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_range_hint": "可以表达积极情感",
        },
    }

    result = executor._invoke_model_node(
        node_id="core_reasoner_primary",
        binding={"model_node_id": "core_reasoner_primary", "task": "chat"},
        state=state,
    )
    assert dict(captured.get("emotion_context") or {}).get("emotion_tag") == "HAPPY"
    assert dict(captured.get("emotion_context") or {}).get("mood_state") == "engaged"
    assert result.get("__orchestration_status__") == "OK"
