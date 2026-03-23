from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_bridge_module() -> ModuleType:
    module_path = Path(__file__).resolve().parents[2] / "tools" / "wechat_uia_bridge.py"
    spec = importlib.util.spec_from_file_location("wechat_uia_bridge_test_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load wechat_uia_bridge module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = _load_bridge_module()


class _FakeNotifyBridge:
    def __init__(self, events: list[dict[str, Any]], *, forward_return: int = 0) -> None:
        self._events = list(events)
        self.forward_return = int(forward_return)
        self.forward_calls: list[tuple[list[dict[str, Any]], str]] = []

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def collect_inbound_events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def forward_events(self, events: list[dict[str, Any]], *, bridge_backend: str = "win_notify") -> int:
        self.forward_calls.append((list(events), str(bridge_backend)))
        return int(self.forward_return)


class _FakeVisionBridge:
    def __init__(self, result: int) -> None:
        self.result = int(result)
        self.calls = 0

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def process_cycle(self) -> int:
        self.calls += 1
        return int(self.result)


def test_notify_bridge_collect_and_forward(monkeypatch) -> None:
    notify_bridge = bridge.WeChatWindowsNotificationInboundBridge(
        edge_url="http://127.0.0.1:17800",
        enabled=True,
        poll_interval_s=0.5,
        callback_timeout_s=1.0,
        dedup_window_s=5.0,
        db_path=r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db",
        handler_keywords=["weixin"],
    )
    notify_bridge._baseline_ready = True
    notify_bridge._next_poll_at = 0.0

    monkeypatch.setattr(
        notify_bridge,
        "_read_new_events_since",
        lambda _last_id: (
            18,
            [{"notification_id": 18, "sender": "Alice", "content": "hello", "ts_ms": 123}],
            1,
        ),
    )

    post_calls: list[dict[str, Any]] = []

    def _fake_post(*, edge_url: str, body: dict[str, Any], timeout_s: float) -> bool:
        assert edge_url == "http://127.0.0.1:17800"
        assert timeout_s == 1.0
        post_calls.append(dict(body))
        return True

    monkeypatch.setattr(bridge, "_post_to_edge_callback", _fake_post)
    forwarded = notify_bridge.process_cycle()

    assert forwarded == 1
    assert len(post_calls) == 1
    assert post_calls[0]["bridge_backend"] == "win_notify"
    assert post_calls[0]["sender"] == "Alice"


def test_vision_notify_escalates_to_vision(monkeypatch) -> None:
    notify_bridge = _FakeNotifyBridge(
        [{"notification_id": 33, "sender": "Alice", "content": "new msg", "ts_ms": 456}],
        forward_return=1,
    )
    vision_bridge = _FakeVisionBridge(result=1)
    inbound = bridge.WeChatVisionNotifyInboundBridge(
        vision_bridge=vision_bridge,
        notify_bridge=notify_bridge,
    )

    monkeypatch.setattr(bridge, "_find_wechat_main_window", lambda: 0x1001)
    check_count = {"n": 0}

    def _fake_foreground(_hwnd: int) -> bool:
        check_count["n"] += 1
        return check_count["n"] >= 2

    monkeypatch.setattr(bridge, "_is_window_foreground_or_child", _fake_foreground)
    monkeypatch.setattr(bridge, "_focus_wechat_main_window", lambda wechat_path: (True, "focused"))

    open_calls: list[str] = []

    def _fake_open_chat(*, target: str, wechat_path: str) -> tuple[bool, str]:
        _ = wechat_path
        open_calls.append(str(target))
        return True, "opened"

    monkeypatch.setattr(bridge, "_open_chat_by_target", _fake_open_chat)
    monkeypatch.setattr(bridge.time, "sleep", lambda _s: None)

    result = inbound.process_cycle()

    assert result == 1
    assert vision_bridge.calls == 1
    assert notify_bridge.forward_calls == []
    assert open_calls == ["Alice"]


def test_vision_notify_fallback_when_focus_fails(monkeypatch) -> None:
    notify_bridge = _FakeNotifyBridge(
        [{"notification_id": 44, "sender": "Bob", "content": "fallback", "ts_ms": 789}],
        forward_return=1,
    )
    vision_bridge = _FakeVisionBridge(result=1)
    inbound = bridge.WeChatVisionNotifyInboundBridge(
        vision_bridge=vision_bridge,
        notify_bridge=notify_bridge,
    )

    monkeypatch.setattr(bridge, "_find_wechat_main_window", lambda: 0x1002)
    monkeypatch.setattr(bridge, "_is_window_foreground_or_child", lambda _hwnd: False)
    monkeypatch.setattr(bridge, "_focus_wechat_main_window", lambda wechat_path: (False, "focus_failed"))
    monkeypatch.setattr(bridge.time, "sleep", lambda _s: None)

    result = inbound.process_cycle()

    assert result == 1
    assert vision_bridge.calls == 0
    assert len(notify_bridge.forward_calls) == 1
    assert notify_bridge.forward_calls[0][1] == "win_notify_focus_failed"


def test_outbound_echo_detects_fuzzy_text_within_window(monkeypatch) -> None:
    bridge._RECENT_OUTBOUND_TEXTS.clear()
    mono = {"value": 1000.0}
    monkeypatch.setattr(bridge.time, "monotonic", lambda: float(mono["value"]))

    bridge._remember_outbound_text("System busy, please retry.")
    mono["value"] = 1084.0

    assert bridge._is_recent_outbound_echo("System busy, please try again.") is True


def test_outbound_echo_expires_after_window(monkeypatch) -> None:
    bridge._RECENT_OUTBOUND_TEXTS.clear()
    mono = {"value": 2000.0}
    monkeypatch.setattr(bridge.time, "monotonic", lambda: float(mono["value"]))

    bridge._remember_outbound_text("System busy, please retry.")
    mono["value"] = 2220.0

    assert bridge._is_recent_outbound_echo("System busy, please try again.") is False


def test_execute_command_rejects_when_wechat_login_required(monkeypatch) -> None:
    monkeypatch.setattr(
        bridge,
        "_focus_wechat_main_window",
        lambda _wechat_path: (True, "focused"),
    )
    monkeypatch.setattr(
        bridge,
        "_detect_wechat_login_gate",
        lambda **_kwargs: (True, "login ui detected: 扫码登录"),
    )

    result = bridge._execute_command(
        {
            "command_id": "cmd-login-required",
            "request_id": "req-login-required",
            "action": "send_text",
            "target": "active_chat",
            "content": "hello",
        },
        wechat_path=r"D:\Apps\WeChat\WeChat.exe",
    )

    assert result["success"] is False
    assert result["reason_code"] == "WECHAT_NOT_LOGGED_IN"
    assert "login ui detected" in str(result["detail"])


def test_execute_command_send_text_when_login_gate_clear(monkeypatch) -> None:
    monkeypatch.setattr(
        bridge,
        "_focus_wechat_main_window",
        lambda _wechat_path: (True, "focused"),
    )
    monkeypatch.setattr(
        bridge,
        "_detect_wechat_login_gate",
        lambda **_kwargs: (False, "chat ui detected"),
    )
    monkeypatch.setattr(
        bridge,
        "_open_chat_by_target",
        lambda *, target, wechat_path: (True, f"open:{target}:{wechat_path}"),
    )
    monkeypatch.setattr(
        bridge,
        "_send_text",
        lambda *, content, wechat_path: (True, f"sent:{content}:{wechat_path}"),
    )
    monkeypatch.setattr(bridge, "_remember_outbound_text", lambda _content: None)

    result = bridge._execute_command(
        {
            "command_id": "cmd-login-clear",
            "request_id": "req-login-clear",
            "action": "send_text",
            "target": "active_chat",
            "content": "hello",
        },
        wechat_path=r"D:\Apps\WeChat\WeChat.exe",
    )

    assert result["success"] is True
    assert result["reason_code"] is None
    assert result["status"] == "ok"


def test_execute_command_send_text_uses_target_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        bridge,
        "_focus_wechat_main_window",
        lambda _wechat_path: (True, "focused"),
    )
    monkeypatch.setattr(
        bridge,
        "_detect_wechat_login_gate",
        lambda **_kwargs: (False, "chat ui detected"),
    )

    open_calls: list[str] = []

    def _fake_open_chat(*, target: str, wechat_path: str) -> tuple[bool, str]:
        _ = wechat_path
        open_calls.append(str(target))
        if str(target) == "Alice":
            return True, "opened_alice"
        return False, "no_match"

    monkeypatch.setattr(bridge, "_open_chat_by_target", _fake_open_chat)
    monkeypatch.setattr(
        bridge,
        "_send_text",
        lambda *, content, wechat_path: (True, f"sent:{content}:{wechat_path}"),
    )
    monkeypatch.setattr(bridge, "_remember_outbound_text", lambda _content: None)

    result = bridge._execute_command(
        {
            "command_id": "cmd-target-candidates",
            "request_id": "req-target-candidates",
            "action": "send_text",
            "target": "wxid_alice",
            "target_candidates": ["wxid_alice", "Alice"],
            "content": "hello",
        },
        wechat_path=r"D:\Apps\WeChat\WeChat.exe",
    )

    assert result["success"] is True
    assert result["reason_code"] is None
    assert open_calls == ["wxid_alice", "Alice"]
    assert result["resolved_target"] == "Alice"


