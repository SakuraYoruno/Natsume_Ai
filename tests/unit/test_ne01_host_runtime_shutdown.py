from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app.host_runtime_shutdown import (
    HostRuntimeShutdownError,
    shutdown_host_runtime_authority,
)


class _ObsAdmission:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    async def aclose(self) -> None:
        self.events.append("obs:drain")
        if self.fail:
            raise RuntimeError("obs drain failed")


class _Security:
    def __init__(self, events: list[str], *, obs_fail: bool = False) -> None:
        self.events = events
        self.obs_command_composition = _ObsAdmission(events, fail=obs_fail)

    async def aclose(self) -> None:
        self.events.append("security:close")


class _Scheduler:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def stop_clock(self) -> None:
        self.events.append("scheduler:stop")


class _TaskRuntime:
    def __init__(
        self,
        events: list[str],
        *,
        activated: bool,
        fail: bool = False,
    ) -> None:
        self.events = events
        self.activated = activated
        self.fail = fail

    async def aclose(self) -> None:
        self.events.append("task-runtime:close")
        if self.fail:
            raise RuntimeError("task runtime close failed")


class _HostLifecycle:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def stop_activity_monitor(self, *, logger: Any) -> None:
        assert logger.name == "test.ne01.host-runtime-shutdown"
        self.events.append("hard-state:close")


class _SyncResource:
    def __init__(self, events: list[str], name: str) -> None:
        self.events = events
        self.name = name

    def close(self) -> None:
        self.events.append(f"{self.name}:close")


class _ToolComposition:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def clear(self) -> None:
        self.events.append("tools:clear")


def _shutdown_kwargs(
    *,
    obs_fail: bool = False,
    task_live: bool = True,
    task_fail: bool = False,
) -> tuple[dict[str, Any], SimpleNamespace, _TaskRuntime, _Security, list[str]]:
    events: list[str] = []
    task_runtime = _TaskRuntime(
        events,
        activated=task_live,
        fail=task_fail,
    )
    security = _Security(events, obs_fail=obs_fail)
    memory = _SyncResource(events, "memory")
    application = SimpleNamespace(
        state=SimpleNamespace(
            task_runtime_container=task_runtime if task_live else None,
            memory_service_container=memory,
            security_service_container=security,
        )
    )
    kwargs = {
        "application": application,
        "security": security,
        "scheduler": _Scheduler(events),
        "task_runtime": task_runtime,
        "agent_workspace_container": object(),
        "workspace_quiesce": (
            lambda *_args: events.append("workspace:close")
        ),
        "host_lifecycle": _HostLifecycle(events),
        "logger": logging.getLogger("test.ne01.host-runtime-shutdown"),
        "embodiment_authority_task": None,
        "reset_life_command_authority": (
            lambda: events.append("life-authority:close")
        ),
        "embodiment_runtime": _SyncResource(events, "embodiment"),
        "set_memory_service_container": (
            lambda value: events.append(f"memory:publish:{value}")
        ),
        "memory": memory,
        "command_ingress": SimpleNamespace(
            aclose=lambda: _async_event(events, "command-ingress:close")
        ),
        "tool_composition": _ToolComposition(events),
        "on_dependencies_released": (
            lambda: events.append("process-refs:withdraw")
        ),
    }
    return kwargs, application, task_runtime, security, events


async def _async_event(events: list[str], value: str) -> None:
    events.append(value)


@pytest.mark.asyncio
async def test_obs_drain_failure_preserves_live_runtime_and_dependencies() -> None:
    kwargs, application, task_runtime, security, events = _shutdown_kwargs(
        obs_fail=True,
    )

    with pytest.raises(HostRuntimeShutdownError) as raised:
        await shutdown_host_runtime_authority(**kwargs)

    assert raised.value.dependencies_preserved is True
    assert events == ["obs:drain"]
    assert application.state.task_runtime_container is task_runtime
    assert application.state.security_service_container is security
    assert application.state.memory_service_container is kwargs["memory"]
    assert application.state.host_runtime_shutdown_error["retry_required"] is True


@pytest.mark.asyncio
async def test_task_runtime_close_failure_keeps_refs_and_hard_state_authority() -> None:
    kwargs, application, task_runtime, security, events = _shutdown_kwargs(
        task_fail=True,
    )

    with pytest.raises(HostRuntimeShutdownError) as raised:
        await shutdown_host_runtime_authority(**kwargs)

    assert raised.value.dependencies_preserved is True
    assert events == ["obs:drain", "scheduler:stop", "task-runtime:close"]
    assert "hard-state:close" not in events
    assert application.state.task_runtime_container is task_runtime
    assert application.state.security_service_container is security
    assert application.state.memory_service_container is kwargs["memory"]


@pytest.mark.asyncio
async def test_partial_startup_failure_releases_every_constructed_resource() -> None:
    kwargs, application, _task_runtime, _security, events = _shutdown_kwargs(
        obs_fail=True,
        task_live=False,
        task_fail=True,
    )

    with pytest.raises(HostRuntimeShutdownError) as raised:
        await shutdown_host_runtime_authority(**kwargs)

    assert raised.value.dependencies_preserved is False
    assert events == [
        "obs:drain",
        "scheduler:stop",
        "task-runtime:close",
        "workspace:close",
        "hard-state:close",
        "life-authority:close",
        "embodiment:close",
        "memory:publish:None",
        "memory:close",
        "command-ingress:close",
        "security:close",
        "tools:clear",
        "process-refs:withdraw",
    ]
    assert application.state.task_runtime_container is None
    assert application.state.memory_service_container is None
    assert application.state.security_service_container is None
