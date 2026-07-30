from __future__ import annotations

import asyncio

import pytest

from host_brain.model_runtime import (
    OutputContract,
    ModelBudgetGuard,
    ProductionProviderBinding,
    ProductionProviderConfigurationError,
    PromptRef,
    PromptRegistry,
    ProviderAttemptRequest,
    ProviderResponse,
    ProviderStreamEvent,
    RouteNotConfiguredError,
    build_production_provider_registry,
    build_vault_backed_provider_registry,
    build_production_route_policy,
    production_provider_payload,
    route_decision_from_graph_runtime,
)
from host_brain.model_runtime.production_route_policy import (
    _apply_cognitive_tier_route,
    _cloud_runtime,
)
from host_brain.app.model_provider_composition import resolve_model_provider_credential


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


@pytest.mark.asyncio
async def test_explicit_production_stream_result_binding_requests_terminal_usage() -> None:
    payloads: list[dict[str, object]] = []

    async def _complete(_payload):  # noqa: ANN001
        return {"content": "unused"}

    async def _stream_with_result(payload):  # noqa: ANN001
        payloads.append(dict(payload))
        yield ProviderStreamEvent.delta("第一句。")
        yield ProviderStreamEvent.final(
            ProviderResponse(
                content="第一句。",
                finish_reason="stop",
                usage={
                    "total_tokens": 8,
                    "completion_tokens": 3,
                    "model_cost_micros": 12,
                },
            )
        )

    registry = build_production_provider_registry(
        [
            ProductionProviderBinding(
                provider="gemini",
                complete=_complete,
                stream_with_result=_stream_with_result,
                credential_available=True,
            )
        ]
    )
    target = route_decision_from_graph_runtime(
        _runtime(), scene_type="CHAT", cognitive_tier="FAST"
    ).targets[0]
    request = ProviderAttemptRequest(
        request_id="request-stream-result",
        attempt_id="attempt-stream-result",
        attempt_index=1,
        retry_reason="initial",
        scene_type="CHAT",
        cognitive_tier="FAST",
        prompt=_rendered_prompt(),
        target=target,
        timeout_ms=1000,
    )

    events = [
        event
        async for event in registry.get("production.gemini").stream_with_result(request)
    ]

    assert [event.kind.value for event in events] == ["DELTA", "FINAL"]
    assert payloads[0]["stream"] is True
    assert payloads[0]["stream_options"] == {"include_usage": True}
    assert payloads[0]["require_final_usage"] is True


def test_vault_backed_transport_emits_server_owned_external_identity() -> None:
    calls = []

    def _transport(**kwargs):
        calls.append(dict(kwargs))
        return {
            "content": "ok",
            "usage": {"output_tokens": 1},
            "metadata": {"provider_latency_ms": 7},
        }

    registry = build_vault_backed_provider_registry(
        ("openai",),
        credential_resolver=lambda _provider: "secret-not-persisted",
        base_url_resolver=lambda _provider: "https://api.openai.com/v1",
        transport=_transport,
    )
    target = route_decision_from_graph_runtime(
        _runtime(provider_name="openai", model_id="gpt-research"),
        scene_type="RESEARCH",
        cognitive_tier="RESEARCH",
    ).targets[0]
    request = ProviderAttemptRequest(
        request_id="request-external-identity",
        attempt_id="attempt-external-identity",
        attempt_index=1,
        retry_reason="initial",
        scene_type="RESEARCH",
        cognitive_tier="RESEARCH",
        prompt=_rendered_prompt(),
        target=target,
        timeout_ms=1000,
    )

    response = asyncio.run(registry.get("production.openai").complete(request))

    assert response.metadata["provider_latency_ms"] >= 0
    assert response.metadata["transport_external"] is True
    assert len(response.metadata["transport_identity_digest"]) == 64
    assert "api.openai.com" not in repr(response.metadata)
    assert calls[0]["base_url_override"] == "https://api.openai.com/v1"


@pytest.mark.asyncio
async def test_vault_backed_adapter_uses_native_provider_stream(monkeypatch) -> None:
    received: list[dict[str, object]] = []

    async def _native_stream(**kwargs):  # noqa: ANN003
        received.append(dict(kwargs))
        yield "第一句。"
        yield "第二句。"

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks",
        _native_stream,
    )
    registry = build_vault_backed_provider_registry(
        ("openai",),
        credential_resolver=lambda _provider: "stream-key",
        base_url_resolver=lambda _provider: "https://api.openai.com/v1",
        transport=lambda **_kwargs: {"content": "buffered fallback must not run"},
    )
    target = route_decision_from_graph_runtime(
        _runtime(provider_name="openai", model_id="gpt-stream"),
        scene_type="CHAT_SURFACE",
        cognitive_tier="A",
    ).targets[0]
    request = ProviderAttemptRequest(
        request_id="request-stream",
        attempt_id="attempt-stream",
        attempt_index=1,
        retry_reason="initial",
        scene_type="CHAT_SURFACE",
        cognitive_tier="A",
        prompt=_rendered_prompt(),
        target=target,
        timeout_ms=1000,
    )

    chunks = [chunk async for chunk in registry.get("production.openai").stream(request)]

    assert chunks == ["第一句。", "第二句。"]
    assert received[0]["model_id"] == "gpt-stream"
    assert received[0]["base_url_override"] == "https://api.openai.com/v1"


