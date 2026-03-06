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
    # M33：CHAT 场景使用场景路由，active_node_count = 2（primary + renderer）
    # node_bindings 包含 3 个节点（+secondary fallback），但 edges 只有 1 条
    # graph_run_trace 记录的是实际执行的节点数（model_calls 数量）
    assert graph.get("status") in {"OK", "DEGRADED", "FAILED"}
    nodes_raw = graph.get("nodes_json")
    if isinstance(nodes_raw, str):
        nodes_value = json.loads(nodes_raw)
    else:
        nodes_value = nodes_raw
    assert isinstance(nodes_value, list)
    # 实际执行的节点数 >= 1（至少 primary 节点）
    assert len(nodes_value) >= 1

    # M33：CHAT 场景 llm_rows 数量 = 实际执行的节点数（不再是固定 9 个）
    assert len(llm_rows) >= 1
    # 第一个节点必须是 core_reasoner_primary
    assert str(llm_rows[0].get("node_id") or "") == "core_reasoner_primary"
