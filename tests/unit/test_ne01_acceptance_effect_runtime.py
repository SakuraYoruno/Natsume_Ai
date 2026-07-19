from __future__ import annotations

import asyncio
from typing import Any

import pytest

from host_brain.composition.runtime_group import (
    GovernedRuntimeGroup,
    GovernedRuntimeGroupCloseError,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    ACCEPTANCE_EFFECT_RECOVERY_PENDING,
    AcceptanceEffectDispatchError,
    AcceptanceEffectDispatcher,
    AcceptanceEffectRuntime,
)


class _EffectStore:
    def __init__(self, rows: tuple[dict[str, Any], ...] = ()) -> None:
        self.rows = rows

    def list_acceptance_effect_intents(self, **kwargs: Any):
        status = kwargs.get("status")
        rows = self.rows
        if status is not None:
            rows = tuple(row for row in rows if row.get("status") == status)
        return tuple(dict(row) for row in rows)

    def claim_acceptance_effect_intent(self, **_kwargs: Any):
        return None

    def commit_acceptance_effect_intent(self, *_args: Any, **_kwargs: Any):
        raise AssertionError("no effect is committed in this fixture")

    def fail_acceptance_effect_intent(self, *_args: Any, **_kwargs: Any):
        raise AssertionError("no effect is failed in this fixture")

    def reschedule_acceptance_effect_intent(self, *_args: Any, **_kwargs: Any):
        raise AssertionError("no effect is rescheduled in this fixture")


class _SurfaceRuntime:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    async def activate(self) -> None:
        self.events.append(f"{self.name}:activate")

    async def close_admission(self) -> None:
        self.events.append(f"{self.name}:close-admission")

    async def drain(self) -> None:
        self.events.append(f"{self.name}:drain")

    async def aclose(self) -> None:
        self.events.append(f"{self.name}:close")


def _dispatcher(store: _EffectStore | None = None) -> AcceptanceEffectDispatcher:
    return AcceptanceEffectDispatcher(
        task_store=store or _EffectStore(),
        handlers={"test.effect": lambda _payload: "test-receipt://sha256/value"},
        owner_id="acceptance-effects::single-owner",
    )


@pytest.mark.asyncio
async def test_runtime_group_owns_one_shared_effect_recovery_loop() -> None:
    events: list[str] = []
    dispatcher = _dispatcher()
    passes: list[str] = []

    async def drain_ready(*, limit: int):
        passes.append(f"drain:{limit}")
        return ()

    dispatcher.drain_ready = drain_ready  # type: ignore[method-assign]
    effect_runtime = AcceptanceEffectRuntime(
        dispatcher=dispatcher,
        poll_seconds=0.01,
    )
    group = GovernedRuntimeGroup(
        {
            "host": _SurfaceRuntime("host", events),
            "operator": _SurfaceRuntime("operator", events),
        },
        acceptance_effect_runtime=effect_runtime,
    )

    await group.activate()
    assert effect_runtime.active is True
    assert passes[0] == "drain:1000"
    assert events[:2] == ["host:activate", "operator:activate"]

    await group.close_admission()
    await group.drain()
    await group.aclose()

    assert effect_runtime.active is False
    assert events == [
        "host:activate",
        "operator:activate",
        "host:close-admission",
        "operator:close-admission",
        "host:drain",
        "operator:drain",
        "operator:close",
        "host:close",
    ]
    # One app-local owner serves both surfaces; neither surface starts a loop.
    assert sum(1 for task in asyncio.all_tasks() if task.get_name() == "governed-acceptance-effect-recovery") == 0


@pytest.mark.asyncio
async def test_effect_runtime_shutdown_waits_for_in_flight_handler_pass() -> None:
    dispatcher = _dispatcher()
    started = asyncio.Event()
    release = asyncio.Event()
    cancelled = False
    pass_no = 0

    async def drain_ready(*, limit: int):
        nonlocal cancelled, pass_no
        pass_no += 1
        if pass_no == 1:
            return ()  # startup recovery
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled = True
            raise
        return ()

    dispatcher.drain_ready = drain_ready  # type: ignore[method-assign]
    runtime = AcceptanceEffectRuntime(
        dispatcher=dispatcher,
        poll_seconds=0.01,
    )
    await runtime.activate()
    await asyncio.wait_for(started.wait(), timeout=1.0)

    closing = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    assert closing.done() is False
    assert cancelled is False

    release.set()
    await asyncio.wait_for(closing, timeout=1.0)
    assert cancelled is False
    assert runtime.active is False


