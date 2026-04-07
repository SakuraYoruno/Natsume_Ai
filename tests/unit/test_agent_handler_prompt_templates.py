from __future__ import annotations

from host_brain.app.handlers.code_agent_handler import _resolve_code_agent_system_prompt
from host_brain.app.handlers.intent_agent_handler import _resolve_intent_agent_system_prompt
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_code_agent_system_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "code_agent_handler.system_prompt_extra",
        content="custom code prompt",
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    assert _resolve_code_agent_system_prompt() == "custom code prompt"


def test_intent_agent_system_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "intent_agent_handler.system_prompt_extra",
        content="custom intent prompt",
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    assert _resolve_intent_agent_system_prompt() == "custom intent prompt"
