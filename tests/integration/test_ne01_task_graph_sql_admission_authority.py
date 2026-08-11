from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetPolicy,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.host_command_repository import (
    HOST_COMMAND_SCHEMA_NOT_READY,
    HostCommandRepository,
    HostCommandRepositoryError,
)
from host_brain.persistence.task_graph_admission_authority import (
    canonical_task_graph_admission_scope,
)
from host_brain.persistence.task_repository import DurableTaskStore
from natsume_contracts.task.v1 import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


NOW = 1_800_000_000_000
MIGRATIONS = (
    Path(__file__).resolve().parents[2]
    / "host_brain"
    / "audit"
    / "migrations"
)
CURRENT_MIGRATION = max(
    int(path.name.split("_", 1)[0]) for path in MIGRATIONS.glob("*.sql")
)


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "task-graph-sql-authority.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-task-graph-sql-authority",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    assert result.summary["current_version"] == CURRENT_MIGRATION
    return db_path


def _migration_catalog_through_0123(tmp_path: Path) -> Path:
    target = tmp_path / "migrations-through-0123"
    target.mkdir()
    for path in MIGRATIONS.glob("*.sql"):
        if int(path.name.split("_", 1)[0]) <= 123:
            shutil.copy2(path, target / path.name)
    return target


def _admission() -> tuple[
    object,
    WorkOrderV3,
    ExecutorAssignment,
    TaskGraph,
]:
    operation_fingerprint = _sha256("task-graph-sql-authority-operation")
    work_order = WorkOrderV3(
        work_order_id="work-order-sql-authority",
        domain="code",
        run_mode="inspect",
        objective="prove canonical TaskGraph SQL admission authority",
        input_pack={"input_ref": "host-input://sql-authority"},
        allowed_actions=(),
        forbidden_actions=("write_files",),
        acceptance_criteria=("authority proven",),
        root_work_order_id="work-order-sql-authority",
        task_graph_id="graph-sql-authority",
        task_node_id="node-sql-authority",
        graph_revision=1,
        budget_policy=BudgetPolicy(
            scope="work-order:work-order-sql-authority",
            limits=BudgetAmount(
                token_count=1_000,
                tool_call_count=1,
                time_ms=60_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=100_000,
            ),
        ),
        business_idempotency_scope="host-command-sql-authority",
        operation_fingerprint=operation_fingerprint,
    )
    node = TaskNode(
        node_id="node-sql-authority",
        dependencies=(),
        route="host_chat",
        input_refs=("host-input://sql-authority",),
        output_contract={"type": "host_reply"},
        retry_policy={"max_attempts": 1},
        risk_level=1,
        acceptance_criteria=("authority proven",),
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id="graph-sql-authority",
        root_work_order_id=work_order.root_work_order_id,
        revision=1,
        planner_ref="planner://sql-authority",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(node,),
    )
    assignment = ExecutorAssignment(
        work_order_id=work_order.root_work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://sql-authority/canonical",
        assigned_at_ms=NOW,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-sql-authority",
        session_id="session-sql-authority",
        principal_id="principal-sql-authority",
        ingress_claim_ref=None,
        ingress_audit_ref=None,
        governor_grant_ref="governor-grant://sql-authority/1",
        input_ref="host-input://sql-authority",
        input_sha256=_sha256("sql-authority-input"),
        root_work_order_id=work_order.root_work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        route_type="chat",
        target_id=node.node_id,
        business_scope=work_order.business_idempotency_scope or "",
        operation_fingerprint=operation_fingerprint,
        created_at_ms=NOW,
    )
    return command, work_order, assignment, graph


def _insert_graph_row(
    conn: sqlite3.Connection,
    graph: TaskGraph,
    *,
    source_kind: str = "CANONICAL",
) -> None:
    conn.execute(
        """
        INSERT INTO task_runtime_graph(
            graph_id, revision, root_work_order_id, planner_ref, status,
            status_version, graph_json, source_kind, source_ref,
            legacy_snapshot_json, created_ts_ms, updated_ts_ms
        ) VALUES (?, ?, ?, ?, 'CREATED', 0, ?, ?, ?, NULL, ?, ?)
        """,
        (
            graph.graph_id,
            graph.revision,
            graph.root_work_order_id,
            graph.planner_ref,
            json.dumps(graph.to_dict(), ensure_ascii=False, sort_keys=True),
            source_kind,
            f"{graph.graph_id}@{graph.revision}",
            NOW,
            NOW,
        ),
    )


def test_plain_sqlite_and_unscoped_task_store_cannot_insert_graph(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    _command, _work_order, _assignment, graph = _admission()

    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(
            sqlite3.OperationalError,
            match="no such function: natsume_task_graph_admission_authorized",
        ):
            _insert_graph_row(conn, graph)
        conn.rollback()
    finally:
        conn.close()

    store = DurableTaskStore(db_path)
    with store._transaction() as guarded:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="TASK_GRAPH_ADMISSION_AUTHORITY_REQUIRED",
        ):
            _insert_graph_row(guarded, graph)
        with canonical_task_graph_admission_scope("graph-wrong-scope", 1):
            with pytest.raises(
                sqlite3.IntegrityError,
                match="TASK_GRAPH_ADMISSION_AUTHORITY_REQUIRED",
            ):
                _insert_graph_row(guarded, graph)
        with canonical_task_graph_admission_scope(graph.graph_id, graph.revision):
            with pytest.raises(
                sqlite3.IntegrityError,
                match="TASK_GRAPH_LEGACY_INSERT_RETIRED",
            ):
                _insert_graph_row(
                    guarded,
                    graph,
                    source_kind="LEGACY",
                )
    assert store.list_graphs() == []


