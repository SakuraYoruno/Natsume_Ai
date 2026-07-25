from __future__ import annotations

import base64
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.internal_artifact_terminal_reconciliation import (
    InternalArtifactTerminalReconciler,
)
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.workspace_allocator import (
    ManagedGitSource,
    ManagedWorkspaceRoot,
    WorkspaceAllocator,
)
from host_brain.agent_runtime.workspace_policy import WorkspacePolicy
from host_brain.agent_runtime.workspace_runtime import DurableWorkspaceRuntime
from host_brain.contracts.agent_runtime.operation_reconciliation import (
    build_reconciliation_attestation_binding,
)
from host_brain.app.accepted_operation_reconciliation_handler import (
    AcceptedOperationReconciliationHandler,
)
from host_brain.app.internal_task_contracts import build_internal_task_contracts
from host_brain.contracts.agent_runtime.internal_raw_failure_claim_result import (
    INTERNAL_RAW_FAILURE_RESULT_REF_PREFIX,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.host_command_repository_types import (
    HOST_COMMAND_INGRESS_CONFLICT,
    HostCommandRepositoryError,
)
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
)
from host_brain.persistence.task_store_types import TaskStoreError
from host_brain.persistence.workspace_repository import WorkspaceRepository
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStore,
)
from host_brain.task_runtime import AdmittedExecution
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationError,
)
from host_brain.task_runtime.operation_failure_reconciliation import (
    OperationFailureReconciliationService,
)
from host_brain.task_runtime.operation_reacquisition import (
    OperationReconciliationCommandService,
)
from host_brain.task_runtime.review_runtime import TaskNodeReviewGate
from tests.integration.test_ne01_operation_online_takeover import (
    _Clock,
    _ProjectedWorker,
    _contracts,
    _coordinator,
    _events,
    _migrated_db,
)
from tests.integration.test_ne01_signed_operation_reacquisition import (
    _FailOnceWorker,
)
from tests.integration.test_ne01_internal_precheckpoint_cancellation_claim import (
    _insert_secure_authority,
    _source_repository,
)
from tests.integration.test_ne01_b5_parallel_success import (
    _NodeGrantService,
    _NodeGrantVerifier,
)


class _FailureResponseLossStore:
    def __init__(self, delegate: DurableTaskStore) -> None:
        self._delegate = delegate
        self.response_lost = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def reconcile_operation_failure(self, *args: Any, **kwargs: Any):
        result = self._delegate.reconcile_operation_failure(*args, **kwargs)
        if not self.response_lost:
            self.response_lost = True
            raise TimeoutError("simulated terminal failure response loss")
        return result


class _UnavailableClaimStore:
    def complete_claim(self, **_: Any) -> None:
        raise RuntimeError("simulated Secure Ingress claim outage")


class _ApprovedReviewRuntime:
    @staticmethod
    def _gate() -> TaskNodeReviewGate:
        return TaskNodeReviewGate(
            disposition="REVIEW_APPROVED",
            allowed=True,
            review_id="review-internal-raw-failure",
            review_status="APPROVED",
            review_type="PRE_EXECUTION_APPROVAL",
            resume_phase="PRE_EXECUTION",
            subject_ref="task-node://internal/raw-failure",
            subject_version=1,
            checkpoint_id="checkpoint://internal/raw-failure",
        )

    def pre_execution_gate(self, **_: Any) -> TaskNodeReviewGate:
        return self._gate()

    def lookup_pre_execution_gate(self, **_: Any) -> TaskNodeReviewGate:
        return self._gate()


def _release_signature(
    private_key: Ed25519PrivateKey,
    *,
    operation: dict[str, Any],
    expected_version: int,
    actor_id: str,
    attestation_ref: str,
) -> str:
    binding = build_reconciliation_attestation_binding(
        reservation_id=str(operation["reservation_id"]),
        business_scope=str(operation["business_scope"]),
        operation_fingerprint=str(operation["operation_fingerprint"]),
        recovery_version=expected_version,
        reconciler_actor_id=actor_id,
        attestation_ref=attestation_ref,
        recovery_action="RELEASE",
        reconciliation_outcome="NO_SIDE_EFFECT",
    )
    return "ed25519:" + base64.urlsafe_b64encode(
        private_key.sign(binding)
    ).decode("ascii")


