from __future__ import annotations

import json

from host_brain.app.input_service_quality import build_realign_user_message
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_build_realign_user_message_uses_runtime_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "input_service_quality.runtime_texts",
        content=json.dumps(
            {
                "realign_user_message": {
                    "header": "[自定义纠偏]",
                    "intro": "请只围绕原始任务重做，不要跑偏。",
                    "original_template": "原任务={original}",
                    "drifted_template": "跑偏结果={first}",
                    "requirement": "要求：直接给出可验证结果。",
                }
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    message = build_realign_user_message(
        original_user_text="请继续整理这段需求",
        first_narrative="我先陪你聊聊心情",
    )

    assert message == (
        "[自定义纠偏]\n"
        "请只围绕原始任务重做，不要跑偏。\n"
        "原任务=请继续整理这段需求\n"
        "跑偏结果=我先陪你聊聊心情\n"
        "要求：直接给出可验证结果。"
    )


def test_build_realign_user_message_uses_japanese_runtime_templates() -> None:
    message = build_realign_user_message(
        original_user_text="この要件を整理してください",
        first_narrative="少し雑談に流れてしまいました",
        render_target_lang="ja",
    )

    assert "元の依頼" in message
    assert "この要件を整理してください" in message
    assert "外れた出力" in message
    assert "少し雑談に流れてしまいました" in message
    assert "原始请求" not in message
