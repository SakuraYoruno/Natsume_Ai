from __future__ import annotations

from edge_client.gateway.humanize_sender import HumanizeSendConfig, humanize_send_sync


def test_humanize_send_sync_defaults_to_disabled(monkeypatch) -> None:
    monkeypatch.delenv("EDGE_HUMANIZE_ENABLED", raising=False)
    sent: list[str] = []
    text = "这是一条较长的卡片消息。" * 20

    def _send(value: str) -> bool:
        sent.append(value)
        return True

    result = humanize_send_sync(
        text,
        send_fn=_send,
        config=HumanizeSendConfig.from_env(),
    )

    assert result.success is True
    assert result.total_segments == 1
    assert result.sent_segments == 1
    assert result.sleep_delay_ms == 0
    assert sent == [text]


def test_humanize_send_sync_bypasses_short_text_delay() -> None:
    sent: list[str] = []

    def _send(text: str) -> bool:
        sent.append(text)
        return True

    result = humanize_send_sync(
        "夏目蓝。",
        send_fn=_send,
        config=HumanizeSendConfig(
            enabled=True,
            short_text_bypass_len=12,
        ),
    )

    assert result.success is True
    assert result.total_segments == 1
    assert result.sent_segments == 1
    assert result.sleep_delay_ms == 0
    assert sent == ["夏目蓝。"]