def _internal_failure_contracts(
    db_path: Path,
    *,
    clock: _Clock,
) -> tuple[Any, Any, Any, Any, dict[str, str]]:
    request_id = "request-internal-raw-failure"
    principal_id = "principal-internal-raw-failure"
    claim_ref = "secure-ingress-claim://internal/raw-failure"
    audit_ref = "secure-ingress-audit://internal/raw-failure"
    body_sha256 = "b" * 64
    _insert_secure_authority(
        db_path,
        request_id=request_id,
        principal_id=principal_id,
        body_sha256=body_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    command, work_order, graph = build_internal_task_contracts(
        work_order=AgentWorkOrder(
            work_order_id="work-order-internal-raw-failure",
            domain="code",
            run_mode="inspect",
            objective="prove signed raw failure cleanup",
            acceptance_criteria=["no external effect"],
            allowed_actions=["write_files"],
            forbidden_actions=["modify_secret_files"],
            workspace={
                "strategy": "durable_workspace",
                "workspace_id": "caller-selected-and-replaced",
            },
        ),
        request_id=request_id,
        session_id="session-internal-raw-failure",
        ts_ms=clock(),
        governor_grant_ref="governor-grant://internal/raw-failure",
        command_authority={
            "principal_id": principal_id,
            "claim_ref": claim_ref,
            "audit_ref": audit_ref,
            "body_sha256": body_sha256,
        },
        frozen_runtime_context={},
    )
    assignment = CanonicalRuntimeRouter(clock=clock).assign_new_task(
        work_order.work_order_id,
        assigned_at_ms=clock(),
    )
    return (
        command,
        work_order,
        assignment,
        graph,
        {
            "claim_ref": claim_ref,
            "request_id": request_id,
        },
    )


@pytest.mark.asyncio
async def test_signed_release_atomically_fails_raw_worker_without_replay(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    verifier = Ed25519ReconciliationProofVerifier(
        {"reconciler-terminal-failure-v1": public_key}
    )
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _FailOnceWorker(
        _ProjectedWorker(projections=projections, calls=calls),
        calls,
    )
    first_store = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    application = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=first_store,
            projections=projections,
            worker=worker,
            owner_id="terminal-failure-owner-a",
            lease_ttl_ms=20,
        ),
    )

    interrupted = await _events(application, contracts)

    assert interrupted[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert worker.invocations == 1
    assert calls == ["root"]
    operation = dict(
        first_store.get_operation_reservation(admitted.reservation_id)
    )
    snapshot = first_store.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    attempt = dict(snapshot["attempts"][0])
    lease = dict(snapshot["active_leases"][0])
    budget = dict(snapshot["budgets"][0])
    assert operation["status"] == "RECOVERY_REQUIRED"
    assert attempt["status"] == "RUNNING"
    assert lease["status"] == "ACTIVE"
    assert budget["status"] in {"ACTIVE", "EXCEEDED"}

    actor_id = "reconciler://operator/terminal-failure-test"
    attestation_ref = "proof://operation-terminal-failure/integration"
    request = {
        "reservation_id": admitted.reservation_id,
        "expected_version": operation["status_version"],
        "reconciler_actor_id": actor_id,
        "attestation_ref": attestation_ref,
        "attestation_key_id": "reconciler-terminal-failure-v1",
        "attestation_signature": _release_signature(
            private_key,
            operation=operation,
            expected_version=operation["status_version"],
            actor_id=actor_id,
            attestation_ref=attestation_ref,
        ),
    }
    evidence = first_store.stage_operation_reconciliation_evidence(
        request_id="request-signed-operation-terminal-failure",
        ingress_audit_ref="secure-ingress-audit://operation/terminal-failure",
        body_sha256="a" * 64,
        action="RELEASE",
        intent_ref=None,
        downstream_receipt_ref=None,
        **request,
    )
    assert evidence["action"] == "RELEASE"
    assert evidence["intent_ref"] is None

    service = OperationFailureReconciliationService(
        task_store=first_store,
        outbox_factory=OutboxMessage,
    )
    with pytest.raises(GraphCancellationOperationReconciliationError) as live:
        service.reconcile_operation_failure(**request)
    assert live.value.side_effects_may_have_run is False
    assert first_store.get_operation_reservation(
        admitted.reservation_id
    ) == operation
    assert first_store.get_attempt(attempt["attempt_id"]) == attempt

    forged = {
        **request,
        "attestation_signature": _release_signature(
            Ed25519PrivateKey.generate(),
            operation=operation,
            expected_version=operation["status_version"],
            actor_id=actor_id,
            attestation_ref=attestation_ref,
        ),
    }
    clock.advance(21)
    with pytest.raises(GraphCancellationOperationReconciliationError) as invalid:
        service.reconcile_operation_failure(**forged)
    assert invalid.value.side_effects_may_have_run is False
    assert first_store.get_operation_reservation(
        admitted.reservation_id
    ) == operation

    reopened = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    response_loss_store = _FailureResponseLossStore(reopened)
    response_loss_service = OperationFailureReconciliationService(
        task_store=response_loss_store,
        outbox_factory=OutboxMessage,
    )
    result = response_loss_service.reconcile_operation_failure(**request)

    assert response_loss_store.response_lost is True
    assert result["status"] == "RECONCILED"
    assert result["replayed"] is True
    assert result["graph_id"] == admitted.graph_id
    assert result["graph_revision"] == admitted.graph_revision
    assert result["node_id"] == admitted.node_id
    assert result["attempt_id"] == attempt["attempt_id"]
    assert result["operation"]["status"] == "RELEASED"
    assert result["operation"]["reconciliation_action"] == "RELEASE"
    assert result["operation"]["reconciliation_outcome"] == "NO_SIDE_EFFECT"
    assert result["terminal"] == {
        "authority_kind": "RECONCILED_OPERATION_TERMINAL_FAILURE",
        "graph_status": "FAILED",
        "node_status": "FAILED",
        "attempt_status": "FAILED",
        "budget_status": "RELEASED",
        "lease_status": "EXPIRED",
        "failure_reason_code": "GOVERNED_WORKER_FAILED",
        "error_ref": result["terminal"]["error_ref"],
    }
    assert result["terminal"]["error_ref"].startswith(
        "taskerror://operation-failure/sha256/"
    )
    assert worker.invocations == 1
    assert calls == ["root"]

    terminal = reopened.resolve_reconciled_operation_failure(
        admitted.reservation_id
    )
    assert terminal["graph"]["status"] == "FAILED"
    assert terminal["node"]["status"] == "FAILED"
    assert terminal["attempt"]["status"] == "FAILED"
    assert terminal["lease"]["status"] == "EXPIRED"
    assert terminal["budget"]["status"] == "RELEASED"
    assert terminal["operation"]["status"] == "RELEASED"
    replay = response_loss_service.reconcile_operation_failure(**request)
    assert replay["replayed"] is True
    assert replay["terminal"] == result["terminal"]
    assert worker.invocations == 1

    command_service = OperationReconciliationCommandService(
        cancellation_service=object(),  # unreachable for no-intent RELEASE
        reacquisition_service=object(),  # unreachable for no-intent RELEASE
        checkpoint_service=object(),  # unreachable for no-intent RELEASE
        failure_service=response_loss_service,
    )
    handler = AcceptedOperationReconciliationHandler(
        service=command_service,
        evidence_store=reopened,
        continuation_provider=lambda: (_ for _ in ()).throw(
            AssertionError("terminal failure must not rerun the target")
        ),
    )
    handler_result = await handler(
        {
            "operator_command_id": "task_operation.reconcile",
            "request_id": evidence["request_id"],
            "evidence_ref": evidence["evidence_ref"],
            "evidence_digest": evidence["evidence_digest"],
            "reservation_id": request["reservation_id"],
            "expected_version": request["expected_version"],
            "action": "RELEASE",
            "reconciler_actor_id": actor_id,
        }
    )
    assert handler_result["status"] == "COMMITTED"
    assert handler_result["operation_status"] == "RELEASED"
    assert handler_result["target_continuation"] is None
    assert handler_result["target_terminal"] == result["terminal"]
    assert handler_result["receipt_ref"].startswith(
        "operation-reconcile://receipt/sha256/"
    )

    with reopened._connect() as conn:
        event_counts = {
            str(row["event_type"]): int(row["count"])
            for row in conn.execute(
                """
                SELECT event_type, COUNT(*) AS count
                FROM task_runtime_outbox
                WHERE event_type IN (
                    'OPERATION_RECOVERY_RELEASE',
                    'TASK_ATTEMPT_FAILED',
                    'TASK_NODE_FAILED',
                    'TASK_GRAPH_FAILED',
                    'TASK_LEASE_EXPIRED',
                    'BUDGET_RESERVATION_RELEASED'
                )
                GROUP BY event_type
                """
            ).fetchall()
        }
    assert event_counts == {
        "BUDGET_RESERVATION_RELEASED": 1,
        "OPERATION_RECOVERY_RELEASE": 1,
        "TASK_ATTEMPT_FAILED": 1,
        "TASK_GRAPH_FAILED": 1,
        "TASK_LEASE_EXPIRED": 1,
        "TASK_NODE_FAILED": 1,
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            DELETE FROM task_runtime_outbox
            WHERE event_type = 'TASK_NODE_FAILED'
            """
        )
    with pytest.raises(TaskStoreError):
        reopened.resolve_reconciled_operation_failure(
            admitted.reservation_id
        )


@pytest.mark.asyncio
async def test_internal_raw_failure_closes_claim_before_real_workspace_cleanup(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    verifier = Ed25519ReconciliationProofVerifier(
        {"reconciler-internal-raw-failure-v1": public_key}
    )
    db_path = _migrated_db(tmp_path)
    clock = _Clock()
    command, work_order, assignment, graph, refs = _internal_failure_contracts(
        db_path,
        clock=clock,
    )
    contracts = (command, work_order, assignment, graph)
    admitted = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
    )
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _FailOnceWorker(
        _ProjectedWorker(projections=projections, calls=calls),
        calls,
    )
    task_store = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    command_repository = HostCommandRepository(
        db_path,
        clock=clock,
        task_store=task_store,
    )
    application = GovernedCommandApplication(
        command_repository=command_repository,
        coordinator=_coordinator(
            store=task_store,
            projections=projections,
            worker=worker,
            owner_id="terminal-internal-raw-failure-owner",
            lease_ttl_ms=20,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_NodeGrantVerifier(),
            review_runtime=_ApprovedReviewRuntime(),
        ),
        node_grant_service=_NodeGrantService(),
    )

    review_wait = await _events(application, contracts)
    assert review_wait[-1]["execution_receipt"]["status"] == "WAITING_APPROVAL"
    interrupted = await _events(application, contracts)

    assert interrupted[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert worker.invocations == 1
    operation = dict(
        task_store.get_operation_reservation(admitted.reservation_id)
    )
    snapshot = task_store.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    attempt = dict(snapshot["attempts"][0])
    lease = dict(snapshot["active_leases"][0])

    source = _source_repository(tmp_path)
    managed = tmp_path / "managed-internal-raw-failure"
    managed.mkdir()
    workspace_repository = WorkspaceRepository(db_path, clock=clock)
    workspace_runtime = DurableWorkspaceRuntime(
        repository=workspace_repository,
        allocator=WorkspaceAllocator(
            repository=workspace_repository,
            managed_roots={
                "internal-raw-failure-worktrees": ManagedWorkspaceRoot(
                    root_ref="internal-raw-failure-worktrees",
                    path=managed,
                    isolation_modes=frozenset({"DIRECTORY_FENCE"}),
                )
            },
            git_sources={
                "internal-raw-failure-source": ManagedGitSource(
                    source_key="internal-raw-failure-source",
                    source_ref_prefix=(
                        "git-worktree://internal-raw-failure-source"
                    ),
                    repository=source,
                )
            },
        ),
        runtime_owner_id="terminal-internal-raw-failure-owner",
        managed_root_ref="internal-raw-failure-worktrees",
        git_source_key="internal-raw-failure-source",
        lease_ms=60_000,
        heartbeat_interval_ms=1_000,
        token_secret=b"r" * 48,
    )
    workspace_id = DurableWorkspaceRuntime.workspace_id_for(
        graph_id=command.graph_id,
        graph_revision=command.graph_revision,
    )
    authority = {
        "root_work_order_id": work_order.root_work_order_id,
        "graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "node_id": work_order.task_node_id,
        "attempt_id": attempt["attempt_id"],
        "lease_id": lease["lease_id"],
        "command_id": command.command_id,
        "principal_id": command.principal_id,
        "lease_owner_id": lease["owner_id"],
        "fencing_token": lease["fencing_token"],
    }
    resolution = WorkspacePolicy(
        workspace_allocator=workspace_runtime.allocator,
        workspace_lease_provider=lambda order, candidate_workspace_id: (
            workspace_runtime.lease_for_order(
                order,
                candidate_workspace_id,
                execution_authority=authority,
            )
        ),
    ).resolve(work_order)
    workspace_path = resolution.path
    artifacts = AgentArtifactStore(tmp_path / "agent-runs-raw-failure")
    stage = artifacts.prepare_governed_stage(
        workspace_id=workspace_id,
        execution_authority=authority,
    )
    assert workspace_path.is_dir()
    assert stage.stage_dir.is_dir()

    actor_id = "reconciler://operator/internal-raw-failure-test"
    attestation_ref = "proof://operation/internal-raw-failure/integration"
    request = {
        "reservation_id": admitted.reservation_id,
        "expected_version": operation["status_version"],
        "reconciler_actor_id": actor_id,
        "attestation_ref": attestation_ref,
        "attestation_key_id": "reconciler-internal-raw-failure-v1",
        "attestation_signature": _release_signature(
            private_key,
            operation=operation,
            expected_version=operation["status_version"],
            actor_id=actor_id,
            attestation_ref=attestation_ref,
        ),
    }
    clock.advance(21)
    terminal_result = OperationFailureReconciliationService(
        task_store=task_store,
        outbox_factory=OutboxMessage,
    ).reconcile_operation_failure(**request)
    assert terminal_result["terminal"]["graph_status"] == "FAILED"
    assert worker.invocations == 1

    blocked = InternalArtifactTerminalReconciler(
        artifact_store=artifacts,
        workspace_runtime=workspace_runtime,
        task_store=task_store,
        command_repository=command_repository,
        projection_store=projections,
        claim_result_store=_UnavailableClaimStore(),
    ).run_once()
    assert blocked[0]["action"] == "BLOCKED"
    assert workspace_path.is_dir()
    assert stage.stage_dir.is_dir()

    claim_store = SQLiteSecureIngressStore(db_path)
    try:
        cleaned = InternalArtifactTerminalReconciler(
            artifact_store=artifacts,
            workspace_runtime=workspace_runtime,
            task_store=task_store,
            command_repository=command_repository,
            projection_store=projections,
            claim_result_store=claim_store,
        ).run_once()
    finally:
        claim_store.close()
    assert cleaned[0]["action"] == "UNSEALED_CLEANED"
    assert cleaned[0]["reason_code"] == "TERMINAL_WORKER_FAILED_UNSEALED"
    assert not workspace_path.exists()
    assert not stage.stage_dir.exists()
    assert workspace_repository.get(workspace_id).status == "CLEANED"
    assert (source / "baseline.txt").read_text(encoding="utf-8") == "baseline\n"
    assert worker.invocations == 1

    with sqlite3.connect(db_path) as conn:
        claim = conn.execute(
            """
            SELECT result_ref
            FROM secure_ingress_idempotency_claim
            WHERE claim_ref = ?
            """,
            (refs["claim_ref"],),
        ).fetchone()
    assert claim is not None
    assert str(claim[0]).startswith(INTERNAL_RAW_FAILURE_RESULT_REF_PREFIX)
    assert (
        command_repository.validate_ingress_claim_result(command.command_id)[
            "command"
        ].command_id
        == command.command_id
    )

    restarted_store = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    restarted_commands = HostCommandRepository(
        db_path,
        clock=clock,
        task_store=restarted_store,
    )
    assert (
        restarted_commands.validate_ingress_claim_result(command.command_id)[
            "command"
        ].command_id
        == command.command_id
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_idempotency_reservation
            SET reconciliation_proof_signature = ?
            WHERE reservation_id = ?
            """,
            ("ed25519:" + ("A" * 88), admitted.reservation_id),
        )
    with pytest.raises(HostCommandRepositoryError) as rejected:
        restarted_commands.validate_ingress_claim_result(command.command_id)
    assert rejected.value.reason_code == HOST_COMMAND_INGRESS_CONFLICT
