from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tools import mateengine_bridge_server as bridge


SECRET = "0123456789abcdef0123456789abcdef"
PLUGIN_SECRET = "plugin-0123456789abcdef0123456789abc"
KEY_ID = "task0494-key"
PLUGIN_KEY_ID = "task0494-plugin-key"
PRINCIPAL = "host.runtime"


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    values = {
        "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
        "NATSUME_BRIDGE_EXPOSURE_MODE": "LOOPBACK",
        "NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID": KEY_ID,
        "NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET": SECRET,
        "NATSUME_MATEENGINE_PLUGIN_HMAC_KEY_ID": PLUGIN_KEY_ID,
        "NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET": PLUGIN_SECRET,
        "NATSUME_MATEENGINE_BRIDGE_ALLOWED_PRINCIPALS": PRINCIPAL,
        "NATSUME_MATEENGINE_BRIDGE_ALLOWED_SCOPES": ",".join(sorted(bridge.KNOWN_SCOPES)),
        "NATSUME_BRIDGE_RATE_PER_SECOND": "10",
        "NATSUME_BRIDGE_RATE_BURST": "20",
        "NATSUME_BRIDGE_NONCE_TTL_SECONDS": "30",
        "NATSUME_BRIDGE_MAX_BODY_BYTES": "65536",
        "NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH": str(tmp_path / "audit" / "bridge.jsonl"),
        "NATSUME_MATEENGINE_BRIDGE_REPLAY_LEDGER_PATH": str(tmp_path / "audit" / "replay.sqlite3"),
        "NATSUME_MATEENGINE_PLUGIN_PRINCIPAL": "host.mateengine.bridge",
        "MATEENGINE_BRIDGE_HTTP_HOST": "127.0.0.1",
        "MATEENGINE_BRIDGE_HTTP_PORT": "18770",
        "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
        "MATEENGINE_BRIDGE_UDP_PORT": "18771",
        "MATEENGINE_BRIDGE_UDP_ENABLED": "false",
        "MATEENGINE_SIGNAL_HOST": "127.0.0.1",
        "MATEENGINE_SIGNAL_PORT": "32145",
        "MATEENGINE_PLUGIN_HOST": "127.0.0.1",
        "MATEENGINE_PLUGIN_PORT": "32146",
    }
    values.update(overrides)
    return values


def _runtime(tmp_path: Path, **overrides: str) -> bridge.BridgeSecurityRuntime:
    return bridge.BridgeSecurityRuntime(
        bridge.build_security_config(_env(tmp_path, **overrides))
    )


def _metadata(
    runtime: bridge.BridgeSecurityRuntime,
    raw_body: bytes,
    *,
    scope: str,
    nonce: str = "nonce-0000000000000001",
    timestamp_ms: int = 1_700_000_000_000,
    transport: str = "http",
    method: str = "POST",
    path: str = "/command",
    principal: str = PRINCIPAL,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "version": bridge.AUTH_VERSION,
        "key_id": runtime.config.key_id,
        "timestamp_ms": timestamp_ms,
        "nonce": nonce,
        "principal": principal,
        "scope": scope,
    }
    values["signature"] = bridge.build_hmac_signature(
        runtime.config.hmac_secret,
        version=values["version"],
        key_id=values["key_id"],
        transport=transport,
        method=method,
        path=path,
        timestamp_ms=timestamp_ms,
        nonce=nonce,
        principal=principal,
        scope=scope,
        body_digest=bridge.body_sha256(raw_body),
    )
    return values


def test_default_configuration_is_disabled_loopback_and_udp_off() -> None:
    config = bridge.build_security_config({})

    assert config.exposure_mode == bridge.EXPOSURE_MODE_DISABLED
    assert config.http_host == "127.0.0.1"
    assert config.udp_host == "127.0.0.1"
    assert config.udp_enabled is False


