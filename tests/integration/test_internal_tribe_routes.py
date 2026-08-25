from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from host_brain.app.internal_tribe_routes import router
from host_brain.tribe.event_bus import InMemoryTribeEventBus
from host_brain.tribe.registry import HostTribeRegistry
from host_brain.tribe.runtime_context import (
    TribeAgentCatalog,
    TribeRuntimeContext,
    activate_tribe_runtime_context,
    deactivate_tribe_runtime_context,
)
from host_brain.tribe.tribe_state import TribeStateRegistry


def _build_client() -> TestClient:
    bus = InMemoryTribeEventBus(max_events=64)
    owner = object()
    context = TribeRuntimeContext(
        state_registry=TribeStateRegistry(),
        tribe_registry=HostTribeRegistry(),
        event_bus=bus,
        agent_catalog=TribeAgentCatalog(
            (("test_placeholder_agent", object),)
        ),
        active_owner_provider=lambda: owner,
    )
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def bind_test_tribe_context(request: Request, call_next):
        activation = activate_tribe_runtime_context(
            owner=owner,
            context=context,
        )
        try:
            return await call_next(request)
        finally:
            deactivate_tribe_runtime_context(activation)

    return TestClient(app)


def test_ingest_tribe_event_requires_token_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INTERNAL_TRIBE_TOKEN", "secret-token")
    client = _build_client()

    resp = client.post(
        "/internal/tribe_event",
        json={"event_type": "channel_message_received", "source_agent": "edge.channel", "payload": {"text": "hi"}},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "UNAUTHORIZED_INTERNAL_TRIBE_EVENT"


def test_ingest_and_list_tribe_events(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INTERNAL_TRIBE_TOKEN", "secret-token")
    client = _build_client()
    headers = {"X-Internal-Token": "secret-token"}

    post_resp = client.post(
        "/internal/tribe_event",
        headers=headers,
        json={
            "event_type": "body_sense_updated",
            "source_agent": "edge.somatic",
            "target_agent": "limbic_agent",
            "payload": {"arousal": 0.6, "request_id": "req_1", "session_id": "sess_1"},
        },
    )
    assert post_resp.status_code == 202
    body = post_resp.json()
    assert body["accepted"] is True
    assert body["event_type"] == "body_sense_updated"

    get_resp = client.get("/internal/tribe_event/recent?limit=5", headers=headers)
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["total"] == 1
    assert data["items"][0]["event_type"] == "body_sense_updated"
    assert data["items"][0]["source_agent"] == "edge.somatic"

    second_resp = client.post(
        "/internal/tribe_event",
        headers=headers,
        json={
            "event_type": "channel_message_received",
            "source_agent": "edge.channel",
            "target_agent": "cortex_agent",
            "payload": {"conversation_id": "conv_1"},
        },
    )
    assert second_resp.status_code == 202

    filtered = client.get(
        "/internal/tribe_event/recent?limit=5&request_id=req_1&source_agent=edge.somatic",
        headers=headers,
    )
    assert filtered.status_code == 200
    filtered_data = filtered.json()
    assert filtered_data["total"] == 1
    assert filtered_data["items"][0]["event_type"] == "body_sense_updated"


def test_gemini_key_status_requires_token_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INTERNAL_TRIBE_TOKEN", "secret-token")
    client = _build_client()

    resp = client.get("/internal/llm/gemini_key")

    assert resp.status_code == 401
    assert resp.json()["detail"] == "UNAUTHORIZED_INTERNAL_TRIBE_EVENT"


def test_gemini_key_status_response_shape(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INTERNAL_TRIBE_TOKEN", "secret-token")
    client = _build_client()
    headers = {"X-Internal-Token": "secret-token"}

    resp = client.get("/internal/llm/gemini_key", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider_name"] == "gemini"
    assert "key_configured" in body
    assert "source" in body
    assert "runtime_profile_available" in body
    assert "env_key_configured" in body
    # 安全约束：不返回密钥原文或密钥列表
    assert "keys" not in body
    assert "api_key" not in body
    assert "pool_size" not in body
