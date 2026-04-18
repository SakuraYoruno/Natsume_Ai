from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.executive_runtime_core import _build_chat_input


def test_build_chat_input_preserves_gateway_voice_delivery_flags() -> None:
    request_body = SimpleNamespace(
        reply_channel="wechat",
        suppress_edge_tts=True,
        suppress_edge_vts=True,
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
        recall_slot_text={},
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
