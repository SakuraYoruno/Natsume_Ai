from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_chat_emotion_context_propagates_to_governor_retrieval_and_graph(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("Natsume_AUDIT_DB", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setenv("HOST_COLD_START_ALLOWED", "true")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "true")
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")

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

    class _FakeRetrievalPipeline:
        def run(self, **kwargs):  # noqa: ANN003
            captured["retrieval_kwargs"] = dict(kwargs)
            return SimpleNamespace(
                status="OK",
                reason_code=None,
                output={
                    "recall": {
                        "routes": [],
                        "route_rows": [],
                        "route_errors": [],
                        "candidate_count": 0,
                    },
                    "rerank": {"topk": []},
                    "evidence_pack": {"evidence_refs": []},
                    "reason_chain": [],
                    "degraded": False,
                },
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
    monkeypatch.setattr(dep, "_ledger", None)
    monkeypatch.setattr(dep, "_intent_engine", _FakeIntentEngine())
    monkeypatch.setattr(dep, "_retrieval_pipeline", _FakeRetrievalPipeline())
    monkeypatch.setattr(dep, "_graph_runtime", _FakeGraphRuntime())

    client = TestClient(appmod.app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "test emotion integration",
            "session_id": "sess::emo::m45",
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_range_hint": "可以表达积极情感",
        },
    )
    assert response.status_code == 200
    body = response.json()

    governance_snapshot = dict(captured.get("governance_snapshot") or {})
    assert governance_snapshot.get("mood_state") == "engaged"
    assert governance_snapshot.get("emotion_tag") == "HAPPY"
    assert dict(governance_snapshot.get("emotion_context") or {}).get("emotion_tag") == "HAPPY"

    retrieval_kwargs = dict(captured.get("retrieval_kwargs") or {})
    assert retrieval_kwargs.get("emotion_context") == "HAPPY"

    graph_kwargs = dict(captured.get("graph_kwargs") or {})
    graph_payload = dict(graph_kwargs.get("payload") or {})
    assert dict(graph_payload.get("emotion_context") or {}).get("mood_state") == "engaged"
    assert dict(graph_payload.get("emotion_context") or {}).get("emotion_tag") == "HAPPY"

    audit_refs = dict(body.get("audit_refs") or {})
    assert dict(audit_refs.get("emotion_context") or {}).get("emotion_tag") == "HAPPY"
