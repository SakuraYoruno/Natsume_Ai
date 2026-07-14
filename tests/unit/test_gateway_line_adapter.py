from __future__ import annotations

from edge_client.gateway.adapters.line_adapter import LineAdapter


def test_line_missing_message_id_uses_stable_webhook_event_id() -> None:
    adapter = LineAdapter(channel_access_token="", channel_secret="")
    event = {
        "type": "message",
        "webhookEventId": "line-event-stable",
        "source": {"type": "user", "userId": "line-user"},
        "message": {"type": "text", "text": "hello"},
    }

    first = adapter.parse_inbound(event)
    second = adapter.parse_inbound(event)

    assert first is not None
    assert second is not None
    assert first.msg_id == "line-event:line-event-stable"
    assert second.msg_id == first.msg_id
    assert first.ts_ms == 0
    assert second.ts_ms == 0


def test_line_missing_all_platform_ids_uses_stable_event_digest() -> None:
    adapter = LineAdapter(channel_access_token="", channel_secret="")
    event = {
        "type": "message",
        "source": {"type": "user", "userId": "line-user"},
        "message": {"type": "text", "text": "hello"},
    }

    first = adapter.parse_inbound(event)
    second = adapter.parse_inbound(dict(event))

    assert first is not None
    assert second is not None
    assert first.msg_id.startswith("line-event-sha256:")
    assert second.msg_id == first.msg_id
