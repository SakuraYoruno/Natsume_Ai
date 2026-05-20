from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.input_routes import (
    _build_gateway_route_started_voice_ack_text,
    _build_gateway_reply_text,
    _build_gateway_voice_text,
    _deliver_gateway_reply,
    _detect_gateway_execution_deviation,
    _dispatch_to_graph,
    _dispatch_to_graph_worker,
    _maybe_realign_gateway_pipeline,
    _run_cortex_orchestrator_pipeline,
    _run_gateway_proactive_progress_push,
    _run_cortex_pipeline,
    _should_start_gateway_progress_push,
    _should_send_gateway_immediate_ack,
    _should_send_gateway_route_started_voice_ack,
    _try_handle_gateway_progress_fastpath,
)
from host_brain.app.schemas import ChatRequest
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.tribe.thalamus_agent import classify_task_intent
from tests.text_quality_assertions import assert_natsume_style


def _run(coro):
    return asyncio.run(coro)


def test_chat_request_declares_gateway_multimodal_fields() -> None:
    request = ChatRequest(
        message="[表情消息]",
        input_source="GATEWAY",
        render_target_lang="zh",
        attachments=[{"type": "emoji", "attachment_id": "att_emoji_1"}],
        multimodal_observations={"primary_modality": "emoji"},
        attachment_summary="用户发来一个偏撒娇的表情。",
    )

    assert request.render_target_lang == "zh"
    assert request.attachments == [{"type": "emoji", "attachment_id": "att_emoji_1"}]
    assert request.multimodal_observations == {"primary_modality": "emoji"}
    assert request.attachment_summary == "用户发来一个偏撒娇的表情。"


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
        completed_payload = dict(completed.get("payload") or {})
        assert completed["status"] == "DEGRADED"
        assert completed["reason_code"] == "GATEWAY_REPLY_EXCEPTION"
        assert "gateway_success=false" in str(completed.get("detail") or "")
        assert completed_payload["gateway_reason_code"] == "GATEWAY_REPLY_EXCEPTION"
    finally:
        ledger.close()


def test_dispatch_worker_gateway_uses_realign_override_for_final_reply() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        delivered_narratives: list[str] = []

        async def _deliver_capture(**kwargs):
            delivered_narratives.append(str(kwargs.get("narrative") or ""))
            return {
                "success": True,
                "status": "SENT",
                "latency_ms": 12,
                "reply_text_len": len(str(kwargs.get("narrative") or "")),
                "reply_preview": str(kwargs.get("narrative") or ""),
                "reply_form": "text",
                "reply_form_reason": "text_only",
                "voice_sent": False,
            }

        with patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "我是夏目蓝，也是国语老师。有什么可以帮你的吗？",
                    "route_decision": {"route": "chat", "target_id": "chat_agent"},
                }
            ),
        ), patch(
            "host_brain.app.input_routes._maybe_realign_gateway_pipeline",
            new=AsyncMock(
                return_value=(
                    {
                        "decision": "APPROVE",
                        "reason_code": None,
                        "narrative": "我是夏目蓝。你想问什么。",
                        "route_decision": {"route": "chat", "target_id": "chat_agent"},
                    },
                    {
                        "triggered": True,
                        "retry_applied": True,
                        "resolved_decision": "APPROVE",
                        "resolved_reason_code": None,
                        "resolved_narrative": "我是夏目蓝。你想问什么。",
                        "resolved_route_decision": {"route": "chat", "target_id": "chat_agent"},
                    },
                )
            ),
        ), patch(
            "host_brain.app.input_routes._deliver_gateway_reply",
            new=AsyncMock(side_effect=_deliver_capture),
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_realign_override_001",
                    session_id="qq_dm_realign_001",
                    source="GATEWAY",
                    text="你是谁",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_realign_001",
                        "msg_id": "msg_realign_001",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        assert delivered_narratives == ["我是夏目蓝。你想问什么。"]
        completed = next(row for row in ledger.voice_event_log if row["event_type"] == "INPUT_COMPLETED")
        payload = dict(completed.get("payload") or {})
        assert payload.get("deviation_retry_applied") is True
        assert payload.get("reply_preview") == "我是夏目蓝。你想问什么。"
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


def test_dispatch_worker_persists_gateway_voice_result_in_completed_payload() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        with patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "这次我把语音和动作都接上了。",
                }
            ),
        ), patch(
            "host_brain.app.input_routes._deliver_gateway_reply",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "status": "OK",
                    "reason_code": None,
                    "detail": "gateway ok",
                    "latency_ms": 30,
                    "reply_text_len": 14,
                    "reply_preview": "preview",
                    "reply_form": "text",
                    "reply_form_reason": "force_dual_voice_reply",
                    "voice_sent": True,
                    "voice_waited": True,
                    "voice_success": True,
                    "voice_status": "ACCEPTED",
                    "voice_reason_code": None,
                    "voice_detail": "tts queued; tts playback started",
                    "voice_latency_ms": 320,
                    "voice_started": True,
                    "voice_vts_started": True,
                    "voice_vts_expression_success": True,
                    "voice_event": {"event_type": "TTS_PLAYBACK_STARTED"},
                }
            ),
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_voice_payload_001",
                    session_id="qq_dm_voice_payload_001",
                    source="GATEWAY",
                    text="请继续处理",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_voice_payload_001",
                        "msg_id": "msg_voice_payload_001",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        completed = next(row for row in ledger.voice_event_log if row["event_type"] == "INPUT_COMPLETED")
        payload = dict(completed.get("payload") or {})
        assert payload["gateway_voice_success"] is True
        assert payload["gateway_voice_started"] is True
        assert payload["gateway_voice_vts_started"] is True
        assert payload["gateway_voice_event"]["event_type"] == "TTS_PLAYBACK_STARTED"
    finally:
        ledger.close()


