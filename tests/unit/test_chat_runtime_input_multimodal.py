from __future__ import annotations

from host_brain.app.chat_runtime_input import build_chat_runtime_input


def test_chat_runtime_input_preserves_multimodal_fields_in_runtime_payload() -> None:
    runtime_input = build_chat_runtime_input(
        request_id="req-mm",
        person_id="p1",
        session_id="sess-mm",
        thread_id="thread-mm",
        user_message="这是什么意思",
        render_target_lang="zh",
        retrieval_summary={"status": "SKIPPED"},
        retrieval_evidence_items=[],
        attachments=[{"type": "image", "remote_url": "https://example.com/screen.png"}],
        multimodal_observations={
            "observed_types": ["image"],
            "summary_for_chat": "用户发来一张截图。",
            "artifact_type": "screenshot",
            "visible_text": "系统错误",
        },
        attachment_summary="用户发来一张截图。",
    )

    payload = runtime_input.to_runtime_payload(
        prompt_context={},
        prompt_context_validation={},
        prompt_context_observability={},
    )

    assert payload["request"]["attachments"] == [{"type": "image", "remote_url": "https://example.com/screen.png"}]
    assert payload["request"]["multimodal_observations"]["artifact_type"] == "screenshot"
    assert payload["request"]["attachment_summary"] == "用户发来一张截图。"
