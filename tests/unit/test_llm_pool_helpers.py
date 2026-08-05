from __future__ import annotations

import os

import pytest

from host_brain.integrations import llm_helpers as helpers


@pytest.fixture(autouse=True)
def _isolated_pool_runtime(monkeypatch: pytest.MonkeyPatch):
    """Make every pool case independent of prior scene policy and cache state."""

    for name in tuple(os.environ):
        if name.startswith("HOST_LLM_POOL_") or name == (
            "PENTEST_LLM_ATTEMPT_TIMEOUT_HARD_CAP_MS"
        ):
            monkeypatch.delenv(name, raising=False)
    helpers._POOL_COMPLETION_CACHE.clear()
    helpers._POOL_COOLDOWN_UNTIL_MS.clear()
    yield
    helpers._POOL_COMPLETION_CACHE.clear()
    helpers._POOL_COOLDOWN_UNTIL_MS.clear()


def test_complete_llm_with_pool_falls_back_and_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_TIER_A", "gemini:gemini-pro,minimax:minimax-her")
    monkeypatch.setenv("HOST_LLM_POOL_CACHE_ENABLED", "true")
    monkeypatch.setenv("HOST_LLM_POOL_CACHE_TTL_MS", "60000")
    monkeypatch.setenv("HOST_LLM_POOL_RATE_LIMIT_COOLDOWN_MS", "12345")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "mini-key")

    helpers._POOL_COMPLETION_CACHE.clear()
    helpers._POOL_COOLDOWN_UNTIL_MS.clear()

    traces: list[dict] = []
    monkeypatch.setattr(
        helpers,
        "_log_llm_pool_call",
        lambda **kwargs: traces.append(dict(kwargs)),
    )

    calls: list[tuple[str, str]] = []

    def _fake_invoke(**kwargs):
        provider_name = str(kwargs.get("provider_name") or "")
        model_id = str(kwargs.get("model_id") or "")
        calls.append((provider_name, model_id))
        if provider_name == "gemini":
            raise RuntimeError("429 rate limit")
        return '{"route":"chat","confidence":0.8}'

    monkeypatch.setattr(helpers, "invoke_llm_text", _fake_invoke)

    result = helpers.complete_llm_with_pool_sync(
        request_id="req-pool-1",
        scene_type="CHAT",
        cognitive_tier="A",
        timeout_ms=1500,
        messages=[{"role": "user", "content": "hello"}],
        response_format="json_object",
    )

    assert result.pool_exhausted is False
    assert result.final_provider == "minimax"
    assert result.cached is False
    assert len(result.attempts) == 2
    assert result.attempts[0].reason_code == "POOL_RATE_LIMITED"
    assert (
        helpers._get_cooldown_remaining_ms(
            provider_name="gemini",
            model_id="gemini-pro",
            scene_type="CHAT",
    )
        > 0
    )
    assert traces[-1]["final_provider"] == "minimax"
    assert traces[-1]["request_id"] == "req-pool-1"
    assert traces[-1]["cached"] is False

    monkeypatch.setattr(
        helpers,
        "invoke_llm_text",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError(f"unexpected provider call: {kwargs}")),
    )

    cached = helpers.complete_llm_with_pool_sync(
        request_id="req-pool-1",
        scene_type="CHAT",
        cognitive_tier="A",
        timeout_ms=1500,
        messages=[{"role": "user", "content": "hello"}],
        response_format="json_object",
    )

    assert cached.cached is True
    assert cached.final_provider == "minimax"
    assert calls == [("gemini", "gemini-pro"), ("minimax", "minimax-her")]
    assert traces[-1]["cached"] is True


@pytest.mark.asyncio
async def test_astream_llm_chunks_with_pool_switches_before_first_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_TIER_A", "gemini:gemini-pro,minimax:minimax-her")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "mini-key")

    helpers._POOL_COOLDOWN_UNTIL_MS.clear()

    traces: list[dict] = []
    monkeypatch.setattr(
        helpers,
        "_log_llm_pool_call",
        lambda **kwargs: traces.append(dict(kwargs)),
    )

    async def _fake_astream(**kwargs):
        provider_name = str(kwargs.get("provider_name") or "")
        if provider_name == "gemini":
            raise RuntimeError("timeout waiting upstream")
        yield "he"
        yield "llo"

    monkeypatch.setattr(helpers, "astream_llm_chunks", _fake_astream)

    chunks: list[str] = []
    async for chunk in helpers.astream_llm_chunks_with_pool(
        request_id="req-stream-1",
        scene_type="CHAT",
        cognitive_tier="A",
        timeout_ms=1500,
        messages=[{"role": "user", "content": "hello"}],
    ):
        chunks.append(chunk)

    assert "".join(chunks) == "hello"
    assert traces[-1]["final_provider"] == "minimax"
    assert traces[-1]["request_id"] == "req-stream-1"
    assert traces[-1]["pool_exhausted"] is False