def test_dispatch_worker_passes_tts_text_ja_to_gateway_reply() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        mocked_deliver = AsyncMock(
            return_value={"success": True, "status": "OK", "latency_ms": 30, "reply_text_len": 14}
        )
        with patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "我记住了，你明天上午九点有个会。",
                    "commit_event": {
                        "phase": "COMMIT",
                        "decision": "APPROVE",
                        "audit_refs": {
                            "emotion_tag": "NEUTRAL",
                            "tts_text_ja": "明日の朝九時に会議があること、ちゃんと覚えてるよ。",
                        },
                    },
                }
            ),
        ), patch(
            "host_brain.app.input_routes._deliver_gateway_reply",
            new=mocked_deliver,
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_tts_text_ja",
                    session_id="qq_dm_tts_text_ja",
                    source="GATEWAY",
                    text="请记住：我明天上午 9 点开会。",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_tts_text_ja",
                        "msg_id": "msg_tts_text_ja",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        mocked_deliver.assert_awaited_once()
        assert mocked_deliver.await_args.kwargs["tts_text_ja"] == "明日の朝九時に会議があること、ちゃんと覚えてるよ。"
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
                        "route": "skill",
                        "target_id": "codex_worker",
                        "task": "修复测试",
                        "reason_code": "LLM_ROUTE_codex_worker",
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
        assert route_payload["route_type"] == "skill"
        assert route_payload["route_target"] == "codex_worker"
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
                        "route": "skill",
                        "target_id": "codex_worker",
                        "task": "fix code",
                        "reason_code": "LLM_ROUTE_codex_worker",
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


def test_run_cortex_orchestrator_pipeline_preserves_gateway_multimodal_fields() -> None:
    captured_request_bodies: list[ChatRequest] = []

    async def _fake_cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        captured_request_bodies.append(kwargs["request_body"])
        yield 'data: {"phase":"COMMIT","decision":"APPROVE","reason_code":null,"narrative":"收到啦。"}'

    payload = {
        "platform": "qq",
        "conversation_id": "qq_dm_mm_contract_001",
        "from_id": "3414578517",
        "msg_type": "image",
        "attachments": [{"type": "image", "attachment_id": "att_img_contract"}],
        "multimodal_observations": {
            "primary_modality": "image",
            "social_signal": "轻轻撒娇",
        },
        "attachment_summary": "用户发来一张轻轻撒娇的图片。",
    }

    with patch(
        "host_brain.app.cortex_orchestrator.cortex_orchestrator_sse_generator",
        new=_fake_cortex_orchestrator_sse_generator,
    ):
        result = _run(
            _run_cortex_orchestrator_pipeline(
                request_id="req_mm_contract",
                session_id="qq_dm_mm_contract_001",
                source="GATEWAY",
                text="[图片消息]",
                payload=payload,
                ts_ms=123456,
                intent_draft_override={"route": "chat", "target_id": "chat_agent"},
            )
        )

    assert result["decision"] == "APPROVE"
    assert captured_request_bodies
    request_body = captured_request_bodies[0]
    assert request_body.input_source == "GATEWAY"
    assert request_body.reply_channel == "qq"
    assert request_body.reply_target == "qq_dm_mm_contract_001"
    assert request_body.render_target_lang == "zh"
    assert request_body.attachments == payload["attachments"]
    assert request_body.multimodal_observations == payload["multimodal_observations"]
    assert request_body.attachment_summary == "用户发来一张轻轻撒娇的图片。"


def test_run_cortex_orchestrator_pipeline_builds_canonical_thalamus_draft_for_gateway(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CORTEX_INTENT_LLM_ENABLED", "false")
    captured_intent_drafts: list[dict[str, object]] = []

    async def _fake_cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        captured_intent_drafts.append(dict(kwargs["intent_draft"]))
        yield 'data: {"phase":"COMMIT","decision":"APPROVE","reason_code":null,"narrative":"我去看主链。"}'

    with patch(
        "host_brain.app.cortex_orchestrator.cortex_orchestrator_sse_generator",
        new=_fake_cortex_orchestrator_sse_generator,
    ):
        result = _run(
            _run_cortex_orchestrator_pipeline(
                request_id="req_gateway_thalamus_canonical",
                session_id="qq_dm_thalamus_canonical",
                source="GATEWAY",
                text="查看完整主链",
                payload={
                    "platform": "qq",
                    "conversation_id": "qq_dm_thalamus_canonical",
                    "from_id": "3414578517",
                },
                ts_ms=123456,
            )
        )

    assert captured_intent_drafts
    intent_draft = captured_intent_drafts[0]
    assert intent_draft["schema_version"] == "Natsume.intent.v1"
    assert intent_draft["route"] == "skill"
    assert intent_draft["target_id"] == "codex_inspect"
    assert intent_draft["route_source"] == "thalamus.rule"
    assert result["route_decision"]["route"] == "skill"
    assert result["route_decision"]["target_id"] == "codex_inspect"


def test_run_cortex_orchestrator_pipeline_routes_gateway_samples_through_thalamus(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CORTEX_INTENT_LLM_ENABLED", "false")
    captured_intent_drafts: list[dict[str, object]] = []

    async def _fake_cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        captured_intent_drafts.append(dict(kwargs["intent_draft"]))
        yield 'data: {"phase":"COMMIT","decision":"APPROVE","reason_code":null,"narrative":"收到。"}'

    cases = [
        ("开始吧", "delegate", "intent_agent"),
        ("使用无头浏览器验证前端效果", "skill", "codex_worker"),
        ("帮我翻译这段话", "delegate", "translate_agent"),
        ("有没有更好的方案", "chat", ""),
        ("帮我安装一下这首歌", "chat", ""),
    ]

    with patch(
        "host_brain.app.cortex_orchestrator.cortex_orchestrator_sse_generator",
        new=_fake_cortex_orchestrator_sse_generator,
    ):
        for index, (text, expected_route, expected_target) in enumerate(cases, start=1):
            result = _run(
                _run_cortex_orchestrator_pipeline(
                    request_id=f"req_gateway_thalamus_matrix_{index}",
                    session_id=f"qq_dm_thalamus_matrix_{index}",
                    source="GATEWAY",
                    text=text,
                    payload={"platform": "qq", "conversation_id": f"qq_dm_thalamus_matrix_{index}"},
                    ts_ms=123456 + index,
                )
            )
            assert result["route_decision"]["route"] == expected_route
            assert result["route_decision"]["target_id"] == expected_target

    assert len(captured_intent_drafts) == len(cases)
    assert all(intent_draft["schema_version"] == "Natsume.intent.v1" for intent_draft in captured_intent_drafts)


def test_dispatch_worker_passes_canonical_thalamus_draft_to_gateway_pipeline(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    canonical_intent = {
        "schema_version": "Natsume.intent.v1",
        "route": "skill",
        "target_id": "codex_worker",
        "task": "使用无头浏览器验证前端效果",
        "route_reason": "THALAMUS_RULE_CODEX_WORKSPACE_WORKER",
        "reason_code": "THALAMUS_RULE_CODEX_WORKSPACE_WORKER",
        "route_confidence": 0.94,
        "route_source": "thalamus.rule",
    }
    fake_agent = SimpleNamespace(
        classify_and_compile_intent_draft=AsyncMock(return_value=canonical_intent),
    )

    try:
        with patch("host_brain.app.input_routes.get_thalamus_agent", return_value=fake_agent), patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "开始验证。",
                    "route_decision": {
                        "route": "skill",
                        "target_id": "codex_worker",
                        "task": "使用无头浏览器验证前端效果",
                    },
                    "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
                }
            ),
        ) as pipeline_mock, patch(
            "host_brain.app.input_routes._deliver_gateway_reply",
            new=AsyncMock(return_value={"success": True, "status": "OK", "latency_ms": 12, "reply_text_len": 5}),
        ), patch(
            "host_brain.app.input_routes._maybe_schedule_gateway_route_started_voice_ack",
        ) as route_ack_mock:
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_gateway_worker_canonical",
                    session_id="qq_dm_worker_canonical",
                    source="GATEWAY",
                    text="使用无头浏览器验证前端效果",
                    payload={"platform": "qq", "conversation_id": "qq_dm_worker_canonical"},
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        fake_agent.classify_and_compile_intent_draft.assert_awaited_once()
        pipeline_mock.assert_awaited_once()
        assert pipeline_mock.await_args.kwargs["intent_draft_override"] == canonical_intent
        route_ack_mock.assert_called_once()
        assert route_ack_mock.call_args.kwargs["route_type"] == "skill"
        assert route_ack_mock.call_args.kwargs["target_id"] == "codex_worker"
    finally:
        ledger.close()


def test_classify_task_intent_prefers_llm_route_when_rules_miss() -> None:
    with patch(
        "host_brain.tribe.thalamus_agent.classify_task_intent_by_rules",
        return_value=None,
    ), patch(
        "host_brain.tribe.thalamus_agent.classify_task_intent_by_llm",
        new=AsyncMock(
            return_value={
                "route": "skill",
                "target_id": "codex_worker",
                "task": "请修复这个报错",
                "reason_code": "LLM_ROUTE_codex_worker",
                "confidence": 0.93,
                "route_source": "llm",
            }
        ),
    ):
        decision = _run(
            classify_task_intent(
                request_id="req_llm_route_001",
                session_id="qq_dm_llm_001",
                text="请修复这个报错",
            )
        )

    assert decision["route"] == "skill"
    assert decision["target_id"] == "codex_worker"
    assert decision["route_source"] == "llm"
    assert float(decision["confidence"]) == 0.93


def test_classify_task_intent_uses_rule_for_action_prefix() -> None:
    decision = _run(
        classify_task_intent(
            request_id="req_llm_route_002",
            session_id="qq_dm_llm_002",
            text="run diagnostics now",
        )
    )

    assert decision["route"] == "delegate"
    assert decision["target_id"] == "intent_agent"
    assert decision["route_source"] in {"rule", "rule.fastpath"}
    assert decision["reason_code"] == "RULE_ACTION_PREFIX"


def test_classify_task_intent_routes_translate_requests() -> None:
    decision = _run(
        classify_task_intent(
            request_id="req_llm_route_003",
            session_id="qq_dm_llm_003",
            text="请翻译成英文：你好",
        )
    )

    assert decision["route"] == "delegate"
    assert decision["target_id"] == "translate_agent"
    assert decision["route_source"] in {"rule", "rule.fastpath"}
    assert decision["reason_code"] == "RULE_FORCE_TRANSLATE_AGENT"


def test_classify_task_intent_keeps_translate_priority_when_text_contains_bug() -> None:
    decision = _run(
        classify_task_intent(
            request_id="req_llm_route_004",
            session_id="qq_dm_llm_004",
            text="请翻译成英文：今天修了三个bug",
        )
    )

    assert decision["route"] == "delegate"
    assert decision["target_id"] == "translate_agent"
    assert decision["reason_code"] == "RULE_FORCE_TRANSLATE_AGENT"


def test_classify_task_intent_routes_recent_turn_query() -> None:
    decision = _run(
        classify_task_intent(
            request_id="req_llm_route_005",
            session_id="qq_dm_llm_005",
            text="你刚刚说了什么",
        )
    )

    assert decision["route"] == "recent_turn"
    assert decision["target_id"] == ""
    assert decision["route_source"] in {"rule", "rule.fastpath"}
    assert decision["reason_code"] == "RULE_RECENT_TURN"


def test_classify_task_intent_routes_task_progress_query() -> None:
    decision = _run(
        classify_task_intent(
            request_id="req_llm_route_005a",
            session_id="qq_dm_llm_005a",
            text="现在进度怎么样了",
        )
    )

    assert decision["route"] == "task_progress"
    assert decision["target_id"] == ""
    assert decision["route_source"] in {"rule", "rule.fastpath"}
    assert decision["reason_code"] == "RULE_TASK_PROGRESS"


def test_classify_task_intent_routes_memory_store_request() -> None:
    decision = _run(
        classify_task_intent(
            request_id="req_llm_route_006",
            session_id="qq_dm_llm_006",
            text="保存这条记忆：我周五晚上有空",
        )
    )

    assert decision["route"] == "skill"
    assert decision["target_id"] == "memory_store"
    assert decision["route_source"] == "rule"
    assert decision["reason_code"] == "RULE_FORCE_MEMORY_STORE"


def test_build_gateway_reply_text_fallback_for_empty_reject() -> None:
    text = _build_gateway_reply_text(
        decision="REJECT",
        narrative="",
        reason_code="RATE_LIMIT",
    )
    assert text == ""


def test_build_gateway_reply_text_uses_prompt_template_override(monkeypatch) -> None:
    _ = monkeypatch
    assert _build_gateway_reply_text(
        decision="REJECT",
        narrative="",
        reason_code="RATE_LIMIT",
    ) == ""
    assert _build_gateway_reply_text(
        decision="APPROVE",
        narrative="",
        reason_code=None,
    ) == ""


def test_build_gateway_route_started_voice_ack_text_uses_prompt_template_override(monkeypatch) -> None:
    _ = monkeypatch
    assert _build_gateway_route_started_voice_ack_text(route_type="delegate", target_id="translate_agent") == ""
    assert _build_gateway_route_started_voice_ack_text(route_type="delegate", target_id="intent_agent") == ""
    assert _build_gateway_route_started_voice_ack_text(route_type="skill", target_id="codex_worker") == ""
    assert _build_gateway_route_started_voice_ack_text(route_type="plan", target_id="") == ""
    assert _build_gateway_route_started_voice_ack_text(route_type="chat", target_id="") == ""


def test_build_gateway_voice_text_uses_prompt_template_override(monkeypatch) -> None:
    _ = monkeypatch
    assert _build_gateway_voice_text(reply_text="```python\nprint('hi')\n```", tts_text_ja=None) == ""
    assert _build_gateway_voice_text(reply_text="这是中文结果。", tts_text_ja=None) == "这是中文结果。"
    assert _build_gateway_voice_text(reply_text="Result ready", tts_text_ja=None) == ""
    assert _build_gateway_voice_text(reply_text="有结果但没有日语", tts_text_ja="") == "有结果但没有日语"


def test_build_gateway_voice_text_prefers_chinese_reply_over_legacy_tts_text() -> None:
    assert (
        _build_gateway_voice_text(
            reply_text="我记住了，你明天上午九点有个会。",
            tts_text_ja="明日の朝九時に会議があること、ちゃんと覚えてるよ。",
        )
        == "我记住了，你明天上午九点有个会。"
    )


def test_build_gateway_voice_text_falls_back_to_safe_japanese_summary_for_latin_or_code() -> None:
    voice_text = _build_gateway_voice_text(
        reply_text="可以直接这样写：```python\\nfrom collections import Counter\\n```",
        tts_text_ja=None,
    )
    assert voice_text == ""


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


def test_deliver_gateway_reply_skips_no_reply_decision_without_edge_send(monkeypatch) -> None:
    post_gateway_reply_mock = AsyncMock(return_value=SimpleNamespace(success=True))
    monkeypatch.setattr(
        "host_brain.channels.edge_rpc_client.post_gateway_reply",
        post_gateway_reply_mock,
    )
    result = _run(
        _deliver_gateway_reply(
            request_id="req_gateway_reply_no_reply_001",
            payload={"platform": "qq", "conversation_id": "qq_dm_no_reply_001"},
            decision="NO_REPLY",
            reason_code="IMAGE_SOCIAL_NO_SAFE_REPLY",
            narrative="",
            user_msg_type="image",
            response_form="text",
            response_form_reason="image_social_reply",
        )
    )

    assert result["success"] is True
    assert result["status"] == "SKIPPED"
    assert result["reason_code"] == "IMAGE_SOCIAL_NO_SAFE_REPLY"
    assert result["reply_text_len"] == 0
    assert post_gateway_reply_mock.await_count == 0


def test_deliver_gateway_reply_sends_media_envelope_without_text_fallback(monkeypatch, tmp_path) -> None:
    media_path = tmp_path / "awkward.webp"
    media_path.write_bytes(b"fake")
    post_gateway_reply_mock = AsyncMock(return_value=SimpleNamespace(success=True, status="OK", detail="", latency_ms=31))
    monkeypatch.setenv("HOST_INPUT_GATEWAY_AUTO_REPLY", "true")
    monkeypatch.setattr(
        "host_brain.channels.edge_rpc_client.post_gateway_reply",
        post_gateway_reply_mock,
    )

    result = _run(
        _deliver_gateway_reply(
            request_id="req_gateway_reply_media_001",
            payload={"platform": "qq", "conversation_id": "qq_dm_media_001"},
            decision="APPROVE",
            reason_code=None,
            narrative="",
            user_msg_type="emoji",
            response_form="sticker",
            response_form_reason="social_reaction",
            reply_envelope_payload={
                "kind": "final_reply",
                "text": "",
                "response_form": "sticker",
                "source": "reaction_catalog",
                "sendable": True,
                "persistable": True,
                "cacheable": False,
                "degraded": False,
                "media_ref": str(media_path),
                "reaction_id": "awkward-1",
            },
        )
    )

    assert result["success"] is True
    assert result["reply_form"] == "sticker"
    assert result["reply_text_len"] == 0
    assert post_gateway_reply_mock.await_count == 1
    call = post_gateway_reply_mock.await_args.kwargs
    assert call["msg_type"] == "image"
    assert call["content"] == ""
    assert call["media_path"] == str(media_path)
    assert call["raw_extra"]["reply_envelope"]["reaction_id"] == "awkward-1"


def test_deliver_gateway_reply_can_still_emit_local_voice_when_auto_reply_disabled(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INPUT_GATEWAY_AUTO_REPLY", "false")
    with patch(
        "host_brain.channels.edge_rpc_client.post_speak",
        new=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                status="ACCEPTED",
                reason_code=None,
                detail="tts queued; tts playback started",
                latency_ms=287,
                event={
                    "event_type": "TTS_PLAYBACK_STARTED",
                    "vts_talk_result": {"success": True},
                    "vts_expression_result": {"success": True},
                    "vts_mouth_sync": {"started": True, "state": "running"},
                },
            )
        ),
    ) as speak_mock:
        result = _run(
            _deliver_gateway_reply(
                request_id="req_gateway_reply_voice_only_001",
                payload={
                    "platform": "wechat",
                    "conversation_id": "wxid_voice_only_001",
                    "raw_extra": {
                        "force_dual_voice_reply": True,
                        "wait_voice_reply_result": True,
                    },
                },
                decision="APPROVE",
                reason_code=None,
                narrative="我先直接在你这边把结果念出来。",
                emotion_tag="GENTLE",
                user_msg_type="text",
                tts_text_ja="こっちで先に読んであげる。",
            )
        )

    speak_mock.assert_awaited_once()
    voiced_text = speak_mock.await_args.kwargs["text"]
    assert voiced_text == "我先直接在你这边把结果念出来。"
    assert result["success"] is True
    assert result["status"] == "SKIPPED"
    assert result["reason_code"] == "GATEWAY_REPLY_DISABLED"
    assert result["voice_sent"] is True
    assert result["voice_waited"] is True
    assert result["voice_success"] is True
    assert result["voice_started"] is True
    assert result["voice_vts_started"] is True
    assert result["voice_vts_expression_success"] is True


