from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from tests.unit.conftest import parse_sse_commit, mock_llm_runtime


def test_chat_emotion_context_propagates_to_governor_retrieval_and_graph(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("Natsume_AUDIT_DB", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setenv("HOST_COLD_START_ALLOWED", "true")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "true")
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    monkeypatch.setenv("HOST_GAMING_EDGE_CHAT_ENABLED", "false")
    monkeypatch.setenv("HOST_CHAT_DISABLE_RETRIEVAL", "false")

    from host_brain.app import fastapi_app as appmod

    captured: dict[str, object] = {}

    class _FakeIntentEngine:
        def evaluate_intent(self, raw_intent, **kwargs):  # noqa: ANN001
            captured["governance_snapshot"] = dict(kwargs.get("governance_snapshot") or {})
            return {
                "decision": "APPROVE",
                "reason_code": None,
                "intent_id": "intent::emo::001",
                "tokens": {"approval_token_ref": "approval::emo::001"},
                "stage": "S_INTENT_OK",
                "committed_route_token_ref": "GOV_OK_RENDER",
                "committed_route_token": {
                    "token": "GOV_OK_RENDER",
                    "reason_code": "APPROVE",
                    "approval_tier": "full",
                },
            }

    class _FakeRetrievalOrchestrator:
        async def arun(self, *, request):  # noqa: ANN001
            captured["retrieval_kwargs"] = {
                "query": request.query,
                "session_id": request.session_id,
                "emotion_context": request.emotion_context,
            }
            return SimpleNamespace(
                retrieval_summary={
                    "status": "OK",
                    "reason_code": "NONE",
                    "route_count": 0,
                    "routes": [],
                    "route_rows": [],
                    "route_errors": [],
                    "candidate_count": 0,
                    "rerank_topk": 0,
                    "evidence_refs": [],
                    "reason_chain": [],
                    "degraded": False,
                },
                retrieval_evidence_items=[],
            )

    class _FakeGraphRuntime:
        def run_graph(self, **kwargs):  # noqa: ANN003
            captured["graph_kwargs"] = dict(kwargs)
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
                                "narrative": {"text": "了解しました。感情コンテキストも反映して返します。"},
                                "intent_draft": {"action": "narrative_only", "args": {}},
                                "meta": {"draft_only": True},
                            },
                        },
                    },
                    "model_control": [],
                },
            )

    import host_brain.app.dependencies as dep
    import host_brain.app.chat_handler as chat_handler_mod
    import host_brain.app.chat_handler_complex as chat_complex_mod
    import host_brain.app.chat_stream_handler as stream_mod
    monkeypatch.setattr(dep, "_ledger", None)
    monkeypatch.setattr(dep, "_intent_engine", _FakeIntentEngine())
    monkeypatch.setattr(dep, "_retrieval_orchestrator", _FakeRetrievalOrchestrator())
    monkeypatch.setattr(dep, "_graph_runtime", _FakeGraphRuntime())
    # _sse_event_generator 通过 get_retrieval_orchestrator() 获取 orchestrator
    monkeypatch.setattr(stream_mod, "get_retrieval_orchestrator", lambda: _FakeRetrievalOrchestrator())
    monkeypatch.setattr(chat_complex_mod, "get_retrieval_orchestrator", lambda: _FakeRetrievalOrchestrator())
    monkeypatch.setattr(chat_complex_mod, "persist_chat_episodic_memory", lambda **kwargs: {"status": "OK"})
    monkeypatch.setattr(chat_complex_mod, "get_episodic_store", lambda: object())
    mock_llm_runtime(monkeypatch)

    client = TestClient(appmod.app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "test emotion integration",
            "session_id": "sess::emo::m45",
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.82,
            "emotion_range_hint": "可以表达积极情感",
        },
    )
    assert response.status_code == 200
    body = parse_sse_commit(response)

    governance_snapshot = dict(captured.get("governance_snapshot") or {})
    assert governance_snapshot.get("mood_state") == "engaged"
    assert governance_snapshot.get("emotion_tag") == "HAPPY"
    assert dict(governance_snapshot.get("emotion_context") or {}).get("emotion_tag") == "HAPPY"

    retrieval_kwargs = dict(captured.get("retrieval_kwargs") or {})
    assert retrieval_kwargs.get("emotion_context") == "HAPPY"

    # _sse_event_generator 不使用 _graph_runtime，graph_kwargs 不会被填充
    # 改为验证 COMMIT 事件中的 emotion_snapshot
    audit_refs = dict(body.get("audit_refs") or {})
    # emotion_snapshot 在 audit_refs 中
    emotion_snapshot = dict(audit_refs.get("emotion_snapshot") or {})
    assert emotion_snapshot.get("version") == "chat_emotion_snapshot.v1"
    assert dict(emotion_snapshot.get("emotion_context") or {}).get("emotion_tag") == "HAPPY"
    # emotion_intensity 由 resolve_chat_emotion_context 处理，默认值为 0.5
    assert float(dict(emotion_snapshot.get("emotion_context") or {}).get("emotion_intensity") or 0.0) > 0.0
