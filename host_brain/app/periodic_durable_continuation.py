"""Lifecycle-owned supervision for exact durable continuation scans.

The supervisor owns cadence and shutdown only.  It deliberately receives an
already-composed continuation callable so it cannot invent admission,
execution, or recovery authority of its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
import re
import time
from typing import Any


ContinuationRunner = Callable[
    [],
    Awaitable[Sequence[Mapping[str, Any]]],
]
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,119}")


class PeriodicDurableContinuation:
    """Serialize one bounded continuation scan across wakeups and timer ticks."""

    def __init__(
        self,
        *,
        runner: ContinuationRunner,
        task_name: str,
        failure_reason_code: str,
        interval_ms: int = 5_000,
    ) -> None:
        if not callable(runner):
            raise TypeError("periodic durable continuation runner must be callable")
        normalized_name = str(task_name or "").strip()
        normalized_failure = str(failure_reason_code or "").strip()
        if not normalized_name or not _REASON_CODE.fullmatch(normalized_failure):
            raise ValueError(
                "periodic durable continuation task name and stable failure code "
                "are required"
            )
        if int(interval_ms) < 10:
            raise ValueError(
                "periodic durable continuation interval must be at least 10ms"
            )
        self._runner = runner
        self._task_name = normalized_name
        self._failure_reason_code = normalized_failure
        self._interval_s = int(interval_ms) / 1_000.0
        self._cycle_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._wake_event: asyncio.Event | None = None
        self._last_report: tuple[dict[str, Any], ...] = ()
        self._last_failure: dict[str, Any] | None = None
        self._completed_cycles = 0

    async def run_once(self) -> tuple[dict[str, Any], ...]:
        """Run one exact scan and surface failures to boot-time callers."""

        async with self._cycle_lock:
            try:
                raw_report = await self._runner()
                report = _normalize_report(raw_report)
            except Exception as exc:
                self._last_failure = {
                    "reason_code": _safe_reason_code(
                        getattr(exc, "reason_code", None),
                        fallback=self._failure_reason_code,
                    ),
                    "error_type": type(exc).__name__,
                    "ts_ms": time.time_ns() // 1_000_000,
                }
                raise
            self._last_report = report
            self._last_failure = None
            self._completed_cycles += 1
            return tuple(dict(item) for item in report)

    async def start(self) -> None:
        """Start polling after the caller has completed its mandatory boot scan."""

        task = self._task
        if task is not None and not task.done():
            return
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._run_loop(
                stop_event=self._stop_event,
                wake_event=self._wake_event,
            ),
            name=self._task_name,
        )

    def wake(self) -> None:
        """Coalesce any number of event-driven wakeups into the next scan."""

        wake_event = self._wake_event
        task = self._task
        if wake_event is not None and task is not None and not task.done():
            wake_event.set()

    def request_stop(self) -> None:
        """Prevent another scan while allowing an active durable scan to finish."""

        stop_event = self._stop_event
        wake_event = self._wake_event
        if stop_event is not None:
            stop_event.set()
        if wake_event is not None:
            wake_event.set()

    async def join(self, *, timeout_s: float) -> None:
        """Wait for quiescence without cancelling a transaction in progress."""

        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=max(0.1, float(timeout_s)),
            )
        except TimeoutError as exc:
            raise RuntimeError(
                f"{self._failure_reason_code}_STOP_TIMEOUT"
            ) from exc
        if self._task is task:
            self._task = None
            self._stop_event = None
            self._wake_event = None

    def health_snapshot(self) -> dict[str, Any]:
        task = self._task
        stop_event = self._stop_event
        return {
            "running": bool(task is not None and not task.done()),
            "stop_requested": bool(stop_event is not None and stop_event.is_set()),
            "completed_cycles": int(self._completed_cycles),
            "last_report": tuple(dict(item) for item in self._last_report),
            "last_failure": (
                dict(self._last_failure)
                if self._last_failure is not None
                else None
            ),
        }

    async def _run_loop(
        self,
        *,
        stop_event: asyncio.Event,
        wake_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    wake_event.wait(),
                    timeout=self._interval_s,
                )
            except TimeoutError:
                pass
            wake_event.clear()
            if stop_event.is_set():
                break
            try:
                await self.run_once()
            except Exception:
                # ``run_once`` retained an opaque health record.  The next
                # bounded timer tick retries the same canonical authority.
                continue


def _normalize_report(
    value: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise TypeError("durable continuation report must be a sequence")
    report: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TypeError("durable continuation report item must be a mapping")
        report.append(dict(item))
    return tuple(report)


def _safe_reason_code(value: Any, *, fallback: str) -> str:
    candidate = str(value or "").strip()
    if _REASON_CODE.fullmatch(candidate):
        return candidate
    return str(fallback)


__all__ = ["PeriodicDurableContinuation"]
