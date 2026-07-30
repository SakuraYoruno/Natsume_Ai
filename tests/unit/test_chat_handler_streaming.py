from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request

from host_brain.app.emotion_context import (
    HostEmotionRuntime,
    build_chat_emotion_snapshot,
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.chat_handler import handle_chat
import host_brain.app.chat_handler as chat_handler_module
from host_brain.app.schemas import ChatRequest
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts


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


class _CapturingIntentEngine(_FakeIntentEngine):
    def __init__(self) -> None:
        self.raw_intents: list[dict[str, Any]] = []

    def evaluate_intent(self, *args, **kwargs) -> dict[str, Any]:  # noqa: ANN002, ANN003
        raw_intent = kwargs.get("raw_intent")
        self.raw_intents.append(dict(raw_intent or {}) if isinstance(raw_intent, dict) else {})
        return super().evaluate_intent(*args, **kwargs)


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


def _input_runtime_ports(
    intent_engine: Any,
    *,
    thalamus_agent: Any | None = None,
    ans_agent: Any | None = None,
) -> InputRuntimeApplicationPorts:
    def _unused(*_args: Any, **_kwargs: Any) -> Any:
        return None

    resolved_thalamus = thalamus_agent or _FakeThalamusAgent()
    resolved_ans = ans_agent or SimpleNamespace(emit_task_success=_unused)
    emotion_runtime = HostEmotionRuntime()

    def resolve_emotion_with_app_runtime(**kwargs: Any) -> dict[str, Any]:
        return resolve_chat_emotion_context(
            runtime=emotion_runtime,
            **kwargs,
        )

    return InputRuntimeApplicationPorts(
        get_intent_engine=lambda: intent_engine,
        get_ledger=lambda: object(),
        get_life_snapshot_reader=lambda: object(),
        get_multimodal_observer=lambda: object(),
        get_multimodal_processor=lambda: object(),
        get_task_status_projection=lambda: object(),
        notify_user_interaction=lambda: None,
        evaluate_governor_with_timing=(
            chat_handler_module._evaluate_governor_with_timing
        ),
        build_chat_emotion_snapshot=build_chat_emotion_snapshot,
        build_chat_governance_snapshot=build_chat_governance_snapshot,
        resolve_chat_emotion_context=resolve_emotion_with_app_runtime,
        chat_request_factory=ChatRequest,
        cortex_orchestrator_sse_generator=_unused,
        create_multimodal_job=_unused,
        update_multimodal_job=_unused,
        reply_form_decision_type=object,
        get_reply_form_decider=lambda: object(),
        message_type=object,
        trigger_reply_performance=_unused,
        render_quick_gateway_progress_text=lambda **_kwargs: "",
        render_quick_gateway_realign_text=lambda **_kwargs: "",
        get_thalamus_agent=lambda: resolved_thalamus,
        get_ans_agent=lambda: resolved_ans,
    )


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
async def test_handle_chat_requires_typed_input_runtime_ports_after_mode_gate() -> None:
    with pytest.raises(RuntimeError, match="HOST_INPUT_RUNTIME_PORTS_NOT_INJECTED"):
        await handle_chat(
            request_body=ChatRequest(message="普通聊天必须使用显式准入端口。"),
            request=_build_request(),
            metrics=_DummyMetrics(),
            hard_state_provider=lambda: {"sys_mode": "normal"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "expected_code"),
    [
        ("get_thalamus_agent", "HOST_INPUT_THALAMUS_AGENT_NOT_INJECTED"),
        ("get_ans_agent", "HOST_INPUT_ANS_AGENT_NOT_INJECTED"),
    ],
)
async def test_handle_chat_fails_closed_when_typed_tribe_agent_is_missing(
    field_name: str,
    expected_code: str,
) -> None:
    input_ports = replace(
        _input_runtime_ports(_FakeIntentEngine()),
        **{field_name: None},
    )

    with pytest.raises(RuntimeError, match=expected_code):
        await handle_chat(
            request_body=ChatRequest(message="聊天入口必须拒绝缺失的 Tribe 运行时端口。"),
            request=_build_request(),
            metrics=_DummyMetrics(),
            hard_state_provider=lambda: {"sys_mode": "normal"},
            input_runtime_ports=input_ports,
        )


@pytest.mark.asyncio
async def test_handle_chat_streams_presence_before_thalamus_classification(monkeypatch) -> None:
    fake_thalamus = _FakeThalamusAgent()
    governed_command_application = object()
    cortex_kwargs: dict[str, Any] = {}

    async def _fake_cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        cortex_kwargs.update(kwargs)
        request_id = str(kwargs.get("request_id") or "req-test")
        yield f'data: {json.dumps({"phase": "COMMIT", "done": True, "decision": "APPROVE", "request_id": request_id}, ensure_ascii=False)}\n\n'

    metrics = _DummyMetrics()
    response = await handle_chat(
        request_body=ChatRequest(message="我想和你认真聊聊今天的心情。"),
        request=_build_request(),
        metrics=metrics,
        governed_command_application=governed_command_application,
        hard_state_provider=lambda: {"sys_mode": "normal"},
        input_runtime_ports=_input_runtime_ports(
            _FakeIntentEngine(),
            thalamus_agent=fake_thalamus,
        ),
        cortex_orchestrator_sse_generator=(
            _fake_cortex_orchestrator_sse_generator
        ),
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
    assert cortex_kwargs["governed_command_application"] is governed_command_application
    assert cortex_kwargs["command_authority"] == {
        "direct_chat_scope_schema": "natsume.direct-chat-scope.v1",
        "direct_chat_scope_kind": "LOCAL_PERSONAL_UNATTESTED",
        "principal_id": "default",
    }


@pytest.mark.asyncio
async def test_handle_chat_derives_local_personal_scope_before_affect_and_cortex(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOST_DEFAULT_USER_ID", "local-companion-owner")
    fake_thalamus = _FakeThalamusAgent()
    cortex_kwargs: dict[str, Any] = {}
    request_body = ChatRequest(
        message="我今天其实有点难过。",
        person_id="caller-selected-other-person",
        workspace_id="caller-selected-other-workspace",
        session_id="browser-session-17",
        emotion_context={"workspace_id": "nested-caller-workspace"},
    )

    async def _fake_cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        cortex_kwargs.update(kwargs)
        yield f'data: {json.dumps({"phase": "COMMIT", "done": True, "decision": "APPROVE", "request_id": kwargs["request_id"]}, ensure_ascii=False)}\n\n'

    response = await handle_chat(
        request_body=request_body,
        request=_build_request(),
        metrics=_DummyMetrics(),
        hard_state_provider=lambda: {"sys_mode": "normal"},
        input_runtime_ports=_input_runtime_ports(
            _FakeIntentEngine(),
            thalamus_agent=fake_thalamus,
        ),
        cortex_orchestrator_sse_generator=_fake_cortex_orchestrator_sse_generator,
    )
    async for _chunk in response.body_iterator:
        pass

    assert request_body.person_id == "local-companion-owner"
    assert request_body.workspace_id == "personal:local-companion-owner"
    assert request_body.session_id == "browser-session-17"
    assert fake_thalamus.last_kwargs["person_id"] == "local-companion-owner"
    assert cortex_kwargs["command_authority"] == {
        "direct_chat_scope_schema": "natsume.direct-chat-scope.v1",
        "direct_chat_scope_kind": "LOCAL_PERSONAL_UNATTESTED",
        "principal_id": "local-companion-owner",
    }
    assert "tenant_id" not in cortex_kwargs["command_authority"]
    assert "claim_ref" not in cortex_kwargs["command_authority"]


@pytest.mark.asyncio
async def test_handle_chat_forwards_multimodal_fields_to_thalamus(monkeypatch) -> None:
    fake_thalamus = _FakeThalamusAgent()

    async def _fake_cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        request_id = str(kwargs.get("request_id") or "req-test")
        yield f'data: {json.dumps({"phase": "COMMIT", "done": True, "decision": "APPROVE", "request_id": request_id}, ensure_ascii=False)}\n\n'

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
        hard_state_provider=lambda: {"sys_mode": "normal"},
        input_runtime_ports=_input_runtime_ports(
            _FakeIntentEngine(),
            thalamus_agent=fake_thalamus,
        ),
        cortex_orchestrator_sse_generator=(
            _fake_cortex_orchestrator_sse_generator
        ),
    )

    body_iter = response.body_iterator
    await body_iter.__anext__()
    await body_iter.__anext__()

    assert fake_thalamus.calls == 1
    assert fake_thalamus.last_kwargs["attachments"][0]["type"] == "image"
    assert fake_thalamus.last_kwargs["multimodal_observations"]["artifact_type"] == "screenshot"


@pytest.mark.asyncio
async def test_handle_chat_normalizes_surface_memory_recall_before_governor(monkeypatch) -> None:
    class _MemoryRecallThalamus(_FakeThalamusAgent):
        async def classify_and_compile_intent_draft(self, **kwargs) -> dict[str, Any]:  # noqa: ANN003
            self.calls += 1
            self.last_kwargs = dict(kwargs)
            request_id = str(kwargs.get("request_id") or "req-test")
            user_message = str(kwargs.get("user_message") or "")
            session_id = str(kwargs.get("session_id") or f"session::{request_id}")
            return {
                "schema_version": "Natsume.intent.v1",
                "id": f"intent::{request_id}",
                "ts_ms": 1778939000000,
                "session_id": session_id,
                "seq": 1,
                "person_id": "person-test",
                "type": "query",
                "action": "memory_recall",
                "route": "skill",
                "target_id": "memory_recall",
                "target": "host",
                "task": user_message,
                "route_reason": "THALAMUS_RULE_MEMORY_RECALL",
                "reason_code": "THALAMUS_RULE_MEMORY_RECALL",
                "args": {"text": user_message},
                "nonce": f"nonce::{request_id}",
            }

    fake_thalamus = _MemoryRecallThalamus()
    intent_engine = _CapturingIntentEngine()

    async def _fake_cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        request_id = str(kwargs.get("request_id") or "req-test")
        yield f'data: {json.dumps({"phase": "COMMIT", "done": True, "decision": "APPROVE", "request_id": request_id}, ensure_ascii=False)}\n\n'

    response = await handle_chat(
        request_body=ChatRequest(
            message="我刚才让你记了什么？",
            surface_task_control={
                "task_mode": "memory_recall",
                "response_move": "recall_memory",
            },
        ),
        request=_build_request(),
        metrics=_DummyMetrics(),
        hard_state_provider=lambda: {"sys_mode": "normal"},
        input_runtime_ports=_input_runtime_ports(
            intent_engine,
            thalamus_agent=fake_thalamus,
        ),
        cortex_orchestrator_sse_generator=(
            _fake_cortex_orchestrator_sse_generator
        ),
    )

    body_iter = response.body_iterator
    await body_iter.__anext__()
    await body_iter.__anext__()

    assert intent_engine.raw_intents
    raw_intent = intent_engine.raw_intents[0]
    assert raw_intent["route"] == "chat"
    assert raw_intent["target_id"] == ""
    assert raw_intent["action"] == "exec_intent"
    assert raw_intent["args"] == {"text": "我刚才让你记了什么？"}
    assert raw_intent["reason_code"] == "SURFACE_MAIN_CHAIN_MEMORY_RECALL"
    assert raw_intent["_surface_route_override"]["target_id"] == "memory_recall"


@pytest.mark.asyncio
async def test_handle_chat_normalizes_thalamus_memory_recall_without_surface_control(monkeypatch) -> None:
    class _MemoryRecallThalamus(_FakeThalamusAgent):
        async def classify_and_compile_intent_draft(self, **kwargs) -> dict[str, Any]:  # noqa: ANN003
            self.calls += 1
            request_id = str(kwargs.get("request_id") or "req-test")
            user_message = str(kwargs.get("user_message") or "")
            session_id = str(kwargs.get("session_id") or f"session::{request_id}")
            return {
                "schema_version": "Natsume.intent.v1",
                "id": f"intent::{request_id}",
                "ts_ms": 1778939000000,
                "session_id": session_id,
                "seq": 1,
                "person_id": "person-test",
                "type": "query",
                "action": "memory_recall",
                "route": "skill",
                "target_id": "memory_recall",
                "target": "host",
                "task": user_message,
                "route_reason": "THALAMUS_RULE_MEMORY_RECALL",
                "reason_code": "THALAMUS_RULE_MEMORY_RECALL",
                "args": {"query": user_message, "text": user_message, "user_message": user_message},
                "nonce": f"nonce::{request_id}",
            }

    fake_thalamus = _MemoryRecallThalamus()
    intent_engine = _CapturingIntentEngine()

    async def _fake_cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        request_id = str(kwargs.get("request_id") or "req-test")
        yield f'data: {json.dumps({"phase": "COMMIT", "done": True, "decision": "APPROVE", "request_id": request_id}, ensure_ascii=False)}\n\n'

    response = await handle_chat(
        request_body=ChatRequest(message="还记得我说推荐格式要怎样吗？"),
        request=_build_request(),
        metrics=_DummyMetrics(),
        hard_state_provider=lambda: {"sys_mode": "normal"},
        input_runtime_ports=_input_runtime_ports(
            intent_engine,
            thalamus_agent=fake_thalamus,
        ),
        cortex_orchestrator_sse_generator=(
            _fake_cortex_orchestrator_sse_generator
        ),
    )

    body_iter = response.body_iterator
    await body_iter.__anext__()
    await body_iter.__anext__()

    raw_intent = intent_engine.raw_intents[0]
    assert raw_intent["route"] == "chat"
    assert raw_intent["target_id"] == ""
    assert raw_intent["action"] == "exec_intent"
    assert raw_intent["args"] == {"text": "还记得我说推荐格式要怎样吗？"}
    assert raw_intent["reason_code"] == "SURFACE_MAIN_CHAIN_MEMORY_RECALL"
    assert raw_intent["_surface_route_override"]["reason_code"] == "THALAMUS_RULE_MEMORY_RECALL"


def test_chat_settings_are_typed_and_resolved_without_module_reload(monkeypatch) -> None:
    monkeypatch.setenv("HOST_ACTION_ARBITER_ENABLED", "true")
    monkeypatch.setenv("HOST_MATEENGINE_CHAT_BUBBLE_MAX_CHARS", "64")
    monkeypatch.setenv("HOST_MATEENGINE_MEMORY_RECALL_CUE_TEXT", "正在找记忆")

    assert chat_handler_module._action_arbiter_enabled() is True
    assert chat_handler_module._mateengine_bubble_text_limit() == 64
    assert chat_handler_module._mateengine_state_cue_text("memory_recall") == "正在找记忆"


@pytest.mark.asyncio
async def test_evaluate_governor_with_timing_stays_on_ledger_owner_thread(monkeypatch) -> None:
    engine = _FakeIntentEngine()
    scheduled: list[Any] = []

    async def _fake_to_thread(function):  # noqa: ANN001
        scheduled.append(function)
        return function()

    monkeypatch.setattr(chat_handler_module.asyncio, "to_thread", _fake_to_thread)

    result, timing = await chat_handler_module._evaluate_governor_with_timing(
        engine=engine,
        intent_draft={"route": "chat"},
        request_id="req-governor-timing",
        thread_id="thread-1",
        governance_snapshot={},
    )

    assert result["decision"] == "APPROVE"
    assert scheduled == []
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
