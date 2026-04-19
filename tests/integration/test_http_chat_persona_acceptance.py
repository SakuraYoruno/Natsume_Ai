from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient

import host_brain.app.fastapi_app as fastapi_app
from host_brain.app.chat_agent import (
    SubAgentContext,
    _build_chat_agent_quality_fallback_reply,
    render_quick_gateway_progress_text,
)
from host_brain.app.intelligent_runtime import apply_response_intelligence
from host_brain.reasoning.reflection_chain import ReflectionChain
from host_brain.tribe.thalamus_agent import ThalamusAgent
from tests.text_quality_assertions import assert_natsume_style


class _FakeIntentEngine:
    def evaluate_intent(self, *args, **kwargs) -> dict[str, Any]:  # noqa: ANN002, ANN003
        request_id = str(kwargs.get("request_id") or "req-test")
        raw_intent = dict(kwargs.get("raw_intent") or {})
        return {
            "decision": "APPROVE",
            "reason_code": None,
            "intent_id": raw_intent.get("id") or f"intent::{request_id}",
            "tokens": {"approval_token_ref": f"approval::{request_id}"},
        }


class _DummyMetaLearning:
    def record(self, *_args, **_kwargs) -> None:
        return None


@dataclass
class _DummyItem:
    content: str = ""
    analysis: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"content": self.content, "analysis": self.analysis}


@dataclass
class _DummyEnhanceResult:
    suggested_emoji: str = ""
    items: list[_DummyItem] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "suggested_emoji": self.suggested_emoji,
            "items": [item.as_dict() for item in self.items],
        }


class _DummyMultimodalProcessor:
    def enhance_output(
        self,
        *,
        text: str,
        emotion_tag: str,
        bond_stage: str,
        render_lang: str,
    ) -> _DummyEnhanceResult:
        _ = (text, emotion_tag, bond_stage, render_lang)
        return _DummyEnhanceResult()


def _allow_immune_gate(_body) -> dict[str, object]:  # noqa: ANN001
    return {
        "decision": "ALLOW",
        "risk_level": "none",
        "reason_codes": [],
        "score": 0.0,
    }


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _parse_sse_events(sse_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in str(sse_text or "").splitlines():
        if not line.startswith("data: "):
            continue
        try:
            event = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _patch_response_intelligence_dependencies(monkeypatch) -> None:
    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "get_meta_learning_loop", lambda: _DummyMetaLearning())
    monkeypatch.setattr(deps, "get_multimodal_processor", lambda: _DummyMultimodalProcessor())
    monkeypatch.setattr(deps, "get_reflection_chain", lambda: ReflectionChain())


def _build_client(monkeypatch, *, route_decision: dict[str, Any], orchestrator_builder):
    _patch_response_intelligence_dependencies(monkeypatch)
    monkeypatch.setattr(
        "host_brain.app.fastapi_app._evaluate_immune_gate_for_chat",
        _allow_immune_gate,
    )
    monkeypatch.setattr(
        "host_brain.app.chat_handler.get_intent_engine",
        lambda: _FakeIntentEngine(),
    )

    canonical_thalamus = ThalamusAgent()

    class _FakeThalamusAgent:
        async def classify_and_compile_intent_draft(self, **kwargs) -> dict[str, Any]:  # noqa: ANN003
            return canonical_thalamus.compile_intent_draft(
                request_id=str(kwargs.get("request_id") or "req-test"),
                session_id=str(kwargs.get("session_id") or "session-test"),
                user_message=str(kwargs.get("user_message") or ""),
                person_id=kwargs.get("person_id"),
                raw_route_decision=dict(route_decision),
                existing_intent_draft=kwargs.get("existing_intent_draft"),
                source=str(kwargs.get("source") or "chat_handler"),
                input_source=kwargs.get("input_source"),
                ts_ms=kwargs.get("ts_ms"),
            )

    monkeypatch.setattr(
        "host_brain.app.chat_handler.get_thalamus_agent",
        lambda: _FakeThalamusAgent(),
    )
    monkeypatch.setattr(
        "host_brain.app.cortex_orchestrator.cortex_orchestrator_sse_generator",
        orchestrator_builder,
    )
    return TestClient(fastapi_app.app, raise_server_exceptions=False)


