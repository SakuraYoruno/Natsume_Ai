from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.durable_task_status_projection import (
    DurableTaskStatusProjection,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from natsume_contracts.task.v1 import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


NOW = 1_900_000_000_000


def _sha(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "durable-task-status.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="durable-task-status-migration",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _admit(db_path: Path) -> None:
    graph = TaskGraph(
        graph_id="graph-status-1",
        root_work_order_id="work-order-status-1",
        revision=1,
        planner_ref="planner://status-projection/immutable",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(
            TaskNode(
                node_id="node-status-1",
                dependencies=(),
                route="delegate:research_agent",
                input_refs=(),
                output_contract={"type": "object"},
                retry_policy={"max_attempts": 1},
                risk_level=1,
                acceptance_criteria=("return one durable result",),
                status=TaskNodeStatus.READY,
            ),
        ),
    )
    work_order = WorkOrderV3(
        work_order_id="work-order-status-1",
        domain="research",
        run_mode="inspect",
        objective="project durable task status",
        acceptance_criteria=("return one durable result",),
        root_work_order_id="work-order-status-1",
        task_graph_id=graph.graph_id,
        task_node_id="node-status-1",
        graph_revision=1,
        approval_policy={"required": False},
        review_policy={"review_required": False},
        budget_policy=BudgetPolicy(
            scope="work-order:work-order-status-1",
            limits=BudgetAmount(
                token_count=1_000,
                tool_call_count=10,
                time_ms=60_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=1_000_000,
            ),
        ),
    )
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-status-1",
        session_id="session-status-1",
        principal_id="person-status-1",
        input_ref="host-input://status/1",
        input_sha256=_sha("status input"),
        root_work_order_id=work_order.work_order_id,
        graph_id=graph.graph_id,
        graph_revision=1,
        route_type="plan",
        target_id="planning_search",
        business_scope="host-command-status",
        operation_fingerprint=_sha("status operation"),
        created_at_ms=NOW,
    )
    assignment = ExecutorAssignment(
        work_order_id=work_order.work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://work-order-status-1/canonical",
        assigned_at_ms=NOW,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    result = HostCommandRepository(db_path, clock=lambda: NOW).admit(
        command,
        work_order,
        assignment,
        graph,
    )
    assert result["created"] is True


def _make_running(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            UPDATE task_runtime_graph
            SET status = 'ACTIVE', status_version = 1, updated_ts_ms = ?
            WHERE graph_id = 'graph-status-1' AND revision = 1
            """,
            (NOW + 100,),
        )
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = 'RUNNING', status_version = 1, updated_ts_ms = ?
            WHERE graph_id = 'graph-status-1' AND graph_revision = 1
              AND node_id = 'node-status-1'
            """,
            (NOW + 110,),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms, acquired_ts_ms,
                started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
            ) VALUES (
                'attempt-status-1', 'graph-status-1', 1, 'node-status-1', 1,
                'RUNNING', 2, 'worker-status-1', 'status-scope',
                'status-fingerprint', ?, ?, ?, NULL, ?, NULL
            )
            """,
            (NOW + 105, NOW + 106, NOW + 110, NOW + 110),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_lease(
                lease_id, attempt_id, owner_id, fencing_token, acquired_ts_ms,
                heartbeat_ts_ms, expires_ts_ms, released_ts_ms, status,
                status_version
            ) VALUES (
                'lease-status-1', 'attempt-status-1', 'worker-status-1', 1,
                ?, ?, ?, NULL, 'ACTIVE', 0
            )
            """,
            (NOW + 106, NOW + 110, NOW + 60_000),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_idempotency_reservation(
                reservation_id, business_scope, operation_fingerprint,
                attempt_id, lease_id, owner_id, fencing_token, expires_ts_ms,
                status, status_version, receipt_ref, legacy_receipt_raw,
                reconciliation_proof_ref, reconciliation_proof_digest,
                reconciliation_proof_key_id, reconciliation_proof_signature,
                reconciliation_action, reconciliation_outcome,
                reconciler_actor_id, reconciliation_recovery_version,
                recovery_reason_code, recovery_ts_ms, created_ts_ms,
                updated_ts_ms
            ) VALUES (
                'reservation-status-1', 'status-scope', 'status-fingerprint',
                'attempt-status-1', 'lease-status-1', 'worker-status-1', 1, ?,
                'RESERVED', 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                NULL, NULL, NULL, NULL, NULL, ?, ?
            )
            """,
            (NOW + 60_000, NOW + 110, NOW + 110),
        )
        zero = json.dumps(
            {
                "token_count": 0,
                "tool_call_count": 0,
                "time_ms": 0,
                "concurrency": 0,
                "retry_count": 0,
                "model_cost_micros": 0,
            },
            separators=(",", ":"),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_budget_reservation(
                budget_id, scope, graph_id, graph_revision, node_id, attempt_id,
                limits_json, reserved_json, consumed_json,
                attempted_consumed_json, exceeded_dimensions_json, status,
                status_version, created_ts_ms, updated_ts_ms
            ) VALUES (
                'budget-status-1', 'status-scope', 'graph-status-1', 1,
                'node-status-1', 'attempt-status-1', ?, ?, ?, ?, '[]',
                'ACTIVE', 1, ?, ?
            )
            """,
            (zero, zero, zero, zero, NOW + 110, NOW + 110),
        )


def _make_completed(db_path: Path) -> None:
    projection = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "narrative": "durable research result",
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            UPDATE task_runtime_graph
            SET status = 'COMPLETED', status_version = 2, updated_ts_ms = ?
            WHERE graph_id = 'graph-status-1' AND revision = 1
            """,
            (NOW + 500,),
        )
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = 'ACCEPTED', status_version = 2, updated_ts_ms = ?
            WHERE graph_id = 'graph-status-1' AND graph_revision = 1
              AND node_id = 'node-status-1'
            """,
            (NOW + 490,),
        )
        conn.execute(
            """
            UPDATE task_runtime_attempt
            SET status = 'SUCCEEDED', status_version = 3,
                completed_ts_ms = ?, updated_ts_ms = ?
            WHERE attempt_id = 'attempt-status-1'
            """,
            (NOW + 480, NOW + 480),
        )
        conn.execute(
            """
            UPDATE task_runtime_idempotency_reservation
            SET status = 'COMMITTED', status_version = 1,
                receipt_ref = 'worker-projection://status/1', updated_ts_ms = ?
            WHERE reservation_id = 'reservation-status-1'
            """,
            (NOW + 470,),
        )
        conn.execute(
            """
            UPDATE task_runtime_budget_reservation
            SET status = 'CONSUMED', status_version = 2, updated_ts_ms = ?
            WHERE budget_id = 'budget-status-1'
            """,
            (NOW + 475,),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_worker_projection(
                projection_id, schema_version, payload_digest, command_id,
                request_id, graph_id, graph_revision, node_id, attempt_id,
                route_type, target_id, decision, projection_json, created_ts_ms
            ) VALUES (
                'worker-projection://status/1',
                'Natsume.task_runtime.worker_projection.v1',
                'sha256:projection-status-1', 'command-status-1',
                'request-status-1', 'graph-status-1', 1, 'node-status-1',
                'attempt-status-1', 'delegate', 'research_agent', 'APPROVE',
                ?, ?
            )
            """,
            (json.dumps(projection, ensure_ascii=False), NOW + 465),
        )


def test_projection_rebuilds_running_and_terminal_status_across_restart(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    _admit(db_path)
    _make_running(db_path)

    first = DurableTaskStatusProjection(db_path, clock=lambda: NOW + 1_000)
    running = first.get_by_request_id(
        "request-status-1",
        session_id="session-status-1",
    )
    assert running is not None
    assert running["source"] == "durable_task_runtime"
    assert running["status"] == "RUNNING"
    assert running["graph_status"] == "ACTIVE"
    assert running["target_id"] == "research_agent"
    assert running["attempt_status_counts"] == {"RUNNING": 1}
    assert running["reservation_status_counts"] == {"RESERVED": 1}
    assert running["budget_status_counts"] == {"ACTIVE": 1}
    assert running["receipt_refs"] == []
    assert running["elapsed_ms"] == 1_000
    assert first.get_by_request_id(
        "request-status-1",
        session_id="different-session",
    ) is None
    assert first.summarize_for_session(
        session_id="session-status-1",
        person_id="person-status-1",
    ) == running
    assert first.summarize_for_session(
        session_id="session-status-1",
        person_id="person-status-1",
        exclude_request_id="request-status-1",
    ) is None

    restarted = DurableTaskStatusProjection(db_path, clock=lambda: NOW + 1_000)
    assert restarted.get_by_request_id("request-status-1") == running

    _make_completed(db_path)
    after_completion = DurableTaskStatusProjection(
        db_path,
        clock=lambda: NOW + 99_000,
    ).get_by_request_id("request-status-1")
    assert after_completion is not None
    assert after_completion["status"] == "COMPLETED"
    assert after_completion["graph_status"] == "COMPLETED"
    assert after_completion["node_status_counts"] == {"ACCEPTED": 1}
    assert after_completion["attempt_status_counts"] == {"SUCCEEDED": 1}
    assert after_completion["reservation_status_counts"] == {"COMMITTED": 1}
    assert after_completion["budget_status_counts"] == {"CONSUMED": 1}
    assert after_completion["receipt_refs"] == ["worker-projection://status/1"]
    assert after_completion["result_text"] == "durable research result"
    assert after_completion["elapsed_ms"] == 500
    assert after_completion["finished_ts_ms"] == NOW + 500


def test_projection_exposes_no_status_mutation_api(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    _admit(db_path)
    projection = DurableTaskStatusProjection(db_path)

    for forbidden in (
        "create_task",
        "update_task",
        "mark_completed",
        "mark_failed",
        "mark_recovering",
    ):
        assert not hasattr(projection, forbidden)

    before = db_path.stat().st_mtime_ns
    assert projection.get_by_request_id("request-status-1") is not None
    assert db_path.stat().st_mtime_ns == before
