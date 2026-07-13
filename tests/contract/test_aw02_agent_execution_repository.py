from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.agent_execution_repository import (
    AGENT_EXECUTION_MIGRATION_ASSET_KEY,
    AGENT_EXECUTION_MIGRATION_SHA256,
    AGENT_EXECUTION_RESULT_CONFLICT,
    AGENT_EXECUTION_RESULT_INVALID,
    AgentExecutionRepositoryError,
    AgentExecutionResultRepository,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from natsume_contracts.task import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


NOW = 1_800_000_000_000


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _admitted_db(tmp_path: Path) -> tuple[Path, str, str]:
    db_path = tmp_path / "agent-result.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw02-agent-result",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    work_order = AgentWorkOrder(
        work_order_id="work-order-agent-result",
        domain="code",
        run_mode="inspect",
        objective="inspect the governed repository",
        input_pack={"input_ref": "input://agent-result/1"},
        allowed_actions=[],
        forbidden_actions=["write_files"],
        acceptance_criteria=["result persisted"],
    )
    node = TaskNode(
        node_id="node-agent-result",
        dependencies=(),
        route="internal_agent",
        input_refs=("input://agent-result/1",),
        output_contract={"type": "agent_result"},
        retry_policy={"max_attempts": 1},
        risk_level=1,
        acceptance_criteria=("result persisted",),
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id="graph-agent-result",
        root_work_order_id=work_order.work_order_id,
        revision=1,
        planner_ref="planner://internal-agent/result",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(node,),
    )
    assignment = ExecutorAssignment(
        work_order_id=work_order.work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://work-order-agent-result/canonical",
        assigned_at_ms=NOW,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    command = build_host_command(
        surface=HostCommandSurface.INTERNAL,
        request_id="request-agent-result",
        session_id="session-agent-result",
        principal_id="principal-agent-result",
        input_ref="input://agent-result/1",
        input_sha256=_sha256("agent-result-input"),
        root_work_order_id=work_order.work_order_id,
        graph_id=graph.graph_id,
        graph_revision=1,
        route_type="internal_agent",
        target_id="agent_runtime",
        business_scope="internal-agent:code",
        operation_fingerprint=_sha256("agent-result-operation"),
        created_at_ms=NOW,
    )
    HostCommandRepository(db_path, clock=lambda: NOW).admit(
        command,
        work_order,
        assignment,
        graph,
    )
    return db_path, command.command_id, work_order.work_order_id


def test_result_is_immutable_digest_bound_and_exactly_replayable(tmp_path: Path) -> None:
    db_path, command_id, work_order_id = _admitted_db(tmp_path)
    repository = AgentExecutionResultRepository(db_path, clock=lambda: NOW + 1)
    result = {
        "status": "EXECUTED",
        "summary": "governed inspection completed",
        "result_text": "all checks passed",
        "artifacts_dir": str(tmp_path / "artifacts"),
    }

    created = repository.append(
        command_id=command_id,
        work_order_id=work_order_id,
        result=result,
    )
    replay = repository.append(
        command_id=command_id,
        work_order_id=work_order_id,
        result=result,
    )

    assert created["created"] is True
    assert created["receipt_ref"].startswith("agentresult://sha256/")
    assert replay["replayed"] is True
    assert replay["receipt_ref"] == created["receipt_ref"]
    assert repository.get(created["receipt_ref"])["result"] == result
    with pytest.raises(AgentExecutionRepositoryError) as conflict:
        repository.append(
            command_id=command_id,
            work_order_id=work_order_id,
            result={**result, "summary": "drifted"},
        )
    assert conflict.value.reason_code == AGENT_EXECUTION_RESULT_CONFLICT


def test_result_rejects_secret_material_and_schema_checksum_drift(tmp_path: Path) -> None:
    db_path, command_id, work_order_id = _admitted_db(tmp_path)
    repository = AgentExecutionResultRepository(db_path)
    with pytest.raises(AgentExecutionRepositoryError) as rejected:
        repository.append(
            command_id=command_id,
            work_order_id=work_order_id,
            result={"status": "EXECUTED", "api_key": "must-not-persist"},
        )
    assert rejected.value.reason_code == AGENT_EXECUTION_RESULT_INVALID

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT checksum FROM schema_migration_assets WHERE asset_key = ?",
            (AGENT_EXECUTION_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert row is not None
    assert row[0] == AGENT_EXECUTION_MIGRATION_SHA256