def test_v1_chat_chat_route_commit_text_stays_in_natsume_voice(monkeypatch) -> None:
    async def _fake_cortex_orchestrator_sse_generator(  # noqa: ANN001
        *,
        request_body,
        request_id: str,
        intent_draft: dict[str, Any],
        emotion_context: dict[str, Any],
        **_kwargs,
    ):
        assert intent_draft["route"] == "chat"
        assert intent_draft["action"] == "narrative_only"
        yield _sse(
            {
                "phase": "EXECUTIVE_ROUTE",
                "request_id": request_id,
                "route_type": "chat",
                "target_id": None,
                "reason_code": intent_draft["route_reason"],
            }
        )
        narrative, _meta = apply_response_intelligence(
            request_id=request_id,
            user_message=request_body.message,
            narrative="您好，作为一个AI，我建议您冷静一下。",
            emotion_context=emotion_context,
            bond_summary={"stage": "STRANGER"},
            render_lang="zh",
            allow_rewrite=True,
        )
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "request_id": request_id,
                "narrative": narrative,
                "audit_refs": {"path": "test:http_chat_persona:chat"},
            }
        )

    client = _build_client(
        monkeypatch,
        route_decision={
            "route": "chat",
            "target_id": "",
            "task": "陪伴安慰",
            "reason_code": "TEST_CHAT_ROUTE",
            "route_source": "test",
        },
        orchestrator_builder=_fake_cortex_orchestrator_sse_generator,
    )
    try:
        response = client.post(
            "/v1/chat",
            json={
                "message": "我今天很难过",
                "emotion_tag": "sadness",
                "emotion_intensity": 0.86,
                "mood_state": "fragile",
            },
        )
    finally:
        client.close()

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
    commit_event = next(event for event in events if event.get("phase") == "COMMIT")

    assert route_event["route_type"] == "chat"
    assert commit_event["decision"] == "APPROVE"
    assert_natsume_style(
        user_message="我今天很难过",
        reply=str(commit_event["narrative"]),
        emotion_tag="sadness",
        bond_stage="STRANGER",
    )


def test_v1_chat_delegate_route_commit_text_stays_in_natsume_voice(monkeypatch) -> None:
    async def _fake_cortex_orchestrator_sse_generator(  # noqa: ANN001
        *,
        request_body,
        request_id: str,
        intent_draft: dict[str, Any],
        **_kwargs,
    ):
        assert intent_draft["route"] == "delegate"
        assert intent_draft["target_id"] == "intent_agent"
        yield _sse(
            {
                "phase": "EXECUTIVE_ROUTE",
                "request_id": request_id,
                "route_type": "delegate",
                "target_id": "intent_agent",
                "reason_code": intent_draft["route_reason"],
            }
        )
        narrative = _build_chat_agent_quality_fallback_reply(
            user_message=request_body.message,
            sub_agent_ctx=SubAgentContext(
                agent_id="intent_agent",
                task="summarize next steps",
                result_text='[DELEGATE:intent_agent] {"narrative":{"text":"整理好了，重点是先检查配置，再重试。"}}',
                success=True,
            ),
            bond_summary={},
            emotion_context={},
            render_target_lang="zh",
        )
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "request_id": request_id,
                "narrative": narrative,
                "audit_refs": {"path": "test:http_chat_persona:delegate"},
            }
        )

    client = _build_client(
        monkeypatch,
        route_decision={
            "route": "delegate",
            "target_id": "intent_agent",
            "task": "整理下一步建议",
            "reason_code": "TEST_DELEGATE_ROUTE",
            "route_source": "test",
        },
        orchestrator_builder=_fake_cortex_orchestrator_sse_generator,
    )
    try:
        response = client.post("/v1/chat", json={"message": "帮我整理一下接下来该怎么做"})
    finally:
        client.close()

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
    commit_event = next(event for event in events if event.get("phase") == "COMMIT")

    assert route_event["route_type"] == "delegate"
    assert route_event["target_id"] == "intent_agent"
    assert "整理好了" in str(commit_event["narrative"])
    assert_natsume_style(
        user_message="帮我整理一下接下来该怎么做",
        reply=str(commit_event["narrative"]),
        emotion_tag="neutral",
        bond_stage="STRANGER",
    )


