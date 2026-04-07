from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.reasoning.reflection_chain import ReflectionChain


def test_prompt_template_store_uses_default_and_override() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)

    default_item = store.get_template("reflection_chain.contextual_fallback.default")
    assert default_item["source"] == "default"
    assert default_item["resolved_content"] == "等我一下，我刚才这句没接稳。"

    updated = store.upsert_template(
        "reflection_chain.contextual_fallback.default",
        content="先让我想一想，我重新接住这句。",
        enabled=True,
    )
    assert updated["source"] == "override"
    assert updated["resolved_content"] == "先让我想一想，我重新接住这句。"
    assert updated["has_override"] is True

    reset = store.reset_template("reflection_chain.contextual_fallback.default")
    assert reset["source"] == "default"
    assert reset["resolved_content"] == "等我一下，我刚才这句没接稳。"
    assert reset["has_override"] is False


def test_prompt_template_store_parses_json_override() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)

    store.upsert_template(
        "chat_agent.response_policy.withdrawn_replacements",
        content='[{"from":"我一直都在","to":"我会在"}]',
        enabled=True,
    )

    resolved = store.resolve_json("chat_agent.response_policy.withdrawn_replacements")
    assert resolved == [{"from": "我一直都在", "to": "我会在"}]


def test_reflection_chain_runtime_uses_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "reflection_chain.contextual_fallback.default",
        content="先别急，我把这句话重新接一下。",
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
        content="我在，先缓一缓。",
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


def test_runtime_template_defaults_include_input_and_qa_fallbacks() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)

    input_routes_defaults = store.resolve_json("input_routes.fallback_texts")
    qa_defaults = store.resolve_json("core.qa.fallback_texts")

    assert input_routes_defaults["reject_default"] == "唔…刚刚好像出了点小问题，等一下再试试好吗？"
    assert input_routes_defaults["approve_empty"] == "收到。"
    assert qa_defaults["default"] == "嗯…脑子有点转不过来，等我缓一下再回你。"
    assert qa_defaults["angry_or_frustrated"] == "我需要冷静一下…等会儿再好好说。"
