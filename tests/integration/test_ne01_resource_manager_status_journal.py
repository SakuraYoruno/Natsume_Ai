from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from host_brain.app.observability_runtime.process_state_projection import (
    load_resource_manager_projection_source,
)
from host_brain.app.resource_manager_status_journal import (
    RuntimeEventResourceManagerStatusJournal,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.resource_manager_status import (
    RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE,
    RESOURCE_MANAGER_STATUS_EVENT_TYPE,
    ResourceManagerStatusJournalError,
)
from host_brain.core.resource_manager import SystemResourceManager
from host_brain.integrations.runtime_events import (
    RuntimeEvent,
    RuntimeEventError,
    RuntimeEventStore,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)


def _migrated_database(tmp_path: Path) -> Path:
    database = tmp_path / "resource-manager-status.sqlite3"
    result = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="ne01-resource-manager-status",
        ts_ms=int(time.time() * 1000),
    )
    assert result.success, result.as_dict()
    assert result.summary["current_version"] == 123
    return database


def _authority(
    database: Path,
    *,
    scope: str,
    owner: str,
) -> tuple[EmbodimentOutboxRepository, dict[str, Any]]:
    repository = EmbodimentOutboxRepository(
        database,
        runtime_scope_id=scope,
    )
    claim = repository.acquire_runtime_lease(
        worker_id=owner,
        lease_ms=300_000,
    )
    assert claim is not None
    return repository, {**claim, "runtime_scope_id": scope}


def _journal(
    database: Path,
    *,
    scope: str,
    claim: dict[str, Any],
    event_store: Any | None = None,
) -> RuntimeEventResourceManagerStatusJournal:
    return RuntimeEventResourceManagerStatusJournal(
        event_store=event_store or RuntimeEventStore(database),
        runtime_scope_id=scope,
        runtime_authority_provider=lambda: dict(claim),
    )


def test_status_journal_restarts_replays_exactly_and_projects_without_file(
    tmp_path: Path,
) -> None:
    database = _migrated_database(tmp_path)
    _repository, claim = _authority(
        database,
        scope="host-scope-a",
        owner="host-a",
    )
    observed = int(time.time() * 1000)
    status = {
        "schema_version": "Natsume.resource-manager.status.v1",
        "running": True,
        "tick_count": 3,
    }
    first = _journal(
        database,
        scope="host-scope-a",
        claim=claim,
    ).append_status(
        expected_revision=0,
        reason="started",
        observed_at_ms=observed,
        payload=status,
    )

    restarted = _journal(
        database,
        scope="host-scope-a",
        claim=claim,
    )
    assert restarted.recover_head() == first
    assert restarted.append_status(
        expected_revision=0,
        reason="started",
        observed_at_ms=observed,
        payload=status,
    ) == first
    with pytest.raises(
        ResourceManagerStatusJournalError,
        match="RESOURCE_MANAGER_STATUS_REPLAY_CONFLICT",
    ):
        restarted.append_status(
            expected_revision=0,
            reason="started",
            observed_at_ms=observed,
            payload={**status, "tick_count": 4},
        )

    projection, source_cursor, source = load_resource_manager_projection_source(
        event_store=RuntimeEventStore(database),
    )
    assert source == "runtime_event_journal"
    assert source_cursor > 0
    assert projection["status"] == "OK"
    assert projection["runtime_scope_id"] == "host-scope-a"
    assert projection["status_revision"] == 1
    assert projection["tick_count"] == 3
    assert projection["status_event_id"] == first.event_id
    assert "status_path" not in projection


def test_response_loss_reopens_the_exact_committed_event(tmp_path: Path) -> None:
    database = _migrated_database(tmp_path)
    _repository, claim = _authority(
        database,
        scope="host-scope-response-loss",
        owner="host-response-loss",
    )
    delegate = RuntimeEventStore(database)

    class ResponseLostStore:
        def list_events_for_aggregates(self, **kwargs: Any) -> Any:
            return delegate.list_events_for_aggregates(**kwargs)

        def get_event_by_id(self, **kwargs: Any) -> Any:
            return delegate.get_event_by_id(**kwargs)

        def append_event(self, event: RuntimeEvent) -> Any:
            delegate.append_event(event)
            raise TimeoutError("response lost after commit")

    journal = _journal(
        database,
        scope="host-scope-response-loss",
        claim=claim,
        event_store=ResponseLostStore(),
    )
    head = journal.append_status(
        expected_revision=0,
        reason="started",
        observed_at_ms=int(time.time() * 1000),
        payload={
            "schema_version": "Natsume.resource-manager.status.v1",
            "running": True,
        },
    )

    assert head.revision == 1
    assert (
        len(
            delegate.list_events_for_aggregates(
                aggregate_refs=(
                    (
                        RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE,
                        "host-scope-response-loss",
                    ),
                ),
                limit=10,
            )
        )
        == 1
    )


