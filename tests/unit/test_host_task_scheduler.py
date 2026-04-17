from __future__ import annotations

import asyncio

import pytest

from host_brain.app.automation.task_scheduler import HostTaskScheduler
from host_brain.app.automation.task_scheduler import ScheduledTask


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