def test_pool_cooldown_is_scoped_per_scene_type() -> None:
    helpers._POOL_COOLDOWN_UNTIL_MS.clear()

    helpers._set_cooldown_ms(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        scene_type="HIPPOCAMPUS_RECALL",
        cooldown_ms=30_000,
    )

    assert (
        helpers._get_cooldown_remaining_ms(
            provider_name="gemini",
            model_id="gemini-2.5-pro",
            scene_type="HIPPOCAMPUS_RECALL",
        )
        > 0
    )
    assert (
        helpers._get_cooldown_remaining_ms(
            provider_name="gemini",
            model_id="gemini-2.5-pro",
            scene_type="CHAT",
        )
        == 0
    )


def test_pool_candidates_use_runtime_profile_key_when_env_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("HOST_LLM_POOL_TIER_A", "gemini:gemini-pro")
    monkeypatch.setattr(
        helpers,
        "_load_api_key_from_runtime_profiles",
        lambda provider_name: "profile-gemini-key" if provider_name == "gemini" else None,
    )

    candidates = helpers._resolve_pool_candidates(cognitive_tier="A")

    assert candidates == [
        {
            "provider_name": "gemini",
            "model_id": "gemini-pro",
            "api_key": "profile-gemini-key",
            "base_url_override": "https://generativelanguage.googleapis.com/v1beta/openai/v1",
        }
    ]


def test_non_chat_pool_prefers_codex_cli_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_CODEX_MIGRATION_ENABLED", "true")
    monkeypatch.setenv("HOST_LLM_POOL_TIER_B", "gemini:gemini-pro")
    monkeypatch.setenv("HOST_CODEX_MODEL_ID", "gpt-5.4-mini")
    monkeypatch.delenv("HOST_LLM_POOL_CODEX_MODEL_ID", raising=False)
    monkeypatch.setattr(helpers, "codex_cli_lm_available", lambda: True)
    monkeypatch.setattr(helpers, "codex_cli_lm_allowed_for_background", lambda: True)

    candidates = helpers._resolve_pool_candidates(
        cognitive_tier="B",
        scene_type="PLAN_SYNTHESIZE",
        provider_name="gemini",
        model_id="gemini-pro",
        api_key="gem-key",
    )

    assert candidates[0]["provider_name"] == "codex"
    assert candidates[0]["model_id"] == "gpt-5.5"
    assert candidates[0]["api_key"] is None


def test_chat_pool_keeps_fast_provider_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_CODEX_MIGRATION_ENABLED", "true")
    monkeypatch.setenv("HOST_LLM_POOL_TIER_A", "gemini:gemini-pro")
    monkeypatch.setattr(helpers, "codex_cli_lm_available", lambda: True)
    monkeypatch.setattr(helpers, "codex_cli_lm_allowed_for_background", lambda: True)

    candidates = helpers._resolve_pool_candidates(
        cognitive_tier="A",
        scene_type="CHAT_SURFACE",
        provider_name="gemini",
        model_id="gemini-pro",
        api_key="gem-key",
    )

    assert candidates[0]["provider_name"] == "gemini"
    assert not any(candidate["provider_name"] == "codex" for candidate in candidates)


def test_scene_pool_spec_overrides_runtime_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "HOST_LLM_POOL_SCENE_CHAT_SURFACE_TIER_A",
        "deepseek:deepseek-v4-flash,gemini:gemini-2.5-flash-lite",
    )
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-key")

    candidates = helpers._resolve_pool_candidates(
        cognitive_tier="A",
        scene_type="CHAT_SURFACE",
        provider_name="deepseek",
        model_id="deepseek-v4-flash",
        api_key="runtime-deep-key",
    )

    assert [(row["provider_name"], row["model_id"]) for row in candidates] == [
        ("deepseek", "deepseek-v4-flash"),
        ("gemini", "gemini-2.5-flash-lite"),
        ("deepseek", "deepseek-v4-pro"),
        ("gemini", "gemini-2.5-pro"),
    ]