def test_v1_chat_plan_route_commit_text_stays_in_natsume_voice(monkeypatch) -> None:
    async def _fake_cortex_orchestrator_sse_generator(  # noqa: ANN001
        *,
        request_body,
        request_id: str,
        intent_draft: dict[str, Any],
        **_kwargs,
    ):
        assert intent_draft["route"] == "plan"
        yield _sse(
            {
                "phase": "EXECUTIVE_ROUTE",
                "request_id": request_id,
                "route_type": "plan",
                "target_id": "plan_execution",
                "reason_code": intent_draft["route_reason"],
            }
        )
        narrative = _build_chat_agent_quality_fallback_reply(
            user_message=request_body.message,
            sub_agent_ctx=SubAgentContext(
                agent_id="plan_execution",
                task="整理今晚安排",
                result_text="",
                success=True,
                payload={
                    "steps": [
                        {
                            "step_index": 1,
                            "success": True,
                            "target_id": "intent_agent",
                            "description": "先把今晚要做的事分成三段",
                            "result_text": "已经整理成工作、休息和收尾三段。",
                        },
                        {
                            "step_index": 2,
                            "success": True,
                            "target_id": "calendar",
                            "description": "给每段分配时间",
                            "result_text": "时间已经排开，也留了缓冲。",
                        },
                    ],
                    "total_steps": 2,
                    "parallel_enabled": False,
                    "success_count": 2,
                },
            ),
            bond_summary={},
            emotion_context={},
            render_target_lang="zh",
        )
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "request_id": request_id,
                "narrative": narrative,
                "audit_refs": {"path": "test:http_chat_persona:plan"},
            }
        )

    client = _build_client(
        monkeypatch,
        route_decision={
            "route": "plan",
            "target_id": "plan_execution",
            "task": "整理今晚安排",
            "reason_code": "TEST_PLAN_ROUTE",
            "route_source": "test",
        },
        orchestrator_builder=_fake_cortex_orchestrator_sse_generator,
    )
    try:
        response = client.post("/v1/chat", json={"message": "帮我规划一下今晚的安排"})
    finally:
        client.close()

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
    commit_event = next(event for event in events if event.get("phase") == "COMMIT")

    assert route_event["route_type"] == "plan"
    assert route_event["target_id"] == "plan_execution"
    assert "第1步" in str(commit_event["narrative"])
    assert_natsume_style(
        user_message="帮我规划一下今晚的安排",
        reply=str(commit_event["narrative"]),
        emotion_tag="neutral",
        bond_stage="STRANGER",
    )


def test_v1_chat_recent_turn_route_commit_text_stays_in_natsume_voice(monkeypatch) -> None:
    async def _fake_cortex_orchestrator_sse_generator(  # noqa: ANN001
        *,
        request_body,
        request_id: str,
        intent_draft: dict[str, Any],
        **_kwargs,
    ):
        assert intent_draft["route"] == "recent_turn"
        yield _sse(
            {
                "phase": "EXECUTIVE_ROUTE",
                "request_id": request_id,
                "route_type": "recent_turn",
                "target_id": "",
                "reason_code": intent_draft["route_reason"],
            }
        )
        narrative = _build_chat_agent_quality_fallback_reply(
            user_message=request_body.message,
            sub_agent_ctx=SubAgentContext(
                agent_id="recent_turn_direct",
                task="回溯上一轮",
                result_text="你刚刚是在问我要不要帮你把今晚的安排也一起顺好。",
                success=True,
            ),
            bond_summary={},
            emotion_context={},
            render_target_lang="zh",
        )
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "request_id": request_id,
                "narrative": narrative,
                "audit_refs": {"path": "test:http_chat_persona:recent_turn"},
            }
        )

    client = _build_client(
        monkeypatch,
        route_decision={
            "route": "recent_turn",
            "target_id": "",
            "task": "回溯上一轮",
            "reason_code": "TEST_RECENT_TURN_ROUTE",
            "route_source": "test",
        },
        orchestrator_builder=_fake_cortex_orchestrator_sse_generator,
    )
    try:
        response = client.post("/v1/chat", json={"message": "你刚刚说了什么？"})
    finally:
        client.close()

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
    commit_event = next(event for event in events if event.get("phase") == "COMMIT")

    assert route_event["route_type"] == "recent_turn"
    assert "你刚刚" in str(commit_event["narrative"])
    assert_natsume_style(
        user_message="你刚刚说了什么？",
        reply=str(commit_event["narrative"]),
        emotion_tag="neutral",
        bond_stage="STRANGER",
    )


