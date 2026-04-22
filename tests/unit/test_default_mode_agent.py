from __future__ import annotations

from unittest.mock import MagicMock

from host_brain.tribe.default_mode_agent import DefaultModeAgent, mark_recent_foreground_interaction
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_default_mode_agent_tick_and_export(monkeypatch) -> None:
    monkeypatch.setenv("HOST_DEFAULT_MODE_FOREGROUND_CHAT_COOLDOWN_MS", "0")
    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.build_offline_state_from_inner_state",
        lambda inner_state, now_ms: {  # noqa: ARG005
            "absence_band": "short",
            "offline_hours": 2.0,
            "loneliness_level": 0.62,
            "anticipation_level": 0.68,
            "continuity_score": 0.73,
            "anticipation_topics": ["一起完成的功能"],
        },
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        lambda **kwargs: (  # noqa: ARG005
            "我在复盘我们今天的推进，也在等你回来。",
            "reflection",
            "llm_ok",
            {"provider": "mock"},
        ),
    )

    agent = DefaultModeAgent(state_registry=TribeStateRegistry())
    tick = agent.tick_idle_cycle(person_id="u-default", topic_phrase="测试", source="unit_test", now_ts_ms=123456)
    exported = agent.export_runtime_state()

    assert tick["user_id"] == "u-default"
    assert tick["focus"] == "reflection"
    assert tick["proactive_voice_candidate"] is True
    assert tick["offline_state"]["continuity_score"] == 0.73
    assert exported["last_focus"] == "reflection"
    assert exported["tick_count"] >= 1
    assert exported["continuity_score"] == 0.73


def test_default_mode_agent_suppresses_inner_activity_during_recent_foreground_chat(monkeypatch) -> None:
    monkeypatch.setenv("HOST_DEFAULT_MODE_FOREGROUND_CHAT_COOLDOWN_MS", "15000")
    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.build_offline_state_from_inner_state",
        lambda inner_state, now_ms: {  # noqa: ARG005
            "absence_band": "recent",
            "offline_hours": 0.5,
            "loneliness_level": 0.12,
            "anticipation_level": 0.2,
            "continuity_score": 0.51,
            "anticipation_topics": [],
        },
    )
    called = {"count": 0}

    def _forbidden_generate(**kwargs):  # noqa: ANN003, ARG001
        called["count"] += 1
        return "不该被调用", "self", "", {}

    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        _forbidden_generate,
    )
    mark_recent_foreground_interaction(ts_ms=123400)

    agent = DefaultModeAgent(state_registry=TribeStateRegistry())
    tick = agent.tick_idle_cycle(
        person_id="u-foreground",
        topic_phrase="",
        source="conversation_ended",
        now_ts_ms=123456,
    )

    assert called["count"] == 0
    assert tick["reason_code"] == "DEFAULT_MODE_FOREGROUND_CHAT_COOLDOWN"
    assert tick["llm_meta"]["suppressed"] is True


def test_default_mode_agent_suppresses_background_side_effects_during_recent_foreground_chat(monkeypatch) -> None:
    monkeypatch.setenv("HOST_DEFAULT_MODE_FOREGROUND_CHAT_COOLDOWN_MS", "15000")
    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.build_offline_state_from_inner_state",
        lambda inner_state, now_ms: {  # noqa: ARG005
            "absence_band": "recent",
            "offline_hours": 0.5,
            "loneliness_level": 0.12,
            "anticipation_level": 0.2,
            "continuity_score": 0.66,
            "anticipation_topics": ["一起完成的功能"],
        },
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        lambda **kwargs: ("不该被调用", "self", "", {}),  # noqa: ARG005
    )
    mock_emit = MagicMock()
    mock_glymphatic = MagicMock()
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.emit_tribe_event", mock_emit)
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.get_glymphatic_agent", lambda: mock_glymphatic)
    mark_recent_foreground_interaction(ts_ms=123400)

    agent = DefaultModeAgent(state_registry=TribeStateRegistry())
    tick = agent.tick_idle_cycle(
        person_id="u-foreground",
        topic_phrase="",
        source="inner_world_ticked",
        now_ts_ms=123456,
    )

    assert tick["background_side_effects_suppressed"] is True
    assert tick["llm_meta"]["background_side_effects_suppressed"] is True
    mock_emit.assert_not_called()
    mock_glymphatic.run_recovery_cycle.assert_not_called()


def test_default_mode_agent_suppresses_recursive_glymphatic_side_effects(monkeypatch) -> None:
    monkeypatch.setenv("HOST_DEFAULT_MODE_FOREGROUND_CHAT_COOLDOWN_MS", "0")
    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.build_offline_state_from_inner_state",
        lambda inner_state, now_ms: {  # noqa: ARG005
            "absence_band": "recent",
            "offline_hours": 0.5,
            "loneliness_level": 0.2,
            "anticipation_level": 0.2,
            "continuity_score": 0.76,
            "anticipation_topics": ["一起完成的功能"],
        },
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        lambda **kwargs: ("我在整理刚才那股恢复后的清醒感。", "self", "llm_ok", {}),  # noqa: ARG005
    )
    mock_emit = MagicMock()
    mock_glymphatic = MagicMock()
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.emit_tribe_event", mock_emit)
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.get_glymphatic_agent", lambda: mock_glymphatic)

    agent = DefaultModeAgent(state_registry=TribeStateRegistry())
    tick = agent.tick_idle_cycle(
        person_id="u-glymphatic",
        topic_phrase="",
        source="glymphatic_deep_clean:score=0.741",
        now_ts_ms=123456,
    )

    assert tick["background_side_effects_suppressed"] is True
    assert tick["reason_code"] == "DEFAULT_MODE_GLYMPHATIC_RECURSION_SUPPRESSED"
    assert tick["llm_meta"]["background_side_effects_suppressed"] is True
    assert tick["llm_meta"]["recursive_glymphatic_source"] is True
    mock_emit.assert_not_called()
    mock_glymphatic.run_recovery_cycle.assert_not_called()