def test_scene_pool_openai_local_uses_host_chat_local_base_without_openai_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HOST_LLM_POOL_SCENE_CHAT_SURFACE_TIER_A",
        "openai:natsume-qwen3.6-35b-iq3m-local",
    )
    monkeypatch.setenv("HOST_LLM_POOL_SCENE_CHAT_SURFACE_INCLUDE_EXPLICIT", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HOST_OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("HOST_CHAT_LOCAL_LLM_ALLOW_EMPTY_KEY", "true")
    monkeypatch.setattr(helpers, "_load_api_key_from_runtime_profiles", lambda provider_name: None)

    candidates = helpers._resolve_pool_candidates(
        cognitive_tier="A",
        scene_type="CHAT_SURFACE",
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        api_key="gem-key",
    )

    assert candidates == [
        {
            "provider_name": "openai",
            "model_id": "natsume-qwen3.6-35b-iq3m-local",
            "api_key": "host-local-placeholder-key",
            "base_url_override": "http://127.0.0.1:11434/v1",
        }
    ]


def test_exec_intent_scene_pool_can_bypass_codex_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_CODEX_MIGRATION_ENABLED", "true")
    monkeypatch.setenv("HOST_LLM_POOL_SCENE_EXEC_INTENT_TIER_C", "deepseek:deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-key")
    monkeypatch.setattr(helpers, "codex_cli_lm_available", lambda: True)
    monkeypatch.setattr(helpers, "codex_cli_lm_allowed_for_background", lambda: True)

    candidates = helpers._resolve_pool_candidates(
        cognitive_tier="C",
        scene_type="EXEC_INTENT",
        provider_name="gemini",
        model_id="gemini-2.5-flash-lite",
        api_key="gem-key",
    )

    assert [(row["provider_name"], row["model_id"]) for row in candidates] == [
        ("deepseek", "deepseek-v4-flash"),
    ]


def test_scene_pool_max_attempts_caps_surface_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "HOST_LLM_POOL_SCENE_CHAT_SURFACE_TIER_A",
        "deepseek:deepseek-v4-flash,gemini:gemini-2.5-flash-lite,minimax:M2-her",
    )
    monkeypatch.setenv("HOST_LLM_POOL_SCENE_CHAT_SURFACE_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "mini-key")
    helpers._POOL_COMPLETION_CACHE.clear()
    helpers._POOL_COOLDOWN_UNTIL_MS.clear()
    calls: list[tuple[str, str]] = []

    def _fake_invoke(**kwargs):
        calls.append((kwargs["provider_name"], kwargs["model_id"]))
        raise RuntimeError("empty llm response")

    monkeypatch.setattr(helpers, "invoke_llm_text", _fake_invoke)

    result = helpers.complete_llm_with_pool_sync(
        request_id="req-surface-cap",
        scene_type="CHAT_SURFACE",
        cognitive_tier="A",
        provider_name="deepseek",
        model_id="deepseek-v4-flash",
        api_key="runtime-deep-key",
        timeout_ms=1000,
        messages=[{"role": "user", "content": "x"}],
        response_format={"type": "json_object"},
    )

    assert result.pool_exhausted is True
    assert calls == [
        ("deepseek", "deepseek-v4-flash"),
        ("gemini", "gemini-2.5-flash-lite"),
    ]


def test_scene_pool_attempt_timeout_override_is_passed_to_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_SCENE_CHAT_SURFACE_TIER_A", "deepseek:deepseek-v4-flash")
    monkeypatch.setenv("HOST_LLM_POOL_SCENE_CHAT_SURFACE_ATTEMPT_TIMEOUT_MS", "3500")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-key")
    helpers._POOL_COMPLETION_CACHE.clear()
    helpers._POOL_COOLDOWN_UNTIL_MS.clear()
    seen_timeouts: list[int] = []

    def _fake_invoke(**kwargs):
        seen_timeouts.append(int(kwargs["timeout_ms"]))
        return '{"reply_text":"在。"}'

    monkeypatch.setattr(helpers, "invoke_llm_text", _fake_invoke)

    result = helpers.complete_llm_with_pool_sync(
        request_id="req-surface-timeout",
        scene_type="CHAT_SURFACE",
        cognitive_tier="A",
        timeout_ms=8000,
        messages=[{"role": "user", "content": "在吗"}],
        response_format={"type": "json_object"},
    )

    assert result.pool_exhausted is False
    assert seen_timeouts == [3500]


def test_multimodal_pool_never_falls_back_to_text_only_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "HOST_LLM_POOL_TIER_MULTIMODAL",
        "deepseek:deepseek-v4-flash,gemini:gemini-2.5-pro,minimax:M2-her",
    )
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "mini-key")
    helpers._POOL_COMPLETION_CACHE.clear()
    helpers._POOL_COOLDOWN_UNTIL_MS.clear()

    calls: list[tuple[str, str]] = []

    def _fake_invoke(**kwargs):
        provider_name = str(kwargs.get("provider_name") or "")
        model_id = str(kwargs.get("model_id") or "")
        calls.append((provider_name, model_id))
        raise RuntimeError("vision provider unavailable")

    monkeypatch.setattr(helpers, "invoke_llm_text", _fake_invoke)

    result = helpers.complete_llm_with_pool_sync(
        request_id="req-multimodal-pool",
        scene_type="MULTIMODAL",
        cognitive_tier="MULTIMODAL",
        provider_name="gemini",
        model_id="gemini-2.5-flash-lite",
        api_key="explicit-gem-key",
        timeout_ms=1500,
        messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]}],
        response_format="json_object",
    )

    assert result.pool_exhausted is True
    assert calls
    assert all(provider == "gemini" for provider, _model in calls)
    assert not any(provider in {"deepseek", "minimax"} for provider, _model in calls)


