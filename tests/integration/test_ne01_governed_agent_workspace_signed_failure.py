from __future__ import annotations

import base64
from pathlib import Path
import sqlite3
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import host_brain.agent_runtime.executor as executor_module
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.executor import execute_agent_runtime_payload
from host_brain.agent_runtime.internal_artifact_terminal_reconciliation import (
    InternalArtifactTerminalReconciler,
)
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.skill_artifact_terminal_reconciliation import (
    AgentArtifactTerminalReconcilerGroup,
    GovernedSkillArtifactTerminalReconciler,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.operation_reconciliation import (
    build_reconciliation_attestation_binding,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
)
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore
from host_brain.persistence.workspace_terminal_failure_authority import (
    finalized_no_effect_failure_allows_workspace_abandonment,
)
from host_brain.task_runtime.operation_failure_reconciliation import (
    OperationFailureReconciliationService,
)
from tests.integration.test_ne01_governed_agent_workspace_acceptance import (
    NOW,
    RUNTIME_OWNER,
    _contracts,
    _git,
    _inject_terminal_acceptance_effect,
    _runtime,
    _source_repository,
    _start_public_attempt,
)


_KEY_ID = "workspace-signed-failure-v1"


class _MutableClock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


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


@pytest.mark.parametrize("tamper", (None, "outbox", "effect", "signature"))
def test_signed_no_effect_failure_cleans_direct_skill_workspace_only_with_exact_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tamper: str | None,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    clock = _MutableClock(NOW)
    source, source_head = _source_repository(tmp_path)
    db_path = tmp_path / "governed-agent-signed-failure.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="governed-agent-signed-failure-migrations",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    task_store = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=(
            Ed25519ReconciliationProofVerifier({_KEY_ID: public_key})
        ),
    )
    command_store = HostCommandRepository(db_path, task_store=task_store)
    projection_store = WorkerProjectionStore(db_path, clock=clock)
    command, order, graph = _contracts()
    node = graph.nodes[0]
    assignment = CanonicalRuntimeRouter().assign_new_task(
        order.work_order_id,
        assigned_at_ms=NOW,
    )
    assert command_store.admit(command, order, assignment, graph)["created"] is True
    attempt_id = "attempt-governed-agent-signed-failure"
    reservation_id = "reservation-governed-agent-signed-failure"
    budget_id = "budget-governed-agent-signed-failure"
    lease = _start_public_attempt(
        task_store=task_store,
        command=command,
        graph=graph,
        node=node,
        attempt_id=attempt_id,
        reservation_id=reservation_id,
        budget_id=budget_id,
        owner_id=RUNTIME_OWNER,
    )
    workspace_runtime = _runtime(
        tmp_path,
        db_path=db_path,
        source=source,
    )
    artifacts = AgentArtifactStore(tmp_path / "agent-runs")

    class _InterruptedWriter:
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
            assert callable(cancelled) and not cancelled()
            assert model_budget_guard is not None
            (workspace / "src" / "interrupted.txt").write_text(
                "sealed but never accepted\n",
                encoding="utf-8",
            )
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="sealed output before response loss",
                result_text="sealed output before response loss",
                verification={"status": "PASS"},
                acceptance={"decision": "REVIEW"},
                review_required=True,
            )

    monkeypatch.setattr(executor_module, "AgentExecutor", _InterruptedWriter)
    raw_result = execute_agent_runtime_payload(
        {
            "work_order": {
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
                "approval_policy": {
                    "require_confirm": True,
                    "confirmed": True,
                },
                "review_policy": {"review_required": True},
                "metadata": {
                    "skill_id": "agent_worker",
                    "request_id": command.request_id,
                },
            }
        },
        model_attempt_service=object(),
        prompt_registry=object(),
        artifact_store=artifacts,
        workspace_runtime=workspace_runtime,
        governed_execution_context={
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
            "lease_owner_id": RUNTIME_OWNER,
            "fencing_token": int(lease["fencing_token"]),
            "reservation_id": reservation_id,
            "node_grant_ref": "governor-decision://sha256/" + "1" * 64,
            "parent_grant_ref": command.governor_grant_ref,
            "business_scope": command.business_scope,
            "operation_fingerprint": command.operation_fingerprint,
            "node_risk_level": 4,
            "pre_execution_review_ref": (
                "review://governed-agent-signed-failure"
            ),
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
        },
    )
    protected = dict(raw_result["_protected_stage"])
    operation = task_store.mark_worker_interruption_recovery_required(
        reservation_id,
        attempt_id=attempt_id,
        lease_id=str(lease["lease_id"]),
        owner_id=RUNTIME_OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_version=0,
        reason_code="GOVERNED_WORKER_FAILED",
    )
    assert operation["status"] == "RECOVERY_REQUIRED"
    clock.value = NOW + 60_001
    actor_id = "reconciler://operator/workspace-signed-failure"
    attestation_ref = "proof://workspace/signed-no-effect-failure"
    settled = OperationFailureReconciliationService(
        task_store=task_store,
        outbox_factory=OutboxMessage,
    ).reconcile_operation_failure(
        reservation_id=reservation_id,
        expected_version=int(operation["status_version"]),
        reconciler_actor_id=actor_id,
        attestation_ref=attestation_ref,
        attestation_key_id=_KEY_ID,
        attestation_signature=_release_signature(
            private_key,
            operation=operation,
            expected_version=int(operation["status_version"]),
            actor_id=actor_id,
            attestation_ref=attestation_ref,
        ),
    )
    assert settled["terminal"]["authority_kind"] == (
        "RECONCILED_OPERATION_TERMINAL_FAILURE"
    )
    assert settled["terminal"]["graph_status"] == "FAILED"
    assert settled["terminal"]["lease_status"] == "EXPIRED"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        assert finalized_no_effect_failure_allows_workspace_abandonment(
            conn,
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
            node_id=node.node_id,
            attempt_id=attempt_id,
            lease_id=str(lease["lease_id"]),
        )
        if tamper == "outbox":
            conn.execute(
                """
                DELETE FROM task_runtime_outbox
                WHERE event_type = 'OPERATION_RECOVERY_RELEASE'
                  AND attempt_id = ?
                """,
                (attempt_id,),
            )
            conn.commit()
        elif tamper == "signature":
            conn.execute(
                """
                UPDATE task_runtime_idempotency_reservation
                SET reconciliation_proof_signature = ?
                WHERE reservation_id = ?
                """,
                ("ed25519:" + ("A" * 86) + "==", reservation_id),
            )
            conn.commit()

    if tamper == "effect":
        _inject_terminal_acceptance_effect(
            task_store,
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
            node_id=node.node_id,
            attempt_id=attempt_id,
        )
    if tamper in {"outbox", "effect"}:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            assert not finalized_no_effect_failure_allows_workspace_abandonment(
                conn,
                graph_id=graph.graph_id,
                graph_revision=graph.revision,
                node_id=node.node_id,
                attempt_id=attempt_id,
                lease_id=str(lease["lease_id"]),
            )

    restarted = _runtime(
        tmp_path,
        db_path=db_path,
        source=source,
        now_ms=clock.value + 1,
    )
    group = AgentArtifactTerminalReconcilerGroup(
        GovernedSkillArtifactTerminalReconciler(
            artifact_store=artifacts,
            workspace_runtime=restarted,
            task_store=task_store,
            command_repository=command_store,
            projection_store=projection_store,
        ),
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
    if tamper is not None:
        assert any(item["action"] == "BLOCKED" for item in reports), reports
        assert all(item["action"] != "ABANDONED" for item in reports)
        assert restarted.repository.get(workspace_id).status == "ACTIVE"
        assert _git(source, "rev-parse", "main") == source_head
        return

    assert any(
        item["action"] == "ABANDONED"
        and item["reason_code"] == "TERMINAL_SIGNED_NO_EFFECT_FAILURE"
        for item in reports
    ), reports
    assert any(item["action"] == "CLEANED" for item in reports), reports
    authority = restarted.repository.get(workspace_id)
    assert authority.status == "CLEANED"
    assert not (tmp_path / "managed" / authority.workspace_relpath).exists()
    assert _git(source, "rev-parse", "main") == source_head
    assert not (source / "src" / "interrupted.txt").exists()

    replay = _runtime(
        tmp_path,
        db_path=db_path,
        source=source,
        now_ms=clock.value + 7_000,
    )
    replay_group = AgentArtifactTerminalReconcilerGroup(
        GovernedSkillArtifactTerminalReconciler(
            artifact_store=artifacts,
            workspace_runtime=replay,
            task_store=task_store,
            command_repository=command_store,
            projection_store=projection_store,
        ),
        InternalArtifactTerminalReconciler(
            artifact_store=artifacts,
            workspace_runtime=replay,
            task_store=task_store,
            command_repository=command_store,
            projection_store=projection_store,
        ),
    )
    assert replay_group.run_once() == ()
    assert replay.repository.get(workspace_id).status == "CLEANED"
    assert protected["ref"].startswith("agent-stage://")
