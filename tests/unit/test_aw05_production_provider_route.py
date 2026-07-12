from __future__ import annotations

import asyncio

import pytest

from host_brain.model_runtime import (
    OutputContract,
    ModelAttemptRequest,
    ProductionProviderBinding,
    ProductionProviderConfigurationError,
    PromptRef,
    PromptRegistry,
    ProviderAttemptRequest,
    RouteNotConfiguredError,
    build_production_provider_registry,
    build_production_route_policy,
    route_decision_from_graph_runtime,
)
from host_brain.model_runtime.production_route_policy import (
    _apply_cognitive_tier_route,
    _cloud_runtime,
    _default_runtime_resolver,
)
from host_brain.model_runtime.production_provider_adapters import _default_credential_resolver


def _rendered_prompt():
    registry = PromptRegistry()
    registry.register_static("provider.test", "hello")
    return registry.render(PromptRef("provider.test"))


def _runtime(**overrides):
    value = {
        "available": True,
        "provider_name": "gemini",
        "model_id": "gemini-test",
        "timeout_ms": 3200,
        "route_reason_code": "TEST_ROUTE",
        "route_target": "cloud",
        "agent_gen_params": {"temperature": 0.2, "max_tokens": 64},
        "llm_api_key": "never-copy-this-secret",
        "provider_fn": object(),
    }
    value.update(overrides)
    return value


def test_graph_runtime_maps_to_one_stable_secret_free_target() -> None:
    first = route_decision_from_graph_runtime(
        _runtime(), scene_type="chat_surface", cognitive_tier="fast"
    )
    second = route_decision_from_graph_runtime(
        _runtime(), scene_type="chat_surface", cognitive_tier="fast"
    )

    assert first == second
    assert first.max_attempts == 1
    assert len(first.targets) == 1
    assert first.targets[0].adapter_id == "production.gemini"
    assert first.targets[0].metadata == {
        "generation_parameters": {"temperature": 0.2, "max_tokens": 64}
    }
    assert "never-copy-this-secret" not in repr(first)


def test_route_boundary_normalizes_sdk_max_output_tokens_alias() -> None:
    decision = route_decision_from_graph_runtime(
        _runtime(agent_gen_params={"temperature": 0.3, "max_output_tokens": 320}),
        scene_type="INNER_ACTIVITY",
        cognitive_tier="B",
    )

    assert decision.targets[0].metadata["generation_parameters"] == {
        "temperature": 0.3,
        "max_tokens": 320,
    }


def test_server_owned_tier_and_cloud_lanes_choose_route_without_request_hints(monkeypatch) -> None:
    monkeypatch.delenv("HOST_MODEL_ROUTE_SCENE_NATSUME_AGENT_TIER_A_MODEL_ID", raising=False)
    monkeypatch.delenv("HOST_MODEL_ROUTE_SCENE_NATSUME_AGENT_TIER_C_MODEL_ID", raising=False)
    base = _runtime(model_id="generic-model")

    tier_a = _apply_cognitive_tier_route(
        base,
        scene_type="NATSUME_AGENT",
        cognitive_tier="A",
    )
    tier_c = _apply_cognitive_tier_route(
        base,
        scene_type="NATSUME_AGENT",
        cognitive_tier="C",
    )
    cloud = _cloud_runtime(
        {
            **base,
            "cloud_provider_name": "gemini",
            "cloud_model_id": "gemini-vision",
            "cloud_timeout_ms": 9000,
            "cloud_available": True,
        }
    )

    assert tier_a["model_id"] == "gemini-2.5-pro"
    assert tier_c["model_id"] == "gemini-2.5-flash-lite"
    assert tier_a["model_id"] != tier_c["model_id"]
    assert cloud["model_id"] == "gemini-vision"
    assert cloud["timeout_ms"] == 9000


def test_default_runtime_resolver_uses_secret_free_subagent_catalog_lane(monkeypatch) -> None:
    monkeypatch.setenv("HOST_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_SUBAGENT_MODEL_ID", "deepseek-subagent")
    monkeypatch.setenv("HOST_SUBAGENT_TIMEOUT_MS", "6400")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-stay-in-adapter")
    request = ModelAttemptRequest(
        request_id="subagent-route",
        scene_type="SUBAGENT",
        cognitive_tier="B",
        prompt=PromptRef("provider.test"),
        deadline_ms=1000,
    )

    runtime = asyncio.run(_default_runtime_resolver(request, _rendered_prompt()))

    assert runtime["provider_name"] == "deepseek"
    assert runtime["model_id"] == "deepseek-subagent"
    assert runtime["timeout_ms"] == 6400
    assert runtime["route_target"] == "cloud"
    assert runtime["route_lane"] == "subagent"
    assert runtime["generation_parameters"]["max_tokens"] == 1200
    assert "must-stay-in-adapter" not in repr(runtime)


