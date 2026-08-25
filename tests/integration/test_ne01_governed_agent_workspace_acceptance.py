from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Any

import pytest

import host_brain.agent_runtime.executor as executor_module
from host_brain.agent_runtime.accepted_workspace_artifact import (
    AcceptedAgentWorkspaceArtifactHandler,
)
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.executor import execute_agent_runtime_payload
from host_brain.agent_runtime.internal_artifact_terminal_reconciliation import (
    InternalArtifactTerminalReconciler,
)
from host_brain.agent_runtime.skill_artifact_terminal_reconciliation import (
    AgentArtifactTerminalReconcilerGroup,
    GovernedSkillArtifactTerminalReconciler,
)
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.workspace_allocator import (
    ManagedGitSource,
    ManagedWorkspaceRoot,
    WorkspaceAllocator,
)
from host_brain.agent_runtime.workspace_artifact_effect import (
    AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
    AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA,
    canonical_workspace_artifact_digest,
)
from host_brain.agent_runtime.workspace_runtime import DurableWorkspaceRuntime
from host_brain.app.governed_task_adapter import _build_contracts
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.verification_contract import (
    VerificationResult,
    VerificationStatus,
)
from host_brain.persistence.host_command_repository import (
    HostCommandRepository,
)
from host_brain.persistence.internal_agent_checkpoint_uow import (
    DurableInternalAgentCheckpointUnitOfWork,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.secure_ingress_repository import (
    SQLiteSecureIngressStore,
)
from host_brain.persistence.task_acceptance_effect_staging_authority import (
    canonical_task_acceptance_effect_staging_scope,
)
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    OutboxMessage,
    build_outbox_payload_ref,
)
from host_brain.persistence.task_store_types import AttemptStatus
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_SCHEMA_VERSION,
    WorkerProjectionStore,
)
from host_brain.persistence.workspace_repository import WorkspaceRepository
from host_brain.contracts.runtime_context import WorldFactV2, WorldSnapshotV2
from host_brain.secure_ingress.graph_cancellation_authority import (
    SecureIngressGraphCancellationAuthorityVerifier,
)
from host_brain.orchestration import GovernedCommandApplication
from host_brain.task_runtime import GovernedTaskCoordinator
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)
from host_brain.task_runtime.acceptance_effects import (
    compile_graph_acceptance_effects,
)
from host_brain.task_runtime.review_runtime import (
    TaskNodeReviewContext,
    TaskNodeReviewRuntime,
)
from tests.integration.test_aw02_internal_agent_runtime_cutover import (
    _commit_graph_cancellation_authority,
)
from tests.integration.test_gr03a_governed_durable_dag import (
    _Publisher,
    _Verifier,
)


NOW = 2_200_000_000_000
ATTEMPT_ID = "attempt-governed-agent-workspace"
TASK_LEASE_ID = "lease-governed-agent-workspace"
RUNTIME_OWNER = "worker-governed-agent-workspace"
RESERVATION_ID = "reservation-governed-agent-workspace"
VERIFICATION_REF = "verification://governed-agent-workspace"


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return str(completed.stdout or "").strip()


def _source_repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "agent-acceptance@example.invalid")
    _git(source, "config", "user.name", "Agent Acceptance Test")
    (source / "src").mkdir()
    (source / "src" / "baseline.txt").write_text(
        "baseline\n",
        encoding="utf-8",
    )
    _git(source, "add", "src/baseline.txt")
    _git(source, "commit", "-m", "initial")
    return source.resolve(), _git(source, "rev-parse", "HEAD")


def _runtime(
    tmp_path: Path,
    *,
    db_path: Path,
    source: Path,
    now_ms: int = NOW,
    runtime_owner_id: str = RUNTIME_OWNER,
) -> DurableWorkspaceRuntime:
    managed = tmp_path / "managed"
    managed.mkdir(exist_ok=True)
    repository = WorkspaceRepository(db_path, clock=lambda: now_ms)
    allocator = WorkspaceAllocator(
        repository=repository,
        managed_roots={
            "worktrees": ManagedWorkspaceRoot(
                root_ref="worktrees",
                path=managed,
                isolation_modes=frozenset({"DIRECTORY_FENCE"}),
            )
        },
        git_sources={
            "runtime-repository": ManagedGitSource(
                source_key="runtime-repository",
                source_ref_prefix="git-worktree://runtime-repository",
                repository=source,
            )
        },
    )
    return DurableWorkspaceRuntime(
        repository=repository,
        allocator=allocator,
        runtime_owner_id=runtime_owner_id,
        managed_root_ref="worktrees",
        git_source_key="runtime-repository",
        lease_ms=5_000,
        heartbeat_interval_ms=1_000,
        token_secret=b"g" * 48,
    )


def _contracts():
    fact = WorldFactV2(
        workspace_id="workspace-governed-agent-context",
        subject_id="person-governed-agent-context",
        key="project.mode",
        value="implementation",
        source_ref="world-observation://governed-agent/1",
        confidence=1.0,
        observed_at_ms=NOW,
        expires_at_ms=NOW + 60_000,
    )
    snapshot = WorldSnapshotV2(
        workspace_id=fact.workspace_id,
        subject_id=fact.subject_id,
        revision=1,
        facts=(fact,),
    )
    intent = {
        "id": "intent-governed-agent-workspace",
        "route": "skill",
        "target_id": "agent_worker",
        "task": "write one accepted generated source file",
        "args": {"task": "write one accepted generated source file"},
    }
    return _build_contracts(
        request_body={},
        request_id="request-governed-agent-workspace",
        session_id="session-governed-agent-workspace",
        ts_ms=NOW,
        intent_draft=intent,
        governor_result={
            "request_id": "request-governed-agent-workspace",
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "tokens": {},
            "ts_ms": NOW,
        },
        person_id=fact.subject_id,
        sanitized_message="write one accepted generated source file",
        emotion_context={},
        render_target_lang="zh",
        command_authority=None,
        frozen_context={
            "runtime_context_schema": "natsume.runtime-context.request.v1",
            "workspace_id": fact.workspace_id,
            "subject_id": fact.subject_id,
            "world_snapshot_v2": snapshot.to_dict(),
            "scene_identity_v2": {
                "schema": "natsume.scene-identity.v2",
                "workspace_id": fact.workspace_id,
                "subject_id": fact.subject_id,
                "scene_id": "scene.implementation",
                "revision": 1,
                "decision_digest": "d" * 64,
            },
        },
        skill_risk_level=4,
        skill_requires_durable_review=True,
    )


