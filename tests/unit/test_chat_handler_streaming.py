from __future__ import annotations

import json
import importlib
from typing import Any

import pytest
from starlette.requests import Request

from host_brain.app.chat_handler import handle_chat
import host_brain.app.chat_handler as chat_handler_module
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
        self.last_kwargs: dict[str, Any] = {}

    async def classify_and_compile_intent_draft(self, **kwargs) -> dict[str, Any]:  # noqa: ANN003
        self.calls += 1
        self.last_kwargs = dict(kwargs)
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


@pytest.mark.asyncio
async def test_handle_chat_forwards_multimodal_fields_to_thalamus(monkeypatch) -> None:
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

    response = await handle_chat(
        request_body=ChatRequest(
            message="这是什么",
            attachments=[{"type": "image", "remote_url": "https://example.com/screen.png"}],
            multimodal_observations={
                "summary_for_chat": "用户发来一张截图，更像是在问界面上这段提示是什么意思。",
                "artifact_type": "screenshot",
                "visible_text": "错误提示",
            },
        ),
        request=_build_request(),
        metrics=_DummyMetrics(),
    )

    body_iter = response.body_iterator
    await body_iter.__anext__()
    await body_iter.__anext__()

    assert fake_thalamus.calls == 1
    assert fake_thalamus.last_kwargs["attachments"][0]["type"] == "image"
    assert fake_thalamus.last_kwargs["multimodal_observations"]["artifact_type"] == "screenshot"


def test_resolve_governor_executor_workers_defaults_to_four(monkeypatch) -> None:
    monkeypatch.delenv("HOST_GOVERNOR_EXECUTOR_WORKERS", raising=False)
    reloaded = importlib.reload(chat_handler_module)

    assert reloaded._resolve_governor_executor_workers() == 4


@pytest.mark.asyncio
async def test_evaluate_governor_with_timing_reports_queue_and_execute_ms() -> None:
    engine = _FakeIntentEngine()

    result, timing = await chat_handler_module._evaluate_governor_with_timing(
        engine=engine,
        intent_draft={"route": "chat"},
        request_id="req-governor-timing",
        thread_id="thread-1",
        governance_snapshot={},
    )

    assert result["decision"] == "APPROVE"
    assert timing["total_latency_ms"] >= 0
    assert timing["queue_wait_ms"] >= 0
    assert timing["execute_ms"] >= 0


@pytest.mark.asyncio
async def test_evaluate_governor_with_timing_inline_mode_skips_queue_wait() -> None:
    engine = _FakeIntentEngine()

    result, timing = await chat_handler_module._evaluate_governor_with_timing(
        engine=engine,
        intent_draft={"route": "chat"},
        request_id="req-governor-inline",
        thread_id="thread-1",
        governance_snapshot={},
        inline=True,
    )

    assert result["decision"] == "APPROVE"
    assert timing["queue_wait_ms"] == 0
    assert timing["execute_ms"] >= 0


def test_should_use_inline_governor_only_for_fast_chat_profiles() -> None:
    assert chat_handler_module._should_use_inline_governor({"route": "chat"}) is False
    assert chat_handler_module._should_use_inline_governor({"route": "skill"}) is False
    assert chat_handler_module._should_use_inline_governor({}) is False
    assert chat_handler_module._should_use_inline_governor({"route": "chat"}) is False
