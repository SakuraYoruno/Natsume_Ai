from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.agent_runtime_shared import _resolve_text_render_target_lang


def test_resolve_text_render_target_lang_forces_gateway_text_to_chinese() -> None:
    request_body = SimpleNamespace(
        input_source="GATEWAY",
        render_target_lang="ja",
    )

    resolved = _resolve_text_render_target_lang(
        request_body=request_body,
        sanitized_message="こんにちは",
    )

    assert resolved == "zh"


def test_resolve_text_render_target_lang_keeps_non_gateway_japanese() -> None:
    request_body = SimpleNamespace(
        input_source="CHAT_API",
        render_target_lang="ja",
    )

    resolved = _resolve_text_render_target_lang(
        request_body=request_body,
        sanitized_message="こんにちは",
    )

    assert resolved == "ja"


def test_resolve_text_render_target_lang_infers_non_gateway_message_language() -> None:
    request_body = SimpleNamespace(
        input_source="CHAT_API",
        render_target_lang=None,
    )

    resolved = _resolve_text_render_target_lang(
        request_body=request_body,
        sanitized_message="こんにちは",
    )

    assert resolved == "ja"
