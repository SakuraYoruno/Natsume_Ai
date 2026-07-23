"""Process-owned composition root for the durable Host Task Runtime spine.

This module owns infrastructure construction and lifecycle only.  It never
imports the ASGI/app layer and does not hide dependencies behind globals.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import inspect
import itertools
import os
from pathlib import Path
import threading
from typing import Any
import uuid

from host_brain.core.ids import now_ms
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
    RecoveryReport,
)
from host_brain.audit.migration_runner import (
    MigrationBatchResult,
    SQLiteMigrationRunner,
)
from host_brain.integrations.runtime_events import (
    RuntimeEventStore,
    RuntimeOutboxPublisher,
)
from host_brain.persistence.agent_execution_repository import (
    AgentExecutionResultRepository,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.internal_agent_checkpoint_uow import (
    DurableInternalAgentCheckpointUnitOfWork,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.review_continuation_store import ReviewContinuationStore
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore
from host_brain.persistence.scheduler_repository import (
    SchedulerDefinitionRepository,
    SchedulerExecutionResultRepository,
)
from host_brain.persistence.terminal_operator_command_store import (
    TerminalOperatorCommandStore,
)
from host_brain.persistence.task_runtime_feature_canary_repository import (
    SQLiteTaskRuntimeFeatureCanaryReceiptStore,
)
from host_brain.task_runtime.graph_cancellation_continuation_worker import (
    GraphCancellationContinuationWorker,
)
from host_brain.task_runtime.graph_cancellation_runtime import (
    GraphCancellationRuntime,
)
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationService,
)
from host_brain.task_runtime.graph_cancellation_effect_reconciliation import (
    GraphCancellationEffectReconciliationService,
)
from host_brain.task_runtime.terminal_state_reconciliation import (
    TerminalStateReconciliationService,
)
from host_brain.task_runtime.local_graph_execution_registry import (
    LocalGraphExecutionRegistry,
)
from host_brain.task_runtime.runtime_outbox import RuntimeOutbox


TASK_RUNTIME_CANONICAL_DB_PATH_INVALID = "TASK_RUNTIME_CANONICAL_DB_PATH_INVALID"
TASK_RUNTIME_CANONICAL_DB_PATH_MISMATCH = "TASK_RUNTIME_CANONICAL_DB_PATH_MISMATCH"
TASK_RUNTIME_MIGRATION_FAILED = "TASK_RUNTIME_MIGRATION_FAILED"
TASK_RUNTIME_CONSTRUCTION_FAILED = "TASK_RUNTIME_CONSTRUCTION_FAILED"
TASK_RUNTIME_PROCESS_SCOPE_VIOLATION = "TASK_RUNTIME_PROCESS_SCOPE_VIOLATION"
TASK_RUNTIME_ALREADY_BOUND = "TASK_RUNTIME_ALREADY_BOUND"
TASK_RUNTIME_COORDINATOR_INVALID = "TASK_RUNTIME_COORDINATOR_INVALID"
TASK_RUNTIME_COORDINATOR_NOT_BOUND = "TASK_RUNTIME_COORDINATOR_NOT_BOUND"
TASK_RUNTIME_ACTIVATION_FAILED = "TASK_RUNTIME_ACTIVATION_FAILED"
TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED = "TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED"
TASK_RUNTIME_DRAIN_FAILED = "TASK_RUNTIME_DRAIN_FAILED"
TASK_RUNTIME_CLOSE_ALREADY_RUNNING = "TASK_RUNTIME_CLOSE_ALREADY_RUNNING"
TASK_RUNTIME_CLOSE_FAILED = "TASK_RUNTIME_CLOSE_FAILED"


class TaskRuntimeContainerError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or TASK_RUNTIME_CONSTRUCTION_FAILED)
        self.detail = str(detail or "task runtime composition failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class TaskRuntimeCloseFailure:
    component: str
    reason_code: str


class TaskRuntimeContainerCloseError(TaskRuntimeContainerError):
    def __init__(self, failures: tuple[TaskRuntimeCloseFailure, ...]) -> None:
        self.failures = failures
        super().__init__(
            TASK_RUNTIME_CLOSE_FAILED,
            ",".join(failure.component for failure in failures),
        )


class TaskRuntimeContainerConstructionError(TaskRuntimeContainerError):
    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        rollback_failures: tuple[TaskRuntimeCloseFailure, ...] = (),
    ) -> None:
        self.rollback_failures = rollback_failures
        super().__init__(reason_code, detail)


MigrationRunnerBuilder = Callable[..., SQLiteMigrationRunner]
TaskStoreBuilder = Callable[..., DurableTaskStore]
CommandRepositoryBuilder = Callable[..., HostCommandRepository]
ReviewStoreBuilder = Callable[..., ReviewStore]
ReviewContinuationStoreBuilder = Callable[..., ReviewContinuationStore]
WorkerProjectionStoreBuilder = Callable[..., WorkerProjectionStore]
InternalAgentCheckpointUowBuilder = Callable[
    ...,
    DurableInternalAgentCheckpointUnitOfWork,
]
AgentResultRepositoryBuilder = Callable[..., AgentExecutionResultRepository]
SchedulerDefinitionRepositoryBuilder = Callable[..., SchedulerDefinitionRepository]
SchedulerResultRepositoryBuilder = Callable[..., SchedulerExecutionResultRepository]
RuntimeEventStoreBuilder = Callable[..., RuntimeEventStore]
RuntimeOutboxPublisherBuilder = Callable[..., RuntimeOutboxPublisher]
TerminalOperatorCommandStoreBuilder = Callable[..., TerminalOperatorCommandStore]
FeatureCanaryStoreBuilder = Callable[..., SQLiteTaskRuntimeFeatureCanaryReceiptStore]


_PROCESS_BOOT_ID = uuid.uuid4().hex
_CONTAINER_CONSTRUCTION_SEQUENCE = itertools.count(1)
_CONTAINER_CONSTRUCTION_SEQUENCE_LOCK = threading.Lock()


_INFRASTRUCTURE_ORDER = (
    "migration_runner",
    "feature_canary_store",
    "task_store",
    "review_store",
    "review_continuation_store",
    "worker_projection_store",
    "internal_agent_checkpoint_uow",
    "command_repository",
    "agent_result_repository",
    "scheduler_definition_repository",
    "scheduler_result_repository",
    "event_store",
    "outbox_publisher",
    "cancellation_continuation_worker",
)


@dataclass(slots=True)
class ProcessTaskRuntimeContainer:
    """Durable Task Runtime infrastructure owned by exactly one Host process."""

    ledger: Any
    canonical_db_path: str
    migration_runner: SQLiteMigrationRunner
    migration_result: MigrationBatchResult
    feature_canary_store: SQLiteTaskRuntimeFeatureCanaryReceiptStore
    feature_canary_signature_verifier: Ed25519ReconciliationProofVerifier | None
    task_store: DurableTaskStore
    recovery_id: str
    recovery_report: RecoveryReport
    review_store: ReviewStore
    review_continuation_store: ReviewContinuationStore
    worker_projection_store: WorkerProjectionStore
    internal_agent_checkpoint_uow: DurableInternalAgentCheckpointUnitOfWork
    command_repository: HostCommandRepository
    agent_result_repository: AgentExecutionResultRepository
    scheduler_definition_repository: SchedulerDefinitionRepository
    scheduler_result_repository: SchedulerExecutionResultRepository
    event_store: RuntimeEventStore
    outbox_publisher: RuntimeOutboxPublisher
    graph_execution_registry: LocalGraphExecutionRegistry
    cancellation_continuation_worker: GraphCancellationContinuationWorker
    operation_reconciliation_service: GraphCancellationOperationReconciliationService
    effect_reconciliation_service: GraphCancellationEffectReconciliationService
    terminal_reconciliation_service: TerminalStateReconciliationService
    terminal_operator_command_store: TerminalOperatorCommandStore
    terminal_reconciliation_inventory: Mapping[str, Any]
    terminal_reconciliation_auto_result: Mapping[str, Any]
    startup_publish_result: Mapping[str, Any]
    owner_process_id: int
    _bound_runtime: Any | None = field(default=None, init=False, repr=False)
    _activated: bool = field(default=False, init=False, repr=False)
    _lifecycle_retry_required: bool = field(
        default=False,
        init=False,
        repr=False,
    )
    _closed: bool = field(default=False, init=False, repr=False)
    _closing: bool = field(default=False, init=False, repr=False)
    _lifecycle_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _last_drain_result: Mapping[str, Any] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    @property
    def infrastructure_ready(self) -> bool:
        return bool(self.migration_result.success and not self._closed)

    @property
    def activated(self) -> bool:
        return self._activated

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def bound_runtime(self) -> Any | None:
        return self._bound_runtime

    @property
    def last_drain_result(self) -> Mapping[str, Any]:
        return self._last_drain_result

    @property
    def construction_order(self) -> tuple[str, ...]:
        return _INFRASTRUCTURE_ORDER

    def execution_owner_id(self, logical_owner_id: str) -> str:
        """Bind a configured logical owner to this boot/construction instance."""

        logical = str(logical_owner_id or "").strip()
        if not logical:
            raise TaskRuntimeContainerError(
                TASK_RUNTIME_COORDINATOR_INVALID,
                "logical task runtime owner is required",
            )
        instance_digest = hashlib.sha256(
            self.recovery_id.encode("utf-8")
        ).hexdigest()[:24]
        return f"{logical}::runtime::{instance_digest}"

    def bind_runtime(self, coordinator: Any) -> None:
        """Bind, but do not activate, the future application coordinator."""

        self._assert_process_owner()
        with self._lifecycle_lock:
            if self._closed or self._closing:
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_COORDINATOR_INVALID,
                    "cannot bind a closed task runtime container",
                )
            if self._lifecycle_retry_required:
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED,
                    "task runtime lifecycle requires close retry before binding",
                )
            if self._bound_runtime is not None:
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_ALREADY_BOUND,
                    "task runtime coordinator is already bound",
                )
            activate = getattr(coordinator, "activate", None) or getattr(
                coordinator, "start", None
            )
            close = getattr(coordinator, "aclose", None) or getattr(
                coordinator, "close", None
            )
            if not callable(activate) or not callable(close):
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_COORDINATOR_INVALID,
                    "coordinator requires activate/start and close/aclose methods",
                )
            self._bound_runtime = coordinator

    async def activate_runtime(self) -> None:
        self._assert_process_owner()
        with self._lifecycle_lock:
            if self._closed or self._closing:
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_ACTIVATION_FAILED,
                    "cannot activate a closing or closed task runtime container",
                )
            if self._lifecycle_retry_required:
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED,
                    "task runtime lifecycle requires close retry before activation",
                )
        if self._activated:
            return
        coordinator = self._bound_runtime
        if coordinator is None:
            raise TaskRuntimeContainerError(
                TASK_RUNTIME_COORDINATOR_NOT_BOUND,
                "bind_runtime must complete before activation",
            )
        activate = getattr(coordinator, "activate", None) or getattr(
            coordinator, "start", None
        )
        try:
            result = activate()
            if inspect.isawaitable(result):
                await result
            await self.cancellation_continuation_worker.start()
        except asyncio.CancelledError:
            rollback_succeeded = await _rollback_runtime_activation(
                coordinator=coordinator,
                cancellation_worker=self.cancellation_continuation_worker,
            )
            with self._lifecycle_lock:
                if rollback_succeeded and self._bound_runtime is coordinator:
                    self._bound_runtime = None
                self._activated = False
                self._lifecycle_retry_required = not rollback_succeeded
            raise
        except Exception as exc:
            rollback_succeeded = await _rollback_runtime_activation(
                coordinator=coordinator,
                cancellation_worker=self.cancellation_continuation_worker,
            )
            with self._lifecycle_lock:
                if rollback_succeeded and self._bound_runtime is coordinator:
                    self._bound_runtime = None
                self._activated = False
                self._lifecycle_retry_required = not rollback_succeeded
            raise TaskRuntimeContainerError(
                TASK_RUNTIME_ACTIVATION_FAILED,
                "bound task runtime coordinator activation failed",
            ) from exc
        with self._lifecycle_lock:
            self._activated = True
            self._lifecycle_retry_required = False

    async def drain_outbox(
        self,
        *,
        batch_limit: int = 1_000,
        max_batches: int = 64,
    ) -> Mapping[str, Any]:
        """Publish ready rows and report durable deferred work truthfully."""

        self._assert_process_owner()
        limit = max(1, int(batch_limit))
        batches = max(1, int(max_batches))
        total_scanned = 0
        total_published = 0
        for batch_no in range(1, batches + 1):
            result = self.outbox_publisher.publish_pending(limit=limit)
            scanned = int(result.get("pending_scanned") or 0)
            published = int(result.get("published_count") or 0)
            if scanned < 0 or published < 0 or published > scanned:
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_DRAIN_FAILED,
                    "outbox publisher returned an invalid drain result",
                )
            total_scanned += scanned
            total_published += published
            backlog = result.get("backlog")
            if not isinstance(backlog, Mapping):
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_DRAIN_FAILED,
                    "outbox publisher omitted durable backlog state",
                )
            try:
                remaining = int(backlog["total_pending"])
                delivery_ready = int(backlog["delivery_ready_count"])
                raw_wakeup = backlog["next_wakeup_ts_ms"]
                next_wakeup = None if raw_wakeup is None else int(raw_wakeup)
            except (KeyError, TypeError, ValueError) as exc:
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_DRAIN_FAILED,
                    "outbox publisher returned invalid durable backlog state",
                ) from exc
            if (
                remaining < 0
                or delivery_ready < 0
                or delivery_ready > remaining
                or (remaining == 0 and next_wakeup is not None)
                or (remaining > 0 and next_wakeup is None)
            ):
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_DRAIN_FAILED,
                    "outbox publisher returned impossible durable backlog state",
                )
            if remaining == 0:
                summary = {
                    "status": "DRAINED",
                    "batch_count": batch_no,
                    "pending_scanned": total_scanned,
                    "published_count": total_published,
                    "remaining_pending": 0,
                    "delivery_ready_count": 0,
                    "next_wakeup_ts_ms": None,
                }
                self._last_drain_result = summary
                return summary
            if delivery_ready == 0:
                summary = {
                    "status": "DEFERRED",
                    "batch_count": batch_no,
                    "pending_scanned": total_scanned,
                    "published_count": total_published,
                    "remaining_pending": remaining,
                    "delivery_ready_count": 0,
                    "next_wakeup_ts_ms": next_wakeup,
                }
                self._last_drain_result = summary
                return summary
        raise TaskRuntimeContainerError(
            TASK_RUNTIME_DRAIN_FAILED,
            f"outbox drain exceeded {batches} batches",
        )

    async def aclose(self) -> None:
        """Quiesce every producer, drain durable outbox, then close stores."""

        self._assert_process_owner()
        if not self._begin_close():
            return
        failures: list[TaskRuntimeCloseFailure] = []
        infrastructure_closed = False
        try:
            coordinator = self._bound_runtime
            if coordinator is not None:
                await _call_optional_lifecycle(
                    coordinator,
                    ("close_admission", "stop_admission", "stop_accepting"),
                    component="runtime_coordinator_admission",
                    failures=failures,
                )
                await _call_optional_lifecycle(
                    coordinator,
                    ("drain",),
                    component="runtime_coordinator_drain",
                    failures=failures,
                )
            try:
                await self.cancellation_continuation_worker.stop()
                await self.cancellation_continuation_worker.join()
            except asyncio.CancelledError:
                raise
            except Exception:
                failures.append(
                    TaskRuntimeCloseFailure(
                        component="graph_cancellation_continuation_quiescence",
                        reason_code=TASK_RUNTIME_CLOSE_FAILED,
                    )
                )
                # A continuation may still be using TaskStore/CommandStore.
                # Never close those stores underneath a non-quiescent worker;
                # report a bounded failure and allow a later shutdown retry.
                raise TaskRuntimeContainerCloseError(tuple(failures))

            if coordinator is not None:
                close = getattr(coordinator, "aclose", None) or getattr(
                    coordinator,
                    "close",
                    None,
                )
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failures.append(
                        TaskRuntimeCloseFailure(
                            component="runtime_coordinator_quiescence",
                            reason_code=TASK_RUNTIME_CLOSE_FAILED,
                        )
                    )
                if failures:
                    # Admission, drain, and close are all quiescence proofs.
                    # Preserve every Store until a later retry proves all
                    # coordinator children have stopped.
                    raise TaskRuntimeContainerCloseError(tuple(failures))

            try:
                await self.drain_outbox()
            except Exception:
                failures.append(
                    TaskRuntimeCloseFailure(
                        component="outbox_drain",
                        reason_code=TASK_RUNTIME_DRAIN_FAILED,
                    )
                )

            close_order = list(
                (name, getattr(self, name))
                for name in reversed(self.construction_order)
            )
            for name, component in close_order:
                await _close_component(name, component, failures)
            infrastructure_closed = True
        finally:
            with self._lifecycle_lock:
                self._activated = False
                if infrastructure_closed:
                    self._closed = True
                    self._lifecycle_retry_required = False
                else:
                    self._lifecycle_retry_required = True
                self._closing = False
        if failures:
            raise TaskRuntimeContainerCloseError(tuple(failures))

    def _begin_close(self) -> bool:
        with self._lifecycle_lock:
            if self._closed:
                return False
            if self._closing:
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_CLOSE_ALREADY_RUNNING,
                    "task runtime close is already running",
                )
            self._closing = True
            return True

    def _assert_process_owner(self) -> None:
        if os.getpid() != self.owner_process_id:
            raise TaskRuntimeContainerError(
                TASK_RUNTIME_PROCESS_SCOPE_VIOLATION,
                "task runtime container cannot be used by an inherited process",
            )


async def _call_optional_lifecycle(
    target: Any,
    method_names: tuple[str, ...],
    *,
    component: str,
    failures: list[TaskRuntimeCloseFailure],
) -> None:
    for method_name in method_names:
        method = getattr(target, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
            if inspect.isawaitable(result):
                await result
        except Exception:
            failures.append(
                TaskRuntimeCloseFailure(
                    component=component,
                    reason_code=TASK_RUNTIME_CLOSE_FAILED,
                )
            )
        return


async def _rollback_runtime_activation(
    *,
    coordinator: Any,
    cancellation_worker: Any,
) -> bool:
    """Best-effort reverse activation without exposing a half-live runtime."""

    succeeded = True
    for target, method_names in (
        (cancellation_worker, ("stop",)),
        (cancellation_worker, ("join",)),
        (coordinator, ("aclose", "close")),
    ):
        for method_name in method_names:
            method = getattr(target, method_name, None)
            if not callable(method):
                continue
            try:
                result = method()
                if inspect.isawaitable(result):
                    await result
            except (Exception, asyncio.CancelledError):
                succeeded = False
            break
    return succeeded


async def _close_component(
    name: str,
    component: Any,
    failures: list[TaskRuntimeCloseFailure],
) -> None:
    method = getattr(component, "aclose", None) or getattr(component, "close", None)
    if not callable(method):
        return
    try:
        result = method()
        if inspect.isawaitable(result):
            await result
    except Exception:
        failures.append(
            TaskRuntimeCloseFailure(
                component=name,
                reason_code=TASK_RUNTIME_CLOSE_FAILED,
            )
        )


def _close_constructed_sync(
    constructed: list[tuple[str, Any]],
) -> tuple[TaskRuntimeCloseFailure, ...]:
    failures: list[TaskRuntimeCloseFailure] = []
    for name, component in reversed(constructed):
        method = getattr(component, "close", None)
        if not callable(method):
            continue
        try:
            result = method()
            if inspect.isawaitable(result):
                close_coroutine = getattr(result, "close", None)
                if callable(close_coroutine):
                    close_coroutine()
                raise RuntimeError("async-only rollback is not supported during construction")
        except Exception:
            failures.append(
                TaskRuntimeCloseFailure(
                    component=name,
                    reason_code=TASK_RUNTIME_CLOSE_FAILED,
                )
            )
    return tuple(failures)


def _canonical_path(ledger: Any, canonical_db_path: str | Path) -> str:
    raw = str(canonical_db_path or "").strip()
    if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
        raise TaskRuntimeContainerConstructionError(
            TASK_RUNTIME_CANONICAL_DB_PATH_INVALID,
            "durable canonical SQLite path is required",
        )
    canonical = str(Path(raw).expanduser().resolve())
    ledger_raw = str(getattr(ledger, "canonical_db_path", "") or "").strip()
    if not ledger_raw or ledger_raw == ":memory:" or ledger_raw.lower().startswith("file:"):
        raise TaskRuntimeContainerConstructionError(
            TASK_RUNTIME_CANONICAL_DB_PATH_INVALID,
            "ledger does not expose a durable canonical SQLite path",
        )
    ledger_canonical = str(Path(ledger_raw).expanduser().resolve())
    if _path_identity(ledger_canonical) != _path_identity(canonical):
        raise TaskRuntimeContainerConstructionError(
            TASK_RUNTIME_CANONICAL_DB_PATH_MISMATCH,
            "task runtime and ledger canonical SQLite paths differ",
        )
    return canonical


def _path_identity(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def _next_startup_recovery_id(*, canonical_db_path: str) -> str:
    """Return one opaque, boot-scoped identity for this construction attempt."""

    with _CONTAINER_CONSTRUCTION_SEQUENCE_LOCK:
        construction_sequence = next(_CONTAINER_CONSTRUCTION_SEQUENCE)
    path_digest = hashlib.sha256(
        _path_identity(canonical_db_path).encode("utf-8")
    ).hexdigest()[:16]
    return (
        f"task-runtime-startup::{os.getpid()}::{_PROCESS_BOOT_ID}::"
        f"{construction_sequence}::{path_digest}"
    )


def _run_canonical_migrations(
    migration_runner: SQLiteMigrationRunner,
    *,
    canonical_db_path: str,
) -> MigrationBatchResult:
    """Delegate all catalog and legacy-schema policy to the migration owner."""

    del canonical_db_path
    return migration_runner.apply_migrations(request_id="startup::task-runtime")


def _build_task_store_with_reconciliation_authority(
    *,
    canonical_db_path: str,
    task_store_builder: TaskStoreBuilder,
    reconciliation_public_keys: Mapping[str, bytes] | None,
) -> DurableTaskStore:
    """Inject the configured verification-only authority into TaskStore."""

    if not reconciliation_public_keys:
        return task_store_builder(canonical_db_path)
    reconciliation_proof_verifier = Ed25519ReconciliationProofVerifier(
        reconciliation_public_keys
    )
    return task_store_builder(
        canonical_db_path,
        reconciliation_proof_verifier=reconciliation_proof_verifier,
    )


def build_process_task_runtime_container(
    *,
    ledger: Any,
    canonical_db_path: str | Path,
    migrations_dir: str | Path | None = None,
    startup_outbox_limit: int = 1_000,
    migration_runner_builder: MigrationRunnerBuilder = SQLiteMigrationRunner,
    task_store_builder: TaskStoreBuilder = DurableTaskStore,
    review_store_builder: ReviewStoreBuilder = ReviewStore,
    review_continuation_store_builder: ReviewContinuationStoreBuilder = (
        ReviewContinuationStore
    ),
    worker_projection_store_builder: WorkerProjectionStoreBuilder = WorkerProjectionStore,
    internal_agent_checkpoint_uow_builder: InternalAgentCheckpointUowBuilder = (
        DurableInternalAgentCheckpointUnitOfWork
    ),
    command_repository_builder: CommandRepositoryBuilder = HostCommandRepository,
    agent_result_repository_builder: AgentResultRepositoryBuilder = (
        AgentExecutionResultRepository
    ),
    scheduler_definition_repository_builder: SchedulerDefinitionRepositoryBuilder = (
        SchedulerDefinitionRepository
    ),
    scheduler_result_repository_builder: SchedulerResultRepositoryBuilder = (
        SchedulerExecutionResultRepository
    ),
    event_store_builder: RuntimeEventStoreBuilder = RuntimeEventStore,
    outbox_publisher_builder: RuntimeOutboxPublisherBuilder = RuntimeOutboxPublisher,
    terminal_operator_command_store_builder: TerminalOperatorCommandStoreBuilder = (
        TerminalOperatorCommandStore
    ),
    feature_canary_store_builder: FeatureCanaryStoreBuilder = (
        SQLiteTaskRuntimeFeatureCanaryReceiptStore
    ),
    reconciliation_public_keys: Mapping[str, bytes] | None = None,
    feature_canary_public_keys: Mapping[str, bytes] | None = None,
    terminal_reconciliation_auto_enabled: bool = False,
    authenticated_outcome_lookup: Callable[[str], Any | None] | None = None,
) -> ProcessTaskRuntimeContainer:
    """Migrate and construct one fail-closed durable infrastructure graph."""

    canonical = _canonical_path(ledger, canonical_db_path)
    constructed: list[tuple[str, Any]] = []
    try:
        # The runner itself persists to canonical migration_log.  Passing the
        # open SQLiteLedger as a second audit sink would write the same file
        # through a competing connection while its migration transaction is
        # held, causing a lock and duplicate audit rows.
        migration_runner = migration_runner_builder(
            db_path=canonical,
            migrations_dir=migrations_dir,
        )
        constructed.append(("migration_runner", migration_runner))
        try:
            migration_result = _run_canonical_migrations(
                migration_runner,
                canonical_db_path=canonical,
            )
        except Exception as exc:
            raise TaskRuntimeContainerConstructionError(
                TASK_RUNTIME_MIGRATION_FAILED,
                "canonical migration runner raised before completion",
            ) from exc
        if not bool(getattr(migration_result, "success", False)):
            reason = str(
                getattr(migration_result, "reason_code", "")
                or TASK_RUNTIME_MIGRATION_FAILED
            )
            raise TaskRuntimeContainerConstructionError(
                TASK_RUNTIME_MIGRATION_FAILED,
                f"canonical migration failed: {reason}",
            )

        feature_canary_store = feature_canary_store_builder(canonical)
        constructed.append(("feature_canary_store", feature_canary_store))
        feature_canary_signature_verifier = (
            Ed25519ReconciliationProofVerifier(feature_canary_public_keys)
            if feature_canary_public_keys
            else None
        )
        task_store = _build_task_store_with_reconciliation_authority(
            canonical_db_path=canonical,
            task_store_builder=task_store_builder,
            reconciliation_public_keys=reconciliation_public_keys,
        )
        constructed.append(("task_store", task_store))
        task_store.assert_outbox_delivery_ready()
        task_store.assert_graph_cancellation_continuation_ready()
        task_store.assert_effect_reconciliation_ready()
        task_store.assert_terminal_reconciliation_ready()
        task_store.assert_node_artifact_ready()
        terminal_operator_command_store = terminal_operator_command_store_builder(
            canonical
        )
        recovery_id = _next_startup_recovery_id(canonical_db_path=canonical)
        recovery_report = task_store.recover_startup(recovery_id=recovery_id)
        terminal_reconciliation_inventory = (
            task_store.inventory_terminal_reconciliation_cases(limit=10_000)
        )
        terminal_reconciliation_service = TerminalStateReconciliationService(
            task_store=task_store,
            outbox_factory=OutboxMessage,
            clock_ms=now_ms,
        )
        terminal_reconciliation_auto_result = (
            terminal_reconciliation_service.reconcile_exact_cases()
            if bool(terminal_reconciliation_auto_enabled)
            else {
                "status": "DISABLED",
                "candidate_count": 0,
                "reconciled_count": 0,
                "results": (),
                "remaining_exact_cases": False,
            }
        )
        review_store = review_store_builder(canonical)
        constructed.append(("review_store", review_store))
        review_continuation_store = review_continuation_store_builder(canonical)
        constructed.append(("review_continuation_store", review_continuation_store))
        worker_projection_store = worker_projection_store_builder(canonical)
        constructed.append(("worker_projection_store", worker_projection_store))
        internal_agent_checkpoint_uow = internal_agent_checkpoint_uow_builder(
            task_store=task_store,
            worker_projection_store=worker_projection_store,
        )
        constructed.append(
            ("internal_agent_checkpoint_uow", internal_agent_checkpoint_uow)
        )
        command_repository = (
            command_repository_builder(
                canonical,
                authenticated_outcome_lookup=authenticated_outcome_lookup,
            )
            if authenticated_outcome_lookup is not None
            else command_repository_builder(canonical)
        )
        constructed.append(("command_repository", command_repository))
        agent_result_repository = agent_result_repository_builder(canonical)
        constructed.append(("agent_result_repository", agent_result_repository))
        scheduler_definition_repository = scheduler_definition_repository_builder(
            canonical
        )
        constructed.append(
            ("scheduler_definition_repository", scheduler_definition_repository)
        )
        scheduler_result_repository = scheduler_result_repository_builder(canonical)
        constructed.append(("scheduler_result_repository", scheduler_result_repository))
        event_store = event_store_builder(canonical)
        constructed.append(("event_store", event_store))
        outbox_publisher = outbox_publisher_builder(
            task_store=task_store,
            event_store=event_store,
            worker_id=f"{recovery_id}::outbox",
        )
        constructed.append(("outbox_publisher", outbox_publisher))
        startup_publish_result = outbox_publisher.publish_pending(
            limit=max(1, int(startup_outbox_limit))
        )
        outbox_publisher.start()
        graph_execution_registry = LocalGraphExecutionRegistry()
        cancellation_runtime_outbox = RuntimeOutbox(
            publisher=outbox_publisher,
            factory=OutboxMessage,
            publish_limit=200,
        )
        cancellation_runtime = GraphCancellationRuntime(
            task_store=task_store,
            runtime_outbox=cancellation_runtime_outbox,
            execution_registry=graph_execution_registry,
            lease_ttl_ms=120_000,
        )
        cancellation_continuation_worker = GraphCancellationContinuationWorker(
            task_store=task_store,
            command_repository=command_repository,
            cancellation_runtime=cancellation_runtime,
            worker_id=f"{recovery_id}::graph-cancellation",
        )
        constructed.append(
            ("cancellation_continuation_worker", cancellation_continuation_worker)
        )
        operation_reconciliation_service = (
            GraphCancellationOperationReconciliationService(
                task_store=task_store,
                outbox_factory=OutboxMessage,
                wake_cancellation=cancellation_continuation_worker.wake,
            )
        )
        effect_reconciliation_service = GraphCancellationEffectReconciliationService(
            task_store=task_store,
            outbox_factory=OutboxMessage,
            wake_cancellation=cancellation_continuation_worker.wake,
            clock_ms=now_ms,
        )
    except TaskRuntimeContainerConstructionError as exc:
        rollback = _close_constructed_sync(constructed)
        raise TaskRuntimeContainerConstructionError(
            exc.reason_code,
            exc.detail,
            rollback_failures=rollback,
        ) from exc
    except Exception as exc:
        rollback = _close_constructed_sync(constructed)
        raise TaskRuntimeContainerConstructionError(
            TASK_RUNTIME_CONSTRUCTION_FAILED,
            f"task runtime infrastructure construction failed at slot {len(constructed) + 1}",
            rollback_failures=rollback,
        ) from exc

    return ProcessTaskRuntimeContainer(
        ledger=ledger,
        canonical_db_path=canonical,
        migration_runner=migration_runner,
        migration_result=migration_result,
        feature_canary_store=feature_canary_store,
        feature_canary_signature_verifier=feature_canary_signature_verifier,
        task_store=task_store,
        recovery_id=recovery_id,
        recovery_report=recovery_report,
        review_store=review_store,
        review_continuation_store=review_continuation_store,
        worker_projection_store=worker_projection_store,
        internal_agent_checkpoint_uow=internal_agent_checkpoint_uow,
        command_repository=command_repository,
        agent_result_repository=agent_result_repository,
        scheduler_definition_repository=scheduler_definition_repository,
        scheduler_result_repository=scheduler_result_repository,
        event_store=event_store,
        outbox_publisher=outbox_publisher,
        graph_execution_registry=graph_execution_registry,
        cancellation_continuation_worker=cancellation_continuation_worker,
        operation_reconciliation_service=operation_reconciliation_service,
        effect_reconciliation_service=effect_reconciliation_service,
        terminal_reconciliation_service=terminal_reconciliation_service,
        terminal_operator_command_store=terminal_operator_command_store,
        terminal_reconciliation_inventory=dict(terminal_reconciliation_inventory),
        terminal_reconciliation_auto_result=dict(
            terminal_reconciliation_auto_result
        ),
        startup_publish_result=dict(startup_publish_result),
        owner_process_id=os.getpid(),
    )


__all__ = [
    "TASK_RUNTIME_ACTIVATION_FAILED",
    "TASK_RUNTIME_ALREADY_BOUND",
    "TASK_RUNTIME_CANONICAL_DB_PATH_INVALID",
    "TASK_RUNTIME_CANONICAL_DB_PATH_MISMATCH",
    "TASK_RUNTIME_CLOSE_ALREADY_RUNNING",
    "TASK_RUNTIME_CLOSE_FAILED",
    "TASK_RUNTIME_CONSTRUCTION_FAILED",
    "TASK_RUNTIME_COORDINATOR_INVALID",
    "TASK_RUNTIME_COORDINATOR_NOT_BOUND",
    "TASK_RUNTIME_DRAIN_FAILED",
    "TASK_RUNTIME_MIGRATION_FAILED",
    "TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED",
    "TASK_RUNTIME_PROCESS_SCOPE_VIOLATION",
    "ProcessTaskRuntimeContainer",
    "TaskRuntimeCloseFailure",
    "TaskRuntimeContainerCloseError",
    "TaskRuntimeContainerConstructionError",
    "TaskRuntimeContainerError",
    "build_process_task_runtime_container",
]
