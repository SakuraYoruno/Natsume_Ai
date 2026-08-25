from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.input_runtime.contracts_and_normalization import (
    _detect_gateway_execution_deviation,
)
from host_brain.input_runtime.gateway_delivery import _deliver_gateway_reply
from host_brain.input_runtime.worker_dispatch import (
    _dispatch_to_graph,
    _dispatch_to_graph_worker,
)
from host_brain.input_runtime.cortex_dispatch import (
    _maybe_realign_gateway_pipeline,
    _prepare_input_affect_handoff,
    _run_cortex_orchestrator_pipeline,
)
from host_brain.app.schemas import ChatRequest
from host_brain.app.input_runtime_composition import build_input_runtime_application_ports
from host_brain.input_runtime.settings import get_input_settings, input_settings_from_mapping
from host_brain.tribe.thalamus_agent import ThalamusAgent, classify_task_intent
from host_brain.tribe.tribe_state import TribeStateRegistry
from tests.text_quality_assertions import assert_natsume_style


def _run(coro):
    return asyncio.run(coro)


async def _approve_governor(**_kwargs):  # noqa: ANN003
    return (
        {
            "decision": "APPROVE",
            "reason_code": None,
            "committed_route_token_ref": "GOV_OK_RENDER",
            "committed_route_token": {"token": "GOV_OK_RENDER"},
        },
        {},
    )


def _test_canonical_emotion_context(*, request_id: str, **_kwargs):  # noqa: ANN003
    """Provide an explicit read-only canonical hand-off for route tests.

    These tests exercise input routing rather than Affective Life persistence.
    They must inject an emotion port instead of relying on a composer-created
    HostEmotionRuntime fallback.
    """

    return {
        "mood_state": "steady",
        "emotion_tag": "NEUTRAL",
        "emotion_intensity": 0.5,
        "emotion_ts_ms": 123456,
        "emotion_inference_source": "test_canonical_affect_port",
        "request_id": str(request_id),
    }


def _ports(**overrides):  # noqa: ANN003
    overrides.setdefault("get_intent_engine", lambda: object())
    overrides.setdefault(
        "get_multimodal_processor",
        lambda: SimpleNamespace(
            enhance_output=lambda **_: SimpleNamespace(suggested_sticker=""),
        ),
    )
    overrides.setdefault("evaluate_governor_with_timing", _approve_governor)
    overrides.setdefault(
        "resolve_chat_emotion_context",
        _test_canonical_emotion_context,
    )
    thalamus = ThalamusAgent(state_registry=TribeStateRegistry())
    overrides.setdefault("get_thalamus_agent", lambda: thalamus)
    return replace(build_input_runtime_application_ports(), **overrides)


@pytest.fixture(autouse=True)
def _reset_input_settings_cache():
    get_input_settings.cache_clear()
    yield
    get_input_settings.cache_clear()


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
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "hello from top agent",
                }
            ),
        ), patch(
            "host_brain.input_runtime.worker_dispatch._deliver_gateway_reply",
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
                        ports=_ports(),
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


def test_dispatch_worker_never_redelivers_a_governed_acceptance_receipt() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    mocked_deliver = AsyncMock()
    try:
        with patch(
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "accepted once",
                    "commit_event": {
                        "phase": "COMMIT",
                        "done": True,
                        "decision": "APPROVE",
                        "governed": True,
                        "execution_receipt": {
                            "graph_id": "graph-governed-delivery",
                            "graph_revision": 1,
                            "status": "ACCEPTED",
                            "accepted": True,
                        },
                        "audit_refs": {
                            "acceptance_effects": {
                                "count": 3,
                                "committed": 3,
                                "failed": 0,
                                "pending": 0,
                                "delivery_committed": True,
                            }
                        },
                    },
                }
            ),
        ), patch(
            "host_brain.input_runtime.worker_dispatch._deliver_gateway_reply",
            new=mocked_deliver,
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_governed_delivery_once",
                    session_id="qq_dm_governed_delivery_once",
                    source="GATEWAY",
                    text="hi",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_governed_delivery_once",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                    dispatch_mode="governed.awaited",
                    ports=_ports(),
                )
            )

        mocked_deliver.assert_not_awaited()
        completed = next(
            row for row in ledger.voice_event_log if row["event_type"] == "INPUT_COMPLETED"
        )
        assert "gateway_delivery_authority=acceptance_effect_outbox" in str(
            completed.get("detail") or ""
        )
        completed_payload = dict(completed.get("payload") or {})
        assert completed_payload["gateway_delivery_authority"] == "acceptance_effect_outbox"
        assert completed_payload["gateway_delivery_state"] == "OUTBOX_COMMITTED"
    finally:
        ledger.close()


