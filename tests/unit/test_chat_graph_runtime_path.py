from __future__ import annotations

import time

from tests.unit.conftest import parse_sse_commit, mock_llm_runtime


def test_chat_approve_path_uses_graph_runtime(monkeypatch) -> None:
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")
    mock_llm_runtime(monkeypatch)

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
    body = parse_sse_commit(resp)
    assert body.get("decision") == "APPROVE", body

    llm = dict((body.get("audit_refs") or {}).get("llm") or {})
    assert llm.get("stage") == "S_ORCH_RUN", llm
    assert llm.get("status") in {"OK", "DEGRADED"}, llm
    prompt_context_observability = dict(llm.get("prompt_context_observability") or {})
    assert prompt_context_observability.get("selected_tier") in {
        "six_route",
        "core_three",
        "minimal_context",
        "bare_llm",
        None,
    }, prompt_context_observability
    route_decision = dict((body.get("audit_refs") or {}).get("route_decision") or {})
    assert route_decision.get("target") in {"host", "cloud", "skills"}, route_decision
    trace_metrics = dict((body.get("audit_refs") or {}).get("trace_metrics") or {})
    background_tasks = dict(trace_metrics.get("background_tasks_latency_ms") or {})
    assert "retrieval_latency_ms" in trace_metrics
    assert background_tasks.get("total_ms") is not None
    assert int(background_tasks.get("total_ms")) >= 0
    bond_queue = dict(trace_metrics.get("bond_queue") or {})
    assert bond_queue.get("status") in {"QUEUED", "DIRECT_FALLBACK_APPLIED", "SKIPPED", None}

