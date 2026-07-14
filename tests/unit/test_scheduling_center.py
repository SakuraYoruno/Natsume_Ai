from __future__ import annotations

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
        "lightnovel_recommender_daily",
        "diary_writer_daily",
        "dream_generator_sleep",
        "life_memory_sleep_replay_daily",
        "gaming_companion_tick",
        "emergent_growth_weekly",
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
    assert configs["emergent_growth_weekly"].trigger_config == {
        "day_of_week": 6,
        "hour": 3,
        "minute": 50,
        "catch_up_window_minutes": 1440,
    }
    assert configs["lightnovel_recommender_daily"].trigger_mode is TriggerMode.CRON
    assert configs["lightnovel_recommender_daily"].resource_profile["resource_class"] == "browser_heavy"
    assert configs["lightnovel_recommender_daily"].resource_profile["uses_llm"] is True
    assert configs["life_memory_sleep_replay_daily"].resource_profile["resource_class"] == "maintenance"
    assert configs["life_memory_sleep_replay_daily"].resource_profile["uses_llm"] is False
    assert configs["gaming_companion_tick"].resource_profile["resource_class"] == "gpu_heavy"
    assert configs["gaming_companion_tick"].resource_profile["uses_gpu"] is True
    assert configs["life_tick"].resource_profile["resource_class"] == "light_tick"


def test_scheduling_center_resolves_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_HOUR", "25")
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_MINUTE", "-3")
    monkeypatch.setenv("HOST_LIGHTNOVEL_RECOMMENDER_URLS", "https://example.test/a, https://example.test/b")
    monkeypatch.setenv("HOST_DREAM_GENERATOR_IDLE_THRESHOLD_MINUTES", "5")
    monkeypatch.setenv("HOST_DREAM_GENERATOR_MAX_EPISODIC_ENTRIES", "7")

    recommender = get_scheduled_task_config_by_task_id("lightnovel_recommender_daily")
    dream = get_scheduled_task_config_by_task_id("dream_generator_sleep")

    assert recommender.trigger_config == {"hour": 23, "minute": 0, "catch_up_window_minutes": 180}
    assert recommender.intent_template["args"]["urls"] == [
        "https://example.test/a",
        "https://example.test/b",
    ]
    assert dream.intent_template["args"]["max_episodic_entries"] == 7
    assert dream.intent_template["args"]["idle_threshold_ms"] == 5 * 60 * 1000


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