def test_dispatch_worker_gateway_queued_reply_stays_ok() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        with patch(
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "hello from top agent",
                }
            ),
        ), patch(
            "host_brain.input_runtime.worker_dispatch._deliver_gateway_reply",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "status": "queued",
                    "reason_code": None,
                    "detail": "outbound queued",
                    "latency_ms": 23,
                    "reply_text_len": 20,
                    "reply_preview": "hello from top agent",
                    "reply_form": "text",
                    "reply_form_reason": "text_only",
                    "voice_sent": False,
                }
            ),
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_queued",
                    session_id="qq_dm_queued",
                    source="GATEWAY",
                    text="hi",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_queued",
                        "msg_id": "msg_queued",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                        dispatch_mode="fire_and_forget.inline",
                        ports=_ports(),
                )
            )

        completed = next(row for row in ledger.voice_event_log if row["event_type"] == "INPUT_COMPLETED")
        completed_payload = dict(completed.get("payload") or {})
        detail = str(completed.get("detail") or "")
        assert completed["status"] == "OK"
        assert completed.get("reason_code") is None
        assert "gateway_success=true" in detail
        assert "gateway_status=queued" in detail
        assert "GATEWAY_REPLY_FAILED" not in detail
        assert completed_payload["gateway_success"] is True
        assert completed_payload["gateway_status"] == "queued"
        assert completed_payload.get("gateway_reason_code") is None
    finally:
        ledger.close()


def test_dispatch_worker_gateway_reply_failure_logs_completed_degraded(monkeypatch) -> None:
    monkeypatch.setenv("HOST_GATEWAY_REPLY_FAILURE_DEGRADE_INPUT", "true")
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        with patch(
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "hello from top agent",
                }
            ),
            ), patch(
                "host_brain.input_runtime.worker_dispatch._deliver_gateway_reply",
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
                        ports=_ports(),
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
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "我是夏目蓝，也是国语老师。有什么可以帮你的吗？",
                    "route_decision": {"route": "chat", "target_id": "surface_reply"},
                }
            ),
        ), patch(
            "host_brain.input_runtime.worker_dispatch._maybe_realign_gateway_pipeline",
            new=AsyncMock(
                return_value=(
                    {
                        "decision": "APPROVE",
                        "reason_code": None,
                        "narrative": "我是夏目蓝。你想问什么。",
                        "route_decision": {"route": "chat", "target_id": "surface_reply"},
                    },
                    {
                        "triggered": True,
                        "retry_applied": True,
                        "resolved_decision": "APPROVE",
                        "resolved_reason_code": None,
                        "resolved_narrative": "我是夏目蓝。你想问什么。",
                        "resolved_route_decision": {"route": "chat", "target_id": "surface_reply"},
                    },
                )
            ),
        ), patch(
            "host_brain.input_runtime.worker_dispatch._deliver_gateway_reply",
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
                        ports=_ports(),
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
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "hello from top agent",
                }
            ),
        ), patch(
            "host_brain.input_runtime.worker_dispatch._deliver_gateway_reply",
            new=AsyncMock(side_effect=RuntimeError("reply failed")),
        ), patch(
            "host_brain.input_runtime.worker_dispatch.get_input_settings",
            return_value=input_settings_from_mapping(
                {"HOST_GATEWAY_REPLY_FAILURE_DEGRADE_INPUT": "false"}
            ),
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
                        ports=_ports(),
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
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "已完成",
                    "route_decision": {
                        "route": "skill",
                        "target_id": "agent_worker",
                        "task": "修复测试",
                        "reason_code": "LLM_ROUTE_agent_worker",
                        "source": "llm",
                        "confidence": 0.91,
                        "override_reason": None,
                        "llm_route_used": True,
                    },
                }
            ),
        ), patch(
            "host_brain.input_runtime.worker_dispatch._deliver_gateway_reply",
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
                        ports=_ports(),
                )
            )

        rows = ledger.voice_event_log
        route_row = next(row for row in rows if row["event_type"] == "INPUT_ROUTE_DECIDED")
        route_payload = dict(route_row.get("payload") or {})
        assert route_row["status"] == "OK"
        assert route_payload["route_type"] == "skill"
        assert route_payload["route_target"] == "agent_worker"
        assert route_payload["route_source"] == "llm"
        assert float(route_payload["route_confidence"]) == 0.91
        assert route_payload["llm_route_used"] is True

        completed_row = next(row for row in rows if row["event_type"] == "INPUT_COMPLETED")
        completed_payload = dict(completed_row.get("payload") or {})
        assert completed_payload["llm_route_used"] is True
    finally:
        ledger.close()


