from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from tests.unit.conftest import parse_sse_commit, mock_llm_runtime


class _FakeIntentEngine:
    def evaluate_intent(self, raw_intent, **kwargs):  # noqa: ANN001
        return {
            "decision": "APPROVE",
            "reason_code": None,
            "intent_id": "intent::gaming::001",
            "tokens": {"approval_token_ref": "approval::gaming::001"},
            "stage": "S_INTENT_OK",
            "committed_route_token_ref": "GOV_OK_RENDER",
            "committed_route_token": {
                "token": "GOV_OK_RENDER",
                "reason_code": "APPROVE",
                "approval_tier": "full",
            },
        }


class _NeverCalledGraphRuntime:
    def run_graph(self, **kwargs):  # noqa: ANN003
        raise AssertionError("graph runtime should not run in gaming edge fast path")


class _HostFallbackGraphRuntime:
    def __init__(self) -> None:
        self.called = False

    def run_graph(self, **kwargs):  # noqa: ANN003
        self.called = True
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
                            "narrative": {"text": "host fallback narrative"},
                            "intent_draft": {"action": "narrative_only", "args": {}},
                            "meta": {"draft_only": True},
                        },
                    },
                    "core_reasoner_primary": {
                        "gateway_status": "OK",
                        "reason_code": None,
                        "provider": "mock",
                        "model_id": "core-reasoner-model",
                        "payload": {
                            "narrative": {"text": "core fallback narrative"},
                            "intent_draft": {"action": "narrative_only", "args": {}},
                            "meta": {"draft_only": True},
                        },
                    },
                    "workbench_code_assist": {
                        "gateway_status": "OK",
                        "reason_code": None,
                        "provider": "mock",
                        "model_id": "workbench-code-model",
                        "payload": {
                            "narrative": {"text": "code assist fallback"},
                            "intent_draft": {"action": "narrative_only", "args": {}},
                            "meta": {"draft_only": True},
                        },
                    },
                    "consistency_verifier": {
                        "gateway_status": "OK",
                        "reason_code": None,
                        "provider": "mock",
                        "model_id": "consistency-model",
                        "payload": {
                            "narrative": {"text": "consistency fallback"},
                            "meta": {"draft_only": True},
                        },
                    },
                    "renderer_ja_c_style": {
                        "gateway_status": "OK",
                        "reason_code": None,
                        "provider": "mock",
                        "model_id": "renderer-model",
                        "payload": {
                            "narrative": {"text": "renderer fallback narrative"},
                            "meta": {"draft_only": True},
                        },
                    },
                },
                "model_control": [],
            },
        )


def _setup_common_env(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setenv("Natsume_AUDIT_DB", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setenv("HOST_COLD_START_ALLOWED", "true")
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "true")
    monkeypatch.setenv("HOST_ENABLE_EDGE_RPC", "true")
    monkeypatch.setenv("HOST_GAMING_EDGE_CHAT_ENABLED", "true")
    monkeypatch.setenv("HOST_SYS_MODE", "gaming")


def test_chat_gaming_mode_routes_to_edge_fast_path(monkeypatch, tmp_path) -> None:
    _setup_common_env(monkeypatch, tmp_path)

    from host_brain.app import fastapi_app as appmod
    import host_brain.app.dependencies as dep

    dep._ledger = None
    dep._intent_engine = _FakeIntentEngine()
    dep._retrieval_pipeline = None
    dep._graph_runtime = _NeverCalledGraphRuntime()
    dep._episodic_store = None

    captured: dict[str, object] = {}

    def _fake_edge_chat(**kwargs):  # noqa: ANN001
        captured["edge_chat_kwargs"] = dict(kwargs)
        return {
            "success": True,
            "reason_code": None,
            "detail": "ok",
            "narrative": "edge minimal reply",
            "model_id": "ollama:3b-q4",
            "draft_only": True,
            "latency_ms": 18,
            "presence": {"success": True, "event": {"available": True}},
            "compile": {"success": True, "event": {"draft_only": True}},
            "emotion_context": dict(kwargs.get("emotion_context") or {}),
        }

    monkeypatch.setattr(
        "host_brain.app.chat_handler_edge._run_gaming_edge_chat",
        _fake_edge_chat,
    )

    client = TestClient(appmod.app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "gaming chat ping",
            "session_id": "sess::gaming::edge",
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.82,
        },
    )
    assert response.status_code == 200
    body = parse_sse_commit(response)
    assert body.get("decision") == "APPROVE"
    assert body.get("narrative") == "edge minimal reply"

    audit_refs = dict(body.get("audit_refs") or {})
    llm = dict(audit_refs.get("llm") or {})
    retrieval = dict(audit_refs.get("retrieval") or {})
    episodic = dict(audit_refs.get("episodic_memory") or {})
    edge_route = dict(audit_refs.get("edge_route") or {})
    route_decision = dict(audit_refs.get("route_decision") or {})

    assert llm.get("provider") == "edge.fallback_llm"
    assert llm.get("stage") == "S_EDGE_RPC_GAMING_CHAT"
    assert retrieval.get("reason_code") == "HOST_GAMING_EDGE_CHAT"
    assert episodic.get("reason_code") == "HOST_GAMING_EDGE_CHAT_MINIMAL_PATH"
    # edge_route 字段在 llm.model_control 中
    model_control = list(llm.get("model_control") or [])
    assert model_control
    assert model_control[0].get("route") == "gaming_cloud_cn"
    assert route_decision.get("target") == "edge"
    assert route_decision.get("route_target") == "cloud_cn"
    assert dict(llm.get("route_decision") or {}).get("route_target") == "cloud_cn"
    assert model_control[0].get("emotion_tag") == "HAPPY"
    llm_nodes = list(llm.get("nodes") or [])
    assert llm_nodes
    node_input = dict(llm_nodes[0].get("input") or {})
    assert node_input.get("emotion_tag") == "HAPPY"
    edge_chat_kwargs = dict(captured.get("edge_chat_kwargs") or {})
    assert dict(edge_chat_kwargs.get("emotion_context") or {}).get("emotion_tag") == "HAPPY"


