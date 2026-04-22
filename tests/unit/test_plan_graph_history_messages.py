from __future__ import annotations

from unittest.mock import patch

from host_brain.app.plan_graph import _synthesize_node
from host_brain.conversation.history import ConversationWindow
from host_brain.conversation.schema import Turn


class _FakeHistory:
    def fetch_window_for_messages(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        return ConversationWindow(
            recent_turns=[
                Turn(
                    turn_id="t1",
                    session_id="sess",
                    person_id="p1",
                    request_id="req-1",
                    role="user",
                    content="前一轮用户",
                    ts_ms=1,
                    turn_index=0,
                ),
                Turn(
                    turn_id="t2",
                    session_id="sess",
                    person_id="p1",
                    request_id="req-2",
                    role="assistant",
                    content="前一轮回复",
                    ts_ms=2,
                    turn_index=1,
                ),
            ],
            older_summary="更早些时候，对方提到过昨天的事。",
            fetched_count=2,
            injected_count=2,
            older_turn_count=3,
        )


def test_synthesize_node_includes_conversation_history_messages() -> None:
    captured: dict[str, object] = {}

    def _fake_stream_llm_chunks(**kwargs):  # noqa: ANN003
        captured["messages"] = kwargs.get("messages")
        yield "综合结果"

    async def _fake_resolve_host_llm_runtime_async(**kwargs):  # noqa: ANN003
        _ = kwargs
        return {"provider_name": "openai", "model_id": "gpt", "llm_api_key": "k", "timeout_ms": 5000, "available": True}

    def _fake_resolve_executive_router_llm_runtime(*, base_runtime):  # noqa: ANN001
        return dict(base_runtime)

    state = {
        "request_id": "req-x",
        "session_id": "sess",
        "person_id": "p1",
        "user_message": "你觉得呢",
        "task_results": [
            {"task_index": 0, "task_description": "子任务A", "result": "结果A", "success": True},
        ],
    }

    with patch(
        "host_brain.integrations.llm_helpers.stream_llm_chunks",
        side_effect=_fake_stream_llm_chunks,
    ), patch(
        "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
        side_effect=_fake_resolve_host_llm_runtime_async,
    ), patch(
        "host_brain.app.graph_config_builder.resolve_executive_router_llm_runtime",
        side_effect=_fake_resolve_executive_router_llm_runtime,
    ), patch(
        "host_brain.app.dependencies.get_conversation_history",
        return_value=_FakeHistory(),
    ):
        result = _synthesize_node(state)

    assert result["final_answer"] == "综合结果"
    messages = list(captured["messages"] or [])
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert "【更早对话摘要】" in messages[0]["content"]
    assert messages[1]["content"] == "前一轮用户"
    assert messages[2]["content"] == "前一轮回复"
