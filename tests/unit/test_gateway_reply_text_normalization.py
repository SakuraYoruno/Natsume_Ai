from __future__ import annotations

from host_brain.app.handlers.gateway_orchestrator_handler import (
    _augment_gateway_prompt_for_reply_depth,
    _normalize_gateway_commit_narrative,
)


class TestGatewayReplyTextNormalization:
    def test_extracts_visible_text_from_contract_json(self) -> None:
        raw_text = (
            '{"narrative":{"text":"你好，世界"},"intent_draft":{},'
            '"meta":{"draft_only":true}}'
        )

        narrative = _normalize_gateway_commit_narrative(raw_text=raw_text)

        assert narrative == "你好，世界"

    def test_falls_back_to_rendered_text_when_raw_is_not_json(self) -> None:
        narrative = _normalize_gateway_commit_narrative(
            raw_text="not json",
            rendered_text="fallback text",
        )

        assert narrative == "fallback text"

    def test_augment_gateway_prompt_uses_brief_policy_for_short_requests(self) -> None:
        prompt = _augment_gateway_prompt_for_reply_depth(
            prompt_text="BASE_PROMPT",
            user_message="please short answer",
        )

        assert prompt.startswith("BASE_PROMPT")
        assert "The user requested a brief answer." in prompt
        assert "Keep the visible reply concise" in prompt

    def test_augment_gateway_prompt_uses_default_depth_policy(self) -> None:
        prompt = _augment_gateway_prompt_for_reply_depth(
            prompt_text="BASE_PROMPT",
            user_message="普通聊天",
        )

        assert prompt.startswith("BASE_PROMPT")
        assert "Default to 2-4 short sentences" in prompt
        assert "avoid one-line shallow replies" in prompt