def test_canonical_payload_is_exact_ten_utf8_lines_without_trailing_newline() -> None:
    digest = "a" * 64
    payload = bridge.canonical_hmac_payload(
        version=bridge.AUTH_VERSION,
        key_id="key-1",
        transport="HTTP",
        method="post",
        path="/command",
        timestamp_ms=1_700_000_000_000,
        nonce="nonce-1",
        principal="principal-1",
        scope=bridge.SCOPE_SAY,
        body_digest=digest.upper(),
    )

    assert payload == (
        "natsume.bridge-auth.v1\n"
        "key-1\n"
        "http\n"
        "POST\n"
        "/command\n"
        "1700000000000\n"
        "nonce-1\n"
        "principal-1\n"
        "bridge:mateengine:say\n"
        + digest
    ).encode("utf-8")
    assert not payload.endswith(b"\n")


def test_cross_language_hmac_golden_vector_is_stable() -> None:
    raw_body = b'{"type":"say","text":"hello"}'
    digest = "46cfda4824baea763cf4462582770878c60180f6fca266386c843459e2dd50f0"

    assert bridge.body_sha256(raw_body) == digest
    assert bridge.build_hmac_signature(
        SECRET.encode("utf-8"),
        version=bridge.AUTH_VERSION,
        key_id="key-1",
        transport="http",
        method="POST",
        path="/command",
        timestamp_ms=1_700_000_000_000,
        nonce="nonce-1",
        principal="principal-1",
        scope=bridge.SCOPE_SAY,
        body_digest=digest,
    ) == "sha256=eda7964ea8ff909be27c5443fbc83e1cfbf7a52d4ca8b4cb12ce3ac7eb022aab"


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"MATEENGINE_BRIDGE_HTTP_HOST": "0.0.0.0"}, "loopback HTTP"),
        ({"HOST_MATEENGINE_BRIDGE_ENABLED": "false"}, "HOST_MATEENGINE_BRIDGE_ENABLED"),
        (
            {
                "MATEENGINE_BRIDGE_UDP_ENABLED": "true",
                "MATEENGINE_BRIDGE_UDP_HOST": "0.0.0.0",
            },
            "loopback UDP",
        ),
        ({"MATEENGINE_SIGNAL_HOST": "192.0.2.10"}, "signal downstream"),
        ({"MATEENGINE_PLUGIN_HOST": "192.0.2.11"}, "plugin downstream"),
        ({"NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET": "too-short"}, "at least 32"),
        ({"NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID": ""}, "KEY_ID"),
        ({"NATSUME_MATEENGINE_BRIDGE_ALLOWED_PRINCIPALS": ""}, "ALLOWED_PRINCIPALS"),
        ({"NATSUME_MATEENGINE_PLUGIN_HMAC_KEY_ID": ""}, "PLUGIN_HMAC_KEY_ID"),
        ({"NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET": "short"}, "PLUGIN_HMAC_SECRET"),
        ({"NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET": SECRET}, "secrets must be distinct"),
        ({"NATSUME_MATEENGINE_PLUGIN_HMAC_KEY_ID": KEY_ID}, "key IDs must be distinct"),
        ({"NATSUME_BRIDGE_RATE_PER_SECOND": "0"}, "RATE_PER_SECOND"),
        ({"NATSUME_BRIDGE_RATE_BURST": "0"}, "RATE_BURST"),
        ({"NATSUME_BRIDGE_NONCE_TTL_SECONDS": "0"}, "NONCE_TTL"),
        ({"NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH": ""}, "AUDIT_JSONL_PATH"),
        ({"NATSUME_MATEENGINE_BRIDGE_REPLAY_LEDGER_PATH": ""}, "REPLAY_LEDGER_PATH"),
    ],
)
def test_loopback_startup_fails_closed_on_incomplete_security(
    tmp_path: Path,
    overrides: dict[str, str],
    fragment: str,
) -> None:
    config = bridge.build_security_config(_env(tmp_path, **overrides))

    with pytest.raises(ValueError, match=fragment):
        bridge.BridgeSecurityRuntime(config)


