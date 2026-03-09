from __future__ import annotations

import time


def test_chat_approve_path_uses_graph_runtime(monkeypatch) -> None:
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")

    from fastapi.testclient import TestClient
    from host_brain.app.fastapi_app import app

    client = TestClient(app)
    session_id = f"graph-path-{int(time.time() * 1000)}"
    resp = client.post(
        "/v1/chat",
        json={
            "message": "test graph runtime path",
            "session_id": session_id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("decision") == "APPROVE", body

    llm = dict((body.get("audit_refs") or {}).get("llm") or {})
    assert llm.get("stage") == "S_ORCH_RUN", llm
    assert llm.get("status") in {"OK", "DEGRADED"}, llm
    context_pack_observability = dict(llm.get("context_pack_observability") or {})
    assert context_pack_observability.get("selected_tier") in {
        "six_route",
        "core_three",
        "minimal_context",
        "bare_llm",
        None,
    }, context_pack_observability
    route_decision = dict((body.get("audit_refs") or {}).get("route_decision") or {})
    assert route_decision.get("target") in {"host", "cloud", "skills"}, route_decision
