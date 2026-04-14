from __future__ import annotations

import asyncio

import pytest

import host_brain.app.provider_factory as provider_factory


def _run(coro):
    return asyncio.run(coro)


def test_gemini_key_failover_rotates_on_rate_limit(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setenv("HOST_GEMINI_KEY_FAILOVER_ENABLED", "true")
    monkeypatch.setenv("HOST_GEMINI_KEY_FAILOVER_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(provider_factory, "resolve_gemini_key_pool", lambda **_: ["k1", "k2", "k3"])

    def _fake_builder(*, api_key: str, model_id: str, **kwargs):  # noqa: ANN003
        _ = kwargs

        def _provider(_payload):  # noqa: ANN001
            calls.append(api_key)
            if api_key == "k1":
                raise RuntimeError("429 rate_limit_exceeded")
            return f"ok:{api_key}:{model_id}"

        return _provider

    monkeypatch.setattr(provider_factory, "build_langchain_chat_provider_fn", _fake_builder)

    provider_fn = provider_factory.build_host_provider_fn_for_node(
        provider_name="gemini",
        model_id="gemini-2.5-flash",
        api_key="k1",
        timeout_ms=8000,
        node_id="planner_constraints",
        base_url_override=None,
    )
    result = _run(provider_fn({"input": {"user_message": "hello"}}))

    assert result == "ok:k2:gemini-2.5-flash"
    assert calls == ["k1", "k2"]


def test_gemini_key_failover_does_not_rotate_on_bad_request(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setenv("HOST_GEMINI_KEY_FAILOVER_ENABLED", "true")
    monkeypatch.setenv("HOST_GEMINI_KEY_FAILOVER_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(provider_factory, "resolve_gemini_key_pool", lambda **_: ["k1", "k2", "k3"])

    def _fake_builder(*, api_key: str, **kwargs):  # noqa: ANN003
        _ = kwargs

        def _provider(_payload):  # noqa: ANN001
            calls.append(api_key)
            if api_key == "k1":
                raise RuntimeError("400 invalid argument: malformed json")
            return "should-not-reach"

        return _provider

    monkeypatch.setattr(provider_factory, "build_langchain_chat_provider_fn", _fake_builder)

    provider_fn = provider_factory.build_host_provider_fn_for_node(
        provider_name="gemini",
        model_id="gemini-2.5-flash",
        api_key="k1",
        timeout_ms=8000,
        node_id="planner_constraints",
        base_url_override=None,
    )
    with pytest.raises(RuntimeError):
        _run(provider_fn({"input": {"user_message": "hello"}}))

    assert calls == ["k1"]


def test_gemini_key_failover_can_be_disabled(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setenv("HOST_GEMINI_KEY_FAILOVER_ENABLED", "false")
    monkeypatch.setattr(provider_factory, "resolve_gemini_key_pool", lambda **_: ["k1", "k2"])

    def _fake_builder(*, api_key: str, **kwargs):  # noqa: ANN003
        _ = kwargs

        def _provider(_payload):  # noqa: ANN001
            calls.append(api_key)
            return f"ok:{api_key}"

        return _provider

    monkeypatch.setattr(provider_factory, "build_langchain_chat_provider_fn", _fake_builder)

    provider_fn = provider_factory.build_host_provider_fn_for_node(
        provider_name="gemini",
        model_id="gemini-2.5-flash",
        api_key="k1",
        timeout_ms=8000,
        node_id="planner_constraints",
        base_url_override=None,
    )
    result = provider_fn({"input": {"user_message": "hello"}})
    assert result == "ok:k1"
    assert calls == ["k1"]


def test_gemini_primary_fallback_default_model_is_flash_lite(monkeypatch) -> None:
    monkeypatch.delenv("HOST_GEMINI_PRIMARY_DISABLE_MODEL_FALLBACK", raising=False)
    monkeypatch.delenv("HOST_GEMINI_PRIMARY_FALLBACK_MODEL_ID", raising=False)

    resolved = provider_factory.resolve_gemini_primary_fallback_model_id("gemini-2.5-pro")

    assert resolved == "gemini-2.5-flash-lite"
