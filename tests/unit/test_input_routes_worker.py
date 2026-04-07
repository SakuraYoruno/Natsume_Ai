from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.input_routes import _build_gateway_reply_text, _dispatch_to_graph_worker
from host_brain.app.prompt_template_store import PromptTemplateStore


def _run(coro):
    return asyncio.run(coro)


def test_dispatch_worker_gateway_completes_and_logs_completed() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        with patch(
            "host_brain.app.input_routes._run_top_agent_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "hello from top agent",
                }
            ),
        ), patch(
            "host_brain.app.input_routes._deliver_gateway_reply",
            new=AsyncMock(return_value={"status": "OK", "latency_ms": 23, "reply_text_len": 20}),
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_ok",
                    session_id="qq_dm_1001",
                    source="GATEWAY",
                    text="hi",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_1001",
                        "msg_id": "msg_1001",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        rows = ledger.voice_event_log
        event_types = [row["event_type"] for row in rows]
        assert "INPUT_DISPATCHED" in event_types
        assert "INPUT_COMPLETED" in event_types
        assert "INPUT_NOT_WIRED" not in event_types

        completed = next(row for row in rows if row["event_type"] == "INPUT_COMPLETED")
        assert completed["status"] == "OK"
        assert "gateway_status=OK" in str(completed.get("detail") or "")
    finally:
        ledger.close()


def test_dispatch_worker_gateway_reply_failure_logs_failed() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        with patch(
            "host_brain.app.input_routes._run_top_agent_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "hello from top agent",
                }
            ),
        ), patch(
            "host_brain.app.input_routes._deliver_gateway_reply",
            new=AsyncMock(side_effect=RuntimeError("reply failed")),
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_fail",
                    session_id="qq_dm_1002",
                    source="GATEWAY",
                    text="hi",
                    payload={"platform": "qq", "conversation_id": "qq_dm_1002"},
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        rows = ledger.voice_event_log
        event_types = [row["event_type"] for row in rows]
        assert "INPUT_FAILED" in event_types
        assert "INPUT_NOT_WIRED" not in event_types

        failed = next(row for row in rows if row["event_type"] == "INPUT_FAILED")
        assert failed["status"] == "FAILED"
        assert failed["reason_code"] == "INPUT_GRAPH_ERROR"
    finally:
        ledger.close()


def test_build_gateway_reply_text_fallback_for_empty_reject() -> None:
    text = _build_gateway_reply_text(
        decision="REJECT",
        narrative="",
        reason_code="RATE_LIMIT",
    )
    assert "再试试" in text


def test_build_gateway_reply_text_uses_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "input_routes.fallback_texts",
        content=json.dumps(
            {
                "reject_default": "先等等我，我马上回来。",
                "approve_empty": "已经收到啦。",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    assert _build_gateway_reply_text(
        decision="REJECT",
        narrative="",
        reason_code="RATE_LIMIT",
    ) == "先等等我，我马上回来。"
    assert _build_gateway_reply_text(
        decision="APPROVE",
        narrative="",
        reason_code=None,
    ) == "已经收到啦。"