def _event(
    *,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    graph_revision: int | None,
    attempt_id: str | None,
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
            f"governed-agent:{aggregate_type}:{aggregate_id}:"
            f"{aggregate_version}:{event_type}"
        ),
        attempt_id=attempt_id,
        outbox_id=(
            f"outbox::governed-agent::{aggregate_type}::{aggregate_id}::"
            f"{aggregate_version}"
        ),
        evidence_refs=evidence_refs,
    )


def _inject_terminal_acceptance_effect(
    task_store: DurableTaskStore,
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
) -> str:
    """Model a pre-existing Effect row that must block Workspace abandonment."""

    effect_id = "acceptance-effect://governed-agent/terminal-drift"
    idempotency_key = "governed-agent:terminal-drift"
    payload_json = json.dumps(
        {"content": "must block terminal Workspace abandonment"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload_sha256 = (
        "sha256:" + hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    )
    with task_store._transaction() as conn:
        with canonical_task_acceptance_effect_staging_scope(
            effect_id=effect_id,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            commit_scope="NODE",
            idempotency_key=idempotency_key,
        ):
            conn.execute(
                """
                INSERT INTO task_runtime_acceptance_effect_intent(
                    effect_id, graph_id, graph_revision, node_id, attempt_id,
                    commit_scope, effect_type, payload_ref, payload_json,
                    payload_sha256, idempotency_key, status, available_ts_ms,
                    fencing_token, attempt_count, status_version,
                    created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, 'NODE',
                          'conversation.assistant_turn', ?, ?, ?, ?,
                          'PENDING', ?, 0, 0, 0, ?, ?)
                """,
                (
                    effect_id,
                    graph_id,
                    graph_revision,
                    node_id,
                    attempt_id,
                    "effect-payload://governed-agent/terminal-drift",
                    payload_json,
                    payload_sha256,
                    idempotency_key,
                    NOW,
                    NOW,
                    NOW,
                ),
            )
    return effect_id


def _projection(
    *,
    command,
    graph,
    node,
    attempt_id: str,
    protected: dict[str, Any],
    result_digest: str,
    node_grant_ref: str,
    node_grant_digest: str,
) -> dict[str, Any]:
    body = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "reason_code": None,
        "command_id": command.command_id,
        "request_id": command.request_id,
        "narrative": "accepted governed source change",
        "route_type": "skill",
        "target_id": command.target_id,
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "node_id": node.node_id,
        "attempt_id": attempt_id,
        "node_grant_ref": node_grant_ref,
        "audit_refs": {
            "path": "governed_task_runtime:frozen_skill",
            "command_id": command.command_id,
            "attempt_id": attempt_id,
            "node_grant_ref": node_grant_ref,
            "node_grant_digest": node_grant_digest,
        },
        "acceptance_effect_intents": [
            {
                "effect_type": AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
                "commit_scope": "NODE",
                "payload": {
                    "schema_version": (
                        AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA
                    ),
                    "work_order_id": command.root_work_order_id,
                    "target_id": command.target_id,
                    "operation_fingerprint": (
                        command.operation_fingerprint
                    ),
                    "protected_artifact": protected,
                    "result_digest": result_digest,
                },
            }
        ],
    }
    payload_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **body,
        "projection_id": (
            "worker-projection://skill/"
            + payload_digest.split(":", 1)[1]
        ),
        "payload_digest": payload_digest,
        "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
    }


def _rejected_projection(
    *,
    command,
    graph,
    node,
    attempt_id: str,
    protected: dict[str, Any],
    result_digest: str,
    node_grant_ref: str,
    node_grant_digest: str,
) -> dict[str, Any]:
    body = {
        "phase": "COMMIT",
        "done": True,
        "decision": "REJECT",
        "reason_code": "AGENT_EXECUTION_FAILED",
        "command_id": command.command_id,
        "request_id": command.request_id,
        "narrative": "governed Agent execution failed",
        "route_type": "skill",
        "target_id": command.target_id,
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "node_id": node.node_id,
        "attempt_id": attempt_id,
        "node_grant_ref": node_grant_ref,
        "audit_refs": {
            "path": "governed_task_runtime:frozen_skill",
            "command_id": command.command_id,
            "attempt_id": attempt_id,
            "node_grant_ref": node_grant_ref,
            "node_grant_digest": node_grant_digest,
            "terminal_workspace_artifact": protected,
            "terminal_workspace_result_digest": result_digest,
        },
    }
    payload_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **body,
        "projection_id": (
            "worker-projection://skill/"
            + payload_digest.split(":", 1)[1]
        ),
        "payload_digest": payload_digest,
        "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
    }


class _Verification:
    def run(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"status": "PASS", "receipt_ref": VERIFICATION_REF}


class _ActionPreview:
    @staticmethod
    def issue_receipt(**_kwargs: Any) -> dict[str, Any]:
        return {
            "event_id": "runtimeevent://governed-agent-workspace-preview",
            "receipt_ref": "action-preview://governed-agent-workspace",
            "receipt": {"authority_kind": "ACCEPTANCE_EFFECT"},
        }

    @staticmethod
    def require_execution(**_kwargs: Any) -> dict[str, Any]:
        return {"decision": "ALLOW"}


