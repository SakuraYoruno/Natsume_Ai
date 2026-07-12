from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from edge_client.channels import wechat_command_queue
from host_brain.channels import mateengine
from host_brain.service_catalog import AuthKind
from host_brain.service_catalog.defaults import build_local_catalog
from natsume_contracts.bridge_auth import build_http_auth_headers
from tools import wxautox4_bridge


MATE_SECRET = "m" * 32
WECHAT_SECRET = "w" * 32
ROOT = Path(__file__).resolve().parents[2]


class _Response:
    status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    @staticmethod
    def read() -> bytes:
        return b'{"success":true}'


def test_mate_producer_defaults_off_and_sends_exact_signed_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HOST_MATEENGINE_SIGNAL_ENABLED", raising=False)
    monkeypatch.delenv("HOST_MATEENGINE_BRIDGE_ENABLED", raising=False)
    assert mateengine._mateengine_enabled() is False
    assert mateengine._mateengine_bridge_enabled() is False

    monkeypatch.setenv("HOST_MATEENGINE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID", "mate.client.v1")
    monkeypatch.setenv("NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET", MATE_SECRET)
    monkeypatch.setenv(
        "NATSUME_MATEENGINE_BRIDGE_CLIENT_PRINCIPAL",
        "host.mateengine.bridge",
    )
    monkeypatch.setattr(mateengine.time, "time", lambda: 1_783_828_800.123)
    monkeypatch.setattr(mateengine.secrets, "token_urlsafe", lambda _size: "nonce-client-1")
    captured: dict[str, object] = {}

    def _urlopen(request, timeout):  # noqa: ANN001
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(mateengine.urllib.request, "urlopen", _urlopen)
    result = mateengine.send_mateengine_command({"action": "say", "text": "safe"})

    assert result.ok is True
    request = captured["request"]
    raw_body = bytes(request.data)
    actual_headers = {key.lower(): value for key, value in request.header_items()}
    expected_headers = build_http_auth_headers(
        secret=MATE_SECRET.encode("utf-8"),
        key_id="mate.client.v1",
        method="POST",
        path="/command",
        timestamp_ms=1_783_828_800_123,
        nonce="nonce-client-1",
        principal="host.mateengine.bridge",
        scope="bridge:mateengine:admin",
        raw_body=raw_body,
    )
    for key, value in expected_headers.items():
        assert actual_headers[key.lower()] == value


def test_mate_http_never_falls_back_to_unsigned_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_MATEENGINE_BRIDGE_ENABLED", "true")
    monkeypatch.delenv("NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET", raising=False)
    monkeypatch.delenv("NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID", raising=False)
    calls: list[object] = []
    monkeypatch.setattr(
        mateengine.urllib.request,
        "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = mateengine.send_mateengine_command({"action": "say", "text": "blocked"})

    assert result.ok is False
    assert result.error == "HOST_MATEENGINE_BRIDGE_CREDENTIAL_REQUIRED"
    assert calls == []


def test_wechat_queue_producer_emits_consumer_compatible_signed_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NATSUME_BRIDGE_EXPOSURE_MODE", "LOOPBACK")
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("NATSUME_WECHAT_BRIDGE_HMAC_KEY_ID", "wechat.queue.v1")
    monkeypatch.setenv("NATSUME_WECHAT_BRIDGE_HMAC_SECRET", WECHAT_SECRET)
    monkeypatch.setenv(
        "NATSUME_WECHAT_BRIDGE_PRINCIPAL_ID",
        "edge.gateway.wechat.bridge",
    )
    monkeypatch.setattr(
        wechat_command_queue.secrets,
        "token_urlsafe",
        lambda _size: "nonce-wechat-1",
    )
    monkeypatch.setattr(
        wechat_command_queue.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="12345678abcdef"),
    )
    queue_path = tmp_path / "commands.jsonl"

    row = wechat_command_queue.enqueue_wechat_command(
        action="send_text",
        target="wxid-safe",
        content="hello",
        request_id="req-safe",
        queue_file=queue_path,
        ts_ms=1_783_828_800_123,
    )
    body = {
        key: row[key]
        for key in ("command_id", "request_id", "action", "target", "content", "ts_ms")
    }
    expected = wxautox4_bridge.sign_bridge_command_row(
        body,
        secret=WECHAT_SECRET,
        key_id="wechat.queue.v1",
        principal="edge.gateway.wechat.bridge",
        timestamp_ms=1_783_828_800_123,
        nonce="nonce-wechat-1",
        source="loopback",
    )

    assert row == expected
    assert queue_path.read_text(encoding="utf-8").strip()


def test_wechat_queue_fails_closed_before_file_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NATSUME_BRIDGE_EXPOSURE_MODE", raising=False)
    monkeypatch.delenv("EDGE_WECHAT_BRIDGE_ENABLED", raising=False)
    queue_path = tmp_path / "commands.jsonl"

    with pytest.raises(RuntimeError, match="BRIDGE_EXPOSURE_DISABLED"):
        wechat_command_queue.enqueue_wechat_command(
            action="send_text",
            target="wxid-blocked",
            queue_file=queue_path,
        )

    assert queue_path.exists() is False


def test_catalog_uses_secure_mate_defaults_and_explicit_18770_owner() -> None:
    default = build_local_catalog({})
    mate_http = default.by_id()["host.mateengine.bridge.http"]
    mate_udp = default.by_id()["host.mateengine.bridge.udp"]
    assert mate_http.enabled is False
    assert mate_udp.enabled is False
    assert mate_http.auth_kind is AuthKind.HMAC
    assert mate_udp.auth_kind is AuthKind.HMAC
    assert mate_udp.bind_host == "127.0.0.1"

    with pytest.raises(ValueError, match="SERVICE_CATALOG_EMBODIMENT_OWNER_REQUIRED"):
        build_local_catalog(
            {
                "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
                "EDGE_AVATAR_BACKEND": "vrm",
            }
        )

    resolved = build_local_catalog(
        {
            "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
            "EDGE_AVATAR_BACKEND": "vrm",
            "NATSUME_EMBODIMENT_BRIDGE_OWNER": "mateengine",
        }
    )
    claims = [
        endpoint
        for endpoint in resolved.endpoints
        if endpoint.listener_id == "embodiment.bridge.http" and endpoint.enabled
    ]
    assert [(item.owner_id, item.endpoint_id) for item in claims] == [
        ("host.mateengine.bridge", "host.mateengine.bridge.http")
    ]


def test_host_launcher_never_restarts_or_adopts_an_incumbent_bridge() -> None:
    script = (ROOT / "scripts/runtime/start_host_side.ps1").read_text(encoding="utf-8")
    section = script.split("function Start-MateEngineBridgeRuntime", 1)[1].split(
        "$repoRoot =",
        1,
    )[0]

    assert "-Default $false" in section
    assert "Stop-ProcessTree" not in section
    assert "existing_bridge_requires_operator_handoff" in section
    assert "incumbent_listener_present" in section
    assert "mateengine_bridge_health_probe.py" in section
    assert "authenticated_health_check_failed" in section
    assert "Stop-Process -Id ([int]$proc.Id)" in section
    assert "Wait-Http -Url $healthUrlValue" not in section
    env_template = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "HOST_MATEENGINE_SIGNAL_ENABLED=false" in env_template
