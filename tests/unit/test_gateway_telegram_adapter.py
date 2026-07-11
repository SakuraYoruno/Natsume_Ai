from __future__ import annotations

from types import SimpleNamespace

import pytest

from edge_client.gateway.adapters.telegram_adapter import TelegramAdapter


def _adapter() -> TelegramAdapter:
    return TelegramAdapter(
        api_id=123,
        api_hash="test-api-hash",
        phone="+10000000000",
    )


def _http_payload(**overrides):  # noqa: ANN003
    return {
        "msg_id": "telegram-message-1",
        "from_id": "telegram-user-1",
        "chat_id": "telegram-chat-1",
        "text": "stable retry",
        "media_type": "text",
        **overrides,
    }


def test_telegram_http_missing_timestamp_retry_is_fully_deterministic() -> None:
    adapter = _adapter()
    payload = _http_payload()

    first = adapter.parse_inbound(dict(payload))
    retried = adapter.parse_inbound(dict(payload))

    assert first is not None
    assert retried is not None
    assert first.ts_ms == retried.ts_ms == 0
    assert first.as_report_payload() == retried.as_report_payload()


@pytest.mark.parametrize(
    ("timestamp_field", "raw_value", "expected_ms"),
    [
        ("ts_ms", 1_774_453_000_123, 1_774_453_000_123),
        ("timestamp_ms", "1774453000123", 1_774_453_000_123),
        ("ts", 1_774_453_000, 1_774_453_000_000),
        ("timestamp", "1774453000", 1_774_453_000_000),
    ],
)
def test_telegram_http_timestamp_aliases_are_explicit(
    timestamp_field: str,
    raw_value: object,
    expected_ms: int,
) -> None:
    message = _adapter().parse_inbound(_http_payload(**{timestamp_field: raw_value}))

    assert message is not None
    assert message.ts_ms == expected_ms


@pytest.mark.parametrize(
    "invalid_timestamp",
    [True, False, float("nan"), float("inf"), float("-inf"), -1, "-0.5", "invalid"],
)
def test_telegram_http_invalid_timestamp_is_stable_unknown(
    invalid_timestamp: object,
) -> None:
    message = _adapter().parse_inbound(_http_payload(ts=invalid_timestamp))

    assert message is not None
    assert message.ts_ms == 0


def test_telegram_http_invalid_priority_alias_does_not_fall_through() -> None:
    message = _adapter().parse_inbound(
        _http_payload(ts_ms=-1, ts=1_774_453_000)
    )

    assert message is not None
    assert message.ts_ms == 0


def test_telegram_telethon_message_without_date_uses_stable_unknown() -> None:
    message = SimpleNamespace(
        photo=None,
        voice=None,
        video=None,
        document=None,
        sticker=None,
        gif=None,
        text="stable telethon event",
        raw_text="stable telethon event",
        action=None,
        sender_id=321,
        id=654,
        chat_id=987,
        peer_id=None,
        sender=None,
        reply_to=None,
        date=None,
    )
    event = SimpleNamespace(message=message, is_group=False, is_channel=False)

    first = _adapter()._telethon_event_to_unified(event)
    retried = _adapter()._telethon_event_to_unified(event)

    assert first is not None
    assert retried is not None
    assert first.ts_ms == retried.ts_ms == 0
    assert first.as_report_payload() == retried.as_report_payload()
