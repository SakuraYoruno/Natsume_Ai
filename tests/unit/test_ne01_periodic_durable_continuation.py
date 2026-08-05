from __future__ import annotations

import asyncio
from typing import Any

import pytest

from host_brain.app.periodic_durable_continuation import (
    PeriodicDurableContinuation,
)


@pytest.mark.asyncio
async def test_periodic_continuation_serializes_wakeups_and_timer_ticks() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    concurrent = 0
    max_concurrent = 0

    async def recover() -> tuple[dict[str, Any], ...]:
        nonlocal calls, concurrent, max_concurrent
        calls += 1
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        entered.set()
        await release.wait()
        concurrent -= 1
        return ({"cycle": calls},)

    continuation = PeriodicDurableContinuation(
        runner=recover,
        task_name="test-periodic-durable-continuation",
        failure_reason_code="TEST_PERIODIC_CONTINUATION_FAILED",
        interval_ms=10,
    )
    await continuation.start()
    continuation.wake()
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    continuation.wake()
    continuation.wake()
    release.set()

    async def wait_for_second_cycle() -> None:
        while calls < 2:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_second_cycle(), timeout=1.0)
    continuation.request_stop()
    await continuation.join(timeout_s=1.0)

    assert max_concurrent == 1
    assert calls >= 2
    assert continuation.health_snapshot()["running"] is False


@pytest.mark.asyncio
async def test_periodic_continuation_retains_failure_and_retries() -> None:
    recovered = asyncio.Event()
    calls = 0

    async def recover() -> tuple[dict[str, Any], ...]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("opaque failure")
        recovered.set()
        return ({"status": "COMPLETED"},)

    continuation = PeriodicDurableContinuation(
        runner=recover,
        task_name="test-periodic-durable-continuation-retry",
        failure_reason_code="TEST_PERIODIC_CONTINUATION_FAILED",
        interval_ms=10,
    )
    await continuation.start()
    continuation.wake()

    async def wait_for_failure() -> None:
        while continuation.health_snapshot()["last_failure"] is None:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_failure(), timeout=1.0)
    failure = continuation.health_snapshot()["last_failure"]
    assert failure["reason_code"] == "TEST_PERIODIC_CONTINUATION_FAILED"
    assert failure["error_type"] == "ValueError"

    continuation.wake()
    await asyncio.wait_for(recovered.wait(), timeout=1.0)
    assert continuation.health_snapshot()["last_failure"] is None
    continuation.request_stop()
    await continuation.join(timeout_s=1.0)


@pytest.mark.asyncio
async def test_periodic_continuation_rejects_untrusted_failure_reason_text() -> None:
    class UnsafeFailure(RuntimeError):
        reason_code = "secret user body must not enter health"

    async def recover() -> tuple[dict[str, Any], ...]:
        raise UnsafeFailure("private payload")

    continuation = PeriodicDurableContinuation(
        runner=recover,
        task_name="test-opaque-failure",
        failure_reason_code="DURABLE_CONTINUATION_FAILED",
        interval_ms=10,
    )

    with pytest.raises(UnsafeFailure):
        await continuation.run_once()

    failure = continuation.health_snapshot()["last_failure"]
    assert failure == {
        "reason_code": "DURABLE_CONTINUATION_FAILED",
        "error_type": "UnsafeFailure",
        "ts_ms": failure["ts_ms"],
    }


@pytest.mark.asyncio
async def test_periodic_continuation_shutdown_waits_for_active_scan() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def recover() -> tuple[dict[str, Any], ...]:
        entered.set()
        await release.wait()
        return ()

    continuation = PeriodicDurableContinuation(
        runner=recover,
        task_name="test-periodic-durable-continuation-shutdown",
        failure_reason_code="TEST_PERIODIC_CONTINUATION_FAILED",
        interval_ms=10,
    )
    await continuation.start()
    continuation.wake()
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    continuation.request_stop()
    joining = asyncio.create_task(continuation.join(timeout_s=1.0))
    await asyncio.sleep(0)
    assert joining.done() is False
    release.set()
    await joining
    assert continuation.health_snapshot()["running"] is False


@pytest.mark.asyncio
async def test_periodic_continuation_shutdown_accepts_cancelled_owned_child() -> None:
    entered = asyncio.Event()

    async def recover() -> tuple[dict[str, Any], ...]:
        entered.set()
        await asyncio.Event().wait()
        return ()

    continuation = PeriodicDurableContinuation(
        runner=recover,
        task_name="test-periodic-owned-cancellation",
        failure_reason_code="TEST_PERIODIC_CONTINUATION_FAILED",
        interval_ms=10,
    )
    await continuation.start()
    continuation.wake()
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    owned_task = continuation._task
    assert owned_task is not None
    owned_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owned_task

    continuation.request_stop()
    await continuation.join(timeout_s=1.0)
    assert continuation.health_snapshot()["running"] is False