def test_remote_approved_requires_approval_reference(tmp_path: Path) -> None:
    config = bridge.build_security_config(
        _env(
            tmp_path,
            NATSUME_BRIDGE_EXPOSURE_MODE="REMOTE_APPROVED",
            MATEENGINE_BRIDGE_HTTP_HOST="0.0.0.0",
        )
    )

    with pytest.raises(ValueError, match="REMOTE_APPROVED"):
        bridge.BridgeSecurityRuntime(config)

    approved = bridge.build_security_config(
        _env(
            tmp_path,
            NATSUME_BRIDGE_EXPOSURE_MODE="REMOTE_APPROVED",
            NATSUME_MATEENGINE_BRIDGE_REMOTE_APPROVAL_REF="MASTER-approval-0494",
            MATEENGINE_BRIDGE_HTTP_HOST="0.0.0.0",
        )
    )
    assert bridge.BridgeSecurityRuntime(approved).config.approval_ref


def test_remote_approved_requires_explicit_rate_controls(tmp_path: Path) -> None:
    env = _env(
        tmp_path,
        NATSUME_BRIDGE_EXPOSURE_MODE="REMOTE_APPROVED",
        NATSUME_MATEENGINE_BRIDGE_REMOTE_APPROVAL_REF="MASTER-approval-0494",
        MATEENGINE_BRIDGE_HTTP_HOST="0.0.0.0",
    )
    env.pop("NATSUME_BRIDGE_RATE_PER_SECOND")
    env.pop("NATSUME_BRIDGE_RATE_BURST")

    with pytest.raises(ValueError, match="explicit NATSUME_BRIDGE_RATE_PER_SECOND"):
        bridge.BridgeSecurityRuntime(bridge.build_security_config(env))


def test_audit_and_replay_derived_paths_cannot_collide(tmp_path: Path) -> None:
    audit_path = tmp_path / "bridge-audit.jsonl"
    config = bridge.build_security_config(
        _env(
            tmp_path,
            NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH=str(audit_path),
            NATSUME_MATEENGINE_BRIDGE_REPLAY_LEDGER_PATH=f"{audit_path}.1",
        )
    )

    with pytest.raises(ValueError, match="path families must be distinct"):
        bridge.BridgeSecurityRuntime(config)


