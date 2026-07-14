from __future__ import annotations

import json
import logging
import re
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
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "wxautox4")
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
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "wxautox4")
    monkeypatch.setenv("EDGE_WECHAT_INBOUND_BACKEND", "wxautox4_hook")

    adapter = WeChatAdapter(None, self_wxid=None)

    snapshot = adapter.health()

    assert snapshot["status"] == "ok"
    assert snapshot["bridge_mode"] is True
    assert snapshot["bridge_queue_enabled"] is True
    assert snapshot["bridge_backend"] == "wxautox4"
    assert snapshot["bridge_inbound_backend"] == "wxautox4_hook"
    assert snapshot["outbound_mode"] == "bridge_queue"
    assert snapshot["inbound_mode"] == "webhook_only"


def test_wechat_adapter_parse_wxautox4_hook_payload_defaults_conversation_to_sender() -> None:
    adapter = WeChatAdapter(None, self_wxid=None)

    message = adapter.parse_inbound(
        {
            "msg_id": "wx-msg-001",
            "sender": "wxid_alice",
            "from_wxid": "wxid_alice",
            "receiver": "local_wechat",
            "to_wxid": "local_wechat",
            "content": "hello from bridge",
            "text": "hello from bridge",
            "msg_type": "1",
            "ts": 1774453000,
            "bridge_backend": "wxautox4_hook",
        }
    )

    assert message is not None
    assert message.platform == Platform.WECHAT
    assert message.msg_id == "wx-msg-001"
    assert message.from_id == "wxid_alice"
    assert message.to_id == "local_wechat"
    assert message.conversation_id == "wxid_alice"
    assert message.msg_type == MsgType.TEXT
    assert message.content == "hello from bridge"
    assert message.sender_name == "wxid_alice"
    assert message.raw_extra["bridge_backend"] == "wxautox4_hook"
    assert message.ts_ms == 1774453000000


def test_wechat_adapter_parse_wxautox4_hook_payload_prefers_roomid_for_group() -> None:
    adapter = WeChatAdapter(None, self_wxid=None)

    message = adapter.parse_inbound(
        {
            "msg_id": "wx-msg-002",
            "sender": "wxid_member_01",
            "from_wxid": "wxid_member_01",
            "roomid": "12345@chatroom",
            "receiver": "local_wechat",
            "content": "群里来消息了",
            "msg_type": "1",
            "ts_ms": 1774453000123,
            "bridge_backend": "wxautox4_hook",
        }
    )

    assert message is not None
    assert message.conversation_id == "12345@chatroom"
    assert message.from_id == "wxid_member_01"
    assert message.is_group is True
    assert message.raw_extra["roomid"] == "12345@chatroom"


def test_wechat_adapter_missing_platform_identity_is_deterministic_across_key_order() -> None:
    adapter = WeChatAdapter(None, self_wxid=None)
    first_payload = {
        "sender": "wxid_alice",
        "receiver": "local_wechat",
        "content": "same authenticated message",
        "msg_type": "1",
    }
    reordered_payload = {
        "msg_type": "1",
        "content": "same authenticated message",
        "receiver": "local_wechat",
        "sender": "wxid_alice",
    }

    first = adapter.parse_inbound(first_payload)
    retried = adapter.parse_inbound(reordered_payload)

    assert first is not None
    assert retried is not None
    assert first.msg_id == retried.msg_id
    assert re.fullmatch(r"wechat-sha256-[0-9a-f]{64}", first.msg_id)
    assert first.ts_ms == retried.ts_ms == 0
    assert first.as_report_payload() == retried.as_report_payload()


def test_wechat_adapter_digest_identity_binds_semantic_message_content() -> None:
    adapter = WeChatAdapter(None, self_wxid=None)
    base = {
        "sender": "wxid_alice",
        "receiver": "local_wechat",
        "msg_type": "1",
    }

    first = adapter.parse_inbound({**base, "content": "message one"})
    different = adapter.parse_inbound({**base, "content": "message two"})

    assert first is not None
    assert different is not None
    assert first.msg_id != different.msg_id
    assert first.ts_ms == different.ts_ms == 0


def test_wechat_adapter_prefers_platform_id_and_platform_time_aliases() -> None:
    adapter = WeChatAdapter(None, self_wxid=None)

    message = adapter.parse_inbound(
        {
            "MsgId": 987654321,
            "CreateTime": "1774453000",
            "sender": "wxid_alice",
            "receiver": "local_wechat",
            "content": "platform identity",
            "msg_type": "1",
        }
    )

    assert message is not None
    assert message.msg_id == "987654321"
    assert message.ts_ms == 1774453000000


