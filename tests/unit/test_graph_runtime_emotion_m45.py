from __future__ import annotations

from host_brain.integrations.graph_executor import GraphExecutor
from host_brain.integrations.graph_runtime import GraphRuntimeContract


def _graph_config() -> dict:
    return {
        "graph_id": "graph-emotion-m45",
        "version": "1.0",
        "entry_node": "node_a",
        "node_bindings": [
            {"node_id": "node_a", "handler_id": "handler_a"},
        ],
        "edges": [],
        "fallback_edges": [],
    }


def test_run_graph_initial_state_contains_emotion_context(monkeypatch) -> None:
    captured: dict[str, dict] = {}

    class _Compiled:
        def invoke(self, state):  # noqa: ANN001
            captured["state"] = dict(state)
            return state

    monkeypatch.setattr(
        GraphExecutor,
        "compile_runtime_graph",
        lambda self: _Compiled(),  # noqa: ARG005
    )

    runtime = GraphRuntimeContract()
    runtime.register_node_handler("handler_a", lambda payload, context: payload)
    result = runtime.run_graph(
        request_id="req-graph-emotion-m45",
        graph_config=_graph_config(),
        payload={
            "emotion_context": {
                "mood_state": "engaged",
                "emotion_tag": "HAPPY",
                "emotion_range_hint": "可以表达积极情感",
            }
        },
        ts_ms=1_700_000_000_000,
    )

    state = dict(captured.get("state") or {})
    assert dict(state.get("emotion_context") or {}).get("mood_state") == "engaged"
    assert dict(state.get("emotion_context") or {}).get("emotion_tag") == "HAPPY"
    assert state.get("mood_state") == "engaged"
    assert state.get("emotion_tag") == "HAPPY"
    assert result.status == "OK"