@pytest.mark.asyncio
async def test_dispatch_graph_reports_other_owner_claim_as_recovery_pending() -> None:
    row = {
        "effect_id": "acceptance-effect://sha256/held",
        "graph_id": "graph-held",
        "graph_revision": 1,
        "effect_type": "test.effect",
        "status": "CLAIMED",
        "lease_owner": "other-process",
        "fencing_token": 7,
        "created_ts_ms": 10,
    }
    dispatcher = _dispatcher(_EffectStore((row,)))

    result = await dispatcher.dispatch_graph(
        graph_id="graph-held",
        graph_revision=1,
    )

    assert len(result) == 1
    assert result[0]["status"] == "CLAIMED"
    assert result[0]["dispatch_state"] == "RECOVERY_PENDING"
    assert result[0]["recovery_pending"] is True
    assert result[0]["dispatch_reason_code"] == (
        "ACCEPTANCE_EFFECT_CLAIM_OR_RETRY_PENDING"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_method", ["stop", "join"])
async def test_group_close_aggregates_review_surface_and_effect_failures(
    failure_method: str,
) -> None:
    events: list[str] = []

    class FailingWorker:
        async def start(self) -> None:
            events.append("review:start")

        def wake(self) -> None:
            return None

        async def stop(self) -> None:
            events.append("review:stop")
            if failure_method == "stop":
                raise RuntimeError("review stop failed")

        async def join(self) -> None:
            events.append("review:join")
            if failure_method == "join":
                raise RuntimeError("review join failed")

    class FailingSurface(_SurfaceRuntime):
        async def aclose(self) -> None:
            events.append("surface:close")
            raise RuntimeError("surface close failed")

    class FailingEffect:
        async def activate(self) -> None:
            events.append("effect:activate")

        async def drain(self) -> None:
            events.append("effect:drain")

        async def aclose(self) -> None:
            events.append("effect:close")
            raise RuntimeError("effect close failed")

    class Commands:
        def get_by_graph(self, **_kwargs: Any) -> None:
            return None

    group = GovernedRuntimeGroup(
        {"host": FailingSurface("surface", events)},
        review_store=object(),
        review_decision_store=object(),
        command_repository=Commands(),
        review_continuation_worker_factory=lambda **_kwargs: FailingWorker(),
        review_continuation_worker_id="review-close-failure",
        acceptance_effect_runtime=FailingEffect(),
    )
    await group.activate()

    with pytest.raises(GovernedRuntimeGroupCloseError) as raised:
        await group.aclose()

    assert "surface:close" in events
    assert "effect:close" in events
    assert tuple(component for component, _exc in raised.value.failures) == (
        "review_continuation_worker",
        "surface:host",
        "acceptance_effect_runtime",
    )


@pytest.mark.asyncio
async def test_effect_runtime_blocks_admission_while_startup_recovery_is_pending(
) -> None:
    row = {
        "effect_id": "acceptance-effect://sha256/startup-held",
        "graph_id": "graph-startup-held",
        "graph_revision": 1,
        "effect_type": "test.effect",
        "status": "CLAIMED",
        "lease_owner": "previous-process",
        "fencing_token": 3,
        "created_ts_ms": 10,
    }
    runtime = AcceptanceEffectRuntime(
        dispatcher=_dispatcher(_EffectStore((row,))),
        poll_seconds=0.01,
    )

    with pytest.raises(AcceptanceEffectDispatchError) as pending:
        await runtime.activate()

    assert pending.value.reason_code == ACCEPTANCE_EFFECT_RECOVERY_PENDING
    assert runtime.active is False
