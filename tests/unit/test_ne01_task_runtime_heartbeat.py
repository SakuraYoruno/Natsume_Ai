from __future__ import annotations

import asyncio
from typing import Any

import pytest

from host_brain.task_runtime.heartbeat import (
    DurableExecutionHeartbeat,
    DurableExecutionHeartbeatError,
    DurableExecutionHeartbeatLifecycleError,
)


class _ControlledSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []
        self._waiters: asyncio.Queue[asyncio.Event] = asyncio.Queue()

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        gate = asyncio.Event()
        await self._waiters.put(gate)
        await gate.wait()

    async def pulse(self) -> None:
        gate = await asyncio.wait_for(self._waiters.get(), timeout=1)
        gate.set()


class _Store:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fail_lease: BaseException | None = None
        self.fail_reservation: BaseException | None = None

    def heartbeat_lease(
        self,
        lease_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_lease_version: int,
        ttl_ms: int,
        outbox: Any,
    ) -> dict[str, Any]:
        del lease_id, owner_id, fencing_token, ttl_ms, outbox
        self.calls.append(("lease", expected_lease_version))
        if self.fail_lease is not None:
            raise self.fail_lease
        return {"status_version": expected_lease_version + 1}

    def renew_operation_reservation(
        self,
        reservation_id: str,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        ttl_ms: int,
        outbox: Any,
    ) -> dict[str, Any]:
        del (
            reservation_id,
            attempt_id,
            lease_id,
            owner_id,
            fencing_token,
            ttl_ms,
            outbox,
        )
        self.calls.append(("reservation", expected_version))
        if self.fail_reservation is not None:
            raise self.fail_reservation
        return {"status_version": expected_version + 1}


class _Outboxes:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **values: Any) -> dict[str, Any]:
        self.calls.append(dict(values))
        return dict(values)


def _heartbeat(
    *,
    store: _Store,
    sleeper: Any,
    outboxes: _Outboxes | None = None,
) -> DurableExecutionHeartbeat:
    return DurableExecutionHeartbeat(
        store=store,
        outbox_builder=outboxes or _Outboxes(),
        attempt_id="attempt-1",
        lease_id="lease-1",
        owner_id="runtime/boot-1",
        fencing_token=7,
        lease_version=2,
        lease_ttl_ms=120_000,
        operation_ttl_ms=80_000,
        sleeper=sleeper,
    )


async def _until(predicate: Any) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("asynchronous heartbeat did not reach expected state")


@pytest.mark.asyncio
async def test_long_execution_renews_lease_then_attached_reservation_repeatedly() -> None:
    store = _Store()
    sleeper = _ControlledSleeper()
    outboxes = _Outboxes()
    heartbeat = _heartbeat(store=store, sleeper=sleeper, outboxes=outboxes)

    assert heartbeat.interval_seconds == 20.0
    await heartbeat.start()

    # Before reservation, the execution lease remains alive independently.
    await sleeper.pulse()
    await _until(lambda: len(store.calls) == 1)
    assert store.calls == [("lease", 2)]
    assert heartbeat.latest_lease_version == 3
    assert heartbeat.latest_reservation_version is None

    await heartbeat.attach_reservation(
        reservation_id="reservation-1",
        reservation_version=4,
    )
    for tick in range(3):
        await sleeper.pulse()
        await _until(lambda tick=tick: len(store.calls) == 3 + (tick * 2))

    assert store.calls == [
        ("lease", 2),
        ("lease", 3),
        ("reservation", 4),
        ("lease", 4),
        ("reservation", 5),
        ("lease", 5),
        ("reservation", 6),
    ]
    assert heartbeat.latest_lease_version == 6
    assert heartbeat.latest_reservation_version == 7
    assert [item["aggregate_version"] for item in outboxes.calls] == [3, 4, 5, 5, 6, 6, 7]
    assert [item["event_type"] for item in outboxes.calls] == [
        "TASK_LEASE_HEARTBEAT",
        "TASK_LEASE_HEARTBEAT",
        "OPERATION_RENEWED",
        "TASK_LEASE_HEARTBEAT",
        "OPERATION_RENEWED",
        "TASK_LEASE_HEARTBEAT",
        "OPERATION_RENEWED",
    ]

    task = heartbeat.running_task
    await heartbeat.stop()
    assert task is not None and task.done()


@pytest.mark.asyncio
async def test_start_and_reservation_attachment_cannot_drift() -> None:
    heartbeat = _heartbeat(store=_Store(), sleeper=_ControlledSleeper())

    with pytest.raises(DurableExecutionHeartbeatLifecycleError):
        await heartbeat.attach_reservation(
            reservation_id="reservation-1",
            reservation_version=0,
        )
    await heartbeat.start()
    with pytest.raises(DurableExecutionHeartbeatLifecycleError):
        await heartbeat.start()
    await heartbeat.attach_reservation(
        reservation_id="reservation-1",
        reservation_version=0,
    )
    with pytest.raises(DurableExecutionHeartbeatLifecycleError):
        await heartbeat.attach_reservation(
            reservation_id="reservation-2",
            reservation_version=99,
        )
    assert heartbeat.reservation_id == "reservation-1"
    assert heartbeat.latest_reservation_version == 0
    await heartbeat.stop()


