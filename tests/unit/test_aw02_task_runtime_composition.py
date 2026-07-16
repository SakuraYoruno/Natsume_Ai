from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.persistence.task_repository import DurableTaskStore, RecoveryReport
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.task_runtime import (
    TASK_RUNTIME_ACTIVATION_FAILED,
    TASK_RUNTIME_CANONICAL_DB_PATH_MISMATCH,
    TASK_RUNTIME_CONSTRUCTION_FAILED,
    TASK_RUNTIME_MIGRATION_FAILED,
    TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED,
    ProcessTaskRuntimeContainer,
    TaskRuntimeContainerCloseError,
    TaskRuntimeContainerConstructionError,
    TaskRuntimeContainerError,
    build_process_task_runtime_container,
)
from host_brain.composition.runtime_group import GovernedRuntimeGroup
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationService,
)
from host_brain.task_runtime.graph_cancellation_effect_reconciliation import (
    GraphCancellationEffectReconciliationService,
)
from host_brain.integrations.runtime_events import (
    RuntimeEventStore,
    RuntimeOutboxPublisher,
)
from host_brain.persistence.agent_execution_repository import (
    AgentExecutionResultRepository,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore
from host_brain.persistence.scheduler_repository import (
    SchedulerDefinitionRepository,
    SchedulerExecutionResultRepository,
)


class _Ledger:
    def __init__(self, canonical_db_path: str | Path) -> None:
        self.canonical_db_path = str(Path(canonical_db_path).resolve())


class _Closeable:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    def close(self) -> None:
        self.events.append(f"{self.name}:close")


class _MigrationRunner(_Closeable):
    def __init__(
        self,
        events: list[str],
        *,
        success: bool = True,
    ) -> None:
        super().__init__("migration", events)
        self.success = success

    def apply_migrations(self, *, request_id: str) -> Any:
        self.events.append(f"migration:apply:{request_id}")
        return SimpleNamespace(
            success=self.success,
            reason_code=None if self.success else "BROKEN_ASSET",
        )


class _OutboxPublisher(_Closeable):
    def __init__(
        self,
        events: list[str],
        *,
        task_store: Any,
        event_store: Any,
        worker_id: str,
    ) -> None:
        super().__init__("publisher", events)
        self.task_store = task_store
        self.event_store = event_store
        self.worker_id = worker_id
        self.backlog: dict[str, int | None] = {
            "total_pending": 0,
            "delivery_ready_count": 0,
            "next_wakeup_ts_ms": None,
        }

    def publish_pending(self, *, limit: int) -> dict[str, Any]:
        self.events.append(f"publisher:publish:{limit}")
        return {
            "status": "OK",
            "pending_scanned": 0,
            "published_count": 0,
            "published": [],
            "backlog": dict(self.backlog),
        }

    def start(self) -> None:
        self.events.append("publisher:start")


class _TaskStore(_Closeable):
    def __init__(
        self,
        events: list[str],
        *,
        recovery_error: Exception | None = None,
    ) -> None:
        super().__init__("task-store", events)
        self.recovery_error = recovery_error

    def assert_outbox_delivery_ready(self) -> None:
        self.events.append("task-store:outbox-ready")

    def assert_graph_cancellation_continuation_ready(self) -> None:
        self.events.append("task-store:cancellation-continuation-ready")

    def assert_effect_reconciliation_ready(self) -> None:
        self.events.append("task-store:effect-reconciliation-ready")

    def claim_pending_graph_cancellations(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    def recover_startup(self, *, recovery_id: str) -> RecoveryReport:
        self.events.append(f"task-store:recover:{recovery_id}")
        if self.recovery_error is not None:
            raise self.recovery_error
        return RecoveryReport(
            recovery_id=recovery_id,
            recovered_attempt_ids=(),
            expired_lease_ids=(),
        )


def _build_fake_container(
    tmp_path: Path,
    events: list[str],
) -> ProcessTaskRuntimeContainer:
    path = tmp_path / "canonical.sqlite3"
    ledger = _Ledger(path)

    def build_migration(**kwargs: Any) -> _MigrationRunner:
        assert kwargs == {
            "db_path": str(path.resolve()),
            "migrations_dir": None,
        }
        events.append("migration:build")
        return _MigrationRunner(events)

    def build_store(db_path: str) -> _TaskStore:
        assert db_path == str(path.resolve())
        events.append("task-store:build")
        return _TaskStore(events)

    def build_repository(db_path: str) -> _Closeable:
        assert db_path == str(path.resolve())
        events.append("repository:build")
        return _Closeable("repository", events)

    def build_review_store(db_path: str) -> _Closeable:
        assert db_path == str(path.resolve())
        events.append("review-store:build")
        return _Closeable("review-store", events)

    def build_review_continuation_store(db_path: str) -> _Closeable:
        assert db_path == str(path.resolve())
        events.append("review-continuation-store:build")
        return _Closeable("review-continuation-store", events)

    def build_worker_projection_store(db_path: str) -> _Closeable:
        assert db_path == str(path.resolve())
        events.append("worker-projection-store:build")
        return _Closeable("worker-projection-store", events)

    def build_agent_result_repository(db_path: str) -> _Closeable:
        assert db_path == str(path.resolve())
        events.append("agent-result:build")
        return _Closeable("agent-result", events)

    def build_scheduler_definition_repository(db_path: str) -> _Closeable:
        assert db_path == str(path.resolve())
        events.append("scheduler-definition:build")
        return _Closeable("scheduler-definition", events)

    def build_scheduler_result_repository(db_path: str) -> _Closeable:
        assert db_path == str(path.resolve())
        events.append("scheduler-result:build")
        return _Closeable("scheduler-result", events)

    def build_event_store(db_path: str) -> _Closeable:
        assert db_path == str(path.resolve())
        events.append("event-store:build")
        return _Closeable("event-store", events)

    def build_publisher(**kwargs: Any) -> _OutboxPublisher:
        events.append("publisher:build")
        return _OutboxPublisher(events, **kwargs)

    return build_process_task_runtime_container(
        ledger=ledger,
        canonical_db_path=path,
        startup_outbox_limit=23,
        migration_runner_builder=build_migration,
        task_store_builder=build_store,
        review_store_builder=build_review_store,
        review_continuation_store_builder=build_review_continuation_store,
        worker_projection_store_builder=build_worker_projection_store,
        command_repository_builder=build_repository,
        agent_result_repository_builder=build_agent_result_repository,
        scheduler_definition_repository_builder=build_scheduler_definition_repository,
        scheduler_result_repository_builder=build_scheduler_result_repository,
        event_store_builder=build_event_store,
        outbox_publisher_builder=build_publisher,
    )


def test_build_migrates_before_constructing_one_explicit_durable_graph(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    container = _build_fake_container(tmp_path, events)

    assert events == [
        "migration:build",
        "migration:apply:startup::task-runtime",
        "task-store:build",
        "task-store:outbox-ready",
        "task-store:cancellation-continuation-ready",
        "task-store:effect-reconciliation-ready",
        f"task-store:recover:{container.recovery_id}",
        "review-store:build",
        "review-continuation-store:build",
        "worker-projection-store:build",
        "repository:build",
        "agent-result:build",
        "scheduler-definition:build",
        "scheduler-result:build",
        "event-store:build",
        "publisher:build",
        "publisher:publish:23",
        "publisher:start",
    ]
    assert container.infrastructure_ready
    assert container.ledger is not None
    assert container.canonical_db_path == str(
        (tmp_path / "canonical.sqlite3").resolve()
    )
    assert container.outbox_publisher.task_store is container.task_store
    assert container.outbox_publisher.event_store is container.event_store
    assert container.outbox_publisher.worker_id == f"{container.recovery_id}::outbox"
    assert container.cancellation_continuation_worker.worker_id == (
        f"{container.recovery_id}::graph-cancellation"
    )
    assert isinstance(
        container.operation_reconciliation_service,
        GraphCancellationOperationReconciliationService,
    )
    assert isinstance(
        container.effect_reconciliation_service,
        GraphCancellationEffectReconciliationService,
    )
    assert container.graph_execution_registry is not None
    assert container.recovery_report.recovery_id == container.recovery_id
    execution_owner = container.execution_owner_id("configured-owner")
    assert execution_owner.startswith("configured-owner::runtime::")
    assert execution_owner == container.execution_owner_id("configured-owner")
    assert container.recovery_id not in execution_owner
    recovery_id_parts = container.recovery_id.split("::")
    assert recovery_id_parts[0] == "task-runtime-startup"
    assert recovery_id_parts[1] == str(container.owner_process_id)
    assert len(recovery_id_parts[2]) == 32
    assert int(recovery_id_parts[3]) > 0
    assert len(recovery_id_parts[4]) == 16
    assert container.startup_publish_result["status"] == "OK"

    asyncio.run(container.aclose())


def test_migration_failure_is_fail_closed_and_rolls_back_runner(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    path = tmp_path / "canonical.sqlite3"

    with pytest.raises(TaskRuntimeContainerConstructionError) as caught:
        build_process_task_runtime_container(
            ledger=_Ledger(path),
            canonical_db_path=path,
            migration_runner_builder=lambda **_kwargs: _MigrationRunner(
                events,
                success=False,
            ),
            task_store_builder=lambda _path: pytest.fail(
                "store construction must not run after migration failure"
            ),
        )

    assert caught.value.reason_code == TASK_RUNTIME_MIGRATION_FAILED
    assert caught.value.rollback_failures == ()
    assert events == [
        "migration:apply:startup::task-runtime",
        "migration:close",
    ]


def test_startup_recovery_failure_is_fail_closed_before_other_stores_or_publish(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    path = tmp_path / "canonical.sqlite3"

    def build_store(_path: str) -> _TaskStore:
        events.append("task-store:build")
        return _TaskStore(events, recovery_error=OSError("private platform detail"))

    with pytest.raises(TaskRuntimeContainerConstructionError) as caught:
        build_process_task_runtime_container(
            ledger=_Ledger(path),
            canonical_db_path=path,
            migration_runner_builder=lambda **_kwargs: _MigrationRunner(events),
            task_store_builder=build_store,
            review_store_builder=lambda _path: pytest.fail(
                "review construction must not run after recovery failure"
            ),
            outbox_publisher_builder=lambda **_kwargs: pytest.fail(
                "publisher construction must not run after recovery failure"
            ),
        )

    assert caught.value.reason_code == TASK_RUNTIME_CONSTRUCTION_FAILED
    assert "private platform detail" not in caught.value.detail
    assert caught.value.rollback_failures == ()
    assert len(events) == 8
    assert events[:2] == [
        "migration:apply:startup::task-runtime",
        "task-store:build",
    ]
    assert events[2] == "task-store:outbox-ready"
    assert events[3] == "task-store:cancellation-continuation-ready"
    assert events[4] == "task-store:effect-reconciliation-ready"
    assert events[5].startswith("task-store:recover:task-runtime-startup::")
    assert events[6:] == ["task-store:close", "migration:close"]


def test_execution_owner_is_unique_per_container_construction(tmp_path: Path) -> None:
    first_events: list[str] = []
    second_events: list[str] = []
    first = _build_fake_container(tmp_path, first_events)
    second = _build_fake_container(tmp_path, second_events)

    assert first.execution_owner_id("canonical") != second.execution_owner_id(
        "canonical"
    )

    asyncio.run(first.aclose())
    asyncio.run(second.aclose())


def test_partial_construction_failure_closes_completed_slots_in_reverse_order(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    path = tmp_path / "canonical.sqlite3"

    def build_store(_path: str) -> _TaskStore:
        events.append("task-store:build")
        return _TaskStore(events)

    def fail_repository(_path: str) -> Any:
        events.append("repository:build")
        raise OSError("unstable platform detail")

    def build_review_store(_path: str) -> _Closeable:
        events.append("review-store:build")
        return _Closeable("review-store", events)

    def build_worker_projection_store(_path: str) -> _Closeable:
        events.append("worker-projection-store:build")
        return _Closeable("worker-projection-store", events)

    def build_review_continuation_store(_path: str) -> _Closeable:
        events.append("review-continuation-store:build")
        return _Closeable("review-continuation-store", events)

    with pytest.raises(TaskRuntimeContainerConstructionError) as caught:
        build_process_task_runtime_container(
            ledger=_Ledger(path),
            canonical_db_path=path,
            migration_runner_builder=lambda **_kwargs: _MigrationRunner(events),
            task_store_builder=build_store,
            review_store_builder=build_review_store,
            review_continuation_store_builder=build_review_continuation_store,
            worker_projection_store_builder=build_worker_projection_store,
            command_repository_builder=fail_repository,
        )

    assert caught.value.reason_code == TASK_RUNTIME_CONSTRUCTION_FAILED
    assert caught.value.rollback_failures == ()
    assert "unstable platform detail" not in caught.value.detail
    assert events[:2] == [
        "migration:apply:startup::task-runtime",
        "task-store:build",
    ]
    assert events[2] == "task-store:outbox-ready"
    assert events[3] == "task-store:cancellation-continuation-ready"
    assert events[4] == "task-store:effect-reconciliation-ready"
    assert events[5].startswith("task-store:recover:task-runtime-startup::")
    assert events[6:] == [
        "review-store:build",
        "review-continuation-store:build",
        "worker-projection-store:build",
        "repository:build",
        "worker-projection-store:close",
        "review-continuation-store:close",
        "review-store:close",
        "task-store:close",
        "migration:close",
    ]


def test_bound_runtime_activates_then_drains_and_closes_before_infrastructure(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    container = _build_fake_container(tmp_path, events)

    class Coordinator:
        async def activate(self) -> None:
            events.append("coordinator:activate")

        def close_admission(self) -> None:
            events.append("coordinator:admission-close")

        async def drain(self) -> None:
            events.append("coordinator:drain")

        async def aclose(self) -> None:
            events.append("coordinator:close")

    coordinator = Coordinator()
    container.bind_runtime(coordinator)

    async def activate_and_close() -> None:
        await container.activate_runtime()
        assert container.cancellation_continuation_worker.running
        events.clear()
        await container.aclose()
        assert not container.cancellation_continuation_worker.running

    asyncio.run(activate_and_close())

    assert events == [
        "coordinator:admission-close",
        "coordinator:drain",
        "coordinator:close",
        "publisher:publish:1000",
        "publisher:close",
        "event-store:close",
        "scheduler-result:close",
        "scheduler-definition:close",
        "agent-result:close",
        "repository:close",
        "worker-projection-store:close",
        "review-continuation-store:close",
        "review-store:close",
        "task-store:close",
        "migration:close",
    ]
    assert container.closed
    assert not container.activated
    asyncio.run(container.aclose())
    assert len(events) == 15


def test_worker_start_failure_rolls_back_and_detaches_activated_coordinator(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    container = _build_fake_container(tmp_path, events)

    class Coordinator:
        async def activate(self) -> None:
            events.append("coordinator:activate")

        async def aclose(self) -> None:
            events.append("coordinator:rollback-close")

    class FailingWorker:
        async def start(self) -> None:
            events.append("worker:start")
            raise RuntimeError("private readiness detail")

        async def stop(self) -> None:
            events.append("worker:rollback-stop")

        async def join(self) -> None:
            events.append("worker:rollback-join")

    coordinator = Coordinator()
    container.cancellation_continuation_worker = FailingWorker()  # type: ignore[assignment]
    container.bind_runtime(coordinator)
    events.clear()

    with pytest.raises(TaskRuntimeContainerError) as caught:
        asyncio.run(container.activate_runtime())

    assert caught.value.reason_code == TASK_RUNTIME_ACTIVATION_FAILED
    assert "private readiness detail" not in caught.value.detail
    assert events == [
        "coordinator:activate",
        "worker:start",
        "worker:rollback-stop",
        "worker:rollback-join",
        "coordinator:rollback-close",
    ]
    assert not container.activated
    assert container.bound_runtime is None

    asyncio.run(container.aclose())


def test_failed_activation_rollback_retains_runtime_until_close_retry(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    container = _build_fake_container(tmp_path, events)

    class Coordinator:
        fail_close = True

        async def activate(self) -> None:
            events.append("coordinator:activate")

        async def aclose(self) -> None:
            events.append("coordinator:close")
            if self.fail_close:
                raise RuntimeError("close failed")

    class FailingWorker:
        async def start(self) -> None:
            raise RuntimeError("worker start failed")

        async def stop(self) -> None:
            return None

        async def join(self) -> None:
            return None

    coordinator = Coordinator()
    container.cancellation_continuation_worker = FailingWorker()  # type: ignore[assignment]
    container.bind_runtime(coordinator)

    with pytest.raises(TaskRuntimeContainerError) as caught:
        asyncio.run(container.activate_runtime())

    assert caught.value.reason_code == TASK_RUNTIME_ACTIVATION_FAILED
    assert container.bound_runtime is coordinator
    assert not container.activated

    with pytest.raises(TaskRuntimeContainerError) as retry:
        asyncio.run(container.activate_runtime())
    assert retry.value.reason_code == TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED
    assert events.count("coordinator:activate") == 1

    with pytest.raises(TaskRuntimeContainerCloseError) as close_failure:
        asyncio.run(container.aclose())
    assert close_failure.value.failures[-1].component == (
        "runtime_coordinator_quiescence"
    )
    assert not container.closed
    assert "task-store:close" not in events

    coordinator.fail_close = False
    asyncio.run(container.aclose())
    assert events.count("coordinator:close") == 3
    assert container.closed


def test_runtime_group_retries_only_children_that_failed_to_close() -> None:
    class Runtime:
        def __init__(self, *, fail_close: bool) -> None:
            self.fail_close = fail_close
            self.close_calls = 0

        async def activate(self) -> None:
            return None

        async def close_admission(self) -> None:
            return None

        async def drain(self) -> None:
            return None

        async def aclose(self) -> None:
            self.close_calls += 1
            if self.fail_close:
                raise RuntimeError("close failed")

    healthy = Runtime(fail_close=False)
    retriable = Runtime(fail_close=True)
    group = GovernedRuntimeGroup({"healthy": healthy, "retriable": retriable})

    async def close_with_retry() -> None:
        await group.activate()
        with pytest.raises(RuntimeError, match="GOVERNED_RUNTIME_GROUP_CLOSE_FAILED"):
            await group.aclose()
        retriable.fail_close = False
        await group.aclose()

    asyncio.run(close_with_retry())
    assert healthy.close_calls == 1
    assert retriable.close_calls == 2


def test_nonquiescent_cancellation_worker_keeps_durable_stores_open(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    container = _build_fake_container(tmp_path, events)

    class RetriableWorker:
        fail = True

        async def start(self) -> None:
            events.append("worker:start")

        async def stop(self) -> None:
            events.append("worker:stop")
            if self.fail:
                raise RuntimeError("still running")

        async def join(self) -> None:
            events.append("worker:join")

        async def aclose(self) -> None:
            events.append("worker:close")

    worker = RetriableWorker()
    container.cancellation_continuation_worker = worker  # type: ignore[assignment]
    container.bind_runtime(
        SimpleNamespace(
            activate=lambda: None,
            aclose=lambda: None,
        )
    )
    events.clear()

    async def close_with_retry() -> None:
        await container.activate_runtime()
        events.clear()
        with pytest.raises(TaskRuntimeContainerCloseError) as caught:
            await container.aclose()
        assert caught.value.failures[-1].component == (
            "graph_cancellation_continuation_quiescence"
        )
        assert events == ["worker:stop"]
        assert not container.closed
        assert container.infrastructure_ready
        with pytest.raises(TaskRuntimeContainerError) as retry:
            await container.activate_runtime()
        assert retry.value.reason_code == TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED
        worker.fail = False
        await container.aclose()

    asyncio.run(close_with_retry())
    assert container.closed
    assert "task-store:close" in events


@pytest.mark.parametrize("failure_phase", ["admission", "drain"])
def test_coordinator_quiescence_failure_keeps_stores_open_for_retry(
    tmp_path: Path,
    failure_phase: str,
) -> None:
    events: list[str] = []
    container = _build_fake_container(tmp_path, events)

    class Coordinator:
        fail = True

        async def activate(self) -> None:
            return None

        async def close_admission(self) -> None:
            if self.fail and failure_phase == "admission":
                raise RuntimeError("admission close failed")

        async def drain(self) -> None:
            if self.fail and failure_phase == "drain":
                raise RuntimeError("drain failed")

        async def aclose(self) -> None:
            return None

    coordinator = Coordinator()
    container.bind_runtime(coordinator)

    async def close_with_retry() -> None:
        await container.activate_runtime()
        events.clear()
        with pytest.raises(TaskRuntimeContainerCloseError):
            await container.aclose()
        assert not container.closed
        assert "task-store:close" not in events
        with pytest.raises(TaskRuntimeContainerError) as retry:
            await container.activate_runtime()
        assert retry.value.reason_code == TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED
        coordinator.fail = False
        await container.aclose()

    asyncio.run(close_with_retry())
    assert container.closed
    assert "task-store:close" in events


def test_drain_outbox_reports_deferred_when_pending_row_is_not_claimable(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    container = _build_fake_container(tmp_path, events)
    container.outbox_publisher.backlog = {  # type: ignore[attr-defined]
        "total_pending": 2,
        "delivery_ready_count": 0,
        "next_wakeup_ts_ms": 9_999,
    }

    result = asyncio.run(container.drain_outbox())

    assert result == {
        "status": "DEFERRED",
        "batch_count": 1,
        "pending_scanned": 0,
        "published_count": 0,
        "remaining_pending": 2,
        "delivery_ready_count": 0,
        "next_wakeup_ts_ms": 9_999,
    }
    assert container.last_drain_result == result
    container.outbox_publisher.backlog = {  # type: ignore[attr-defined]
        "total_pending": 0,
        "delivery_ready_count": 0,
        "next_wakeup_ts_ms": None,
    }
    asyncio.run(container.aclose())


def test_canonical_authority_mismatch_builds_nothing(tmp_path: Path) -> None:
    called: list[str] = []

    with pytest.raises(TaskRuntimeContainerConstructionError) as caught:
        build_process_task_runtime_container(
            ledger=_Ledger(tmp_path / "ledger.sqlite3"),
            canonical_db_path=tmp_path / "different.sqlite3",
            migration_runner_builder=lambda **_kwargs: called.append("called"),
        )

    assert caught.value.reason_code == TASK_RUNTIME_CANONICAL_DB_PATH_MISMATCH
    assert called == []


def test_composition_boundary_does_not_import_app_layer() -> None:
    source_path = (
        Path(__file__).parents[2]
        / "host_brain"
        / "composition"
        / "task_runtime.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not any(name == "host_brain.app" for name in imports)
    assert not any(name.startswith("host_brain.app.") for name in imports)


def test_real_sqlite_adapters_share_the_migrated_canonical_ledger(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "canonical.sqlite3")
    container: ProcessTaskRuntimeContainer | None = None
    try:
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=ledger.canonical_db_path,
        )

        assert type(container.migration_runner) is SQLiteMigrationRunner
        assert type(container.task_store) is DurableTaskStore
        assert type(container.review_store) is ReviewStore
        assert type(container.worker_projection_store) is WorkerProjectionStore
        assert type(container.command_repository) is HostCommandRepository
        assert type(container.agent_result_repository) is AgentExecutionResultRepository
        assert type(container.scheduler_definition_repository) is (
            SchedulerDefinitionRepository
        )
        assert type(container.scheduler_result_repository) is (
            SchedulerExecutionResultRepository
        )
        assert type(container.event_store) is RuntimeEventStore
        assert type(container.outbox_publisher) is RuntimeOutboxPublisher
        assert str(container.task_store.db_path.resolve()) == (
            ledger.canonical_db_path
        )
        assert str(container.review_store.db_path.resolve()) == (
            ledger.canonical_db_path
        )
        assert str(container.worker_projection_store.db_path.resolve()) == (
            ledger.canonical_db_path
        )
        assert str(container.command_repository.db_path.resolve()) == (
            ledger.canonical_db_path
        )
        assert str(container.agent_result_repository.db_path.resolve()) == (
            ledger.canonical_db_path
        )
        assert str(container.scheduler_definition_repository.db_path.resolve()) == (
            ledger.canonical_db_path
        )
        assert str(container.scheduler_result_repository.db_path.resolve()) == (
            ledger.canonical_db_path
        )
        assert str(container.event_store.db_path.resolve()) == (
            ledger.canonical_db_path
        )
        assert container.migration_result.success
        assert container.recovery_report.recovery_id == container.recovery_id
        assert container.recovery_report.recovered_count == 0
        assert container.startup_publish_result["status"] == "OK"

        asyncio.run(container.aclose())
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=ledger.canonical_db_path,
        )
        assert container.migration_result.success
        assert container.migration_result.summary["applied_count"] == 0
    finally:
        if container is not None:
            asyncio.run(container.aclose())
        ledger.close()
