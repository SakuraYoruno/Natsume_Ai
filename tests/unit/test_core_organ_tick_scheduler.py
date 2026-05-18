from __future__ import annotations

import logging


def test_continuous_organ_ticks_default_on_even_when_heavy_background_disabled(monkeypatch) -> None:
    from host_brain.app.http_hooks import _resolve_continuous_organ_ticks_enabled

    monkeypatch.delenv("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", raising=False)

    assert _resolve_continuous_organ_ticks_enabled(background_tasks_enabled=False) is True


def test_continuous_organ_ticks_can_be_explicitly_disabled(monkeypatch) -> None:
    from host_brain.app.http_hooks import _resolve_continuous_organ_ticks_enabled

    monkeypatch.setenv("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", "false")

    assert _resolve_continuous_organ_ticks_enabled(background_tasks_enabled=True) is False


def test_bootstrap_core_organ_tick_scheduler_registers_only_core_ticks(monkeypatch) -> None:
    from host_brain.app.http_hooks import _bootstrap_core_organ_tick_scheduler
    from host_brain.scheduler import scheduler_routes
    from host_brain.scheduler.task_registry import SchedulerTaskRegistry

    old_registry_provider = scheduler_routes._registry
    old_health_provider = scheduler_routes._health_reporter
    monkeypatch.setattr(SchedulerTaskRegistry, "_instance", None)

    started = {"count": 0}

    def _fake_start(self) -> None:  # noqa: ANN001
        started["count"] += 1

    monkeypatch.setattr("host_brain.scheduler.scheduler.NatsumeScheduler.start", _fake_start)

    try:
        statuses = _bootstrap_core_organ_tick_scheduler(
            logger=logging.getLogger("test"),
            get_ledger=lambda: None,
            retention_policy=None,
        )
        registry = SchedulerTaskRegistry.get_instance()

        assert started["count"] == 1
        assert statuses["organ_tick_scheduler"].startswith("OK")
        assert registry.get("inner_world_numeric_tick") is not None
        assert registry.get("inner_world_monologue_tick") is not None
        assert registry.get("silence_tick") is not None
        assert registry.get("body_sense_tick") is not None
        assert registry.get("emotion_decay_tick") is not None
        assert registry.get("proactive_talk_periodic") is None
        assert registry.get("long_term_memory_distillation") is None
        assert registry.get("gaming_companion_tick") is None
        assert registry.get("emergent_growth_weekly") is None
    finally:
        scheduler_routes._registry = old_registry_provider
        scheduler_routes._health_reporter = old_health_provider


def test_background_runtime_core_ticks_include_emotion_decay(monkeypatch) -> None:
    from host_brain.app.http_hooks_runtime_helpers import _build_background_runtime_snapshot
    from host_brain.core.ids import now_ms

    ts_ms = now_ms()
    task_ids = [
        "inner_world_numeric_tick",
        "inner_world_monologue_tick",
        "silence_tick",
        "body_sense_tick",
        "emotion_decay_tick",
    ]

    def _fake_scheduler_health() -> dict:
        return {
            "scheduler_status": "HEALTHY",
            "total_tasks": len(task_ids),
            "enabled_tasks": len(task_ids),
            "suspended_tasks": 0,
            "error_tasks": 0,
            "tasks": [
                {
                    "task_id": task_id,
                    "trigger_mode": "interval",
                    "trigger_config": {"seconds": 60},
                    "priority": "LOW",
                    "enabled": True,
                    "status": "OK",
                    "last_run_ts": ts_ms,
                }
                for task_id in task_ids
            ],
            "ts_ms": ts_ms,
        }

    monkeypatch.setattr(
        "host_brain.scheduler.scheduler_routes.get_scheduler_health_snapshot_safe",
        _fake_scheduler_health,
    )

    snapshot = _build_background_runtime_snapshot(
        now_ms=ts_ms,
        persona_user_id="user_1",
        inner_world_state={"user_id": "user_1", "last_tick_ts_ms": ts_ms},
    )

    assert snapshot["core_ticks_registered"] is True
    assert snapshot["core_ticks_missing"] == []
    assert "emotion_decay_tick" in snapshot["core_tick_ids"]
    assert snapshot["core_tick_tasks"]["emotion_decay_tick"]["enabled"] is True
    assert snapshot["is_advancing"] is True


def test_background_runtime_uses_scheduler_tick_when_inner_world_state_is_stale() -> None:
    from host_brain.app.http_hooks_runtime_helpers import _build_background_runtime_snapshot
    from host_brain.core.ids import now_ms

    ts_ms = now_ms()
    task_ids = [
        "inner_world_numeric_tick",
        "inner_world_monologue_tick",
        "silence_tick",
        "body_sense_tick",
        "emotion_decay_tick",
    ]
    scheduler_snapshot = {
        "scheduler_status": "HEALTHY",
        "total_tasks": len(task_ids),
        "enabled_tasks": len(task_ids),
        "suspended_tasks": 0,
        "error_tasks": 0,
        "tasks": [
            {
                "task_id": task_id,
                "trigger_mode": "interval",
                "trigger_config": {"seconds": 60},
                "priority": "LOW",
                "enabled": True,
                "status": "OK",
                "last_run_ts": ts_ms,
            }
            for task_id in task_ids
        ],
        "ts_ms": ts_ms,
    }

    snapshot = _build_background_runtime_snapshot(
        now_ms=ts_ms,
        persona_user_id="user_1",
        inner_world_state={"user_id": "user_1", "last_tick_ts_ms": ts_ms - 600_000},
        scheduler_snapshot_override=scheduler_snapshot,
    )

    assert snapshot["last_inner_world_tick_ts_ms"] == ts_ms
    assert snapshot["tick_health"] == "fresh"
    assert snapshot["is_advancing"] is True


def test_obs_organ_status_reads_host_scheduler_first(monkeypatch) -> None:
    from host_brain.app.obs_routes_health import _resolve_scheduler_task_for_obs

    monkeypatch.setattr(
        "host_brain.app.obs_routes_health.fetch_host_json",
        lambda *_args, **_kwargs: {
            "scheduler_status": "HEALTHY",
            "tasks": [
                {
                    "task_id": "emotion_decay_tick",
                    "enabled": True,
                    "status": "OK",
                }
            ],
        },
    )

    task = _resolve_scheduler_task_for_obs("emotion_decay_tick")

    assert task is not None
    assert task["enabled"] is True
