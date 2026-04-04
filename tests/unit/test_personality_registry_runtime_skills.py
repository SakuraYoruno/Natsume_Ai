from __future__ import annotations

from host_brain.skills.personality_registry import build_personality_skill_registry


def test_build_personality_skill_registry_includes_runtime_skills() -> None:
    registry = build_personality_skill_registry()

    for skill_name in (
        "inner_world_tick",
        "inner_world_awaken",
        "inner_world_session_end",
        "silence_tick",
        "silence_update_emotion",
        "silence_add_resistance",
        "body_sense_tick",
        "body_sense_conversation_start",
        "body_sense_conversation_end",
        "body_sense_update_loneliness",
        "gaming_companion_tick",
        "emergent_growth_weekly",
        "emergent_growth_status",
    ):
        assert registry.get(skill_name) is not None
