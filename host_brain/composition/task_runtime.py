"""Process-owned composition root for the durable Host Task Runtime spine.

This module owns infrastructure construction and lifecycle only.  It never
imports the ASGI/app layer and does not hide dependencies behind globals.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import inspect
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

from host_brain.agent_runtime.task_store import DurableTaskStore
from host_brain.audit.migration_runner import (
    MigrationBatchResult,
    SQLiteMigrationRunner,
)
from host_brain.integrations.runtime_events import (
    RuntimeEventStore,
    RuntimeOutboxPublisher,
)
from host_brain.persistence import (
    HostCommandRepository,
    SchedulerDefinitionRepository,
    SchedulerExecutionResultRepository,
)
from host_brain.persistence.agent_execution_repository import (
    AgentExecutionResultRepository,
)


TASK_RUNTIME_CANONICAL_DB_PATH_INVALID = "TASK_RUNTIME_CANONICAL_DB_PATH_INVALID"
TASK_RUNTIME_CANONICAL_DB_PATH_MISMATCH = "TASK_RUNTIME_CANONICAL_DB_PATH_MISMATCH"
TASK_RUNTIME_MIGRATION_FAILED = "TASK_RUNTIME_MIGRATION_FAILED"
TASK_RUNTIME_CONSTRUCTION_FAILED = "TASK_RUNTIME_CONSTRUCTION_FAILED"
TASK_RUNTIME_PROCESS_SCOPE_VIOLATION = "TASK_RUNTIME_PROCESS_SCOPE_VIOLATION"
TASK_RUNTIME_ALREADY_BOUND = "TASK_RUNTIME_ALREADY_BOUND"
TASK_RUNTIME_COORDINATOR_INVALID = "TASK_RUNTIME_COORDINATOR_INVALID"
TASK_RUNTIME_COORDINATOR_NOT_BOUND = "TASK_RUNTIME_COORDINATOR_NOT_BOUND"
TASK_RUNTIME_ACTIVATION_FAILED = "TASK_RUNTIME_ACTIVATION_FAILED"
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
AgentResultRepositoryBuilder = Callable[..., AgentExecutionResultRepository]
SchedulerDefinitionRepositoryBuilder = Callable[..., SchedulerDefinitionRepository]
SchedulerResultRepositoryBuilder = Callable[..., SchedulerExecutionResultRepository]
RuntimeEventStoreBuilder = Callable[..., RuntimeEventStore]
RuntimeOutboxPublisherBuilder = Callable[..., RuntimeOutboxPublisher]


_INFRASTRUCTURE_ORDER = (
    "migration_runner",
    "task_store",
    "command_repository",
    "agent_result_repository",
    "scheduler_definition_repository",
    "scheduler_result_repository",
    "event_store",
    "outbox_publisher",
)


@dataclass(frozen=True, slots=True)
class _LegacyLedgerExpansion:
    migration_id: int
    name: str
    filename: str
    checksum: str
    table: str
    columns: frozenset[str]


# SQLiteLedger's compatibility schema predates catalog ownership and already
# contains these two additive changes.  Old ledgers therefore have the proven
# postcondition but no schema_migrations row.  Nothing else is eligible for an
# automatic baseline: every entry is pinned to its frozen asset checksum.
_LEGACY_LEDGER_EXPANSIONS = {
    "0041_canon_change_log_body": _LegacyLedgerExpansion(
        migration_id=41,
        name="canon_change_log_body",
        filename="0041_canon_change_log_body.sql",
        checksum="663fc6a8c069e8d7749cc580f347edc18ec89fb685f602f6e614e5c32524b06f",
        table="canon_change_log",
        columns=frozenset({"before_body", "after_body"}),
    ),
    "0043_canon_proposal_content": _LegacyLedgerExpansion(
        migration_id=43,
        name="canon_proposal_content",
        filename="0043_canon_proposal_content.sql",
        checksum="cefd91006687803d58817a1965eee369a9334c1f0558d8fd8eb804b105c8487f",
        table="canon_proposal_log",
        columns=frozenset(
            {"proposed_title", "proposed_tags", "proposed_body"}
        ),
    ),
}
_MAX_LEGACY_RECONCILIATIONS = len(_LEGACY_LEDGER_EXPANSIONS)


@dataclass(slots=True)
class ProcessTaskRuntimeContainer:
    """Durable Task Runtime infrastructure owned by exactly one Host process."""

    ledger: Any
    canonical_db_path: str
    migration_runner: SQLiteMigrationRunner
    migration_result: MigrationBatchResult
    task_store: DurableTaskStore
    command_repository: HostCommandRepository
    agent_result_repository: AgentExecutionResultRepository
    scheduler_definition_repository: SchedulerDefinitionRepository
    scheduler_result_repository: SchedulerExecutionResultRepository
    event_store: RuntimeEventStore
    outbox_publisher: RuntimeOutboxPublisher
    startup_publish_result: Mapping[str, Any]
    owner_process_id: int
    _bound_runtime: Any | None = field(default=None, init=False, repr=False)
    _activated: bool = field(default=False, init=False, repr=False)
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

    def bind_runtime(self, coordinator: Any) -> None:
        """Bind, but do not activate, the future application coordinator."""

        self._assert_process_owner()
        with self._lifecycle_lock:
            if self._closed or self._closing:
                raise TaskRuntimeContainerError(
                    TASK_RUNTIME_COORDINATOR_INVALID,
                    "cannot bind a closed task runtime container",
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
        except Exception as exc:
            raise TaskRuntimeContainerError(
                TASK_RUNTIME_ACTIVATION_FAILED,
                "bound task runtime coordinator activation failed",
            ) from exc
        self._activated = True

    async def drain_outbox(
        self,
        *,
        batch_limit: int = 1_000,
        max_batches: int = 64,
    ) -> Mapping[str, Any]:
        """Publish every currently pending row, bounded for shutdown safety."""

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
            if scanned == 0 or scanned < limit:
                summary = {
                    "status": "DRAINED",
                    "batch_count": batch_no,
                    "pending_scanned": total_scanned,
                    "published_count": total_published,
                }
                self._last_drain_result = summary
                return summary
        raise TaskRuntimeContainerError(
            TASK_RUNTIME_DRAIN_FAILED,
            f"outbox drain exceeded {batches} batches",
        )

    async def aclose(self) -> None:
        """Stop admission, drain durable outbox, then close in reverse order."""

        self._assert_process_owner()
        if not self._begin_close():
            return
        failures: list[TaskRuntimeCloseFailure] = []
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
                await self.drain_outbox()
            except Exception:
                failures.append(
                    TaskRuntimeCloseFailure(
                        component="outbox_drain",
                        reason_code=TASK_RUNTIME_DRAIN_FAILED,
                    )
                )

            close_order: list[tuple[str, Any]] = []
            if coordinator is not None:
                close_order.append(("runtime_coordinator", coordinator))
            close_order.extend(
                (name, getattr(self, name))
                for name in reversed(self.construction_order)
            )
            for name, component in close_order:
                await _close_component(name, component, failures)
        finally:
            with self._lifecycle_lock:
                self._activated = False
                self._closed = True
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


def _run_canonical_migrations(
    migration_runner: SQLiteMigrationRunner,
    *,
    canonical_db_path: str,
) -> MigrationBatchResult:
    """Run the catalog, reconciling only proven embedded-schema expansions."""

    for reconciliation_no in range(_MAX_LEGACY_RECONCILIATIONS + 1):
        result = migration_runner.apply_migrations(
            request_id="startup::task-runtime"
        )
        if bool(getattr(result, "success", False)):
            return result
        if reconciliation_no >= _MAX_LEGACY_RECONCILIATIONS:
            return result
        if not _reconcile_legacy_ledger_expansion(
            migration_runner=migration_runner,
            migration_result=result,
            canonical_db_path=canonical_db_path,
        ):
            return result
    raise AssertionError("unreachable migration reconciliation state")


def _reconcile_legacy_ledger_expansion(
    *,
    migration_runner: SQLiteMigrationRunner,
    migration_result: Any,
    canonical_db_path: str,
) -> bool:
    """Backfill one frozen migration only when its full effect already exists."""

    if str(getattr(migration_result, "reason_code", "") or "") != (
        "MIGRATION_EXEC_FAILED"
    ):
        return False
    records = tuple(getattr(migration_result, "records", ()) or ())
    if not records:
        return False
    failed = records[-1]
    if str(getattr(failed, "status", "") or "") != "FAILED":
        return False
    record_id = str(getattr(failed, "migration_id", "") or "")
    expansion = _LEGACY_LEDGER_EXPANSIONS.get(record_id)
    if expansion is None:
        return False
    failure_detail = str(getattr(failed, "detail", "") or "").lower()
    if not any(
        f"duplicate column name: {column}" in failure_detail
        for column in expansion.columns
    ):
        return False

    migrations_dir = getattr(migration_runner, "migrations_dir", None)
    if migrations_dir is None:
        return False
    asset_path = Path(migrations_dir) / expansion.filename
    try:
        asset_text = asset_path.read_text(encoding="utf-8-sig")
    except OSError:
        return False
    checksum = hashlib.sha256(asset_text.encode("utf-8")).hexdigest()
    if checksum != expansion.checksum:
        return False

    conn = sqlite3.connect(canonical_db_path, timeout=5.0, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("BEGIN IMMEDIATE")
        columns = {
            str(row["name"])
            for row in conn.execute(
                f'PRAGMA table_info("{expansion.table}")'
            ).fetchall()
        }
        if not expansion.columns.issubset(columns):
            conn.rollback()
            return False
        existing = conn.execute(
            """
            SELECT name, checksum
            FROM schema_migrations
            WHERE migration_id = ?
            """,
            (expansion.migration_id,),
        ).fetchone()
        if existing is not None:
            matches = (
                str(existing["name"]) == expansion.name
                and str(existing["checksum"]) == expansion.checksum
            )
            conn.rollback()
            return matches
        conn.execute(
            """
            INSERT INTO schema_migrations(
                migration_id,
                name,
                checksum,
                applied_ts_ms
            ) VALUES (?, ?, ?, ?)
            """,
            (
                expansion.migration_id,
                expansion.name,
                expansion.checksum,
                int(time.time() * 1000),
            ),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False
    finally:
        conn.close()


def build_process_task_runtime_container(
    *,
    ledger: Any,
    canonical_db_path: str | Path,
    migrations_dir: str | Path | None = None,
    startup_outbox_limit: int = 1_000,
    migration_runner_builder: MigrationRunnerBuilder = SQLiteMigrationRunner,
    task_store_builder: TaskStoreBuilder = DurableTaskStore,
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

        task_store = task_store_builder(canonical)
        constructed.append(("task_store", task_store))
        command_repository = command_repository_builder(canonical)
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
        )
        constructed.append(("outbox_publisher", outbox_publisher))
        startup_publish_result = outbox_publisher.publish_pending(
            limit=max(1, int(startup_outbox_limit))
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
        task_store=task_store,
        command_repository=command_repository,
        agent_result_repository=agent_result_repository,
        scheduler_definition_repository=scheduler_definition_repository,
        scheduler_result_repository=scheduler_result_repository,
        event_store=event_store,
        outbox_publisher=outbox_publisher,
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
    "TASK_RUNTIME_PROCESS_SCOPE_VIOLATION",
    "ProcessTaskRuntimeContainer",
    "TaskRuntimeCloseFailure",
    "TaskRuntimeContainerCloseError",
    "TaskRuntimeContainerConstructionError",
    "TaskRuntimeContainerError",
    "build_process_task_runtime_container",
]
