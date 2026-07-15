"""Fail-closed periodic heartbeats for one governed task execution.

The heartbeat owns only renewal cadence and version tracking.  It deliberately
uses a duck-typed store and an injected outbox builder so composition can wire
the durable implementation without making this module another service locator.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any


class DurableExecutionHeartbeatError(RuntimeError):
    """A renewal failed and the caller must not commit the worker result."""

    def __init__(self, phase: str, cause: BaseException) -> None:
        self.phase = phase
        self.cause = cause
        super().__init__(f"durable execution heartbeat failed during {phase}")


class DurableExecutionHeartbeatLifecycleError(RuntimeError):
    """The heartbeat lifecycle was used in an ambiguous or drifting order."""


OutboxBuilder = Callable[..., Any]
Sleeper = Callable[[float], Awaitable[None]]


class DurableExecutionHeartbeat:
    """Renew an execution lease and, once attached, its reservation.

    ``checkpoint()`` is the fail-closed barrier that a coordinator must call
    immediately before committing an operation.  The first renewal failure is
    retained permanently; both ``checkpoint()`` and ``stop()`` then raise the
    same stable error type/message and expose the original exception as
    ``error.cause``.
    """

    def __init__(
        self,
        *,
        store: Any,
        outbox_builder: OutboxBuilder,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        lease_version: int,
        lease_ttl_ms: int,
        operation_ttl_ms: int,
        sleeper: Sleeper | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        self._store = store
        self._outbox_builder = outbox_builder
        self._attempt_id = _required_text(attempt_id, field="attempt_id")
        self._lease_id = _required_text(lease_id, field="lease_id")
        self._owner_id = _required_text(owner_id, field="owner_id")
        self._fencing_token = _positive_int(fencing_token, field="fencing_token")
        self._lease_version = _non_negative_int(lease_version, field="lease_version")
        self._lease_ttl_ms = _positive_int(lease_ttl_ms, field="lease_ttl_ms")
        self._operation_ttl_ms = _positive_int(
            operation_ttl_ms,
            field="operation_ttl_ms",
        )
        default_interval = min(
            30.0,
            self._lease_ttl_ms / 4_000.0,
            self._operation_ttl_ms / 4_000.0,
        )
        if interval_seconds is None:
            self._interval_seconds = default_interval
        else:
            value = float(interval_seconds)
            if value <= 0:
                raise ValueError("interval_seconds must be positive")
            self._interval_seconds = value
        self._sleeper = sleeper or asyncio.sleep

        self._reservation_id: str | None = None
        self._reservation_version: int | None = None
        self._started = False
        self._stopped = False
        self._stop_requested = False
        self._failure: DurableExecutionHeartbeatError | None = None
        self._task: asyncio.Task[None] | None = None
        self._state_lock = asyncio.Lock()

    @property
    def interval_seconds(self) -> float:
        return self._interval_seconds

    @property
    def latest_lease_version(self) -> int:
        return self._lease_version

    @property
    def latest_reservation_version(self) -> int | None:
        return self._reservation_version

    @property
    def reservation_id(self) -> str | None:
        return self._reservation_id

    @property
    def running_task(self) -> asyncio.Task[None] | None:
        """Expose the owned task for observability and no-leak assertions."""

        return self._task

    async def start(self) -> None:
        """Start exactly one background renewal loop."""

        if self._started:
            raise DurableExecutionHeartbeatLifecycleError(
                "durable execution heartbeat cannot be started more than once"
            )
        if self._stopped:
            raise DurableExecutionHeartbeatLifecycleError(
                "a stopped durable execution heartbeat cannot be restarted"
            )
        self._started = True
        self._task = asyncio.create_task(
            self._run(),
            name=f"task-heartbeat:{self._attempt_id}",
        )

    async def attach_reservation(
        self,
        *,
        reservation_id: str,
        reservation_version: int,
    ) -> None:
        """Attach the one operation reservation created for this execution."""

        await self.checkpoint()
        if not self._started or self._stopped:
            raise DurableExecutionHeartbeatLifecycleError(
                "reservation attachment requires a running heartbeat"
            )
        async with self._state_lock:
            if self._reservation_id is not None:
                raise DurableExecutionHeartbeatLifecycleError(
                    "operation reservation cannot be attached more than once"
                )
            self._reservation_id = _required_text(
                reservation_id,
                field="reservation_id",
            )
            self._reservation_version = _non_negative_int(
                reservation_version,
                field="reservation_version",
            )

    async def checkpoint(self) -> None:
        """Raise the retained renewal failure before any durable commit."""

        failure = self._failure
        if failure is not None:
            raise failure from failure.cause
        task = self._task
        if task is not None and task.done() and not self._stopped:
            # A cancelled background task is not a successful heartbeat.  Its
            # cancellation is intentionally surfaced instead of being hidden.
            task.result()

    async def stop(self) -> None:
        """Stop and join the owned task, then enforce the fail-closed barrier."""

        if not self._started:
            raise DurableExecutionHeartbeatLifecycleError(
                "durable execution heartbeat was not started"
            )
        task = self._task
        if not self._stopped:
            self._stop_requested = True
            self._stopped = True
            cancelled_by_stop = False
            if task is not None and not task.done():
                task.cancel()
                cancelled_by_stop = True
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    # Suppress only the cancellation issued immediately above.
                    # Cancellation of the caller remains externally observable.
                    if current is not None and current.cancelling():
                        raise
                    if not cancelled_by_stop:
                        raise
        await self.checkpoint()

    async def _run(self) -> None:
        while not self._stop_requested:
            try:
                await self._sleeper(self._interval_seconds)
            except asyncio.CancelledError:
                if self._stop_requested:
                    return
                raise
            if self._stop_requested:
                return
            try:
                await self._renew_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._failure is None:
                    phase = getattr(exc, "_heartbeat_phase", "renewal")
                    self._failure = DurableExecutionHeartbeatError(str(phase), exc)
                return

    async def _renew_once(self) -> None:
        async with self._state_lock:
            lease_next_version = self._lease_version + 1
            lease_outbox = await self._build_outbox(
                aggregate_type="TASK_LEASE",
                aggregate_id=self._lease_id,
                aggregate_version=lease_next_version,
                event_type="TASK_LEASE_HEARTBEAT",
            )
            try:
                renewed_lease = await _call_with_exact_replay(
                    lambda: self._store.heartbeat_lease(
                        self._lease_id,
                        owner_id=self._owner_id,
                        fencing_token=self._fencing_token,
                        expected_lease_version=self._lease_version,
                        ttl_ms=self._lease_ttl_ms,
                        outbox=lease_outbox,
                    )
                )
                self._lease_version = _advanced_version(
                    renewed_lease,
                    expected=lease_next_version,
                    subject="lease heartbeat",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _mark_phase(exc, "lease heartbeat")
                raise

            if self._reservation_id is None:
                return
            if self._reservation_version is None:  # defensive state invariant
                raise DurableExecutionHeartbeatLifecycleError(
                    "attached reservation has no aggregate version"
                )
            reservation_next_version = self._reservation_version + 1
            reservation_outbox = await self._build_outbox(
                aggregate_type="OPERATION_RESERVATION",
                aggregate_id=self._reservation_id,
                aggregate_version=reservation_next_version,
                event_type="OPERATION_RENEWED",
            )
            try:
                renewed_reservation = await _call_with_exact_replay(
                    lambda: self._store.renew_operation_reservation(
                        self._reservation_id,
                        attempt_id=self._attempt_id,
                        lease_id=self._lease_id,
                        owner_id=self._owner_id,
                        fencing_token=self._fencing_token,
                        expected_version=self._reservation_version,
                        ttl_ms=self._operation_ttl_ms,
                        outbox=reservation_outbox,
                    )
                )
                self._reservation_version = _advanced_version(
                    renewed_reservation,
                    expected=reservation_next_version,
                    subject="operation reservation renewal",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _mark_phase(exc, "operation reservation renewal")
                raise

    async def _build_outbox(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
    ) -> Any:
        builder = getattr(self._outbox_builder, "create", self._outbox_builder)
        outbox = builder(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            event_type=event_type,
            attempt_id=self._attempt_id,
        )
        return await _maybe_await(outbox)


def _mark_phase(exc: BaseException, phase: str) -> None:
    try:
        setattr(exc, "_heartbeat_phase", phase)
    except BaseException:
        # Some extension exceptions may reject attributes.  The stable public
        # error still reports a generic renewal phase in that rare case.
        return


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _call_with_exact_replay(operation: Callable[[], Any]) -> Any:
    """Retry once with identical arguments after an ambiguous call failure."""

    try:
        return await _maybe_await(operation())
    except asyncio.CancelledError:
        raise
    except Exception as first_exc:
        try:
            return await _maybe_await(operation())
        except asyncio.CancelledError:
            raise
        except Exception as replay_exc:
            raise replay_exc from first_exc


def _advanced_version(value: Any, *, expected: int, subject: str) -> int:
    if not isinstance(value, Mapping):
        raise DurableExecutionHeartbeatLifecycleError(
            f"{subject} returned no durable mapping"
        )
    actual = _non_negative_int(value.get("status_version"), field=f"{subject} version")
    if actual != expected:
        raise DurableExecutionHeartbeatLifecycleError(
            f"{subject} returned version {actual}, expected {expected}"
        )
    return actual


def _required_text(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _positive_int(value: Any, *, field: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if normalized <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return normalized


def _non_negative_int(value: Any, *, field: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if normalized < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return normalized


__all__ = [
    "DurableExecutionHeartbeat",
    "DurableExecutionHeartbeatError",
    "DurableExecutionHeartbeatLifecycleError",
]
