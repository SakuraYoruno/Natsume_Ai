from __future__ import annotations

import json

from host_brain.app.dispatch_agent import _build_failure_recovery_plan
from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_build_failure_recovery_plan_uses_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "dispatch_agent.failure_recovery.templates",
        content=json.dumps(
            {
                "file_error": {
                    "summary": "custom-file-summary",
                    "task_template": "FILE::{user_message}::{target_id}::{error_text}",
                },
                "default_error": {
                    "summary": "custom-default-summary",
                    "task_template": "DEFAULT::{user_message}::{target_id}::{error_text}",
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    file_plan = _build_failure_recovery_plan(
        target_id="file_read",
        user_message="read missing file",
        error_text="FILE_NOT_FOUND: missing",
    )
    assert file_plan is not None
    assert file_plan["summary"] == "custom-file-summary"
    assert file_plan["task"] == "FILE::read missing file::file_read::FILE_NOT_FOUND: missing"

    default_plan = _build_failure_recovery_plan(
        target_id="noop",
        user_message="do nothing",
        error_text="UNKNOWN_REASON: skip",
    )
    assert default_plan is not None
    assert default_plan["summary"] == "custom-default-summary"
    assert default_plan["task"] == "DEFAULT::do nothing::noop::UNKNOWN_REASON: skip"


def test_builtin_dispatch_failure_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("dispatch_agent.failure_recovery.templates")
    assert templates["file_error"]["summary"] == "原技能失败，正在让执行专家分析文件问题并推断可行修复方案。"
    assert templates["browser_error"]["task_template"].startswith("用户原请求：{user_message}")
    assert templates["default_error"]["summary"] == "原技能失败，正在尝试自动分析并修复运行环境。"
