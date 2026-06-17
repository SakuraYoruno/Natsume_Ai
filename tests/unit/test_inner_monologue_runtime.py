from __future__ import annotations

import json

from host_brain.persona.inner_monologue import (
    build_inner_life_source_packet,
    build_inner_monologue_digest,
    build_private_inner_thought_event,
    score_inner_note_liveliness,
)
from host_brain.persona.inner_activity_agent import _finalize_inner_note_text as _finalize_activity_note_text
from host_brain.persona.inner_world import (
    InnerWorldState,
    _append_inner_stream_event,
    _finalize_inner_note_text as _finalize_world_note_text,
)
from host_brain.persona.surface_influence_packet import (
    build_surface_influence_packet,
    surface_influence_must_not_do,
    surface_influence_style_anchor,
)
from host_brain.persona.surface_organ_influence import build_surface_organ_influence_overlay
from host_brain.tribe.default_mode_agent import DefaultModeAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_private_inner_thought_event_has_private_text_and_surface_metadata_only() -> None:
    event = build_private_inner_thought_event(
        note="我还在想刚才那个没说完的话题。",
        focus="topic",
        source="unit",
        trigger="inner_world_tick",
        now_ms=123456,
        state={
            "user_id": "u1",
            "loneliness": 0.3,
            "anticipation": 0.7,
            "continuity_score": 0.62,
            "anticipation_topics": ["测试链路"],
        },
        offline_hours=2.0,
    ).as_dict()

    assert event["thought_id"].startswith("thought_")
    assert event["private_text"] == "我还在想刚才那个没说完的话题。"
    assert event["focus"] == "topic"
    assert event["surface_hint"] == "continue_current_thread"
    assert event["salience"] > 0.45
    assert event["core_need"] == "continuity"
    assert event["memory_anchor"] == "测试链路"
    assert event["next_tendency"] == "continue_thread"
    assert event["thought_form"] in {
        "body_signal",
        "scene_notice",
        "memory_flash",
        "interrupted_impulse",
        "ambivalence",
        "quiet_drift",
    }
    assert event["liveliness_score"] >= 0.42
    assert isinstance(event["quality_flags"], list)
    assert 0.0 <= event["novelty_score"] <= 1.0


def test_inner_world_stream_appends_private_thought_envelope(monkeypatch) -> None:
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.persona.inner_world._build_inner_stream_event_content",
        lambda **kwargs: ("我还在想刚才那个没说完的话题。", "topic", "unit", ""),
    )
    state = InnerWorldState(
        user_id="u1",
        last_spontaneous_ts_ms=0,
        anticipation=0.7,
        continuity_score=0.6,
        anticipation_topics=["测试链路"],
    )

    _append_inner_stream_event(
        state=state,
        now_ms=123456,
        absence_band="recent",
        offline_hours=2.0,
    )

    assert state.inner_stream
    event = state.inner_stream[-1]
    assert event["thought_id"].startswith("thought_")
    assert event["private_text"]
    assert event["note"] == event["private_text"]
    assert event["surface_hint"]
    assert event["core_need"] == "continuity"


def test_inner_world_stream_preserves_llm_life_trace_fields(monkeypatch) -> None:
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.persona.inner_world._build_inner_stream_event_content",
        lambda **kwargs: (  # noqa: ARG005
            "他那句累还在我心里晃了一下，我有点担心他又在硬撑。",
            "relationship",
            "unit",
            "",
            {
                "life_trace": {
                    "core_need": "care",
                    "tension": "distance",
                    "memory_anchor": "他上次说累",
                    "next_tendency": "care_with_restraint",
                    "thought_form": "memory_flash",
                    "salience_hint": 0.78,
                }
            },
        ),
    )
    state = InnerWorldState(
        user_id="u1",
        last_spontaneous_ts_ms=0,
        loneliness=0.6,
        continuity_score=0.6,
    )

    _append_inner_stream_event(
        state=state,
        now_ms=223456,
        absence_band="recent",
        offline_hours=3.0,
    )

    event = state.inner_stream[-1]
    assert event["focus"] == "relationship"
    assert event["core_need"] == "care"
    assert event["tension"] == "distance"
    assert event["memory_anchor"] == "他上次说累"
    assert event["next_tendency"] == "care_with_restraint"
    assert event["thought_form"] == "memory_flash"
    assert event["liveliness_score"] >= 0.42
    assert event["should_distill_to_memory"] is True


