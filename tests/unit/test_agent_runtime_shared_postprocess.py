from __future__ import annotations

import pytest

from host_brain.app.agent_runtime_shared import _async_post_process


@pytest.mark.asyncio
async def test_async_post_process_uses_minimal_mode_for_fast_chat_without_voice(monkeypatch) -> None:
    async def _fake_persist_post_process_audit(**kwargs):  # noqa: ANN003
        return None

    async def _fake_run_in_post_process_executor(fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return None

    class _ForbiddenCortex:
        def post_process_bond_update(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("bond update should be skipped in minimal fast-chat postprocess")

        def emit_conversation_ended(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("conversation ended should be skipped in minimal fast-chat postprocess")

    async def _forbidden_drive_stream_vts_emotion(**kwargs):  # noqa: ANN003
        raise AssertionError("VTS emotion drive should be skipped in minimal fast-chat postprocess")

    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._persist_post_process_audit",
        _fake_persist_post_process_audit,
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._run_in_post_process_executor",
        _fake_run_in_post_process_executor,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_episodic_store",
        lambda: None,
    )
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.mark_recent_foreground_interaction",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "host_brain.tribe.cortex_agent.get_cortex_agent",
        lambda: _ForbiddenCortex(),
    )
    monkeypatch.setattr(
        "host_brain.app.vts_emotion_driver._drive_stream_vts_emotion",
        _forbidden_drive_stream_vts_emotion,
    )

    await _async_post_process(
        request_id="req-fast-postprocess",
        person_id="user_1",
        session_id="sess-fast-postprocess",
        user_message="我只是想和你聊聊。",
        narrative="我在，慢慢说吧。",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
        ts_ms=1,
        render_target_lang="zh",
        total_latency_ms=50,
        llm_latency_ms=30,
        first_token_ms=10,
        provider_name="mock",
        model_id="mock-fast",
        buffer_stream=False,
        delegated_agents=[],
        retrieval_summary={"status": "SKIPPED"},
        gov_decision={"decision": "APPROVE"},
        pii_ingress_stats={},
        pii_egress_stats={},
        trace_stage_meta={
            "chat_speed_profile": "direct_fast",
            "voice_outputs_enabled": False,
        },
    )
