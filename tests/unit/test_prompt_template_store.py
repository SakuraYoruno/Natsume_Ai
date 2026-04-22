from __future__ import annotations

import json

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.reasoning.reflection_chain import ReflectionChain


def test_prompt_template_store_uses_default_and_override() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)

    default_item = store.get_template("reflection_chain.contextual_fallback.default")
    assert default_item["source"] == "default"
    assert store.resolve_json("reflection_chain.contextual_fallback.default")["zh"] == "嗯，我在。刚才那句我想再理一下，马上接着你说。"

    store.upsert_template(
        "reflection_chain.contextual_fallback.default",
        content=json.dumps({"zh": "先让我想一想，我重新接住这句。", "ja": "少しだけ言い直しますね。"}, ensure_ascii=False),
        enabled=True,
    )
    updated = store.get_template("reflection_chain.contextual_fallback.default")
    assert updated["source"] == "override"
    assert store.resolve_json("reflection_chain.contextual_fallback.default")["zh"] == "先让我想一想，我重新接住这句。"
    assert updated["has_override"] is True

    store.reset_template("reflection_chain.contextual_fallback.default")
    reset = store.get_template("reflection_chain.contextual_fallback.default")
    assert reset["source"] == "default"
    assert store.resolve_json("reflection_chain.contextual_fallback.default")["zh"] == "嗯，我在。刚才那句我想再理一下，马上接着你说。"
    assert reset["has_override"] is False


def test_prompt_template_store_parses_json_override() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)

    store.upsert_template(
        "chat_agent.render_policy.withdrawn_replacements",
        content='[{"from":"我一直都在","to":"我会在"}]',
        enabled=True,
    )

    resolved = store.resolve_json("chat_agent.render_policy.withdrawn_replacements")
    assert resolved == [{"from": "我一直都在", "to": "我会在"}]


def test_reflection_chain_runtime_uses_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "reflection_chain.contextual_fallback.default",
        content=json.dumps({"zh": "先别急，我把这句话重新接一下。", "ja": "少し待ってください。今の返事を整えます。"}, ensure_ascii=False),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    result = ReflectionChain().revise(
        user_message="随便说点什么",
        ai_response="",
        emotion_tag="",
        bond_stage="STRANGER",
    )

    assert result.revised_text == "先别急，我把这句话重新接一下。"


def test_reflection_chain_runtime_uses_empathy_prefix_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "reflection_chain.revision_empathy_prefix",
        content=json.dumps({"zh": "我在，先缓一缓。", "ja": "大丈夫です。まずは落ち着きましょう。"}, ensure_ascii=False),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    result = ReflectionChain().revise(
        user_message="我今天真的有点撑不住了",
        ai_response="我们慢慢来。",
        emotion_tag="sadness",
        bond_stage="FRIEND",
    )

    assert result.revised_text.startswith("我在，先缓一缓。")


def test_chat_agent_minimal_fallback_defaults_are_not_internal_draft_style() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)

    default_text = str(store.get_template("chat_agent.minimal_fallback.default")["resolved_content"] or "")
    code_fallback_text = str(store.get_template("chat_agent.minimal_fallback.codex_codegen")["resolved_content"] or "")

    assert "内部草稿" not in default_text
    assert "整理成更清楚一点的说法" not in default_text
    assert "内部草稿" not in code_fallback_text
    assert "整理成更清楚一点的说法" not in code_fallback_text


def test_runtime_template_defaults_include_input_and_qa_fallbacks() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)

    input_routes_defaults = store.resolve_json("input_routes.fallback_texts")
    qa_defaults = store.resolve_json("core.qa.fallback_texts")

    assert input_routes_defaults["zh"]["reject_default"] == "刚才那一下有点卡住了，我这边马上重试，你先别急。"
    assert input_routes_defaults["zh"]["approve_empty"] == "好，我接到了，这就去处理。"
    assert input_routes_defaults["ja"]["approve_empty"] == "ええ、受け取りました。すぐ取りかかります。"
    assert qa_defaults["default"] == "嗯…脑子有点转不过来，等我缓一下再回你。"
    assert qa_defaults["angry_or_frustrated"] == "我需要冷静一下…等会儿再好好说。"

