from __future__ import annotations

from pathlib import Path

from edge_client.gateway.adapters.wechat_adapter import WeChatAdapter
from edge_client.gateway.models import MsgType, Platform, UnifiedReply


class _WxSvcStub:
    def stats(self) -> dict[str, object]:
        return {
            "contact_db_exists": True,
            "message_db_count": 1,
        }


def test_wechat_adapter_uses_bridge_queue_for_text(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "uia")
    monkeypatch.setenv("EDGE_WECHAT_COMMAND_TIMEOUT_MS", "4321")

    adapter = WeChatAdapter(None, self_wxid=None)
    monkeypatch.setattr(adapter, "_build_search_candidates", lambda _conversation_id: ["Alice", "wxid_alice"])

    called: dict[str, object] = {}

    def _fake_enqueue_wechat_command(**kwargs):
        called["enqueue"] = dict(kwargs)
        return {"command_id": "wechatcmd-bridge-001"}

    def _fake_wait_wechat_command_result(*, command_id: str, timeout_ms: int, result_file: Path | None = None):
        called["wait"] = {
            "command_id": command_id,
            "timeout_ms": timeout_ms,
            "result_file": result_file,
        }
        return {"success": True, "status": "ok", "detail": "bridge ok"}

    monkeypatch.setattr(
        "edge_client.channels.wechat_command_queue.resolve_command_file_paths",
        lambda: (Path("queue.jsonl"), Path("result.jsonl")),
    )
    monkeypatch.setattr(
        "edge_client.channels.wechat_command_queue.enqueue_wechat_command",
        _fake_enqueue_wechat_command,
    )
    monkeypatch.setattr(
        "edge_client.channels.wechat_command_queue.wait_wechat_command_result",
        _fake_wait_wechat_command_result,
    )
    monkeypatch.setattr(
        adapter,
        "_send_via_pywinauto",
        lambda _reply: (_ for _ in ()).throw(AssertionError("pywinauto should not be used in bridge mode")),
    )

    reply = UnifiedReply(
        conversation_id="wxid_alice",
        platform=Platform.WECHAT,
        msg_type=MsgType.TEXT,
        content="hello from bridge",
    )

    assert adapter.send_reply(reply) is True
    assert called["enqueue"] == {
        "action": "send_text",
        "target": "wxid_alice",
        "target_candidates": ["Alice", "wxid_alice"],
        "content": "hello from bridge",
        "request_id": called["enqueue"]["request_id"],
        "queue_file": Path("queue.jsonl"),
        "ts_ms": called["enqueue"]["ts_ms"],
    }
    assert str(called["wait"]["command_id"]) == "wechatcmd-bridge-001"
    assert int(called["wait"]["timeout_ms"]) == 4321


def test_wechat_adapter_health_reports_bridge_mode_without_db(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "uia")
    monkeypatch.setenv("EDGE_WECHAT_INBOUND_BACKEND", "uia_hook")

    adapter = WeChatAdapter(None, self_wxid=None)

    snapshot = adapter.health()

    assert snapshot["status"] == "ok"
    assert snapshot["bridge_mode"] is True
    assert snapshot["bridge_queue_enabled"] is True
    assert snapshot["bridge_backend"] == "uia"
    assert snapshot["bridge_inbound_backend"] == "uia_hook"
    assert snapshot["outbound_mode"] == "bridge_queue"
    assert snapshot["inbound_mode"] == "webhook_only"


def test_wechat_adapter_parse_uia_hook_payload_defaults_conversation_to_sender() -> None:
    adapter = WeChatAdapter(None, self_wxid=None)

    message = adapter.parse_inbound(
        {
            "msg_id": "uia-msg-001",
            "sender": "wxid_alice",
            "from_wxid": "wxid_alice",
            "receiver": "local_wechat",
            "to_wxid": "local_wechat",
            "content": "hello from uia",
            "text": "hello from uia",
            "msg_type": "1",
            "ts": 1774453000,
            "bridge_backend": "uia_hook",
        }
    )

    assert message is not None
    assert message.platform == Platform.WECHAT
    assert message.msg_id == "uia-msg-001"
    assert message.from_id == "wxid_alice"
    assert message.to_id == "local_wechat"
    assert message.conversation_id == "wxid_alice"
    assert message.msg_type == MsgType.TEXT
    assert message.content == "hello from uia"
    assert message.sender_name == "wxid_alice"
    assert message.raw_extra["bridge_backend"] == "uia_hook"
    assert message.ts_ms == 1774453000000


def test_wechat_adapter_parse_uia_hook_payload_prefers_roomid_for_group() -> None:
    adapter = WeChatAdapter(None, self_wxid=None)

    message = adapter.parse_inbound(
        {
            "msg_id": "uia-msg-002",
            "sender": "wxid_member_01",
            "from_wxid": "wxid_member_01",
            "roomid": "12345@chatroom",
            "receiver": "local_wechat",
            "content": "群里来消息了",
            "msg_type": "1",
            "ts_ms": 1774453000123,
            "bridge_backend": "uia_hook",
        }
    )

    assert message is not None
    assert message.conversation_id == "12345@chatroom"
    assert message.from_id == "wxid_member_01"
    assert message.is_group is True
    assert message.raw_extra["roomid"] == "12345@chatroom"


def test_wechat_adapter_health_reports_webhook_only_when_bridge_keeps_db_service(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "uia")
    monkeypatch.setenv("EDGE_WECHAT_INBOUND_BACKEND", "uia_hook")

    adapter = WeChatAdapter(
        _WxSvcStub(),
        self_wxid=None,
        bridge_mode=True,
        db_watch_enabled=False,
        bridge_inbound_backend="uia_hook",
    )

    snapshot = adapter.health()

    assert snapshot["status"] == "ok"
    assert snapshot["bridge_mode"] is True
    assert snapshot["db_stats"]["contact_db_exists"] is True
    assert snapshot["db_stats"]["db_watch_enabled"] is False
    assert snapshot["inbound_mode"] == "webhook_only"