def test_dispatch_worker_accepts_delegate_without_legacy_skill_exec() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        with patch(
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "translation done",
                    "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
                    "route_decision": {
                        "route": "delegate",
                        "target_id": "translate_agent",
                        "task": "translate text",
                        "reason_code": "LLM_ROUTE_translate_agent",
                        "source": "llm",
                        "confidence": 0.95,
                        "llm_route_used": True,
                    },
                }
            ),
        ), patch(
            "host_brain.input_runtime.worker_dispatch._deliver_gateway_reply",
            new=AsyncMock(return_value={"success": True, "status": "OK", "latency_ms": 15, "reply_text_len": 12}),
        ):
            _run(
                _dispatch_to_graph_worker(
                    request_id="req_worker_quality_missing_exec",
                    session_id="qq_dm_quality_missing_exec",
                    source="GATEWAY",
                    text="please translate this text",
                    payload={
                        "platform": "qq",
                        "conversation_id": "qq_dm_quality_missing_exec",
                        "msg_id": "msg_quality_missing_exec",
                    },
                    ts_ms=123456,
                    ledger=ledger,
                        dispatch_mode="fire_and_forget.inline",
                        ports=_ports(),
                )
            )

        completed = next(row for row in ledger.voice_event_log if row["event_type"] == "INPUT_COMPLETED")
        assert completed["status"] == "OK"
        assert completed["reason_code"] is None
        completed_payload = dict(completed.get("payload") or {})
        assert completed_payload.get("exec_quality_degraded") is False
        assert completed_payload.get("exec_quality_reason_code") is None
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
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
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
            "host_brain.input_runtime.worker_dispatch._deliver_gateway_reply",
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
                        ports=_ports(),
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
                intent_draft_override={"route": "chat", "target_id": "surface_reply"},
                ports=_ports(
                    cortex_orchestrator_sse_generator=_fake_cortex_orchestrator_sse_generator
                ),
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


def test_asr_text_inference_cannot_become_user_self_report() -> None:
    tag, intensity, multimodal, context = _prepare_input_affect_handoff(
        source="ASR",
        ts_ms=123_456,
        payload={
            "emotion_tag": "SAD",
            "emotion_intensity": 0.96,
            "emotion_source": "asr_text_bootstrap",
            "emotion_modality": "asr_text",
            "emotion_confidence": 0.96,
            "acoustic_emotion_available": False,
            "emotion_context": {
                "emotion_tag": "HAPPY",
                "user_affect_observations": [
                    {"modality": "SELF_REPORT", "emotion_tag": "HAPPY"}
                ],
            },
            "multimodal_observations": {
                "primary_modality": "voice",
                "user_affect_observations": [
                    {
                        "modality": "SELF_REPORT",
                        "emotion_tag": "HAPPY",
                        "raw_audio": "must-not-cross-the-host-boundary",
                    }
                ],
            },
        },
    )

    assert tag is None
    assert intensity is None
    assert context is None
    assert multimodal == {"primary_modality": "voice"}


