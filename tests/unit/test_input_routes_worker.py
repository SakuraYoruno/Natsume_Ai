from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.input_routes import (
    _build_gateway_reply_text,
    _dispatch_to_graph,
    _dispatch_to_graph_worker,
    _run_gateway_proactive_progress_push,
    _run_cortex_pipeline,
    _should_start_gateway_progress_push,
    _should_send_gateway_immediate_ack,
    _try_handle_gateway_progress_fastpath,
)
from host_brain.app.prompt_template_store import PromptTemplateStore


def _run(coro):
    return asyncio.run(coro)


def test_dispatch_worker_gateway_completes_and_logs_completed() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        with patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
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
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
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


def test_run_cortex_pipeline_forwards_to_cortex_orchestrator() -> None:
    expected = {
        "decision": "APPROVE",
        "reason_code": None,
        "narrative": "compat shim",
        "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
    }
    with patch(
        "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
        new=AsyncMock(return_value=expected),
    ) as mocked:
        result = _run(
            _run_cortex_pipeline(
                request_id="req_compat",
                session_id="sess_compat",
                source="GATEWAY",
                text="hello",
                payload={},
                ts_ms=1,
            )
        )

    mocked.assert_awaited_once()
    assert result == expected


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


def test_should_send_gateway_immediate_ack_for_action_intent() -> None:
    should_send, reason = _should_send_gateway_immediate_ack(
        source="GATEWAY",
        text="请帮我总结一下今天的执行结果",
        payload={"msg_type": "text"},
    )

    assert should_send is True
    assert reason == "ACK_ACTION_INTENT"


def test_should_skip_gateway_immediate_ack_for_progress_query() -> None:
    should_send, reason = _should_send_gateway_immediate_ack(
        source="GATEWAY",
        text="进度怎么样了，完成了吗？",
        payload={"msg_type": "text"},
    )

    assert should_send is False
    assert reason == "ACK_PROGRESS_QUERY"


def test_dispatch_to_graph_schedules_gateway_immediate_ack(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")
        monkeypatch.setenv("HOST_GATEWAY_IMMEDIATE_ACK_ENABLED", "true")
        with patch(
            "host_brain.app.input_routes._get_ledger_safe",
            return_value=ledger,
        ), patch(
            "host_brain.app.input_routes._dispatch_to_graph_worker",
            new=AsyncMock(return_value=None),
        ), patch(
            "host_brain.app.input_routes._maybe_schedule_gateway_immediate_ack",
        ) as ack_mock:
            _run(
                _dispatch_to_graph(
                    request_id="req_ack_schedule_001",
                    session_id="qq_dm_2001",
                    source="GATEWAY",
                    text="请帮我整理一下今天的任务清单",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_2001",
                        "msg_type": "text",
                    },
                    ts_ms=123456,
                )
            )

        ack_mock.assert_called_once()
    finally:
        ledger.close()


def test_gateway_progress_fastpath_replies_from_runtime_registry(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)

    class _FakeRuntimeTaskRegistry:
        def summarize_for_chat(self, **_kwargs):  # noqa: ANN003
            return {
                "status": "RUNNING",
                "target_id": "session_summary",
                "progress_message": "已完成检索，正在汇总",
                "elapsed_ms": 42000,
                "result_text": "",
            }

    try:
        monkeypatch.setenv("HOST_GATEWAY_PROGRESS_FASTPATH_ENABLED", "true")
        with patch(
            "host_brain.app.dependencies.get_runtime_task_registry",
            return_value=_FakeRuntimeTaskRegistry(),
        ), patch(
            "host_brain.channels.edge_rpc_client.post_gateway_reply",
            new=AsyncMock(return_value=SimpleNamespace(success=True, status="SENT", reason_code=None, latency_ms=28)),
        ) as post_mock:
            handled = _run(
                _try_handle_gateway_progress_fastpath(
                    request_id="req_progress_fp_001",
                    session_id="qq_dm_3001",
                    source="GATEWAY",
                    text="现在进度怎么样了？",
                    payload={
                        "person_id": "person_demo",
                        "platform": "qq",
                        "conversation_id": "qq_dm_3001",
                        "sender_name": "测试用户",
                    },
                    ledger=ledger,
                    ts_ms=123456,
                )
            )

        assert handled is True
        post_mock.assert_awaited_once()
        kwargs = post_mock.await_args.kwargs
        assert kwargs["platform"] == "qq"
        assert kwargs["conversation_id"] == "qq_dm_3001"
        assert "session_summary" in kwargs["content"]
    finally:
        ledger.close()


