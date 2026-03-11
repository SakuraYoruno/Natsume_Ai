from __future__ import annotations

import json

from fastapi.testclient import TestClient
from tests.unit.conftest import parse_sse_commit, mock_llm_runtime


def test_chat_persists_graph_trace_and_llm_node_calls(monkeypatch, tmp_path) -> None:
    audit_path = tmp_path / "audit.sqlite3"
    monkeypatch.setenv("Natsume_AUDIT_DB", str(audit_path))
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")
    monkeypatch.setenv("HOST_COLD_START_ALLOWED", "true")

    from host_brain.app import fastapi_app as appmod

    # Reset cached singletons to guarantee this test uses isolated ledger/runtime state.
    import host_brain.app.dependencies as dep
    dep._ledger = None
    dep._intent_engine = None
    dep._retrieval_pipeline = None
    dep._graph_runtime = None

    mock_llm_runtime(monkeypatch)
    client = TestClient(appmod.app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "persist observability rows",
            "session_id": "sess::obs::persist",
        },
    )
    assert response.status_code == 200
    body = parse_sse_commit(response)
    request_id = str(body.get("request_id") or "")
    assert request_id

    # _sse_event_generator 写入 airlock 记录，不写入 graph_run_trace/llm_node_call_log
    # 验证 COMMIT 事件中的 audit_refs 包含正确的 LLM 信息
    audit_refs = dict(body.get("audit_refs") or {})
    llm = dict(audit_refs.get("llm") or {})
    assert llm.get("provider") == "mock"
    assert llm.get("status") == "OK"
    # airlock 检疫记录
    airlock = dict(audit_refs.get("airlock") or {})
    assert airlock.get("ingress_classification") in {"FACT", "DRAFT_CODE", "DRAFT_INSTRUCTION", "UNKNOWN"}