def test_collect_command_target_candidates_filters_blocked_and_non_contact(monkeypatch) -> None:
    monkeypatch.setattr(bridge, "_resolve_allowed_contact_names", lambda: ["三少爷的剑"])

    candidates = bridge._collect_command_target_candidates(
        {
            "target": "文件传输助手",
            "target_candidates": ["公众号助手", "三少爷的剑"],
        }
    )
    assert candidates == ["三少爷的剑"]

    fallback = bridge._collect_command_target_candidates(
        {
            "target": "微信团队",
            "target_candidates": ["公众号消息"],
        }
    )
    assert fallback == ["三少爷的剑"]


def test_vision_notify_skips_blocked_sender(monkeypatch) -> None:
    notify_bridge = _FakeNotifyBridge(
        [{"notification_id": 99, "sender": "文件传输助手", "content": "ignored", "ts_ms": 123}],
        forward_return=1,
    )
    vision_bridge = _FakeVisionBridge(result=1)
    inbound = bridge.WeChatVisionNotifyInboundBridge(
        vision_bridge=vision_bridge,
        notify_bridge=notify_bridge,
    )

    monkeypatch.setattr(bridge, "_find_wechat_main_window", lambda: 0x1100)
    monkeypatch.setattr(bridge, "_is_window_foreground_or_child", lambda _hwnd: False)

    result = inbound.process_cycle()

    assert result == 0
    assert vision_bridge.calls == 0
    assert notify_bridge.forward_calls == []
