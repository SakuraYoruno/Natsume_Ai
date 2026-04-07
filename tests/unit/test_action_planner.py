from __future__ import annotations

import json

import pytest

from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.app.vision.action_planner import generate_action_plan
from host_brain.audit.sqlite_ledger import SQLiteLedger


@pytest.mark.asyncio
async def test_action_planner_prompt_templates_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "vision.action_planner.prompt_templates",
        content=json.dumps(
            {
                "system_prompt": "自定义动作规划 system",
                "user_prompt_template": "目标={goal}\n上下文={vision_context_section}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    captured: dict[str, object] = {}

    def _fake_provider(payload: dict) -> dict:
        captured["payload"] = payload
        return {
            "content": json.dumps(
                {
                    "goal_understood": "点击登录",
                    "actions": [
                        {"type": "click", "target_desc": "登录按钮", "x": 10, "y": 20},
                        {"type": "done", "target_desc": "完成"},
                    ],
                    "confidence": 0.9,
                    "notes": "ok",
                },
                ensure_ascii=False,
            )
        }

    plan = await generate_action_plan(
        request_id="req-action-template",
        session_id="sess-action-template",
        goal="点击登录",
        vision_context=None,
        llm_runtime={
            "cloud_provider_name": "gemini",
            "cloud_model_id": "gemini-2.5-flash",
            "cloud_llm_api_key": "key",
            "cloud_timeout_ms": 15000,
            "cloud_available": True,
            "cloud_provider_fn": _fake_provider,
        },
    )

    payload = dict(captured["payload"] or {})
    messages = list(payload.get("messages") or [])
    assert messages[0]["content"] == "自定义动作规划 system"
    assert messages[1]["content"] == "目标=点击登录\n上下文=（用户未提供截图，请根据文字描述进行分析）"
    assert plan.actions[0].target_desc == "登录按钮"
    assert plan.extra["goal_understood"] == "点击登录"
