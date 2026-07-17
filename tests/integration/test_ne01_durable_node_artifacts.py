from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.contracts.agent_runtime.node_artifact import (
    artifact_digest,
    build_task_result_artifact_ref,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_NODE_ARTIFACT_RECONCILIATION_REQUIRED,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    build_outbox_payload_ref,
)
from host_brain.persistence.task_node_artifact_store import (
    TASK_NODE_ARTIFACT_MIGRATION_ASSET_KEY,
    TASK_NODE_ARTIFACT_MIGRATION_SHA256,
)
from host_brain.task_runtime.governed_workers import GovernedFrozenNodeWorker
from natsume_contracts.task import HostCommandSurface, build_host_command


NOW = 2_100_000_000_000
GRAPH_ID = "graph-node-artifact"
REVISION = 1
ROOT_NODE_ID = "node-root"
CHILD_NODE_ID = "node-child"
ATTEMPT_ID = "attempt-node-artifact-root"
RECEIPT_REF = "worker-projection://node-artifact/root/receipt"
EVIDENCE_REF = "acceptance://node-artifact/root"


def _migrated_db(tmp_path: Path, name: str = "node-artifact.sqlite3") -> Path:
    db_path = tmp_path / name
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"node-artifact-{name}",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    return db_path


def _event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    graph_revision: int | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        graph_revision=graph_revision,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            graph_revision=graph_revision,
        ),
        idempotency_key=(
            f"node-artifact:{aggregate_type}:{aggregate_id}:"
            f"{aggregate_version}:{event_type}"
        ),
        outbox_id=(
            f"outbox::node-artifact::{aggregate_type}::{aggregate_id}::"
            f"{aggregate_version}"
        ),
        evidence_refs=evidence_refs,
    )


def _output_contract(*, required: tuple[str, ...] = ("receipt_ref", "result")) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(required),
        "properties": {
            "receipt_ref": {"type": "string"},
            "result": {"type": "string"},
        },
    }


def _graph(
    *,
    graph_id: str = GRAPH_ID,
    revision: int = REVISION,
    root_status: TaskNodeStatus = TaskNodeStatus.VERIFIED,
    child_input_ref: str | None = None,
    root_contract: dict[str, Any] | None = None,
) -> TaskGraph:
    artifact_ref = child_input_ref or build_task_result_artifact_ref(
        graph_id,
        revision,
        ROOT_NODE_ID,
    )
    return TaskGraph(
        graph_id=graph_id,
        root_work_order_id=f"work-order::{graph_id}",
        revision=revision,
        planner_ref=f"planner://node-artifact/{graph_id}/{revision}",
        status=TaskGraphStatus.ACTIVE,
        created_at=NOW,
        result_node_id=CHILD_NODE_ID,
        nodes=(
            TaskNode(
                node_id=ROOT_NODE_ID,
                dependencies=(),
                route="chat:root",
                input_refs=("input://node-artifact/root",),
                output_contract=root_contract or _output_contract(),
                retry_policy={"max_attempts": 1},
                risk_level=1,
                acceptance_criteria=(),
                status=root_status,
                status_version=3 if root_status is TaskNodeStatus.VERIFIED else 4,
            ),
            TaskNode(
                node_id=CHILD_NODE_ID,
                dependencies=(ROOT_NODE_ID,),
                route="chat:child",
                input_refs=(artifact_ref,),
                output_contract=_output_contract(required=("receipt_ref",)),
                retry_policy={"max_attempts": 1},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.BLOCKED,
            ),
        ),
    )