def test_deliver_gateway_reply_force_dual_voice_waits_for_edge_voice_result(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INPUT_GATEWAY_AUTO_REPLY", "true")
    with patch(
        "host_brain.channels.edge_rpc_client.post_gateway_reply",
        new=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                status="SENT",
                reason_code=None,
                detail="text sent",
                latency_ms=24,
            )
        ),
    ), patch(
        "host_brain.channels.edge_rpc_client.post_speak",
        new=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                status="ACCEPTED",
                reason_code=None,
                detail="tts queued; tts playback started",
                latency_ms=320,
                event={
                    "event_type": "TTS_PLAYBACK_STARTED",
                    "vts_talk_result": {"success": True},
                    "vts_expression_result": {"success": True},
                    "vts_mouth_sync": {"started": True, "state": "running"},
                },
            )
        ),
    ) as speak_mock:
        result = _run(
            _deliver_gateway_reply(
                request_id="req_gateway_reply_voice_wait_001",
                payload={
                    "platform": "wechat",
                    "conversation_id": "wxid_demo_001",
                    "raw_extra": {
                        "force_dual_voice_reply": True,
                        "wait_voice_reply_result": True,
                    },
                },
                decision="APPROVE",
                reason_code=None,
                narrative="今天我会直接把结果说给你听。",
                emotion_tag="HAPPY",
                user_msg_type="text",
                tts_text_ja="今日はそのまま声で伝えるね。",
            )
        )

    speak_mock.assert_awaited_once()
    assert speak_mock.await_args.kwargs["text"] == "今天我会直接把结果说给你听。"
    assert result["success"] is True
    assert result["reply_form_reason"] == "force_dual_voice_reply"
    assert result["voice_sent"] is True
    assert result["voice_waited"] is True
    assert result["voice_success"] is True
    assert result["voice_started"] is True
    assert result["voice_vts_started"] is True
    assert result["voice_vts_expression_success"] is True


