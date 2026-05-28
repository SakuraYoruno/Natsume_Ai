from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.agent_runtime_shared import _resolve_text_render_target_lang
from host_brain.app.chat_agent import ChatAgentInput
from host_brain.app.surface_main_chain_adapter import surface_main_chain_required


def test_resolve_text_render_target_lang_uses_text_form_not_gateway_source() -> None:
    request_body = SimpleNamespace(
        input_source="GATEWAY",
        render_target_lang="ja",
        response_form="text",
    )

    resolved = _resolve_text_render_target_lang(
        request_body=request_body,
        sanitized_message="こんにちは",
    )

    assert resolved == "zh"


def test_resolve_text_render_target_lang_ignores_injected_lang_for_text() -> None:
    request_body = SimpleNamespace(
        input_source="CHAT_API",
        render_target_lang="ja",
        response_form="text",
    )

    resolved = _resolve_text_render_target_lang(
        request_body=request_body,
        sanitized_message="こんにちは",
    )

    assert resolved == "zh"


def test_resolve_text_render_target_lang_does_not_infer_from_user_message_for_text() -> None:
    request_body = SimpleNamespace(
        input_source="CHAT_API",
        render_target_lang=None,
        response_form="text",
    )

    resolved = _resolve_text_render_target_lang(
        request_body=request_body,
        sanitized_message="こんにちは",
    )

    assert resolved == "zh"


def test_resolve_text_render_target_lang_uses_voice_form_for_chinese() -> None:
    request_body = SimpleNamespace(
        input_source="GATEWAY",
        render_target_lang="zh",
        response_form="voice",
    )

    resolved = _resolve_text_render_target_lang(
        request_body=request_body,
        sanitized_message="你好",
    )

    assert resolved == "zh"


def test_resolve_text_render_target_lang_uses_media_form_for_media_action() -> None:
    request_body = SimpleNamespace(
        input_source="CHAT_API",
        render_target_lang="ja",
        response_form="sticker",
    )

    resolved = _resolve_text_render_target_lang(
        request_body=request_body,
        sanitized_message="こんにちは",
    )

    assert resolved == "media"


def test_surface_main_chain_text_form_is_not_inferred_from_render_profile() -> None:
    agent_input = ChatAgentInput(
        request_id="req-form",
        session_id="sess-form",
        user_message="你好",
        person_id="person-form",
        render_profile="ja_voice",
        response_form="",
    )

    assert surface_main_chain_required(agent_input) is True


def test_surface_main_chain_skips_explicit_voice_form() -> None:
    agent_input = ChatAgentInput(
        request_id="req-form",
        session_id="sess-form",
        user_message="你好",
        person_id="person-form",
        render_profile="zh_text",
        response_form="voice",
    )

    assert surface_main_chain_required(agent_input) is False
