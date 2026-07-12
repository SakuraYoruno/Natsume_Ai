from __future__ import annotations

import builtins
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


NOW_MS = 1_900_000_000_000
SECRET = "task0494-wxautox4-ephemeral-secret-0001"
CALLBACK_SECRET = "task0494-wxautox4-callback-secret-0002"


def _load_module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "tools" / "wxautox4_bridge.py"
    spec = importlib.util.spec_from_file_location("task0494_wxautox4_bridge", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bridge = _load_module()


class _FakeWx:
    def __init__(self) -> None:
        self.send_calls: list[tuple[str, str]] = []

    def SendMsg(self, content: str, *, who: str) -> None:
        self.send_calls.append((content, who))


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size: int = -1) -> bytes:
        return b'{"status":"accepted"}'


def _env(tmp_path: Path, *, mode: str = "LOOPBACK") -> dict[str, str]:
    return {
        bridge.BRIDGE_EXPOSURE_MODE_ENV: mode,
        bridge.LEGACY_BRIDGE_ENABLED_ENV: "true",
        bridge.BRIDGE_HMAC_SECRET_ENV: SECRET,
        bridge.BRIDGE_CALLBACK_HMAC_SECRET_ENV: CALLBACK_SECRET,
        bridge.BRIDGE_HMAC_KEY_ID_ENV: "bridge.wxautox4.test-key",
        bridge.BRIDGE_PRINCIPAL_ENV: "test.m48b.bridge.wxautox4",
        bridge.BRIDGE_ALLOWED_SCOPES_ENV: bridge.BRIDGE_REQUIRED_SCOPE,
        bridge.BRIDGE_RATE_PER_MINUTE_ENV: "10",
        bridge.BRIDGE_RATE_BURST_ENV: "2",
        bridge.BRIDGE_COMMAND_TTL_MS_ENV: "60000",
        bridge.BRIDGE_LEDGER_PATH_ENV: str(tmp_path / "security.sqlite3"),
        bridge.BRIDGE_AUDIT_PATH_ENV: str(tmp_path / "security-audit.jsonl"),
    }


def _config(tmp_path: Path, *, mode: str = "LOOPBACK"):
    env = _env(tmp_path, mode=mode)
    edge_url = "http://127.0.0.1:19000"
    if mode == "REMOTE_APPROVED":
        edge_url = "https://192.0.2.10:19000"
        env.update(
            {
                bridge.BRIDGE_ALLOWED_SOURCES_ENV: "gateway.remote.test",
                bridge.BRIDGE_ALLOWED_ACTIONS_ENV: "send_text",
                bridge.BRIDGE_REMOTE_APPROVAL_REF_ENV: "approval.remote.test",
            }
        )
    return bridge.load_bridge_exposure_config(
        edge_url=edge_url,
        command_queue_file=str(tmp_path / "commands.jsonl"),
        command_result_file=str(tmp_path / "results.jsonl"),
        environ=env,
    )


def _signed(
    config,
    *,
    command_id: str,
    nonce: str,
    content: str = "hello",
    source: str | None = None,
):
    return bridge.sign_bridge_command_row(
        {
            "command_id": command_id,
            "request_id": f"request.{command_id}",
            "action": "send_text",
            "target": "Alice",
            "content": content,
        },
        secret=SECRET,
        key_id=config.key_id,
        principal=config.principal_id,
        timestamp_ms=NOW_MS,
        nonce=nonce,
        source=(
            source
            or ("gateway.remote.test" if config.mode == "REMOTE_APPROVED" else "loopback")
        ),
        approval_ref=config.approval_ref,
    )


def _processor(tmp_path: Path, wx: _FakeWx, authority):
    return bridge.OutboundProcessor(
        wx,
        command_queue_file=str(tmp_path / "commands.jsonl"),
        command_result_file=str(tmp_path / "results.jsonl"),
        authority=authority,
    )