def test_canonical_admission_and_exact_replay_are_the_only_positive_path(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _admission()
    repository = HostCommandRepository(db_path, clock=lambda: NOW)

    created = repository.admit(command, work_order, assignment, graph)
    assert created["created"] is True
    assert created["replayed"] is False
    replayed = repository.admit(command, work_order, assignment, graph)
    assert replayed["created"] is False
    assert replayed["replayed"] is True

    with sqlite3.connect(str(db_path)) as conn:
        counts = tuple(
            int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "task_runtime_graph",
                "task_runtime_node",
                "host_command_journal",
            )
        )
    assert counts == (1, 1, 1)


def test_admitted_graph_is_closed_to_late_node_and_raw_command_inserts(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _admission()
    HostCommandRepository(db_path, clock=lambda: NOW).admit(
        command,
        work_order,
        assignment,
        graph,
    )
    store = DurableTaskStore(db_path)

    with store._transaction() as conn:
        with canonical_task_graph_admission_scope(graph.graph_id, graph.revision):
            with pytest.raises(
                sqlite3.IntegrityError,
                match="TASK_NODE_ADMISSION_ALREADY_CLOSED",
            ):
                conn.execute(
                    """
                    INSERT INTO task_runtime_node(
                        graph_id, graph_revision, node_id, status, status_version,
                        node_json, cancellation_requested_ts_ms,
                        created_ts_ms, updated_ts_ms
                    ) VALUES (?, ?, 'late-node', 'READY', 0, ?, NULL, ?, ?)
                    """,
                    (
                        graph.graph_id,
                        graph.revision,
                        json.dumps(
                            {
                                **graph.nodes[0].to_dict(),
                                "node_id": "late-node",
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        NOW,
                        NOW,
                    ),
                )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="HOST_COMMAND_ADMISSION_AUTHORITY_REQUIRED",
        ):
            conn.execute(
                """
                INSERT INTO host_command_journal
                SELECT * FROM host_command_journal WHERE command_id = ?
                """,
                (str(command.command_id),),
            )


def test_trigger_tamper_fails_host_command_schema_readiness(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "DROP TRIGGER trg_task_runtime_node_admission_authority_insert"
        )
        conn.executescript(
            """
            CREATE TRIGGER trg_task_runtime_node_admission_authority_insert
            BEFORE INSERT ON task_runtime_node
            BEGIN
                SELECT CASE
                    WHEN natsume_task_graph_admission_authorized(
                        NEW.graph_id,
                        NEW.graph_revision
                    ) <> 1 AND 1 = 0 THEN
                        RAISE(ABORT, 'TASK_NODE_ADMISSION_AUTHORITY_REQUIRED')
                    WHEN NOT EXISTS (
                        SELECT 1
                        FROM task_runtime_graph AS graph
                        WHERE graph.graph_id = NEW.graph_id
                          AND graph.revision = NEW.graph_revision
                          AND graph.source_kind = 'CANONICAL'
                    ) THEN
                        RAISE(ABORT, 'TASK_NODE_CANONICAL_GRAPH_REQUIRED')
                    WHEN EXISTS (
                        SELECT 1
                        FROM host_command_journal AS admission
                        WHERE admission.graph_id = NEW.graph_id
                          AND admission.graph_revision = NEW.graph_revision
                    ) THEN
                        RAISE(ABORT, 'TASK_NODE_ADMISSION_ALREADY_CLOSED')
                END;
            END;
            """
        )

    with pytest.raises(HostCommandRepositoryError) as captured:
        HostCommandRepository(db_path)
    assert captured.value.reason_code == HOST_COMMAND_SCHEMA_NOT_READY


def test_migration_0124_partial_failure_rolls_back_and_exact_retry_succeeds(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "task-graph-admission-migration-rollback.sqlite3"
    head_123 = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_migration_catalog_through_0123(tmp_path),
    ).apply_migrations(
        request_id="ne01-task-graph-admission-head-0123",
        ts_ms=NOW,
    )
    assert head_123.success is True, head_123.as_dict()
    assert head_123.summary["current_version"] == 123

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "ALTER TABLE host_command_journal "
            "RENAME TO host_command_journal_blocked"
        )

    failed = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-task-graph-admission-fault",
        ts_ms=NOW + 1,
    )
    assert failed.success is False
    assert failed.records[-1].migration_id == "0124_task_graph_admission_authority"
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT MAX(migration_id) FROM schema_migrations"
        ).fetchone() == (123,)
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name LIKE 'trg_%admission_authority_insert'
            """
        ).fetchone() == (0,)
        conn.execute(
            "ALTER TABLE host_command_journal_blocked "
            "RENAME TO host_command_journal"
        )

    retried = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-task-graph-admission-retry",
        ts_ms=NOW + 2,
    )
    assert retried.success is True, retried.as_dict()
    assert retried.summary["current_version"] == CURRENT_MIGRATION
    HostCommandRepository(db_path).assert_schema_ready()
