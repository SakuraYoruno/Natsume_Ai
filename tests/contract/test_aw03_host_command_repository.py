from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
from pathlib import Path

import pytest

from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetExceededAction,
    BudgetPolicy,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.host_command_repository import (
    HOST_COMMAND_CONFLICT,
    HOST_COMMAND_DURABLE_DATA_INVALID,
    HOST_COMMAND_INGRESS_CONFLICT,
    HOST_COMMAND_MIGRATION_ASSET_KEY,
    HOST_COMMAND_MIGRATION_SHA256,
    HOST_COMMAND_SCHEMA_NOT_READY,
    HostCommandRepository,
    HostCommandRepositoryError,
)
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_SCHEMA_VERSION,
    WorkerProjectionStore,
)
import host_brain.persistence.host_command_repository as repository_module
from natsume_contracts.task.v1 import (
    ExecutorAssignment,
    HostCommand,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


NOW = 1_800_000_000_000


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "host-command.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-host-command-migration",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _admission(
    *,
    surface: HostCommandSurface = HostCommandSurface.CHAT,
    request_id: str = "request-aw03-1",
    input_sha256: str | None = None,
    ingress_claim_ref: str | None = None,
    ingress_audit_ref: str | None = None,
) -> tuple[HostCommand, WorkOrderV3, ExecutorAssignment, TaskGraph]:
    work_order = WorkOrderV3(
        work_order_id="work-order-aw03-1",
        domain="code",
        run_mode="inspect",
        objective="execute the admitted host command",
        input_pack={"input_ref": "host-input://request-aw03-1"},
        allowed_actions=(),
        forbidden_actions=("write_files",),
        acceptance_criteria=("reply produced",),
        root_work_order_id="work-order-aw03-1",
        task_graph_id="graph-aw03-1",
        task_node_id="node-aw03-1",
        graph_revision=1,
        budget_policy=BudgetPolicy(
            scope="work-order:work-order-aw03-1",
            limits=BudgetAmount(
                token_count=10_000,
                tool_call_count=10,
                time_ms=60_000,
                concurrency=1,
                retry_count=3,
                model_cost_micros=1_000_000,
            ),
        ),
        business_idempotency_scope="host-command-aw03",
        operation_fingerprint=_sha256("aw03-operation"),
    )
    node = TaskNode(
        node_id="node-aw03-1",
        dependencies=(),
        route="host_chat",
        input_refs=("host-input://request-aw03-1",),
        output_contract={"type": "host_reply"},
        retry_policy={"max_attempts": 1},
        risk_level=1,
        acceptance_criteria=("reply produced",),
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id="graph-aw03-1",
        root_work_order_id=work_order.work_order_id,
        revision=1,
        planner_ref="planner://host-command/aw03",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(node,),
    )
    assignment = ExecutorAssignment(
        work_order_id=work_order.work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://work-order-aw03-1/canonical",
        assigned_at_ms=NOW,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    command = build_host_command(
        surface=surface,
        request_id=request_id,
        session_id="session-aw03-1",
        principal_id="principal-aw03-1",
        ingress_claim_ref=ingress_claim_ref,
        ingress_audit_ref=ingress_audit_ref,
        governor_grant_ref="governor-grant://aw03/1",
        input_ref="host-input://request-aw03-1",
        input_sha256=input_sha256 or _sha256("aw03-input"),
        root_work_order_id=work_order.work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        route_type="chat",
        target_id=node.node_id,
        business_scope="host-command-aw03",
        operation_fingerprint=_sha256("aw03-operation"),
        created_at_ms=NOW,
    )
    return command, work_order, assignment, graph


def _insert_committed_claim(
    db_path: Path,
    *,
    claim_ref: str,
    audit_ref: str,
    request_id: str,
    input_sha256: str,
    result_ref: str | None = None,
) -> None:
    stored_body_sha256 = str(input_sha256).removeprefix("sha256:")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO secure_ingress_idempotency_claim(
                principal_id, surface, idempotency_digest, action,
                required_scopes_json, request_id, body_sha256, claim_ref,
                result_ref, claim_status, provisional_expires_at_ms,
                claimed_at_ms, updated_at_ms, expires_at_ms
            ) VALUES (?, 'host.input', ?, 'host:input', '["host:write"]', ?, ?, ?, ?,
                      'COMMITTED', NULL, ?, ?, ?)
            """,
            (
                "principal-aw03-1",
                _sha256("wire-idempotency"),
                request_id,
                stored_body_sha256,
                claim_ref,
                result_ref,
                NOW,
                NOW,
                NOW + 60_000,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_audit(
                audit_ref, request_id, correlation_id, surface, action, method,
                normalized_path, principal_id, tenant_id, key_id,
                credential_kind, credential_ref_digest, requested_scopes_json,
                required_scopes_json, body_sha256, idempotency_digest,
                nonce_digest, transport_key_digest, decision, reason_code,
                stage, claim_ref, rate_snapshot_json, side_effects_executed, ts_ms
            ) VALUES (?, ?, NULL, 'host.input', 'host:input', 'POST', '/v1/host/input',
                      'principal-aw03-1', 'tenant-aw03', 'key-aw03', 'HMAC_SHA256',
                      ?, '["host:write"]', '["host:write"]', ?, ?, ?, ?, 'ALLOW',
                      'SECURE_INGRESS_ALLOWED', 'COMPLETE', ?, '{}', 0, ?)
            """,
            (
                audit_ref,
                request_id,
                _sha256("credential-ref"),
                stored_body_sha256,
                _sha256("wire-idempotency"),
                _sha256("nonce"),
                _sha256("transport"),
                claim_ref,
                NOW,
            ),
        )


def _tool_admission(
    *,
    claim_ref: str,
    audit_ref: str,
) -> tuple[HostCommand, WorkOrderV3, ExecutorAssignment, TaskGraph]:
    work_order = WorkOrderV3(
        work_order_id="work-order-aw03-tool",
        domain="tool",
        run_mode="inspect",
        objective="execute the admitted governed Tool command",
        input_pack={
            "input_ref": "tool-input://request-aw03-tool",
            "intent_draft": {"route": "tool", "target_id": "read_status"},
        },
        allowed_actions=("tool:read_status",),
        forbidden_actions=(),
        acceptance_criteria=("tool result verified",),
        root_work_order_id="work-order-aw03-tool",
        task_graph_id="graph-aw03-tool",
        task_node_id="node-aw03-tool",
        graph_revision=1,
        budget_policy=BudgetPolicy(
            scope="work-order:work-order-aw03-tool",
            limits=BudgetAmount(
                token_count=0,
                tool_call_count=1,
                time_ms=60_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=0,
            ),
        ),
        business_idempotency_scope="host-tool:read_status",
        operation_fingerprint=_sha256("aw03-tool-operation"),
    )
    node = TaskNode(
        node_id="node-aw03-tool",
        dependencies=(),
        route="tool",
        input_refs=("tool-input://request-aw03-tool",),
        output_contract={"type": "tool_result"},
        retry_policy={"max_attempts": 1},
        risk_level=1,
        acceptance_criteria=("tool result verified",),
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id="graph-aw03-tool",
        root_work_order_id=work_order.work_order_id,
        revision=1,
        planner_ref="planner://host-command/aw03/tool",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(node,),
    )
    assignment = ExecutorAssignment(
        work_order_id=work_order.work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://work-order-aw03-tool/canonical",
        assigned_at_ms=NOW,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    command = build_host_command(
        surface=HostCommandSurface.TOOL,
        request_id="request-aw03-tool",
        session_id="session-aw03-tool",
        principal_id="principal-aw03-tool",
        ingress_claim_ref=claim_ref,
        ingress_audit_ref=audit_ref,
        governor_grant_ref="governor-grant://aw03/tool",
        input_ref="tool-input://request-aw03-tool",
        input_sha256=_sha256("aw03-tool-input"),
        root_work_order_id=work_order.work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        route_type="tool",
        target_id="read_status",
        business_scope="host-tool:read_status",
        operation_fingerprint=_sha256("aw03-tool-operation"),
        created_at_ms=NOW,
    )
    return command, work_order, assignment, graph


def _insert_tool_claim(
    db_path: Path,
    *,
    claim_ref: str,
    audit_ref: str,
    result_ref: str | None = None,
) -> None:
    input_digest = _sha256("aw03-tool-input").removeprefix("sha256:")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO secure_ingress_idempotency_claim(
                principal_id, surface, idempotency_digest, action,
                required_scopes_json, request_id, body_sha256, claim_ref,
                result_ref, claim_status, provisional_expires_at_ms,
                claimed_at_ms, updated_at_ms, expires_at_ms
            ) VALUES (?, 'host.tool', ?, 'host:tool', '["host:write"]', ?, ?, ?, ?,
                      'COMMITTED', NULL, ?, ?, ?)
            """,
            (
                "principal-aw03-tool",
                _sha256("wire-tool-idempotency"),
                "request-aw03-tool",
                input_digest,
                claim_ref,
                result_ref,
                NOW,
                NOW,
                NOW + 60_000,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_audit(
                audit_ref, request_id, correlation_id, surface, action, method,
                normalized_path, principal_id, tenant_id, key_id,
                credential_kind, credential_ref_digest, requested_scopes_json,
                required_scopes_json, body_sha256, idempotency_digest,
                nonce_digest, transport_key_digest, decision, reason_code,
                stage, claim_ref, rate_snapshot_json, side_effects_executed, ts_ms
            ) VALUES (?, 'request-aw03-tool', NULL, 'host.tool', 'host:tool', 'POST',
                      '/tools/execute', 'principal-aw03-tool', 'tenant-aw03',
                      'key-aw03-tool', 'HMAC_SHA256', ?, '["host:write"]',
                      '["host:write"]', ?, ?, ?, ?, 'ALLOW',
                      'SECURE_INGRESS_ALLOWED', 'COMPLETE', ?, '{}', 0, ?)
            """,
            (
                audit_ref,
                _sha256("credential-tool-ref"),
                input_digest,
                _sha256("wire-tool-idempotency"),
                _sha256("tool-nonce"),
                _sha256("tool-transport"),
                claim_ref,
                NOW,
            ),
        )


def _set_tool_terminal(
    db_path: Path,
    *,
    graph_status: str = "FAILED",
    node_status: str = "FAILED",
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        graph_row = conn.execute(
            """
            SELECT graph_json FROM task_runtime_graph
            WHERE graph_id = 'graph-aw03-tool' AND revision = 1
            """
        ).fetchone()
        node_row = conn.execute(
            """
            SELECT node_json FROM task_runtime_node
            WHERE graph_id = 'graph-aw03-tool' AND graph_revision = 1
              AND node_id = 'node-aw03-tool'
            """
        ).fetchone()
        assert graph_row is not None and node_row is not None
        graph_payload = json.loads(graph_row[0])
        node_payload = json.loads(node_row[0])
        graph_payload["status"] = graph_status
        graph_payload["status_version"] = 1
        node_payload["status"] = node_status
        node_payload["status_version"] = 1
        graph_payload["nodes"] = [node_payload]
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = ?, status_version = 1, node_json = ?, updated_ts_ms = ?
            WHERE graph_id = 'graph-aw03-tool' AND graph_revision = 1
              AND node_id = 'node-aw03-tool'
            """,
            (node_status, json.dumps(node_payload, sort_keys=True), NOW + 1),
        )
        conn.execute(
            """
            UPDATE task_runtime_graph
            SET status = ?, status_version = 1, graph_json = ?, updated_ts_ms = ?
            WHERE graph_id = 'graph-aw03-tool' AND revision = 1
            """,
            (graph_status, json.dumps(graph_payload, sort_keys=True), NOW + 1),
        )


def _counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(str(db_path)) as conn:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "host_command_journal",
                "task_runtime_graph",
                "task_runtime_node",
                "task_runtime_outbox",
            )
        }


def test_aw03_migration_is_catalog_owned_and_repository_contains_no_ddl(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    repository = HostCommandRepository(db_path, clock=lambda: NOW)
    repository.assert_schema_ready()

    with sqlite3.connect(str(db_path)) as conn:
        asset = conn.execute(
            "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key = ?",
            (HOST_COMMAND_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert asset == (HOST_COMMAND_MIGRATION_SHA256, "APPLIED")
    assert "CREATE TABLE" not in inspect.getsource(repository_module)


def test_aw03_schema_missing_or_checksum_drift_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    missing.write_bytes(b"")
    with pytest.raises(HostCommandRepositoryError) as absent:
        HostCommandRepository(missing)
    assert absent.value.reason_code == HOST_COMMAND_SCHEMA_NOT_READY

    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = ?",
            ("0" * 64, HOST_COMMAND_MIGRATION_ASSET_KEY),
        )
    with pytest.raises(HostCommandRepositoryError) as drifted:
        HostCommandRepository(db_path)
    assert drifted.value.reason_code == HOST_COMMAND_SCHEMA_NOT_READY


def test_aw03_admit_is_atomic_typed_and_exact_replay_is_read_only(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    repository = HostCommandRepository(db_path, clock=lambda: NOW)
    command, work_order, assignment, graph = _admission()

    created = repository.admit(command, work_order, assignment, graph)
    replayed = repository.admit(command, work_order, assignment, graph)
    by_graph = repository.get_by_graph(
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
    )

    assert created["created"] is True and created["replayed"] is False
    assert replayed["created"] is False and replayed["replayed"] is True
    assert isinstance(replayed["command"], HostCommand)
    assert isinstance(replayed["work_order"], WorkOrderV3)
    assert replayed["work_order"].root_work_order_id == work_order.work_order_id
    assert isinstance(replayed["assignment"], ExecutorAssignment)
    assert isinstance(replayed["graph"], TaskGraph)
    assert replayed["assignment_id"] == assignment.assignment_id
    assert by_graph is not None
    assert by_graph["command"].command_id == command.command_id
    assert by_graph["graph"].graph_id == graph.graph_id
    assert _counts(db_path) == {
        "host_command_journal": 1,
        "task_runtime_graph": 1,
        "task_runtime_node": 1,
        "task_runtime_outbox": 2,
    }
    with sqlite3.connect(str(db_path)) as conn:
        events = conn.execute(
            """
            SELECT aggregate_type, graph_revision, event_type
            FROM task_runtime_outbox ORDER BY aggregate_type
            """
        ).fetchall()
        durable_work_order = json.loads(
            conn.execute(
                "SELECT work_order_json FROM host_command_journal WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()[0]
        )
    assert events == [
        ("HOST_COMMAND", None, "HOST_COMMAND_ADMITTED"),
        ("TASK_GRAPH", 1, "TASK_GRAPH_CREATED"),
    ]
    assert durable_work_order["schema_version"] == "Natsume.agent.work_order.v3"


def test_aw03_work_order_v3_budget_graph_node_and_review_lineage_survives_restart(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    repository = HostCommandRepository(db_path, clock=lambda: NOW)
    command, legacy, assignment, graph = _admission()
    work_order = WorkOrderV3(
        work_order_id=legacy.work_order_id,
        domain=legacy.domain,
        run_mode=legacy.run_mode,
        objective=legacy.objective,
        workspace=dict(legacy.workspace),
        input_pack=dict(legacy.input_pack),
        allowed_actions=tuple(legacy.allowed_actions),
        forbidden_actions=tuple(legacy.forbidden_actions),
        acceptance_criteria=tuple(legacy.acceptance_criteria),
        verification_plan=dict(legacy.verification_plan),
        approval_policy=dict(legacy.approval_policy),
        review_policy={"review_required": True, "mode": "governed"},
        metadata={"lineage": "gr-03a"},
        root_work_order_id=legacy.work_order_id,
        task_graph_id=graph.graph_id,
        task_node_id=graph.nodes[0].node_id,
        graph_revision=graph.revision,
        attempt=2,
        capability_requirements=("host_chat", "artifact_write"),
        risk_level=3,
        input_refs=("host-input://request-aw03-1",),
        output_contract={"type": "host_reply", "version": 3},
        budget_policy=BudgetPolicy(
            scope="work-order-aw03-1",
            limits=BudgetAmount(token_count=4_000, tool_call_count=8, time_ms=60_000),
            on_exceeded=BudgetExceededAction.REVIEW,
            metadata={"owner": "host-command"},
        ),
        business_idempotency_scope=command.business_scope,
        operation_fingerprint=command.operation_fingerprint,
        status_version=7,
        approval_ref="approval://aw03/1",
        review_ref="review://aw03/1",
    )
    work_order.validate()

    admitted = repository.admit(command, work_order, assignment, graph)
    restarted = HostCommandRepository(db_path, clock=lambda: NOW).get(command.command_id)

    assert restarted is not None
    assert isinstance(admitted["work_order"], WorkOrderV3)
    assert isinstance(restarted["work_order"], WorkOrderV3)
    assert restarted["work_order"].to_dict() == work_order.to_dict()
    with sqlite3.connect(str(db_path)) as conn:
        durable = json.loads(
            conn.execute(
                "SELECT work_order_json FROM host_command_journal WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()[0]
        )
    assert durable["schema_version"] == "Natsume.agent.work_order.v3"
    assert durable["budget_policy"] == work_order.budget_policy.to_dict()
    assert durable["task_graph_id"] == graph.graph_id
    assert durable["task_node_id"] == graph.nodes[0].node_id
    assert durable["review_ref"] == "review://aw03/1"


def test_aw03_replay_payload_drift_fails_without_mutation(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    repository = HostCommandRepository(db_path, clock=lambda: NOW)
    original = _admission()
    repository.admit(*original)
    drifted = _admission(input_sha256=_sha256("different-input"))

    with pytest.raises(HostCommandRepositoryError) as error:
        repository.admit(*drifted)

    assert error.value.reason_code == HOST_COMMAND_CONFLICT
    assert _counts(db_path)["task_runtime_outbox"] == 2


@pytest.mark.parametrize("fault_stage", ["after_graph", "after_command", "after_outboxes", "after_ingress_claim"])
def test_aw03_any_admission_fault_rolls_back_graph_command_outboxes_and_claim(
    tmp_path: Path,
    fault_stage: str,
) -> None:
    db_path = _migrated_db(tmp_path)
    claim_ref = "secure-ingress-claim://aw03/fault"
    input_sha = _sha256("aw03-input")
    _insert_committed_claim(
        db_path,
        claim_ref=claim_ref,
        audit_ref="secure-ingress-audit://aw03/fault",
        request_id="request-aw03-fault",
        input_sha256=input_sha,
    )
    command, work_order, assignment, graph = _admission(
        surface=HostCommandSurface.HOST_INPUT,
        request_id="request-aw03-fault",
        ingress_claim_ref=claim_ref,
        ingress_audit_ref="secure-ingress-audit://aw03/fault",
    )

    def fault(stage: str) -> None:
        if stage == fault_stage:
            raise RuntimeError(f"fault:{stage}")

    repository = HostCommandRepository(db_path, clock=lambda: NOW, fault_injector=fault)
    with pytest.raises(RuntimeError, match=f"fault:{fault_stage}"):
        repository.admit(command, work_order, assignment, graph)

    assert _counts(db_path) == {
        "host_command_journal": 0,
        "task_runtime_graph": 0,
        "task_runtime_node": 0,
        "task_runtime_outbox": 0,
    }
    with sqlite3.connect(str(db_path)) as conn:
        result_ref = conn.execute(
            "SELECT result_ref FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
            (claim_ref,),
        ).fetchone()[0]
    assert result_ref is None


def test_aw03_ingress_claim_is_cas_bound_in_the_admission_transaction(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    claim_ref = "secure-ingress-claim://aw03/success"
    input_sha = _sha256("aw03-input")
    _insert_committed_claim(
        db_path,
        claim_ref=claim_ref,
        audit_ref="secure-ingress-audit://aw03/success",
        request_id="request-aw03-success",
        input_sha256=input_sha,
    )
    command, work_order, assignment, graph = _admission(
        surface=HostCommandSurface.HOST_INPUT,
        request_id="request-aw03-success",
        ingress_claim_ref=claim_ref,
        ingress_audit_ref="secure-ingress-audit://aw03/success",
    )
    repository = HostCommandRepository(db_path, clock=lambda: NOW)

    admitted = repository.admit(command, work_order, assignment, graph)
    replayed = repository.admit(command, work_order, assignment, graph)

    assert admitted["created"] is True
    assert replayed["replayed"] is True
    with sqlite3.connect(str(db_path)) as conn:
        result_ref = conn.execute(
            "SELECT result_ref FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
            (claim_ref,),
        ).fetchone()[0]
    assert result_ref == command.command_id


def test_aw03_ingress_binding_drift_fails_before_any_task_write(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    claim_ref = "secure-ingress-claim://aw03/drift"
    _insert_committed_claim(
        db_path,
        claim_ref=claim_ref,
        audit_ref="secure-ingress-audit://aw03/drift",
        request_id="request-aw03-drift",
        input_sha256=_sha256("different-wire-body"),
    )
    admission = _admission(
        surface=HostCommandSurface.HOST_INPUT,
        request_id="request-aw03-drift",
        ingress_claim_ref=claim_ref,
        ingress_audit_ref="secure-ingress-audit://aw03/drift",
    )
    repository = HostCommandRepository(db_path, clock=lambda: NOW)

    with pytest.raises(HostCommandRepositoryError) as error:
        repository.admit(*admission)

    assert error.value.reason_code == HOST_COMMAND_INGRESS_CONFLICT
    assert _counts(db_path)["task_runtime_graph"] == 0


def test_aw03_tampered_typed_json_fails_closed_on_read(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    repository = HostCommandRepository(db_path, clock=lambda: NOW)
    command, work_order, assignment, graph = _admission()
    repository.admit(command, work_order, assignment, graph)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE host_command_journal SET assignment_json = ? WHERE command_id = ?",
            ('{"schema_version":"tampered"}', command.command_id),
        )

    with pytest.raises(HostCommandRepositoryError) as error:
        repository.get(command.command_id)

    assert error.value.reason_code == HOST_COMMAND_DURABLE_DATA_INVALID


def test_tool_command_id_duplicate_requires_exact_nonworker_terminal_lineage(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    claim_ref = "secure-ingress-claim://aw03/tool/nonworker"
    audit_ref = "secure-ingress-audit://aw03/tool/nonworker"
    _insert_tool_claim(db_path, claim_ref=claim_ref, audit_ref=audit_ref)
    command, work_order, assignment, graph = _tool_admission(
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    repository = HostCommandRepository(db_path, clock=lambda: NOW)
    repository.admit(command, work_order, assignment, graph)
    _set_tool_terminal(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE secure_ingress_idempotency_claim
            SET result_ref = ?, updated_at_ms = ?
            WHERE claim_ref = ?
            """,
            (command.command_id, NOW + 2, claim_ref),
        )

    first = repository.validate_ingress_claim_result(command.command_id)
    duplicate = HostCommandRepository(
        db_path,
        clock=lambda: NOW + 3,
    ).validate_ingress_claim_result(command.command_id)

    assert first["command"].command_id == command.command_id
    assert duplicate["command"].command_id == command.command_id
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_worker_projection
            WHERE command_id = ?
            """,
            (command.command_id,),
        ).fetchone()[0] == 0


def test_tool_verifier_failure_replays_approve_projection_with_terminal_lineage(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    claim_ref = "secure-ingress-claim://aw03/tool/verifier-failed"
    audit_ref = "secure-ingress-audit://aw03/tool/verifier-failed"
    _insert_tool_claim(db_path, claim_ref=claim_ref, audit_ref=audit_ref)
    command, work_order, assignment, graph = _tool_admission(
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    repository = HostCommandRepository(db_path, clock=lambda: NOW)
    repository.admit(command, work_order, assignment, graph)
    attempt_id = "attempt://aw03/tool/verifier-failed"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms, acquired_ts_ms,
                started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
            ) VALUES (?, 'graph-aw03-tool', 1, 'node-aw03-tool', 1,
                      'FAILED', 3, 'worker-aw03-tool', 'host-tool:read_status',
                      ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                _sha256("aw03-tool-operation"),
                NOW,
                NOW,
                NOW,
                NOW + 1,
                NOW + 1,
                "verification://aw03/tool/failed",
            ),
        )
    projection_ref = "worker-projection://aw03/tool/verifier-failed"
    projection_body = {
        "phase": "COMMIT",
        "done": True,
        # Worker execution succeeded; the independent verifier failed later.
        "decision": "APPROVE",
        "reason_code": None,
        "command_id": command.command_id,
        "request_id": command.request_id,
        "narrative": "worker completed before verifier rejection",
        "route_type": "tool",
        "target_id": "read_status",
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "node_id": "node-aw03-tool",
        "attempt_id": attempt_id,
        "node_grant_ref": "governor-decision://aw03/tool/child",
        "audit_refs": {"verification_ref": "verification://aw03/tool/failed"},
    }
    projection = {
        **projection_body,
        "projection_id": projection_ref,
        "payload_digest": _sha256(
            json.dumps(
                projection_body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
    }
    WorkerProjectionStore(db_path, clock=lambda: NOW + 1).stage(
        projection_id=projection_ref,
        projection=projection,
    )
    _set_tool_terminal(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE secure_ingress_idempotency_claim
            SET result_ref = ?, updated_at_ms = ?
            WHERE claim_ref = ?
            """,
            (projection_ref, NOW + 2, claim_ref),
        )

    first = repository.validate_ingress_claim_result(command.command_id)
    duplicate = HostCommandRepository(
        db_path,
        clock=lambda: NOW + 3,
    ).validate_ingress_claim_result(command.command_id)

    assert first["command"].command_id == command.command_id
    assert duplicate["command"].command_id == command.command_id
    assert WorkerProjectionStore(db_path).get(projection_ref)["decision"] == "APPROVE"
