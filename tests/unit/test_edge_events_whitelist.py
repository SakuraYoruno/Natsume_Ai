from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

import host_brain.app.system_handler as system_handler
from host_brain.app.schemas import EdgeEventsRequest
from host_brain.app.system_handler import receive_edge_events_response


class _LedgerStub:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def log_voice_event(self, row: dict) -> None:
        self.rows.append(dict(row))

    def debug(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    def info(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    def warning(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    def error(self, *args, **kwargs) -> None:
        _ = (args, kwargs)


def test_edge_events_accepts_whitelisted_event_type() -> None:
    ledger = _LedgerStub()
    request_body = EdgeEventsRequest(
        events=[
            {
                "event_type": "asr_chunk",
                "ts_ms": int(time.time() * 1000),
                "request_id": "req-edge-allow-1",
                "session_id": "sess-edge-allow-1",
            }
        ]
    )

    response = receive_edge_events_response(
        request_body=request_body,
        get_ledger=lambda: ledger,
        logger=_LedgerStub(),
    )

    assert response.accepted == 1
    assert len(ledger.rows) == 1
    assert ledger.rows[0]["event_type"] == "ASR_CHUNK"


def test_edge_events_rejects_unknown_event_type_with_400() -> None:
    ledger = _LedgerStub()
    request_body = EdgeEventsRequest(
        events=[
            {
                "event_type": "UNKNOWN_EVENT_TYPE",
                "ts_ms": int(time.time() * 1000),
                "request_id": "req-edge-deny-1",
                "session_id": "sess-edge-deny-1",
            }
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        receive_edge_events_response(
            request_body=request_body,
            get_ledger=lambda: ledger,
            logger=_LedgerStub(),
        )

    assert exc_info.value.status_code == 400
    assert "Unknown edge event_type" in str(exc_info.value.detail)


def test_edge_events_rejects_unknown_event_source_with_400() -> None:
    ledger = _LedgerStub()
    request_body = EdgeEventsRequest(
        events=[
            {
                "event_type": "ASR_CHUNK",
                "ts_ms": int(time.time() * 1000),
                "request_id": "req-edge-deny-2",
                "session_id": "sess-edge-deny-2",
                "source": "public_internet",
            }
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        receive_edge_events_response(
            request_body=request_body,
            get_ledger=lambda: ledger,
            logger=_LedgerStub(),
        )

    assert exc_info.value.status_code == 400
    assert "Unknown edge event source" in str(exc_info.value.detail)


def test_edge_events_final_asr_chunk_collects_bridge_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _LedgerStub()
    logger = _LedgerStub()
    scheduled: list[dict] = []

    monkeypatch.setenv("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_ENABLED", "true")
    monkeypatch.setattr(
        system_handler,
        "_schedule_edge_asr_bridge",
        lambda *, candidate, logger: scheduled.append(dict(candidate)),
    )

    request_body = EdgeEventsRequest(
        events=[
            {
                "event_type": "ASR_CHUNK",
                "ts_ms": int(time.time() * 1000),
                "request_id": "req-edge-bridge-1",
                "session_id": "sess-edge-bridge-1",
                "payload": {
                    "text": "hello bridge",
                    "confidence": 0.93,
                    "final": True,
                    "seq": 7,
                },
            }
        ]
    )

    response = receive_edge_events_response(
        request_body=request_body,
        get_ledger=lambda: ledger,
        logger=logger,
    )

    assert response.accepted == 1
    assert len(scheduled) == 1
    candidate = scheduled[0]
    assert candidate["request_id"] == "req-edge-bridge-1"
    assert candidate["session_id"] == "sess-edge-bridge-1"
    assert candidate["text"] == "hello bridge"
    assert candidate["event_type"] == "ASR_CHUNK"
    assert candidate["payload"]["confidence"] == 0.93
    assert candidate["payload"]["is_final"] is True
    assert candidate["payload"]["seq"] == 7


def test_edge_events_non_final_asr_chunk_skips_bridge_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _LedgerStub()
    logger = _LedgerStub()
    scheduled: list[dict] = []

    monkeypatch.setenv("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_ENABLED", "true")
    monkeypatch.setattr(
        system_handler,
        "_schedule_edge_asr_bridge",
        lambda *, candidate, logger: scheduled.append(dict(candidate)),
    )

    request_body = EdgeEventsRequest(
        events=[
            {
                "event_type": "ASR_CHUNK",
                "ts_ms": int(time.time() * 1000),
                "request_id": "req-edge-bridge-non-final",
                "session_id": "sess-edge-bridge-non-final",
                "payload": {
                    "text": "partial bridge",
                    "confidence": 0.93,
                    "final": False,
                    "seq": 8,
                },
            }
        ]
    )

    response = receive_edge_events_response(
        request_body=request_body,
        get_ledger=lambda: ledger,
        logger=logger,
    )

    assert response.accepted == 1
    assert scheduled == []


def test_edge_events_placeholder_asr_text_skips_bridge_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _LedgerStub()
    logger = _LedgerStub()
    scheduled: list[dict] = []

    monkeypatch.setenv("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_ENABLED", "true")
    monkeypatch.setattr(
        system_handler,
        "_schedule_edge_asr_bridge",
        lambda *, candidate, logger: scheduled.append(dict(candidate)),
    )

    request_body = EdgeEventsRequest(
        events=[
            {
                "event_type": "ASR_CHUNK",
                "ts_ms": int(time.time() * 1000),
                "request_id": "req-edge-bridge-2",
                "session_id": "sess-edge-bridge-2",
                "payload": {
                    "text": "[ASR_VAD_NO_SPEECH]",
                    "confidence": 0.99,
                    "final": True,
                    "seq": 8,
                },
            }
        ]
    )

    response = receive_edge_events_response(
        request_body=request_body,
        get_ledger=lambda: ledger,
        logger=logger,
    )

    assert response.accepted == 1
    assert scheduled == []


def test_schedule_edge_asr_bridge_allows_same_text_with_new_seq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[object] = []

    class _LoopStub:
        def create_task(self, coro):  # noqa: ANN001
            scheduled.append(coro)
            coro.close()
            return object()

    monkeypatch.setenv("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_DEDUP_WINDOW_MS", "15000")
    monkeypatch.setattr(system_handler.asyncio, "get_running_loop", lambda: _LoopStub())
    system_handler._EDGE_ASR_BRIDGE_RECENT.clear()
    system_handler._EDGE_ASR_BRIDGE_LAST_TEXT.clear()

    system_handler._schedule_edge_asr_bridge(
        candidate={
            "request_id": "req-a",
            "session_id": "sess-a",
            "text": "哥哥,现在测试真实采集",
            "ts_ms": 1000,
            "payload": {"text": "哥哥,现在测试真实采集", "seq": 1, "is_final": True},
        },
        logger=_LedgerStub(),
    )
    system_handler._schedule_edge_asr_bridge(
        candidate={
            "request_id": "req-b",
            "session_id": "sess-a",
            "text": "哥哥,现在测试真实采集",
            "ts_ms": 2500,
            "payload": {"text": "哥哥,现在测试真实采集", "seq": 2, "is_final": True},
        },
        logger=_LedgerStub(),
    )

    assert len(scheduled) == 2


def test_schedule_edge_asr_bridge_dedups_same_text_same_seq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[object] = []

    class _LoopStub:
        def create_task(self, coro):  # noqa: ANN001
            scheduled.append(coro)
            coro.close()
            return object()

    monkeypatch.setenv("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_DEDUP_WINDOW_MS", "15000")
    monkeypatch.setattr(system_handler.asyncio, "get_running_loop", lambda: _LoopStub())
    system_handler._EDGE_ASR_BRIDGE_RECENT.clear()
    system_handler._EDGE_ASR_BRIDGE_LAST_TEXT.clear()

    candidate = {
        "request_id": "req-same",
        "session_id": "sess-same",
        "text": "哥哥,现在测试真实采集",
        "ts_ms": 1000,
        "payload": {"text": "哥哥,现在测试真实采集", "seq": 3, "is_final": True},
    }
    system_handler._schedule_edge_asr_bridge(candidate=candidate, logger=_LedgerStub())
    system_handler._schedule_edge_asr_bridge(
        candidate={**candidate, "request_id": "req-same-2", "ts_ms": 1800},
        logger=_LedgerStub(),
    )

    assert len(scheduled) == 1
