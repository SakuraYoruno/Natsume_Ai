from __future__ import annotations

import json

from host_brain.app.handlers.intent_agent_entry_handler import intent_agent_entry_handler
from host_brain.app.prompt_template_store import (
    PromptTemplateStore,
    resolve_prompt_template_json_merged,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger


def _install_store(monkeypatch, content: dict) -> PromptTemplateStore:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "intent_agent_entry_handler.failure_texts",
        content=json.dumps(content, ensure_ascii=False),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    return store


def test_intent_entry_handler_invoke_unavailable_uses_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        {
            "invoke_model_unavailable": "custom invoke unavailable",
        },
    )

    out = intent_agent_entry_handler({"request_id": "req-intent-entry-1"}, {})

    assert out["subgraph_failure"]["safe_fallback_reason"] == "custom invoke unavailable"


def test_intent_entry_handler_node_exception_uses_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        {
            "node_exception": "NODE={node_id}|RETRY={max_retry_count}|ERR={error}",
        },
    )

    def fail_first(_node_id, _payload=None):
        raise RuntimeError("boom")

    out = intent_agent_entry_handler(
        {"request_id": "req-intent-entry-2"},
        {"request_id": "req-intent-entry-2", "invoke_model_node": fail_first},
    )

    assert out["subgraph_failure"]["safe_fallback_reason"] == "NODE=core_reasoner_primary|RETRY=2|ERR=boom"


def test_builtin_intent_entry_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("intent_agent_entry_handler.failure_texts")

    assert isinstance(templates, dict)
    assert templates.get("invoke_model_unavailable")
    assert templates.get("local_loop_timeout")
    assert templates.get("node_exception")
    assert templates.get("max_steps_reached")
