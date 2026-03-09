from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient


def _mount_chat_runtime(monkeypatch, dep) -> None:  # noqa: ANN001
    class _FakeIntentEngine:
        def evaluate_intent(self, raw_intent, **kwargs):  # noqa: ANN001
            return {
                "decision": "APPROVE",
                "reason_code": None,
                "intent_id": "intent::retrieval::001",
                "tokens": {"approval_token_ref": "approval::retrieval::001"},
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
                                "narrative": {"text": "retrieval degraded but chat continues"},
                                "intent_draft": {"action": "narrative_only", "args": {}},
                                "meta": {"draft_only": True},
                            },
                        },
                    },
                    "model_control": [],
                },
            )

    monkeypatch.setattr(dep, "_ledger", None)
    monkeypatch.setattr(dep, "_intent_engine", _FakeIntentEngine())
    monkeypatch.setattr(dep, "_retrieval_pipeline", None)
    monkeypatch.setattr(dep, "_retrieval_orchestrator", None)
    monkeypatch.setattr(dep, "_graph_runtime", _FakeGraphRuntime())
    monkeypatch.setattr(dep, "_episodic_store", None)


def test_chat_retrieval_init_failure_degrades_without_internal_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("Natsume_AUDIT_DB", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setenv("HOST_COLD_START_ALLOWED", "true")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "true")
    monkeypatch.setenv("HOST_CHAT_DISABLE_RETRIEVAL", "false")
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    monkeypatch.setenv("HOST_GAMING_EDGE_CHAT_ENABLED", "false")

    from host_brain.app import fastapi_app as appmod
    import host_brain.app.dependencies as dep

    _mount_chat_runtime(monkeypatch, dep)

    def _raise_retrieval_init_error():  # noqa: ANN202
        raise RuntimeError("chromadb package unavailable: cannot import name 'Space'")

    monkeypatch.setattr(
        "host_brain.app.chat_handler.get_retrieval_orchestrator",
        _raise_retrieval_init_error,
    )
    monkeypatch.setattr(
        "host_brain.app.chat_handler.persist_chat_episodic_memory",
        lambda **kwargs: {"status": "OK", "memory_id": "mem::ok"},
    )
    monkeypatch.setattr("host_brain.app.chat_handler.get_episodic_store", lambda: object())

    client = TestClient(appmod.app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "retrieval init failure should not break main chat path",
            "session_id": "sess::retrieval::init-fail",
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body.get("decision") == "APPROVE"
    assert body.get("reason_code") == "RETRIEVAL_INIT_FAILED"
    assert "INTERNAL_SERVER_ERROR" != body.get("reason_code")

    retrieval = dict(dict(body.get("audit_refs") or {}).get("retrieval") or {})
    assert retrieval.get("status") == "DEGRADED"
    assert retrieval.get("reason_code") == "RETRIEVAL_INIT_FAILED"
    assert retrieval.get("degraded") is True
