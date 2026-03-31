from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.input_routes import InputPayload, _log_input_event, receive_input


def _run(coro):
    return asyncio.run(coro)


def test_receive_input_rejects_placeholder_gateway_conversation_id() -> None:
    body = InputPayload(
        source="GATEWAY",
        platform="qq",
        conversation_id="current_chat",
        content="hello",
    )

    response = _run(receive_input(body, SimpleNamespace()))

    assert response.status == "REJECTED"
    assert "invalid gateway conversation_id" in response.detail


def test_receive_input_dispatches_final_asr_payload() -> None:
    body = InputPayload(
        source="ASR",
        session_id="session-001",
        text="hello world",
        is_final=True,
    )
    dispatch_mock = AsyncMock(return_value=None)

    with patch("host_brain.app.input_routes._dispatch_to_graph", new=dispatch_mock), patch(
        "host_brain.app.dependencies.notify_user_interaction",
        return_value=None,
    ):
        response = _run(receive_input(body, SimpleNamespace()))

    assert response.status == "ACCEPTED"
    assert dispatch_mock.await_count == 1
    assert dispatch_mock.await_args.kwargs["source"] == "ASR"
    assert dispatch_mock.await_args.kwargs["text"] == "hello world"


def test_log_input_event_persists_gateway_platform_metadata() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        _log_input_event(
            ledger=ledger,
            request_id="req_gateway_platform",
            session_id="conv_generic_001",
            source="GATEWAY",
            input_payload={
                "platform": "telegram",
                "conversation_id": "conv_generic_001",
                "msg_id": "tg-msg-001",
                "msg_type": "text",
                "from_id": "user-a",
                "to_id": "bot-b",
                "is_group": False,
            },
            phase="ACCEPTED",
            status="ACCEPTED",
            detail="source=GATEWAY platform=telegram",
            ts_ms=123456,
        )
        rows = ledger.voice_event_log
        assert len(rows) == 1
        payload = rows[0]["payload"]
        assert payload["platform"] == "telegram"
        assert payload["conversation_id"] == "conv_generic_001"
        assert payload["msg_id"] == "tg-msg-001"
        assert payload["msg_type"] == "text"
        assert payload["from_id"] == "user-a"
        assert payload["to_id"] == "bot-b"
        assert payload["is_group"] is False
    finally:
        ledger.close()
