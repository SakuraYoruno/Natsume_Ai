from __future__ import annotations

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.prompt_template_store import (
    PromptTemplateStore,
    resolve_prompt_template_json_merged,
    resolve_prompt_template_text,
)


def test_prompt_template_store_is_retired_and_does_not_read_yaml_or_overrides() -> None:
    store = PromptTemplateStore(ledger=SQLiteLedger(":memory:", check_same_thread=False), cache_ttl_ms=0)

    assert store.list_templates() == []
    assert store.resolve_text("chat_agent.minimal_fallback.default") == ""
    assert store.resolve_json("chat_agent.minimal_fallback.default") is None

    item = store.get_template("chat_agent.minimal_fallback.default")
    assert item["source"] == "retired"
    assert item["resolved_content"] == ""

    with pytest.raises(RuntimeError):
        store.upsert_template("anything", content="nope")


def test_prompt_template_store_only_proxies_prompt_specs_for_structural_prompts() -> None:
    payload = resolve_prompt_template_json_merged("multimodal_observer.model_prompt.templates")

    assert isinstance(payload, dict)
    assert "image_system_prompt" in payload
    assert resolve_prompt_template_text("multimodal_observer.model_prompt.templates") == ""
    assert resolve_prompt_template_json_merged("unknown.template") is None