def test_default_invalid_and_legacy_switch_stop_before_wxautox4_import(tmp_path, monkeypatch) -> None:
    default = bridge.load_bridge_exposure_config(environ={}, command_queue_file="unused")
    invalid = bridge.load_bridge_exposure_config(
        environ={bridge.BRIDGE_EXPOSURE_MODE_ENV: "open"},
        command_queue_file="unused",
    )
    legacy_env = _env(tmp_path)
    legacy_env[bridge.LEGACY_BRIDGE_ENABLED_ENV] = "false"
    legacy = bridge.load_bridge_exposure_config(
        environ=legacy_env,
        command_queue_file=str(tmp_path / "commands.jsonl"),
    )

    assert (default.mode, default.enabled, default.reason_code) == (
        bridge.BRIDGE_MODE_DISABLED,
        False,
        bridge.BRIDGE_REASON_DISABLED,
    )
    assert invalid.reason_code == bridge.BRIDGE_REASON_MODE_INVALID
    assert legacy.reason_code == bridge.BRIDGE_REASON_LEGACY_DISABLED
    legacy_env.pop(bridge.LEGACY_BRIDGE_ENABLED_ENV)
    implicit_legacy = bridge.load_bridge_exposure_config(
        environ=legacy_env,
        command_queue_file=str(tmp_path / "commands.jsonl"),
    )
    assert implicit_legacy.reason_code == bridge.BRIDGE_REASON_LEGACY_DISABLED

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"wxautox4", "wxautox4_wechatbot"}:
            raise AssertionError("disabled mode imported a real wx provider")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    runtime = bridge.WxAutoX4Bridge(
        edge_url="http://127.0.0.1:19000",
        command_queue_file=str(tmp_path / "commands.jsonl"),
        command_result_file=str(tmp_path / "results.jsonl"),
        exposure_config=default,
    )
    assert runtime.start() == 1


def test_loopback_rejects_remote_edge_and_missing_security_material(tmp_path) -> None:
    env = _env(tmp_path)
    remote_url = bridge.load_bridge_exposure_config(
        edge_url="http://192.0.2.10:19000",
        command_queue_file=str(tmp_path / "commands.jsonl"),
        environ=env,
    )
    assert remote_url.enabled is False
    assert remote_url.reason_code == bridge.BRIDGE_REASON_EDGE_URL_NOT_LOOPBACK

    env.pop(bridge.BRIDGE_HMAC_SECRET_ENV)
    missing_secret = bridge.load_bridge_exposure_config(
        edge_url="http://127.0.0.1:19000",
        command_queue_file=str(tmp_path / "commands.jsonl"),
        environ=env,
    )
    assert missing_secret.enabled is False
    assert missing_secret.reason_code == bridge.BRIDGE_REASON_CONFIG_INVALID


