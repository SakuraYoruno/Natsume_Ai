from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_chat_persists_graph_trace_and_llm_node_calls(monkeypatch, tmp_path) -> None:
    audit_path = tmp_path / "audit.sqlite3"
    monkeypatch.setenv("Natsume_AUDIT_DB", str(audit_path))
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")
    monkeypatch.setenv("HOST_COLD_START_ALLOWED", "true")

    from host_brain.app import fastapi_app as appmod

    # Reset cached singletons to guarantee this test uses isolated ledger/runtime state.
    appmod._ledger = None
    appmod._intent_engine = None
    appmod._retrieval_pipeline = None
    appmod._graph_runtime = None

    client = TestClient(appmod.app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "persist observability rows",
            "session_id": "sess::obs::persist",
        },
    )
    assert response.status_code == 200
    body = response.json()
    request_id = str(body.get("request_id") or "")
    assert request_id

    ledger = appmod.get_ledger()
    graph_rows = ledger.fetch_by_request_id("graph_run_trace", request_id)
    llm_rows = ledger.fetch_by_request_id("llm_node_call_log", request_id)

    assert len(graph_rows) == 1
    graph = graph_rows[0]
    assert int(graph.get("node_count") or 0) == len(appmod.CHAT_GRAPH_NODE_ORDER)
    assert graph.get("status") in {"OK", "DEGRADED", "FAILED"}
    nodes_raw = graph.get("nodes_json")
    if isinstance(nodes_raw, str):
        nodes_value = json.loads(nodes_raw)
    else:
        nodes_value = nodes_raw
    assert isinstance(nodes_value, list)
    assert len(nodes_value) == len(appmod.CHAT_GRAPH_NODE_ORDER)

    assert len(llm_rows) == len(appmod.CHAT_GRAPH_NODE_ORDER)
    assert [str(row.get("node_id") or "") for row in llm_rows] == list(appmod.CHAT_GRAPH_NODE_ORDER)
    for row in llm_rows:
        assert str(row.get("provider") or "")
        assert str(row.get("model_id") or "")
        assert str(row.get("input_digest") or "")
        assert str(row.get("output_digest") or "")