def test_deliver_gateway_reply_can_still_trigger_local_voice_when_primary_delivery_fails(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INPUT_GATEWAY_AUTO_REPLY", "true")
    with patch(
        "host_brain.channels.edge_rpc_client.post_gateway_reply",
        new=AsyncMock(
            return_value=SimpleNamespace(
                success=False,
                status="FAILED",
                reason_code="WECHAT_SEND_FAILED",
                detail="target not found",
                latency_ms=18,
            )
        ),
    ), patch(
        "host_brain.channels.edge_rpc_client.post_speak",
        new=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                status="ACCEPTED",
                reason_code=None,
                detail="tts queued; tts playback started",
                latency_ms=301,
                event={
                    "event_type": "TTS_PLAYBACK_STARTED",
                    "vts_talk_result": {"success": True},
                    "vts_expression_result": {"success": True},
                    "vts_mouth_sync": {"started": True, "state": "running"},
                },
            )
        ),
    ) as speak_mock:
        result = _run(
            _deliver_gateway_reply(
                request_id="req_gateway_reply_voice_on_failure_001",
                payload={
                    "platform": "wechat",
                    "conversation_id": "wxid_missing_target",
                    "raw_extra": {
                        "force_dual_voice_reply": True,
                        "wait_voice_reply_result": True,
                        "allow_voice_reply_on_primary_failure": True,
                    },
                },
                decision="APPROVE",
                reason_code=None,
                narrative="就算文字发不出去，我也先把声音链路跑起来。",
                emotion_tag="HAPPY",
                user_msg_type="text",
                tts_text_ja="文字が届かなくても、先に声でつなぐね。",
            )
        )

    speak_mock.assert_awaited_once()
    assert speak_mock.await_args.kwargs["text"] == "就算文字发不出去，我也先把声音链路跑起来。"
    assert result["success"] is False
    assert result["reason_code"] == "WECHAT_SEND_FAILED"
    assert result["voice_sent"] is True
    assert result["voice_success"] is True
    assert result["voice_started"] is True
    assert result["voice_vts_started"] is True