def test_wechat_adapter_excludes_auth_tokens_and_raw_bodies_from_identity_and_result() -> None:
    adapter = WeChatAdapter(None, self_wxid=None)
    semantic_payload = {
        "sender": "wxid_alice",
        "receiver": "local_wechat",
        "content": "redacted transport fields",
        "msg_type": "1",
    }
    first = adapter.parse_inbound(
        {
            **semantic_payload,
            "token": "token-secret-one",
            "raw_body": b"raw-body-one",
            "metadata": {
                "access_token": "nested-token-one",
                "custom_auth_token": "nested-custom-token-one",
                "request_body": "nested-raw-body-one",
                "keep": "safe-metadata",
            },
        }
    )
    retried = adapter.parse_inbound(
        {
            "metadata": {
                "keep": "safe-metadata",
                "request_body": "nested-raw-body-two",
                "access_token": "nested-token-two",
                "custom_auth_token": "nested-custom-token-two",
            },
            **semantic_payload,
            "raw_body": b"raw-body-two",
            "token": "token-secret-two",
        }
    )

    assert first is not None
    assert retried is not None
    assert first.msg_id == retried.msg_id
    assert first.raw_extra["metadata"] == {"keep": "safe-metadata"}
    encoded_result = json.dumps(first.as_report_payload(), ensure_ascii=False)
    for forbidden in (
        "token-secret-one",
        "nested-token-one",
        "nested-custom-token-one",
        "raw-body-one",
        "nested-raw-body-one",
    ):
        assert forbidden not in encoded_result


def test_wechat_adapter_parse_failure_log_never_echoes_payload_value(monkeypatch, caplog) -> None:
    adapter = WeChatAdapter(None, self_wxid=None)
    secret = "token-and-raw-body-must-not-be-logged"

    def _raise_with_payload_value(_payload):
        raise ValueError(secret)

    monkeypatch.setattr(adapter, "_resolve_inbound_conversation_id", _raise_with_payload_value)
    caplog.set_level(logging.WARNING)

    assert adapter.parse_inbound({"token": secret, "raw_body": secret}) is None
    assert "error_type=ValueError" in caplog.text
    assert secret not in caplog.text


def test_wechat_adapter_health_reports_webhook_only_when_bridge_keeps_db_service(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "wxautox4")
    monkeypatch.setenv("EDGE_WECHAT_INBOUND_BACKEND", "wxautox4_hook")

    adapter = WeChatAdapter(
        _WxSvcStub(),
        self_wxid=None,
        bridge_mode=True,
        db_watch_enabled=False,
        bridge_inbound_backend="wxautox4_hook",
    )

    snapshot = adapter.health()

    assert snapshot["status"] == "ok"
    assert snapshot["bridge_mode"] is True
    assert snapshot["db_stats"]["contact_db_exists"] is True
    assert snapshot["db_stats"]["db_watch_enabled"] is False
    assert snapshot["inbound_mode"] == "webhook_only"


def test_wechat_adapter_legacy_uia_backend_falls_back_to_wxautox4(monkeypatch, caplog) -> None:
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "uia")

    caplog.set_level("WARNING")

    assert WeChatAdapter._bridge_backend() == "wxautox4"
    # The adapter resolver is pure; bootstrap owns the single warning.
    assert caplog.text == ""


def test_wechat_adapter_falls_back_to_plain_send_when_humanized_send_fails(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "wxautox4")

    adapter = WeChatAdapter(None, self_wxid=None)
    called: list[str] = []

    monkeypatch.setattr(
        adapter,
        "_send_text_humanized",
        lambda reply: (called.append(f"humanized:{reply.content}") or False, "segment=1/2; error=timeout"),
    )
    monkeypatch.setattr(
        adapter,
        "_send_via_bridge_queue",
        lambda reply: (called.append(f"plain:{reply.content}") or True),
    )

    reply = UnifiedReply(
        conversation_id="wxid_alice",
        platform=Platform.WECHAT,
        msg_type=MsgType.TEXT,
        content="消息发出去了吗",
    )

    assert adapter.send_reply(reply) is True
    assert called == ["humanized:消息发出去了吗", "plain:消息发出去了吗"]
    assert adapter.health()["last_outbound"]["detail"].startswith("bridge_queue_plain_fallback:")
