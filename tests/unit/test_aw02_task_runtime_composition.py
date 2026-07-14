from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.task_runtime import (
    TASK_RUNTIME_CANONICAL_DB_PATH_MISMATCH,
    TASK_RUNTIME_CONSTRUCTION_FAILED,
    TASK_RUNTIME_MIGRATION_FAILED,
    ProcessTaskRuntimeContainer,
    TaskRuntimeContainerConstructionError,
    build_process_task_runtime_container,
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
    ) -> None:
        super().__init__("publisher", events)
        self.task_store = task_store
        self.event_store = event_store

    def publish_pending(self, *, limit: int) -> dict[str, Any]:
        self.events.append(f"publisher:publish:{limit}")
        return {
            "status": "OK",
            "pending_scanned": 0,
            "published_count": 0,
            "published": [],
        }


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

    def build_store(db_path: str) -> _Closeable:
        assert db_path == str(path.resolve())
        events.append("task-store:build")
        return _Closeable("task-store", events)

    def build_repository(db_path: str) -> _Closeable:
        assert db_path == str(path.resolve())
        events.append("repository:build")
        return _Closeable("repository", events)

    def build_review_store(db_path: str) -> _Closeable:
        assert db_path == str(path.resolve())
        events.append("review-store:build")
        return _Closeable("review-store", events)

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
        "review-store:build",
        "worker-projection-store:build",
        "repository:build",
        "agent-result:build",
        "scheduler-definition:build",
        "scheduler-result:build",
        "event-store:build",
        "publisher:build",
        "publisher:publish:23",
    ]
    assert container.infrastructure_ready
    assert container.ledger is not None
    assert container.canonical_db_path == str(
        (tmp_path / "canonical.sqlite3").resolve()
    )
    assert container.outbox_publisher.task_store is container.task_store
    assert container.outbox_publisher.event_store is container.event_store
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


def test_partial_construction_failure_closes_completed_slots_in_reverse_order(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    path = tmp_path / "canonical.sqlite3"

    def build_store(_path: str) -> _Closeable:
        events.append("task-store:build")
        return _Closeable("task-store", events)

    def fail_repository(_path: str) -> Any:
        events.append("repository:build")
        raise OSError("unstable platform detail")

    def build_review_store(_path: str) -> _Closeable:
        events.append("review-store:build")
        return _Closeable("review-store", events)

    def build_worker_projection_store(_path: str) -> _Closeable:
        events.append("worker-projection-store:build")
        return _Closeable("worker-projection-store", events)

    with pytest.raises(TaskRuntimeContainerConstructionError) as caught:
        build_process_task_runtime_container(
            ledger=_Ledger(path),
            canonical_db_path=path,
            migration_runner_builder=lambda **_kwargs: _MigrationRunner(events),
            task_store_builder=build_store,
            review_store_builder=build_review_store,
            worker_projection_store_builder=build_worker_projection_store,
            command_repository_builder=fail_repository,
        )

    assert caught.value.reason_code == TASK_RUNTIME_CONSTRUCTION_FAILED
    assert caught.value.rollback_failures == ()
    assert "unstable platform detail" not in caught.value.detail
    assert events == [
        "migration:apply:startup::task-runtime",
        "task-store:build",
        "review-store:build",
        "worker-projection-store:build",
        "repository:build",
        "worker-projection-store:close",
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
    asyncio.run(container.activate_runtime())
    events.clear()

    asyncio.run(container.aclose())

    assert events == [
        "coordinator:admission-close",
        "coordinator:drain",
        "publisher:publish:1000",
        "coordinator:close",
        "publisher:close",
        "event-store:close",
        "scheduler-result:close",
        "scheduler-definition:close",
        "agent-result:close",
        "repository:close",
        "worker-projection-store:close",
        "review-store:close",
        "task-store:close",
        "migration:close",
    ]
    assert container.closed
    assert not container.activated
    asyncio.run(container.aclose())
    assert len(events) == 14


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
