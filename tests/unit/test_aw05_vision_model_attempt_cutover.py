from __future__ import annotations

import json

import pytest

from host_brain.app import chat_handler_vision as vision
from host_brain.app.vision import action_planner
from host_brain.model_runtime import (
    InMemoryAttemptAuditRepository,
    ModelAttemptService,
    ProviderResponse,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    VISION_ACTION_PLANNER_PROMPT_ID,
    VISION_UI_ANALYSIS_PROMPT_ID,
    build_live_production_prompt_registry,
)
from host_brain.model_runtime.provider_adapters import ProviderAdapterRegistry


class _VisionAdapter:
    adapter_id = "test.vision"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.requests = []

    async def complete(self, request):  # noqa: ANN001, ANN202
        self.requests.append(request)
        return ProviderResponse(content=self.responses.pop(0))

    async def stream(self, request):  # noqa: ANN001
        response = await self.complete(request)
        yield response.content


def _install_service(monkeypatch, responses: list[str]):  # noqa: ANN001, ANN202
    registry = build_live_production_prompt_registry()
    adapter = _VisionAdapter(responses)
    route = RouteDecision(
        decision_id="route-vision-test",
        targets=(
            ProviderTarget(
                adapter_id=adapter.adapter_id,
                provider="test",
                model_id="vision-model",
                timeout_ms=5_000,
            ),
        ),
        reason="unit_test",
        max_attempts=2,
    )
    service = ModelAttemptService(
        prompt_registry=registry,
        route_policy=StaticRoutePolicy(route),
        provider_adapters=ProviderAdapterRegistry((adapter,)),
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    for module in (vision, action_planner):
        monkeypatch.setattr(module, "get_prompt_registry", lambda: registry)
        monkeypatch.setattr(module, "get_model_attempt_service", lambda: service)
    return adapter


@pytest.mark.asyncio
async def test_vision_narrative_ignores_forged_provider_and_records_attempt_trace(monkeypatch) -> None:
    adapter = _install_service(monkeypatch, ["这是一个登录界面。"])

    result = await vision._call_vision_llm(
        request_id="req-vision-aw05",
        user_message="这个界面是什么？",
        vision_context={"screen_description": "用户名与密码输入框"},
        llm_runtime={
            "provider_name": "forged",
            "model_id": "forged-model",
            "llm_api_key": "secret",
            "provider_fn": lambda _payload: (_ for _ in ()).throw(AssertionError("must not run")),
            "available": True,
            "timeout_ms": 5_000,
        },
        ts_ms=1_800_000_000_000,
    )

    assert result["status"] == "OK"
    assert result["narrative"] == "这是一个登录界面。"
    assert result["provider"] == "test"
    assert result["model_used"] == "vision-model"
    assert result["model_attempt"]["route_decision_id"] == "route-vision-test"
    request = adapter.requests[0]
    assert request.prompt.prompt_id == VISION_UI_ANALYSIS_PROMPT_ID
    assert request.prompt.version == "v1.0.0"
    assert request.scene_type == "VISION_UI"


@pytest.mark.asyncio
async def test_vision_action_planner_uses_registered_json_attempt(monkeypatch) -> None:
    adapter = _install_service(
        monkeypatch,
        [
            json.dumps(
                {
                    "goal_understood": "点击登录",
                    "visual_summary": "看到了登录按钮",
                    "confidence": 0.9,
                    "actions": [
                        {"type": "click", "target_desc": "登录按钮", "x": 100, "y": 200},
                        {"type": "done", "target_desc": "完成"},
                    ],
                },
                ensure_ascii=False,
            )
        ],
    )

    plan = await action_planner.generate_action_plan(
        request_id="req-action-aw05",
        session_id="session-action-aw05",
        goal="点击登录按钮",
        vision_context={"screen_description": "登录按钮位于右下角"},
        llm_runtime={
            "provider_name": "forged",
            "llm_api_key": "secret",
            "available": True,
            "timeout_ms": 5_000,
        },
    )

    assert [action.type.value for action in plan.actions] == ["click", "done"]
    assert plan.extra["model_attempt"]["route_decision_id"] == "route-vision-test"
    request = adapter.requests[0]
    assert request.prompt.prompt_id == VISION_ACTION_PLANNER_PROMPT_ID
    assert request.prompt.version == "v1.0.0"
    assert request.scene_type == "VISION_ACTION_PLANNER"

