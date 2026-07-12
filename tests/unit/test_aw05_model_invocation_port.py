from __future__ import annotations

import inspect

from host_brain.app import dependencies
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


def test_invocation_port_exposes_no_service_or_attempt_budget_override() -> None:
    for function in (
        compiled_prompt_request,
        complete_compiled_prompt,
        complete_compiled_prompt_sync,
    ):
        parameters = inspect.signature(function).parameters
        assert "service" not in parameters
        assert "model_attempt_service" not in parameters
        assert "max_attempts" not in parameters


def test_sync_invocation_uses_only_the_reset_process_composition() -> None:
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
    dependencies.reset_model_runtime_composition(composition)
    try:
        result = complete_compiled_prompt_sync(
            prompt_id="surface.main_chain.live.text",
            messages=[{"role": "user", "content": "hello"}],
            request_id="req-reset-composition",
            scene_type="chat_surface",
            cognitive_tier="a",
            deadline_ms=500,
        )
    finally:
        dependencies.reset_model_runtime_composition()

    assert result.success is True
    assert result.content == "composition-result"