def test_deliver_gateway_reply_qq_text_plus_sticker_sends_structured_image(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INPUT_GATEWAY_AUTO_REPLY", "true")
    post_gateway_reply_mock = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="SENT",
            reason_code=None,
            detail="ok",
            latency_ms=12,
        )
    )
    fake_mm = SimpleNamespace(
        enhance_output=lambda **_: SimpleNamespace(
            suggested_sticker="",
        )
    )

    with patch(
        "host_brain.channels.edge_rpc_client.post_gateway_reply",
        new=post_gateway_reply_mock,
    ), patch(
        "host_brain.app.dependencies.get_multimodal_processor",
        return_value=fake_mm,
    ):
        result = _run(
            _deliver_gateway_reply(
                request_id="req_gateway_reply_qq_emoji_text_001",
                payload={
                    "platform": "qq",
                    "conversation_id": "qq_dm_emoji_text_001",
                },
                decision="APPROVE",
                reason_code=None,
                narrative="你好呀！有什么事吗？",
                user_msg_type="text",
                response_form="text_plus_sticker",
                response_form_reason="phatic_greeting",
                reply_envelope_payload={
                    "kind": "final_reply",
                    "text": "你好呀！有什么事吗？",
                    "response_form": "text_plus_sticker",
                    "source": "test",
                    "sendable": True,
                    "persistable": True,
                    "cacheable": False,
                    "raw_extra": {"sticker": "https://example.invalid/sora-wave.webp"},
                },
            )
        )

    assert result["success"] is True
    assert post_gateway_reply_mock.await_count == 2
    primary_call = post_gateway_reply_mock.await_args_list[0].kwargs
    secondary_call = post_gateway_reply_mock.await_args_list[1].kwargs
    assert primary_call["msg_type"] == "text"
    assert primary_call["content"] == "你好呀！有什么事吗？"
    assert secondary_call["msg_type"] == "image"
    assert secondary_call["content"] == "https://example.invalid/sora-wave.webp"