def test_gateway_progress_fastpath_send_failure_falls_back(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)

    class _FakeRuntimeTaskRegistry:
        def summarize_for_chat(self, **_kwargs):  # noqa: ANN003
            return {
                "status": "RUNNING",
                "target_id": "session_summary",
                "progress_message": "正在执行",
                "elapsed_ms": 2000,
            }

    try:
        monkeypatch.setenv("HOST_GATEWAY_PROGRESS_FASTPATH_ENABLED", "true")
        with patch(
            "host_brain.app.dependencies.get_runtime_task_registry",
            return_value=_FakeRuntimeTaskRegistry(),
        ), patch(
            "host_brain.channels.edge_rpc_client.post_gateway_reply",
            new=AsyncMock(return_value=SimpleNamespace(success=False, status="FAILED", reason_code="TIMEOUT", latency_ms=100)),
        ):
            handled = _run(
                _try_handle_gateway_progress_fastpath(
                    request_id="req_progress_fp_002",
                    session_id="qq_dm_3002",
                    source="GATEWAY",
                    text="进度到哪了",
                    payload={
                        "person_id": "person_demo",
                        "platform": "qq",
                        "conversation_id": "qq_dm_3002",
                    },
                    ledger=ledger,
                    ts_ms=123456,
                )
            )

        assert handled is False
    finally:
        ledger.close()


def test_should_start_gateway_progress_push_for_gateway_action() -> None:
    should_start, reason = _should_start_gateway_progress_push(
        source="GATEWAY",
        text="请帮我跑一下今天的全量测试并汇总结果",
        payload={"platform": "qq", "conversation_id": "qq_dm_4001"},
    )

    assert should_start is True
    assert reason == "PROGRESS_PUSH_ENABLED"


def test_should_skip_gateway_progress_push_for_progress_query() -> None:
    should_start, reason = _should_start_gateway_progress_push(
        source="GATEWAY",
        text="现在进度怎么样了",
        payload={"platform": "qq", "conversation_id": "qq_dm_4002"},
    )

    assert should_start is False
    assert reason == "PROGRESS_PUSH_PROGRESS_QUERY"


def test_run_gateway_proactive_progress_push_sends_update(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    monkeypatch.setenv("HOST_GATEWAY_PROGRESS_PUSH_WARMUP_MS", "0")
    monkeypatch.setenv("HOST_GATEWAY_PROGRESS_PUSH_INTERVAL_MS", "10")
    monkeypatch.setenv("HOST_GATEWAY_PROGRESS_PUSH_MAX_UPDATES", "1")
    monkeypatch.setenv("HOST_GATEWAY_PROGRESS_PUSH_WAIT_RESULT", "true")
    monkeypatch.setenv("HOST_GATEWAY_PROGRESS_PUSH_TIMEOUT_MS", "2000")
    monkeypatch.setattr(
        "host_brain.app.input_routes._build_gateway_progress_reply_text",
        lambda **_kwargs: (
            "正在处理 session_summary，已进行 12s。当前进展：已完成检索，正在汇总",
            "PROGRESS_SUMMARY_FOUND",
            {"status": "RUNNING", "target_id": "session_summary", "progress_message": "已完成检索，正在汇总"},
        ),
    )

    stop_event = asyncio.Event()
    try:
        with patch(
            "host_brain.channels.edge_rpc_client.post_gateway_reply",
            new=AsyncMock(return_value=SimpleNamespace(success=True, status="SENT", reason_code=None, latency_ms=30)),
        ) as post_mock:
            _run(
                _run_gateway_proactive_progress_push(
                    request_id="req_progress_push_001",
                    session_id="qq_dm_4003",
                    source="GATEWAY",
                    text="请执行任务",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_4003",
                        "person_id": "person_demo",
                    },
                    ledger=ledger,
                    stop_event=stop_event,
                )
            )

        post_mock.assert_awaited_once()
        kwargs = post_mock.await_args.kwargs
        assert kwargs["platform"] == "qq"
        assert kwargs["conversation_id"] == "qq_dm_4003"
        assert "session_summary" in kwargs["content"]
    finally:
        ledger.close()
