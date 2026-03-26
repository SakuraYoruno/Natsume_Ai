from __future__ import annotations

import asyncio

from edge_client.actuators.vts_driver import VtsConfig, _authenticate_vts


class _FakeVtsRequestFactory:
    @staticmethod
    def authentication_token():
        return ("authentication_token",)

    @staticmethod
    def authentication(token: str):
        return ("authentication", token)


class _FakeVts:
    def __init__(
        self,
        *,
        persisted_token: str = "",
        auth_responses: list[dict],
        token_responses: list[dict],
    ) -> None:
        self.persisted_token = persisted_token
        self.auth_responses = list(auth_responses)
        self.token_responses = list(token_responses)
        self.authentic_token = ""
        self.connect_calls = 0
        self.write_token_calls = 0
        self.auth_tokens_seen: list[str] = []
        self.vts_request = _FakeVtsRequestFactory()

    async def connect(self) -> None:
        self.connect_calls += 1

    async def read_token(self) -> str:
        return self.persisted_token

    async def write_token(self) -> None:
        self.write_token_calls += 1
        self.persisted_token = self.authentic_token

    async def request(self, request):
        request_type = request[0]
        if request_type == "authentication_token":
            return self.token_responses.pop(0)
        if request_type == "authentication":
            self.auth_tokens_seen.append(request[1])
            return self.auth_responses.pop(0)
        raise AssertionError(f"unexpected request: {request}")


def test_authenticate_vts_refreshes_invalid_stale_token(monkeypatch) -> None:
    fake_vts = _FakeVts(
        persisted_token="stale-token",
        auth_responses=[
            {
                "data": {
                    "authenticated": False,
                    "reason": "Authentication request failed because token is invalid or has been revoked by the user.",
                }
            },
            {
                "data": {
                    "authenticated": True,
                    "reason": "Token valid. The plugin is authenticated for the duration of this session.",
                }
            },
        ],
        token_responses=[
            {
                "data": {
                    "authenticationToken": "fresh-token",
                }
            }
        ],
    )

    monkeypatch.setattr(
        "edge_client.actuators.vts_driver._run_in_vts_loop",
        lambda coro, timeout_s: asyncio.run(coro),
    )

    _authenticate_vts(
        vts=fake_vts,
        plugin_name="NatsumeAi",
        plugin_developer="Natsume",
        token_path=".vts_token",
    )

    assert fake_vts.connect_calls == 1
    assert fake_vts.auth_tokens_seen == ["stale-token", "fresh-token"]
    assert fake_vts.write_token_calls == 1
    assert fake_vts.persisted_token == "fresh-token"


def test_vts_config_normalizes_known_plugin_name_typo() -> None:
    cfg = VtsConfig(plugin_name=" NastumeAi ")

    assert cfg.plugin_name == "NatsumeAi"