def test_deliver_gateway_reply_text_plus_sticker_without_structured_payload_is_suppressed(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INPUT_GATEWAY_AUTO_REPLY", "true")
    post_gateway_reply_mock = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="SENT",
            reason_code=None,
            detail="ok",
            latency_ms=12,
        )
    )
    fake_mm = SimpleNamespace(
        enhance_output=lambda **_: SimpleNamespace(
            suggested_sticker="",
        )
    )

    with patch(
        "host_brain.channels.edge_rpc_client.post_gateway_reply",
        new=post_gateway_reply_mock,
    ), patch(
        "host_brain.app.dependencies.get_multimodal_processor",
        return_value=fake_mm,
    ):
        result = _run(
            _deliver_gateway_reply(
                request_id="req_gateway_reply_qq_emoji_text_002",
                payload={
                    "platform": "qq",
                    "conversation_id": "qq_dm_emoji_text_002",
                },
                decision="APPROVE",
                reason_code=None,
                narrative="我在呀。你想跟我说什么？",
                user_msg_type="text",
                response_form="text_plus_sticker",
                response_form_reason="phatic_greeting",
            )
        )

    assert result["success"] is True
    assert result["status"] == "SKIPPED"
    assert result["reason_code"] == "INVALID_REPLY_ENVELOPE"
    assert post_gateway_reply_mock.await_count == 0


def test_deliver_gateway_reply_plain_text_phatic_does_not_append_default_emoji(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INPUT_GATEWAY_AUTO_REPLY", "true")
    post_gateway_reply_mock = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="SENT",
            reason_code=None,
            detail="ok",
            latency_ms=12,
        )
    )
    fake_mm = SimpleNamespace(
        enhance_output=lambda **_: SimpleNamespace(
            suggested_sticker="",
        )
    )

    with patch(
        "host_brain.channels.edge_rpc_client.post_gateway_reply",
        new=post_gateway_reply_mock,
    ), patch(
        "host_brain.app.dependencies.get_multimodal_processor",
        return_value=fake_mm,
    ):
        result = _run(
            _deliver_gateway_reply(
                request_id="req_gateway_reply_plain_phatic_no_emoji",
                payload={
                    "platform": "qq",
                    "conversation_id": "qq_dm_plain_phatic_no_emoji",
                },
                decision="APPROVE",
                reason_code=None,
                narrative="哥哥，我在呀，刚刚还想着你是不是又来晃一圈呢。",
                user_msg_type="text",
                response_form="text",
                response_form_reason="phatic_greeting",
            )
        )

    assert result["success"] is True
    assert post_gateway_reply_mock.await_count == 1
    primary_call = post_gateway_reply_mock.await_args_list[0].kwargs
    assert primary_call["msg_type"] == "text"
    assert primary_call["content"] == "哥哥，我在呀，刚刚还想着你是不是又来晃一圈呢。"