def _seed_graph(
    store: DurableTaskStore,
    graph: TaskGraph,
    *,
    attempt_id: str = ATTEMPT_ID,
    receipt_ref: str = RECEIPT_REF,
) -> None:
    store.create_graph(
        graph,
        outbox=_event(
            "TASK_GRAPH",
            graph.graph_id,
            0,
            "TASK_GRAPH_CREATED",
            graph_revision=graph.revision,
        ),
    )
    if graph.nodes[0].status is TaskNodeStatus.ACCEPTED:
        return
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms, acquired_ts_ms,
                started_ts_ms, completed_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, 1, 'SUCCEEDED', 3, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                graph.graph_id,
                graph.revision,
                ROOT_NODE_ID,
                "worker-node-artifact",
                "scope-node-artifact",
                "sha256:" + "a" * 64,
                NOW,
                NOW,
                NOW,
                NOW + 1,
                NOW + 1,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_lease(
                lease_id, attempt_id, owner_id, fencing_token,
                acquired_ts_ms, heartbeat_ts_ms, expires_ts_ms,
                released_ts_ms, status, status_version
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'RELEASED', 1)
            """,
            (
                f"lease::{attempt_id}",
                attempt_id,
                "worker-node-artifact",
                NOW,
                NOW,
                NOW + 60_000,
                NOW + 1,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_idempotency_reservation(
                reservation_id, business_scope, operation_fingerprint,
                attempt_id, lease_id, owner_id, fencing_token, expires_ts_ms,
                status, status_version, receipt_ref, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'COMMITTED', 1, ?, ?, ?)
            """,
            (
                f"reservation::{attempt_id}",
                "scope-node-artifact",
                "sha256:" + "a" * 64,
                attempt_id,
                f"lease::{attempt_id}",
                "worker-node-artifact",
                NOW + 60_000,
                receipt_ref,
                NOW,
                NOW + 1,
            ),
        )
        conn.commit()


def _acceptance(graph: TaskGraph, *, attempt_id: str = ATTEMPT_ID) -> dict[str, Any]:
    return {
        "schema_version": "Natsume.agent.acceptance_judgement.v1",
        "judgement_id": f"accept::{attempt_id}",
        "work_order_id": graph.root_work_order_id,
        "task_graph_id": graph.graph_id,
        "task_node_id": ROOT_NODE_ID,
        "attempt_id": attempt_id,
        "decision": "ACCEPT",
        "reason_code": None,
        "evidence_refs": [EVIDENCE_REF],
        "criteria": [],
        "critic_candidate": None,
        "started_at_ms": NOW,
        "finished_at_ms": NOW + 1,
    }


def _accept_outbox(graph: TaskGraph) -> OutboxMessage:
    return _event(
        "TASK_NODE",
        f"{graph.graph_id}@{graph.revision}/{ROOT_NODE_ID}",
        4,
        "TASK_NODE_ACCEPTED",
        evidence_refs=(EVIDENCE_REF,),
    )


