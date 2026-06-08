from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.executive_runtime_core import _build_chat_input


def test_build_chat_input_preserves_gateway_voice_delivery_flags() -> None:
    request_body = SimpleNamespace(
        reply_channel="wechat",
        suppress_edge_tts=True,
        suppress_edge_vts=True,
        render_policy={"primary_topic": "technical_help"},
        surface_task_control={"task_mode": "technical_help", "response_move": "technical_next_step"},
    )

    chat_input = _build_chat_input(
        request_body=request_body,
        request_id="req-test",
        session_id="sess-test",
        person_id="person-test",
        sanitized_message="你好",
        emotion_context={},
        emotion_snapshot={},
        bond_summary={},
        retrieval_evidence_items=[],
        retrieval_summary={},
        render_target_lang="zh",
        llm_runtime={},
        ts_ms=123,
        intent_draft={},
        trace_stage_meta={},
        sub_agent_context=None,
    )

    assert chat_input.reply_channel == "wechat"
    assert chat_input.suppress_edge_tts is True
    assert chat_input.suppress_edge_vts is True
    assert chat_input.render_policy == {"primary_topic": "technical_help"}
    assert chat_input.surface_task_control == {"task_mode": "technical_help", "response_move": "technical_next_step"}


def test_build_chat_input_upgrades_base_runtime_to_surface_reply_runtime(monkeypatch) -> None:
    request_body = SimpleNamespace(
        reply_channel="wechat",
        suppress_edge_tts=True,
        suppress_edge_vts=True,
    )

    def _fake_resolve_surface_reply_llm_runtime(*, base_runtime):
        runtime = dict(base_runtime or {})
        runtime["agent_role"] = "surface_reply"
        runtime["model_id"] = "gemini-2.5-pro"
        runtime["chat_model_id"] = "gemini-2.5-pro"
        return runtime

    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.resolve_surface_reply_llm_runtime",
        _fake_resolve_surface_reply_llm_runtime,
    )

    chat_input = _build_chat_input(
        request_body=request_body,
        request_id="req-test",
        session_id="sess-test",
        person_id="person-test",
        sanitized_message="你好",
        emotion_context={},
        emotion_snapshot={},
        bond_summary={},
        retrieval_evidence_items=[],
        retrieval_summary={},
        render_target_lang="zh",
        llm_runtime={"provider_name": "gemini", "model_id": "gemini-2.5-flash-lite"},
        ts_ms=123,
        intent_draft={},
        trace_stage_meta={},
        sub_agent_context=None,
    )

    assert isinstance(chat_input.llm_runtime, dict)
    assert chat_input.llm_runtime["agent_role"] == "surface_reply"
    assert chat_input.llm_runtime["model_id"] == "gemini-2.5-pro"