def test_deliver_gateway_reply_non_qq_text_plus_sticker_keeps_secondary_send(monkeypatch) -> None:
    monkeypatch.setenv("HOST_INPUT_GATEWAY_AUTO_REPLY", "true")
    post_gateway_reply_mock = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="SENT",
            reason_code=None,
            detail="ok",
            latency_ms=12,
        )
    )
    fake_mm = SimpleNamespace(
        enhance_output=lambda **_: SimpleNamespace(
            suggested_sticker="",
        )
    )

    with patch(
        "host_brain.channels.edge_rpc_client.post_gateway_reply",
        new=post_gateway_reply_mock,
    ), patch(
        "host_brain.app.dependencies.get_multimodal_processor",
        return_value=fake_mm,
    ):
        result = _run(
            _deliver_gateway_reply(
                request_id="req_gateway_reply_wechat_emoji_text_001",
                payload={
                    "platform": "wechat",
                    "conversation_id": "wx_dm_emoji_text_001",
                },
                decision="APPROVE",
                reason_code=None,
                narrative="你好呀！有什么事吗？",
                user_msg_type="text",
                response_form="text_plus_sticker",
                response_form_reason="phatic_greeting",
                reply_envelope_payload={
                    "kind": "final_reply",
                    "text": "你好呀！有什么事吗？",
                    "response_form": "text_plus_sticker",
                    "source": "test",
                    "sendable": True,
                    "persistable": True,
                    "cacheable": False,
                    "raw_extra": {"sticker": "https://example.invalid/sora-wave.webp"},
                },
            )
        )

    assert result["success"] is True
    assert post_gateway_reply_mock.await_count == 2
    primary_call = post_gateway_reply_mock.await_args_list[0].kwargs
    secondary_call = post_gateway_reply_mock.await_args_list[1].kwargs
    assert primary_call["msg_type"] == "text"
    assert primary_call["content"] == "你好呀！有什么事吗？"
    assert secondary_call["msg_type"] == "image"
    assert secondary_call["content"] == "https://example.invalid/sora-wave.webp"
    assert secondary_call["request_id"].endswith("::secondary::image")
    assert secondary_call["wait_result"] is True


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
        completed_payload = dict(completed.get("payload") or {})
        assert completed["status"] == "OK"
        assert "gateway_success=true" in str(completed.get("detail") or "")
        assert "gateway_status=SKIPPED" in str(completed.get("detail") or "")
        assert completed_payload["gateway_reason_code"] == "GATEWAY_REPLY_DISABLED"
    finally:
        ledger.close()


def test_should_send_gateway_immediate_ack_for_action_intent() -> None:
    should_send, reason = _should_send_gateway_immediate_ack(
        source="GATEWAY",
        text="请帮我总结一下今天的执行结果",
        payload={"msg_type": "text"},
        intent_draft={"route": "skill", "target_id": "session_summary"},
    )

    assert should_send is False
    assert reason == "ACK_RETIRED_REPLY_ENVELOPE_ONLY"


def test_should_skip_gateway_immediate_ack_when_route_unresolved() -> None:
    should_send, reason = _should_send_gateway_immediate_ack(
        source="GATEWAY",
        text="请帮我总结一下今天的执行结果",
        payload={"msg_type": "text"},
    )

    assert should_send is False
    assert reason == "ACK_ROUTE_UNRESOLVED"


def test_should_skip_gateway_immediate_ack_for_progress_query() -> None:
    should_send, reason = _should_send_gateway_immediate_ack(
        source="GATEWAY",
        text="进度怎么样了，完成了吗？",
        payload={"msg_type": "text"},
    )

    assert should_send is False
    assert reason == "ACK_PROGRESS_QUERY"


def test_should_skip_gateway_immediate_ack_for_direct_open_app() -> None:
    should_send, reason = _should_send_gateway_immediate_ack(
        source="GATEWAY",
        text="打开酷狗音乐",
        payload={"msg_type": "text"},
    )

    assert should_send is False
    assert reason == "ACK_DIRECT_OPEN_APP"


def test_should_skip_gateway_immediate_ack_for_chat_route_even_if_help_words_present() -> None:
    should_send, reason = _should_send_gateway_immediate_ack(
        source="GATEWAY",
        text="你为什么要帮我，我们很熟吗",
        payload={"msg_type": "text"},
        intent_draft={"route": "chat"},
    )

    assert should_send is False
    assert reason == "ACK_NON_EXECUTION_ROUTE"


def test_should_send_gateway_route_started_voice_ack_for_translate_delegate() -> None:
    should_send, reason = _should_send_gateway_route_started_voice_ack(
        source="GATEWAY",
        payload={"platform": "qq", "conversation_id": "qq_dm_route_ack"},
        route_type="delegate",
        target_id="translate_agent",
    )

    assert should_send is True
    assert reason == "ROUTE_ACK_TRANSLATE_AGENT"


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


def test_dispatch_worker_schedules_route_started_voice_ack_for_long_delegate(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)

    try:
        with patch(
            "host_brain.tribe.thalamus_agent.classify_task_intent_by_rules",
            return_value={
                "route": "delegate",
                "target_id": "translate_agent",
                "task": "translate",
            },
        ), patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "translated",
                    "route_decision": {
                        "route": "delegate",
                        "target_id": "translate_agent",
                        "task": "translate",
                    },
                }
            ),
        ), patch(
            "host_brain.app.input_routes._deliver_gateway_reply",
            new=AsyncMock(return_value={"success": True, "status": "OK", "latency_ms": 12, "reply_text_len": 12}),
        ), patch(
            "host_brain.app.input_routes._maybe_schedule_gateway_route_started_voice_ack",
        ) as route_ack_mock:
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_route_ack",
                    session_id="qq_dm_route_ack",
                    source="GATEWAY",
                    text="帮我翻译这段话",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_route_ack",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="fire_and_forget.inline",
                    run_in_thread=False,
                )
            )

        route_ack_mock.assert_called_once()
        kwargs = route_ack_mock.call_args.kwargs
        assert kwargs["route_type"] == "delegate"
        assert kwargs["target_id"] == "translate_agent"
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

        assert handled is False
        post_mock.assert_not_awaited()
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
        intent_draft={"route": "skill", "target_id": "codex_worker"},
    )

    assert should_start is False
    assert reason == "PROGRESS_PUSH_RETIRED_REPLY_ENVELOPE_ONLY"


def test_should_skip_gateway_progress_push_when_route_unresolved() -> None:
    should_start, reason = _should_start_gateway_progress_push(
        source="GATEWAY",
        text="请帮我跑一下今天的全量测试并汇总结果",
        payload={"platform": "qq", "conversation_id": "qq_dm_4005"},
    )

    assert should_start is False
    assert reason == "PROGRESS_PUSH_RETIRED_REPLY_ENVELOPE_ONLY"