def test_pool_billing_error_gets_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_BILLING_COOLDOWN_MS", "7777")

    reason_code, cooldown_ms = helpers._classify_pool_error(RuntimeError("Error code: 402 - Insufficient Balance"))

    assert reason_code == "POOL_BILLING_UNAVAILABLE"
    assert cooldown_ms == 7777


def test_pool_format_error_can_get_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_FORMAT_INVALID_COOLDOWN_MS", "4567")

    reason_code, cooldown_ms = helpers._classify_pool_error(RuntimeError("empty llm response"))

    assert reason_code == "POOL_FORMAT_INVALID"
    assert cooldown_ms == 4567


def test_pool_format_error_scene_cooldown_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_FORMAT_INVALID_COOLDOWN_MS", "4567")
    monkeypatch.setenv("HOST_LLM_POOL_SCENE_CHAT_SURFACE_FORMAT_INVALID_COOLDOWN_MS", "0")

    reason_code, cooldown_ms = helpers._classify_pool_error(
        RuntimeError("empty llm response"),
        scene_type="CHAT_SURFACE",
    )

    assert reason_code == "POOL_FORMAT_INVALID"
    assert cooldown_ms == 0


def test_chat_surface_json_format_can_wrap_plain_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_JSON_TEXT_FALLBACK_SCENES", "CHAT_SURFACE")

    content = helpers._validate_pool_response(
        content="在呢，短短接住你一下。",
        response_format={"type": "json_object"},
        scene_type="CHAT_SURFACE",
    )

    assert '"reply_text": "在呢，短短接住你一下。"' in content
    assert '"natsume_emotion_tag": "NEUTRAL"' in content


def test_chat_surface_does_not_retry_empty_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_SCENE_CHAT_SURFACE_TIER_A", "deepseek:deepseek-v4-flash,gemini:gemini-2.5-flash-lite")
    monkeypatch.setenv("HOST_LLM_POOL_JSON_TEXT_FALLBACK_SCENES", "CHAT_SURFACE")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("HOST_LLM_POOL_SCENE_CHAT_SURFACE_MAX_ATTEMPTS", "1")
    helpers._POOL_COMPLETION_CACHE.clear()
    helpers._POOL_COOLDOWN_UNTIL_MS.clear()
    calls: list[tuple[str, str | None]] = []

    def _fake_invoke(**kwargs):
        provider_name = str(kwargs.get("provider_name") or "")
        response_format = kwargs.get("response_format")
        calls.append((provider_name, "json" if response_format else None))
        return ""

    monkeypatch.setattr(helpers, "invoke_llm_text", _fake_invoke)

    result = helpers.complete_llm_with_pool_sync(
        request_id="req-surface-empty-no-retry",
        scene_type="CHAT_SURFACE",
        cognitive_tier="A",
        provider_name="deepseek",
        model_id="deepseek-v4-flash",
        api_key="runtime-deep-key",
        timeout_ms=1000,
        messages=[{"role": "user", "content": "在吗"}],
        response_format={"type": "json_object"},
    )

    assert result.pool_exhausted is True
    assert calls == [("deepseek", "json")]
    assert result.attempts[0].reason_code == "POOL_FORMAT_INVALID"