def test_v1_chat_skill_route_commit_text_stays_in_natsume_voice(monkeypatch) -> None:
    async def _fake_cortex_orchestrator_sse_generator(  # noqa: ANN001
        *,
        request_body,
        request_id: str,
        intent_draft: dict[str, Any],
        **_kwargs,
    ):
        assert intent_draft["route"] == "skill"
        assert intent_draft["target_id"] == "memory_store"
        yield _sse(
            {
                "phase": "EXECUTIVE_ROUTE",
                "request_id": request_id,
                "route_type": "skill",
                "target_id": "memory_store",
                "reason_code": intent_draft["route_reason"],
            }
        )
        narrative = _build_chat_agent_quality_fallback_reply(
            user_message=request_body.message,
            sub_agent_ctx=SubAgentContext(
                agent_id="memory_store",
                task="记住偏好",
                result_text="记好了，我会记着你周五晚上通常有空。",
                success=True,
            ),
            bond_summary={},
            emotion_context={},
            render_target_lang="zh",
        )
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "request_id": request_id,
                "narrative": narrative,
                "audit_refs": {"path": "test:http_chat_persona:skill"},
            }
        )

    client = _build_client(
        monkeypatch,
        route_decision={
            "route": "skill",
            "target_id": "memory_store",
            "task": "记住偏好",
            "reason_code": "TEST_SKILL_ROUTE",
            "route_source": "test",
        },
        orchestrator_builder=_fake_cortex_orchestrator_sse_generator,
    )
    try:
        response = client.post("/v1/chat", json={"message": "记住我周五晚上一般有空"})
    finally:
        client.close()

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
    commit_event = next(event for event in events if event.get("phase") == "COMMIT")

    assert route_event["route_type"] == "skill"
    assert route_event["target_id"] == "memory_store"
    assert "记好了" in str(commit_event["narrative"])
    assert_natsume_style(
        user_message="记住我周五晚上一般有空",
        reply=str(commit_event["narrative"]),
        emotion_tag="neutral",
        bond_stage="STRANGER",
    )


def test_v1_chat_vision_delegate_commit_text_stays_in_natsume_voice(monkeypatch) -> None:
    async def _fake_cortex_orchestrator_sse_generator(  # noqa: ANN001
        *,
        request_body,
        request_id: str,
        intent_draft: dict[str, Any],
        **_kwargs,
    ):
        assert intent_draft["route"] == "delegate"
        assert intent_draft["target_id"] == "vision_agent"
        yield _sse(
            {
                "phase": "EXECUTIVE_ROUTE",
                "request_id": request_id,
                "route_type": "delegate",
                "target_id": "vision_agent",
                "reason_code": intent_draft["route_reason"],
            }
        )
        narrative = _build_chat_agent_quality_fallback_reply(
            user_message=request_body.message,
            sub_agent_ctx=SubAgentContext(
                agent_id="vision_agent",
                task="解释登录界面",
                result_text="这个界面是登录页，先输账号和密码，再点登录按钮就好。",
                success=True,
            ),
            bond_summary={},
            emotion_context={},
            render_target_lang="zh",
        )
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "request_id": request_id,
                "narrative": narrative,
                "audit_refs": {"path": "test:http_chat_persona:vision_delegate"},
            }
        )

    client = _build_client(
        monkeypatch,
        route_decision={
            "route": "delegate",
            "target_id": "vision_agent",
            "task": "解释登录界面",
            "reason_code": "TEST_VISION_ROUTE",
            "route_source": "test",
        },
        orchestrator_builder=_fake_cortex_orchestrator_sse_generator,
    )
    try:
        response = client.post("/v1/chat", json={"message": "帮我看看这个登录界面怎么操作"})
    finally:
        client.close()

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
    commit_event = next(event for event in events if event.get("phase") == "COMMIT")

    assert route_event["route_type"] == "delegate"
    assert route_event["target_id"] == "vision_agent"
    assert "登录页" in str(commit_event["narrative"])
    assert_natsume_style(
        user_message="帮我看看这个登录界面怎么操作",
        reply=str(commit_event["narrative"]),
        emotion_tag="neutral",
        bond_stage="STRANGER",
    )


