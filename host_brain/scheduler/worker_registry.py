"""Post-Acceptance Scheduler handler registry and plan-worker resolver.

The real Scheduler handlers registered here are resolved only by the
Acceptance Effect handler.  The Task Runtime resolver deliberately owns a
different object: a canonical plan worker which can describe an immutable
operation but cannot reach this registry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Mapping
from typing import Any, Protocol

from natsume_contracts.task import HostCommand

from host_brain.scheduler.contracts import (
    SCHEDULER_WORKER_ROUTE,
    SchedulerDefinitionSnapshot,
    SchedulerOccurrence,
)
from host_brain.task_runtime.worker_registry import (
    GovernedWorkerRegistryError,
    GovernedWorkerRoute,
)


SCHEDULER_HANDLER_NOT_REGISTERED = "SCHEDULER_HANDLER_NOT_REGISTERED"
SCHEDULER_DURABLE_RESULT_INVALID = "SCHEDULER_DURABLE_RESULT_INVALID"


class SchedulerWorkerError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or SCHEDULER_DURABLE_RESULT_INVALID)
        self.detail = str(detail or "scheduler worker failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class SchedulerHandler(Protocol):
    def __call__(
        self,
        *,
        occurrence: SchedulerOccurrence,
        definition: SchedulerDefinitionSnapshot,
        command: HostCommand,
        execution_context: Mapping[str, Any],
    ) -> Mapping[str, Any] | Awaitable[Mapping[str, Any]]: ...


class SchedulerWorkerRegistry:
    """Composition-owned exact map of post-Acceptance real handlers."""

    def __init__(self, *, default_handler: SchedulerHandler | None = None) -> None:
        self._handlers: dict[str, SchedulerHandler] = {}
        if default_handler is not None:
            self._handlers[SCHEDULER_WORKER_ROUTE] = _handler(default_handler)

    def register(self, worker_key: str, handler: SchedulerHandler) -> None:
        key = _worker_key(worker_key)
        if key in self._handlers:
            raise ValueError(f"scheduler worker key already registered: {key}")
        self._handlers[key] = _handler(handler)

    def register_default(self, handler: SchedulerHandler) -> None:
        self.register(SCHEDULER_WORKER_ROUTE, handler)

    @property
    def worker_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def resolve(self, *, worker_key: str, task_id: str) -> SchedulerHandler:
        key = _worker_key(worker_key)
        handler = self._handlers.get(key)
        if handler is None:
            raise SchedulerWorkerError(
                SCHEDULER_HANDLER_NOT_REGISTERED,
                f"no governed scheduler handler is registered for worker_key={key!r}",
            )
        return handler


class SchedulerPlanWorker(Protocol):
    """Canonical pre-Acceptance worker that can only emit an operation plan."""

    def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        execution_context: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]: ...


class SchedulerWorkerResolver:
    """Resolve every Scheduler node to one handler-free canonical plan worker."""

    def __init__(
        self,
        *,
        canonical_worker: SchedulerPlanWorker,
    ) -> None:
        if not callable(getattr(canonical_worker, "execute_stream", None)):
            raise TypeError("canonical_worker must expose execute_stream")
        self._canonical = canonical_worker

    def resolve(self, *, executor: str, worker_route: str) -> SchedulerPlanWorker:
        executor_name = str(executor or "").strip().lower()
        if executor_name != "canonical":
            raise SchedulerWorkerError(
                SCHEDULER_HANDLER_NOT_REGISTERED,
                "scheduler executor assignment is invalid",
            )
        try:
            route = GovernedWorkerRoute.parse(str(worker_route or ""))
        except GovernedWorkerRegistryError as exc:
            raise SchedulerWorkerError(
                SCHEDULER_HANDLER_NOT_REGISTERED,
                "scheduler worker route is not registered",
            ) from exc
        if route.kind != "delegate" or not route.target_id:
            raise SchedulerWorkerError(
                SCHEDULER_HANDLER_NOT_REGISTERED,
                "scheduler worker route must be canonical delegate:<worker_key>",
            )
        return self._canonical


def _handler(handler: SchedulerHandler) -> SchedulerHandler:
    if not callable(handler):
        raise TypeError("scheduler handler must be callable")
    return handler


def _worker_key(value: object) -> str:
    import re

    key = str(value or "").strip().lower()
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) is None:
        raise ValueError("worker_key must be a bounded lower-case identifier")
    return key


__all__ = [
    "SCHEDULER_DURABLE_RESULT_INVALID",
    "SCHEDULER_HANDLER_NOT_REGISTERED",
    "SchedulerHandler",
    "SchedulerPlanWorker",
    "SchedulerWorkerError",
    "SchedulerWorkerRegistry",
    "SchedulerWorkerResolver",
]