def test_asr_acoustic_attestation_reaches_chat_as_typed_observation_only() -> None:
    captured_request_bodies: list[ChatRequest] = []

    async def _fake_cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        captured_request_bodies.append(kwargs["request_body"])
        yield 'data: {"phase":"COMMIT","decision":"APPROVE","reason_code":null,"narrative":"我在听。"}'

    result = _run(
        _run_cortex_orchestrator_pipeline(
            request_id="req-asr-acoustic-observation",
            session_id="session-asr-acoustic-observation",
            source="ASR",
            text="我有点撑不住了。",
            payload={
                "emotion_tag": "SAD",
                "emotion_intensity": 0.76,
                "emotion_source": "acoustic_model_v2",
                "emotion_modality": "acoustic",
                "emotion_confidence": 0.83,
                "acoustic_emotion_available": True,
                "emotion_inference_degraded": False,
                "emotion_context": {"emotion_tag": "HAPPY"},
                "multimodal_observations": {
                    "primary_modality": "voice",
                    "user_affect_observations": [
                        {"modality": "SELF_REPORT", "emotion_tag": "HAPPY"}
                    ],
                },
            },
            ts_ms=123_456,
            intent_draft_override={"route": "chat", "target_id": "surface_reply"},
            ports=_ports(
                cortex_orchestrator_sse_generator=_fake_cortex_orchestrator_sse_generator
            ),
        )
    )

    assert result["decision"] == "APPROVE"
    assert len(captured_request_bodies) == 1
    request_body = captured_request_bodies[0]
    assert request_body.emotion_tag is None
    assert request_body.emotion_intensity is None
    assert request_body.emotion_context is None
    observation = request_body.multimodal_observations["user_affect_observations"][0]
    assert observation == {
        "modality": "ACOUSTIC",
        "emotion_distribution": {"SAD": 0.76},
        "confidence": 0.83,
        "observed_at_ms": 123_456,
        "expires_at_ms": 128_456,
        "provider": "edge-acoustic:acoustic_model_v2",
    }


def test_run_cortex_orchestrator_pipeline_builds_canonical_thalamus_draft_for_gateway() -> None:
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
                ports=_ports(
                    cortex_orchestrator_sse_generator=_fake_cortex_orchestrator_sse_generator
                ),
            )
        )

    assert captured_intent_drafts
    intent_draft = captured_intent_drafts[0]
    assert intent_draft["schema_version"] == "Natsume.intent.v1"
    assert intent_draft["route"] == "skill"
    assert intent_draft["target_id"] == "agent_inspect"
    assert intent_draft["route_source"] == "thalamus.rule"
    assert result["route_decision"]["route"] == "skill"
    assert result["route_decision"]["target_id"] == "agent_inspect"


def test_run_cortex_orchestrator_pipeline_routes_gateway_samples_through_thalamus() -> None:
    captured_intent_drafts: list[dict[str, object]] = []

    async def _fake_cortex_orchestrator_sse_generator(**kwargs):  # noqa: ANN003
        captured_intent_drafts.append(dict(kwargs["intent_draft"]))
        yield 'data: {"phase":"COMMIT","decision":"APPROVE","reason_code":null,"narrative":"收到。"}'

    cases = [
        ("开始吧", "chat", ""),
        ("使用无头浏览器验证前端效果", "skill", "agent_frontend_acceptance"),
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
                    ports=_ports(
                        cortex_orchestrator_sse_generator=_fake_cortex_orchestrator_sse_generator
                    ),
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
        "target_id": "agent_worker",
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
        with patch(
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
            new=AsyncMock(
                return_value={
                    "decision": "APPROVE",
                    "reason_code": None,
                    "narrative": "开始验证。",
                    "route_decision": {
                        "route": "skill",
                        "target_id": "agent_worker",
                        "task": "使用无头浏览器验证前端效果",
                    },
                    "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
                }
            ),
        ) as pipeline_mock, patch(
            "host_brain.input_runtime.worker_dispatch._deliver_gateway_reply",
            new=AsyncMock(return_value={"success": True, "status": "OK", "latency_ms": 12, "reply_text_len": 5}),
        ):
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
                        ports=_ports(get_thalamus_agent=lambda: fake_agent),
                )
            )

        fake_agent.classify_and_compile_intent_draft.assert_awaited_once()
        pipeline_mock.assert_awaited_once()
        assert pipeline_mock.await_args.kwargs["intent_draft_override"] == canonical_intent
    finally:
        ledger.close()


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
            ports=_ports(),
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
        "host_brain.channels.edge_transport.submit_gateway_reply",
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
            ports=_ports(),
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
        "host_brain.channels.edge_transport.submit_gateway_reply",
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
            ports=_ports(),
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
        "host_brain.channels.edge_transport.submit_gateway_reply",
        new=post_gateway_reply_mock,
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
                ports=_ports(get_multimodal_processor=lambda: fake_mm),
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
        "host_brain.channels.edge_transport.submit_gateway_reply",
        new=post_gateway_reply_mock,
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
                ports=_ports(get_multimodal_processor=lambda: fake_mm),
            )
        )

    assert result["success"] is True
    assert result["status"] == "SENT"
    assert post_gateway_reply_mock.await_count == 1


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
        "host_brain.channels.edge_transport.submit_gateway_reply",
        new=post_gateway_reply_mock,
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
                ports=_ports(
                    get_multimodal_processor=lambda: fake_mm,
                ),
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
        "host_brain.channels.edge_transport.submit_gateway_reply",
        new=post_gateway_reply_mock,
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
                ports=_ports(get_multimodal_processor=lambda: fake_mm),
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
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
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
                        ports=_ports(),
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


