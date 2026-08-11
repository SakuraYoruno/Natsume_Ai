from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import pytest

from host_brain.app.governed_acceptance_effect_projection import (
    dispatch_accepted_effects,
)
from host_brain.composition.runtime_group import (
    GovernedRuntimeGroup,
    GovernedRuntimeGroupCloseError,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
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
async def test_effect_runtime_wake_interrupts_long_recovery_poll() -> None:
    dispatcher = _dispatcher()
    passes = 0
    background_drained = asyncio.Event()

    async def drain_ready(*, limit: int):
        nonlocal passes
        passes += 1
        if passes > 1:
            background_drained.set()
        return ()

    dispatcher.drain_ready = drain_ready  # type: ignore[method-assign]
    runtime = AcceptanceEffectRuntime(
        dispatcher=dispatcher,
        poll_seconds=60.0,
    )

    await runtime.activate()
    try:
        runtime.wake()
        await asyncio.wait_for(background_drained.wait(), timeout=0.5)
        assert passes == 2
    finally:
        await runtime.aclose()


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
async def test_effect_runtime_reports_recovery_pass_failure_without_stopping(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dispatcher = _dispatcher()
    attempted = asyncio.Event()
    recovered = asyncio.Event()
    pass_no = 0
    observations: list[dict[str, Any]] = []

    def observe(item: dict[str, Any]) -> None:
        observations.append(dict(item))
        if (
            item.get("status") == "RECOVERED"
            and any(row.get("status") == "FAILED" for row in observations)
        ):
            recovered.set()

    async def drain_ready(*, limit: int):
        nonlocal pass_no
        pass_no += 1
        if pass_no == 1:
            return ()  # startup recovery
        if pass_no == 2:
            attempted.set()
            raise RuntimeError("temporary recovery store failure")
        return ()

    dispatcher.drain_ready = drain_ready  # type: ignore[method-assign]
    runtime = AcceptanceEffectRuntime(
        dispatcher=dispatcher,
        poll_seconds=0.01,
        recovery_observer=observe,
    )
    caplog.set_level(
        logging.ERROR,
        logger="Natsume.acceptance_effect_recovery",
    )

    await runtime.activate()
    try:
        await asyncio.wait_for(attempted.wait(), timeout=1.0)
        await asyncio.wait_for(recovered.wait(), timeout=1.0)
        for _ in range(50):
            if any(
                record.message == "ACCEPTANCE_EFFECT_RECOVERY_PASS_FAILED"
                for record in caplog.records
            ):
                break
            await asyncio.sleep(0.01)
        assert runtime.active is True
        assert pass_no >= 2
        assert any(
            record.message.startswith("ACCEPTANCE_EFFECT_RECOVERY_PASS_FAILED")
            and record.exc_info is None
            for record in caplog.records
        )
        failure = next(
            item for item in observations if item.get("status") == "FAILED"
        )
        assert failure["reason_code"] == "ACCEPTANCE_EFFECT_RECOVERY_PASS_FAILED"
        assert failure["error_type"].endswith("RuntimeError")
        assert failure["error_digest"].startswith("sha256:")
        assert "temporary recovery store failure" not in str(failure)
        assert "temporary recovery store failure" not in caplog.text
        candidate = hashlib.sha256(
            b"builtins.RuntimeError\0temporary recovery store failure"
        ).hexdigest()
        assert failure["error_digest"] != f"sha256:{candidate}"
        assert observations[-1]["status"] == "RECOVERED"
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_effect_runtime_retries_observer_before_resolving_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dispatcher = _dispatcher()
    pass_no = 0
    observer_calls: list[str] = []
    recovered = asyncio.Event()

    async def drain_ready(*, limit: int):
        nonlocal pass_no
        pass_no += 1
        if pass_no == 1:
            return ()
        if pass_no == 2:
            raise RuntimeError("sensitive provider detail")
        return ()

    failed_once = False

    def observe(item: dict[str, Any]) -> None:
        nonlocal failed_once
        status = str(item["status"])
        observer_calls.append(status)
        if status == "FAILED" and not failed_once:
            failed_once = True
            raise OSError("guardian temporarily unavailable")
        if status == "RECOVERED" and "FAILED" in observer_calls:
            recovered.set()

    dispatcher.drain_ready = drain_ready  # type: ignore[method-assign]
    runtime = AcceptanceEffectRuntime(
        dispatcher=dispatcher,
        poll_seconds=0.01,
        recovery_observer=observe,
    )
    caplog.set_level(logging.ERROR, logger="Natsume.acceptance_effect_recovery")

    await runtime.activate()
    try:
        await asyncio.wait_for(recovered.wait(), timeout=1.0)
        assert observer_calls[-3:] == ["FAILED", "FAILED", "RECOVERED"]
        assert any(
            row.message.startswith("ACCEPTANCE_EFFECT_RECOVERY_OBSERVER_FAILED")
            and row.exc_info is None
            for row in caplog.records
        )
        assert "guardian temporarily unavailable" not in caplog.text
        assert runtime.active is True
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_effect_runtime_retries_restart_resolution_projection() -> None:
    dispatcher = _dispatcher()
    resolution_projected = asyncio.Event()
    calls = 0

    async def drain_ready(*, limit: int):
        return ()

    def observe(item: dict[str, Any]) -> None:
        nonlocal calls
        if item["status"] != "RECOVERED":
            return
        calls += 1
        if calls == 1:
            raise OSError("guardian temporarily unavailable")
        resolution_projected.set()

    dispatcher.drain_ready = drain_ready  # type: ignore[method-assign]
    runtime = AcceptanceEffectRuntime(
        dispatcher=dispatcher,
        poll_seconds=0.01,
        recovery_observer=observe,
    )

    await runtime.activate()
    try:
        await asyncio.wait_for(resolution_projected.wait(), timeout=1.0)
        assert calls == 2
        assert runtime.active is True
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_effect_runtime_observes_startup_pending_query_failure() -> None:
    dispatcher = _dispatcher()
    observations: list[dict[str, Any]] = []

    async def drain_ready(*, limit: int):
        return ()

    def fail_pending_query(*, limit: int):
        raise OSError("sensitive pending-query detail")

    dispatcher.drain_ready = drain_ready  # type: ignore[method-assign]
    dispatcher.list_recovery_pending = fail_pending_query  # type: ignore[method-assign]
    runtime = AcceptanceEffectRuntime(
        dispatcher=dispatcher,
        recovery_observer=lambda item: observations.append(dict(item)),
    )

    with pytest.raises(OSError, match="pending-query"):
        await runtime.activate()

    assert runtime.active is False
    assert len(observations) == 1
    assert observations[0]["status"] == "FAILED"
    assert observations[0]["error_type"].endswith("OSError")
    assert "sensitive pending-query detail" not in str(observations[0])


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
async def test_governed_projection_reports_visible_reply_and_background_work() -> None:
    calls: list[tuple[str, int]] = []

    class InteractiveDispatcher:
        async def dispatch_interactive_graph(
            self,
            *,
            graph_id: str,
            graph_revision: int,
        ) -> tuple[dict[str, Any], ...]:
            calls.append((graph_id, graph_revision))
            return (
                {
                    "effect_type": "conversation.user_turn",
                    "status": "COMMITTED",
                },
                {
                    "effect_type": "surface.reply_delivery",
                    "status": "COMMITTED",
                },
                {
                    "effect_type": "life.foreground_completed",
                    "status": "PENDING",
                    "attempt_count": 0,
                },
                {
                    "effect_type": "host.companion_expression_enqueue",
                    "status": "PENDING",
                    "attempt_count": 0,
                },
                {
                    "effect_type": "conversation.preference_signal",
                    "status": "PENDING",
                    "attempt_count": 0,
                },
            )

        async def dispatch_graph(self, **_kwargs: Any):
            raise AssertionError("interactive graph must use the reply cutoff")

    projected = await dispatch_accepted_effects(
        {
            "execution_receipt": {
                "accepted": True,
                "status": "ACCEPTED",
                "graph_id": "graph-interactive-summary",
                "graph_revision": 2,
            }
        },
        InteractiveDispatcher(),
    )

    assert calls == [("graph-interactive-summary", 2)]
    assert projected["audit_refs"]["acceptance_effects"] == {
        "count": 5,
        "committed": 2,
        "failed": 0,
        "pending": 3,
        "recovery_pending": 0,
        "dispatch_status": "DELIVERY_COMMITTED_BACKGROUND_PENDING",
        "delivery_committed": True,
        "background_pending": 3,
    }


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
async def test_effect_runtime_quarantines_startup_pending_without_blocking_host(
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
    observations: list[dict[str, Any]] = []
    runtime = AcceptanceEffectRuntime(
        dispatcher=_dispatcher(_EffectStore((row,))),
        poll_seconds=0.01,
        recovery_observer=lambda item: observations.append(dict(item)),
    )

    await runtime.activate()
    try:
        assert runtime.active is True
        assert len(observations) == 1
        assert observations[0]["status"] == "FAILED"
        assert observations[0]["error_type"].endswith(
            "AcceptanceEffectDispatchError"
        )
        assert "startup-held" not in str(observations[0])
    finally:
        await runtime.aclose()
