from __future__ import annotations

import json
from types import SimpleNamespace

import host_brain.integrations.llm_helpers as llm_helpers
from host_brain.integrations.llm_helpers import stream_llm_chunks


def test_stream_llm_chunks_passes_max_tokens_to_native_gemini(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeChatGoogleGenerativeAI:
        def __init__(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)

        def stream(self, _messages):  # noqa: ANN001
            yield SimpleNamespace(content="你好。")

    monkeypatch.setattr("langchain_google_genai.ChatGoogleGenerativeAI", _FakeChatGoogleGenerativeAI)

    chunks = list(
        stream_llm_chunks(
            provider_name="gemini",
            model_id="gemini-2.5-flash-lite",
            api_key="test-key",
            timeout_ms=5000,
            messages=[
                {"role": "system", "content": "你是夏目蓝。"},
                {"role": "user", "content": "今天有点累，抱抱我吧。"},
            ],
            gen_params={"temperature": 0.7, "top_p": 0.9, "max_tokens": 96},
        )
    )

    assert chunks == ["你好。"]
    assert captured["max_tokens"] == 96
    assert captured["model"] == "gemini-2.5-flash-lite"
    assert captured["api_key"] == "test-key"


def test_stream_llm_chunks_accepts_max_output_tokens_alias(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeChatGoogleGenerativeAI:
        def __init__(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)

        def stream(self, _messages):  # noqa: ANN001
            yield SimpleNamespace(content="在。")

    monkeypatch.setattr("langchain_google_genai.ChatGoogleGenerativeAI", _FakeChatGoogleGenerativeAI)

    chunks = list(
        stream_llm_chunks(
            provider_name="gemini",
            model_id="gemini-2.5-flash-lite",
            api_key="test-key",
            timeout_ms=5000,
            messages=[
                {"role": "system", "content": "你是夏目蓝。"},
                {"role": "user", "content": "在吗"},
            ],
            gen_params={"max_output_tokens": 128},
        )
    )

    assert chunks == ["在。"]
    assert captured["max_tokens"] == 128


def test_stream_llm_chunks_raises_tiny_cap_for_native_gemini_pro(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeChatGoogleGenerativeAI:
        def __init__(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)

        def stream(self, _messages):  # noqa: ANN001
            yield SimpleNamespace(content=[{"type": "text", "text": "你好"}])

    monkeypatch.setattr("langchain_google_genai.ChatGoogleGenerativeAI", _FakeChatGoogleGenerativeAI)

    chunks = list(
        stream_llm_chunks(
            provider_name="gemini",
            model_id="gemini-3.1-pro-preview",
            api_key="test-key",
            timeout_ms=5000,
            messages=[{"role": "user", "content": "hello"}],
            gen_params={"max_output_tokens": 16},
        )
    )

    assert chunks == ["你好"]
    assert captured["max_tokens"] == 1024


class _FakeStreamingHTTPResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = [line.encode("utf-8") for line in lines]
        self._index = 0

    def readline(self) -> bytes:
        if self._index >= len(self._lines):
            return b""
        value = self._lines[self._index]
        self._index += 1
        return value

    def __enter__(self) -> "_FakeStreamingHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return False


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return False


class _FakeRequestsResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return dict(self._payload)


def test_invoke_llm_text_uses_direct_openai_compat_http(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("HOST_LLM_OPENAI_COMPAT_KEEPALIVE_ENABLED", "false")

    def _fake_urlopen(request, timeout=0):  # noqa: ANN001, ANN202
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["auth"] = request.headers.get("Authorization")
        return _FakeHTTPResponse({"choices": [{"message": {"content": "在。"}}]})

    monkeypatch.setattr(llm_helpers.urllib.request, "urlopen", _fake_urlopen)

    text = llm_helpers.invoke_llm_text(
        provider_name="deepseek",
        model_id="deepseek-v4-flash",
        api_key="test-key",
        timeout_ms=5000,
        messages=[{"role": "user", "content": "在吗"}],
        gen_params={"max_output_tokens": 32},
    )

    assert text == "在。"
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["timeout"] == 5.0
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"] == {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "在吗"}],
        "stream": False,
        "temperature": 0.4,
        "top_p": 0.9,
        "max_tokens": 128,
    }


def test_invoke_llm_text_reuses_openai_compat_session(monkeypatch) -> None:
    class _FakeSession:
        def __init__(self) -> None:
            self.posts: list[dict[str, object]] = []

        def post(self, endpoint, json=None, headers=None, timeout=0):  # noqa: ANN001, ANN202
            self.posts.append(
                {
                    "endpoint": endpoint,
                    "json": dict(json or {}),
                    "headers": dict(headers or {}),
                    "timeout": timeout,
                }
            )
            return _FakeRequestsResponse({"choices": [{"message": {"content": "在。"}}]})

    session = _FakeSession()
    monkeypatch.setenv("HOST_LLM_OPENAI_COMPAT_KEEPALIVE_ENABLED", "true")
    monkeypatch.setattr(llm_helpers, "_OPENAI_COMPAT_SESSION", session)
    monkeypatch.setattr(
        llm_helpers.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("urlopen should not be used")),
    )

    first = llm_helpers.invoke_llm_text(
        provider_name="openai",
        model_id="gpt-5.4-mini",
        api_key="test-key",
        timeout_ms=5000,
        messages=[{"role": "user", "content": "在吗"}],
    )
    second = llm_helpers.invoke_llm_text(
        provider_name="openai",
        model_id="gpt-5.4-mini",
        api_key="test-key",
        timeout_ms=5000,
        messages=[{"role": "user", "content": "还在吗"}],
    )

    assert first == "在。"
    assert second == "在。"
    assert len(session.posts) == 2
    assert session.posts[0]["endpoint"] == "https://api.openai.com/v1/chat/completions"
    assert session.posts[0]["headers"]["Authorization"] == "Bearer test-key"


def test_stream_llm_chunks_uses_direct_openai_compat_sse(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(request, timeout=0):  # noqa: ANN001, ANN202
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeStreamingHTTPResponse(
            [
                'data: {"choices":[{"delta":{"content":"夏目"}}]}\n',
                "\n",
                'data: {"choices":[{"delta":{"content":"蓝。"}}]}\n',
                "data: [DONE]\n",
            ]
        )

    monkeypatch.setattr(llm_helpers.urllib.request, "urlopen", _fake_urlopen)

    chunks = list(
        stream_llm_chunks(
            provider_name="deepseek",
            model_id="deepseek-v4-flash",
            api_key="test-key",
            timeout_ms=5000,
            messages=[{"role": "user", "content": "你是谁"}],
        )
    )

    assert chunks == ["夏目", "蓝。"]
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["body"]["stream"] is True


def test_stream_llm_chunks_uses_minimax_standard_sse_for_m2_her(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(request, timeout=0):  # noqa: ANN001, ANN202
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeStreamingHTTPResponse(
            [
                'data: {"choices":[{"delta":{"content":"夏目"}}]}\n',
                "\n",
                'data: {"choices":[{"delta":{"content":"蓝。"}}]}\n',
                "\n",
                'data: {"choices":[{"message":{"content":"夏目蓝。"}}]}\n',
                "\n",
            ]
        )

    monkeypatch.setattr(llm_helpers.urllib.request, "urlopen", _fake_urlopen)

    chunks = list(
        stream_llm_chunks(
            provider_name="minimax",
            model_id="M2-her",
            api_key="test-key",
            timeout_ms=5000,
            messages=[
                {"role": "system", "content": "你是夏目蓝。"},
                {"role": "user", "content": "你是"},
            ],
        )
    )

    assert chunks == ["夏目", "蓝。"]
    assert captured["url"] == "https://api.minimaxi.com/v1/text/chatcompletion_v2"
    assert captured["timeout"] == 5.0
    assert captured["body"] == {
        "model": "M2-her",
        "messages": [
            {"role": "system", "content": "你是夏目蓝。"},
            {"role": "user", "content": "你是"},
        ],
        "stream": True,
        "temperature": 0.4,
        "top_p": 0.9,
        "max_completion_tokens": 1024,
    }


def test_stream_llm_chunks_minimax_standard_falls_back_to_final_message_when_no_delta(monkeypatch) -> None:
    def _fake_urlopen(_request, timeout=0):  # noqa: ANN001, ANN202
        return _FakeStreamingHTTPResponse(
            [
                'data: {"choices":[{"message":{"content":"夏目蓝。"}}]}\n',
                "\n",
            ]
        )

    monkeypatch.setattr(llm_helpers.urllib.request, "urlopen", _fake_urlopen)

    chunks = list(
        stream_llm_chunks(
            provider_name="minimax",
            model_id="M2-her",
            api_key="test-key",
            timeout_ms=5000,
            messages=[
                {"role": "user", "content": "你是"},
            ],
        )
    )

    assert chunks == ["夏目蓝。"]