@pytest.mark.asyncio
async def test_vault_backed_adapter_forwards_real_final_usage_stream_without_estimation(
    monkeypatch,
) -> None:
    received: list[dict[str, object]] = []

    async def _native_stream_events(**kwargs):  # noqa: ANN003
        received.append(dict(kwargs))
        yield {"kind": "delta", "text": "先缓一下。"}
        yield {
            "kind": "final",
            "content": "先缓一下。",
            "usage": {"total_tokens": 9, "completion_tokens": 4},
        }

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_stream_events",
        _native_stream_events,
    )
    registry = build_vault_backed_provider_registry(
        ("openai",),
        credential_resolver=lambda _provider: "stream-key",
        base_url_resolver=lambda _provider: "https://api.openai.com/v1",
        transport=lambda **_kwargs: {"content": "buffered fallback must not run"},
    )
    target = route_decision_from_graph_runtime(
        _runtime(provider_name="openai", model_id="gpt-stream"),
        scene_type="CHAT_SURFACE",
        cognitive_tier="A",
    ).targets[0]
    request = ProviderAttemptRequest(
        request_id="request-stream-result",
        attempt_id="attempt-stream-result",
        attempt_index=1,
        retry_reason="initial",
        scene_type="CHAT_SURFACE",
        cognitive_tier="A",
        prompt=_rendered_prompt(),
        target=target,
        timeout_ms=1000,
    )

    adapter = registry.get("production.openai")
    assert adapter.supports_canonical_stream_result is True
    events = [event async for event in adapter.stream_with_result(request)]

    assert [event.kind.value for event in events] == ["DELTA", "FINAL"]
    assert events[-1].response is not None
    assert events[-1].response.usage == {
        "total_tokens": 9,
        "completion_tokens": 4,
    }
    # The adapter forwards only provider-owned values.  A missing price stays
    # missing so the guarded ModelAttempt service can reject it fail-closed.
    assert "model_cost_micros" not in events[-1].response.usage
    assert events[-1].response.metadata["transport_external"] is True
    assert received[0]["model_id"] == "gpt-stream"
    assert received[0]["base_url_override"] == "https://api.openai.com/v1"


@pytest.mark.parametrize(
    "base_url",
    (
        "http://127.0.0.1:11434/v1",
        "http://localhost:8000/v1",
        "https://10.0.0.8/v1",
        "https://ollama:11434/v1",
        "https://model.service.internal/v1",
    ),
)
def test_vault_backed_transport_marks_local_endpoints_non_external(base_url: str) -> None:
    registry = build_vault_backed_provider_registry(
        ("openai",),
        credential_resolver=lambda _provider: "local-key",
        base_url_resolver=lambda _provider: base_url,
        transport=lambda **_kwargs: {"content": "local"},
    )
    target = route_decision_from_graph_runtime(
        _runtime(provider_name="openai", model_id="local-model"),
        scene_type="RESEARCH",
        cognitive_tier="RESEARCH",
    ).targets[0]
    request = ProviderAttemptRequest(
        request_id="request-local-identity",
        attempt_id="attempt-local-identity",
        attempt_index=1,
        retry_reason="initial",
        scene_type="RESEARCH",
        cognitive_tier="RESEARCH",
        prompt=_rendered_prompt(),
        target=target,
        timeout_ms=1000,
    )

    response = asyncio.run(registry.get("production.openai").complete(request))

    assert response.metadata["transport_external"] is False


def test_guarded_provider_payload_clamps_output_without_exposing_budget_authority() -> None:
    target = route_decision_from_graph_runtime(
        _runtime(agent_gen_params={"temperature": 0.2, "max_tokens": 64}),
        scene_type="CHAT",
        cognitive_tier="FAST",
    ).targets[0]
    request = ProviderAttemptRequest(
        request_id="request-guarded-1",
        attempt_id="attempt-guarded-1",
        attempt_index=1,
        retry_reason="initial",
        scene_type="CHAT",
        cognitive_tier="FAST",
        prompt=_rendered_prompt(),
        target=target,
        timeout_ms=1000,
        model_budget_guard=ModelBudgetGuard(
            budget_id="budget://provider-route-test",
            graph_id="graph-provider-route-test",
            graph_revision=1,
            node_id="node-provider-route-test",
            attempt_id="attempt-provider-route-test",
            deadline_ms=1000,
            max_provider_attempts=1,
            max_token_count=20,
            max_output_tokens=17,
            max_model_cost_micros=100,
        ),
    )

    payload = production_provider_payload(request, stream=False)

    assert payload["max_tokens"] == 17
    assert "_natsume_hard_max_output_tokens" not in payload
    assert "model_budget_guard" not in payload
    assert "budget://provider-route-test" not in repr(payload)


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

    resolved = resolve_model_provider_credential("openai")

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