def test_dispatch_worker_keeps_delegate_reply_on_normal_delivery_path() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    delivery_mock = AsyncMock(
        return_value={"success": True, "status": "OK", "latency_ms": 12, "reply_text_len": 12}
    )

    try:
        with patch(
            "host_brain.tribe.thalamus_agent.classify_task_intent_by_rules",
            return_value={
                "route": "delegate",
                "target_id": "translate_agent",
                "task": "translate",
            },
        ), patch(
            "host_brain.input_runtime.worker_dispatch._run_cortex_orchestrator_pipeline",
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
            "host_brain.input_runtime.worker_dispatch._deliver_gateway_reply",
            new=delivery_mock,
        ):
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
                    ports=_ports(),
                )
            )

        delivery_mock.assert_awaited_once()
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
            "host_brain.input_runtime.cortex_dispatch._run_cortex_orchestrator_pipeline",
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
                    ports=_ports(),
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


def test_maybe_realign_gateway_pipeline_skips_for_agent_worker_skill(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        monkeypatch.setenv("HOST_GATEWAY_DEVIATION_GUARD_ENABLED", "true")
        with patch(
            "host_brain.input_runtime.cortex_dispatch._run_cortex_orchestrator_pipeline",
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
                        "route_decision": {"route": "skill", "target_id": "agent_worker"},
                        "commit_event": {"phase": "COMMIT", "decision": "APPROVE"},
                    },
                    ledger=ledger,
                    ports=_ports(),
                )
            )

        assert result["route_decision"]["target_id"] == "agent_worker"
        assert meta is not None
        assert meta["triggered"] is False
        assert meta["reason_code"] == "DEVIATION_SKIPPED_codex_runtime"
        rerun_mock.assert_not_awaited()
    finally:
        ledger.close()


def test_maybe_realign_gateway_pipeline_skips_for_translate_delegate(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        monkeypatch.setenv("HOST_GATEWAY_DEVIATION_GUARD_ENABLED", "true")
        with patch(
            "host_brain.input_runtime.cortex_dispatch._run_cortex_orchestrator_pipeline",
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
                    ports=_ports(),
                )
            )

        assert result["route_decision"]["target_id"] == "translate_agent"
        assert meta is not None
        assert meta["triggered"] is False
        assert meta["reason_code"] == "DEVIATION_SKIPPED_TRANSLATE_AGENT"
        rerun_mock.assert_not_awaited()
    finally:
        ledger.close()


def test_maybe_realign_gateway_pipeline_skips_for_chat_route(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        monkeypatch.setenv("HOST_GATEWAY_DEVIATION_GUARD_ENABLED", "true")
        with patch(
            "host_brain.input_runtime.cortex_dispatch._run_cortex_orchestrator_pipeline",
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
                    ports=_ports(),
                )
            )

        assert result["route_decision"]["route"] == "chat"
        assert meta is not None
        assert meta["triggered"] is False
        assert meta["reason_code"] == "DEVIATION_NON_EXECUTION_ROUTE"
        rerun_mock.assert_not_awaited()
    finally:
        ledger.close()
