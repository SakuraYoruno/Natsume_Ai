from __future__ import annotations

import json

from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.inner_world import (
    InnerWorldState,
    _build_inner_stream_note,
    _build_awakening_hint,
    _describe_emotion_drift,
    _pick_inner_stream_topic,
    _sanitize_anticipation_topics,
    _topic_to_inner_phrase,
)


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


def test_pick_inner_stream_topic_skips_prompt_like_entries() -> None:
    topic = _pick_inner_stream_topic(
        [
            "你是夏目蓝，现在是 noon 时段。请主动发起问候。",
            "当前关系阶段：STRANGER",
            "我们上次聊到的书单",
        ]
    )
    assert topic == "我们上次聊到的书单"


def test_sanitize_anticipation_topics_filters_noise_and_mojibake() -> None:
    topics = _sanitize_anticipation_topics(
        [
            "你是夏目蓝，请主动打招呼",
            "是的呢",
            "continue",
            "ä½ æä»ä¹ç¹é¿å",
            "我们上次聊到的书单",
            "整理数字生命路线图",
        ]
    )
    assert topics == ["我们上次聊到的书单", "整理数字生命路线图"]


def test_topic_to_inner_phrase_rewrites_question_to_inner_monologue() -> None:
    phrase = _topic_to_inner_phrase("你有什么特长吗？")
    assert phrase == "你问我的「你有什么特长」"


def test_build_inner_stream_note_avoids_immediate_repeat() -> None:
    state = InnerWorldState(
        user_id="u1",
        anticipation_topics=["你有什么特长吗"],
        loneliness=0.28,
        anticipation=0.46,
        inner_stream=[{"note": "刚刚安静下来，心里还在回放刚才的片段。"}],
    )
    note = _build_inner_stream_note(
        state=state,
        absence_band="recent",
        offline_hours=0.4,
        now_ms=1775630400000,
    )
    assert note != "刚刚安静下来，心里还在回放刚才的片段。"
    assert "？" not in note