def _commit_artifact(
    store: DurableTaskStore,
    graph: TaskGraph,
    *,
    worker_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return store.transition_node_to_accepted_with_artifact(
        graph.graph_id,
        graph.revision,
        ROOT_NODE_ID,
        expected_version=3,
        outbox=_accept_outbox(graph),
        acceptance_judgement=_acceptance(graph),
        attempt_id=ATTEMPT_ID,
        receipt_ref=RECEIPT_REF,
        worker_output=worker_output or {"result": "root-result"},
    )


def test_0102_migration_asset_and_physical_schema_are_checksum_bound(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path, "node-artifact-schema.sqlite3")
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "audit"
        / "migrations"
        / "0102_task_runtime_node_artifact.sql"
    )
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        TASK_NODE_ARTIFACT_MIGRATION_SHA256
    )
    with sqlite3.connect(db_path) as conn:
        asset = conn.execute(
            """
            SELECT checksum FROM schema_migration_assets
            WHERE asset_key = ?
            """,
            (TASK_NODE_ARTIFACT_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert asset == (TASK_NODE_ARTIFACT_MIGRATION_SHA256,)
    DurableTaskStore(db_path, clock=lambda: NOW).assert_node_artifact_ready()


def test_node_acceptance_and_artifact_are_atomic_and_exactly_replayable(
    tmp_path: Path,
) -> None:
    store = DurableTaskStore(_migrated_db(tmp_path), clock=lambda: NOW)
    graph = _graph()
    _seed_graph(store, graph)

    first = _commit_artifact(store, graph)
    replay = _commit_artifact(store, graph)

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["artifact"] == first["artifact"]
    assert first["node"]["status"] == "ACCEPTED"
    assert first["artifact"]["payload"] == {
        "receipt_ref": RECEIPT_REF,
        "result": "root-result",
    }
    assert store.resolve_node_input_artifacts(
        graph.graph_id,
        graph.revision,
        CHILD_NODE_ID,
    ) == [first["artifact"]]

    with pytest.raises(TaskStoreError) as drift:
        _commit_artifact(store, graph, worker_output={"result": "different"})
    assert drift.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    assert store.get_node(graph.graph_id, graph.revision, ROOT_NODE_ID)["status"] == (
        "ACCEPTED"
    )


def test_output_contract_failure_rolls_back_node_and_artifact(tmp_path: Path) -> None:
    store = DurableTaskStore(_migrated_db(tmp_path), clock=lambda: NOW)
    graph = _graph(root_contract=_output_contract(required=("receipt_ref", "missing")))
    _seed_graph(store, graph)

    with pytest.raises(TaskStoreError) as invalid:
        _commit_artifact(store, graph)

    assert invalid.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    assert store.get_node(graph.graph_id, graph.revision, ROOT_NODE_ID)["status"] == (
        "VERIFIED"
    )
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_node_artifact"
        ).fetchone()[0] == 0


def test_artifact_insert_conflict_rolls_back_accepted_transition(tmp_path: Path) -> None:
    store = DurableTaskStore(_migrated_db(tmp_path), clock=lambda: NOW)
    graph = _graph()
    _seed_graph(store, graph)
    contract = _output_contract()
    payload = {"receipt_ref": RECEIPT_REF, "result": "preexisting"}
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO task_runtime_node_artifact(
                artifact_ref, schema_version, graph_id, graph_revision,
                node_id, attempt_id, receipt_ref, output_contract_json,
                output_contract_sha256, payload_json, payload_sha256,
                created_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                build_task_result_artifact_ref(
                    graph.graph_id,
                    graph.revision,
                    ROOT_NODE_ID,
                ),
                "Natsume.task_runtime.node_artifact.v1",
                graph.graph_id,
                graph.revision,
                ROOT_NODE_ID,
                ATTEMPT_ID,
                RECEIPT_REF,
                json.dumps(contract, sort_keys=True, separators=(",", ":")),
                artifact_digest(contract),
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                artifact_digest(payload),
                NOW,
            ),
        )
        conn.commit()

    with pytest.raises(TaskStoreError) as conflict:
        _commit_artifact(store, graph)

    assert conflict.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    assert store.get_node(graph.graph_id, graph.revision, ROOT_NODE_ID)["status"] == (
        "VERIFIED"
    )


class _LostAcceptanceResponseStore(DurableTaskStore):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lose_once = True

    def transition_node_to_accepted_with_artifact(self, *args: Any, **kwargs: Any):
        result = super().transition_node_to_accepted_with_artifact(*args, **kwargs)
        if self.lose_once:
            self.lose_once = False
            raise ConnectionError("simulated response loss after COMMIT")
        return result


