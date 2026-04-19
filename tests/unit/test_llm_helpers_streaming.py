from __future__ import annotations

from types import SimpleNamespace

from host_brain.integrations.llm_helpers import stream_llm_chunks


def test_stream_llm_chunks_passes_max_tokens_to_chat_openai(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)

        def stream(self, _messages):  # noqa: ANN001
            yield SimpleNamespace(content="你好。")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", _FakeChatOpenAI)

    chunks = list(
        stream_llm_chunks(
            provider_name="gemini",
            model_id="gemini-2.5-flash-lite",
            api_key="test-key",
            timeout_ms=5000,
            prompt_text="今天有点累，抱抱我吧。",
            system_prompt="你是夏目蓝。",
            gen_params={"temperature": 0.7, "top_p": 0.9, "max_tokens": 96},
        )
    )

    assert chunks == ["你好。"]
    assert captured["max_tokens"] == 96


def test_stream_llm_chunks_accepts_max_output_tokens_alias(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)

        def stream(self, _messages):  # noqa: ANN001
            yield SimpleNamespace(content="在。")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", _FakeChatOpenAI)

    chunks = list(
        stream_llm_chunks(
            provider_name="gemini",
            model_id="gemini-2.5-flash-lite",
            api_key="test-key",
            timeout_ms=5000,
            prompt_text="在吗",
            system_prompt="你是夏目蓝。",
            gen_params={"max_output_tokens": 128},
        )
    )

    assert chunks == ["在。"]
    assert captured["max_tokens"] == 128