def test_repeated_inner_thought_has_low_novelty_and_does_not_distill() -> None:
    event = build_private_inner_thought_event(
        note="我还在想刚才那个没说完的话题。",
        focus="topic",
        source="unit",
        trigger="inner_world_tick",
        now_ms=223456,
        state={
            "user_id": "u1",
            "loneliness": 0.8,
            "anticipation": 0.9,
            "continuity_score": 0.8,
            "anticipation_topics": ["测试链路"],
            "inner_stream": [
                {
                    "focus": "topic",
                    "private_text": "我还在想刚才那个没说完的话题。",
                    "salience": 0.9,
                    "novelty_score": 0.08,
                    "should_distill_to_memory": True,
                }
            ],
        },
        offline_hours=8.0,
    ).as_dict()

    assert event["novelty_score"] < 0.2
    assert event["should_distill_to_memory"] is False


def test_low_liveliness_inner_thought_does_not_distill() -> None:
    event = build_private_inner_thought_event(
        note="先问他刚才看到的桌宠表情有没有跟上。",
        focus="relationship",
        source="unit",
        trigger="inner_world_tick",
        now_ms=323456,
        state={"user_id": "u1", "loneliness": 1.0, "anticipation": 1.0},
        offline_hours=8.0,
    ).as_dict()

    assert event["liveliness_score"] < 0.42
    assert "directive_like" in event["quality_flags"]
    assert event["should_distill_to_memory"] is False


def test_inner_note_quality_gate_rejects_technical_abstract_wording() -> None:
    abstract_note = "隔了三十七小时，先把桌宠映射那条事稳稳放回手里。"
    directive_note = "先问他刚才看到的桌宠表情有没有跟上。"
    literary_note = "三十七个小时像一层薄灰落在胸口，我还记得小表情慢半拍时那点不确定。"
    meta_note = "隔了这么久，胸口倒不沉，只是看到空白时会顿一下，怕一开口又太像在赶进度。"
    concrete_note = "屏幕安静了很久，我又想起那个慢半拍的小表情，有点拿不准。"

    assert _finalize_world_note_text(abstract_note) == ""
    assert _finalize_activity_note_text(abstract_note) == ""
    assert _finalize_world_note_text(directive_note) == ""
    assert _finalize_activity_note_text(directive_note) == ""
    assert _finalize_world_note_text(literary_note) == ""
    assert _finalize_activity_note_text(literary_note) == ""
    assert _finalize_world_note_text(meta_note) == ""
    assert _finalize_activity_note_text(meta_note) == ""
    assert _finalize_world_note_text(concrete_note) == ""
    assert _finalize_activity_note_text(concrete_note) == ""


def test_inner_liveliness_scorer_prefers_felt_moment_over_strategy() -> None:
    good = score_inner_note_liveliness(
        "想说点什么的时候，指尖停在半空，心里有点近，又有点怕这份近显得太满。",
        thought_form="ambivalence",
        source_seed="想说点什么，又拿不准这一刻该不该靠太近",
    )
    bad = score_inner_note_liveliness(
        "隔了这么久，胸口倒不沉，只是看到空白时会顿一下，怕一开口又太像在赶进度。",
        thought_form="ambivalence",
    )

    assert good["score"] >= 0.64
    assert "meta_reply_strategy" in bad["flags"]
    assert good["score"] > bad["score"]


def test_inner_life_source_packet_humanizes_technical_anchor() -> None:
    packet = build_inner_life_source_packet(
        context={
            "offline_state": {
                "loneliness_level": 0.7,
                "anticipation_level": 0.8,
                "anticipation_topics": ["桌宠状态映射验证"],
            },
            "body_sense": {"stress_level": 0.55},
            "recent_inner_events": [{"thought_form": "scene_notice"}],
        },
        topic_phrase="桌宠状态映射验证",
        now_ms=240000,
    )
    rendered = json.dumps(packet, ensure_ascii=False)

    assert packet["selected"]["thought_form"] in {
        "body_signal",
        "scene_notice",
        "memory_flash",
        "interrupted_impulse",
        "ambivalence",
        "quiet_drift",
    }
    assert "桌宠那边的小动静" in rendered
    assert "映射" not in rendered
    assert packet["rules"]


