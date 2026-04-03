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


class _FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self.status = 200
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


class _FakeWx:
    def __init__(self) -> None:
        self.send_calls: list[tuple[str, str]] = []

    def SendMsg(self, content: str, *, who: str) -> None:
        self.send_calls.append((content, who))


def _read_result_rows(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_post_to_edge_callback_targets_gateway_webhook(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _fake_urlopen(req, timeout: float):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _FakeResponse({"status": "accepted"})

    monkeypatch.setattr(bridge.urllib.request, "urlopen", _fake_urlopen)

    ok = bridge._post_to_edge_callback(
        "http://127.0.0.1:19000",
        {"msg_id": "msg-1", "content": "hello"},
        timeout_s=1.5,
    )

    assert ok is True
    assert str(seen["url"]).endswith("/v1/gateway/wechat/webhook")


def test_post_to_edge_callback_rejects_rejected_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        bridge.urllib.request,
        "urlopen",
        lambda req, timeout: _FakeResponse(
            {"success": False, "status": "rejected", "event": {"deprecated": True}}
        ),
    )

    ok = bridge._post_to_edge_callback(
        "http://127.0.0.1:19000",
        {"msg_id": "msg-2", "content": "hello"},
        timeout_s=1.0,
    )

    assert ok is False


def test_inbound_processor_dedups_duplicate_messages(monkeypatch) -> None:
    processor = bridge.InboundProcessor(
        object(),
        edge_url="http://127.0.0.1:19000",
        callback_timeout_s=1.0,
        dedup_window_s=5.0,
    )
    message = SimpleNamespace(
        sender_name="Alice",
        sender="wxid_alice",
        content="hello from bridge",
        roomid="",
    )
    post_calls: list[dict[str, object]] = []

    def _fake_post(edge_url: str, body: dict[str, object], *, timeout_s: float) -> bool:
        assert edge_url == "http://127.0.0.1:19000"
        assert timeout_s == 1.0
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
    processor = bridge.OutboundProcessor(
        _FakeWx(),
        command_queue_file=str(tmp_path / "queue.jsonl"),
        command_result_file=str(result_file),
    )

    processor._execute_command(
        {
            "command_id": "cmd-1",
            "request_id": "req-1",
            "action": "send_text",
            "target": "Alice",
            "target_candidates": ["Alice"],
            "content": "hello",
        }
    )

    rows = _read_result_rows(result_file)

    assert processor._wx.send_calls == [("hello", "Alice")]
    assert rows[-1]["success"] is True
    assert rows[-1]["resolved_target"] == "Alice"


def test_outbound_processor_rejects_unsupported_action(tmp_path) -> None:
    result_file = tmp_path / "result.jsonl"
    processor = bridge.OutboundProcessor(
        _FakeWx(),
        command_queue_file=str(tmp_path / "queue.jsonl"),
        command_result_file=str(result_file),
    )

    processor._execute_command(
        {
            "command_id": "cmd-2",
            "request_id": "req-2",
            "action": "unsupported_action",
            "target": "Alice",
        }
    )

    rows = _read_result_rows(result_file)

    assert processor._wx.send_calls == []
    assert rows[-1]["success"] is False
    assert rows[-1]["reason_code"] == "WECHAT_UNKNOWN_ACTION"
