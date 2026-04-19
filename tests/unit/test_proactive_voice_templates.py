from __future__ import annotations

import datetime
import json

from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.proactive_voice import (
    PersonaProactiveVoice,
    SCENE_LATE_NIGHT,
    SCENE_MORNING_GREET,
    SCENE_TOPIC_FOLLOWUP,
)


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime.datetime(year, month, day, hour, minute, 0).timestamp() * 1000)


def test_proactive_voice_holiday_templates_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "proactive_voice.holiday_templates",
        content=json.dumps(
            {
                "special": {
                    "春节": {
                        "warm": ["自定义春节问候"],
                    }
                },
                "default": {
                    "warm": ["自定义节日快乐"],
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    voice = PersonaProactiveVoice()
    assert voice._get_holiday_template({"holiday_name": "春节"}, "warm", 0) == "自定义春节问候"
    assert voice._get_holiday_template({"holiday_name": "测试节"}, "warm", 0) == "自定义测试节快乐"


def test_proactive_voice_morning_templates_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "proactive_voice.morning_greet_templates",
        content=json.dumps(
            {
                "winter": {
                    "mid": {
                        "warm": ["自定义冬日早安"],
                    }
                }
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    voice = PersonaProactiveVoice()
    content = voice._get_template(
        SCENE_MORNING_GREET,
        "warm",
        {},
        _ts(2026, 1, 5, 8, 0),
        "FRIEND",
    )
    assert content == "自定义冬日早安"


def test_proactive_voice_scene_templates_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "proactive_voice.scene_templates",
        content=json.dumps(
            {
                "late_night": {
                    "mid": {
                        "warm": ["自定义深夜陪伴"],
                    }
                }
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    voice = PersonaProactiveVoice()
    content = voice._get_template(
        SCENE_LATE_NIGHT,
        "warm",
        {},
        _ts(2026, 1, 5, 1, 0),
        "FRIEND",
    )
    assert content == "自定义深夜陪伴"


def test_proactive_voice_runtime_texts_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "proactive_voice.runtime_texts",
        content=json.dumps(
            {
                "gaming_companion_followup": "先打完这局，我们再聊{topic_label}",
                "trait_default": "自定义默认风格",
                "default_topic_label": "那件小事",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    voice = PersonaProactiveVoice()
    content, reason = voice._apply_activity_to_content(
        "原始内容",
        scene_type=SCENE_TOPIC_FOLLOWUP,
        ctx={
            "activity_monitor": {"recent_event_type": "gaming_detected"},
            "pending_followup_topics": [{"topic_label": "项目"}],
        },
    )

    assert content == "先打完这局，我们再聊项目"
    assert reason == "activity:gaming_companion_followup"
    assert voice._describe_trait_influence(None, "default") == "自定义默认风格"
    assert voice._extract_topic_label({}) == "那件小事"


def test_builtin_proactive_voice_runtime_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("proactive_voice.runtime_texts")
    assert templates["guarded_soften_followup"] == "忽然想起你之前提过的{topic_label}，如果你现在愿意聊的话，可以慢慢说。"
    assert templates["gaming_companion_followup"] == "你先专心打，等你这局打完再和我说说{topic_label}？"
    assert templates["generic_default_reply"] == "你好～"
    assert templates["trait_fallback"] == "自然风格"


def test_proactive_voice_japanese_scene_and_runtime_templates() -> None:
    voice = PersonaProactiveVoice()

    content = voice._get_template(
        SCENE_LATE_NIGHT,
        "warm",
        {"render_target_lang": "ja"},
        _ts(2026, 1, 5, 1, 0),
        "FRIEND",
    )

    assert any(ch in content for ch in "あいうえおアイウエオ")
    assert "你" not in content
    assert "我" not in content

    trait = voice._describe_trait_influence(None, "default", render_target_lang="ja")
    assert any(ch in trait for ch in "あいうえおアイウエオ")
    assert "自然" not in trait or "自然で" in trait
