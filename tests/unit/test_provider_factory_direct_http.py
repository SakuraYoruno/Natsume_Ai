from __future__ import annotations

import json
import sys

import host_brain.app.provider_factory as provider_factory


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return False


def test_host_provider_uses_direct_openai_compat_http(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(provider_factory, "_build_host_openai_system_prompt", lambda _node_id: "sys-prompt")
    monkeypatch.setattr(provider_factory, "render_host_generation_prompt", lambda _payload: "user-prompt")
    monkeypatch.setattr(provider_factory, "coerce_gateway_payload_json_text", lambda text: text)

    def _fake_urlopen(request, timeout=0):  # noqa: ANN001, ANN202
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["auth"] = request.headers.get("Authorization")
        return _FakeHTTPResponse({"choices": [{"message": {"content": "在。"}}]})

    monkeypatch.setattr(provider_factory.urllib.request, "urlopen", _fake_urlopen)
    before_modules = set(sys.modules)

    provider_fn = provider_factory.build_host_provider_fn(
        provider_name="deepseek",
        model_id="deepseek-v4-flash",
        api_key="deepseek-key",
        timeout_ms=4500,
        base_url_override="https://api.deepseek.com/v1",
    )
    result = provider_fn({"input": {"user_message": "在吗"}})

    assert result == "在。"
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["timeout"] == 4.5
    assert captured["auth"] == "Bearer deepseek-key"
    assert captured["body"] == {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "sys-prompt"},
            {"role": "user", "content": "user-prompt"},
        ],
        "stream": False,
        "temperature": 0.4,
        "top_p": 0.9,
        "max_tokens": 1024,
    }
    new_modules = set(sys.modules) - before_modules
    assert "langchain_openai" not in new_modules
    assert "langchain_google_genai" not in new_modules
    assert "transformers" not in new_modules
    assert "torch" not in new_modules
