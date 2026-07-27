from __future__ import annotations

import pytest

from host_brain.contracts.skill_execution import SkillExecutionPolicy
from host_brain.scheduler.scheduling_center import (
    build_default_scheduler_registries,
    build_personality_scheduler_seed_task_configs,
    build_registry_driven_scheduling_plan,
    build_scheduler_advisory_prompt,
    get_scheduled_task_config_by_task_id,
    parse_scheduler_advisory,
    scheduler_seed_task_ids,
)
from host_brain.scheduler.task_registry import TaskPriority, TriggerMode
from host_brain.skills.registry import SkillMeta, SkillRegistry


def _noop_handler(args: dict, ctx: dict | None = None) -> dict:
    return {"status": "OK", "args": args, "ctx": ctx}


def test_scheduling_center_builds_registry_driven_seed_tasks(monkeypatch) -> None:
    monkeypatch.delenv("HOST_LIGHTNOVEL_RECOMMENDER_HOUR", raising=False)
    monkeypatch.delenv("HOST_LIGHTNOVEL_RECOMMENDER_MINUTE", raising=False)
    monkeypatch.delenv("HOST_DIARY_WRITER_HOUR", raising=False)
    monkeypatch.delenv("HOST_DIARY_WRITER_MINUTE", raising=False)
    monkeypatch.delenv("HOST_DREAM_GENERATOR_HOUR", raising=False)
    monkeypatch.delenv("HOST_DREAM_GENERATOR_MINUTE", raising=False)

    task_ids = scheduler_seed_task_ids()

    assert task_ids == [
        "life_tick",
        "evolution_cycle_daily",
        "life_memory_sleep_replay_daily",
    ]
    assert task_ids.count("life_tick") == 1
    assert {
        "inner_world_tick",
        "inner_world_numeric_tick",
        "inner_world_monologue_tick",
        "silence_tick",
        "body_sense_tick",
        "emotion_decay_tick",
        "proactive_talk_periodic",
    }.isdisjoint(task_ids)

    configs = {task.task_id: task for task in build_personality_scheduler_seed_task_configs()}
    assert configs["life_tick"].intent_template == {
        "skill_name": "life_tick",
        "args": {"person_id": "default", "timezone": "Asia/Shanghai"},
        "proposer_node_id": "scheduler",
    }
    assert configs["life_tick"].trigger_mode is TriggerMode.INTERVAL
    assert configs["life_tick"].resource_profile["periodic_authority"] == "scheduler_clock"
    assert configs["evolution_cycle_daily"].priority is TaskPriority.MEDIUM
    assert configs["evolution_cycle_daily"].trigger_config == {"hour": 3, "minute": 20, "catch_up_window_minutes": 720}
    assert configs["life_memory_sleep_replay_daily"].resource_profile["resource_class"] == "maintenance"
    assert configs["life_memory_sleep_replay_daily"].resource_profile["uses_llm"] is False
    assert configs["life_tick"].resource_profile["resource_class"] == "light_tick"


def test_scheduling_center_resolves_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_HOUR", "25")
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_MINUTE", "-3")
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_URLS", "https://example.test/a, https://example.test/b")
    monkeypatch.setenv("HOST_DREAM_GENERATOR_IDLE_THRESHOLD_MINUTES", "5")
    monkeypatch.setenv("HOST_DREAM_GENERATOR_MAX_EPISODIC_ENTRIES", "7")

    with pytest.raises(KeyError, match="lightnovel_recommender_daily"):
        get_scheduled_task_config_by_task_id(
            "lightnovel_recommender_daily"
        )
    with pytest.raises(KeyError, match="dream_generator_sleep"):
        get_scheduled_task_config_by_task_id("dream_generator_sleep")

    plan = build_registry_driven_scheduling_plan(
        registries=build_default_scheduler_registries(),
        include_system_tasks=False,
    )
    skipped = {item.skill_name: item.reason for item in plan.skipped}
    assert skipped["agent_lightnovel_recommender"] == (
        "skill requires a typed post-Acceptance effect"
    )
    assert skipped["dream_generator"] == (
        "skill requires a typed post-Acceptance effect"
    )


def test_scheduling_center_keeps_high_risk_auto_schedule_as_advisory() -> None:
    registry = SkillRegistry()
    registry.register(
        SkillMeta(
            skill_name="dangerous_auto_skill",
            clearance="HIGH",
            risk_level=4,
            scope="system",
            reversible=False,
            require_confirm=True,
            handler=_noop_handler,
            scheduler_allowed=True,
            scheduler_profile={
                "auto_schedule": True,
                "task_id": "dangerous_auto_skill_periodic",
                "trigger_mode": "interval",
                "trigger_config": {"seconds": 60},
            },
            execution_policy=SkillExecutionPolicy.INLINE_READ_ONLY,
        )
    )

    plan = build_registry_driven_scheduling_plan(
        registries={"test": registry},
        include_system_tasks=False,
    )

    assert plan.tasks == ()
    assert plan.skipped[0].skill_name == "dangerous_auto_skill"
    assert "high-risk" in plan.skipped[0].reason


def test_scheduling_center_advisory_prompt_and_parser() -> None:
    prompt = build_scheduler_advisory_prompt(registries=build_default_scheduler_registries())
    advisories = parse_scheduler_advisory(
        {
            "advisories": [
                {
                    "skill_name": "diary_writer",
                    "decision": "keep",
                    "reason": "daily memory consolidation",
                    "proposed_profile": {"task_id": "diary_writer_daily"},
                }
            ]
        }
    )

    assert "SchedulingCenter advisory" in prompt
    assert "diary_writer_daily" in prompt
    assert advisories[0].skill_name == "diary_writer"
    assert advisories[0].proposed_profile["task_id"] == "diary_writer_daily"
