from __future__ import annotations

import pytest

from host_brain.integrations import llm_helpers as helpers




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


def test_openai_compat_payload_maps_typed_deepseek_thinking_mode() -> None:
    payload = helpers._build_openai_compat_payload(
        provider_name="deepseek",
        model_id="deepseek-v4-flash",
        messages=[{"role": "user", "content": "陪我说两句"}],
        gen_params={"thinking_mode": "disabled", "max_tokens": 640},
    )

    assert payload["thinking"] == {"type": "disabled"}
    assert "thinking_mode" not in payload


@pytest.mark.parametrize("configured_mode", (None, "enabled", "disabled"))
def test_deepseek_v4_flash_always_uses_non_thinking_mode(
    configured_mode: str | None,
) -> None:
    gen_params = {"max_tokens": 640}
    if configured_mode is not None:
        gen_params["thinking_mode"] = configured_mode

    payload = helpers._build_openai_compat_payload(
        provider_name="deepseek",
        model_id="deepseek-v4-flash",
        messages=[{"role": "user", "content": "陪我说两句"}],
        gen_params=gen_params,
    )

    assert payload["thinking"] == {"type": "disabled"}


def test_openai_compat_payload_does_not_leak_thinking_mode_to_other_provider() -> None:
    payload = helpers._build_openai_compat_payload(
        provider_name="gemini",
        model_id="gemini-2.5-flash-lite",
        messages=[{"role": "user", "content": "陪我说两句"}],
        gen_params={"thinking_mode": "disabled", "max_tokens": 640},
    )

    assert "thinking" not in payload
    assert "thinking_mode" not in payload


def test_openai_compat_payload_rejects_invalid_thinking_mode() -> None:
    with pytest.raises(ValueError, match="thinking_mode"):
        helpers._build_openai_compat_payload(
            provider_name="deepseek",
            model_id="deepseek-v4-flash",
            messages=[{"role": "user", "content": "陪我说两句"}],
            gen_params={"thinking_mode": "sometimes"},
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

    monkeypatch.setenv("HOST_LLM_OPENAI_COMPAT_KEEPALIVE_ENABLED", "true")
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