def test_lost_acceptance_response_replays_exact_artifact_without_second_write(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    store = _LostAcceptanceResponseStore(db_path, clock=lambda: NOW)
    graph = _graph()
    _seed_graph(store, graph)

    with pytest.raises(ConnectionError, match="after COMMIT"):
        _commit_artifact(store, graph)
    replay = _commit_artifact(store, graph)

    assert replay["replayed"] is True
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_node_artifact"
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'TASK_NODE_ACCEPTED'
            """
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("column", "replacement"),
    (
        ("payload_json", json.dumps({"receipt_ref": RECEIPT_REF, "result": "tampered"})),
        ("output_contract_json", json.dumps({"type": "object", "required": []})),
        ("receipt_ref", "worker-projection://node-artifact/tampered/receipt"),
    ),
)
def test_artifact_payload_contract_and_receipt_tampering_fail_closed(
    tmp_path: Path,
    column: str,
    replacement: str,
) -> None:
    store = DurableTaskStore(_migrated_db(tmp_path, f"tamper-{column}.sqlite3"), clock=lambda: NOW)
    graph = _graph()
    _seed_graph(store, graph)
    _commit_artifact(store, graph)

    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"UPDATE task_runtime_node_artifact SET {column} = ?",
                (replacement,),
            )
        conn.execute("DROP TRIGGER trg_task_runtime_node_artifact_immutable_update")
        conn.execute(
            f"UPDATE task_runtime_node_artifact SET {column} = ?",
            (replacement,),
        )
        conn.execute(
            """
            CREATE TRIGGER trg_task_runtime_node_artifact_immutable_update
            BEFORE UPDATE ON task_runtime_node_artifact
            BEGIN
                SELECT RAISE(ABORT, 'task runtime node artifact is immutable');
            END
            """
        )
        conn.commit()

    with pytest.raises(TaskStoreError) as tampered:
        store.get_node_artifact(graph.graph_id, graph.revision, ROOT_NODE_ID)
    assert tampered.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


@pytest.mark.parametrize(
    "wrong_ref",
    (
        build_task_result_artifact_ref("different-graph", REVISION, ROOT_NODE_ID),
        build_task_result_artifact_ref(GRAPH_ID, REVISION + 1, ROOT_NODE_ID),
    ),
)
def test_cross_graph_or_revision_result_ref_is_rejected(
    tmp_path: Path,
    wrong_ref: str,
) -> None:
    store = DurableTaskStore(_migrated_db(tmp_path), clock=lambda: NOW)
    graph = _graph(child_input_ref=wrong_ref)
    _seed_graph(store, graph)

    with pytest.raises(TaskStoreError) as invalid:
        store.resolve_node_input_artifacts(
            graph.graph_id,
            graph.revision,
            CHILD_NODE_ID,
        )
    assert invalid.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_upgraded_active_accepted_predecessor_without_artifact_is_quarantined(
    tmp_path: Path,
) -> None:
    store = DurableTaskStore(_migrated_db(tmp_path), clock=lambda: NOW)
    graph = _graph(root_status=TaskNodeStatus.ACCEPTED)
    _seed_graph(store, graph)

    with pytest.raises(TaskStoreError) as legacy:
        store.assert_node_artifact_ready()
    assert (
        legacy.value.reason_code
        == TASK_STORE_NODE_ARTIFACT_RECONCILIATION_REQUIRED
    )
    assert store.get_node(graph.graph_id, graph.revision, CHILD_NODE_ID)["status"] == (
        "BLOCKED"
    )


def test_schema_readiness_rejects_missing_immutable_trigger(tmp_path: Path) -> None:
    store = DurableTaskStore(_migrated_db(tmp_path), clock=lambda: NOW)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TRIGGER trg_task_runtime_node_artifact_immutable_delete")
        conn.commit()

    with pytest.raises(TaskStoreError) as stale:
        store.assert_node_artifact_ready()
    assert stale.value.reason_code == TASK_STORE_SCHEMA_NOT_READY


def test_schema_readiness_is_cached_and_force_revalidate_detects_drift(
    tmp_path: Path,
) -> None:
    store = DurableTaskStore(_migrated_db(tmp_path), clock=lambda: NOW)
    store.assert_node_artifact_ready()

    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TRIGGER trg_task_runtime_node_artifact_immutable_delete")
        conn.commit()

    # Normal acceptance/resolution paths reuse the construction-scoped READY
    # mode instead of rescanning every active graph for every node.
    store.assert_node_artifact_ready()
    with pytest.raises(TaskStoreError) as stale:
        store.assert_node_artifact_ready(force_revalidate=True)
    assert stale.value.reason_code == TASK_STORE_SCHEMA_NOT_READY


class _GrantVerifier:
    def verify(self, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)


class _ProjectionStore:
    def stage(self, *, projection_id: str, projection: dict[str, Any]) -> dict[str, Any]:
        return dict(projection)


class _ArtifactExecutor:
    def __init__(self) -> None:
        self.seen: tuple[dict[str, Any], ...] = ()

    async def execute_stream(self, frozen: Any):
        self.seen = tuple(dict(item) for item in frozen.input_artifacts)
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "child consumed root artifact",
            "route_type": "chat",
            "target_id": "child",
        }


@pytest.mark.asyncio
async def test_frozen_successor_worker_receives_resolved_durable_artifact(
    tmp_path: Path,
) -> None:
    store = DurableTaskStore(_migrated_db(tmp_path), clock=lambda: NOW)
    graph = _graph()
    _seed_graph(store, graph)
    committed = _commit_artifact(store, graph)
    artifacts = store.resolve_node_input_artifacts(
        graph.graph_id,
        graph.revision,
        CHILD_NODE_ID,
    )
    artifact_ref = committed["artifact"]["artifact_ref"]
    order = WorkOrderV3(
        work_order_id=graph.root_work_order_id,
        domain="chat",
        run_mode="worker",
        objective="consume predecessor output",
        input_pack={},
        metadata={
            "planning_lineage": {
                "node_bindings": [
                    {
                        "node_id": CHILD_NODE_ID,
                        "worker_route": "chat",
                        "target_id": "child",
                        "objective": "consume predecessor output",
                    }
                ]
            }
        },
        root_work_order_id=graph.root_work_order_id,
        task_graph_id=graph.graph_id,
        task_node_id=CHILD_NODE_ID,
        graph_revision=graph.revision,
        budget_policy=BudgetPolicy(
            scope="artifact-worker-test",
            limits=BudgetAmount(time_ms=1_000, concurrency=1),
        ),
        business_idempotency_scope="artifact-worker-test",
        operation_fingerprint="sha256:" + "b" * 64,
        approval_ref="governor-grant://artifact/parent",
        review_ref="review-waiver://artifact/child",
    )
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-artifact-child",
        session_id="session-artifact-child",
        principal_id="principal-artifact-child",
        input_ref="input://sha256/" + "1" * 64,
        input_sha256="sha256:" + "1" * 64,
        governor_grant_ref=order.approval_ref,
        root_work_order_id=graph.root_work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        route_type="chat",
        target_id="child",
        business_scope=order.business_idempotency_scope,
        operation_fingerprint=order.operation_fingerprint,
        created_at_ms=NOW,
    )
    executor = _ArtifactExecutor()
    worker = GovernedFrozenNodeWorker(
        route_kind="chat",
        executor=executor,
        node_grants=_GrantVerifier(),
        projections=_ProjectionStore(),
    )
    events = [
        event
        async for event in worker.execute_stream(
            command=command,
            work_order=order,
            execution_context={
                "command_id": command.command_id,
                "work_order_id": order.work_order_id,
                "graph_id": graph.graph_id,
                "graph_revision": graph.revision,
                "node_id": CHILD_NODE_ID,
                "attempt_id": "attempt-artifact-child",
                "lease_id": "lease-artifact-child",
                "lease_owner_id": "worker-artifact-child",
                "fencing_token": 1,
                "worker_route": "chat:child",
                "node_input_refs": [artifact_ref],
                "node_input_artifacts": artifacts,
                "node_grant_ref": "governor-grant://artifact/child",
            },
        )
    ]

    assert events[-1]["decision"] == "APPROVE"
    assert executor.seen[0]["payload"]["result"] == "root-result"
    assert executor.seen[0]["payload_sha256"] == artifact_digest(
        executor.seen[0]["payload"]
    )