def test_torn_audit_terminal_row_fails_closed_before_runtime_start(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "audit" / "bridge.jsonl"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(
        '{"event":"previous","ts_ms":1700000000000}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="torn terminal row"):
        _runtime(tmp_path)


def test_preauth_source_limiter_bounds_unauthenticated_audit_pressure(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        NATSUME_BRIDGE_PREAUTH_RATE_PER_SECOND="1",
        NATSUME_BRIDGE_PREAUTH_RATE_BURST="1",
    )

    assert runtime.admit_source(
        remote_host="192.0.2.10",
        transport="http",
        now_monotonic=10.0,
    ) is True
    assert runtime.admit_source(
        remote_host="192.0.2.10",
        transport="http",
        now_monotonic=10.0,
    ) is False
    assert runtime.admit_source(
        remote_host="192.0.2.10",
        transport="http",
        now_monotonic=10.1,
    ) is False

    rows = [
        json.loads(line)
        for line in runtime.config.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(row.get("event") == "preauth_rate_limited" for row in rows) == 1


def test_loopback_command_and_health_both_require_authentication(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    raw = b'{"type":"say","text":"hello"}'

    rejected = runtime.authorize(
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        raw_body=raw,
        metadata={},
        required_scopes=(bridge.SCOPE_SAY,),
        now_ms=1_700_000_000_000,
        now_monotonic=10.0,
    )
    health = runtime.authorize_health(
        remote_host="127.0.0.1",
        metadata={},
        now_ms=1_700_000_000_000,
        now_monotonic=10.0,
    )

    assert rejected.allowed is False
    assert rejected.reason == "invalid_version"
    assert health.allowed is False
    assert health.reason == "invalid_version"


def test_valid_hmac_is_one_shot_and_binds_exact_raw_body(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    raw = b'{"type":"say","text":"hello"}'
    metadata = _metadata(runtime, raw, scope=bridge.SCOPE_SAY)

    accepted = runtime.authorize(
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        raw_body=raw,
        metadata=metadata,
        required_scopes=(bridge.SCOPE_SAY,),
        now_ms=1_700_000_000_000,
        now_monotonic=10.0,
    )
    replay = runtime.authorize(
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        raw_body=raw,
        metadata=metadata,
        required_scopes=(bridge.SCOPE_SAY,),
        now_ms=1_700_000_000_000,
        now_monotonic=10.0,
    )
    tampered_metadata = _metadata(
        runtime,
        raw,
        scope=bridge.SCOPE_SAY,
        nonce="nonce-0000000000000002",
    )
    tampered = runtime.authorize(
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        raw_body=raw + b" ",
        metadata=tampered_metadata,
        required_scopes=(bridge.SCOPE_SAY,),
        now_ms=1_700_000_000_000,
        now_monotonic=10.0,
    )

    assert accepted.allowed is True
    assert replay.reason == "replay_detected"
    assert tampered.reason == "invalid_signature"


def test_future_timestamp_cannot_outlive_nonce_claim(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, NATSUME_BRIDGE_NONCE_TTL_SECONDS="1")
    raw = b'{"type":"say","text":"future"}'
    initial_ms = 1_700_000_000_000
    metadata = _metadata(
        runtime,
        raw,
        scope=bridge.SCOPE_SAY,
        nonce="nonce-future-window-0001",
        timestamp_ms=initial_ms + 1_000,
    )

    first = runtime.authorize(
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        raw_body=raw,
        metadata=metadata,
        required_scopes=(bridge.SCOPE_SAY,),
        now_ms=initial_ms,
        now_monotonic=10.0,
    )
    replay = runtime.authorize(
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        raw_body=raw,
        metadata=metadata,
        required_scopes=(bridge.SCOPE_SAY,),
        now_ms=initial_ms + 1_001,
        now_monotonic=11.001,
    )

    assert first.allowed is True
    assert replay.reason == "replay_detected"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("version", "old", "invalid_version"),
        ("key_id", "wrong-key", "invalid_key_id"),
        ("scope", bridge.SCOPE_MOTION, "required_scope_missing"),
        ("timestamp_ms", 1_699_999_900_000, "expired_timestamp"),
    ],
)
def test_auth_metadata_failures_are_stable(
    tmp_path: Path,
    field: str,
    value: Any,
    reason: str,
) -> None:
    runtime = _runtime(tmp_path)
    raw = b'{"type":"say","text":"hello"}'
    metadata = _metadata(runtime, raw, scope=bridge.SCOPE_SAY)
    metadata[field] = value

    decision = runtime.authorize(
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        raw_body=raw,
        metadata=metadata,
        required_scopes=(bridge.SCOPE_SAY,),
        now_ms=1_700_000_000_000,
        now_monotonic=10.0,
    )

    assert decision.allowed is False
    assert decision.reason == reason


def test_rate_limit_precedes_side_effect(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        NATSUME_BRIDGE_RATE_PER_SECOND="1",
        NATSUME_BRIDGE_RATE_BURST="1",
    )
    raw = b'{"type":"say","text":"hello"}'
    first = _metadata(runtime, raw, scope=bridge.SCOPE_SAY, nonce="nonce-rate-000000000001")
    second = _metadata(runtime, raw, scope=bridge.SCOPE_SAY, nonce="nonce-rate-000000000002")

    assert runtime.authorize(
        transport="http", method="POST", path="/command", remote_host="127.0.0.1",
        raw_body=raw, metadata=first, required_scopes=(bridge.SCOPE_SAY,),
        now_ms=1_700_000_000_000, now_monotonic=10.0,
    ).allowed
    denied = runtime.authorize(
        transport="http", method="POST", path="/command", remote_host="127.0.0.1",
        raw_body=raw, metadata=second, required_scopes=(bridge.SCOPE_SAY,),
        now_ms=1_700_000_000_000, now_monotonic=10.0,
    )
    assert denied.reason == "rate_limited"


def test_rate_budget_cannot_be_multiplied_by_principal_or_scope_rotation(
    tmp_path: Path,
) -> None:
    runtime = _runtime(
        tmp_path,
        NATSUME_MATEENGINE_BRIDGE_ALLOWED_PRINCIPALS=f"{PRINCIPAL},host.rotated",
        NATSUME_BRIDGE_RATE_PER_SECOND="0.001",
        NATSUME_BRIDGE_RATE_BURST="1",
    )
    raw = b'{"type":"say","text":"hello"}'
    first = _metadata(
        runtime,
        raw,
        scope=bridge.SCOPE_SAY,
        nonce="nonce-rate-global-00000001",
    )
    rotated = _metadata(
        runtime,
        raw,
        scope=bridge.SCOPE_EXPRESSION,
        nonce="nonce-rate-global-00000002",
        principal="host.rotated",
    )

    allowed = runtime.authorize(
        transport="http", method="POST", path="/command",
        remote_host="127.0.0.1", raw_body=raw, metadata=first,
        required_scopes=(bridge.SCOPE_SAY,),
        now_ms=1_700_000_000_000, now_monotonic=10.0,
    )
    denied = runtime.authorize(
        transport="http", method="POST", path="/command",
        remote_host="127.0.0.1", raw_body=raw, metadata=rotated,
        required_scopes=(bridge.SCOPE_EXPRESSION,),
        now_ms=1_700_000_000_000, now_monotonic=10.0,
    )

    assert allowed.allowed is True
    assert denied.reason == "rate_limited"


def test_authenticated_command_audits_without_command_body_and_executes_once(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    raw = b'{"type":"say","text":"PRIVATE-TEXT","request_id":"req-1"}'
    metadata = _metadata(runtime, raw, scope=bridge.SCOPE_SAY)
    calls: list[dict[str, Any]] = []

    result = bridge.process_command_request(
        raw_body=raw,
        metadata=metadata,
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        runtime=runtime,
        command_handler=lambda payload: calls.append(payload) or {"success": True},
        now_ms=1_700_000_000_000,
        now_monotonic=10.0,
    )

    audit_text = runtime.config.audit_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert result.status == 200
    assert len(calls) == 1
    assert "command_accepted" in audit_text
    assert "command_completed" in audit_text
    assert "PRIVATE-TEXT" not in audit_text
    assert SECRET not in audit_text


def test_replay_claim_is_restored_from_durable_ledger_after_restart(
    tmp_path: Path,
) -> None:
    now_ms = int(__import__("time").time() * 1000)
    first_runtime = _runtime(tmp_path)
    raw = b'{"type":"say","text":"hello"}'
    metadata = _metadata(
        first_runtime,
        raw,
        scope=bridge.SCOPE_SAY,
        nonce="nonce-restart-0000000001",
        timestamp_ms=now_ms,
    )
    first = bridge.process_command_request(
        raw_body=raw,
        metadata=metadata,
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        runtime=first_runtime,
        command_handler=lambda _payload: {"success": True},
        now_ms=now_ms,
        now_monotonic=10.0,
    )

    # Force the accepted audit row out of the retained audit family. Replay
    # authority must come from the dedicated SQLite ledger, not audit history.
    assert first_runtime.audit_log is not None
    first_runtime.audit_log.max_bytes = 700
    first_runtime.audit_log.archive_count = 1
    for index in range(12):
        assert first_runtime.audit(
            {
                "event": "rejection_noise",
                "ts_ms": now_ms + index,
                "padding": "x" * 400,
            }
        )

    restarted_runtime = _runtime(tmp_path)
    replay = restarted_runtime.authorize(
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        raw_body=raw,
        metadata=metadata,
        required_scopes=(bridge.SCOPE_SAY,),
        now_ms=now_ms,
        now_monotonic=10.0,
    )

    assert first.status == 200
    assert replay.allowed is False
    assert replay.reason == "replay_detected"


def test_audit_failure_prevents_command_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    raw = b'{"type":"say","text":"hello"}'
    metadata = _metadata(runtime, raw, scope=bridge.SCOPE_SAY)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(runtime, "audit", lambda _row: False)

    result = bridge.process_command_request(
        raw_body=raw,
        metadata=metadata,
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        runtime=runtime,
        command_handler=lambda payload: calls.append(payload) or {"success": True},
        now_ms=1_700_000_000_000,
        now_monotonic=10.0,
    )

    assert result.status == 503
    assert result.payload["detail"] == "audit_unavailable"
    assert calls == []


def test_body_cap_rejects_before_command_handler(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, NATSUME_BRIDGE_MAX_BODY_BYTES="32")
    raw = b'{"type":"say","text":"this body is deliberately too long"}'
    calls: list[dict[str, Any]] = []

    result = bridge.process_command_request(
        raw_body=raw,
        metadata={},
        transport="http",
        method="POST",
        path="/command",
        remote_host="127.0.0.1",
        runtime=runtime,
        command_handler=lambda payload: calls.append(payload) or {"success": True},
    )

    assert result.status == 413
    assert calls == []


def test_signed_udp_envelope_is_authenticated_and_replay_safe(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, MATEENGINE_BRIDGE_UDP_ENABLED="true")
    raw = b'{"type":"say","text":"hello"}'
    envelope = bridge.build_signed_udp_envelope(
        raw_body=raw,
        runtime=runtime,
        path="/command",
        principal=PRINCIPAL,
        scope=bridge.SCOPE_SAY,
        timestamp_ms=1_700_000_000_000,
        nonce="nonce-udp-0000000000001",
    )
    datagram = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    calls: list[dict[str, Any]] = []

    first = bridge.process_udp_datagram(
        datagram,
        ("127.0.0.1", 50000),
        runtime=runtime,
        command_handler=lambda payload: calls.append(payload) or {"success": True},
        now_ms=1_700_000_000_000,
        now_monotonic=10.0,
    )
    replay = bridge.process_udp_datagram(
        datagram,
        ("127.0.0.1", 50000),
        runtime=runtime,
        command_handler=lambda payload: calls.append(payload) or {"success": True},
        now_ms=1_700_000_000_000,
        now_monotonic=10.0,
    )

    assert first.status == 200
    assert replay.authorization.reason == "replay_detected"
    assert len(calls) == 1


def test_unsigned_udp_payload_never_reaches_command_handler(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, MATEENGINE_BRIDGE_UDP_ENABLED="true")
    calls: list[dict[str, Any]] = []

    result = bridge.process_udp_datagram(
        b'{"type":"say","text":"anonymous"}',
        ("127.0.0.1", 50000),
        runtime=runtime,
        command_handler=lambda payload: calls.append(payload) or {"success": True},
    )

    assert result.status == 400
    assert calls == []


class _FakeSocket:
    def __init__(self, sent: list[tuple[bytes, tuple[str, int]]]) -> None:
        self.sent = sent

    def __enter__(self) -> "_FakeSocket":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def sendto(self, data: bytes, target: tuple[str, int]) -> int:
        self.sent.append((data, target))
        return len(data)


def test_plugin_forward_is_zero_send_without_security_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []
    bridge._install_security_runtime(None)
    monkeypatch.setattr(
        bridge.socket,
        "socket",
        lambda *_args, **_kwargs: created.append(object()),
    )

    result = bridge.send_plugin_command({"type": "accessory"}, detail="test")

    assert result["success"] is False
    assert result["forwarded"] is False
    assert created == []


def test_plugin_forward_uses_signed_envelope_without_real_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    sent: list[tuple[bytes, tuple[str, int]]] = []
    bridge._install_security_runtime(runtime)
    monkeypatch.setattr(
        bridge.socket,
        "socket",
        lambda *_args, **_kwargs: _FakeSocket(sent),
    )

    result = bridge.send_plugin_command(
        {"type": "accessory", "accessory": "hat", "enabled": True},
        detail="test",
    )

    assert result["success"] is True
    assert len(sent) == 1
    envelope = json.loads(sent[0][0].decode("utf-8"))
    raw_body = envelope["payload_json"].encode("utf-8")
    assert envelope["version"] == bridge.AUTH_VERSION
    assert envelope["key_id"] == PLUGIN_KEY_ID
    assert envelope["scope"] == bridge.SCOPE_PLUGIN
    assert envelope["body_sha256"] == hashlib.sha256(raw_body).hexdigest()
    assert envelope["signature"] == bridge.build_hmac_signature(
        runtime.config.plugin_hmac_secret,
        version=envelope["version"],
        key_id=envelope["key_id"],
        transport=envelope["transport"],
        method=envelope["method"],
        path=envelope["path"],
        timestamp_ms=envelope["timestamp_ms"],
        nonce=envelope["nonce"],
        principal=envelope["principal"],
        scope=envelope["scope"],
        body_digest=envelope["body_sha256"],
    )


def test_health_payload_never_exposes_command_or_udp_bodies(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    bridge.state["last_command"] = {"text": "private"}
    bridge.state["command_history"] = [{"text": "private"}]
    bridge.state["last_udp"] = {"payload": {"text": "private"}}

    payload = bridge.build_health_payload(runtime)
    serialized = json.dumps(payload)

    assert "last_command" not in payload
    assert "recent_commands" not in payload
    assert "last_udp" not in payload
    assert "private" not in serialized


def test_sequence_limits_bound_steps_timeline_and_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MATEENGINE_MOTION_SEQUENCE_MAX_STEPS", "1")
    monkeypatch.setenv("MATEENGINE_MOTION_SEQUENCE_MAX_HOLD_MS", "100")
    monkeypatch.setenv("MATEENGINE_ACTION_SEQUENCE_MAX_STEPS", "1")
    monkeypatch.setenv("MATEENGINE_ACTION_SEQUENCE_MAX_TIMELINE_MS", "500")
    monkeypatch.setenv("MATEENGINE_SEQUENCE_MAX_CONCURRENT", "1")

    assert bridge._validate_sequence_payload(
        {"steps": [{"hold_ms": 0}, {"hold_ms": 0}]},
        "motion_sequence",
    ) == "motion_sequence exceeds step limit"
    assert bridge._validate_sequence_payload(
        {"steps": [{"hold_ms": 101}]},
        "motion_sequence",
    ) == "motion_sequence hold_ms exceeds limit"
    assert bridge._validate_sequence_payload(
        {"steps": [{"at_ms": 501, "command": {"type": "say"}}]},
        "action_sequence",
    ) == "action_sequence timeline exceeds limit"

    bridge._SEQUENCE_ACTIVE = 0
    assert bridge._acquire_sequence_slot() is True
    assert bridge._acquire_sequence_slot() is False
    bridge._release_sequence_slot()
    assert bridge._SEQUENCE_ACTIVE == 0


def test_signal_downstream_nonloopback_is_zero_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    bridge._install_security_runtime(runtime)
    monkeypatch.setattr(bridge, "MATEENGINE_HOST", "192.0.2.50")
    created: list[object] = []
    monkeypatch.setattr(
        bridge.socket,
        "socket",
        lambda *_args, **_kwargs: created.append(object()),
    )

    result = bridge.send_udp({"type": "natsume_mouth"}, detail="test")

    assert result["success"] is False
    assert result["forwarded"] is False
    assert created == []
