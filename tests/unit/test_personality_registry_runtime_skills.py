from __future__ import annotations

from host_brain.skills.personality_registry import build_personality_skill_registry


def test_build_personality_skill_registry_includes_runtime_skills() -> None:
    registry = build_personality_skill_registry()

    assert registry.get("inner_world_tick") is None
    for skill_name in (
        "inner_world_numeric_tick",
        "inner_world_monologue_tick",
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

    weekly = registry.get("emergent_growth_weekly")
    assert weekly is not None
    assert weekly.scheduler_allowed is False
    assert "兼容入口" in weekly.description

    for skill_name in ("inner_world_numeric_tick", "inner_world_monologue_tick"):
        skill = registry.get(skill_name)
        assert skill is not None
        assert skill.scheduler_allowed is False

    for skill_name in ("silence_tick", "body_sense_tick"):
        skill = registry.get(skill_name)
        assert skill is not None
        assert skill.scheduler_allowed is False
        assert skill.scheduler_profile is None
