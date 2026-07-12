"""TASK-0495 model-provider construction safety matrix.

The matrix is deliberately construction-only.  Real provider branches are
replaced with recording builders, and no test may reach HTTP, Codex CLI, or a
production credential.  A mock callable is valid only when ``mock`` is the
explicitly selected provider.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from host_brain.app import provider_factory
from host_brain.core.errors import (
    LLM_ROUTE_ALL_FAILED,
    SERVICE_UNAVAILABLE_DEPENDENCY,
)
from host_brain.integrations import llm_gateway


REAL_HTTP_PROVIDERS = ("openai", "gemini", "deepseek", "minimax")
REAL_GATEWAY_PROVIDERS = (*REAL_HTTP_PROVIDERS, "codex")


def _provider_payload(label: str) -> str:
    return json.dumps(
        {
            "narrative": {"text": label},
            "intent_draft": {},
            "meta": {"draft_only": True, "source": label},
        },
        sort_keys=True,
    )


def _recording_provider(label: str) -> Callable[[dict[str, Any]], str]:
    def provider(_payload: dict[str, Any]) -> str:
        return _provider_payload(label)

    setattr(provider, "task0495_provider_label", label)
    return provider


def _construct(
    context: str,
    *,
    provider_name: str,
    api_key: str | None,
    model_id: str | None = None,
) -> Callable[[dict[str, Any]], Any]:
    kwargs: dict[str, Any] = {
        "provider_name": provider_name,
        "model_id": model_id or f"{provider_name}-task0495-model",
        "api_key": api_key,
        "timeout_ms": 1_000,
        "base_url_override": "https://provider.invalid/v1",
    }
    if context == "host":
        return provider_factory.build_host_provider_fn(**kwargs)
    if context == "node":
        return provider_factory.build_host_provider_fn_for_node(
            **kwargs,
            node_id="planner_constraints",
        )
    raise AssertionError(f"unknown construction context: {context}")


def _assert_stable_construction_failure(
    context: str,
    *,
    provider_name: str,
    api_key: str | None,
    reason_code: str,
) -> provider_factory.HostProviderUnavailableError:
    constructed = _construct(
        context,
        provider_name=provider_name,
        api_key=api_key,
    )

    assert callable(constructed)
    assert getattr(constructed, "natsume_provider_kind", None) == "unavailable"
    assert getattr(constructed, "natsume_provider_reason_code", None) == reason_code

    with pytest.raises(provider_factory.HostProviderUnavailableError) as caught:
        constructed({"input": {}})
    assert caught.value.reason_code == reason_code
    assert str(caught.value) == reason_code
    return caught.value


def _forbid_mock(*_args: Any, **_kwargs: Any) -> Callable[[dict[str, Any]], Any]:
    raise AssertionError("TASK-0495 forbids an implicit mock provider")


@pytest.mark.parametrize("context", ("host", "node"))
@pytest.mark.parametrize("provider_name", REAL_HTTP_PROVIDERS)
def test_real_keyed_provider_uses_the_real_builder_in_every_context(
    monkeypatch: pytest.MonkeyPatch,
    context: str,
    provider_name: str,
) -> None:
    built: list[tuple[dict[str, Any], Callable[[dict[str, Any]], Any]]] = []

    def recording_builder(**kwargs: Any) -> Callable[[dict[str, Any]], Any]:
        provider = _recording_provider(f"real:{provider_name}:{context}")
        built.append((dict(kwargs), provider))
        return provider

    monkeypatch.setattr(
        provider_factory,
        "build_langchain_chat_provider_fn",
        recording_builder,
    )
    monkeypatch.setattr(provider_factory, "build_host_mock_provider", _forbid_mock)
    monkeypatch.setattr(
        provider_factory,
        "build_host_mock_provider_for_node",
        _forbid_mock,
    )

    constructed = _construct(
        context,
        provider_name=provider_name,
        api_key=f"task0495-{provider_name}-key",
    )

    assert len(built) == 1
    assert constructed is built[0][1]
    assert built[0][0]["provider_name"] == provider_name
    assert built[0][0]["api_key"] == f"task0495-{provider_name}-key"


@pytest.mark.parametrize("context", ("host", "node"))
def test_explicit_mock_is_the_only_factory_mock_path(
    monkeypatch: pytest.MonkeyPatch,
    context: str,
) -> None:
    host_mock = _recording_provider("explicit-mock:host")
    node_mock = _recording_provider("explicit-mock:node")
    monkeypatch.setattr(
        provider_factory,
        "build_host_mock_provider",
        lambda: host_mock,
    )
    monkeypatch.setattr(
        provider_factory,
        "build_host_mock_provider_for_node",
        lambda _node_id: node_mock,
    )
    monkeypatch.setattr(
        provider_factory,
        "build_langchain_chat_provider_fn",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit mock must not construct a real provider")
        ),
    )

    constructed = _construct(context, provider_name="mock", api_key=None)

    assert constructed is (host_mock if context == "host" else node_mock)


@pytest.mark.parametrize("context", ("host", "node"))
def test_codex_cli_no_key_uses_the_real_cli_builder(
    monkeypatch: pytest.MonkeyPatch,
    context: str,
) -> None:
    cli_provider = _recording_provider(f"codex-cli:{context}")
    calls: list[dict[str, Any]] = []

    def cli_builder(**kwargs: Any) -> Callable[[dict[str, Any]], Any]:
        calls.append(dict(kwargs))
        return cli_provider

    monkeypatch.setattr(
        provider_factory,
        "codex_cli_lm_allowed_for_background",
        lambda: True,
    )
    monkeypatch.setattr(provider_factory, "_build_codex_cli_provider_fn", cli_builder)
    monkeypatch.setattr(provider_factory, "build_host_mock_provider", _forbid_mock)
    monkeypatch.setattr(
        provider_factory,
        "build_host_mock_provider_for_node",
        _forbid_mock,
    )

    constructed = _construct(context, provider_name="codex", api_key=None)

    assert constructed is cli_provider
    assert len(calls) == 1
    assert calls[0]["model_id"] == "codex-task0495-model"
    if context == "node":
        assert calls[0]["node_id"] == "planner_constraints"


@pytest.mark.parametrize("context", ("host", "node"))
def test_codex_relay_key_uses_the_real_http_builder(
    monkeypatch: pytest.MonkeyPatch,
    context: str,
) -> None:
    relay_provider = _recording_provider(f"codex-relay:{context}")
    calls: list[dict[str, Any]] = []

    def relay_builder(**kwargs: Any) -> Callable[[dict[str, Any]], Any]:
        calls.append(dict(kwargs))
        return relay_provider

    monkeypatch.setattr(
        provider_factory,
        "build_langchain_chat_provider_fn",
        relay_builder,
    )
    monkeypatch.setattr(
        provider_factory,
        "codex_cli_lm_allowed_for_background",
        lambda: False,
    )
    monkeypatch.setattr(provider_factory, "build_host_mock_provider", _forbid_mock)
    monkeypatch.setattr(
        provider_factory,
        "build_host_mock_provider_for_node",
        _forbid_mock,
    )

    constructed = _construct(
        context,
        provider_name="codex",
        api_key="task0495-codex-relay-key",
    )

    assert constructed is relay_provider
    assert len(calls) == 1
    assert calls[0]["provider_name"] == "codex"
    assert calls[0]["api_key"] == "task0495-codex-relay-key"


@pytest.mark.parametrize("context", ("host", "node"))
@pytest.mark.parametrize("provider_name", REAL_HTTP_PROVIDERS)
def test_missing_real_provider_key_is_stable_and_never_selects_mock(
    monkeypatch: pytest.MonkeyPatch,
    context: str,
    provider_name: str,
) -> None:
    monkeypatch.setattr(provider_factory, "build_host_mock_provider", _forbid_mock)
    monkeypatch.setattr(
        provider_factory,
        "build_host_mock_provider_for_node",
        _forbid_mock,
    )

    _assert_stable_construction_failure(
        context,
        provider_name=provider_name,
        api_key=None,
        reason_code=provider_factory.HOST_PROVIDER_CREDENTIAL_REQUIRED,
    )


@pytest.mark.parametrize("context", ("host", "node"))
@pytest.mark.parametrize(
    ("provider_name", "reason_code"),
    (
        ("disabled", provider_factory.HOST_PROVIDER_DISABLED),
        ("not-a-provider", provider_factory.HOST_PROVIDER_UNSUPPORTED),
    ),
)
def test_disabled_and_unknown_provider_fail_stably_without_mock(
    monkeypatch: pytest.MonkeyPatch,
    context: str,
    provider_name: str,
    reason_code: str,
) -> None:
    monkeypatch.setattr(provider_factory, "build_host_mock_provider", _forbid_mock)
    monkeypatch.setattr(
        provider_factory,
        "build_host_mock_provider_for_node",
        _forbid_mock,
    )

    _assert_stable_construction_failure(
        context,
        provider_name=provider_name,
        api_key="task0495-unused-key",
        reason_code=reason_code,
    )


@pytest.mark.parametrize("context", ("host", "node"))
def test_codex_without_cli_or_relay_key_fails_stably_without_mock(
    monkeypatch: pytest.MonkeyPatch,
    context: str,
) -> None:
    monkeypatch.setattr(
        provider_factory,
        "codex_cli_lm_allowed_for_background",
        lambda: False,
    )
    monkeypatch.setattr(provider_factory, "build_host_mock_provider", _forbid_mock)
    monkeypatch.setattr(
        provider_factory,
        "build_host_mock_provider_for_node",
        _forbid_mock,
    )

    _assert_stable_construction_failure(
        context,
        provider_name="codex",
        api_key=None,
        reason_code=provider_factory.HOST_PROVIDER_CODEX_UNAVAILABLE,
    )


@pytest.mark.parametrize("context", ("host", "node"))
@pytest.mark.parametrize("failure_type", (ModuleNotFoundError, RuntimeError))
def test_real_builder_error_is_wrapped_stably_and_never_selects_mock(
    monkeypatch: pytest.MonkeyPatch,
    context: str,
    failure_type: type[Exception],
) -> None:
    def fail_builder(**_kwargs: Any) -> Callable[[dict[str, Any]], Any]:
        raise failure_type("private dependency detail")

    monkeypatch.setattr(
        provider_factory,
        "build_langchain_chat_provider_fn",
        fail_builder,
    )
    monkeypatch.setattr(provider_factory, "build_host_mock_provider", _forbid_mock)
    monkeypatch.setattr(
        provider_factory,
        "build_host_mock_provider_for_node",
        _forbid_mock,
    )

    caught = _assert_stable_construction_failure(
        context,
        provider_name="openai",
        api_key="task0495-openai-key",
        reason_code=provider_factory.HOST_PROVIDER_CONSTRUCTION_FAILED,
    )
    assert "private dependency detail" not in str(caught)


@pytest.mark.parametrize("provider_name", REAL_GATEWAY_PROVIDERS)
def test_gateway_missing_real_provider_callable_degrades_without_mock(
    monkeypatch: pytest.MonkeyPatch,
    provider_name: str,
) -> None:
    mock_calls: list[dict[str, Any]] = []

    def forbidden_default_mock(payload: dict[str, Any]) -> str:
        mock_calls.append(dict(payload))
        return _provider_payload("forbidden-implicit-gateway-mock")

    monkeypatch.setattr(
        llm_gateway,
        "_default_mock_provider",
        forbidden_default_mock,
    )
    gateway = llm_gateway.LlmGatewayContract(
        provider=None,
        provider_name=provider_name,
        api_key=f"task0495-{provider_name}-key",
        available=True,
    )

    result = gateway.generate(
        request_id=f"req-task0495-missing-{provider_name}",
        model_profile_id=f"mp.{provider_name}.task0495",
        prompt_payload={"messages": [{"role": "user", "content": "isolated"}]},
    )

    assert result.success is False
    assert result.reason_code == SERVICE_UNAVAILABLE_DEPENDENCY
    assert result.output["status"] == "DEGRADED"
    assert result.output["provider"] == provider_name
    assert mock_calls == []


def test_gateway_unknown_provider_is_rejected_before_callable_invocation() -> None:
    provider_calls: list[dict[str, Any]] = []

    def must_not_run(payload: dict[str, Any]) -> str:
        provider_calls.append(dict(payload))
        return _provider_payload("unknown-provider-ran")

    gateway = llm_gateway.LlmGatewayContract(
        provider=must_not_run,
        provider_name="unknown-task0495-provider",
        api_key="task0495-unknown-key",
        available=True,
    )

    result = gateway.generate(
        request_id="req-task0495-unknown-provider",
        model_profile_id="mp.unknown.task0495",
        prompt_payload={"messages": []},
    )

    assert result.success is False
    assert result.reason_code == SERVICE_UNAVAILABLE_DEPENDENCY
    assert provider_calls == []


def test_gateway_provider_exception_detail_is_sanitized() -> None:
    secret_detail = "private-provider-secret=task0495-must-not-escape"

    def exploding_provider(_payload: dict[str, Any]) -> str:
        raise RuntimeError(secret_detail)

    gateway = llm_gateway.LlmGatewayContract(
        provider=exploding_provider,
        provider_name="openai",
        api_key="task0495-openai-key",
        available=True,
    )
    result = gateway.generate(
        request_id="req-task0495-provider-exception",
        model_profile_id="mp.task0495.provider-exception",
        prompt_payload={"messages": []},
    )

    assert result.success is False
    assert result.reason_code == SERVICE_UNAVAILABLE_DEPENDENCY
    assert secret_detail not in result.detail
    assert secret_detail not in json.dumps(result.output, sort_keys=True)


def test_hot_route_name_change_cannot_relabel_existing_mock_callable() -> None:
    gateway = llm_gateway.LlmGatewayContract(
        provider=_recording_provider("explicit-mock-before-hot-route"),
        provider_name="mock",
        api_key=None,
        available=True,
    )
    gateway.update_route_config(new_config={"provider_name": "openai"})
    gateway.api_key = "task0495-openai-key"

    result = gateway.generate(
        request_id="req-task0495-hot-route-binding",
        model_profile_id="mp.task0495.hot-route-binding",
        prompt_payload={"messages": []},
    )

    assert result.success is False
    assert result.reason_code == SERVICE_UNAVAILABLE_DEPENDENCY


def test_missing_nonmock_fallback_callable_cannot_use_default_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_calls: list[dict[str, Any]] = []

    def forbidden_default_mock(payload: dict[str, Any]) -> str:
        mock_calls.append(dict(payload))
        return _provider_payload("forbidden-fallback-mock")

    monkeypatch.setattr(
        llm_gateway,
        "_default_mock_provider",
        forbidden_default_mock,
    )

    def failed_primary(_payload: dict[str, Any]) -> str:
        raise RuntimeError("primary unavailable")

    gateway = llm_gateway.LlmGatewayContract(
        provider=failed_primary,
        provider_name="mock",
        api_key=None,
        fallback_providers=[
            {
                "provider_name": "openai",
                "provider_fn": None,
                "api_key": "task0495-openai-fallback-key",
                "available": True,
            }
        ],
    )

    result = gateway.route_call(
        request_id="req-task0495-missing-fallback",
        model_profile_id="mp.task0495.missing-fallback",
        prompt_payload={"messages": []},
    )

    assert result.success is False
    assert result.reason_code == LLM_ROUTE_ALL_FAILED
    assert mock_calls == []


def test_malformed_fallback_entries_fail_closed_without_mock() -> None:
    provider_calls: list[dict[str, Any]] = []

    def failed_primary(_payload: dict[str, Any]) -> str:
        raise RuntimeError("primary unavailable")

    def noncallable_marker(payload: dict[str, Any]) -> str:
        provider_calls.append(dict(payload))
        return _provider_payload("must-not-run")

    gateway = llm_gateway.LlmGatewayContract(
        provider=failed_primary,
        provider_name="mock",
        api_key=None,
        fallback_providers=[
            None,
            "mock",
            {
                "provider_name": "unknown-task0495-provider",
                "provider_fn": noncallable_marker,
                "api_key": "task0495-unknown-key",
            },
            {
                "provider_name": "openai",
                "provider_fn": object(),
                "api_key": "task0495-openai-key",
            },
        ],  # type: ignore[list-item]
    )

    result = gateway.route_call(
        request_id="req-task0495-malformed-fallbacks",
        model_profile_id="mp.task0495.malformed-fallbacks",
        prompt_payload={"messages": []},
    )

    assert result.success is False
    assert result.reason_code == LLM_ROUTE_ALL_FAILED
    assert provider_calls == []


def test_explicit_mock_fallback_may_use_default_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_mock = llm_gateway._default_mock_provider
    mock_calls: list[dict[str, Any]] = []

    def recording_default_mock(payload: dict[str, Any]) -> str:
        mock_calls.append(dict(payload))
        return original_mock(payload)

    monkeypatch.setattr(
        llm_gateway,
        "_default_mock_provider",
        recording_default_mock,
    )

    def failed_primary(_payload: dict[str, Any]) -> str:
        raise RuntimeError("primary unavailable")

    gateway = llm_gateway.LlmGatewayContract(
        provider=failed_primary,
        provider_name="mock",
        api_key=None,
        fallback_providers=[
            {
                "provider_name": "mock",
                "provider_fn": None,
                "api_key": None,
                "available": True,
            }
        ],
    )

    result = gateway.route_call(
        request_id="req-task0495-explicit-mock-fallback",
        model_profile_id="mp.task0495.explicit-mock-fallback",
        prompt_payload={"messages": []},
    )

    assert result.success is True
    assert result.output["provider"] == "mock"
    assert len(mock_calls) == 1
