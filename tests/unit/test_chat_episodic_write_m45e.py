from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from tests.unit.conftest import parse_sse_commit, mock_llm_runtime


def _mount_chat_runtime(monkeypatch, appmod) -> None:  # noqa: ANN001
    class _FakeIntentEngine:
        def evaluate_intent(self, raw_intent, **kwargs):  # noqa: ANN001
            return {
                "decision": "APPROVE",
                "reason_code": None,
                "intent_id": "intent::episodic::001",
                "tokens": {"approval_token_ref": "approval::episodic::001"},
                "stage": "S_INTENT_OK",
                "committed_route_token_ref": "GOV_OK_RENDER",
                "committed_route_token": {
                    "token": "GOV_OK_RENDER",
                    "reason_code": "APPROVE",
                    "approval_tier": "full",
                },
            }

    class _FakeGraphRuntime:
        def run_graph(self, **kwargs):  # noqa: ANN003
            return SimpleNamespace(
                status="OK",
                reason_code=None,
                stage="S_ORCH_RUN",
                detail="ok",
                output={
                    "payload": {
                        "chat_direct_ja": {
                            "gateway_status": "OK",
                            "reason_code": None,
                            "provider": "mock",
                            "model_id": "chat-direct-model",
                            "payload": {
                                "narrative": {"text": "hello from natsume"},
                                "intent_draft": {"action": "narrative_only", "args": {}},
                                "meta": {"draft_only": True},
                            },
                        },
                    },
                    "model_control": [],
                },
            )

    monkeypatch.setattr(appmod, "_ledger", None)
    monkeypatch.setattr(appmod, "_intent_engine", _FakeIntentEngine())
    monkeypatch.setattr(appmod, "_retrieval_pipeline", None)
    monkeypatch.setattr(appmod, "_graph_runtime", _FakeGraphRuntime())
    monkeypatch.setattr(appmod, "_episodic_store", None)


def test_chat_writes_episodic_memory_on_approve(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("Natsume_AUDIT_DB", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setenv("HOST_COLD_START_ALLOWED", "true")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")

    from host_brain.app import fastapi_app as appmod
    import host_brain.app.dependencies as dep

    _mount_chat_runtime(monkeypatch, dep)
    mock_llm_runtime(monkeypatch)

    client = TestClient(appmod.app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "remember this user statement",
            "session_id": "sess::episodic::ok",
            "emotion_tag": "HAPPY",
        },
    )
    assert response.status_code == 200
    body = parse_sse_commit(response)

    assert body.get("decision") == "APPROVE"
    audit_refs = dict(body.get("audit_refs") or {})
    episodic_info = dict(audit_refs.get("episodic_memory") or {})
    assert episodic_info.get("status") == "OK"
    memory_id = str(episodic_info.get("memory_id") or "")
    assert memory_id

    from host_brain.app.dependencies import get_episodic_store, get_ledger
    record = get_episodic_store().get_memory(memory_id)
    assert record is not None
    assert "remember this user statement" in record.text
    assert "hello from natsume" in record.text
    assert record.emotion_tag == "HAPPY"

    rows = get_ledger().fetch_by_request_id("memory_ops_log", str(body.get("request_id") or ""))
    assert any(str(row.get("op_kind") or "") == "WRITE" and str(row.get("status") or "") == "SUCCESS" for row in rows)


def test_chat_episodic_write_failure_is_non_blocking(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("Natsume_AUDIT_DB", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setenv("HOST_COLD_START_ALLOWED", "true")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")

    from host_brain.app import fastapi_app as appmod
    import host_brain.app.dependencies as dep

    _mount_chat_runtime(monkeypatch, dep)
    mock_llm_runtime(monkeypatch)
    monkeypatch.setattr("host_brain.app.chat_stream_handler.persist_chat_episodic_memory", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("episodic boom")))

    client = TestClient(appmod.app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "this still should return reply",
            "session_id": "sess::episodic::degrade",
        },
    )
    assert response.status_code == 200
    body = parse_sse_commit(response)
    assert body.get("decision") == "APPROVE"

    episodic_info = dict(dict(body.get("audit_refs") or {}).get("episodic_memory") or {})
    assert episodic_info.get("status") == "DEGRADED"
    assert episodic_info.get("reason_code") == "EPISODIC_WRITE_FAILED"