def test_signed_command_executes_once_and_restart_replay_has_zero_effect(tmp_path) -> None:
    config = _config(tmp_path)
    assert config.enabled is True
    row = _signed(config, command_id="command.once.1", nonce="nonce.once.1")
    wx = _FakeWx()

    first_authority = bridge.BridgeCommandAuthority(config, clock_ms=lambda: NOW_MS)
    _processor(tmp_path, wx, first_authority)._execute_command(row)
    assert wx.send_calls == [("hello", "Alice")]

    restarted_authority = bridge.BridgeCommandAuthority(config, clock_ms=lambda: NOW_MS)
    _processor(tmp_path, wx, restarted_authority)._execute_command(row)
    assert wx.send_calls == [("hello", "Alice")]

    with bridge.sqlite3.connect(config.ledger_path) as conn:
        claim = conn.execute(
            "SELECT status, COUNT(*) FROM bridge_command_claim GROUP BY status"
        ).fetchone()
    assert claim == ("EXECUTED", 1)

    results = [
        json.loads(line)
        for line in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert results[-1]["reason_code"] == bridge.BRIDGE_REASON_REPLAYED

    audit_text = (tmp_path / "security-audit.jsonl").read_text(encoding="utf-8")
    assert SECRET not in audit_text
    assert "hello" not in audit_text
    assert "Alice" not in audit_text
    audit = [json.loads(line) for line in audit_text.splitlines()]
    assert [row["decision"] for row in audit] == ["ALLOW", "DENY"]


def test_concurrent_replay_claim_and_rate_consumption_are_one_transaction(
    tmp_path: Path,
) -> None:
    env = _env(tmp_path)
    env[bridge.BRIDGE_RATE_PER_MINUTE_ENV] = "1"
    env[bridge.BRIDGE_RATE_BURST_ENV] = "5"
    config = bridge.load_bridge_exposure_config(
        edge_url="http://127.0.0.1:19000",
        command_queue_file=str(tmp_path / "commands.jsonl"),
        command_result_file=str(tmp_path / "results.jsonl"),
        environ=env,
    )
    authority = bridge.BridgeCommandAuthority(config, clock_ms=lambda: NOW_MS)
    row = _signed(
        config,
        command_id="concurrent.command.1",
        nonce="concurrent.nonce.00000001",
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        decisions = list(pool.map(lambda _index: authority.authorize(row), range(2)))

    assert sorted(decision.reason_code for decision in decisions) == sorted(
        [bridge.BRIDGE_REASON_ALLOWED, bridge.BRIDGE_REASON_REPLAYED]
    )
    with bridge.sqlite3.connect(config.ledger_path) as conn:
        tokens = conn.execute("SELECT tokens FROM bridge_rate_bucket").fetchone()
    assert tokens == pytest.approx((4.0,))


def test_command_signature_uses_frozen_ten_field_canonical_vector(tmp_path) -> None:
    config = _config(tmp_path)
    row = _signed(config, command_id="command.vector.1", nonce="nonce.vector.1")
    canonical = "\n".join(
        (
            "natsume.bridge-auth.v1",
            config.key_id,
            "jsonl",
            "COMMAND",
            "wxautox4",
            str(NOW_MS),
            "nonce.vector.1",
            config.principal_id,
            bridge.BRIDGE_REQUIRED_SCOPE,
            row["body_sha256"],
        )
    ).encode("utf-8")
    assert not canonical.endswith(b"\n")
    assert row["hmac_sha256"] == hmac.new(
        SECRET.encode("utf-8"), canonical, hashlib.sha256
    ).hexdigest()


def test_tamper_scope_expiry_and_no_authority_precede_wx_api(tmp_path) -> None:
    config = _config(tmp_path)
    authority = bridge.BridgeCommandAuthority(config, clock_ms=lambda: NOW_MS)
    wx = _FakeWx()
    processor = _processor(tmp_path, wx, authority)

    tampered = _signed(config, command_id="command.tamper", nonce="nonce.tamper")
    tampered["content"] = "forged"
    processor._execute_command(tampered)

    wrong_scope = _signed(config, command_id="command.scope", nonce="nonce.scope")
    wrong_scope["scope"] = "bridge:admin"
    processor._execute_command(wrong_scope)

    expired = bridge.sign_bridge_command_row(
        {
            "command_id": "command.expired",
            "request_id": "request.expired",
            "action": "send_text",
            "target": "Alice",
            "content": "old",
        },
        secret=SECRET,
        key_id=config.key_id,
        principal=config.principal_id,
        timestamp_ms=NOW_MS - config.command_ttl_ms - 1,
        nonce="nonce.expired",
    )
    processor._execute_command(expired)

    raw_processor = _processor(tmp_path, wx, None)
    raw_processor._execute_command(
        {"command_id": "raw", "action": "send_text", "target": "Alice", "content": "raw"}
    )
    assert wx.send_calls == []

    reasons = [
        json.loads(line)["reason_code"]
        for line in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert reasons == [
        bridge.BRIDGE_REASON_BODY_DIGEST_INVALID,
        bridge.BRIDGE_REASON_SCOPE_DENIED,
        bridge.BRIDGE_REASON_EXPIRED,
        bridge.BRIDGE_REASON_DISABLED,
    ]


def test_remote_requires_explicit_approval_scope_source_rate_and_durable_paths(tmp_path) -> None:
    env = _env(tmp_path, mode="REMOTE_APPROVED")
    incomplete = bridge.load_bridge_exposure_config(
        edge_url="https://192.0.2.10:19000",
        command_queue_file=str(tmp_path / "commands.jsonl"),
        environ=env,
    )
    assert incomplete.enabled is False
    assert incomplete.reason_code == bridge.BRIDGE_REASON_REMOTE_NOT_APPROVED

    config = _config(tmp_path, mode="REMOTE_APPROVED")
    assert config.enabled is True
    authority = bridge.BridgeCommandAuthority(config, clock_ms=lambda: NOW_MS)
    wx = _FakeWx()
    processor = _processor(tmp_path, wx, authority)

    wrong_source = bridge.sign_bridge_command_row(
        {
            "command_id": "remote.source",
            "request_id": "request.remote.source",
            "action": "send_text",
            "target": "Alice",
            "content": "hello",
        },
        secret=SECRET,
        key_id=config.key_id,
        principal=config.principal_id,
        timestamp_ms=NOW_MS,
        nonce="remote.source.nonce",
        source="unapproved.source",
        approval_ref=config.approval_ref,
    )
    processor._execute_command(wrong_source)
    wrong_approval = bridge.sign_bridge_command_row(
        {
            "command_id": "remote.approval",
            "request_id": "request.remote.approval",
            "action": "send_text",
            "target": "Alice",
            "content": "hello",
        },
        secret=SECRET,
        key_id=config.key_id,
        principal=config.principal_id,
        timestamp_ms=NOW_MS,
        nonce="remote.approval.nonce",
        source="gateway.remote.test",
        approval_ref="approval.forged",
    )
    processor._execute_command(wrong_approval)
    valid = _signed(
        config,
        command_id="remote.valid",
        nonce="remote.valid.nonce",
    )
    processor._execute_command(valid)
    assert wx.send_calls == [("hello", "Alice")]
    reasons = [
        json.loads(line)["reason_code"]
        for line in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert reasons[:2] == [
        bridge.BRIDGE_REASON_SOURCE_DENIED,
        bridge.BRIDGE_REASON_APPROVAL_DENIED,
    ]

    denied_action = bridge.sign_bridge_command_row(
        {
            "command_id": "remote.action",
            "request_id": "request.remote.action",
            "action": "voice_call",
            "target": "Alice",
        },
        secret=SECRET,
        key_id=config.key_id,
        principal=config.principal_id,
        timestamp_ms=NOW_MS,
        nonce="remote.action.nonce",
        source="gateway.remote.test",
        approval_ref=config.approval_ref,
    )
    processor._execute_command(denied_action)
    assert wx.send_calls == [("hello", "Alice")]
    assert json.loads(
        (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )["reason_code"] == bridge.BRIDGE_REASON_SCOPE_DENIED


def test_positive_rate_and_burst_are_durable_and_fail_closed(tmp_path) -> None:
    env = _env(tmp_path)
    env[bridge.BRIDGE_RATE_PER_MINUTE_ENV] = "1"
    env[bridge.BRIDGE_RATE_BURST_ENV] = "1"
    config = bridge.load_bridge_exposure_config(
        edge_url="http://127.0.0.1:19000",
        command_queue_file=str(tmp_path / "commands.jsonl"),
        command_result_file=str(tmp_path / "results.jsonl"),
        environ=env,
    )
    wx = _FakeWx()
    processor = _processor(
        tmp_path,
        wx,
        bridge.BridgeCommandAuthority(config, clock_ms=lambda: NOW_MS),
    )
    for index in range(3):
        processor._execute_command(
            _signed(
                config,
                command_id=f"rate.command.{index}",
                nonce=f"rate.nonce.{index}",
                content=f"message-{index}",
            )
        )
    assert wx.send_calls == [("message-0", "Alice")]
    result = json.loads(
        (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert result["reason_code"] == bridge.BRIDGE_REASON_RATE_LIMITED


def test_remote_source_rotation_cannot_multiply_credential_rate(tmp_path: Path) -> None:
    env = _env(tmp_path, mode="REMOTE_APPROVED")
    env.update(
        {
            bridge.BRIDGE_ALLOWED_SOURCES_ENV: "gateway.remote.a,gateway.remote.b",
            bridge.BRIDGE_ALLOWED_ACTIONS_ENV: "send_text",
            bridge.BRIDGE_REMOTE_APPROVAL_REF_ENV: "approval.remote.test",
            bridge.BRIDGE_RATE_PER_MINUTE_ENV: "1",
            bridge.BRIDGE_RATE_BURST_ENV: "1",
        }
    )
    config = bridge.load_bridge_exposure_config(
        edge_url="https://192.0.2.10:19000",
        command_queue_file=str(tmp_path / "commands.jsonl"),
        command_result_file=str(tmp_path / "results.jsonl"),
        environ=env,
    )
    authority = bridge.BridgeCommandAuthority(config, clock_ms=lambda: NOW_MS)

    first = authority.authorize(
        _signed(
            config,
            command_id="source.command.1",
            nonce="source.nonce.0000000001",
            source="gateway.remote.a",
        )
    )
    rotated = authority.authorize(
        _signed(
            config,
            command_id="source.command.2",
            nonce="source.nonce.0000000002",
            source="gateway.remote.b",
        )
    )

    assert first.allowed is True
    assert rotated.reason_code == bridge.BRIDGE_REASON_RATE_LIMITED


def test_remote_callback_uses_existing_internal_raw_body_hmac_protocol(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path, mode="REMOTE_APPROVED")
    seen = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(bridge.urllib.request, "urlopen", fake_urlopen)
    body = {"msg_id": "message.1", "content": "hello"}
    assert bridge._post_to_edge_callback(
        config.edge_url,
        body,
        timeout_s=1.0,
        security=config,
        now_ms=NOW_MS,
        nonce="callback.nonce.1",
    ) is True

    request = seen["request"]
    headers = {key.lower(): value for key, value in request.header_items()}
    timestamp = str(NOW_MS // 1000)
    canonical = (
        "natsume-webhook-v1\nwechat\nPOST\n/v1/gateway/wechat/webhook\n"
        f"{timestamp}\ncallback.nonce.1\n"
    ).encode() + request.data
    expected = hmac.new(CALLBACK_SECRET.encode(), canonical, hashlib.sha256).hexdigest()
    assert headers["x-natsume-webhook-timestamp"] == timestamp
    assert headers["x-natsume-webhook-nonce"] == "callback.nonce.1"
    assert headers["x-natsume-webhook-signature"] == expected


def test_callback_and_inbound_processor_bind_exact_configured_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(
        bridge.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: calls.append(object()),
    )

    assert bridge._post_to_edge_callback(
        "http://192.0.2.10:19000",
        {"msg_id": "url-mismatch"},
        timeout_s=1.0,
        security=config,
    ) is False
    with pytest.raises(ValueError, match=bridge.BRIDGE_REASON_CONFIG_INVALID):
        bridge.InboundProcessor(
            _FakeWx(),
            edge_url="http://192.0.2.10:19000",
            security=config,
        )
    assert calls == []


def test_unsigned_callback_and_unwritable_audit_fail_before_provider(tmp_path, monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        bridge.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: calls.append(object()),
    )
    assert bridge._post_to_edge_callback(
        "http://127.0.0.1:19000",
        {"msg_id": "unsigned"},
        timeout_s=1.0,
        security=None,
    ) is False
    assert calls == []

    env = _env(tmp_path)
    audit_directory = tmp_path / "audit-is-a-directory"
    audit_directory.mkdir()
    env[bridge.BRIDGE_AUDIT_PATH_ENV] = str(audit_directory)
    config = bridge.load_bridge_exposure_config(
        edge_url="http://127.0.0.1:19000",
        command_queue_file=str(tmp_path / "commands.jsonl"),
        command_result_file=str(tmp_path / "results.jsonl"),
        environ=env,
    )
    assert config.enabled is True
    with pytest.raises((OSError, IsADirectoryError)):
        bridge.BridgeCommandAuthority(config)


def test_active_config_and_processor_bind_exact_queue_and_result_paths(
    tmp_path: Path,
) -> None:
    missing_result = bridge.load_bridge_exposure_config(
        edge_url="http://127.0.0.1:19000",
        command_queue_file=str(tmp_path / "commands.jsonl"),
        environ=_env(tmp_path),
    )
    assert missing_result.enabled is False
    assert missing_result.reason_code == bridge.BRIDGE_REASON_CONFIG_INVALID

    config = _config(tmp_path)
    authority = bridge.BridgeCommandAuthority(config, clock_ms=lambda: NOW_MS)
    wx = _FakeWx()
    with pytest.raises(ValueError, match=bridge.BRIDGE_REASON_CONFIG_INVALID):
        bridge.OutboundProcessor(
            wx,
            command_queue_file=config.queue_path,
            command_result_file=f"{config.audit_path}.1",
            authority=authority,
        )
    assert wx.send_calls == []


def test_torn_audit_and_runtime_short_write_poison_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    torn_path = tmp_path / "torn-audit.jsonl"
    torn_path.write_text(
        '{"schema_version":"natsume.bridge-security-audit.v1",'
        '"ts_ms":1,"decision":"ALLOW","reason_code":"previous"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="torn terminal row"):
        bridge._BridgeJsonlAudit(str(torn_path))

    audit_path = tmp_path / "short-write-audit.jsonl"
    audit = bridge._BridgeJsonlAudit(str(audit_path))
    real_open = builtins.open

    class _ShortWriter:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

        def write(self, data: bytes) -> int:
            return max(0, len(data) - 1)

        def flush(self) -> None:
            return None

    def short_open(file, mode="r", *args, **kwargs):
        if Path(file).resolve() == audit_path.resolve() and mode == "ab":
            return _ShortWriter()
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", short_open)
    row = {"ts_ms": NOW_MS, "decision": "ALLOW", "reason_code": "test"}
    with pytest.raises(OSError, match="short write"):
        audit.append(row)
    with pytest.raises(OSError, match="poisoned"):
        audit.append(row)


def test_oversized_jsonl_row_is_drained_with_one_rejection(tmp_path) -> None:
    queue = tmp_path / "oversized.jsonl"
    queue.write_bytes(b'{"value":"' + b"x" * (bridge.BRIDGE_MAX_COMMAND_ROW_BYTES * 3) + b'"}\n')
    rows, offset, discarding = bridge._read_jsonl_rows_from_offset(str(queue), 0)
    assert rows == [{"__bridge_rejection_reason": bridge.BRIDGE_REASON_BODY_TOO_LARGE}]
    assert offset == queue.stat().st_size
    assert discarding is False


def test_jsonl_reader_bounds_rows_and_total_batch_memory(tmp_path: Path) -> None:
    queue = tmp_path / "many-rows.jsonl"
    queue.write_bytes(b'{}\n' * (bridge.BRIDGE_MAX_COMMAND_ROWS_PER_CYCLE + 44))

    first, first_offset, first_discarding = bridge._read_jsonl_rows_from_offset(
        str(queue), 0
    )
    second, second_offset, second_discarding = bridge._read_jsonl_rows_from_offset(
        str(queue), first_offset
    )

    assert len(first) == bridge.BRIDGE_MAX_COMMAND_ROWS_PER_CYCLE
    assert first_offset < queue.stat().st_size
    assert first_discarding is False
    assert len(second) == 44
    assert second_offset == queue.stat().st_size
    assert second_discarding is False


def test_oversized_row_drain_obeys_per_cycle_byte_budget(tmp_path: Path) -> None:
    queue = tmp_path / "unbounded-row.jsonl"
    queue.write_bytes(b"x" * (bridge.BRIDGE_MAX_COMMAND_BATCH_BYTES * 3))

    rows, offset, discarding = bridge._read_jsonl_rows_from_offset(str(queue), 0)

    assert rows == [
        {"__bridge_rejection_reason": bridge.BRIDGE_REASON_BODY_TOO_LARGE}
    ]
    assert 0 < offset <= bridge.BRIDGE_MAX_COMMAND_BATCH_BYTES
    assert discarding is True

    previous_offset = offset
    rows, offset, discarding = bridge._read_jsonl_rows_from_offset(
        str(queue), offset, discarding
    )
    assert rows == []
    assert 0 < offset - previous_offset <= bridge.BRIDGE_MAX_COMMAND_BATCH_BYTES
    assert discarding is True

    with queue.open("ab") as handle:
        handle.write(b"\n")
    while discarding:
        previous_offset = offset
        rows, offset, discarding = bridge._read_jsonl_rows_from_offset(
            str(queue), offset, discarding
        )
        assert rows == []
        assert 0 < offset - previous_offset <= bridge.BRIDGE_MAX_COMMAND_BATCH_BYTES
    assert offset == queue.stat().st_size


def test_storage_path_families_reject_rotation_and_sqlite_collisions(tmp_path) -> None:
    env = _env(tmp_path)
    audit_path = tmp_path / "audit.jsonl"
    env[bridge.BRIDGE_AUDIT_PATH_ENV] = str(audit_path)
    env[bridge.BRIDGE_LEDGER_PATH_ENV] = f"{audit_path}.8"
    collision = bridge.load_bridge_exposure_config(
        edge_url="http://127.0.0.1:19000",
        command_queue_file=str(tmp_path / "commands.jsonl"),
        command_result_file=str(tmp_path / "results.jsonl"),
        environ=env,
    )
    assert collision.enabled is False
    assert collision.reason_code == bridge.BRIDGE_REASON_CONFIG_INVALID

    env[bridge.BRIDGE_LEDGER_PATH_ENV] = str(tmp_path / "security.sqlite3")
    env[bridge.BRIDGE_AUDIT_PATH_ENV] = f"{env[bridge.BRIDGE_LEDGER_PATH_ENV]}-wal"
    sqlite_collision = bridge.load_bridge_exposure_config(
        edge_url="http://127.0.0.1:19000",
        command_queue_file=str(tmp_path / "commands.jsonl"),
        command_result_file=str(tmp_path / "results.jsonl"),
        environ=env,
    )
    assert sqlite_collision.enabled is False


def test_row_and_body_caps_fail_before_device_api(tmp_path) -> None:
    config = _config(tmp_path)
    wx = _FakeWx()
    processor = _processor(
        tmp_path,
        wx,
        bridge.BridgeCommandAuthority(config, clock_ms=lambda: NOW_MS),
    )
    too_large = {
        "__bridge_rejection_reason": bridge.BRIDGE_REASON_BODY_TOO_LARGE,
    }
    processor._execute_command(too_large)
    assert wx.send_calls == []
    result = json.loads((tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert result["reason_code"] == bridge.BRIDGE_REASON_BODY_TOO_LARGE
