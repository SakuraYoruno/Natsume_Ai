from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


def _load_bridge_module() -> ModuleType:
    module_path = Path(__file__).resolve().parents[2] / "tools" / "wechat_uia_bridge.py"
    spec = importlib.util.spec_from_file_location("wechat_uia_bridge_test_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load wechat_uia_bridge module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bridge = _load_bridge_module()


class _FakeController:
    def __init__(self, *, inbound_batches: list[tuple[str, list[Any]]] | None = None, send_result: tuple[bool, str | None, str, str | None] | None = None) -> None:
        self._inbound_batches = list(inbound_batches or [])
        self._send_result = send_result or (True, None, "ok", "Alice")
        self.send_calls: list[dict[str, Any]] = []

    def collect_inbound_messages(self) -> tuple[str, list[Any]]:
        if self._inbound_batches:
            return self._inbound_batches.pop(0)
        return "", []

    def send_text(self, *, target: str, content: str, target_candidates: list[str] | None = None) -> tuple[bool, str | None, str, str | None]:
        self.send_calls.append({
            "target": target,
            "content": content,
            "target_candidates": list(target_candidates or []),
        })
        return self._send_result


def test_uia_inbound_primes_baseline_then_forwards_new_message(monkeypatch) -> None:
    msg1 = bridge.UiaMessageCandidate(sender="Alice", content="hello", signature="sig-1", top=1)
    msg2 = bridge.UiaMessageCandidate(sender="Alice", content="world", signature="sig-2", top=2)
    controller = _FakeController(inbound_batches=[("Alice", [msg1]), ("Alice", [msg1, msg2])])
    inbound = bridge.WeChatPureUiaInboundBridge(
        edge_url="http://127.0.0.1:19000",
        enabled=True,
        poll_interval_s=0.0,
        callback_timeout_s=1.0,
        dedup_window_s=5.0,
        client=controller,
    )

    post_calls: list[dict[str, Any]] = []

    def _fake_post(edge_url: str, body: dict[str, Any], *, timeout_s: float) -> bool:
        assert edge_url == "http://127.0.0.1:19000"
        assert timeout_s == 1.0
        post_calls.append(dict(body))
        return True

    monkeypatch.setattr(bridge, "_post_to_edge_callback", _fake_post)

    assert inbound.process_cycle() == 0
    assert post_calls == []

    inbound._next_poll_at = 0.0
    assert inbound.process_cycle() == 1
    assert len(post_calls) == 1
    assert post_calls[0]["sender"] == "Alice"
    assert post_calls[0]["content"] == "world"
    assert post_calls[0]["bridge_backend"] == "uia_hook"


def test_execute_command_delegates_to_uia_controller() -> None:
    controller = _FakeController(send_result=(True, None, "uia_send_ok target=Alice", "Alice"))

    result = bridge._execute_command(
        {
            "command_id": "cmd-1",
            "request_id": "req-1",
            "action": "send_text",
            "target": "Alice",
            "target_candidates": ["Alice"],
            "content": "hello",
        },
        controller=controller,
    )

    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["resolved_target"] == "Alice"
    assert controller.send_calls[0]["target_candidates"] == ["Alice"]


def test_execute_command_rejects_unsupported_action() -> None:
    controller = _FakeController()

    result = bridge._execute_command(
        {
            "command_id": "cmd-2",
            "request_id": "req-2",
            "action": "video_call",
            "target": "Alice",
        },
        controller=controller,
    )

    assert result["success"] is False
    assert result["reason_code"] == "WECHAT_ACTION_UNSUPPORTED"
    assert controller.send_calls == []