def test_openai_compat_empty_response_summary_redacts_content() -> None:
    summary = helpers._summarize_openai_compat_empty_response(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "hidden chain",
                        "refusal": "hidden refusal",
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0},
        }
    )

    encoded = str(summary)
    assert summary["choice_count"] == 1
    assert summary["message_content_len"] == 0
    assert summary["message_reasoning_content_len"] == len("hidden chain")
    assert "hidden chain" not in encoded
    assert "hidden refusal" not in encoded


def test_openai_compat_payload_raises_deepseek_json_token_floor() -> None:
    payload = helpers._build_openai_compat_payload(
        model_id="deepseek-v4-flash",
        messages=[{"role": "user", "content": "输出 JSON"}],
        gen_params={"max_tokens": 220, "response_format": {"type": "json_object"}},
    )

    assert payload["max_tokens"] == 384


def test_openai_compat_payload_keeps_internal_hard_cap_below_deepseek_floor() -> None:
    payload = helpers._build_openai_compat_payload(
        model_id="deepseek-v4-flash",
        messages=[{"role": "user", "content": "输出 JSON"}],
        gen_params={
            "max_tokens": 17,
            "response_format": {"type": "json_object"},
            "_natsume_hard_max_output_tokens": 17,
        },
    )

    assert payload["max_tokens"] == 17
    assert "_natsume_hard_max_output_tokens" not in payload


def test_openai_compat_payload_keeps_other_model_token_cap() -> None:
    payload = helpers._build_openai_compat_payload(
        model_id="gemini-2.5-flash-lite",
        messages=[{"role": "user", "content": "输出 JSON"}],
        gen_params={"max_tokens": 220, "response_format": {"type": "json_object"}},
    )

    assert payload["max_tokens"] == 220


def test_complete_prompt_with_pool_rejects_empty_prompt_before_provider_call() -> None:
    with pytest.raises(RuntimeError, match="empty llm prompt"):
        helpers.complete_prompt_with_pool_sync(
            request_id="req-empty-prompt",
            scene_type="EVOLUTION_DESIRE",
            cognitive_tier="B",
            prompt="",
            timeout_ms=1000,
        )


def test_openai_compat_http_error_includes_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    class HTTPError(Exception):
        pass

    class _Response:
        status_code = 400
        text = '{"error":{"message":"Empty input messages"}}'

        def raise_for_status(self) -> None:
            exc = HTTPError("400 Client Error")
            exc.response = self
            raise exc

    class _Session:
        def post(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            return _Response()

    monkeypatch.setattr(helpers, "_openai_compat_keepalive_enabled", lambda: True)
    monkeypatch.setattr(helpers, "_get_openai_compat_session", lambda: _Session())

    with pytest.raises(RuntimeError) as excinfo:
        helpers._post_openai_compat_json(
            endpoint="https://api.deepseek.com/v1/chat/completions",
            payload={"model": "deepseek-v4-flash", "messages": []},
            api_key="deep-key",
            timeout_s=1.0,
        )

    assert "HTTP 400" in str(excinfo.value)
    assert "Empty input messages" in str(excinfo.value)


def test_non_surface_json_format_stays_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_JSON_TEXT_FALLBACK_SCENES", "CHAT_SURFACE")

    with pytest.raises(ValueError, match="valid json object"):
        helpers._validate_pool_response(
            content="route chat",
            response_format={"type": "json_object"},
            scene_type="EXEC_INTENT",
        )


def test_pool_ssl_eof_gets_provider_unavailable_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_UNAVAILABLE_COOLDOWN_MS", "2345")

    reason_code, cooldown_ms = helpers._classify_pool_error(RuntimeError("EOF occurred in violation of protocol (_ssl.c:992)"))

    assert reason_code == "POOL_PROVIDER_UNAVAILABLE"
    assert cooldown_ms == 2345
