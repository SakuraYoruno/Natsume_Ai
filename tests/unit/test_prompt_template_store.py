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

    listed_ids = {item["template_id"] for item in store.list_templates()}
    assert "react_loop.prompt_templates" in listed_ids
    assert "pentest_agent_handler.system_prompt_extra" in listed_ids
    assert store.resolve_text("surface_reply.minimal_fallback.default") == ""
    assert store.resolve_json("surface_reply.minimal_fallback.default") is None

    item = store.get_template("surface_reply.minimal_fallback.default")
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


def test_prompt_template_store_exposes_versioned_agent_prompt_specs() -> None:
    store = PromptTemplateStore(ledger=SQLiteLedger(":memory:", check_same_thread=False), cache_ttl_ms=0)

    item = store.get_template("pentest_agent_handler.system_prompt_extra")
    prompt = resolve_prompt_template_text("pentest_agent_handler.system_prompt_extra")

    assert item["source"] == "prompt_spec"
    assert item["version"].startswith("v")
    assert item["category"] == "agent_prompt"
    assert item["scope"] == "pentest_agent"
    assert item["content_type"] == "text"
    assert "prompt_specs.py" in item["source_path"]
    assert "PTES" in prompt