def test_should_skip_gateway_progress_push_for_progress_query() -> None:
    should_start, reason = _should_start_gateway_progress_push(
        source="GATEWAY",
        text="现在进度怎么样了",
        payload={"platform": "qq", "conversation_id": "qq_dm_4002"},
    )

    assert should_start is False
    assert reason == "PROGRESS_PUSH_RETIRED_REPLY_ENVELOPE_ONLY"


def test_should_skip_gateway_progress_push_for_chat_route_even_if_help_words_present() -> None:
    should_start, reason = _should_start_gateway_progress_push(
        source="GATEWAY",
        text="你为什么要帮我，我们很熟吗",
        payload={"platform": "qq", "conversation_id": "qq_dm_4004"},
        intent_draft={"route": "chat"},
    )

    assert should_start is False
    assert reason == "PROGRESS_PUSH_RETIRED_REPLY_ENVELOPE_ONLY"


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
        assert_natsume_style(
            user_message="请执行任务",
            reply=str(kwargs["content"]),
            emotion_tag="neutral",
            bond_stage="FRIEND",
        )
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
        assert meta["resolved_decision"] == "APPROVE"
        assert "卡顿原因" in str(meta.get("resolved_narrative") or "")
        assert "卡顿原因" in str(result.get("narrative") or "")
        assert_natsume_style(
            user_message="请帮我总结前端卡顿原因并给出修复方案",
            reply=str(result.get("narrative") or ""),
            emotion_tag="neutral",
            bond_stage="FRIEND",
        )
    finally:
        ledger.close()


def test_maybe_realign_gateway_pipeline_skips_for_codex_worker_skill(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        monkeypatch.setenv("HOST_GATEWAY_DEVIATION_GUARD_ENABLED", "true")
        with patch(
            "host_brain.app.input_routes._maybe_send_gateway_realign_notice",
            new=AsyncMock(return_value={"sent": True, "status": "SENT"}),
        ) as notice_mock, patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(),
        ) as rerun_mock:
            result, meta = _run(
                _maybe_realign_gateway_pipeline(
                    request_id="req_codegen_skip_001",
                    session_id="wechat_dm_9001",
                    source="GATEWAY",
                    user_text="写一个 Python 函数，输入数组后去重并按出现频次降序返回。",
                    payload={
                        "platform": "wechat",
                        "conversation_id": "wechat_dm_9001",
                        "sender_name": "测试用户",
                    },
                    ts_ms=123456,
                    pipeline_result={
                        "decision": "APPROVE",
                        "reason_code": None,
                        "narrative": "from collections import Counter\n\ndef sort_by_frequency(arr):\n    return []",
                        "route_decision": {"route": "skill", "target_id": "codex_worker"},
                        "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
                    },
                    ledger=ledger,
                )
            )

        assert result["route_decision"]["target_id"] == "codex_worker"
        assert meta is not None
        assert meta["triggered"] is False
        assert meta["reason_code"] == "DEVIATION_SKIPPED_codex_runtime"
        notice_mock.assert_not_awaited()
        rerun_mock.assert_not_awaited()
    finally:
        ledger.close()


def test_maybe_realign_gateway_pipeline_skips_for_translate_delegate(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        monkeypatch.setenv("HOST_GATEWAY_DEVIATION_GUARD_ENABLED", "true")
        with patch(
            "host_brain.app.input_routes._maybe_send_gateway_realign_notice",
            new=AsyncMock(return_value={"sent": True, "status": "SENT"}),
        ) as notice_mock, patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(),
        ) as rerun_mock:
            result, meta = _run(
                _maybe_realign_gateway_pipeline(
                    request_id="req_translate_skip_001",
                    session_id="wechat_dm_9002",
                    source="GATEWAY",
                    user_text="把“今天辛苦了，早点休息”翻译成英文。",
                    payload={
                        "platform": "wechat",
                        "conversation_id": "wechat_dm_9002",
                        "sender_name": "测试用户",
                    },
                    ts_ms=123456,
                    pipeline_result={
                        "decision": "APPROVE",
                        "reason_code": None,
                        "narrative": "You've worked hard today. Get some rest early.",
                        "route_decision": {"route": "delegate", "target_id": "translate_agent"},
                        "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
                    },
                    ledger=ledger,
                )
            )

        assert result["route_decision"]["target_id"] == "translate_agent"
        assert meta is not None
        assert meta["triggered"] is False
        assert meta["reason_code"] == "DEVIATION_SKIPPED_TRANSLATE_AGENT"
        notice_mock.assert_not_awaited()
        rerun_mock.assert_not_awaited()
    finally:
        ledger.close()


def test_maybe_realign_gateway_pipeline_skips_for_chat_route(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        monkeypatch.setenv("HOST_GATEWAY_DEVIATION_GUARD_ENABLED", "true")
        with patch(
            "host_brain.app.input_routes._maybe_send_gateway_realign_notice",
            new=AsyncMock(return_value={"sent": True, "status": "SENT"}),
        ) as notice_mock, patch(
            "host_brain.app.input_routes._run_cortex_orchestrator_pipeline",
            new=AsyncMock(),
        ) as rerun_mock:
            result, meta = _run(
                _maybe_realign_gateway_pipeline(
                    request_id="req_chat_skip_001",
                    session_id="qq_dm_chat_skip",
                    source="GATEWAY",
                    user_text="你为什么要帮我，我们很熟吗",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_chat_skip",
                        "sender_name": "测试用户",
                    },
                    ts_ms=123456,
                    pipeline_result={
                        "decision": "APPROVE",
                        "reason_code": None,
                        "narrative": "因为你问了，我才回答。",
                        "route_decision": {"route": "chat"},
                        "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
                    },
                    ledger=ledger,
                )
            )

        assert result["route_decision"]["route"] == "chat"
        assert meta is not None
        assert meta["triggered"] is False
        assert meta["reason_code"] == "DEVIATION_NON_EXECUTION_ROUTE"
        notice_mock.assert_not_awaited()
        rerun_mock.assert_not_awaited()
    finally:
        ledger.close()
