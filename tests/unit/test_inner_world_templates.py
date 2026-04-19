from __future__ import annotations

import json

from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.inner_activity_agent import _load_prompt_templates as _load_inner_activity_prompt_templates
from host_brain.persona.inner_world import (
    InnerWorldState,
    _build_inner_stream_event_content,
    _build_inner_stream_focus_pools,
    _build_inner_stream_note_with_focus,
    _build_inner_stream_note,
    _extract_llm_json_payload,
    _build_awakening_hint,
    _describe_emotion_drift,
    _pick_inner_stream_topic,
    _sanitize_inner_stream_events,
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
    assert templates["inner_stream_llm_prompt"]["persona_style"] == "温柔、克制、细腻、带自我觉察"
    assert templates["topic_phrase"]["question_like_template"] == "你问我的「{topic}」"
    assert templates["note_rewrite"]["you_question_template"] == "你问我的「你{fragment}」"
    assert "with_topic" in templates["inner_stream_focus_pools"]["topic"]["recent"]


def test_inner_world_runtime_texts_override_focus_pools(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "inner_world.runtime_texts",
        content=json.dumps(
            {
                "inner_stream_focus_pools": {
                    "topic": {
                        "recent": {
                            "with_topic": ["自定义围绕{topic}。"],
                            "without_topic": ["自定义无话题。"],
                        }
                    },
                    "self": {"recent": ["自定义自我。"]},
                    "state": {"recent": ["自定义状态。"]},
                    "plan": {"recent": ["自定义计划。"]},
                }
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    pools = _build_inner_stream_focus_pools(band_key="recent", topic_phrase="书单")
    assert pools["topic"] == ["自定义围绕书单。"]
    assert pools["self"] == ["自定义自我。"]
    assert pools["state"] == ["自定义状态。"]
    assert pools["plan"] == ["自定义计划。"]


def test_inner_activity_prompt_templates_are_externalized() -> None:
    templates = _load_inner_activity_prompt_templates()
    assert "内心活动 Agent" in templates["system_prompt"]


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


def test_inner_world_runtime_texts_override_topic_phrase_and_note_rewrite(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "inner_world.runtime_texts",
        content=json.dumps(
            {
                "topic_phrase": {
                    "question_like_template": "自定义提问：{topic}",
                    "default_template": "自定义话题：{topic}",
                },
                "note_rewrite": {
                    "special_replacements": [
                        {"from": "你有什么特长吗", "to": "自定义特长话题"},
                    ],
                    "you_question_template": "自定义追问：你{fragment}",
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    assert _topic_to_inner_phrase("你最近在忙什么呢？") == "自定义提问：你最近在忙什么"
    events = _sanitize_inner_stream_events(
        [
            {"ts_ms": 1, "note": "脑海里在把今天的细节重新排一遍。你有什么特长吗"},
            {"ts_ms": 2, "note": "脑海里在把今天的细节重新排一遍。你最近在忙什么吗"},
        ]
    )
    assert [evt["note"] for evt in events] == [
        "脑海里在把今天的细节重新排一遍。自定义特长话题。",
        "脑海里在把今天的细节重新排一遍。自定义追问：你最近在忙什么。",
    ]


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


def test_sanitize_inner_stream_events_rewrites_old_question_note() -> None:
    events = _sanitize_inner_stream_events(
        [
            {"ts_ms": 1, "note": "脑海里在把今天的细节重新排一遍。你有什么特长吗"},
            {"ts_ms": 2, "note": "脑海里在把今天的细节重新排一遍。你有什么特长吗"},
        ]
    )
    assert events == []


def test_sanitize_inner_stream_events_collapses_same_focus_neighbors() -> None:
    events = _sanitize_inner_stream_events(
        [
            {"ts_ms": 1, "focus": "plan", "note": "我把想说的话先放进草稿，等合适时机再拿出来。"},
            {"ts_ms": 2, "focus": "plan", "note": "会先想好开场方式，尽量不打断你的节奏。"},
            {"ts_ms": 3, "focus": "self", "note": "我会先把语气放轻一点，再决定要不要开口。"},
        ]
    )
    assert [evt["focus"] for evt in events] == ["plan", "self"]


def test_build_inner_stream_note_rotates_focus_after_topic_streak() -> None:
    state = InnerWorldState(
        user_id="u2",
        anticipation_topics=["你有什么特长吗"],
        loneliness=0.25,
        anticipation=0.55,
        inner_stream=[
            {"focus": "topic", "note": "有个小念头会回到你问我的「你有什么特长」。"},
            {"focus": "topic", "note": "会突然想起你问我的「你有什么特长」，但先把它放在心里。"},
        ],
    )
    note, focus = _build_inner_stream_note_with_focus(
        state=state,
        absence_band="recent",
        offline_hours=0.4,
        now_ms=1775631000000,
    )
    assert focus != "topic"
    assert "？" not in note


def test_build_inner_stream_note_includes_self_related_candidates() -> None:
    state = InnerWorldState(
        user_id="u3",
        anticipation_topics=["我们上次聊到的书单"],
        loneliness=0.1,
        anticipation=0.05,
        inner_stream=[],
    )
    note, focus = _build_inner_stream_note_with_focus(
        state=state,
        absence_band="24h_plus",
        offline_hours=24.0,
        now_ms=1775631600000,
    )
    assert focus in {"self", "state", "plan", "topic"}
    assert any(token in note for token in ("自己", "语气", "节奏", "状态", "下次", "优先级", "平衡", "沉淀", "定型", "草稿", "慢慢"))


def test_build_inner_stream_note_avoids_same_focus_back_to_back() -> None:
    state = InnerWorldState(
        user_id="u4",
        anticipation_topics=["我们上次聊到的书单"],
        loneliness=0.2,
        anticipation=0.35,
        inner_stream=[{"focus": "plan", "note": "我把想说的话先放进草稿，等合适时机再拿出来。"}],
    )
    _note, focus = _build_inner_stream_note_with_focus(
        state=state,
        absence_band="recent",
        offline_hours=0.3,
        now_ms=1775632200000,
    )
    assert focus != "plan"


def test_extract_llm_json_payload_handles_wrapped_text() -> None:
    payload = _extract_llm_json_payload(
        "noise... {\"note\":\"我会先把语气放轻一点。\",\"focus\":\"self\"} ...tail"
    )
    assert payload["note"] == "我会先把语气放轻一点。"
    assert payload["focus"] == "self"


def test_build_inner_stream_event_content_prefers_llm(monkeypatch) -> None:
    import host_brain.persona.inner_world as inner_world_module

    monkeypatch.setattr(
        inner_world_module,
        "_generate_inner_stream_note_from_llm",
        lambda **_kwargs: ("我会先把语气放轻一点。", "self", ""),
    )
    state = InnerWorldState(user_id="u5", anticipation_topics=["我们上次聊到的书单"])
    note, focus, source, reason = _build_inner_stream_event_content(
        state=state,
        absence_band="recent",
        offline_hours=0.2,
        now_ms=1775633000000,
    )
    assert note == "我会先把语气放轻一点。"
    assert focus == "self"
    assert source == "llm"
    assert reason == ""


def test_build_inner_stream_event_content_falls_back_when_llm_unavailable(monkeypatch) -> None:
    import host_brain.persona.inner_world as inner_world_module

    monkeypatch.setattr(
        inner_world_module,
        "_generate_inner_stream_note_from_llm",
        lambda **_kwargs: ("", "", "INNER_WORLD_LLM_UNAVAILABLE"),
    )
    state = InnerWorldState(user_id="u6", anticipation_topics=["我们上次聊到的书单"])
    note, focus, source, reason = _build_inner_stream_event_content(
        state=state,
        absence_band="recent",
        offline_hours=0.2,
        now_ms=1775633200000,
    )
    assert bool(note)
    assert focus in {"self", "state", "plan", "topic"}
    assert source == "fallback"
    assert reason == "INNER_WORLD_LLM_UNAVAILABLE"
