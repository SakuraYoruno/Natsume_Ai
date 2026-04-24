from __future__ import annotations

import json

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


def test_minimax_m2_her_uses_standard_chatcompletion_endpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(provider_factory, "_build_host_openai_system_prompt", lambda _node_id: "sys-prompt")
    monkeypatch.setattr(provider_factory, "render_host_generation_prompt", lambda _payload: "user-prompt")
    monkeypatch.setattr(provider_factory, "coerce_gateway_payload_json_text", lambda text: text)

    def _fake_urlopen(request, timeout=0):  # noqa: ANN001, ANN202
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "夏目蓝。",
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(provider_factory.urllib.request, "urlopen", _fake_urlopen)

    provider_fn = provider_factory.build_host_provider_fn(
        provider_name="minimax",
        model_id="M2-her",
        api_key="mini-key",
        timeout_ms=3200,
        base_url_override="https://api.minimaxi.com/v1",
    )
    result = provider_fn({"input": {"user_message": "你是"}})

    assert result == "夏目蓝。"
    assert captured["url"] == "https://api.minimaxi.com/v1/text/chatcompletion_v2"
    assert captured["timeout"] == 3.2
    assert captured["body"] == {
        "model": "M2-her",
        "messages": [
            {"role": "system", "name": "NatsumeHost", "content": "sys-prompt"},
            {"role": "user", "name": "User", "content": "user-prompt"},
        ],
        "stream": False,
        "temperature": 0.4,
        "top_p": 0.9,
        "max_completion_tokens": 1024,
    }


def test_minimax_standard_endpoint_can_be_derived_from_base_override() -> None:
    assert provider_factory._resolve_minimax_standard_endpoint("https://api.minimaxi.com/v1") == (
        "https://api.minimaxi.com/v1/text/chatcompletion_v2"
    )
    assert provider_factory._resolve_minimax_standard_endpoint(
        "https://api.minimaxi.com/v1/text/chatcompletion_v2"
    ) == "https://api.minimaxi.com/v1/text/chatcompletion_v2"
