from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.requests import Request

from host_brain.app.chat_handler import handle_chat
from host_brain.app.schemas import ChatRequest


class _DummyMetrics:
    def __init__(self) -> None:
        self.chat_calls = 0
        self.rejections: list[str] = []
        self.errors = 0

    def inc_chat(self) -> None:
        self.chat_calls += 1

    def inc_rejection(self, reason_code: str) -> None:
        self.rejections.append(str(reason_code))

    def inc_error(self) -> None:
        self.errors += 1


class _FakeIntentEngine:
    def evaluate_intent(self, *args, **kwargs) -> dict[str, Any]:  # noqa: ANN002, ANN003
        request_id = str(kwargs.get("request_id") or "req-test")
        return {
            "decision": "APPROVE",
            "reason_code": None,
            "intent_id": f"intent::{request_id}",
            "tokens": {"approval_token_ref": f"approval::{request_id}"},
        }


class _FakeThalamusAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def classify_and_compile_intent_draft(self, **kwargs) -> dict[str, Any]:  # noqa: ANN003
        self.calls += 1
        request_id = str(kwargs.get("request_id") or "req-test")
        user_message = str(kwargs.get("user_message") or "")
        session_id = str(kwargs.get("session_id") or f"session::{request_id}")
        return {
            "id": f"intent::{request_id}",
            "schema_version": "Natsume.intent.v1",
            "session_id": session_id,
            "route": "chat",
            "target_id": "",
            "action": "narrative_only",
            "task": user_message,
            "route_reason": "TEST_DEFERRED_THALAMUS",
            "route_confidence": 0.9,
        }


def _build_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat",
            "headers": [],
            "state": {},
        }
    )


def _parse_sse_chunk(chunk: Any) -> dict[str, Any]:
    text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return {}


@pytest.mark.asyncio
async def test_handle_chat_streams_presence_before_thalamus_classification(monkeypatch) -> None:
    fake_thalamus = _FakeThalamusAgent()

    async def _fake_cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        request_id = str(kwargs.get("request_id") or "req-test")
        yield f'data: {json.dumps({"phase": "COMMIT", "done": True, "decision": "APPROVE", "request_id": request_id}, ensure_ascii=False)}\n\n'

    monkeypatch.setattr("host_brain.app.chat_handler.get_thalamus_agent", lambda: fake_thalamus)
    monkeypatch.setattr("host_brain.app.chat_handler.get_intent_engine", lambda: _FakeIntentEngine())
    monkeypatch.setattr(
        "host_brain.app.cortex_orchestrator.cortex_orchestrator_sse_generator",
        _fake_cortex_orchestrator_sse_generator,
    )

    metrics = _DummyMetrics()
    response = await handle_chat(
        request_body=ChatRequest(message="我想和你认真聊聊今天的心情。"),
        request=_build_request(),
        metrics=metrics,
    )

    assert fake_thalamus.calls == 0
    assert metrics.chat_calls == 1

    body_iter = response.body_iterator
    first_chunk = await body_iter.__anext__()
    first_event = _parse_sse_chunk(first_chunk)

    assert first_event["phase"] == "PRESENCE"
    assert first_event["event"] == "THINKING"
    assert fake_thalamus.calls == 0

    second_chunk = await body_iter.__anext__()
    second_event = _parse_sse_chunk(second_chunk)

    assert fake_thalamus.calls == 1
    assert second_event["phase"] == "PRESENCE"
    assert second_event["event"] == "GOV_APPROVED"

    remaining = []
    async for chunk in body_iter:
        remaining.append(_parse_sse_chunk(chunk))

    assert any(event.get("phase") == "COMMIT" for event in remaining)
