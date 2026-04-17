from __future__ import annotations

import asyncio
import json

from host_brain.app.prompt_template_store import (
    PromptTemplateStore,
    resolve_prompt_template_json_merged,
)
from host_brain.app.sub_agent_protocol import (
    SubAgentDispatcher,
    SubAgentRequest,
    get_agent_capabilities,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_get_agent_capabilities_uses_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "sub_agent_protocol.agent_capabilities",
        content=json.dumps(
            {
                "translate_agent": "翻译能力::override",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    capabilities = get_agent_capabilities()

    assert capabilities["translate_agent"] == "翻译能力::override"
    assert "translate_agent" in capabilities


def test_dispatcher_uses_unknown_agent_template(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "sub_agent_protocol.runtime_texts",
        content=json.dumps(
            {
                "unknown_agent": "UNKNOWN::{agent_id}",
                "timeout_after": "TIMEOUT::{timeout_ms}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    dispatcher = SubAgentDispatcher()
    result = asyncio.run(dispatcher.dispatch(SubAgentRequest(agent_id="missing_agent", task="noop")))

    assert result.success is False
    assert result.error == "UNKNOWN::missing_agent"


def test_dispatcher_uses_timeout_template(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "sub_agent_protocol.runtime_texts",
        content=json.dumps(
            {
                "unknown_agent": "UNKNOWN::{agent_id}",
                "timeout_after": "TIMEOUT::{timeout_ms}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    async def _raise_timeout(awaitable, *_args, **_kwargs):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr("host_brain.app.sub_agent_protocol.asyncio.wait_for", _raise_timeout)

    dispatcher = SubAgentDispatcher()
    dispatcher._handler_map = {"translate_agent": lambda _payload, _context: {"ok": True}}

    result = asyncio.run(
        dispatcher.dispatch(
            SubAgentRequest(
                agent_id="translate_agent",
                task="noop",
                timeout_ms=321,
            )
        )
    )

    assert result.success is False
    assert result.error == "TIMEOUT::321"


def test_builtin_sub_agent_templates_are_clean() -> None:
    capabilities = get_agent_capabilities()
    runtime_texts = resolve_prompt_template_json_merged("sub_agent_protocol.runtime_texts")

    assert isinstance(capabilities, dict)
    assert "codex_codegen" not in capabilities
    assert capabilities.get("translate_agent") == "翻译专家：处理中日英多语翻译，包含校对和润色"
    assert capabilities.get("repair_agent") == "环境修复专家：当浏览器、依赖或运行环境缺失时，尝试自动安装并恢复执行能力"

    assert isinstance(runtime_texts, dict)
    assert runtime_texts.get("unknown_agent") == "unknown agent_id: {agent_id}"
    assert runtime_texts.get("timeout_after") == "timeout after {timeout_ms}ms"
