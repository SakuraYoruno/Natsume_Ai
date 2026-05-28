from __future__ import annotations

import asyncio

import pytest

from host_brain.app.automation.task_scheduler import HostTaskScheduler
from host_brain.app.automation.task_scheduler import ScheduledTask
from host_brain.app.automation.task_scheduler import TaskPriority
from host_brain.app.automation.task_scheduler import TaskStatus


@pytest.mark.asyncio
async def test_host_task_scheduler_start_does_not_immediately_run_periodic_task() -> None:
    scheduler = HostTaskScheduler()
    calls: list[str] = []

    async def _task() -> None:
        calls.append("ran")

    scheduler.register(
        ScheduledTask(
            task_id="periodic_no_immediate_run",
            name="periodic_no_immediate_run",
            fn=_task,
            interval_s=30.0,
        )
    )

    await scheduler.start()
    await asyncio.sleep(1.2)
    await scheduler.stop()

    assert calls == []


@pytest.mark.asyncio
async def test_host_task_scheduler_trigger_now_still_runs_periodic_task() -> None:
    scheduler = HostTaskScheduler()
    calls: list[str] = []

    async def _task() -> None:
        calls.append("ran")

    scheduler.register(
        ScheduledTask(
            task_id="periodic_trigger_now",
            name="periodic_trigger_now",
            fn=_task,
            interval_s=30.0,
        )
    )

    await scheduler.start()
    assert scheduler.trigger_now("periodic_trigger_now") is True
    await asyncio.sleep(1.2)
    await scheduler.stop()

    assert calls == ["ran"]


def test_host_task_scheduler_defers_heavy_task_under_resource_pressure(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResourceManager:
        def assess_background_task(self, **kwargs):
            calls.append(kwargs)
            return {
                "allowed": False,
                "reason_code": "RESOURCE_PRESSURE_HIGH",
                "pressure_status": "HIGH",
                "retry_after_s": 120,
            }

    async def _task() -> None:
        raise AssertionError("resource gate should defer this task before execution")

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_resource_manager",
        lambda create_if_missing=False: FakeResourceManager(),
    )

    scheduler = HostTaskScheduler()
    task = ScheduledTask(
        task_id="distillation_engine_periodic",
        name="distillation_engine_periodic",
        fn=_task,
        interval_s=60.0,
        priority=TaskPriority.P3,
    )

    assert scheduler._resource_gate_allows(task, now=1000.0) is False  # noqa: SLF001
    assert task.status == TaskStatus.DEFERRED
    assert "RESOURCE_PRESSURE_HIGH" in str(task.last_error)
    assert task.last_run_ts > 1000.0
    assert calls[0]["uses_llm"] is True
