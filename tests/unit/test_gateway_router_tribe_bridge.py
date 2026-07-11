from __future__ import annotations

from edge_client.channels.input_reporter import InputReportResult, InputSourceType
from edge_client.gateway.models import MsgType, Platform, UnifiedMessage
from edge_client.gateway.router import GatewayRouter


class _StubReporter:
    def __init__(self, result: InputReportResult) -> None:
        self._result = result

    def report_gateway_payload(self, payload):  # noqa: ANN001
        return self._result


class _StubBridge:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish_event(self, **kwargs):  # noqa: ANN003
        self.events.append(dict(kwargs))
        return {"ok": True}


def _msg() -> UnifiedMessage:
    return UnifiedMessage(
        msg_id="gw_msg_1",
        platform=Platform.WECHAT,
        from_id="wxid_user",
        to_id="wxid_bot",
        conversation_id="conv_1",
        msg_type=MsgType.TEXT,
        content="hello",
        ts_ms=1234567890,
        is_group=False,
    )


def test_gateway_router_publishes_channel_message_received() -> None:
    reporter = _StubReporter(
        InputReportResult(
            success=True,
            source=InputSourceType.GATEWAY,
            reason_code=None,
            detail="ok",
            cached=False,
        )
    )
    bridge = _StubBridge()
    router = GatewayRouter(reporter=reporter, tribe_bridge=bridge)

    result = router.route(_msg())

    assert result.success is True
    assert len(bridge.events) >= 1
    event_types = [e["event_type"] for e in bridge.events]
    assert "channel_message_received" in event_types or "social_message_received" in event_types


def test_gateway_router_publishes_gateway_degraded_on_cached() -> None:
    reporter = _StubReporter(
        InputReportResult(
            success=False,
            source=InputSourceType.GATEWAY,
            reason_code="INPUT_REPORT_FAILED",
            detail="timeout",
            cached=True,
        )
    )
    bridge = _StubBridge()
    router = GatewayRouter(reporter=reporter, tribe_bridge=bridge)

    result = router.route(_msg())

    assert result.success is False
    event_types = [event["event_type"] for event in bridge.events]
    assert "channel_message_received" in event_types
    assert "gateway_degraded" in event_types