def test_chat_gaming_edge_failure_falls_back_to_host_graph(monkeypatch, tmp_path) -> None:
    _setup_common_env(monkeypatch, tmp_path)

    from host_brain.app import fastapi_app as appmod
    import host_brain.app.dependencies as dep

    fallback_runtime = _HostFallbackGraphRuntime()
    dep._ledger = None
    dep._intent_engine = _FakeIntentEngine()
    dep._retrieval_pipeline = None
    dep._graph_runtime = fallback_runtime
    dep._episodic_store = None

    monkeypatch.setattr(
        "host_brain.app.chat_handler_edge._run_gaming_edge_chat",
        lambda **kwargs: {  # noqa: ARG005
            "success": False,
            "reason_code": "EDGE_RPC_FAILED",
            "detail": "mock edge down",
            "presence": {"success": False, "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC"},
        },
    )

    client = TestClient(appmod.app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "gaming chat fallback",
            "session_id": "sess::gaming::fallback",
        },
    )
    assert response.status_code == 200
    body = parse_sse_commit(response)
    assert body.get("decision") == "APPROVE"
    # edge 失败后 fallback 到 _sse_event_generator（SCENE_CHAT 普通路径）
    # _graph_runtime 不再被调用，改为检查 retrieval 降级状态
    audit_refs = dict(body.get("audit_refs") or {})
    llm = dict(audit_refs.get("llm") or {})
    # fallback 路径使用 mock provider
    assert llm.get("provider") == "mock"


def test_chat_gaming_mode_does_not_route_edge_when_flag_disabled(monkeypatch, tmp_path) -> None:
    _setup_common_env(monkeypatch, tmp_path)
    monkeypatch.setenv("HOST_GAMING_EDGE_CHAT_ENABLED", "false")
    monkeypatch.setenv("HOST_ENABLE_RETRIEVAL", "false")

    from host_brain.app import fastapi_app as appmod
    import host_brain.app.dependencies as dep

    fallback_runtime = _HostFallbackGraphRuntime()
    dep._ledger = None
    dep._intent_engine = _FakeIntentEngine()
    dep._retrieval_pipeline = None
    dep._graph_runtime = fallback_runtime
    dep._episodic_store = None

    monkeypatch.setattr(
        "host_brain.app.chat_handler_edge._run_gaming_edge_chat",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("edge route should not run")),  # noqa: ARG005
    )
    mock_llm_runtime(monkeypatch)

    client = TestClient(appmod.app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "gaming mode but edge route disabled",
            "session_id": "sess::gaming::flag-off",
        },
    )
    assert response.status_code == 200
    body = parse_sse_commit(response)
    assert body.get("decision") == "APPROVE"
    # flag 禁用时走 _sse_event_generator（SCENE_CHAT 普通路径），不使用 _graph_runtime
    llm = dict(dict(body.get("audit_refs") or {}).get("llm") or {})
    assert llm.get("provider") == "mock"


def test_run_gaming_edge_chat_forwards_emotion_context_to_rpc(monkeypatch) -> None:
    from host_brain.app import chat_handler_edge as chat_edge_mod

    calls: list[dict[str, object]] = []

    def _fake_post_edge_fallback_rpc(**kwargs):  # noqa: ANN001
        calls.append(dict(kwargs))
        endpoint = str(kwargs.get("endpoint_path") or "")
        if endpoint.endswith("/presence"):
            return {
                "success": True,
                "status": "OK",
                "reason_code": None,
                "detail": "presence ok",
                "event": {"available": True},
                "ts_ms": 1,
            }
        return {
            "success": True,
            "status": "OK",
            "reason_code": None,
            "detail": "compile ok",
            "event": {
                "model_id": "edge-fallback-3b",
                "draft_only": True,
                "draft": {"summary": "ok"},
            },
            "ts_ms": 2,
        }

    monkeypatch.setattr(chat_edge_mod, "_post_edge_fallback_rpc", _fake_post_edge_fallback_rpc)
    result = chat_edge_mod._run_gaming_edge_chat(
        request_id="req::edge::emotion",
        session_id="sess::edge::emotion",
        message="edge chat with emotion",
        emotion_context={
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.9,
            "emotion_range_hint": "playful",
        },
    )
    assert result.get("success") is True
    assert len(calls) == 2
    compile_payload = dict(calls[1].get("payload") or {})
    edge_emotion = dict(compile_payload.get("emotion_context") or {})
    assert edge_emotion.get("mood_state") == "engaged"
    assert edge_emotion.get("emotion_tag") == "HAPPY"
    assert float(edge_emotion.get("emotion_intensity") or 0.0) == 0.9
