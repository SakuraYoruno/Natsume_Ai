from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.input_routes import (
    _build_gateway_intent_draft,
    _build_gateway_reply_text,
    _deliver_gateway_reply,
    _detect_gateway_execution_deviation,
    _dispatch_to_graph,
    _dispatch_to_graph_worker,
    _maybe_realign_gateway_pipeline,
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
            new=AsyncMock(return_value={"success": True, "status": "OK", "latency_ms": 23, "reply_text_len": 20}),
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


def test_dispatch_worker_gateway_reply_failure_logs_completed_degraded() -> None:
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
        assert "INPUT_COMPLETED" in event_types
        assert "INPUT_FAILED" not in event_types
        assert "INPUT_NOT_WIRED" not in event_types

        completed = next(row for row in rows if row["event_type"] == "INPUT_COMPLETED")
        assert completed["status"] == "DEGRADED"
        assert completed["reason_code"] == "GATEWAY_REPLY_EXCEPTION"
        assert "gateway_success=false" in str(completed.get("detail") or "")
        assert "gateway_reason_code=GATEWAY_REPLY_EXCEPTION" in str(completed.get("detail") or "")
    finally:
        ledger.close()


def test_dispatch_worker_gateway_reply_failure_can_be_ignored_for_completion_status() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        with patch.dict(
            "os.environ",
            {"HOST_GATEWAY_REPLY_FAILURE_DEGRADE_INPUT": "false"},
            clear=False,
        ), patch(
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
                    request_id="req_worker_fail_ignored",
                    session_id="qq_dm_1003",
                    source="GATEWAY",
                    text="hi",
                    payload={"platform": "qq", "conversation_id": "qq_dm_1003"},
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        completed = next(row for row in ledger.voice_event_log if row["event_type"] == "INPUT_COMPLETED")
        assert completed["status"] == "OK"
        assert completed["reason_code"] is None
        assert "gateway_success=false" in str(completed.get("detail") or "")
        assert "gateway_failure_ignored=true" in str(completed.get("detail") or "")
    finally:
        ledger.close()


def test_dispatch_worker_logs_route_decided_event_with_route_payload() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        with patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "已完成",
                    "route_decision": {
                        "route": "delegate",
                        "target_id": "code_agent",
                        "task": "修复测试",
                        "reason_code": "LLM_ROUTE_CODE_AGENT",
                        "source": "llm",
                        "confidence": 0.91,
                        "override_reason": None,
                        "llm_route_used": True,
                    },
                }
            ),
        ), patch(
            "host_brain.app.input_routes._deliver_gateway_reply",
            new=AsyncMock(return_value={"success": True, "status": "OK", "latency_ms": 20, "reply_text_len": 8}),
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_route_event",
                    session_id="qq_dm_route_event",
                    source="GATEWAY",
                    text="请修复测试",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_route_event",
                        "msg_id": "msg_route_event",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        rows = ledger.voice_event_log
        route_row = next(row for row in rows if row["event_type"] == "INPUT_ROUTE_DECIDED")
        route_payload = dict(route_row.get("payload") or {})
        assert route_row["status"] == "OK"
        assert route_payload["route_type"] == "delegate"
        assert route_payload["route_target"] == "code_agent"
        assert route_payload["route_source"] == "llm"
        assert float(route_payload["route_confidence"]) == 0.91
        assert route_payload["llm_route_used"] is True

        completed_row = next(row for row in rows if row["event_type"] == "INPUT_COMPLETED")
        completed_payload = dict(completed_row.get("payload") or {})
        assert completed_payload["llm_route_used"] is True
    finally:
        ledger.close()


def test_dispatch_worker_degrades_when_delegate_route_missing_skill_exec() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        with patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "delegate done",
                    "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
                    "route_decision": {
                        "route": "delegate",
                        "target_id": "code_agent",
                        "task": "fix code",
                        "reason_code": "LLM_ROUTE_CODE_AGENT",
                        "source": "llm",
                        "confidence": 0.95,
                        "llm_route_used": True,
                    },
                }
            ),
        ), patch(
            "host_brain.app.input_routes._deliver_gateway_reply",
            new=AsyncMock(return_value={"success": True, "status": "OK", "latency_ms": 15, "reply_text_len": 12}),
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_quality_missing_exec",
                    session_id="qq_dm_quality_missing_exec",
                    source="GATEWAY",
                    text="please fix this bug",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_quality_missing_exec",
                        "msg_id": "msg_quality_missing_exec",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        completed = next(row for row in ledger.voice_event_log if row["event_type"] == "INPUT_COMPLETED")
        assert completed["status"] == "DEGRADED"
        assert completed["reason_code"] == "SKILL_EXEC_MISSING"
        completed_payload = dict(completed.get("payload") or {})
        assert completed_payload.get("exec_quality_degraded") is True
        assert completed_payload.get("exec_quality_reason_code") == "SKILL_EXEC_MISSING"
    finally:
        ledger.close()


def test_dispatch_worker_degrades_when_skill_exec_is_no_effect() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        ledger.log_skill_exec(
            {
                "request_id": "req_worker_quality_no_effect",
                "intent_id": "intent::req_worker_quality_no_effect::memory_store",
                "event_id": "evt::req_worker_quality_no_effect::memory_store",
                "skill_name": "memory_store",
                "result_status": "SUCCESS",
                "result_code": None,
                "result_digest": "no memory writer available",
                "ts_ms": 123450,
            }
        )

        with patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "memory done",
                    "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
                    "route_decision": {
                        "route": "skill",
                        "target_id": "memory_store",
                        "task": "remember this",
                        "reason_code": "LLM_ROUTE_MEMORY_STORE",
                        "source": "llm",
                        "confidence": 0.9,
                        "llm_route_used": True,
                    },
                }
            ),
        ), patch(
            "host_brain.app.input_routes._deliver_gateway_reply",
            new=AsyncMock(return_value={"success": True, "status": "OK", "latency_ms": 20, "reply_text_len": 11}),
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_quality_no_effect",
                    session_id="qq_dm_quality_no_effect",
                    source="GATEWAY",
                    text="remember this",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_quality_no_effect",
                        "msg_id": "msg_quality_no_effect",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        completed = next(row for row in ledger.voice_event_log if row["event_type"] == "INPUT_COMPLETED")
        assert completed["status"] == "DEGRADED"
        assert completed["reason_code"] == "SKILL_EXEC_NO_EFFECT"
        completed_payload = dict(completed.get("payload") or {})
        assert completed_payload.get("exec_quality_degraded") is True
        assert completed_payload.get("exec_quality_reason_code") == "SKILL_EXEC_NO_EFFECT"
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


def test_build_gateway_intent_draft_prefers_llm_route_when_available() -> None:
    with patch(
        "host_brain.app.input_routes._run_gateway_intent_route_llm",
        new=AsyncMock(
            return_value={
                "route": "delegate",
                "target_id": "code_agent",
                "task": "请修复这个报错",
                "reason_code": "LLM_ROUTE_CODE_AGENT",
                "confidence": 0.93,
                "route_source": "llm",
                "llm_route_used": True,
            }
        ),
    ):
        draft = _run(
            _build_gateway_intent_draft(
                request_id="req_llm_route_001",
                session_id="qq_dm_llm_001",
                source="GATEWAY",
                text="请修复这个报错",
            )
        )

    assert draft["route"] == "delegate"
    assert draft["target_id"] == "code_agent"
    assert draft["delegate_agent"] == "code_agent"
    assert draft["route_source"] == "llm"
    assert draft["llm_route_used"] is True
    assert float(draft["route_confidence"]) == 0.93


def test_build_gateway_intent_draft_falls_back_to_rule_when_llm_unavailable() -> None:
    with patch(
        "host_brain.app.input_routes._run_gateway_intent_route_llm",
        new=AsyncMock(return_value=None),
    ):
        draft = _run(
            _build_gateway_intent_draft(
                request_id="req_llm_route_002",
                session_id="qq_dm_llm_002",
                source="GATEWAY",
                text="run diagnostics now",
            )
        )

    assert draft["route"] == "delegate"
    assert draft["target_id"] == "intent_agent"
    assert draft["route_source"] == "rule"
    assert draft["llm_route_used"] is False
    assert draft["route_reason"] == "RULE_ACTION_INTENT"


def test_build_gateway_intent_draft_force_overrides_translate() -> None:
    with patch(
        "host_brain.app.input_routes._run_gateway_intent_route_llm",
        new=AsyncMock(
            return_value={
                "route": "chat",
                "target_id": "",
                "task": "",
                "reason_code": "LLM_ROUTE_CHAT",
                "confidence": 0.88,
                "route_source": "llm",
                "llm_route_used": True,
            }
        ),
    ):
        draft = _run(
            _build_gateway_intent_draft(
                request_id="req_llm_route_003",
                session_id="qq_dm_llm_003",
                source="GATEWAY",
                text="请翻译成英文：你好",
            )
        )

    assert draft["route"] == "delegate"
    assert draft["target_id"] == "translate_agent"
    assert draft["route_source"] == "override"
    assert draft["route_override_reason"] == "FORCE_OVERRIDE_TRANSLATE_AGENT"
    assert draft["llm_route_used"] is True


def test_build_gateway_intent_draft_keeps_translate_priority_when_text_contains_bug() -> None:
    with patch(
        "host_brain.app.input_routes._run_gateway_intent_route_llm",
        new=AsyncMock(
            return_value={
                "route": "delegate",
                "target_id": "translate_agent",
                "task": "请翻译成英文：今天修了三个bug",
                "reason_code": "LLM_ROUTE_TRANSLATE_AGENT",
                "confidence": 0.92,
                "route_source": "llm",
                "llm_route_used": True,
            }
        ),
    ):
        draft = _run(
            _build_gateway_intent_draft(
                request_id="req_llm_route_004",
                session_id="qq_dm_llm_004",
                source="GATEWAY",
                text="请翻译成英文：今天修了三个bug",
            )
        )

    assert draft["route"] == "delegate"
    assert draft["target_id"] == "translate_agent"
    assert draft.get("route_override_reason") != "FORCE_OVERRIDE_CODE_AGENT"


def test_build_gateway_intent_draft_force_overrides_recent_turn_query() -> None:
    with patch(
        "host_brain.app.input_routes._run_gateway_intent_route_llm",
        new=AsyncMock(
            return_value={
                "route": "chat",
                "target_id": "",
                "task": "",
                "reason_code": "LLM_ROUTE_CHAT",
                "confidence": 0.88,
                "route_source": "llm",
                "llm_route_used": True,
            }
        ),
    ):
        draft = _run(
            _build_gateway_intent_draft(
                request_id="req_llm_route_005",
                session_id="qq_dm_llm_005",
                source="GATEWAY",
                text="你刚刚说了什么",
            )
        )

    assert draft["route"] == "recent_turn"
    assert draft["target_id"] == ""
    assert draft["route_source"] == "override"
    assert draft["route_override_reason"] == "FORCE_OVERRIDE_RECENT_TURN"


def test_build_gateway_intent_draft_force_overrides_task_progress_query() -> None:
    with patch(
        "host_brain.app.input_routes._run_gateway_intent_route_llm",
        new=AsyncMock(
            return_value={
                "route": "chat",
                "target_id": "",
                "task": "",
                "reason_code": "LLM_ROUTE_CHAT",
                "confidence": 0.82,
                "route_source": "llm",
                "llm_route_used": True,
            }
        ),
    ):
        draft = _run(
            _build_gateway_intent_draft(
                request_id="req_llm_route_005a",
                session_id="qq_dm_llm_005a",
                source="GATEWAY",
                text="现在进度怎么样了",
            )
        )

    assert draft["route"] == "task_progress"
    assert draft["target_id"] == ""
    assert draft["route_source"] == "override"
    assert draft["route_override_reason"] == "FORCE_OVERRIDE_TASK_PROGRESS"


def test_build_gateway_intent_draft_rule_fallback_captures_save_memory_phrase() -> None:
    with patch(
        "host_brain.app.input_routes._run_gateway_intent_route_llm",
        new=AsyncMock(return_value=None),
    ):
        draft = _run(
            _build_gateway_intent_draft(
                request_id="req_llm_route_006",
                session_id="qq_dm_llm_006",
                source="GATEWAY",
                text="保存这条记忆：我周五晚上有空",
            )
        )

    assert draft["route"] in {"skill", "delegate"}
    if draft["route"] == "skill":
        assert draft["target_id"] == "memory_store"


def test_build_gateway_reply_text_fallback_for_empty_reject() -> None:
    text = _build_gateway_reply_text(
        decision="REJECT",
        narrative="",
        reason_code="RATE_LIMIT",
    )
    assert "重试" in text


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


def test_deliver_gateway_reply_skips_when_auto_reply_disabled(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INPUT_GATEWAY_AUTO_REPLY", "false")
    result = _run(
        _deliver_gateway_reply(
            request_id="req_gateway_reply_skip_001",
            payload={"platform": "qq", "conversation_id": "qq_dm_skip_001"},
            decision="APPROVE",
            reason_code=None,
            narrative="任务已完成",
            user_msg_type="text",
        )
    )
    assert result["success"] is True
    assert result["status"] == "SKIPPED"
    assert result["reason_code"] == "GATEWAY_REPLY_DISABLED"
    assert result["reply_form"] == "text"
    assert result["reply_form_reason"] == "auto_reply_disabled"


def test_dispatch_worker_gateway_auto_reply_disabled_still_completes_ok(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        monkeypatch.setenv("HOST_INPUT_GATEWAY_AUTO_REPLY", "false")
        with patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "任务已完成",
                }
            ),
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_auto_reply_off_001",
                    session_id="qq_dm_auto_reply_off_001",
                    source="GATEWAY",
                    text="请帮我总结今天的执行结果",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_auto_reply_off_001",
                        "msg_id": "msg_auto_reply_off_001",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        completed = next(row for row in ledger.voice_event_log if row["event_type"] == "INPUT_COMPLETED")
        assert completed["status"] == "OK"
        assert "gateway_success=true" in str(completed.get("detail") or "")
        assert "gateway_status=SKIPPED" in str(completed.get("detail") or "")
        assert "gateway_reason_code=GATEWAY_REPLY_DISABLED" in str(completed.get("detail") or "")
    finally:
        ledger.close()


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


def test_detect_gateway_execution_deviation_triggers_on_low_overlap() -> None:
    result = _detect_gateway_execution_deviation(
        source="GATEWAY",
        user_text="请帮我总结今天前端卡顿原因并给出修复方案",
        decision="APPROVE",
        narrative="好的，我们来聊聊今天的心情和天气。",
    )

    assert result["triggered"] is True
    assert result["reason_code"] == "DEVIATION_OVERLAP_LOW"
    assert float(result["overlap_ratio"]) < 0.18


def test_detect_gateway_execution_deviation_skips_when_overlap_ok() -> None:
    result = _detect_gateway_execution_deviation(
        source="GATEWAY",
        user_text="请帮我总结今天前端卡顿原因并给出修复方案",
        decision="APPROVE",
        narrative="前端卡顿原因主要是主线程阻塞和轮询过密，修复方案是异步化和缓存。",
    )

    assert result["triggered"] is False
    assert result["reason_code"] in {"DEVIATION_OVERLAP_OK", "DEVIATION_KEYWORDS_INSUFFICIENT"}


def test_maybe_realign_gateway_pipeline_retries_when_deviation(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        monkeypatch.setenv("HOST_GATEWAY_DEVIATION_GUARD_ENABLED", "true")
        monkeypatch.setenv("HOST_GATEWAY_DEVIATION_RETRY_ENABLED", "true")
        with patch(
            "host_brain.app.input_routes._maybe_send_gateway_realign_notice",
            new=AsyncMock(return_value={"sent": True, "status": "SENT"}),
        ), patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "前端卡顿原因是主线程阻塞与高频刷新，修复是异步线程化和节流。",
                    "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
                }
            ),
        ) as rerun_mock:
            result, meta = _run(
                _maybe_realign_gateway_pipeline(
                    request_id="req_realign_001",
                    session_id="qq_dm_5001",
                    source="GATEWAY",
                    user_text="请帮我总结前端卡顿原因并给出修复方案",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_5001",
                        "sender_name": "测试用户",
                    },
                    ts_ms=123456,
                    pipeline_result={
                        "decision": "APPROVE",
                        "reason_code": None,
                        "narrative": "我们先聊聊别的事情吧。",
                        "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
                    },
                    ledger=ledger,
                )
            )

        rerun_mock.assert_awaited_once()
        assert meta is not None
        assert meta["triggered"] is True
        assert meta["retry_applied"] is True
        assert "卡顿原因" in str(result.get("narrative") or "")
    finally:
        ledger.close()