def test_production_route_policy_freezes_default_and_scene_routes() -> None:
    policy = build_production_route_policy(
        default_runtime=_runtime(model_id="default-model"),
        runtime_by_route={
            ("CHAT_SURFACE", "FAST"): _runtime(model_id="fast-model"),
        },
    )
    request = type("Request", (), {"scene_type": "CHAT_SURFACE", "cognitive_tier": "FAST"})()
    decision = asyncio.run(policy.resolve(request, _rendered_prompt()))
    assert decision.targets[0].model_id == "fast-model"


def test_route_mapping_fails_closed_for_unavailable_or_unknown_provider() -> None:
    with pytest.raises(RouteNotConfiguredError, match="unavailable"):
        route_decision_from_graph_runtime(
            _runtime(available=False), scene_type="CHAT", cognitive_tier="FAST"
        )
    with pytest.raises(ProductionProviderConfigurationError, match="unsupported"):
        route_decision_from_graph_runtime(
            _runtime(provider_name="mystery"), scene_type="CHAT", cognitive_tier="FAST"
        )


def test_injected_provider_callable_is_invoked_exactly_once() -> None:
    payloads = []

    async def _complete(payload):
        payloads.append(dict(payload))
        return {"content": "ok", "usage": {"output_tokens": 1}}

    registry = build_production_provider_registry(
        [
            ProductionProviderBinding(
                provider="gemini",
                complete=_complete,
                credential_available=True,
            )
        ]
    )
    target = route_decision_from_graph_runtime(
        _runtime(), scene_type="CHAT", cognitive_tier="FAST"
    ).targets[0]
    request = ProviderAttemptRequest(
        request_id="request-1",
        attempt_id="attempt-1",
        attempt_index=1,
        retry_reason="initial",
        scene_type="CHAT",
        cognitive_tier="FAST",
        prompt=_rendered_prompt(),
        target=target,
        timeout_ms=1000,
    )

    response = asyncio.run(registry.get("production.gemini").complete(request))
    assert response.content == "ok"
    assert len(payloads) == 1
    assert payloads[0]["model"] == "gemini-test"
    assert payloads[0]["messages"] == [{"role": "user", "content": "hello"}]
    assert "timeout_ms" not in payloads[0]
    assert "llm_api_key" not in payloads[0]


def test_provider_registry_requires_explicit_credential_status_without_leaking_secret() -> None:
    secret = "top-secret-value"

    def _bound_callable(_payload, _secret=secret):
        return "unused"

    with pytest.raises(ProductionProviderConfigurationError) as captured:
        build_production_provider_registry(
            [
                ProductionProviderBinding(
                    provider="deepseek",
                    complete=_bound_callable,
                    credential_available=False,
                )
            ]
        )
    assert secret not in str(captured.value)
    assert "deepseek" in str(captured.value)


def test_production_credential_resolver_does_not_cache_or_mutate_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "adapter-only-secret")
    before = dict(__import__("os").environ)

    resolved = _default_credential_resolver("openai")

    assert resolved == "adapter-only-secret"
    assert dict(__import__("os").environ) == before


@pytest.mark.parametrize("provider", ["openai", "openai_compatible", "gemini", "deepseek", "codex"])
def test_supported_provider_callable_bindings_have_stable_adapter_ids(provider: str) -> None:
    registry = build_production_provider_registry(
        [
            ProductionProviderBinding(
                provider=provider,
                complete=lambda _payload: "ok",
                credential_available=True,
            )
        ]
    )
    assert registry.ids() == (f"production.{provider}",)


def test_payload_preserves_output_contract_without_transport_policy() -> None:
    registry = PromptRegistry()
    registry.register_static(
        "provider.json",
        "return json",
        output_contract=OutputContract(kind="json_object"),
    )
    prompt = registry.render(PromptRef("provider.json"))
    assert prompt.output_contract.response_format() == {"type": "json_object"}
