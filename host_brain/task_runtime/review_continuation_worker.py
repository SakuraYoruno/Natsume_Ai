"""Process-owned delivery worker for terminal TaskNode Review continuations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import inspect
import re
from typing import Any, Protocol


_DEFAULT_POLL_INTERVAL_MS = 1_000
_DEFAULT_CLAIM_LEASE_MS = 30_000
_DEFAULT_RETRY_DELAY_MS = 1_000
_DEFAULT_BATCH_LIMIT = 16
_DEFAULT_SHUTDOWN_GRACE_MS = 5_000
_MAX_ERROR_CODE_LENGTH = 192

REVIEW_CONTINUATION_SHUTDOWN_TIMEOUT = "REVIEW_CONTINUATION_SHUTDOWN_TIMEOUT"


class ReviewContinuationShutdownError(RuntimeError):
    def __init__(self) -> None:
        self.reason_code = REVIEW_CONTINUATION_SHUTDOWN_TIMEOUT
        super().__init__(
            f"{self.reason_code}: review continuation worker did not quiesce"
        )


class ReviewContinuationStorePort(Protocol):
    def assert_review_continuation_ready(
        self,
        *,
        force_revalidate: bool = False,
    ) -> None: ...

    def reconcile_terminal_reviews(self) -> Mapping[str, int]: ...

    def claim_pending_review_continuations(
        self,
        *,
        worker_id: str,
        lease_ms: int,
        limit: int = 100,
    ) -> Sequence[Mapping[str, Any]]: ...

    def heartbeat_review_continuation(
        self,
        review_id: str,
        *,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        lease_ms: int,
        expected_version: int,
    ) -> Mapping[str, Any]: ...

    def defer_review_continuation(
        self,
        review_id: str,
        *,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        retry_delay_ms: int,
        last_error: str,
        expected_version: int,
    ) -> Mapping[str, Any]: ...

    def complete_review_continuation(
        self,
        review_id: str,
        *,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        expected_version: int,
    ) -> Mapping[str, Any]: ...

    def validate_review_continuation_claim(
        self,
        claim: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def validate_review_continuation_authority(
        self,
        authority: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class ReviewContinuationRuntimePort(Protocol):
    async def continue_review_continuation(
        self,
        claim: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class ReviewExpiryPort(Protocol):
    def expire_due(self) -> Mapping[str, Any]: ...


@dataclass(slots=True)
class _ClaimLease:
    review_id: str
    graph_id: str
    graph_revision: int
    node_id: str
    subject_version: int
    resume_phase: str
    claim_token: str
    fencing_token: int
    expected_version: int


Sleeper = Callable[[float], Awaitable[None]]


class ReviewContinuationWorker:
    """Claim and deliver exact Review checkpoints across Host processes."""

    def __init__(
        self,
        *,
        store: ReviewContinuationStorePort,
        runtime: ReviewContinuationRuntimePort,
        worker_id: str,
        review_expirer: ReviewExpiryPort | None = None,
        claim_lease_ms: int = _DEFAULT_CLAIM_LEASE_MS,
        poll_interval_ms: int = _DEFAULT_POLL_INTERVAL_MS,
        retry_delay_ms: int = _DEFAULT_RETRY_DELAY_MS,
        batch_limit: int = _DEFAULT_BATCH_LIMIT,
        shutdown_grace_ms: int = _DEFAULT_SHUTDOWN_GRACE_MS,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        owner = str(worker_id or "").strip()
        if not owner:
            raise ValueError("worker_id is required")
        if (
            int(claim_lease_ms) <= 0
            or int(poll_interval_ms) <= 0
            or int(retry_delay_ms) < 0
            or int(batch_limit) <= 0
            or int(shutdown_grace_ms) <= 0
        ):
            raise ValueError("continuation worker timing and batch values are invalid")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        if review_expirer is not None and not callable(
            getattr(review_expirer, "expire_due", None)
        ):
            raise TypeError("review_expirer must expose expire_due")
        self._store = store
        self._runtime = runtime
        self._review_expirer = review_expirer
        self._worker_id = owner
        self._claim_lease_ms = int(claim_lease_ms)
        self._poll_interval_seconds = int(poll_interval_ms) / 1_000.0
        self._retry_delay_ms = int(retry_delay_ms)
        self._batch_limit = int(batch_limit)
        self._shutdown_grace_seconds = int(shutdown_grace_ms) / 1_000.0
        self._sleeper = sleeper
        self._wake_event: asyncio.Event | None = None
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Fail closed on 0096, reconcile history, then scan immediately."""

        async with self._lifecycle_lock:
            if self.running:
                return
            await _maybe_await(
                self._store.assert_review_continuation_ready(
                    force_revalidate=True,
                )
            )
            await self._expire_due_reviews()
            reconciled = await _maybe_await(self._store.reconcile_terminal_reviews())
            if not isinstance(reconciled, Mapping):
                raise RuntimeError("review continuation reconciliation is invalid")
            self._wake_event = asyncio.Event()
            self._stop_event = asyncio.Event()
            self._task = asyncio.create_task(
                self._run(),
                name=f"review-continuation::{self._worker_id}",
            )

    def wake(self) -> None:
        wake = self._wake_event
        if wake is not None:
            wake.set()

    async def stop(self) -> None:
        stop = self._stop_event
        if stop is not None:
            stop.set()
        self.wake()
        task = self._task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._shutdown_grace_seconds,
            )
        except TimeoutError:
            task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=max(0.1, self._shutdown_grace_seconds),
                )
            except asyncio.CancelledError:
                caller = asyncio.current_task()
                if caller is not None and caller.cancelling():
                    raise
            except TimeoutError as exc:
                raise ReviewContinuationShutdownError() from exc

    async def join(self) -> None:
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._shutdown_grace_seconds,
            )
        except TimeoutError as exc:
            raise ReviewContinuationShutdownError() from exc
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                raise
            if task.cancelled():
                return
            raise

    async def aclose(self) -> None:
        await self.stop()
        await self.join()

    async def run_once(self) -> int:
        await self._expire_due_reviews()
        claimed_raw = await _maybe_await(
            self._store.claim_pending_review_continuations(
                worker_id=self._worker_id,
                lease_ms=self._claim_lease_ms,
                limit=self._batch_limit,
            )
        )
        if not isinstance(claimed_raw, Sequence) or isinstance(
            claimed_raw,
            (str, bytes, bytearray),
        ):
            raise RuntimeError("review continuation store returned an invalid batch")
        claimed = tuple(claimed_raw)
        if len(claimed) > self._batch_limit:
            raise RuntimeError("review continuation store exceeded the requested limit")
        if not claimed:
            return 0
        await asyncio.gather(*(self._process_claim_isolated(raw) for raw in claimed))
        return len(claimed)

    async def _expire_due_reviews(self) -> None:
        expirer = self._review_expirer
        if expirer is None:
            return
        result = await _maybe_await(expirer.expire_due())
        if not isinstance(result, Mapping):
            raise RuntimeError("Review expiry sweep returned an invalid result")

    async def _process_claim_isolated(self, raw: Mapping[str, Any]) -> None:
        try:
            await self._process_claim(raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Malformed or already-stale claims remain recoverable after lease
            # expiry.  No process-local exception may delete their evidence.
            return

    async def _run(self) -> None:
        stop = self._required_stop_event()
        while not stop.is_set():
            try:
                claimed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                claimed = 0
            if stop.is_set():
                break
            if claimed >= self._batch_limit:
                continue
            await self._wait_for_wake_or_poll()

    async def _wait_for_wake_or_poll(self) -> None:
        wake = self._required_wake_event()
        stop = self._required_stop_event()
        if wake.is_set():
            wake.clear()
            return

        async def _delay() -> None:
            await self._sleeper(self._poll_interval_seconds)

        waiters = {
            asyncio.create_task(wake.wait()),
            asyncio.create_task(stop.wait()),
            asyncio.create_task(_delay()),
        }
        try:
            _, pending = await asyncio.wait(
                waiters,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            for task in waiters:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            wake.clear()

    async def _process_claim(self, raw: Mapping[str, Any]) -> None:
        lease = self._validated_claim(raw)
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_claim(lease, stop_heartbeat),
            name=f"review-continuation-heartbeat::{lease.review_id}",
        )
        delivery = asyncio.create_task(
            self._deliver_claim(raw),
            name=f"review-continuation-delivery::{lease.review_id}",
        )
        try:
            done, _pending = await asyncio.wait(
                {delivery, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done and delivery not in done:
                # The Review claim authorizes the atomic READY->Attempt/Lease
                # hand-off.  After that transaction commits, the Task lease is
                # the execution authority.  Python cancellation cannot revoke
                # an already-running blocking capability call, so do not
                # pretend that losing this coordination heartbeat stops an
                # external side effect.  Await the in-flight Task delivery but
                # never complete/defer with the stale Review claim; a later
                # owner reconciles the durable checkpoint.  If the hand-off
                # has not happened, its in-transaction claim fence rejects it.
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    caller = asyncio.current_task()
                    if caller is not None and caller.cancelling():
                        raise
                except Exception:
                    pass
                try:
                    await delivery
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                return
            stop_heartbeat.set()
            try:
                await heartbeat
            except asyncio.CancelledError:
                caller = asyncio.current_task()
                if caller is not None and caller.cancelling():
                    delivery.cancel()
                    await asyncio.gather(delivery, return_exceptions=True)
                    raise
            except Exception:
                # Delivery may already have committed a durable Task
                # transition, but the lost claim cannot authorize coordination
                # completion.  A later owner reconciles the checkpoint.
                return
            try:
                await delivery
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._defer_claim(lease, last_error=_stable_error_code(exc))
                return
            try:
                await _maybe_await(
                    self._store.complete_review_continuation(
                        lease.review_id,
                        worker_id=self._worker_id,
                        claim_token=lease.claim_token,
                        fencing_token=lease.fencing_token,
                        expected_version=lease.expected_version,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._defer_claim(lease, last_error=_stable_error_code(exc))
        finally:
            # `_process_claim` owns both child tasks.  Parent cancellation
            # during `asyncio.wait` must not orphan a delivery or a heartbeat
            # that can keep renewing authority after worker shutdown.
            stop_heartbeat.set()
            pending = tuple(task for task in (delivery, heartbeat) if not task.done())
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _deliver_claim(self, raw: Mapping[str, Any]) -> None:
        authority_raw = await _maybe_await(
            self._store.validate_review_continuation_claim(raw)
        )
        authority = _mapping(authority_raw, field_name="continuation authority")
        renewed_raw = await _maybe_await(
            self._store.validate_review_continuation_authority(authority)
        )
        renewed = _mapping(renewed_raw, field_name="renewed continuation authority")
        result = await self._runtime.continue_review_continuation(renewed)
        if not isinstance(result, Mapping):
            raise RuntimeError("review continuation runtime returned an invalid result")

    async def _heartbeat_claim(
        self,
        lease: _ClaimLease,
        stop: asyncio.Event,
    ) -> None:
        interval = max(0.001, self._claim_lease_ms / 3_000.0)
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                refreshed_raw = await _maybe_await(
                    self._store.heartbeat_review_continuation(
                        lease.review_id,
                        worker_id=self._worker_id,
                        claim_token=lease.claim_token,
                        fencing_token=lease.fencing_token,
                        lease_ms=self._claim_lease_ms,
                        expected_version=lease.expected_version,
                    )
                )
                refreshed = _mapping(refreshed_raw, field_name="continuation heartbeat")
                self._assert_same_claim(lease, refreshed)
                lease.expected_version = _non_negative_int(
                    refreshed.get("status_version"),
                    field_name="status_version",
                )

    async def _defer_claim(self, lease: _ClaimLease, *, last_error: str) -> None:
        await _maybe_await(
            self._store.defer_review_continuation(
                lease.review_id,
                worker_id=self._worker_id,
                claim_token=lease.claim_token,
                fencing_token=lease.fencing_token,
                retry_delay_ms=self._retry_delay_ms,
                last_error=last_error,
                expected_version=lease.expected_version,
            )
        )

    def _validated_claim(self, raw: Mapping[str, Any]) -> _ClaimLease:
        record = _mapping(raw, field_name="review continuation claim")
        owner = _required_text(record.get("claim_owner"), "claim_owner")
        if owner != self._worker_id:
            raise RuntimeError("review continuation claim belongs to another worker")
        review = _mapping(record.get("review"), field_name="ReviewItem")
        node = _mapping(record.get("node"), field_name="TaskNode")
        review_id = _required_text(record.get("review_id"), "review_id")
        graph_id = _required_text(record.get("graph_id"), "graph_id")
        graph_revision = _positive_int(record.get("graph_revision"), "graph_revision")
        node_id = _required_text(record.get("node_id"), "node_id")
        subject_version = _non_negative_int(
            record.get("subject_version"),
            field_name="subject_version",
        )
        resume_phase = _required_text(record.get("resume_phase"), "resume_phase")
        if (
            _required_text(review.get("review_id"), "review.review_id") != review_id
            or _required_text(node.get("graph_id"), "node.graph_id") != graph_id
            or _positive_int(node.get("graph_revision"), "node.graph_revision")
            != graph_revision
            or _required_text(node.get("node_id"), "node.node_id") != node_id
            or _non_negative_int(
                review.get("subject_version"),
                field_name="review.subject_version",
            )
            != subject_version
            or _required_text(review.get("resume_phase"), "review.resume_phase")
            != resume_phase
        ):
            raise RuntimeError("review continuation claim binding drifted")
        return _ClaimLease(
            review_id=review_id,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            subject_version=subject_version,
            resume_phase=resume_phase,
            claim_token=_required_text(record.get("claim_token"), "claim_token"),
            fencing_token=_positive_int(
                record.get("claim_fencing_token"),
                "claim_fencing_token",
            ),
            expected_version=_non_negative_int(
                record.get("status_version"),
                field_name="status_version",
            ),
        )

    def _assert_same_claim(
        self,
        lease: _ClaimLease,
        record: Mapping[str, Any],
    ) -> None:
        if (
            _required_text(record.get("review_id"), "review_id") != lease.review_id
            or _required_text(record.get("graph_id"), "graph_id") != lease.graph_id
            or _positive_int(record.get("graph_revision"), "graph_revision")
            != lease.graph_revision
            or _required_text(record.get("node_id"), "node_id") != lease.node_id
            or _required_text(record.get("claim_owner"), "claim_owner")
            != self._worker_id
            or _required_text(record.get("claim_token"), "claim_token")
            != lease.claim_token
            or _positive_int(
                record.get("claim_fencing_token"),
                "claim_fencing_token",
            )
            != lease.fencing_token
        ):
            raise RuntimeError("review continuation heartbeat returned a different claim")

    def _required_wake_event(self) -> asyncio.Event:
        if self._wake_event is None:
            raise RuntimeError("review continuation worker is not started")
        return self._wake_event

    def _required_stop_event(self) -> asyncio.Event:
        if self._stop_event is None:
            raise RuntimeError("review continuation worker is not started")
        return self._stop_event


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{field_name} must be a mapping")
    return dict(value)


def _required_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise RuntimeError(f"{field_name} is required")
    return normalized


def _positive_int(value: Any, field_name: str) -> int:
    normalized = _non_negative_int(value, field_name=field_name)
    if normalized < 1:
        raise RuntimeError(f"{field_name} must be a positive integer")
    return normalized


def _non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{field_name} must be a non-negative integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{field_name} must be a non-negative integer") from exc
    if normalized < 0:
        raise RuntimeError(f"{field_name} must be a non-negative integer")
    return normalized


def _stable_error_code(exc: Exception) -> str:
    reason = getattr(exc, "reason_code", None)
    if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", reason):
        normalized = reason
    else:
        normalized = (
            re.sub(r"[^A-Z0-9_]+", "_", exc.__class__.__name__.upper()).strip("_")
            or "UNKNOWN_ERROR"
        )[:128]
    return f"REVIEW_CONTINUATION_{normalized}"[:_MAX_ERROR_CODE_LENGTH]


__all__ = [
    "REVIEW_CONTINUATION_SHUTDOWN_TIMEOUT",
    "ReviewContinuationRuntimePort",
    "ReviewContinuationShutdownError",
    "ReviewContinuationStorePort",
    "ReviewContinuationWorker",
    "ReviewExpiryPort",
]
