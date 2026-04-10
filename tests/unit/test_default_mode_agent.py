from __future__ import annotations

from host_brain.tribe.default_mode_agent import DefaultModeAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_default_mode_agent_tick_and_export(monkeypatch) -> None:
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
