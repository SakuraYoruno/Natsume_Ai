from __future__ import annotations

import json
import inspect

from host_brain.model_runtime import (
    InMemoryAttemptAuditRepository,
    ModelAttemptService,
    PERSONA_INNER_ACTIVITY_PROMPT_ID,
    ProviderResponse,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    build_live_production_prompt_registry,
)
from host_brain.model_runtime.provider_adapters import ProviderAdapterRegistry
from host_brain.persona import inner_activity_agent as agent


class _InnerAdapter:
    adapter_id = "test.inner"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.requests = []

    async def complete(self, request):  # noqa: ANN001, ANN202
        self.requests.append(request)
        return ProviderResponse(content=self.responses.pop(0))

    async def stream(self, request):  # noqa: ANN001
        response = await self.complete(request)
        yield response.content


def _install_service(responses: list[str]) -> _InnerAdapter:
    registry = build_live_production_prompt_registry()
    adapter = _InnerAdapter(responses)
    route = RouteDecision(
        decision_id="route-inner-test",
        targets=(
            ProviderTarget(
                adapter_id=adapter.adapter_id,
                provider="test",
                model_id="inner-model",
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
    adapter.service = service
    adapter.prompt_registry = registry
    return adapter


def _call(adapter: _InnerAdapter) -> str:
    return agent._run_inner_activity_llm(
        timeout_ms=5_000,
        prompt_text='{"goal":"generate_inner_activity_note"}',
        system_prompt="return json",
        model_attempt_service=adapter.service,
        prompt_registry=adapter.prompt_registry,
    )


def test_inner_activity_port_exposes_no_provider_or_cache_authority() -> None:
    parameters = set(inspect.signature(agent._run_inner_activity_llm).parameters)

    assert parameters == {
        "timeout_ms",
        "prompt_text",
        "system_prompt",
        "model_attempt_sink",
        "retry_evaluator",
        "model_attempt_service",
        "prompt_registry",
    }


def test_inner_activity_uses_registered_prompt_and_service_owned_json_repair(monkeypatch) -> None:
    adapter = _install_service(
        [
            "not-json",
            json.dumps({"note": "封面错位的画面还卡在眼前。", "focus": "memory"}, ensure_ascii=False),
        ],
    )

    raw = _call(adapter)

    assert "封面错位" in raw
    assert len(adapter.requests) == 2
    assert adapter.requests[0].prompt.prompt_id == PERSONA_INNER_ACTIVITY_PROMPT_ID
    assert adapter.requests[0].prompt.version == "v1.0.0"
    assert adapter.requests[1].retry_reason == "output_schema_repair"
    assert all(request.target.provider == "test" for request in adapter.requests)


def test_inner_activity_does_not_keep_cross_request_direct_cache(monkeypatch) -> None:
    payload = json.dumps({"note": "早安已经错过钟点了。", "focus": "self"}, ensure_ascii=False)
    adapter = _install_service([payload, payload])

    assert _call(adapter) == _call(adapter)
    assert len(adapter.requests) == 2


def test_inner_activity_freshness_retry_is_owned_by_one_model_request(monkeypatch) -> None:
    duplicate = json.dumps({"note": "重复念头", "focus": "self"}, ensure_ascii=False)
    fresh = json.dumps({"note": "窗边那张错位封面还卡在眼前。", "focus": "memory"}, ensure_ascii=False)
    adapter = _install_service([duplicate, fresh])
    monkeypatch.setenv("HOST_INNER_ACTIVITY_LLM_ENABLED", "true")
    monkeypatch.setenv("NATSUME_INNER_WORLD_LLM_ENABLED", "true")
    monkeypatch.setattr(
        agent,
        "_build_inner_activity_context",
        lambda **_kwargs: {
            "recent_inner_events": [{"note": "重复念头。"}],
            "offline_state": {"offline_hours": 0.0, "absence_band": "recent"},
        },
    )
    monkeypatch.setattr(
        agent,
        "build_inner_life_source_packet",
        lambda **_kwargs: {"selected": {"seed": "错位封面", "thought_form": "scene_notice"}},
    )
    monkeypatch.setattr(agent, "_score_inner_activity_note", lambda **_kwargs: {"score": 1.0, "flags": []})
    monkeypatch.setattr(agent, "normalize_inner_life_trace", lambda *_args, **_kwargs: {"ok": True})

    note, focus, reason, meta = agent.generate_inner_activity_note(
        persona_user_id="person-aw05",
        absence_band="recent",
        offline_hours=0.0,
        inner_state={},
        now_ms=123,
        model_attempt_service=adapter.service,
        prompt_registry=adapter.prompt_registry,
    )

    assert note == "窗边那张错位封面还卡在眼前。"
    assert focus == "memory"
    assert reason == ""
    assert len(adapter.requests) == 2
    assert {request.request_id for request in adapter.requests} == {adapter.requests[0].request_id}
    assert adapter.requests[1].retry_reason == "INNER_ACTIVITY_LLM_DUPLICATE"
    assert [row["status"] for row in meta["model_attempt"]["attempts"]] == ["rejected", "succeeded"]