def test_v1_chat_task_progress_route_commit_text_stays_in_natsume_voice(monkeypatch) -> None:
    async def _fake_cortex_orchestrator_sse_generator(  # noqa: ANN001
        *,
        request_id: str,
        intent_draft: dict[str, Any],
        **_kwargs,
    ):
        assert intent_draft["route"] == "task_progress"
        yield _sse(
            {
                "phase": "EXECUTIVE_ROUTE",
                "request_id": request_id,
                "route_type": "task_progress",
                "target_id": None,
                "reason_code": intent_draft["route_reason"],
            }
        )
        narrative = render_quick_gateway_progress_text(
            status="FAILED",
            target_id="翻译任务",
            progress_message="网关波动",
            elapsed_ms=12_300,
            bond_stage="STRANGER",
            emotion_tag="NEUTRAL",
        )
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "request_id": request_id,
                "narrative": narrative,
                "audit_refs": {"path": "test:http_chat_persona:task_progress"},
            }
        )

    client = _build_client(
        monkeypatch,
        route_decision={
            "route": "task_progress",
            "target_id": "",
            "task": "查询当前任务进度",
            "reason_code": "TEST_TASK_PROGRESS_ROUTE",
            "route_source": "test",
        },
        orchestrator_builder=_fake_cortex_orchestrator_sse_generator,
    )
    try:
        response = client.post("/v1/chat", json={"message": "上个任务进度到哪了"})
    finally:
        client.close()

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    route_event = next(event for event in events if event.get("phase") == "EXECUTIVE_ROUTE")
    commit_event = next(event for event in events if event.get("phase") == "COMMIT")

    assert route_event["route_type"] == "task_progress"
    assert any(
        marker in str(commit_event["narrative"])
        for marker in ("遇到了点问题", "出了点岔子")
    )
    assert_natsume_style(
        user_message="上个任务进度到哪了",
        reply=str(commit_event["narrative"]),
        emotion_tag="neutral",
        bond_stage="STRANGER",
        required_markers=("我",),
    )


def test_v1_chat_locked_mode_commit_text_stays_in_natsume_voice(monkeypatch) -> None:
    monkeypatch.setenv("HOST_SYS_MODE", "locked")
    monkeypatch.setattr(
        "host_brain.app.fastapi_app._evaluate_immune_gate_for_chat",
        _allow_immune_gate,
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    try:
        response = client.post("/v1/chat", json={"message": "你好"})
    finally:
        client.close()

    assert response.status_code == 200
    commit_event = next(
        event for event in _parse_sse_events(response.text) if event.get("phase") == "COMMIT"
    )

    assert commit_event["decision"] == "REJECT"
    assert commit_event["reason_code"] == "SYS_MODE_LOCKED"
    assert_natsume_style(
        user_message="你好",
        reply=str(commit_event["narrative"]),
        emotion_tag="neutral",
        bond_stage="STRANGER",
    )


def test_v1_chat_maintenance_mode_commit_text_stays_in_natsume_voice(monkeypatch) -> None:
    monkeypatch.setenv("HOST_SYS_MODE", "maintenance")
    monkeypatch.setattr(
        "host_brain.app.fastapi_app._evaluate_immune_gate_for_chat",
        _allow_immune_gate,
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    try:
        response = client.post("/v1/chat", json={"message": "你好"})
    finally:
        client.close()

    assert response.status_code == 200
    commit_event = next(
        event for event in _parse_sse_events(response.text) if event.get("phase") == "COMMIT"
    )

    assert commit_event["decision"] == "REJECT"
    assert commit_event["reason_code"] == "SYS_MODE_MAINTENANCE"
    assert_natsume_style(
        user_message="你好",
        reply=str(commit_event["narrative"]),
        emotion_tag="neutral",
        bond_stage="STRANGER",
    )
