from __future__ import annotations

import json

from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import ToolSpec
from host_brain.app.prompt_template_store import (
    PromptTemplateStore,
    resolve_prompt_template_json_merged,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_run_react_loop_uses_prompt_template_overrides(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "react_loop.prompt_templates",
        content=json.dumps(
            {
                "system_prompt_template": "SYS\n{tools_description}",
                "tool_line": "* {tool_id}: {description}",
                "tool_params_line": "  ARGS: {param_desc}",
                "tool_result_message": "OBS::{obs_text}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    tool = ToolSpec(
        tool_id="alpha",
        display_name="Alpha",
        description="first tool",
        parameters={"properties": {"query": {"description": "keyword"}}},
    )

    monkeypatch.setattr("host_brain.app.agents.react_loop.is_tool_allowed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "host_brain.app.agents.react_loop.execute_tool",
        lambda _tool_id, args: {"status": "ok", "echo": args.get("query")},
    )

    class _Gateway:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, str]]] = []

        def complete(self, messages):
            self.calls.append([dict(item) for item in messages])
            if len(self.calls) == 1:
                return {
                    "content": json.dumps(
                        {
                            "thought": "step1",
                            "action_type": "tool_call",
                            "action": {"tool": "alpha", "args": {"query": "hi"}},
                        },
                        ensure_ascii=False,
                    )
                }
            return {
                "content": json.dumps(
                    {
                        "thought": "done",
                        "action_type": "final_answer",
                        "final_answer": "ok",
                    },
                    ensure_ascii=False,
                )
            }

    gateway = _Gateway()
    result = run_react_loop(
        agent_id="codex_worker",
        user_input="hello",
        tools=[tool],
        gateway=gateway,
    )

    assert result.final_answer == "ok"
    assert gateway.calls[0][0]["content"].startswith("SYS\n")
    assert "* alpha: first tool" in gateway.calls[0][0]["content"]
    assert "ARGS: query(keyword)" in gateway.calls[0][0]["content"]
    assert gateway.calls[1][-1]["content"].startswith("OBS::{")
    assert '"echo": "hi"' in gateway.calls[1][-1]["content"]


def test_run_react_loop_uses_no_llm_overrides(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "react_loop.prompt_templates",
        content=json.dumps(
            {
                "no_llm_final_answer": "NO-LLM:{agent_id}",
                "no_llm_thought": "fallback-thought",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    result = run_react_loop(
        agent_id="intent_agent",
        user_input="hello",
        tools=[],
        gateway=None,
    )

    assert result.final_answer == "NO-LLM:intent_agent"
    assert result.steps[0].thought == "fallback-thought"


def test_builtin_react_prompt_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("react_loop.prompt_templates")

    assert isinstance(templates, dict)
    assert str(templates.get("system_prompt_template") or "").startswith("你是夏目蓝的执行模块")
    assert templates.get("no_tools_description")
    assert templates.get("tool_params_line") == "  参数: {param_desc}"
    assert templates.get("parse_failure_thought")
    assert templates.get("no_llm_final_answer")
    assert templates.get("tool_result_message") == "工具执行结果：{obs_text}\n\n请继续。"
    assert templates.get("abnormal_stop_final_answer")