@pytest.mark.asyncio
async def test_reservation_failure_is_retained_and_blocks_checkpoint_and_stop() -> None:
    store = _Store()
    store.fail_reservation = RuntimeError("reservation CAS lost")
    sleeper = _ControlledSleeper()
    heartbeat = _heartbeat(store=store, sleeper=sleeper)
    await heartbeat.start()
    await heartbeat.attach_reservation(
        reservation_id="reservation-1",
        reservation_version=8,
    )

    await sleeper.pulse()
    task = heartbeat.running_task
    assert task is not None
    await _until(task.done)

    with pytest.raises(DurableExecutionHeartbeatError) as first:
        await heartbeat.checkpoint()
    with pytest.raises(DurableExecutionHeartbeatError) as second:
        await heartbeat.checkpoint()
    with pytest.raises(DurableExecutionHeartbeatError) as stopped:
        await heartbeat.stop()

    assert first.value is second.value is stopped.value
    assert first.value.phase == "operation reservation renewal"
    assert first.value.cause is store.fail_reservation
    assert store.calls == [
        ("lease", 2),
        ("reservation", 8),
        ("reservation", 8),
    ]
    assert heartbeat.latest_lease_version == 3
    assert heartbeat.latest_reservation_version == 8
    assert task.done()


@pytest.mark.asyncio
async def test_lease_failure_stops_before_reservation_renewal() -> None:
    store = _Store()
    store.fail_lease = RuntimeError("lease fenced")
    sleeper = _ControlledSleeper()
    heartbeat = _heartbeat(store=store, sleeper=sleeper)
    await heartbeat.start()
    await heartbeat.attach_reservation(
        reservation_id="reservation-1",
        reservation_version=1,
    )

    await sleeper.pulse()
    task = heartbeat.running_task
    assert task is not None
    await _until(task.done)
    with pytest.raises(DurableExecutionHeartbeatError) as failed:
        await heartbeat.stop()

    assert failed.value.phase == "lease heartbeat"
    assert store.calls == [("lease", 2), ("lease", 2)]
    assert heartbeat.latest_lease_version == 2
    assert heartbeat.latest_reservation_version == 1
    assert task.done()


@pytest.mark.asyncio
async def test_ambiguous_renewal_response_is_replayed_once_with_same_versions() -> None:
    class _LoseFirstResponseStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.lost_lease = False
            self.lost_reservation = False

        def heartbeat_lease(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            result = super().heartbeat_lease(*args, **kwargs)
            if not self.lost_lease:
                self.lost_lease = True
                raise TimeoutError("lease response lost after commit")
            return result

        def renew_operation_reservation(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            result = super().renew_operation_reservation(*args, **kwargs)
            if not self.lost_reservation:
                self.lost_reservation = True
                raise TimeoutError("reservation response lost after commit")
            return result

    store = _LoseFirstResponseStore()
    sleeper = _ControlledSleeper()
    heartbeat = _heartbeat(store=store, sleeper=sleeper)
    await heartbeat.start()
    await heartbeat.attach_reservation(
        reservation_id="reservation-1",
        reservation_version=4,
    )

    await sleeper.pulse()
    await _until(lambda: heartbeat.latest_reservation_version == 5)

    assert store.calls == [
        ("lease", 2),
        ("lease", 2),
        ("reservation", 4),
        ("reservation", 4),
    ]
    assert heartbeat.latest_lease_version == 3
    assert heartbeat.latest_reservation_version == 5
    await heartbeat.stop()


@pytest.mark.asyncio
async def test_external_sleeper_cancellation_is_not_swallowed() -> None:
    async def cancelled(_: float) -> None:
        raise asyncio.CancelledError

    heartbeat = _heartbeat(store=_Store(), sleeper=cancelled)
    await heartbeat.start()
    task = heartbeat.running_task
    assert task is not None
    await _until(task.done)

    with pytest.raises(asyncio.CancelledError):
        await heartbeat.checkpoint()
    with pytest.raises(asyncio.CancelledError):
        await heartbeat.stop()
    assert task.done()


@pytest.mark.asyncio
async def test_non_exception_process_signal_is_not_wrapped_as_renewal_failure() -> None:
    class _ProcessSignal(BaseException):
        pass

    store = _Store()
    store.fail_lease = _ProcessSignal("terminate")
    sleeper = _ControlledSleeper()
    heartbeat = _heartbeat(store=store, sleeper=sleeper)
    await heartbeat.start()
    await sleeper.pulse()
    task = heartbeat.running_task
    assert task is not None
    await _until(task.done)

    with pytest.raises(_ProcessSignal, match="terminate"):
        await heartbeat.checkpoint()
    with pytest.raises(_ProcessSignal, match="terminate"):
        await heartbeat.stop()
    assert task.done()
