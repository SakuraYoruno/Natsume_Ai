from __future__ import annotations

import pytest

import host_brain.app.provider_factory as provider_factory


def test_gemini_provider_uses_only_supplied_key(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_builder(*, api_key: str, **kwargs):  # noqa: ANN003
        _ = kwargs

        def _provider(_payload):  # noqa: ANN001
            calls.append(api_key)
            raise RuntimeError("429 rate_limit_exceeded")

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

    with pytest.raises(RuntimeError, match="rate_limit_exceeded"):
        provider_fn({"input": {"user_message": "hello"}})

    assert calls == ["k1"]


def test_gemini_primary_fallback_default_model_is_flash_lite(monkeypatch) -> None:
    monkeypatch.delenv("HOST_GEMINI_PRIMARY_DISABLE_MODEL_FALLBACK", raising=False)
    monkeypatch.delenv("HOST_GEMINI_PRIMARY_FALLBACK_MODEL_ID", raising=False)

    resolved = provider_factory.resolve_gemini_primary_fallback_model_id("gemini-2.5-pro")

    assert resolved == "gemini-2.5-flash-lite"
