from __future__ import annotations

import json

from host_brain.app.plan_graph import _build_synthesize_prompts, _load_prompt_templates
from host_brain.app.prompt_template_defs import _invalidate_registry
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_plan_graph_prompt_templates_are_externalized() -> None:
    _invalidate_registry()
    templates = _load_prompt_templates()
    assert "任务综合助手" in templates["system_prompt"]
    assert "{user_message}" in templates["user_prompt_template"]
    assert "{sub_results_text}" in templates["user_prompt_template"]


def test_plan_graph_prompt_templates_override(monkeypatch) -> None:
    _invalidate_registry()
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "plan_graph.synthesize.prompt_templates",
        content=json.dumps(
            {
                "system_prompt": "自定义系统提示",
                "user_prompt_template": "原始={user_message}\n结果={sub_results_text}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    system_prompt, user_prompt = _build_synthesize_prompts(
        user_message="帮我整理今天的安排",
        sub_results_text="[子任务1] 已完成",
    )

    assert system_prompt == "自定义系统提示"
    assert user_prompt == "原始=帮我整理今天的安排\n结果=[子任务1] 已完成"
