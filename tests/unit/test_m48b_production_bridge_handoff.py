from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from edge_client.channels import wechat_command_queue
from host_brain.channels import mateengine
from host_brain.channels.embodiment import EmbodimentOutboxAck
from host_brain.service_catalog import AuthKind
from host_brain.service_catalog.defaults import build_local_catalog
from tools import wxautox4_bridge


WECHAT_SECRET = "w" * 32
ROOT = Path(__file__).resolve().parents[2]


def test_mate_producer_defaults_off_and_queues_one_edge_embodiment_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HOST_MATEENGINE_SIGNAL_ENABLED", raising=False)
    monkeypatch.delenv("HOST_MATEENGINE_BRIDGE_ENABLED", raising=False)
    assert mateengine._mateengine_enabled() is False
    assert mateengine._mateengine_bridge_enabled() is False

    monkeypatch.setattr(mateengine.time, "time", lambda: 1_783_828_800.123)
    captured: dict[str, object] = {}

    def _enqueue(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        return EmbodimentOutboxAck(
            outbox_id="outbox-mate-1",
            command_id="command-mate-1",
            idempotency_key="mate-key-1",
        )

    monkeypatch.setattr(mateengine, "queue_mateengine_command", _enqueue)
    result = mateengine.send_mateengine_command({"action": "say", "text": "safe"})

    assert result.ok is True
    assert result.backend == "edge_embodiment_v1"
    assert result.target == "edge://mateengine/command"
    assert captured == {
        "action": "command",
        "payload": {"command": {"action": "say", "text": "safe"}},
        "request_id": "mateengine_1783828800123",
    }


def test_mate_queue_rejection_is_returned_without_host_device_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mateengine,
        "queue_mateengine_command",
        lambda **_kwargs: EmbodimentOutboxAck(
            outbox_id="outbox-mate-rejected",
            command_id="command-mate-rejected",
            idempotency_key="mate-key-rejected",
            status="REJECTED",
            reason_code="EDGE_EMBODIMENT_REJECTED",
            detail="edge rejected command",
        ),
    )

    result = mateengine.send_mateengine_command({"action": "say", "text": "blocked"})

    assert result.ok is False
    assert result.backend == "edge_embodiment_v1"
    assert result.error == "edge rejected command"


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
