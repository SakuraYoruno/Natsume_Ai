from __future__ import annotations

import inspect

from host_brain.model_runtime import (
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    PromptRef,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    build_live_production_prompt_registry,
    build_model_runtime_composition,
    compiled_prompt_request,
    complete_compiled_prompt,
    complete_compiled_prompt_sync,
)


def test_compiled_prompt_port_exposes_no_route_or_secret_override() -> None:
    request = compiled_prompt_request(
        prompt_id="surface.main_chain.live.text",
        messages=[{"role": "user", "content": "hello"}],
        request_id="req-port",
        scene_type="chat_surface",
        cognitive_tier="a",
        deadline_ms=500,
    )

    assert isinstance(request.prompt, PromptRef)
    assert request.prompt.prompt_id == "surface.main_chain.live.text"
    assert request.prompt.variables == {
        "messages": ({"role": "user", "content": "hello"},)
    }
    assert request.scene_type == "CHAT_SURFACE"
    assert request.metadata == {}
    assert not hasattr(request, "provider_name")
    assert not hasattr(request, "api_key")


def test_invocation_port_requires_explicit_service_without_attempt_budget_override() -> None:
    assert "attempt_service" not in inspect.signature(compiled_prompt_request).parameters
    for function in (complete_compiled_prompt, complete_compiled_prompt_sync):
        parameters = inspect.signature(function).parameters
        assert parameters["attempt_service"].default is inspect.Parameter.empty
        assert "max_attempts" not in parameters


def test_sync_invocation_uses_only_the_explicit_composed_service() -> None:
    composition = build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=StaticRoutePolicy(
            RouteDecision(
                "route-reset-composition",
                (ProviderTarget("test", "test", "model-test"),),
                "unit-test",
                max_attempts=1,
            )
        ),
        provider_adapters=[
            CallableProviderAdapter("test", complete_fn=lambda _: "composition-result")
        ],
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    result = complete_compiled_prompt_sync(
        attempt_service=composition.attempt_service,
        prompt_id="surface.main_chain.live.text",
        messages=[{"role": "user", "content": "hello"}],
        request_id="req-explicit-composition",
        scene_type="chat_surface",
        cognitive_tier="a",
        deadline_ms=500,
    )

    assert result.success is True
    assert result.content == "composition-result"
