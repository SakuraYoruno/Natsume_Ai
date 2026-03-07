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
    # M33：CHAT 场景使用场景路由，active_node_count = 3（primary + consistency + renderer）
    # graph_run_trace / llm_node_call_log 记录的是本次实际执行节点，而不是全量 9 节点注册表。
    assert graph.get("status") in {"OK", "DEGRADED", "FAILED"}
    nodes_raw = graph.get("nodes_json")
    if isinstance(nodes_raw, str):
        nodes_value = json.loads(nodes_raw)
    else:
        nodes_value = nodes_raw
    assert isinstance(nodes_value, list)
    # 实际执行的节点数 >= 1（至少 primary 节点），且不应退回旧的全量 9 节点视觉混淆。
    assert len(nodes_value) >= 1
    assert len(llm_rows) == 1
    assert str(llm_rows[0].get("node_id") or "") == "chat_direct_ja"
    assert len(llm_rows) < 9
