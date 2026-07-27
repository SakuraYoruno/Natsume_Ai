"""Async clock producer for governed scheduler occurrences.

The clock has exactly two effects: atomically admit a durable occurrence and
invoke ``SchedulerCommandService`` with composition-injected SERVICE authority.
It never calls a task handler and never creates a thread or ``Timer``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import inspect
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from host_brain.contracts.scheduler_clock_batch import (
    SchedulerClockBatchError,
    validate_scheduler_clock_batch,
)
from host_brain.core.ids import now_ms as default_now_ms
from host_brain.scheduler.command_ingress import SchedulerCommandIngress
from host_brain.scheduler.contracts import (
    SchedulerDefinitionSnapshot,
    SchedulerOccurrence,
    SchedulerOccurrenceSource,
)
from host_brain.scheduler.governed_runtime import SchedulerCommandService


SCHEDULER_CLOCK_DEFINITION_INVALID = "SCHEDULER_CLOCK_DEFINITION_INVALID"
SCHEDULER_CLOCK_OCCURRENCE_FAILED = "SCHEDULER_CLOCK_OCCURRENCE_FAILED"


class SchedulerClockError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or SCHEDULER_CLOCK_OCCURRENCE_FAILED)
        self.detail = str(detail or "scheduler clock failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class SchedulerClockRepositoryPort(Protocol):
    def read_clock_batch(
        self,
        *,
        due_at_ms: int,
        limit: int = 100,
    ) -> Mapping[str, Any]: ...

    def list_pending_occurrences(self, *, limit: int = 100) -> list[Mapping[str, Any]]: ...

    def list_due(
        self,
        *,
        due_at_ms: int,
        limit: int = 100,
    ) -> list[Mapping[str, Any]]: ...

    def get_revision(
        self,
        task_id: str,
        revision: int,
    ) -> Mapping[str, Any] | None: ...

    def admit_due_occurrence(
        self,
        *,
        task_id: str,
        definition_revision: int,
        expected_head_version: int,
        scheduled_at_ms: int,
        next_scheduled_at_ms: int | None,
        payload: Mapping[str, Any],
        authority: Mapping[str, Any],
        due_retry_key: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        lease_duration_ms: int,
    ) -> Mapping[str, Any]: ...

    def claim_clock_retry(
        self,
        *,
        retry_key: str,
        retry_kind: str,
        task_id: str,
        occurrence_id: str | None,
        owner_id: str,
        at_ms: int,
        lease_duration_ms: int,
    ) -> Mapping[str, Any]: ...

    def defer_clock_retry(
        self,
        *,
        retry_key: str,
        owner_id: str,
        fencing_token: int,
        at_ms: int,
        next_retry_at_ms: int,
        reason_code: str,
    ) -> Mapping[str, Any]: ...

    def renew_clock_retry(
        self,
        *,
        retry_key: str,
        owner_id: str,
        fencing_token: int,
        at_ms: int,
        lease_duration_ms: int,
    ) -> Mapping[str, Any]: ...

    def clear_clock_retry(
        self,
        *,
        retry_key: str,
        owner_id: str,
        fencing_token: int,
        at_ms: int,
    ) -> bool: ...

    def clock_retry_summary(self) -> Mapping[str, Any]: ...


class SchedulerClock:
    """Lifecycle-owned polling producer with durable crash recovery."""

    def __init__(
        self,
        *,
        definition_repository: SchedulerClockRepositoryPort,
        command_ingress: SchedulerCommandIngress,
        command_service: SchedulerCommandService,
        owner_id: str,
        now_ms: Callable[[], int] = default_now_ms,
        poll_interval_s: float = 1.0,
        limit: int = 100,
        failure_backoff_initial_s: float = 5.0,
        failure_backoff_max_s: float = 300.0,
        shutdown_timeout_s: float = 5.0,
        retry_lease_duration_s: float = 60.0,
    ) -> None:
        if not callable(now_ms):
            raise TypeError("now_ms must be callable")
        self._definitions = definition_repository
        self._ingress = command_ingress
        self._commands = command_service
        self._owner_id = str(owner_id or "").strip()
        if not self._owner_id:
            raise ValueError("owner_id is required")
        self._now_ms = now_ms
        self._poll_interval_s = max(0.05, float(poll_interval_s))
        self._limit = max(1, min(1_000, int(limit)))
        self._failure_backoff_initial_ms = max(
            1_000,
            int(float(failure_backoff_initial_s) * 1_000),
        )
        self._failure_backoff_max_ms = max(
            self._failure_backoff_initial_ms,
            int(float(failure_backoff_max_s) * 1_000),
        )
        self._shutdown_timeout_s = max(0.05, float(shutdown_timeout_s))
        self._retry_lease_duration_ms = max(
            1_000,
            int(float(retry_lease_duration_s) * 1_000),
        )
        self._run_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._active = False
        self._last_tick_ms: int | None = None
        self._last_batch_ref: str | None = None
        self._last_error: str | None = None
        self._dispatch_tasks: set[asyncio.Future[Any]] = set()

    async def activate(self) -> None:
        if self._active:
            return
        self._active = True
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(
            self._run_loop(),
            name="governed-scheduler-clock",
        )

    async def start(self) -> None:
        await self.activate()

    async def aclose(self) -> None:
        if not self._active and self._loop_task is None:
            return
        self._active = False
        self._stop_event.set()
        task = self._loop_task
        self._loop_task = None
        if task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=self._shutdown_timeout_s,
                )
            except TimeoutError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def stop(self) -> None:
        await self.aclose()

    async def run_once(self) -> dict[str, Any]:
        """Recover pending clock work, then admit and execute newly-due work."""

        async with self._run_lock:
            received_at_ms = _non_negative_now(self._now_ms)
            self._last_tick_ms = received_at_ms
            recovered = 0
            admitted_count = 0
            executed = 0
            deferred = 0
            failures: list[dict[str, str]] = []
            seen: set[str] = set()

            try:
                batch = validate_scheduler_clock_batch(
                    self._definitions.read_clock_batch(
                        due_at_ms=received_at_ms,
                        limit=self._limit,
                    ),
                    expected_captured_at_ms=received_at_ms,
                    expected_limit=self._limit,
                )
            except SchedulerClockBatchError as exc:
                raise SchedulerClockError(
                    exc.reason_code,
                    exc.detail,
                ) from exc
            self._last_batch_ref = str(batch["batch_ref"])
            pending_rows = list(batch["pending_occurrences"])
            for raw in pending_rows:
                claim: Mapping[str, Any] | None = None
                dispatch_owned = False
                try:
                    occurrence = SchedulerOccurrence.from_mapping(raw)
                    if occurrence.source is not SchedulerOccurrenceSource.CLOCK:
                        continue
                    seen.add(occurrence.occurrence_id)
                    claim = self._claim_retry(
                        retry_key=occurrence.occurrence_id,
                        retry_kind="OCCURRENCE",
                        task_id=occurrence.task_id,
                        occurrence_id=occurrence.occurrence_id,
                        at_ms=received_at_ms,
                    )
                    if not bool(claim.get("claimed")):
                        deferred += 1
                        continue
                    recovered += 1
                    dispatch_owned = True
                    await self._execute_occurrence(
                        occurrence,
                        claim=claim,
                        received_at_ms=received_at_ms,
                        authority=occurrence.authority_dict(),
                    )
                    executed += 1
                except Exception as exc:
                    if (
                        claim is not None
                        and bool(claim.get("claimed"))
                        and not dispatch_owned
                    ):
                        self._record_retry(
                            claim,
                            at_ms=_non_negative_now(self._now_ms),
                            exc=exc,
                        )
                    failures.append(_failure(raw, exc))

            due_rows = list(batch["due_definitions"])
            if due_rows:
                for raw in due_rows:
                    due_key = _due_retry_key(raw)
                    claim: Mapping[str, Any] | None = None
                    occurrence: SchedulerOccurrence | None = None
                    dispatch_owned = False
                    try:
                        definition = SchedulerDefinitionSnapshot.from_mapping(raw)
                        claim = self._claim_retry(
                            retry_key=due_key,
                            retry_kind="DUE",
                            task_id=definition.task_id,
                            occurrence_id=None,
                            at_ms=received_at_ms,
                        )
                        if not bool(claim.get("claimed")):
                            deferred += 1
                            continue
                        self._commands.preflight_definition(definition)
                        scheduled_at_ms = definition.next_scheduled_at_ms
                        if scheduled_at_ms is None:
                            raise SchedulerClockError(
                                SCHEDULER_CLOCK_DEFINITION_INVALID,
                                "due definition has no scheduled timestamp",
                            )
                        payload = _occurrence_payload(definition)
                        authority = self._ingress.evaluate_clock_occurrence(
                            task_id=definition.task_id,
                            definition_revision=definition.definition_revision,
                            scheduled_at_epoch_ms=scheduled_at_ms,
                            payload=payload,
                            received_at_ms=received_at_ms,
                        )
                        claim = self._renew_retry(
                            claim,
                            at_ms=_non_negative_now(self._now_ms),
                        )
                        occurrence_raw = self._definitions.admit_due_occurrence(
                            task_id=definition.task_id,
                            definition_revision=definition.definition_revision,
                            expected_head_version=definition.head_version,
                            scheduled_at_ms=scheduled_at_ms,
                            next_scheduled_at_ms=_next_scheduled_at_ms(
                                definition,
                                scheduled_at_ms=scheduled_at_ms,
                            ),
                            payload=payload,
                            authority=authority.to_dict(),
                            due_retry_key=str(claim["retry_key"]),
                            lease_owner_id=self._owner_id,
                            lease_fencing_token=int(claim["lease_fencing_token"]),
                            lease_duration_ms=self._retry_lease_duration_ms,
                        )
                        occurrence = SchedulerOccurrence.from_mapping(occurrence_raw)
                        claim = _transferred_occurrence_claim(
                            occurrence_raw,
                            occurrence=occurrence,
                            owner_id=self._owner_id,
                            expected_fencing_token=int(claim["lease_fencing_token"]),
                        )
                        if occurrence.occurrence_id in seen:
                            self._clear_retry(
                                claim,
                                at_ms=_non_negative_now(self._now_ms),
                            )
                            continue
                        seen.add(occurrence.occurrence_id)
                        admitted_count += 1
                        dispatch_owned = True
                        await self._execute_occurrence(
                            occurrence,
                            claim=claim,
                            received_at_ms=received_at_ms,
                            authority=authority,
                        )
                        executed += 1
                    except Exception as exc:
                        failure_raw: object = occurrence.to_dict() if occurrence else raw
                        if (
                            claim is not None
                            and bool(claim.get("claimed"))
                            and not dispatch_owned
                        ):
                            self._record_retry(
                                claim,
                                at_ms=_non_negative_now(self._now_ms),
                                exc=exc,
                            )
                        failures.append(_failure(failure_raw, exc))

            self._last_error = failures[-1]["reason_code"] if failures else None
            return {
                "batch_ref": self._last_batch_ref,
                "received_at_ms": received_at_ms,
                "recovered": recovered,
                "admitted": admitted_count,
                "executed": executed,
                "deferred": deferred,
                "failed": len(failures),
                "failures": failures,
            }

    def health(self) -> dict[str, Any]:
        retry_summary = dict(self._definitions.clock_retry_summary())
        return {
            "active": self._active,
            "last_tick_ms": self._last_tick_ms,
            "last_batch_ref": self._last_batch_ref,
            "last_error": self._last_error,
            "poll_interval_s": self._poll_interval_s,
            "backoff_count": int(retry_summary.get("retry_count") or 0),
            "next_retry_at_ms": retry_summary.get("next_retry_at_ms"),
            "dispatch_in_flight": len(self._dispatch_tasks),
        }

    def _claim_retry(
        self,
        *,
        retry_key: str,
        retry_kind: str,
        task_id: str,
        occurrence_id: str | None,
        at_ms: int,
    ) -> Mapping[str, Any]:
        return self._definitions.claim_clock_retry(
            retry_key=retry_key,
            retry_kind=retry_kind,
            task_id=task_id,
            occurrence_id=occurrence_id,
            owner_id=self._owner_id,
            at_ms=at_ms,
            lease_duration_ms=self._retry_lease_duration_ms,
        )

    def _record_retry(
        self,
        claim: Mapping[str, Any],
        *,
        at_ms: int,
        exc: Exception,
    ) -> None:
        claim = self._renew_retry(claim, at_ms=at_ms)
        attempts = int(claim.get("failure_count") or 0) + 1
        delay = min(
            self._failure_backoff_max_ms,
            self._failure_backoff_initial_ms * (2 ** min(attempts - 1, 20)),
        )
        self._definitions.defer_clock_retry(
            retry_key=str(claim["retry_key"]),
            owner_id=self._owner_id,
            fencing_token=int(claim["lease_fencing_token"]),
            at_ms=at_ms,
            next_retry_at_ms=at_ms + delay,
            reason_code=str(
                getattr(exc, "reason_code", None)
                or SCHEDULER_CLOCK_OCCURRENCE_FAILED
            ),
        )

    def _renew_retry(
        self,
        claim: Mapping[str, Any],
        *,
        at_ms: int,
    ) -> Mapping[str, Any]:
        renewed = self._definitions.renew_clock_retry(
            retry_key=str(claim["retry_key"]),
            owner_id=self._owner_id,
            fencing_token=int(claim["lease_fencing_token"]),
            at_ms=at_ms,
            lease_duration_ms=self._retry_lease_duration_ms,
        )
        return {**dict(renewed), "claimed": True}

    def _clear_retry(self, claim: Mapping[str, Any], *, at_ms: int) -> None:
        claim = self._renew_retry(claim, at_ms=at_ms)
        self._definitions.clear_clock_retry(
            retry_key=str(claim["retry_key"]),
            owner_id=self._owner_id,
            fencing_token=int(claim["lease_fencing_token"]),
            at_ms=at_ms,
        )

    async def _execute_occurrence(
        self,
        occurrence: SchedulerOccurrence,
        *,
        claim: Mapping[str, Any],
        received_at_ms: int,
        authority: Any,
    ) -> None:
        dispatch = asyncio.create_task(
            self._execute_claimed_occurrence(
                occurrence,
                claim=claim,
                received_at_ms=received_at_ms,
                authority=authority,
            ),
            name=f"scheduler-occurrence-{occurrence.occurrence_id[-16:]}",
        )
        self._dispatch_tasks.add(dispatch)
        dispatch.add_done_callback(self._finish_dispatch)
        await asyncio.shield(dispatch)

    async def _execute_claimed_occurrence(
        self,
        occurrence: SchedulerOccurrence,
        *,
        claim: Mapping[str, Any],
        received_at_ms: int,
        authority: Any,
    ) -> None:
        claim = _require_occurrence_claim(
            claim,
            occurrence=occurrence,
            owner_id=self._owner_id,
        )
        claim = self._renew_retry(
            claim,
            at_ms=_non_negative_now(self._now_ms),
        )
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._maintain_retry_lease(claim, stop_event=stop_heartbeat),
            name=f"scheduler-lease-{occurrence.occurrence_id[-16:]}",
        )
        command = asyncio.create_task(
            self._invoke_occurrence(
                occurrence,
                authority=authority,
                received_at_ms=received_at_ms,
            ),
            name=f"scheduler-command-{occurrence.occurrence_id[-16:]}",
        )
        try:
            done, _ = await asyncio.wait(
                {command, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done:
                heartbeat_error = heartbeat.exception()
                if heartbeat_error is not None:
                    command.cancel()
                    with suppress(asyncio.CancelledError):
                        await command
                    raise heartbeat_error
            await command
        except asyncio.CancelledError:
            command.cancel()
            with suppress(asyncio.CancelledError):
                await command
            raise
        except Exception as exc:
            stop_heartbeat.set()
            await self._finish_heartbeat(heartbeat)
            failed_at_ms = _non_negative_now(self._now_ms)
            self._record_retry(claim, at_ms=failed_at_ms, exc=exc)
            raise
        else:
            stop_heartbeat.set()
            await self._finish_heartbeat(heartbeat)
            self._clear_retry(
                claim,
                at_ms=_non_negative_now(self._now_ms),
            )
        finally:
            stop_heartbeat.set()
            if not heartbeat.done():
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat

    async def _invoke_occurrence(
        self,
        occurrence: SchedulerOccurrence,
        *,
        authority: Any,
        received_at_ms: int,
    ) -> None:
        authority_request_id = (
            str(authority.get("request_id") or "")
            if isinstance(authority, Mapping)
            else str(getattr(authority, "request_id", None) or "")
        )
        if authority_request_id != occurrence.request_id:
            raise SchedulerClockError(
                SCHEDULER_CLOCK_OCCURRENCE_FAILED,
                "clock authority request differs from the durable occurrence",
            )
        result = self._commands.execute_occurrence(
            occurrence=occurrence,
            authority=authority,
            received_at_ms=received_at_ms,
        )
        if inspect.isawaitable(result):
            await result

    async def _maintain_retry_lease(
        self,
        claim: Mapping[str, Any],
        *,
        stop_event: asyncio.Event,
    ) -> None:
        interval_s = max(0.05, self._retry_lease_duration_ms / 3_000.0)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
                return
            except TimeoutError:
                self._renew_retry(
                    claim,
                    at_ms=_non_negative_now(self._now_ms),
                )

    @staticmethod
    async def _finish_heartbeat(heartbeat: asyncio.Task[None]) -> None:
        if not heartbeat.done():
            await heartbeat
        else:
            heartbeat.result()

    def _finish_dispatch(self, task: asyncio.Future[Any]) -> None:
        self._dispatch_tasks.discard(task)
        with suppress(asyncio.CancelledError):
            task.exception()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                self._last_error = str(
                    getattr(exc, "reason_code", None)
                    or SCHEDULER_CLOCK_OCCURRENCE_FAILED
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval_s,
                )
            except TimeoutError:
                continue


def _occurrence_payload(definition: SchedulerDefinitionSnapshot) -> dict[str, Any]:
    raw = definition.definition.get("occurrence_payload")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise SchedulerClockError(
            SCHEDULER_CLOCK_DEFINITION_INVALID,
            "definition.occurrence_payload must be an object",
        )
    # Contract construction already recursively checked the definition for
    # finite JSON and forbidden secret-like fields.
    return dict(raw)


def _next_scheduled_at_ms(
    definition: SchedulerDefinitionSnapshot,
    *,
    scheduled_at_ms: int,
) -> int | None:
    trigger_mode = str(definition.definition.get("trigger_mode") or "").strip().lower()
    trigger_config = definition.definition.get("trigger_config")
    config = dict(trigger_config) if isinstance(trigger_config, Mapping) else {}
    if trigger_mode == "once":
        return None
    if trigger_mode == "interval":
        if "milliseconds" in config:
            interval_ms = _positive_int(config["milliseconds"], "milliseconds")
        else:
            seconds = config.get("seconds", 900)
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
                raise SchedulerClockError(
                    SCHEDULER_CLOCK_DEFINITION_INVALID,
                    "interval seconds must be numeric",
                )
            interval_ms = int(float(seconds) * 1_000)
            if interval_ms < 1:
                raise SchedulerClockError(
                    SCHEDULER_CLOCK_DEFINITION_INVALID,
                    "interval must be at least one millisecond",
                )
        return scheduled_at_ms + interval_ms
    if trigger_mode == "cron":
        return _next_daily_cron(config, scheduled_at_ms=scheduled_at_ms)
    raise SchedulerClockError(
        SCHEDULER_CLOCK_DEFINITION_INVALID,
        f"unsupported trigger mode: {trigger_mode or '<empty>'}",
    )


def _next_daily_cron(config: Mapping[str, Any], *, scheduled_at_ms: int) -> int:
    hour = _bounded_int(config.get("hour", 0), "hour", maximum=23)
    minute = _bounded_int(config.get("minute", 0), "minute", maximum=59)
    second = _bounded_int(config.get("second", 0), "second", maximum=59)
    zone_name = str(config.get("timezone") or "UTC").strip()
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise SchedulerClockError(
            SCHEDULER_CLOCK_DEFINITION_INVALID,
            f"unknown cron timezone: {zone_name}",
        ) from exc
    current = datetime.fromtimestamp(scheduled_at_ms / 1_000.0, tz=timezone.utc).astimezone(zone)
    weekdays = _cron_weekdays(config.get("day_of_week"))
    for days_ahead in range(0, 8):
        day = current + timedelta(days=days_ahead)
        if weekdays is not None and day.weekday() not in weekdays:
            continue
        candidate = day.replace(hour=hour, minute=minute, second=second, microsecond=0)
        if candidate > current:
            return int(candidate.astimezone(timezone.utc).timestamp() * 1_000)
    raise SchedulerClockError(
        SCHEDULER_CLOCK_DEFINITION_INVALID,
        "cron weekday set produced no next occurrence",
    )


def _cron_weekdays(value: object) -> frozenset[int] | None:
    if value is None:
        return None
    raw_values = value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
    if not raw_values:
        raise SchedulerClockError(
            SCHEDULER_CLOCK_DEFINITION_INVALID,
            "day_of_week must not be empty",
        )
    weekdays: set[int] = set()
    for raw in raw_values:
        if type(raw) is not int or raw < 0 or raw > 6:
            raise SchedulerClockError(
                SCHEDULER_CLOCK_DEFINITION_INVALID,
                "day_of_week must contain integers from 0 (Monday) to 6 (Sunday)",
            )
        weekdays.add(raw)
    return frozenset(weekdays)


def _failure(raw: object, exc: Exception) -> dict[str, str]:
    task_id = str(dict(raw).get("task_id") or "") if isinstance(raw, Mapping) else ""
    occurrence_id = (
        str(dict(raw).get("occurrence_id") or "") if isinstance(raw, Mapping) else ""
    )
    return {
        "task_id": task_id,
        "occurrence_id": occurrence_id,
        "reason_code": str(
            getattr(exc, "reason_code", None) or SCHEDULER_CLOCK_OCCURRENCE_FAILED
        ),
    }


def _transferred_occurrence_claim(
    raw: Mapping[str, Any],
    *,
    occurrence: SchedulerOccurrence,
    owner_id: str,
    expected_fencing_token: int,
) -> Mapping[str, Any]:
    claim = raw.get("clock_retry_claim")
    if not isinstance(claim, Mapping):
        raise SchedulerClockError(
            SCHEDULER_CLOCK_OCCURRENCE_FAILED,
            "clock admission did not return its atomically transferred occurrence claim",
        )
    validated = _require_occurrence_claim(
        claim,
        occurrence=occurrence,
        owner_id=owner_id,
    )
    if int(validated["lease_fencing_token"]) != expected_fencing_token:
        raise SchedulerClockError(
            SCHEDULER_CLOCK_OCCURRENCE_FAILED,
            "clock admission changed the DUE fencing token during transfer",
        )
    return validated


def _require_occurrence_claim(
    claim: Mapping[str, Any],
    *,
    occurrence: SchedulerOccurrence,
    owner_id: str,
) -> Mapping[str, Any]:
    value = dict(claim)
    if (
        not bool(value.get("claimed"))
        or str(value.get("retry_kind") or "").upper() != "OCCURRENCE"
        or str(value.get("retry_key") or "") != occurrence.occurrence_id
        or str(value.get("occurrence_id") or "") != occurrence.occurrence_id
        or str(value.get("task_id") or "") != occurrence.task_id
        or str(value.get("lease_owner_id") or "") != owner_id
        or type(value.get("lease_fencing_token")) is not int
        or int(value["lease_fencing_token"]) < 1
    ):
        raise SchedulerClockError(
            SCHEDULER_CLOCK_OCCURRENCE_FAILED,
            "scheduler dispatch requires an exact occurrence-bound owner fence",
        )
    return value


def _due_retry_key(raw: object) -> str:
    if not isinstance(raw, Mapping):
        return "due:invalid"
    data = dict(raw)
    return (
        "due:"
        f"{str(data.get('task_id') or '')}:"
        f"{str(data.get('definition_revision') or '')}:"
        f"{str(data.get('next_scheduled_at_ms') or '')}"
    )


def _non_negative_now(clock: Callable[[], int]) -> int:
    value = clock()
    if type(value) is not int or value < 0:
        raise SchedulerClockError(
            SCHEDULER_CLOCK_DEFINITION_INVALID,
            "clock must return a non-negative integer epoch millisecond",
        )
    return value


def _positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise SchedulerClockError(
            SCHEDULER_CLOCK_DEFINITION_INVALID,
            f"{field_name} must be an integer >= 1",
        )
    return value


def _bounded_int(value: object, field_name: str, *, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise SchedulerClockError(
            SCHEDULER_CLOCK_DEFINITION_INVALID,
            f"{field_name} must be an integer between 0 and {maximum}",
        )
    return value


__all__ = [
    "SCHEDULER_CLOCK_DEFINITION_INVALID",
    "SCHEDULER_CLOCK_OCCURRENCE_FAILED",
    "SchedulerClock",
    "SchedulerClockError",
]