def test_status_recovery_crosses_the_1000_row_cursor_boundary(
    tmp_path: Path,
) -> None:
    database = _migrated_database(tmp_path)
    _repository, claim = _authority(
        database,
        scope="host-scope-paged",
        owner="host-paged",
    )
    journal = _journal(
        database,
        scope="host-scope-paged",
        claim=claim,
    )
    observed = int(time.time() * 1000)
    for revision in range(1001):
        journal.append_status(
            expected_revision=revision,
            reason="periodic_tick",
            observed_at_ms=observed + revision,
            payload={"running": True, "tick_count": revision},
        )

    recovered = _journal(
        database,
        scope="host-scope-paged",
        claim=claim,
    ).recover_head()
    assert recovered is not None
    assert recovered.revision == 1001
    assert dict(recovered.payload["status"])["tick_count"] == 1000


def test_stale_host_is_fenced_and_two_runtime_scopes_are_isolated(
    tmp_path: Path,
) -> None:
    database = _migrated_database(tmp_path)
    first_repository, first_claim = _authority(
        database,
        scope="host-scope-first",
        owner="host-first",
    )
    _second_repository, second_claim = _authority(
        database,
        scope="host-scope-second",
        owner="host-second",
    )
    first = _journal(
        database,
        scope="host-scope-first",
        claim=first_claim,
    )
    second = _journal(
        database,
        scope="host-scope-second",
        claim=second_claim,
    )
    first_head = first.append_status(
        expected_revision=0,
        reason="started",
        observed_at_ms=int(time.time() * 1000),
        payload={"running": True},
    )
    second_head = second.append_status(
        expected_revision=0,
        reason="started",
        observed_at_ms=int(time.time() * 1000),
        payload={"running": True},
    )
    assert first_head.runtime_scope_id != second_head.runtime_scope_id
    assert first_head.revision == second_head.revision == 1

    assert first_repository.release_runtime_lease(
        worker_id=str(first_claim["owner_id"]),
        lease_id=str(first_claim["lease_id"]),
        fencing_token=int(first_claim["fencing_token"]),
    )
    with pytest.raises(
        ResourceManagerStatusJournalError,
        match="RESOURCE_MANAGER_STATUS_APPEND_FAILED",
    ):
        first.append_status(
            expected_revision=1,
            reason="periodic_tick",
            observed_at_ms=int(time.time() * 1000),
            payload={"running": True, "tick_count": 1},
        )

    heads = RuntimeEventStore(database).list_latest_event_heads(
        aggregate_type=RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE,
        event_type=RESOURCE_MANAGER_STATUS_EVENT_TYPE,
        limit=10,
    )
    assert {str(row["aggregate_id"]) for row in heads} == {
        "host-scope-first",
        "host-scope-second",
    }


def test_migration_rejects_incomplete_authority_and_journal_is_append_only(
    tmp_path: Path,
) -> None:
    database = _migrated_database(tmp_path)
    _repository, claim = _authority(
        database,
        scope="host-scope-tamper",
        owner="host-tamper",
    )
    store = RuntimeEventStore(database)
    with pytest.raises(RuntimeEventError):
        store.append_event(
            RuntimeEvent.create(
                event_type=RESOURCE_MANAGER_STATUS_EVENT_TYPE,
                source_kind="SYSTEM",
                source_ref="resource-manager://status/incomplete",
                aggregate_type=RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE,
                aggregate_id="host-scope-tamper",
                aggregate_version=1,
                payload_ref="resource-manager://status/incomplete/revision/1",
                payload={
                    "runtime_scope_id": "host-scope-tamper",
                    "status_revision": 1,
                    "status": {},
                },
                ts_ms=int(time.time() * 1000),
            )
        )

    head = _journal(
        database,
        scope="host-scope-tamper",
        claim=claim,
    ).append_status(
        expected_revision=0,
        reason="started",
        observed_at_ms=int(time.time() * 1000),
        payload={"running": True},
    )
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE runtime_event_timeline SET payload_digest = ? "
                "WHERE event_id = ?",
                ("sha256:" + "0" * 64, head.event_id),
            )


def test_resource_manager_start_and_stop_are_durable_without_status_file(
    tmp_path: Path,
) -> None:
    database = _migrated_database(tmp_path)
    _repository, claim = _authority(
        database,
        scope="host-scope-manager",
        owner="host-manager",
    )
    manager = SystemResourceManager(
        interval_s=3600,
        llm_enabled=False,
        auto_gc_enabled=False,
        auto_trim_enabled=False,
        degradation_state_provider=lambda: {
            "status": "READY",
            "durability_status": "ACTIVE",
        },
        status_journal=_journal(
            database,
            scope="host-scope-manager",
            claim=claim,
        ),
    )

    manager.start(run_immediately=False)
    started = manager.get_status()
    assert started["durability"]["status"] == "ACTIVE"
    assert started["durability"]["revision"] == 1
    manager.stop()
    stopped = manager.get_status()
    assert stopped["running"] is False
    assert stopped["durability"]["revision"] == 2
    assert not (tmp_path / "runtime_resource_manager.json").exists()
