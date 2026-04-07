from __future__ import annotations

import json

from host_brain.app.prompt_builder import CHAT_RENDER_NODE_ID
from host_brain.app.prompt_template_store import (
    PromptTemplateStore,
    resolve_prompt_template_json_merged,
)
from host_brain.app.provider_factory import (
    _build_host_mock_reply_text,
    _build_host_openai_system_prompt,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_build_host_openai_system_prompt_uses_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "provider_factory.host_openai.prompt_templates",
        content=json.dumps(
            {
                "system_prompt_with_node": "node={node_id}",
                "system_prompt_default": "default-system",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    assert _build_host_openai_system_prompt("chat_render") == "node=chat_render"
    assert _build_host_openai_system_prompt("") == "default-system"


def test_build_host_mock_reply_text_uses_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "provider_factory.host_mock.reply_templates",
        content=json.dumps(
            {
                "fallback_user_message": "hi",
                "zh_render_reply": "ZH:{user_message}",
                "ja_render_reply": "JA:{user_message}",
                "verifier_pass_reply": "SAFE",
                "generic_reply": "GEN:{user_message}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    assert _build_host_mock_reply_text(CHAT_RENDER_NODE_ID, {"user_message": "你好", "render_target_lang": "zh"}) == "ZH:你好"
    assert _build_host_mock_reply_text(CHAT_RENDER_NODE_ID, {"user_message": "hello", "render_target_lang": "ja"}) == "JA:hello"
    assert _build_host_mock_reply_text("safety_verifier", {}) == "SAFE"
    assert _build_host_mock_reply_text("other", {}) == "GEN:hi"


def test_builtin_provider_factory_templates_are_clean() -> None:
    openai_templates = resolve_prompt_template_json_merged("provider_factory.host_openai.prompt_templates")
    mock_templates = resolve_prompt_template_json_merged("provider_factory.host_mock.reply_templates")

    assert isinstance(openai_templates, dict)
    assert str(openai_templates.get("system_prompt_default") or "").startswith("You are Natsume Ai.")
    assert "processing node `{node_id}`" in str(openai_templates.get("system_prompt_with_node") or "")

    assert isinstance(mock_templates, dict)
    assert mock_templates.get("fallback_user_message") == "你好"
    assert mock_templates.get("zh_render_reply") == "收到，先给你一版中文草稿：{user_message}"
    assert mock_templates.get("generic_reply") == "收到，先给你一版草稿：{user_message}"