def _seed_active_attempt(
    *,
    db_path: Path,
    graph,
    node,
) -> None:
    running_node = replace(
        node,
        status=TaskNodeStatus.RUNNING,
        status_version=2,
    )
    active_graph = replace(
        graph,
        status=TaskGraphStatus.ACTIVE,
        nodes=(running_node,),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            UPDATE task_runtime_graph
            SET status = 'ACTIVE', status_version = 1, graph_json = ?,
                updated_ts_ms = ?
            WHERE graph_id = ? AND revision = ?
            """,
            (
                json.dumps(
                    active_graph.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                NOW,
                graph.graph_id,
                graph.revision,
            ),
        )
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = 'RUNNING', status_version = 2, node_json = ?,
                updated_ts_ms = ?
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
            """,
            (
                json.dumps(
                    running_node.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                NOW,
                graph.graph_id,
                graph.revision,
                node.node_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms, acquired_ts_ms,
                started_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, 1, 'RUNNING', 2, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ATTEMPT_ID,
                graph.graph_id,
                graph.revision,
                node.node_id,
                RUNTIME_OWNER,
                "host-skill:agent_worker",
                "sha256:" + "e" * 64,
                NOW,
                NOW,
                NOW,
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_lease(
                lease_id, attempt_id, owner_id, fencing_token,
                acquired_ts_ms, heartbeat_ts_ms, expires_ts_ms,
                status, status_version
            ) VALUES (?, ?, ?, 7, ?, ?, ?, 'ACTIVE', 0)
            """,
            (
                TASK_LEASE_ID,
                ATTEMPT_ID,
                RUNTIME_OWNER,
                NOW,
                NOW,
                NOW + 60_000,
            ),
        )
        conn.commit()


def _complete_attempt(
    *,
    db_path: Path,
    graph,
    node,
    receipt_ref: str,
) -> None:
    verified_node = replace(
        node,
        status=TaskNodeStatus.VERIFIED,
        status_version=3,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = 'VERIFIED', status_version = 3, node_json = ?,
                updated_ts_ms = ?
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
            """,
            (
                json.dumps(
                    verified_node.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                NOW + 1,
                graph.graph_id,
                graph.revision,
                node.node_id,
            ),
        )
        conn.execute(
            """
            UPDATE task_runtime_attempt
            SET status = 'SUCCEEDED', status_version = 3,
                completed_ts_ms = ?, updated_ts_ms = ?
            WHERE attempt_id = ?
            """,
            (NOW + 1, NOW + 1, ATTEMPT_ID),
        )
        conn.execute(
            """
            UPDATE task_runtime_lease
            SET status = 'RELEASED', status_version = 1, released_ts_ms = ?
            WHERE lease_id = ?
            """,
            (NOW + 1, TASK_LEASE_ID),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_idempotency_reservation(
                reservation_id, business_scope, operation_fingerprint,
                attempt_id, lease_id, owner_id, fencing_token, expires_ts_ms,
                status, status_version, receipt_ref, created_ts_ms,
                updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, 7, ?, 'COMMITTED', 1, ?, ?, ?)
            """,
            (
                RESERVATION_ID,
                "host-skill:agent_worker",
                "sha256:" + "e" * 64,
                ATTEMPT_ID,
                TASK_LEASE_ID,
                RUNTIME_OWNER,
                NOW + 60_000,
                receipt_ref,
                NOW,
                NOW + 1,
            ),
        )
        conn.commit()


def _start_public_attempt(
    *,
    task_store: DurableTaskStore,
    command,
    graph,
    node,
    attempt_id: str,
    reservation_id: str,
    budget_id: str,
    owner_id: str,
) -> dict[str, Any]:
    task_store.transition_graph(
        graph.graph_id,
        graph.revision,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event(
            aggregate_type="TASK_GRAPH",
            aggregate_id=graph.graph_id,
            aggregate_version=1,
            event_type="TASK_GRAPH_ACTIVE",
            graph_revision=graph.revision,
            attempt_id=None,
        ),
    )
    task_store.create_budget_reservation(
        BudgetReservation(
            budget_id=budget_id,
            scope=command.business_scope,
            limits=BudgetAmount(
                token_count=4_096,
                tool_call_count=8,
                time_ms=60_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=250_000,
            ),
            reserved=BudgetAmount(
                token_count=4_096,
                tool_call_count=8,
                time_ms=60_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=250_000,
            ),
            status=BudgetReservationStatus.ACTIVE,
        ),
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        outbox=_event(
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id=budget_id,
            aggregate_version=0,
            event_type="BUDGET_RESERVATION_CREATED",
            graph_revision=None,
            attempt_id=None,
        ),
    )
    claimed = task_store.claim_ready_node(
        attempt_id=attempt_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        node_id=node.node_id,
        expected_node_version=0,
        expected_attempt_count=0,
        owner_id=owner_id,
        lease_ttl_ms=60_000,
        budget_id=budget_id,
        operation_scope=command.business_scope,
        operation_fingerprint=command.operation_fingerprint,
        attempt_created_outbox=_event(
            aggregate_type="TASK_ATTEMPT",
            aggregate_id=attempt_id,
            aggregate_version=0,
            event_type="TASK_ATTEMPT_CREATED",
            graph_revision=None,
            attempt_id=attempt_id,
        ),
        attempt_leased_outbox=_event(
            aggregate_type="TASK_ATTEMPT",
            aggregate_id=attempt_id,
            aggregate_version=1,
            event_type="TASK_ATTEMPT_LEASED",
            graph_revision=None,
            attempt_id=attempt_id,
        ),
    )
    lease = dict(claimed["lease"])
    task_store.transition_attempt(
        attempt_id,
        owner_id=owner_id,
        fencing_token=int(lease["fencing_token"]),
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            aggregate_type="TASK_ATTEMPT",
            aggregate_id=attempt_id,
            aggregate_version=2,
            event_type="TASK_ATTEMPT_RUNNING",
            graph_revision=None,
            attempt_id=attempt_id,
        ),
    )
    task_store.transition_node(
        graph.graph_id,
        graph.revision,
        node.node_id,
        expected_version=1,
        target_status=TaskNodeStatus.RUNNING,
        outbox=_event(
            aggregate_type="TASK_NODE",
            aggregate_id=f"{graph.graph_id}@{graph.revision}/{node.node_id}",
            aggregate_version=2,
            event_type="TASK_NODE_RUNNING",
            graph_revision=None,
            attempt_id=None,
        ),
    )
    reserved = task_store.reserve_operation(
        reservation_id=reservation_id,
        business_scope=command.business_scope,
        operation_fingerprint=command.operation_fingerprint,
        attempt_id=attempt_id,
        lease_id=str(lease["lease_id"]),
        owner_id=owner_id,
        fencing_token=int(lease["fencing_token"]),
        ttl_ms=60_000,
        outbox=_event(
            aggregate_type="OPERATION_RESERVATION",
            aggregate_id=reservation_id,
            aggregate_version=0,
            event_type="OPERATION_RESERVED",
            graph_revision=None,
            attempt_id=attempt_id,
        ),
    )
    assert reserved.acquired is True
    return lease


@pytest.mark.asyncio
async def test_governed_agent_acceptance_promotes_real_sqlite_git_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, source_head = _source_repository(tmp_path)
    db_path = tmp_path / "governed-agent.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="governed-agent-migrations",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    task_store = DurableTaskStore(db_path)
    command_store = HostCommandRepository(db_path, task_store=task_store)
    projection_store = WorkerProjectionStore(db_path)
    command, order, graph = _contracts()
    node = graph.nodes[0]
    assignment = CanonicalRuntimeRouter().assign_new_task(
        order.work_order_id,
        assigned_at_ms=NOW,
    )
    admitted = command_store.admit(command, order, assignment, graph)
    assert admitted["created"] is True
    _seed_active_attempt(db_path=db_path, graph=graph, node=node)

    workspace_runtime = _runtime(
        tmp_path,
        db_path=db_path,
        source=source,
    )
    artifacts = AgentArtifactStore(tmp_path / "agent-runs")

    class _WritingAgent:
        def __init__(self, **_dependencies: Any) -> None:
            pass

        def execute(
            self,
            _order,
            *,
            workspace: Path,
            cancelled=None,
            model_budget_guard=None,
        ) -> AgentResult:
            assert callable(cancelled)
            assert not cancelled()
            assert model_budget_guard is not None
            assert model_budget_guard.graph_id == graph.graph_id
            assert model_budget_guard.node_id == node.node_id
            assert model_budget_guard.attempt_id == ATTEMPT_ID
            (workspace / "src" / "generated.txt").write_text(
                "accepted governed write\n",
                encoding="utf-8",
            )
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="generated one accepted file",
                result_text="generated one accepted file",
                verification={
                    "status": "PASS",
                    "receipt_ref": VERIFICATION_REF,
                },
                acceptance={"decision": "ACCEPT"},
                review_required=False,
            )

    monkeypatch.setattr(executor_module, "AgentExecutor", _WritingAgent)
    node_grant_ref = "governor-decision://sha256/" + "a" * 64
    node_grant_digest = "sha256:" + "b" * 64
    governed_context = {
        "execution_mode": "governed_task_runtime",
        "command_id": command.command_id,
        "request_id": command.request_id,
        "principal_id": command.principal_id,
        "target_id": command.target_id,
        "command_target_id": command.target_id,
        "work_order_id": order.work_order_id,
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "node_id": node.node_id,
        "attempt_id": ATTEMPT_ID,
        "lease_id": TASK_LEASE_ID,
        "lease_owner_id": RUNTIME_OWNER,
        "fencing_token": 7,
        "reservation_id": RESERVATION_ID,
        "node_grant_ref": node_grant_ref,
        "parent_grant_ref": command.governor_grant_ref,
        "business_scope": command.business_scope,
        "operation_fingerprint": command.operation_fingerprint,
        "node_risk_level": 4,
        "pre_execution_review_ref": (
            "review://review-governed-agent-workspace"
        ),
        "budget": {
            "budget_id": "budget-governed-agent-workspace",
            "status": "ACTIVE",
            "status_version": 1,
            "remaining": {
                "token_count": 4_096,
                "tool_call_count": 8,
                "time_ms": 60_000,
                "concurrency": 1,
                "retry_count": 1,
                "model_cost_micros": 250_000,
            },
        },
    }
    raw_order = {
        "schema_version": "Natsume.agent.work_order.v2",
        "work_order_id": order.work_order_id,
        "domain": "code",
        "run_mode": "worker",
        "objective": order.objective,
        "workspace": {"strategy": "runtime_repo"},
        "input_pack": {"user_request": order.objective},
        "allowed_actions": ["read_files", "write_files"],
        "forbidden_actions": ["modify_secret_files"],
        "acceptance_criteria": ["tests pass"],
        "verification_plan": {"commands": ["python -m pytest -q"]},
        "approval_policy": {"require_confirm": True, "confirmed": True},
        "review_policy": {"review_required": True},
        "metadata": {
            "skill_id": "agent_worker",
            "request_id": command.request_id,
        },
    }
    raw_result = execute_agent_runtime_payload(
        {"work_order": raw_order},
        model_attempt_service=object(),
        prompt_registry=object(),
        artifact_store=artifacts,
        workspace_runtime=workspace_runtime,
        governed_execution_context=governed_context,
    )
    assert raw_result["status"] == "EXECUTED", raw_result
    protected = dict(raw_result.pop("_protected_stage"))
    result_digest = canonical_workspace_artifact_digest(raw_result)
    projection = _projection(
        command=command,
        graph=graph,
        node=node,
        attempt_id=ATTEMPT_ID,
        protected=protected,
        result_digest=result_digest,
        node_grant_ref=node_grant_ref,
        node_grant_digest=node_grant_digest,
    )
    staged_projection = projection_store.stage(
        projection_id=projection["projection_id"],
        projection=projection,
    )
    assert staged_projection["projection_id"] == projection["projection_id"]
    _complete_attempt(
        db_path=db_path,
        graph=graph,
        node=node,
        receipt_ref=projection["projection_id"],
    )

    worker_commit = {
        **projection,
        "receipt_ref": projection["projection_id"],
    }
    node_acceptance_ref = "acceptance://governed-agent-workspace"
    criterion = order.acceptance_criteria[0]
    acceptance = {
        "schema_version": "Natsume.agent.acceptance_judgement.v1",
        "judgement_id": "acceptance-governed-agent-workspace",
        "work_order_id": order.work_order_id,
        "task_graph_id": graph.graph_id,
        "task_node_id": node.node_id,
        "attempt_id": ATTEMPT_ID,
        "decision": "ACCEPT",
        "reason_code": None,
        "evidence_refs": [node_acceptance_ref],
        "criteria": [
            {
                "criterion_id": "criterion-governed-agent-workspace",
                "criterion_text": criterion,
                "verdict": "PASS",
                "evidence_refs": [node_acceptance_ref],
                "deterministic": True,
                "reason_code": None,
                "critic_verdict": None,
            }
        ],
        "critic_candidate": None,
        "started_at_ms": NOW,
        "finished_at_ms": NOW + 1,
    }
    task_store.commit_node_acceptance(
        graph.graph_id,
        graph.revision,
        node.node_id,
        expected_version=3,
        outbox=_event(
            aggregate_type="TASK_NODE",
            aggregate_id=f"{graph.graph_id}@1/{node.node_id}",
            aggregate_version=4,
            event_type="TASK_NODE_ACCEPTED",
            graph_revision=None,
            attempt_id=ATTEMPT_ID,
            evidence_refs=(node_acceptance_ref,),
        ),
        acceptance_judgement=acceptance,
        attempt_id=ATTEMPT_ID,
        receipt_ref=projection["projection_id"],
        worker_output=worker_commit,
    )
    specs = compile_graph_acceptance_effects(
        command=command,
        work_order=order,
        node_id=node.node_id,
        attempt_id=ATTEMPT_ID,
        worker_commit=worker_commit,
    )
    completed = task_store.transition_graph_and_stage_acceptance_effects(
        graph.graph_id,
        graph.revision,
        expected_version=1,
        outbox=_event(
            aggregate_type="TASK_GRAPH",
            aggregate_id=graph.graph_id,
            aggregate_version=2,
            event_type="TASK_GRAPH_COMPLETED",
            graph_revision=graph.revision,
            attempt_id=None,
        ),
        effect_intents=specs,
    )
    assert completed["graph"]["status"] == "COMPLETED"
    artifact_effect = next(
        effect
        for effect in completed["effect_intents"]
        if effect["effect_type"] == AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE
    )
    handler = AcceptedAgentWorkspaceArtifactHandler(
        command_repository=command_store,
        projection_store=projection_store,
        artifact_store=artifacts,
        workspace_runtime=workspace_runtime,
        task_store=task_store,
    )
    dispatcher = AcceptanceEffectDispatcher(
        task_store=task_store,
        handlers={AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE: handler},
        owner_id="acceptance-owner-governed-agent",
        action_preview_service=_ActionPreview(),
    )
    dispatched = await dispatcher.dispatch_effect(
        artifact_effect["effect_id"]
    )

    assert dispatched is not None
    assert dispatched["status"] == "COMMITTED"
    assert str(dispatched["receipt_ref"]).startswith(
        "workspace-promotion://"
    )
    seal = artifacts.inspect_governed_stage(
        stage_ref=protected["ref"],
        seal_digest=protected["digest"],
    )
    lifecycle = workspace_runtime.repository.get_lifecycle(
        seal.workspace_id
    )
    assert lifecycle.state.value == "PROMOTED"
    assert lifecycle.accepted_event is not None
    assert lifecycle.accepted_event.base_commit == source_head
    assert lifecycle.accepted_event.accepted_head != source_head
    assert _git(source, "rev-parse", "main") == source_head
    assert not (source / "src" / "generated.txt").exists()

    restarted = _runtime(
        tmp_path,
        db_path=db_path,
        source=source,
        now_ms=NOW + 6_000,
    )
    restarted_lifecycle = restarted.repository.get_lifecycle(
        seal.workspace_id
    )
    assert restarted_lifecycle.state.value == "PROMOTED"
    assert (
        restarted_lifecycle.accepted_event.accepted_head
        == lifecycle.accepted_event.accepted_head
    )
    accepted = artifacts.inspect_governed_stage(
        stage_ref=protected["ref"],
        seal_digest=protected["digest"],
    )
    assert accepted.accepted_dir is not None

    terminal = InternalArtifactTerminalReconciler(
        artifact_store=artifacts,
        workspace_runtime=restarted,
        task_store=task_store,
        command_repository=command_store,
        projection_store=projection_store,
    )
    cleaned = terminal.run_once()
    assert any(
        item["workspace_id"] == seal.workspace_id
        and item["attempt_id"] == ATTEMPT_ID
        and item["action"] == "CLEANED"
        for item in cleaned
    ), cleaned
    cleaned_authority = restarted.repository.get(seal.workspace_id)
    assert cleaned_authority.status == "CLEANED"
    assert cleaned_authority.cleanup_receipt_ref.startswith(
        "workspace-cleanup://"
    )
    assert not (
        tmp_path / "managed" / cleaned_authority.workspace_relpath
    ).exists()

    replay = _runtime(
        tmp_path,
        db_path=db_path,
        source=source,
        now_ms=NOW + 12_000,
    )
    replayed = terminal.run_once()
    assert all(item["workspace_id"] != seal.workspace_id for item in replayed)
    assert replay.repository.get(seal.workspace_id).status == "CLEANED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_outcome", "inject_acceptance_effect"),
    (
        ("verification-failure", False),
        ("post-review-cancellation", False),
        ("post-review-cancellation", True),
    ),
)
async def test_nonaccepted_agent_skill_reconciles_after_real_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    terminal_outcome: str,
    inject_acceptance_effect: bool,
) -> None:
    attempt_id = "attempt-governed-agent-failed"
    reservation_id = "reservation-governed-agent-failed"
    budget_id = "budget-governed-agent-failed"
    owner_id = RUNTIME_OWNER
    source, source_head = _source_repository(tmp_path)
    db_path = tmp_path / "governed-agent-failed.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="governed-agent-failed-migrations",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    task_store = DurableTaskStore(db_path, clock=lambda: NOW)
    command_store = HostCommandRepository(db_path, task_store=task_store)
    projection_store = WorkerProjectionStore(db_path, clock=lambda: NOW)
    command, order, graph = _contracts()
    node = graph.nodes[0]
    assignment = CanonicalRuntimeRouter().assign_new_task(
        order.work_order_id,
        assigned_at_ms=NOW,
    )
    admitted = command_store.admit(command, order, assignment, graph)
    assert admitted["created"] is True
    lease = _start_public_attempt(
        task_store=task_store,
        command=command,
        graph=graph,
        node=node,
        attempt_id=attempt_id,
        reservation_id=reservation_id,
        budget_id=budget_id,
        owner_id=owner_id,
    )

    workspace_runtime = _runtime(
        tmp_path,
        db_path=db_path,
        source=source,
    )
    artifacts = AgentArtifactStore(tmp_path / "agent-runs")

    class _TerminalAgent:
        def __init__(self, **_dependencies: Any) -> None:
            pass

        def execute(
            self,
            _order,
            *,
            workspace: Path,
            cancelled=None,
            model_budget_guard=None,
        ) -> AgentResult:
            assert callable(cancelled)
            assert not cancelled()
            assert model_budget_guard is not None
            (workspace / "src" / "failed-generated.txt").write_text(
                "must never reach accepted HEAD\n",
                encoding="utf-8",
            )
            if terminal_outcome == "post-review-cancellation":
                return AgentResult(
                    status="EXECUTED",
                    work_order_id=order.work_order_id,
                    summary="governed write awaits acceptance review",
                    result_text="governed write awaits acceptance review",
                    verification={
                        "status": "PASS",
                        "receipt_ref": (
                            "verification://governed-agent/review"
                        ),
                    },
                    acceptance={"decision": "REVIEW"},
                    review_required=True,
                    reason_code="ACCEPTANCE_REVIEW_REQUIRED",
                )
            return AgentResult(
                status="FAILED",
                work_order_id=order.work_order_id,
                summary="governed write failed verification",
                result_text="governed write failed verification",
                verification={
                    "status": "FAIL",
                    "reason_code": "AGENT_EXECUTION_FAILED",
                },
                acceptance={"decision": "FAIL"},
                review_required=True,
                reason_code="AGENT_EXECUTION_FAILED",
            )

    monkeypatch.setattr(executor_module, "AgentExecutor", _TerminalAgent)
    node_grant_ref = "governor-decision://sha256/" + "d" * 64
    node_grant_digest = "sha256:" + "e" * 64
    governed_context = {
        "execution_mode": "governed_task_runtime",
        "command_id": command.command_id,
        "request_id": command.request_id,
        "principal_id": command.principal_id,
        "target_id": command.target_id,
        "command_target_id": command.target_id,
        "work_order_id": order.work_order_id,
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "node_id": node.node_id,
        "attempt_id": attempt_id,
        "lease_id": str(lease["lease_id"]),
        "lease_owner_id": owner_id,
        "fencing_token": int(lease["fencing_token"]),
        "reservation_id": reservation_id,
        "node_grant_ref": node_grant_ref,
        "parent_grant_ref": command.governor_grant_ref,
        "business_scope": command.business_scope,
        "operation_fingerprint": command.operation_fingerprint,
        "node_risk_level": 4,
        "pre_execution_review_ref": "review://review-governed-agent-failed",
        "budget": {
            "budget_id": budget_id,
            "status": "ACTIVE",
            "status_version": 0,
            "remaining": {
                "token_count": 4_096,
                "tool_call_count": 8,
                "time_ms": 60_000,
                "concurrency": 1,
                "retry_count": 1,
                "model_cost_micros": 250_000,
            },
        },
    }
    raw_order = {
        "schema_version": "Natsume.agent.work_order.v2",
        "work_order_id": order.work_order_id,
        "domain": "code",
        "run_mode": "worker",
        "objective": order.objective,
        "workspace": {"strategy": "runtime_repo"},
        "input_pack": {"user_request": order.objective},
        "allowed_actions": ["read_files", "write_files"],
        "forbidden_actions": ["modify_secret_files"],
        "acceptance_criteria": ["tests pass"],
        "verification_plan": {"commands": ["python -m pytest -q"]},
        "approval_policy": {"require_confirm": True, "confirmed": True},
        "review_policy": {"review_required": True},
        "metadata": {
            "skill_id": "agent_worker",
            "request_id": command.request_id,
        },
    }
    raw_result = execute_agent_runtime_payload(
        {"work_order": raw_order},
        model_attempt_service=object(),
        prompt_registry=object(),
        artifact_store=artifacts,
        workspace_runtime=workspace_runtime,
        governed_execution_context=governed_context,
    )
    protected = dict(raw_result.pop("_protected_stage"))
    result_digest = canonical_workspace_artifact_digest(raw_result)
    projection = (
        _rejected_projection(
            command=command,
            graph=graph,
            node=node,
            attempt_id=attempt_id,
            protected=protected,
            result_digest=result_digest,
            node_grant_ref=node_grant_ref,
            node_grant_digest=node_grant_digest,
        )
        if terminal_outcome == "verification-failure"
        else _projection(
            command=command,
            graph=graph,
            node=node,
            attempt_id=attempt_id,
            protected=protected,
            result_digest=result_digest,
            node_grant_ref=node_grant_ref,
            node_grant_digest=node_grant_digest,
        )
    )
    projection_store.stage(
        projection_id=projection["projection_id"],
        projection=projection,
    )
    task_store.commit_operation(
        reservation_id,
        attempt_id=attempt_id,
        lease_id=str(lease["lease_id"]),
        owner_id=owner_id,
        fencing_token=int(lease["fencing_token"]),
        expected_version=0,
        receipt_ref=projection["projection_id"],
        outbox=_event(
            aggregate_type="OPERATION_RESERVATION",
            aggregate_id=reservation_id,
            aggregate_version=1,
            event_type="OPERATION_COMMITTED",
            graph_revision=None,
            attempt_id=attempt_id,
            evidence_refs=(
                projection["projection_id"],
                (
                    "sha256://execution/"
                    + projection["projection_id"].rsplit("/", 1)[-1]
                ),
            ),
        ),
    )
    task_store.transition_attempt(
        attempt_id,
        owner_id=owner_id,
        fencing_token=int(lease["fencing_token"]),
        expected_version=2,
        target_status=AttemptStatus.SUCCEEDED,
        outbox=_event(
            aggregate_type="TASK_ATTEMPT",
            aggregate_id=attempt_id,
            aggregate_version=3,
            event_type="TASK_ATTEMPT_SUCCEEDED",
            graph_revision=None,
            attempt_id=attempt_id,
        ),
    )
    active_reports = AgentArtifactTerminalReconcilerGroup(
        GovernedSkillArtifactTerminalReconciler(
            artifact_store=artifacts,
            workspace_runtime=workspace_runtime,
            task_store=task_store,
            command_repository=command_store,
            projection_store=projection_store,
        ),
        InternalArtifactTerminalReconciler(
            artifact_store=artifacts,
            workspace_runtime=workspace_runtime,
            task_store=task_store,
            command_repository=command_store,
            projection_store=projection_store,
        ),
    ).run_once()
    assert any(item["action"] == "HOLD" for item in active_reports)
    assert all(item["action"] != "BLOCKED" for item in active_reports)
    for expected_version, target in (
        (2, TaskNodeStatus.EXECUTED),
        (3, TaskNodeStatus.VERIFYING),
    ):
        task_store.transition_node(
            graph.graph_id,
            graph.revision,
            node.node_id,
            expected_version=expected_version,
            target_status=target,
            outbox=_event(
                aggregate_type="TASK_NODE",
                aggregate_id=f"{graph.graph_id}@{graph.revision}/{node.node_id}",
                aggregate_version=expected_version + 1,
                event_type=f"TASK_NODE_{target.value}",
                graph_revision=None,
                attempt_id=None,
            ),
        )
    if terminal_outcome == "verification-failure":
        verification = VerificationResult(
            verification_id="verify-governed-agent-failed",
            work_order_id=order.work_order_id,
            required=True,
            status=VerificationStatus.FAIL,
            task_graph_id=graph.graph_id,
            task_node_id=node.node_id,
            attempt_id=attempt_id,
            reason_code="AGENT_EXECUTION_FAILED",
            evidence_refs=(projection["projection_id"],),
            checks=(
                {
                    "name": "durable-worker-projection",
                    "status": "FAIL",
                    "reason_code": "AGENT_EXECUTION_FAILED",
                },
            ),
            started_at_ms=NOW,
            finished_at_ms=NOW + 1,
        )
        failed = task_store.finalize_node_verification_failure(
            graph.graph_id,
            graph.revision,
            node.node_id,
            attempt_id=attempt_id,
            reservation_id=reservation_id,
            budget_id=budget_id,
            expected_node_version=4,
            expected_graph_version=1,
            expected_budget_version=0,
            verification_result=verification,
            node_outbox=_event(
                aggregate_type="TASK_NODE",
                aggregate_id=(
                    f"{graph.graph_id}@{graph.revision}/{node.node_id}"
                ),
                aggregate_version=5,
                event_type="TASK_NODE_VERIFICATION_FAILED",
                graph_revision=None,
                attempt_id=attempt_id,
                evidence_refs=(projection["projection_id"],),
            ),
            graph_outbox=_event(
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph.graph_id,
                aggregate_version=2,
                event_type="TASK_GRAPH_FAILED",
                graph_revision=graph.revision,
                attempt_id=None,
                evidence_refs=(projection["projection_id"],),
            ),
            budget_outbox=_event(
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget_id,
                aggregate_version=1,
                event_type="BUDGET_RESERVATION_RELEASED",
                graph_revision=None,
                attempt_id=None,
                evidence_refs=(projection["projection_id"],),
            ),
        )
        assert failed["graph"]["status"] == "FAILED"
    else:
        verification = VerificationResult(
            verification_id="verify-governed-agent-review",
            work_order_id=order.work_order_id,
            required=True,
            status=VerificationStatus.PASS,
            task_graph_id=graph.graph_id,
            task_node_id=node.node_id,
            attempt_id=attempt_id,
            reason_code=None,
            evidence_refs=(projection["projection_id"],),
            checks=(
                {
                    "name": "durable-worker-projection",
                    "status": "PASS",
                    "reason_code": None,
                },
            ),
            started_at_ms=NOW,
            finished_at_ms=NOW + 1,
        )
        verified = task_store.commit_node_verification(
            graph.graph_id,
            graph.revision,
            node.node_id,
            attempt_id=attempt_id,
            reservation_id=reservation_id,
            expected_node_version=4,
            target_status=TaskNodeStatus.VERIFIED,
            verification_result=verification,
            node_outbox=_event(
                aggregate_type="TASK_NODE",
                aggregate_id=(
                    f"{graph.graph_id}@{graph.revision}/{node.node_id}"
                ),
                aggregate_version=5,
                event_type="TASK_NODE_VERIFIED",
                graph_revision=None,
                attempt_id=attempt_id,
                evidence_refs=(projection["projection_id"],),
            ),
        )
        assert verified["node"]["status"] == "VERIFIED"
        reviews = ReviewStore(db_path, clock=lambda: NOW)
        review_runtime = TaskNodeReviewRuntime(reviews, clock=lambda: NOW)
        review_item = review_runtime.prepare_post_execution_acceptance_review(
            context=TaskNodeReviewContext(
                work_order_id=order.work_order_id,
                root_work_order_id=order.root_work_order_id,
                graph_id=graph.graph_id,
                graph_revision=graph.revision,
                node_id=node.node_id,
                node_status_version=6,
                attempt_id=attempt_id,
                requester=command.principal_id,
                reason=(
                    "Acceptance policy requires durable post-execution review"
                ),
                risk_level=4,
                evidence_refs=(projection["projection_id"],),
            )
        )
        criterion = node.acceptance_criteria[0]
        acceptance = {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": "accept-governed-agent-review",
            "work_order_id": order.work_order_id,
            "task_graph_id": graph.graph_id,
            "task_node_id": node.node_id,
            "attempt_id": attempt_id,
            "decision": "REVIEW",
            "reason_code": "ACCEPTANCE_REVIEW_REQUIRED",
            "evidence_refs": [projection["projection_id"]],
            "criteria": [
                {
                    "criterion_id": "criterion-governed-agent-review",
                    "criterion_text": criterion,
                    "verdict": "FAIL",
                    "evidence_refs": [projection["projection_id"]],
                    "deterministic": True,
                    "reason_code": "ACCEPTANCE_REVIEW_REQUIRED",
                    "critic_verdict": None,
                }
            ],
            "critic_candidate": None,
            "started_at_ms": NOW,
            "finished_at_ms": NOW + 1,
        }
        waiting = task_store.commit_node_acceptance_review(
            graph.graph_id,
            graph.revision,
            node.node_id,
            attempt_id=attempt_id,
            expected_node_version=5,
            acceptance_judgement=acceptance,
            node_outbox=_event(
                aggregate_type="TASK_NODE",
                aggregate_id=(
                    f"{graph.graph_id}@{graph.revision}/{node.node_id}"
                ),
                aggregate_version=6,
                event_type="TASK_NODE_WAITING_ACCEPTANCE_REVIEW",
                graph_revision=None,
                attempt_id=attempt_id,
                evidence_refs=(projection["projection_id"],),
            ),
            review_item=review_item,
            work_order_id=order.work_order_id,
            expected_graph_version=1,
            graph_outbox=_event(
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph.graph_id,
                aggregate_version=2,
                event_type="TASK_GRAPH_BLOCKED",
                graph_revision=graph.revision,
                attempt_id=None,
            ),
        )
        assert waiting["node"]["status"] == "WAITING_ACCEPTANCE_REVIEW"
        assert waiting["graph"]["status"] == "BLOCKED"
        assert waiting["decision"]["target_status"] == (
            "WAITING_ACCEPTANCE_REVIEW"
        )
        cancellation_claims = SQLiteSecureIngressStore(
            db_path,
            rate_limit=100,
        )
        try:
            application = GovernedCommandApplication(
                command_repository=command_store,
                coordinator=GovernedTaskCoordinator(
                    task_store=task_store,
                    internal_agent_checkpoint_uow=(
                        DurableInternalAgentCheckpointUnitOfWork(
                            task_store=task_store,
                            worker_projection_store=projection_store,
                        )
                    ),
                    outbox_publisher=_Publisher(),
                    outbox_factory=OutboxMessage,
                    worker_resolver=object(),
                    verifier=_Verifier("PASS"),
                    acceptance=object(),
                    projection_reader=projection_store,
                    owner_id=owner_id,
                ),
                cancellation_authority_verifier=(
                    SecureIngressGraphCancellationAuthorityVerifier(
                        cancellation_claims
                    )
                ),
            )
            cancel_request_id = (
                "request-governed-agent-post-review-cancellation"
            )
            cancellation_authority = _commit_graph_cancellation_authority(
                db_path,
                graph_id=graph.graph_id,
                graph_revision=graph.revision,
                request_id=cancel_request_id,
                principal_id=(
                    "service.operator.governed-agent-cancellation"
                ),
            )
            cancelled = await application.request_graph_cancellation(
                command_id=command.command_id,
                graph_id=graph.graph_id,
                graph_revision=graph.revision,
                authority=cancellation_authority,
                source="OPERATOR",
                reason_code="OPERATOR_REQUEST",
            )
        finally:
            cancellation_claims.close()
        assert cancelled["execution_receipt"]["status"] == "CANCELLED"
        snapshot = task_store.read_scheduler_snapshot(
            graph.graph_id,
            graph.revision,
        )
        assert snapshot["graph_record"]["status"] == "CANCELLED"
        assert snapshot["graph_record"]["nodes"][0]["status"] == "CANCELLED"
        assert snapshot["attempts"][0]["status"] == "SUCCEEDED"
        assert snapshot["budgets"][0]["status"] == "RELEASED"
        assert reviews.get_review(review_item.review_id)["review"][
            "status"
        ] == "CANCELLED"
        cancellation = task_store.get_graph_cancellation_for_graph(
            graph.graph_id,
            graph.revision,
        )
        assert cancellation is not None
        assert cancellation["status"] == "FINALIZED"
        assert cancellation["payload"]["graph_target_status"] == "BLOCKED"
        decision = task_store.get_graph_cancellation_decision_for_intent(
            cancellation["intent_ref"]
        )
        assert decision is not None
        assert decision["payload"]["graph_target_status"] == "CANCELLED"
        assert decision["payload"]["node_states_after"][node.node_id][
            "status"
        ] == "CANCELLED"
        assert task_store.list_acceptance_effect_intents(
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
            node_id=node.node_id,
            limit=1,
        ) == []
        if inject_acceptance_effect:
            effect_id = _inject_terminal_acceptance_effect(
                task_store,
                graph_id=graph.graph_id,
                graph_revision=graph.revision,
                node_id=node.node_id,
                attempt_id=attempt_id,
            )
            assert [
                effect["effect_id"]
                for effect in task_store.list_acceptance_effect_intents(
                    graph_id=graph.graph_id,
                    graph_revision=graph.revision,
                    node_id=node.node_id,
                    limit=1,
                )
            ] == [effect_id]

    restarted = _runtime(
        tmp_path,
        db_path=db_path,
        source=source,
        now_ms=NOW + 6_000,
    )
    skill_terminal = GovernedSkillArtifactTerminalReconciler(
        artifact_store=artifacts,
        workspace_runtime=restarted,
        task_store=task_store,
        command_repository=command_store,
        projection_store=projection_store,
    )
    group = AgentArtifactTerminalReconcilerGroup(
        skill_terminal,
        InternalArtifactTerminalReconciler(
            artifact_store=artifacts,
            workspace_runtime=restarted,
            task_store=task_store,
            command_repository=command_store,
            projection_store=projection_store,
        ),
    )
    reports = group.run_once()
    workspace_id = restarted.workspace_id_for(
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
    )
    if inject_acceptance_effect:
        assert any(
            item["action"] == "BLOCKED"
            and item["reason_code"] == "SKILL_ARTIFACT_TERMINAL_INVALID"
            for item in reports
        ), json.dumps(reports, ensure_ascii=False, sort_keys=True, indent=2)
        assert all(item["action"] != "ABANDONED" for item in reports)
        assert restarted.repository.get(workspace_id).status == "ACTIVE"
        assert _git(source, "rev-parse", "main") == source_head
        assert not (source / "src" / "failed-generated.txt").exists()
        return
    expected_reason = (
        "TERMINAL_VERIFICATION_FAILED"
        if terminal_outcome == "verification-failure"
        else "TERMINAL_POST_REVIEW_CANCELLED"
    )
    assert any(
        item["action"] == "ABANDONED"
        and item["reason_code"] == expected_reason
        for item in reports
    ), json.dumps(reports, ensure_ascii=False, sort_keys=True, indent=2)
    assert any(item["action"] == "CLEANED" for item in reports), reports
    authority = restarted.repository.get(workspace_id)
    assert authority.status == "CLEANED"
    assert not (tmp_path / "managed" / authority.workspace_relpath).exists()
    assert _git(source, "rev-parse", "main") == source_head
    assert not (source / "src" / "failed-generated.txt").exists()

    replay_runtime = _runtime(
        tmp_path,
        db_path=db_path,
        source=source,
        now_ms=NOW + 12_000,
    )
    replay_group = AgentArtifactTerminalReconcilerGroup(
        GovernedSkillArtifactTerminalReconciler(
            artifact_store=artifacts,
            workspace_runtime=replay_runtime,
            task_store=task_store,
            command_repository=command_store,
            projection_store=projection_store,
        ),
        InternalArtifactTerminalReconciler(
            artifact_store=artifacts,
            workspace_runtime=replay_runtime,
            task_store=task_store,
            command_repository=command_store,
            projection_store=projection_store,
        ),
    )
    assert replay_group.run_once() == ()
    assert replay_runtime.repository.get(authority.workspace_id).status == "CLEANED"