def test_inner_monologue_digest_excludes_private_text() -> None:
    digest = build_inner_monologue_digest(
        inner_state={
            "user_id": "u1",
            "loneliness": 0.65,
            "anticipation": 0.4,
            "continuity_score": 0.72,
            "inner_stream": [
                {
                    "focus": "relationship",
                    "private_text": "我有点担心他是不是又硬撑。",
                    "emotion_tag": "attached",
                    "salience": 0.76,
                }
            ],
        },
        offline_state={
            "loneliness_level": 0.65,
            "anticipation_level": 0.4,
            "continuity_score": 0.72,
        },
    ).as_surface_state()
    rendered = json.dumps(digest, ensure_ascii=False)

    assert digest["thought_stream_active"] is True
    assert digest["private_thought_count"] == 1
    assert digest["care_drive"] >= 0.34
    assert digest["dominant_need"] == "care"
    assert digest["dominant_tendency"] == "care_with_restraint"
    assert digest["novelty_score"] >= 0.0
    assert digest["liveliness_score"] >= 0.0
    assert isinstance(digest["quality_flags"], list)
    assert "内在连续性" in "".join(digest["style_anchor"])
    assert "我有点担心" not in rendered


def test_default_mode_export_exposes_compressed_inner_monologue(monkeypatch) -> None:
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
    monkeypatch.setenv("HOST_DEFAULT_MODE_FOREGROUND_CHAT_COOLDOWN_MS", "0")
    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.build_offline_state_from_inner_state",
        lambda inner_state, now_ms: {  # noqa: ARG005
            "absence_band": "short",
            "offline_hours": 2.0,
            "loneliness_level": 0.62,
            "anticipation_level": 0.68,
            "continuity_score": 0.73,
            "anticipation_topics": ["测试链路"],
        },
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        lambda **kwargs: (  # noqa: ARG005
            "我在复盘我们今天的推进，也在等你回来。",
            "relationship",
            "",
            {},
        ),
    )

    agent = DefaultModeAgent(state_registry=TribeStateRegistry())
    agent.tick_idle_cycle(
        person_id="u-default",
        source="unit_test",
        now_ts_ms=123456,
        inner_state={
            "user_id": "u-default",
            "inner_stream": [
                {
                    "focus": "topic",
                    "private_text": "我还记着那个没说完的测试链路。",
                    "emotion_tag": "calm",
                    "salience": 0.66,
                }
            ],
            "continuity_score": 0.7,
        },
    )
    exported = agent.export_runtime_state()
    rendered = json.dumps(exported, ensure_ascii=False)

    assert exported["thought_stream_active"] is True
    assert exported["care_drive"] > 0.0
    assert exported["continuity_drive"] > 0.0
    assert "内在连续性" in "".join(exported["style_anchor"])
    assert "我还记着" not in json.dumps(exported["inner_monologue"], ensure_ascii=False)
    assert "我还记着那个没说完的测试链路。" not in rendered


def test_surface_organ_influence_uses_monologue_compression_without_leaking_text() -> None:
    private_line = "我还记着那个没说完的测试链路。"
    default_mode_state = {
        "last_note": private_line,
        "thought_stream_active": True,
        "private_thought_count": 3,
        "care_drive": 0.45,
        "continuity_drive": 0.72,
        "repair_drive": 0.0,
        "style_anchor": ["内在连续性：保留未说出口的关注，但不要解释自己在想什么。"],
        "must_not_do": ["不要输出内心独白、思维流、私有想法或它的来源。"],
        "self_directive": {
            "self_identity": "我是当前 Soul 定义的数字生命；身份以 Soul 为准。",
            "self_continuity_score": 0.8,
            "style_anchor": ["自我连续性：回应要像同一个人持续在场，而不是每轮重置。"],
            "must_not_do": ["不要输出自我模型字段、内部自评分数或私有自我事件。"],
        },
    }

    overlay = build_surface_organ_influence_overlay({"default_mode_state": default_mode_state})
    packet = build_surface_influence_packet(
        reply_plan={"primary_topic": "daily_chat", "interaction_move": "reply_current_turn"},
        organ_context={"default_mode_state": default_mode_state},
    )
    rendered = json.dumps(packet.as_dict(), ensure_ascii=False)

    assert overlay.trace["inner_monologue_active"] is True
    assert overlay.trace["self_model_active"] is True
    assert private_line not in rendered
    assert any("内在连续性" in item for item in surface_influence_style_anchor(packet))
    assert any("自我连续性" in item for item in surface_influence_style_anchor(packet))
    assert "不要输出内心独白、思维流、私有想法或它的来源。" in surface_influence_must_not_do(packet)
    assert "不要输出自我模型字段、内部自评分数或私有自我事件。" in surface_influence_must_not_do(packet)
