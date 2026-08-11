from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


def _load_bridge_module() -> ModuleType:
    module_path = Path(__file__).resolve().parents[2] / "tools" / "wxautox4_bridge.py"
    spec = importlib.util.spec_from_file_location("wxautox4_bridge_test_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load wxautox4_bridge module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bridge = _load_bridge_module()
NOW_MS = 1_900_000_000_000
BRIDGE_SECRET = "task0494-wxautox4-legacy-test-secret"
CALLBACK_SECRET = "task0494-wxautox4-callback-test-key"


class _FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self.status = 200
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


class _FakeWx:
    def __init__(self) -> None:
        self.send_calls: list[tuple[str, str]] = []

    def SendMsg(self, content: str, *, who: str) -> None:
        self.send_calls.append((content, who))


def _read_result_rows(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _secured_outbound_processor(tmp_path: Path) -> tuple[object, object]:
    queue_file = tmp_path / "queue.jsonl"
    env = {
        bridge.BRIDGE_EXPOSURE_MODE_ENV: bridge.BRIDGE_MODE_LOOPBACK,
        bridge.LEGACY_BRIDGE_ENABLED_ENV: "true",
        bridge.BRIDGE_HMAC_SECRET_ENV: BRIDGE_SECRET,
        bridge.BRIDGE_CALLBACK_HMAC_SECRET_ENV: CALLBACK_SECRET,
        bridge.BRIDGE_HMAC_KEY_ID_ENV: "bridge.wxautox4.legacy-tests",
        bridge.BRIDGE_PRINCIPAL_ENV: "test.wxautox4.legacy-suite",
        bridge.BRIDGE_ALLOWED_SCOPES_ENV: bridge.BRIDGE_REQUIRED_SCOPE,
        bridge.BRIDGE_RATE_PER_MINUTE_ENV: "120",
        bridge.BRIDGE_RATE_BURST_ENV: "20",
        bridge.BRIDGE_COMMAND_TTL_MS_ENV: "60000",
        bridge.BRIDGE_LEDGER_PATH_ENV: str(tmp_path / "security.sqlite3"),
        bridge.BRIDGE_AUDIT_PATH_ENV: str(tmp_path / "security-audit.jsonl"),
    }
    config = bridge.load_bridge_exposure_config(
        edge_url="http://127.0.0.1:19000",
        command_queue_file=str(queue_file),
        command_result_file=str(tmp_path / "result.jsonl"),
        environ=env,
    )
    assert config.enabled is True
    authority = bridge.BridgeCommandAuthority(config, clock_ms=lambda: NOW_MS)
    processor = bridge.OutboundProcessor(
        _FakeWx(),
        command_queue_file=str(queue_file),
        command_result_file=str(tmp_path / "result.jsonl"),
        authority=authority,
    )
    return config, processor


def _signed_command(config, payload: dict[str, object], *, nonce: str) -> dict[str, object]:
    return bridge.sign_bridge_command_row(
        payload,
        secret=BRIDGE_SECRET,
        key_id=config.key_id,
        principal=config.principal_id,
        timestamp_ms=NOW_MS,
        nonce=nonce,
        source="loopback",
    )


def test_post_to_edge_callback_targets_gateway_webhook(monkeypatch, tmp_path) -> None:
    seen: dict[str, object] = {}

    def _fake_urlopen(req, timeout: float):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _FakeResponse({"status": "accepted"})

    monkeypatch.setattr(bridge.urllib.request, "urlopen", _fake_urlopen)

    config, _ = _secured_outbound_processor(tmp_path)
    ok = bridge._post_to_edge_callback(
        "http://127.0.0.1:19000",
        {"msg_id": "msg-1", "content": "hello"},
        timeout_s=1.5,
        security=config,
    )

    assert ok is True
    assert str(seen["url"]).endswith("/v1/gateway/wechat/webhook")


def test_post_to_edge_callback_rejects_rejected_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        bridge.urllib.request,
        "urlopen",
        lambda req, timeout: _FakeResponse(
            {"success": False, "status": "rejected", "event": {"deprecated": True}}
        ),
    )

    config, _ = _secured_outbound_processor(tmp_path)
    ok = bridge._post_to_edge_callback(
        "http://127.0.0.1:19000",
        {"msg_id": "msg-2", "content": "hello"},
        timeout_s=1.0,
        security=config,
    )

    assert ok is False


def test_inbound_processor_dedups_duplicate_messages(monkeypatch, tmp_path) -> None:
    config, _ = _secured_outbound_processor(tmp_path)
    processor = bridge.InboundProcessor(
        object(),
        edge_url="http://127.0.0.1:19000",
        callback_timeout_s=1.0,
        dedup_window_s=5.0,
        security=config,
    )
    message = SimpleNamespace(
        sender_name="Alice",
        sender="wxid_alice",
        content="hello from bridge",
        roomid="",
    )
    post_calls: list[dict[str, object]] = []

    def _fake_post(
        edge_url: str,
        body: dict[str, object],
        *,
        timeout_s: float,
        security,
    ) -> bool:
        assert edge_url == "http://127.0.0.1:19000"
        assert timeout_s == 1.0
        assert security is config
        post_calls.append(dict(body))
        return True

    monkeypatch.setattr(bridge, "_post_to_edge_callback", _fake_post)

    processor._forward_one_message(message, chat_name="Alice", ts_ms=1774453000000)
    processor._forward_one_message(message, chat_name="Alice", ts_ms=1774453000000)

    assert len(post_calls) == 1
    assert post_calls[0]["bridge_backend"] == "wxautox4"
    assert post_calls[0]["content"] == "hello from bridge"


def test_outbound_processor_executes_send_text(tmp_path) -> None:
    result_file = tmp_path / "result.jsonl"
    config, processor = _secured_outbound_processor(tmp_path)

    processor._execute_command(
        _signed_command(config, {
            "command_id": "cmd-1",
            "request_id": "req-1",
            "action": "send_text",
            "target": "Alice",
            "target_candidates": ["Alice"],
            "content": "hello",
        }, nonce="nonce.legacy.send-text.1")
    )

    rows = _read_result_rows(result_file)

    assert processor._wx.send_calls == [("hello", "Alice")]
    assert rows[-1]["success"] is True
    assert rows[-1]["resolved_target"] == "Alice"


def test_outbound_processor_rejects_unsupported_action(tmp_path) -> None:
    result_file = tmp_path / "result.jsonl"
    config, processor = _secured_outbound_processor(tmp_path)

    processor._execute_command(
        _signed_command(config, {
            "command_id": "cmd-2",
            "request_id": "req-2",
            "action": "unsupported_action",
            "target": "Alice",
        }, nonce="nonce.legacy.unsupported.2")
    )

    rows = _read_result_rows(result_file)

    assert processor._wx.send_calls == []
    assert rows[-1]["success"] is False
    assert rows[-1]["reason_code"] == "WECHAT_UNKNOWN_ACTION"
