from __future__ import annotations

import json

from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.inner_world import _build_awakening_hint, _describe_emotion_drift


def test_inner_world_runtime_texts_override_emotion_drift(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "inner_world.runtime_texts",
        content=json.dumps(
            {
                "emotion_drift": {
                    "high_positive": "custom-high-positive",
                    "default": "custom-default",
                }
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    assert _describe_emotion_drift(0.8, 0.8) == "custom-high-positive"
    assert _describe_emotion_drift(0.0, 0.2) == "custom-default"


def test_inner_world_runtime_texts_override_awakening_hint(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "inner_world.runtime_texts",
        content=json.dumps(
            {
                "awakening_hint": {
                    "wait_hints": {
                        "7d_plus": "custom-wait",
                    },
                    "loneliness": {
                        "high": "custom-lonely",
                    },
                    "topic_templates": {
                        "7d_plus": "custom-topic {topic}",
                    },
                    "anticipation": {
                        "long_absence": "custom-return",
                    },
                }
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    hint = _build_awakening_hint(
        offline_hours=8 * 24.0,
        absence_band="7d_plus",
        loneliness=0.9,
        anticipation=0.8,
        topics=["旅行计划"],
    )
    assert "custom-wait" in hint
    assert "custom-lonely" in hint
    assert "custom-topic 旅行计划" in hint

    return_only = _build_awakening_hint(
        offline_hours=8 * 24.0,
        absence_band="7d_plus",
        loneliness=0.1,
        anticipation=0.8,
        topics=[],
    )
    assert "custom-return" in return_only


def test_builtin_inner_world_runtime_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("inner_world.runtime_texts")
    assert templates["emotion_drift"]["high_positive"] == "心情还不错，有些期待"
    assert templates["emotion_drift"]["default"] == "状态平稳，在等待中"
    assert templates["awakening_hint"]["wait_hints"]["7d_plus"] == "像隔了很久很久"
    assert templates["awakening_hint"]["topic_templates"]["recent"] == "还记得{topic}"
