from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
import host_brain.agent_runtime.executor as agent_runtime_executor_module
from host_brain.agent_runtime.internal_result_proposal import (
    InternalAgentProposalExecutor,
)
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.workspace_allocator import (
    ManagedGitSource,
    ManagedWorkspaceRoot,
    WorkspaceAllocator,
)
from host_brain.agent_runtime.workspace_runtime import DurableWorkspaceRuntime
from host_brain.agent_runtime.accepted_result import (
    InternalAcceptedResultError,
    InternalAcceptedResultHandler,
)
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.agent_tools import (
    AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
)
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_store_types import TaskStoreError
from host_brain.contracts.agent_runtime.budget_policy import BudgetExceededAction
from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount
import host_brain.app.internal_task_runtime as internal_task_runtime_module
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3
from host_brain.contracts.agent_runtime.workspace_lifecycle import (
    WorkspaceLifecycleState,
)
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.tokens import TokenService
from host_brain.app.internal_task_runtime import (
    InternalAgentCommandService,
    _build_contracts,
    build_governed_internal_runtime,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.integrations.runtime_events import RuntimeEventStore, RuntimeOutboxPublisher
from host_brain.persistence.agent_execution_repository import (
    AgentExecutionResultRepository,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.review_continuation_store import ReviewContinuationStore
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore
from host_brain.persistence.workspace_repository import WorkspaceRepository
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore
from host_brain.model_runtime import (
    ModelAttemptResult,
    ProviderResponse,
    ProviderTarget,
    RouteDecision,
    build_live_production_prompt_registry,
)
from host_brain.runtime.context_application import FrozenRuntimeContextV1
from host_brain.runtime.context_contracts import WorldSnapshotV2
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)
from host_brain.task_runtime.admission import AdmittedExecution
from host_brain.task_runtime.governed_workers import FrozenNodeInput


NOW = 1_800_000_000_000


class _MutableClock:
    def __init__(self, value: int = NOW) -> None:
        self.value = int(value)

    def __call__(self) -> int:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += int(milliseconds)


class _RuntimeContextAuthority:
    def __init__(self, *, principal_id: str) -> None:
        workspace_id = "workspace-internal-integration"
        subject_id = principal_id
        world = WorldSnapshotV2(
            workspace_id=workspace_id,
            subject_id=subject_id,
            revision=1,
            facts=(),
        )
        self._payload = {
            "runtime_context_schema": "natsume.runtime-context.request.v1",
            "runtime_context_observed_at_ms": NOW,
            "runtime_context_principal_id": principal_id,
            "workspace_id": workspace_id,
            "subject_id": subject_id,
            "world_snapshot_v2": world.to_dict(),
            "scene_identity_v2": {
                "schema": "natsume.scene-identity.v2",
                "workspace_id": workspace_id,
                "subject_id": subject_id,
                "scene_id": "scene.internal-integration",
                "revision": 1,
                "decision_digest": hashlib.sha256(
                    b"internal-integration-scene"
                ).hexdigest(),
            },
        }

    def freeze_request(self, **_kwargs: Any) -> FrozenRuntimeContextV1:
        return FrozenRuntimeContextV1(payload=deepcopy(self._payload))

    def require_frozen(self, value: Any) -> FrozenRuntimeContextV1:
        source = dict(value or {})
        if any(source.get(key) != expected for key, expected in self._payload.items()):
            raise ValueError("Internal Runtime Context drifted")
        return FrozenRuntimeContextV1(payload=deepcopy(self._payload))


class _ExactPreviewAuthority:
    def __init__(self) -> None:
        self._records: dict[str, tuple[dict[str, Any], SimpleNamespace]] = {}
        self._issued: dict[str, dict[str, Any]] = {}

    def issue_receipt(self, **kwargs: Any) -> dict[str, Any]:
        index = len(self._records) + 1
        event_id = f"runtimeevent://internal-cutover/{index}"
        receipt_ref = f"action-preview://internal-cutover/{index}"
        authority = dict(kwargs.get("authority_context") or {})
        authority_kind = (
            "ACCEPTANCE_EFFECT"
            if authority.get("schema_version")
            == "Natsume.action_preview.acceptance_effect_authority_request.v1"
            else "TASK_NODE"
        )
        boundary = {
            key: deepcopy(value)
            for key, value in kwargs.items()
            if key != "guardian_capability_id"
        }
        node_grant = dict(kwargs.get("node_grant") or {})
        grant_digest = "sha256:" + _sha256(
            json.dumps(
                node_grant,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        receipt_payload = {
            "authority_kind": authority_kind,
            "node_grant_ref": str(kwargs.get("node_grant_ref") or ""),
            "node_grant_digest": grant_digest,
        }
        receipt = SimpleNamespace(
            receipt_ref=receipt_ref,
            payload=receipt_payload,
        )
        self._records[event_id] = (boundary, receipt)
        issued = {
            "event_id": event_id,
            "receipt_ref": receipt_ref,
            "receipt": deepcopy(receipt_payload),
        }
        self._issued[event_id] = deepcopy(issued)
        return issued

    def require_execution(self, *, event_id: str, **kwargs: Any) -> Any:
        boundary, receipt = self._records[event_id]
        assert kwargs == boundary
        return receipt

    def reopen_receipt(self, *, event_id: str) -> dict[str, Any]:
        return {**deepcopy(self._issued[event_id]), "replayed": True}


class _SimulatedProjectionProcessStop(BaseException):
    pass


class _CrashBeforeProjectionStore:
    def __init__(self, delegate: WorkerProjectionStore) -> None:
        self._delegate = delegate
        self.stage_calls = 0
        self.crash_enabled = True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def stage(self, *, projection_id: str, projection: Any) -> Any:
        self.stage_calls += 1
        if self.crash_enabled:
            self.crash_enabled = False
            raise _SimulatedProjectionProcessStop(
                "process stopped after Agent seal and before projection"
            )
        return self._delegate.stage(
            projection_id=projection_id,
            projection=projection,
        )


class _RecoveredCheckpointResponseLossStore:
    def __init__(self, delegate: DurableTaskStore) -> None:
        self._delegate = delegate
        self.commit_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def commit_internal_agent_recovered_checkpoint(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        self.commit_calls += 1
        result = self._delegate.commit_internal_agent_recovered_checkpoint(
            *args,
            **kwargs,
        )
        if self.commit_calls == 1:
            raise TimeoutError("simulated recovered checkpoint response loss")
        return result


class _RejectedBudgetResponseLossStore:
    def __init__(self, delegate: DurableTaskStore) -> None:
        self._delegate = delegate
        self.reject_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def reject_internal_agent_checkpoint_budget_bundle(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        self.reject_calls += 1
        result = self._delegate.reject_internal_agent_checkpoint_budget_bundle(
            *args,
            **kwargs,
        )
        if self.reject_calls == 1:
            raise TimeoutError("simulated rejected budget bundle response loss")
        return result


class _SimulatedAttemptSucceededProcessStop(BaseException):
    pass


class _CrashAfterSucceededAttemptStore:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.crash_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def transition_attempt(self, *args: Any, **kwargs: Any) -> Any:
        result = self._delegate.transition_attempt(*args, **kwargs)
        target = getattr(kwargs.get("target_status"), "value", kwargs.get("target_status"))
        if str(target or "").upper() == "SUCCEEDED" and self.crash_count == 0:
            self.crash_count += 1
            raise _SimulatedAttemptSucceededProcessStop(
                "process stopped after Attempt SUCCEEDED and before Node continuation"
            )
        return result


class _RejectedBudgetFaultStore(DurableTaskStore):
    def __init__(self, *args: Any, fail_event: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fail_event = str(fail_event)
        self.captured_rejection: tuple[tuple[Any, ...], dict[str, Any]] | None = None

    def reject_internal_agent_checkpoint_budget_bundle(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        self.captured_rejection = (tuple(args), dict(kwargs))
        return super().reject_internal_agent_checkpoint_budget_bundle(
            *args,
            **kwargs,
        )

    def _insert_outbox(self, conn, message, **kwargs):  # noqa: ANN001, ANN202
        if message.event_type == self.fail_event:
            raise RuntimeError(f"fault before {self.fail_event} outbox insert")
        return super()._insert_outbox(conn, message, **kwargs)


def _sha256(value: bytes | str) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _insert_secure_authority(
    db_path: Path,
    *,
    request_id: str,
    principal_id: str,
    body_sha256: str,
    claim_ref: str,
    audit_ref: str,
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO secure_ingress_idempotency_claim(
                principal_id, surface, idempotency_digest, action,
                required_scopes_json, request_id, body_sha256, claim_ref,
                result_ref, claim_status, provisional_expires_at_ms,
                claimed_at_ms, updated_at_ms, expires_at_ms
            ) VALUES (?, 'internal.agent', ?, 'internal:agent:execute',
                      '["internal:agent:execute"]', ?, ?, ?, NULL,
                      'COMMITTED', NULL, ?, ?, ?)
            """,
            (
                principal_id,
                f"sha256:{_sha256('internal-idempotency:' + request_id)}",
                request_id,
                body_sha256,
                claim_ref,
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
            ) VALUES (?, ?, NULL, 'internal.agent', 'internal:agent:execute', 'POST',
                      '/internal/codex/work-orders/execute', ?, 'tenant-internal',
                      'key-internal', 'HMAC_SHA256', ?,
                      '["internal:agent:execute"]', '["internal:agent:execute"]',
                      ?, ?, ?, ?, 'ALLOW', 'SECURE_INGRESS_ALLOWED', 'COMPLETE',
                      ?, '{}', 0, ?)
            """,
            (
                audit_ref,
                request_id,
                principal_id,
                f"sha256:{_sha256('internal-credential:' + request_id)}",
                body_sha256,
                f"sha256:{_sha256('internal-idempotency:' + request_id)}",
                f"sha256:{_sha256('internal-nonce:' + request_id)}",
                f"sha256:{_sha256('internal-transport:' + request_id)}",
                claim_ref,
                NOW,
            ),
        )


class _Executor:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls
        self.review_required = False
        self.before_return = None

    def execute(
        self,
        order: AgentWorkOrder,
        *,
        workspace_execution_authority: dict[str, Any] | None = None,
    ) -> dict:
        assert workspace_execution_authority is not None
        self._calls.append(order.work_order_id)
        if self.before_return is not None:
            self.before_return(order)
        return {
            "status": "EXECUTED",
            "work_order_id": order.work_order_id,
            "summary": "internal governed worker completed",
            "result_text": "durable internal result",
            "verification": {"status": "PASSED"},
            "acceptance": {
                "decision": "REVIEW" if self.review_required else "ACCEPT"
            },
            "risk_flags": [],
            "review_required": self.review_required,
            "metadata": {"executor_probe": "canonical"},
        }


class _IntentEngine:
    def __init__(self, ledger: SQLiteLedger, token_service: TokenService) -> None:
        self._ledger = ledger
        self._token_service = token_service
        self.calls = 0

    def evaluate_intent(self, **kwargs):
        self.calls += 1
        intent = kwargs["raw_intent"]
        token_ref = self._token_service.issue_approval_token(
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=intent["ts_ms"],
        )
        binding = build_governed_action_binding(
            intent_envelope=intent,
            approval_token_ref=token_ref,
            risk_level=1,
            issued_at_ms=intent["ts_ms"],
            expires_at_ms=intent["ts_ms"] + 120_000,
        )
        decision = {
            "request_id": kwargs["request_id"],
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "reason_code": None,
            "tokens": {"approval_token_ref": token_ref},
            "approved_action_binding": binding,
            "snapshot_refs": {},
            "stage": "S_INTENT_OK",
            "ts_ms": intent["ts_ms"],
        }
        self._ledger.log_governor(decision)
        return decision


class _FinishOnlyAttemptService:
    def __init__(self) -> None:
        self.calls = 0

    def complete_sync(self, request):  # noqa: ANN001, ANN201
        self.calls += 1
        target = ProviderTarget(
            adapter_id="test.finish-only",
            provider="test",
            model_id="finish-only",
            timeout_ms=5_000,
        )
        return ModelAttemptResult(
            request_id=request.request_id,
            success=True,
            route_decision=RouteDecision(
                decision_id="route-finish-only",
                targets=(target,),
                reason="write-finish-laundering-regression",
                max_attempts=1,
            ),
            attempts=(),
            response=ProviderResponse(
                content=json.dumps(
                    {
                        "thought": "claim completion without an admitted tool",
                        "tool_id": "agent_finish",
                        "tool_args": {},
                        "final_answer": "pretend the write completed",
                    }
                )
            ),
            final_target=target,
        )


@pytest.mark.asyncio
async def test_internal_proposal_supplies_exact_durable_workspace_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "internal-workspace@example.invalid"),
        ("config", "user.name", "Internal Workspace Test"),
    ):
        completed = subprocess.run(
            ["git", "-C", str(source), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
    (source / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    for args in (("add", "baseline.txt"), ("commit", "-m", "initial")):
        completed = subprocess.run(
            ["git", "-C", str(source), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr

    request_id = "request-internal-durable-workspace"
    suffix = hashlib.sha256(f"internal\n{request_id}".encode()).hexdigest()[:40]
    graph_id = f"graph-internal-{suffix}"
    workspace_id = DurableWorkspaceRuntime.workspace_id_for(
        graph_id=graph_id,
        graph_revision=1,
    )
    command, work_order, graph = _build_contracts(
        work_order=AgentWorkOrder(
            work_order_id="work-order-internal-durable-workspace",
            domain="code",
            run_mode="inspect",
            objective="read from the durable workspace",
            workspace={
                "strategy": "durable_workspace",
                "workspace_id": workspace_id,
            },
            allowed_actions=["read_files"],
            forbidden_actions=["write_files"],
            acceptance_criteria=["result executed"],
            review_policy={"review_required": False},
        ),
        request_id=request_id,
        session_id="session-internal-durable-workspace",
        ts_ms=NOW,
        governor_grant_ref="governor-grant://internal/durable-workspace",
        command_authority={
            "principal_id": "principal-internal-durable-workspace",
            "claim_ref": "ingress-claim://internal/durable-workspace",
            "audit_ref": "audit://internal/durable-workspace",
            "body_sha256": _sha256("internal-durable-workspace"),
        },
        frozen_runtime_context={},
    )
    node = graph.nodes[0]
    assignment = CanonicalRuntimeRouter(clock=lambda: NOW).assign_new_task(
        work_order.work_order_id,
        assigned_at_ms=NOW,
    )
    db_path = tmp_path / "internal-durable-workspace.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="internal-durable-workspace-migrate",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref,
                status, status_version, graph_json, source_kind,
                created_ts_ms, updated_ts_ms
            ) VALUES (?, 1, ?, ?, 'ACTIVE', 0, '{}', 'CANONICAL', ?, ?)
            """,
            (
                graph.graph_id,
                work_order.root_work_order_id,
                graph.planner_ref,
                NOW,
                NOW,
            ),
        )
    managed = tmp_path / "managed"
    managed.mkdir()
    repository = WorkspaceRepository(db_path)
    allocator = WorkspaceAllocator(
        repository=repository,
        managed_roots={
            "internal-worktrees": ManagedWorkspaceRoot(
                root_ref="internal-worktrees",
                path=managed,
                isolation_modes=frozenset({"DIRECTORY_FENCE"}),
            )
        },
        git_sources={
            "internal-source": ManagedGitSource(
                source_key="internal-source",
                source_ref_prefix="git-worktree://internal-source",
                repository=source.resolve(),
            )
        },
    )
    runtime_owner = "internal-durable-worker"
    workspace_runtime = DurableWorkspaceRuntime(
        repository=repository,
        allocator=allocator,
        runtime_owner_id=runtime_owner,
        managed_root_ref="internal-worktrees",
        git_source_key="internal-source",
        lease_ms=30_000,
        heartbeat_interval_ms=1_000,
        token_secret=b"i" * 48,
    )

    class _CanonicalAgentExecutor:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def execute(
            self,
            candidate,
            *,
            workspace,
            cancelled=None,
        ):  # noqa: ANN001, ANN202
            assert candidate.work_order_id == work_order.work_order_id
            assert workspace == (managed / workspace_id).resolve()
            assert cancelled is not None and cancelled() is False
            return AgentResult(
                status="EXECUTED",
                work_order_id=candidate.work_order_id,
                summary="durable workspace authority accepted",
                result_text="durable workspace authority accepted",
                verification={"status": "PASS"},
                acceptance={"decision": "ACCEPT"},
                review_required=False,
            )

    monkeypatch.setattr(
        agent_runtime_executor_module,
        "AgentExecutor",
        _CanonicalAgentExecutor,
    )
    proposal_executor = InternalAgentProposalExecutor(
        command_repository=type(
            "CommandReader",
            (),
            {
                "get": lambda self, command_id: (
                    {
                        "command": command,
                        "work_order": work_order,
                        "graph": graph,
                        "assignment": assignment,
                    }
                    if command_id == command.command_id
                    else None
                )
            },
        )(),
        executor=AgentRuntimeExecutor(
            artifact_store=AgentArtifactStore(tmp_path / "agent-runs"),
            workspace_runtime=workspace_runtime,
            model_attempt_service=object(),
            prompt_registry=object(),
        ),
    )
    frozen = FrozenNodeInput(
        command_id=command.command_id,
        request_id=command.request_id,
        session_id=command.session_id,
        principal_id=command.principal_id,
        work_order_id=work_order.work_order_id,
        root_work_order_id=work_order.root_work_order_id or work_order.work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        node_id=node.node_id,
        attempt_id="attempt-internal-durable-workspace",
        lease_id="lease-internal-durable-workspace",
        lease_owner_id=runtime_owner,
        fencing_token=7,
        node_risk_level=node.risk_level,
        route="delegate",
        target_id="agent_runtime",
        objective=work_order.objective,
        input_refs=tuple(work_order.input_refs),
        _input_pack_json=json.dumps(work_order.input_pack),
        _input_artifacts_json="[]",
        node_grant_ref="node-grant://internal/durable-workspace",
        parent_grant_ref="governor-grant://internal/durable-workspace",
        business_scope=command.business_scope,
        operation_fingerprint=command.operation_fingerprint,
    )

    events = [event async for event in proposal_executor.execute_stream(frozen)]

    assert events[-1]["decision"] == "APPROVE"
    authority = repository.get(workspace_id)
    assert authority.graph_id == graph.graph_id
    assert authority.root_work_order_id == work_order.root_work_order_id
    assert authority.owner_principal_id == command.principal_id
    assert authority.lease_fencing_token == 1


@pytest.mark.asyncio
async def test_internal_accepted_artifact_promotes_with_real_git_and_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "accepted-source"
    source.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "accepted-artifact@example.invalid"),
        ("config", "user.name", "Accepted Artifact Test"),
    ):
        completed = subprocess.run(
            ["git", "-C", str(source), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
    (source / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    for args in (("add", "baseline.txt"), ("commit", "-m", "initial")):
        completed = subprocess.run(
            ["git", "-C", str(source), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr

    db_path = tmp_path / "accepted-artifact.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="internal-accepted-artifact-migrate",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    request_id = "request-internal-accepted-artifact"
    principal_id = "principal-internal-accepted-artifact"
    body_sha256 = _sha256("internal-accepted-artifact")
    claim_ref = "ingress-claim://internal/accepted-artifact"
    audit_ref = "audit://secure-ingress/internal/accepted-artifact"
    _insert_secure_authority(
        db_path,
        request_id=request_id,
        principal_id=principal_id,
        body_sha256=body_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    suffix = hashlib.sha256(f"internal\n{request_id}".encode()).hexdigest()[:40]
    graph_id = f"graph-internal-{suffix}"
    workspace_id = DurableWorkspaceRuntime.workspace_id_for(
        graph_id=graph_id,
        graph_revision=1,
    )
    managed = tmp_path / "accepted-worktrees"
    managed.mkdir()
    workspace_repository = WorkspaceRepository(db_path)
    allocator = WorkspaceAllocator(
        repository=workspace_repository,
        managed_roots={
            "accepted-worktrees": ManagedWorkspaceRoot(
                root_ref="accepted-worktrees",
                path=managed,
                isolation_modes=frozenset({"DIRECTORY_FENCE"}),
            )
        },
        git_sources={
            "accepted-source": ManagedGitSource(
                source_key="accepted-source",
                source_ref_prefix="git-worktree://accepted-source",
                repository=source.resolve(),
            )
        },
    )
    workspace_runtime = DurableWorkspaceRuntime(
        repository=workspace_repository,
        allocator=allocator,
        runtime_owner_id="internal-accepted-owner",
        managed_root_ref="accepted-worktrees",
        git_source_key="accepted-source",
        lease_ms=30_000,
        heartbeat_interval_ms=1_000,
        token_secret=b"a" * 48,
    )
    artifacts = AgentArtifactStore(tmp_path / "accepted-agent-runs")
    agent_calls: list[str] = []

    class _CanonicalAgentExecutor:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def execute(self, candidate, *, workspace, cancelled=None):  # noqa: ANN001, ANN202
            assert workspace == (managed / workspace_id).resolve()
            assert cancelled is not None and cancelled() is False
            agent_calls.append(candidate.work_order_id)
            return AgentResult(
                status="EXECUTED",
                work_order_id=candidate.work_order_id,
                summary="result executed with real accepted artifact",
                result_text="result executed with real accepted artifact",
                verification={"status": "PASS"},
                acceptance={"decision": "ACCEPT"},
                review_required=False,
            )

    monkeypatch.setattr(
        agent_runtime_executor_module,
        "AgentExecutor",
        _CanonicalAgentExecutor,
    )
    ledger = SQLiteLedger(str(db_path))
    claim_store = SQLiteSecureIngressStore(db_path)
    runtime = None
    try:
        task_store = DurableTaskStore(db_path)
        event_store = RuntimeEventStore(db_path)
        infrastructure = SimpleNamespace(
            task_store=task_store,
            review_store=ReviewStore(db_path),
            review_continuation_store=ReviewContinuationStore(db_path),
            command_repository=HostCommandRepository(db_path),
            outbox_publisher=RuntimeOutboxPublisher(task_store, event_store),
            worker_projection_store=WorkerProjectionStore(db_path),
        )
        result_repository = AgentExecutionResultRepository(db_path)
        preview_authority = _ExactPreviewAuthority()
        token_service = TokenService(approval_ttl_ms=180_000)
        accepted_result = InternalAcceptedResultHandler(
            command_repository=infrastructure.command_repository,
            projection_store=infrastructure.worker_projection_store,
            result_repository=result_repository,
            artifact_store=artifacts,
            workspace_runtime=workspace_runtime,
            task_store=task_store,
        )
        dispatcher = AcceptanceEffectDispatcher(
            task_store=task_store,
            handlers={"internal.agent.result_commit": accepted_result},
            owner_id="internal-accepted-effect-owner",
            action_preview_service=preview_authority,
        )
        runtime = build_governed_internal_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            token_service=token_service,
            result_repository=result_repository,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id="internal-accepted-owner",
            canonical_executor=AgentRuntimeExecutor(
                artifact_store=artifacts,
                workspace_runtime=workspace_runtime,
                model_attempt_service=object(),
                prompt_registry=object(),
            ),
            runtime_context_application=_RuntimeContextAuthority(
                principal_id=principal_id
            ),
            action_preview_service=preview_authority,
            acceptance_effect_dispatcher=dispatcher,
        )
        await runtime.activate()
        service = InternalAgentCommandService(
            runtime=runtime,
            intent_engine=_IntentEngine(
                ledger,
                token_service,
            ),
            claim_result_store=claim_store,
        )
        terminal = await service.execute(
            work_order=AgentWorkOrder(
                work_order_id="work-order-internal-accepted-artifact",
                domain="code",
                run_mode="inspect",
                objective="promote one clean read-only result",
                workspace={
                    "strategy": "durable_workspace",
                    "workspace_id": workspace_id,
                },
                allowed_actions=["read_files"],
                forbidden_actions=["write_files"],
                verification_plan={
                    "required": True,
                    "commands": [["git", "status", "--porcelain"]],
                },
                review_policy={"review_required": False},
            ),
            request_id=request_id,
            session_id="session-internal-accepted-artifact",
            ts_ms=NOW,
            command_authority={
                "principal_id": principal_id,
                "claim_ref": claim_ref,
                "audit_ref": audit_ref,
                "body_sha256": body_sha256,
            },
        )

        assert terminal["decision"] == "APPROVE", json.dumps(
            terminal,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        assert terminal["internal_agent_effect_status"] == "COMMITTED"
        assert agent_calls == ["work-order-internal-accepted-artifact"]
        lifecycle = workspace_repository.get_lifecycle(workspace_id)
        assert lifecycle.state is WorkspaceLifecycleState.PROMOTED
        assert lifecycle.accepted_event is not None
        assert lifecycle.accepted_event.accepted_head
        command_id = terminal["execution_receipt"]["command_id"]
        durable_result = result_repository.get_by_command(command_id)
        assert durable_result is not None
        assert durable_result["result_status"] == "EXECUTED"

        with sqlite3.connect(str(db_path)) as conn:
            effect_id = str(
                conn.execute(
                    "SELECT effect_id FROM task_runtime_acceptance_effect_intent "
                    "WHERE effect_type = 'internal.agent.result_commit'"
                ).fetchone()[0]
            )
        effect = task_store.get_acceptance_effect_intent(effect_id)
        assert effect["status"] == "COMMITTED"
        durable_payload = dict(effect["payload"])
        assert durable_payload.pop("_action_preview_context")["schema_version"] == (
            "Natsume.acceptance_effect.action_preview_context.v1"
        )
        matching_preview = next(
            (
                (event_id, boundary, receipt)
                for event_id, (boundary, receipt) in preview_authority._records.items()
                if dict(boundary.get("authority_context") or {}).get("effect_id")
                == effect_id
            ),
            None,
        )
        assert matching_preview is not None
        event_id, boundary, receipt = matching_preview
        reconcile_payload = {
            **durable_payload,
            "_action_preview_context": {
                "schema_version": "Natsume.internal.action_preview_context.v2",
                "origin_authority_kind": "ACCEPTANCE_EFFECT",
                "origin_event_id": event_id,
                "origin_receipt_ref": receipt.receipt_ref,
                "origin_boundary": deepcopy(boundary),
            },
        }
        replay = await accepted_result.reconcile(reconcile_payload)
        assert replay["replayed"] is True
        assert replay["artifact_ref"] == lifecycle.latest_event.terminal_ref
        assert agent_calls == ["work-order-internal-accepted-artifact"]

        stage_authority = next(
            (tmp_path / "accepted-agent-runs" / "governed" / "accepted").glob(
                "*/*/owner.json"
            )
        )
        owner = json.loads(stage_authority.read_text(encoding="utf-8"))
        lease_id = owner["execution_authority"]["lease_id"]
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE task_runtime_lease SET owner_id = 'tampered-owner' "
                "WHERE lease_id = ?",
                (lease_id,),
            )
        with pytest.raises(InternalAcceptedResultError, match="exact durable"):
            await accepted_result.reconcile(reconcile_payload)
    finally:
        if runtime is not None:
            await runtime.aclose()
        claim_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_internal_post_checkpoint_budget_rejection_is_one_transaction_and_replay_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "internal-budget-rejected.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="internal-budget-rejected-migrate",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    request_id = "request-internal-budget-rejected"
    principal_id = "principal-internal-budget-rejected"
    body_sha256 = _sha256("internal-budget-rejected")
    claim_ref = "ingress-claim://internal/budget-rejected"
    audit_ref = "audit://secure-ingress/internal/budget-rejected"
    _insert_secure_authority(
        db_path,
        request_id=request_id,
        principal_id=principal_id,
        body_sha256=body_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    monkeypatch.setattr(
        internal_task_runtime_module,
        "_INTERNAL_AGENT_BUDGET_LIMITS",
        BudgetAmount(
            token_count=100,
            tool_call_count=1,
            time_ms=0,
            concurrency=1,
            retry_count=1,
            model_cost_micros=100_000,
        ),
    )

    clock = _MutableClock()
    durable_store = DurableTaskStore(db_path, clock=clock)
    response_loss_store = _RejectedBudgetResponseLossStore(durable_store)
    event_store = RuntimeEventStore(db_path)
    projection_store = WorkerProjectionStore(db_path, clock=clock)
    command_repository = HostCommandRepository(db_path, clock=clock)
    infrastructure = SimpleNamespace(
        task_store=response_loss_store,
        review_store=ReviewStore(db_path),
        review_continuation_store=ReviewContinuationStore(db_path),
        command_repository=command_repository,
        outbox_publisher=RuntimeOutboxPublisher(response_loss_store, event_store),
        worker_projection_store=projection_store,
    )
    ledger = SQLiteLedger(str(db_path))
    claim_store = SQLiteSecureIngressStore(db_path)
    runtime = None
    calls: list[str] = []

    class _ProtectedExecutor(_Executor):
        def execute(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            result = super().execute(*args, **kwargs)
            result["_protected_stage"] = {
                "schema_version": "Natsume.agent.governed_artifact_stage.v1",
                "ref": "agent-stage://internal-budget-rejected/sealed",
                "digest": "sha256:" + "b" * 64,
            }
            return result

    try:
        token_service = TokenService(approval_ttl_ms=180_000)
        preview_authority = _ExactPreviewAuthority()
        result_repository = AgentExecutionResultRepository(db_path)
        accepted_result = InternalAcceptedResultHandler(
            command_repository=command_repository,
            projection_store=projection_store,
            result_repository=result_repository,
        )
        runtime = build_governed_internal_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            token_service=token_service,
            result_repository=result_repository,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id="internal-budget-rejected-owner",
            canonical_executor=_ProtectedExecutor(calls),
            runtime_context_application=_RuntimeContextAuthority(
                principal_id=principal_id
            ),
            action_preview_service=preview_authority,
            acceptance_effect_dispatcher=AcceptanceEffectDispatcher(
                task_store=response_loss_store,
                handlers={"internal.agent.result_commit": accepted_result},
                owner_id="internal-budget-rejected-effect-owner",
                action_preview_service=preview_authority,
            ),
        )
        await runtime.activate()
        service = InternalAgentCommandService(
            runtime=runtime,
            intent_engine=_IntentEngine(ledger, token_service),
            claim_result_store=claim_store,
        )
        terminal = await service.execute(
            work_order=AgentWorkOrder(
                work_order_id="work-order-internal-budget-rejected",
                domain="code",
                run_mode="inspect",
                objective="prove atomic post-checkpoint budget rejection",
                acceptance_criteria=["result executed"],
                review_policy={"review_required": False},
            ),
            request_id=request_id,
            session_id="session-internal-budget-rejected",
            ts_ms=NOW,
            command_authority={
                "principal_id": principal_id,
                "claim_ref": claim_ref,
                "audit_ref": audit_ref,
                "body_sha256": body_sha256,
            },
        )

        assert terminal["execution_receipt"]["status"] == "BUDGET_BLOCKED"
        assert terminal["execution_receipt"]["accepted"] is False
        assert terminal["execution_receipt"]["reason_code"] == (
            "GOVERNED_BUDGET_EXCEEDED"
        )
        assert response_loss_store.reject_calls == 2
        assert calls == ["work-order-internal-budget-rejected"]
        admitted = command_repository.get_by_request(
            surface="internal",
            request_id=request_id,
        )
        assert admitted is not None
        command = admitted["command"]
        snapshot = durable_store.read_scheduler_snapshot(
            command.graph_id,
            command.graph_revision,
        )
        assert snapshot["graph_record"]["status"] == "BLOCKED"
        assert snapshot["graph_record"]["nodes"][0]["status"] == "FAILED"
        assert snapshot["attempts"][0]["status"] == "FAILED"
        assert snapshot["active_leases"] == []
        assert snapshot["budgets"][0]["status"] == "RELEASED"

        attempt = snapshot["attempts"][0]
        budget = snapshot["budgets"][0]
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            assert conn.execute(
                "SELECT status FROM task_runtime_lease WHERE attempt_id = ?",
                (attempt["attempt_id"],),
            ).fetchone()[0] == "RELEASED"
            assert conn.execute(
                "SELECT status FROM task_runtime_idempotency_reservation",
            ).fetchone()[0] == "COMMITTED"
            usages = conn.execute(
                "SELECT * FROM task_runtime_budget_usage WHERE budget_id = ?",
                (budget["budget_id"],),
            ).fetchall()
            assert len(usages) == 2
            rejected = [row for row in usages if int(row["accepted"]) == 0]
            assert len(rejected) == 1
            assert rejected[0]["reason_code"] == "TASK_STORE_BUDGET_EXCEEDED"
            assert rejected[0]["usage_id"] in tuple(
                terminal["execution_receipt"]["evidence_refs"]
            )
            terminal_outboxes = conn.execute(
                """
                SELECT aggregate_type, aggregate_version, event_type,
                       attempt_id, created_ts_ms
                FROM task_runtime_outbox
                WHERE attempt_id = ? AND event_type IN (
                    'TASK_ATTEMPT_FAILED', 'TASK_NODE_FAILED',
                    'TASK_GRAPH_BLOCKED', 'TASK_LEASE_RELEASED_TERMINAL',
                    'BUDGET_RESERVATION_RELEASED'
                )
                ORDER BY aggregate_type
                """,
                (attempt["attempt_id"],),
            ).fetchall()
            assert len(terminal_outboxes) == 5
            assert {int(row["created_ts_ms"]) for row in terminal_outboxes} == {
                int(attempt["completed_ts_ms"])
            }
        assert durable_store.list_terminal_reconciliation_inventory_candidates() == ()
    finally:
        if runtime is not None:
            await runtime.aclose()
        claim_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_internal_budget_rejection_rolls_back_every_terminal_mutation_fault(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "internal-budget-rejected-fault.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="internal-budget-rejected-fault-migrate",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    request_id = "request-internal-budget-rejected-fault"
    principal_id = "principal-internal-budget-rejected-fault"
    body_sha256 = _sha256("internal-budget-rejected-fault")
    claim_ref = "ingress-claim://internal/budget-rejected-fault"
    audit_ref = "audit://secure-ingress/internal/budget-rejected-fault"
    _insert_secure_authority(
        db_path,
        request_id=request_id,
        principal_id=principal_id,
        body_sha256=body_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    monkeypatch.setattr(
        internal_task_runtime_module,
        "_INTERNAL_AGENT_BUDGET_LIMITS",
        BudgetAmount(
            token_count=100,
            tool_call_count=1,
            time_ms=0,
            concurrency=1,
            retry_count=1,
            model_cost_micros=100_000,
        ),
    )
    clock = _MutableClock()
    store = _RejectedBudgetFaultStore(
        db_path,
        clock=clock,
        fail_event="TASK_ATTEMPT_FAILED",
    )
    event_store = RuntimeEventStore(db_path)
    projections = WorkerProjectionStore(db_path, clock=clock)
    commands = HostCommandRepository(db_path, clock=clock)
    infrastructure = SimpleNamespace(
        task_store=store,
        review_store=ReviewStore(db_path),
        review_continuation_store=ReviewContinuationStore(db_path),
        command_repository=commands,
        outbox_publisher=RuntimeOutboxPublisher(store, event_store),
        worker_projection_store=projections,
    )
    ledger = SQLiteLedger(str(db_path))
    claim_store = SQLiteSecureIngressStore(db_path)
    runtime = None

    class _ProtectedExecutor(_Executor):
        def execute(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            result = super().execute(*args, **kwargs)
            result["_protected_stage"] = {
                "schema_version": "Natsume.agent.governed_artifact_stage.v1",
                "ref": "agent-stage://internal-budget-rejected-fault/sealed",
                "digest": "sha256:" + "c" * 64,
            }
            return result

    try:
        token_service = TokenService(approval_ttl_ms=180_000)
        preview_authority = _ExactPreviewAuthority()
        result_repository = AgentExecutionResultRepository(db_path)
        accepted_result = InternalAcceptedResultHandler(
            command_repository=commands,
            projection_store=projections,
            result_repository=result_repository,
        )
        runtime = build_governed_internal_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            token_service=token_service,
            result_repository=result_repository,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id="internal-budget-rejected-fault-owner",
            canonical_executor=_ProtectedExecutor([]),
            runtime_context_application=_RuntimeContextAuthority(
                principal_id=principal_id
            ),
            action_preview_service=preview_authority,
            acceptance_effect_dispatcher=AcceptanceEffectDispatcher(
                task_store=store,
                handlers={"internal.agent.result_commit": accepted_result},
                owner_id="internal-budget-rejected-fault-effect-owner",
                action_preview_service=preview_authority,
            ),
        )
        await runtime.activate()
        service = InternalAgentCommandService(
            runtime=runtime,
            intent_engine=_IntentEngine(ledger, token_service),
            claim_result_store=claim_store,
        )
        with pytest.raises(RuntimeError, match="TASK_ATTEMPT_FAILED"):
            await service.execute(
                work_order=AgentWorkOrder(
                    work_order_id="work-order-internal-budget-rejected-fault",
                    domain="code",
                    run_mode="inspect",
                    objective="rollback every rejected budget terminal mutation",
                    acceptance_criteria=["result executed"],
                    review_policy={"review_required": False},
                ),
                request_id=request_id,
                session_id="session-internal-budget-rejected-fault",
                ts_ms=NOW,
                command_authority={
                    "principal_id": principal_id,
                    "claim_ref": claim_ref,
                    "audit_ref": audit_ref,
                    "body_sha256": body_sha256,
                },
            )
        assert store.captured_rejection is not None
        captured_args, captured_kwargs = store.captured_rejection

        def assert_pre_bundle_state() -> None:
            with sqlite3.connect(str(db_path)) as conn:
                assert conn.execute(
                    "SELECT status FROM task_runtime_graph"
                ).fetchone()[0] == "ACTIVE"
                assert conn.execute(
                    "SELECT status FROM task_runtime_node"
                ).fetchone()[0] == "RUNNING"
                assert conn.execute(
                    "SELECT status FROM task_runtime_attempt"
                ).fetchone()[0] == "RUNNING"
                assert conn.execute(
                    "SELECT status FROM task_runtime_lease ORDER BY fencing_token DESC"
                ).fetchone()[0] == "ACTIVE"
                assert conn.execute(
                    "SELECT status FROM task_runtime_budget_reservation"
                ).fetchone()[0] == "EXCEEDED"
                assert conn.execute(
                    "SELECT status FROM task_runtime_idempotency_reservation"
                ).fetchone()[0] == "COMMITTED"
                assert conn.execute(
                    "SELECT COUNT(*) FROM task_runtime_outbox WHERE event_type IN ("
                    "'TASK_ATTEMPT_FAILED','TASK_NODE_FAILED','TASK_GRAPH_BLOCKED',"
                    "'TASK_LEASE_RELEASED_TERMINAL','BUDGET_RESERVATION_RELEASED')"
                ).fetchone()[0] == 0

        assert_pre_bundle_state()
        fault_events = (
            "TASK_ATTEMPT_FAILED",
            "TASK_NODE_FAILED",
            "TASK_GRAPH_BLOCKED",
            "TASK_LEASE_RELEASED_TERMINAL",
            "BUDGET_RESERVATION_RELEASED",
        )
        for event_type in fault_events:
            store.fail_event = event_type
            with pytest.raises(RuntimeError, match=event_type):
                store.reject_internal_agent_checkpoint_budget_bundle(
                    *captured_args,
                    **captured_kwargs,
                )
            assert_pre_bundle_state()

        store.fail_event = "NO_FAULT"
        committed = store.reject_internal_agent_checkpoint_budget_bundle(
            *captured_args,
            **captured_kwargs,
        )
        replayed = store.reject_internal_agent_checkpoint_budget_bundle(
            *captured_args,
            **captured_kwargs,
        )
        assert committed["replayed"] is False
        assert replayed["replayed"] is True
        assert replayed["attempt"]["status"] == "FAILED"
        assert replayed["node"]["status"] == "FAILED"
        assert replayed["graph"]["status"] == "BLOCKED"
        assert replayed["lease"]["status"] == "RELEASED"
        assert replayed["budget"]["status"] == "RELEASED"
    finally:
        if runtime is not None:
            await runtime.aclose()
        claim_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_internal_real_sealed_budget_rejection_restarts_to_tombstone_and_clean_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "s"
    source.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "budget-rejection@example.invalid"),
        ("config", "user.name", "Budget Rejection Test"),
    ):
        completed = subprocess.run(
            ["git", "-C", str(source), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
    (source / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    for args in (("add", "baseline.txt"), ("commit", "-m", "initial")):
        completed = subprocess.run(
            ["git", "-C", str(source), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr

    db_path = tmp_path / "internal-real-budget-rejected.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="internal-real-budget-rejected-migrate",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    request_id = "request-internal-real-budget-rejected"
    principal_id = "principal-internal-real-budget-rejected"
    body_sha256 = _sha256("internal-real-budget-rejected")
    claim_ref = "ingress-claim://internal/real-budget-rejected"
    audit_ref = "audit://secure-ingress/internal/real-budget-rejected"
    _insert_secure_authority(
        db_path,
        request_id=request_id,
        principal_id=principal_id,
        body_sha256=body_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    monkeypatch.setattr(
        internal_task_runtime_module,
        "_INTERNAL_AGENT_BUDGET_LIMITS",
        BudgetAmount(
            token_count=100,
            tool_call_count=10,
            time_ms=60_000,
            concurrency=1,
            retry_count=1,
            model_cost_micros=100_000,
        ),
    )

    suffix = hashlib.sha256(f"internal\n{request_id}".encode()).hexdigest()[:40]
    graph_id = f"graph-internal-{suffix}"
    workspace_id = DurableWorkspaceRuntime.workspace_id_for(
        graph_id=graph_id,
        graph_revision=1,
    )
    managed = tmp_path / "w"
    managed.mkdir()
    # Keep the physical test roots short enough for the governed
    # Workspace/Attempt segments on Windows without weakening production ids.
    artifact_root = tmp_path / "a"
    clock = _MutableClock()
    token_secret = b"b" * 48
    agent_calls: list[str] = []
    runtimes: list[Any] = []

    class _CanonicalBudgetAgentExecutor:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def execute(self, candidate, *, workspace, cancelled=None):  # noqa: ANN001, ANN202
            assert workspace == (managed / workspace_id).resolve()
            assert cancelled is not None and cancelled() is False
            agent_calls.append(candidate.work_order_id)
            return AgentResult(
                status="EXECUTED",
                work_order_id=candidate.work_order_id,
                summary="result executed before exact budget rejection",
                result_text="result executed before exact budget rejection",
                verification={"status": "PASS"},
                acceptance={"decision": "ACCEPT"},
                review_required=False,
                metadata={
                    "model_attempts": [
                        {
                            "schema_version": (
                                "Natsume.model_runtime.budget_measurement.v1"
                            ),
                            "measurement_ref": (
                                "model-budget://internal-real-budget-rejected/1"
                            ),
                            "attempt_count": 1,
                            "retry_count": 0,
                            "token_count": 101,
                            "model_cost_micros": 10_000,
                        }
                    ],
                    "trace": {
                        "schema_version": "nle.trace.redacted.v1",
                        "payload_hash": "sha256:" + "d" * 64,
                        "steps": [],
                    },
                },
            )

    monkeypatch.setattr(
        agent_runtime_executor_module,
        "AgentExecutor",
        _CanonicalBudgetAgentExecutor,
    )

    def build_workspace_runtime(
        *, owner_id: str
    ) -> tuple[WorkspaceRepository, DurableWorkspaceRuntime]:
        repository = WorkspaceRepository(db_path, clock=clock)
        runtime = DurableWorkspaceRuntime(
            repository=repository,
            allocator=WorkspaceAllocator(
                repository=repository,
                managed_roots={
                    "budget-rejection-worktrees": ManagedWorkspaceRoot(
                        root_ref="budget-rejection-worktrees",
                        path=managed,
                        isolation_modes=frozenset({"DIRECTORY_FENCE"}),
                    )
                },
                git_sources={
                    "budget-rejection-source": ManagedGitSource(
                        source_key="budget-rejection-source",
                        source_ref_prefix="git-worktree://budget-rejection-source",
                        repository=source.resolve(),
                    )
                },
            ),
            runtime_owner_id=owner_id,
            managed_root_ref="budget-rejection-worktrees",
            git_source_key="budget-rejection-source",
            lease_ms=60_000,
            heartbeat_interval_ms=1_000,
            token_secret=token_secret,
        )
        return repository, runtime

    ledger = SQLiteLedger(str(db_path))
    claim_store = SQLiteSecureIngressStore(db_path)
    token_service = TokenService(approval_ttl_ms=180_000)
    intent_engine = _IntentEngine(ledger, token_service)
    preview_authority = _ExactPreviewAuthority()
    result_repository = AgentExecutionResultRepository(db_path)

    def build_runtime(
        *,
        owner_id: str,
        task_store: DurableTaskStore,
        workspace_runtime: DurableWorkspaceRuntime,
        artifacts: AgentArtifactStore,
    ) -> Any:
        command_repository = HostCommandRepository(db_path, clock=clock)
        projections = WorkerProjectionStore(db_path, clock=clock)
        event_store = RuntimeEventStore(db_path)
        infrastructure = SimpleNamespace(
            task_store=task_store,
            review_store=ReviewStore(db_path),
            review_continuation_store=ReviewContinuationStore(db_path),
            command_repository=command_repository,
            outbox_publisher=RuntimeOutboxPublisher(task_store, event_store),
            worker_projection_store=projections,
        )
        accepted_result = InternalAcceptedResultHandler(
            command_repository=command_repository,
            projection_store=projections,
            result_repository=result_repository,
            artifact_store=artifacts,
            workspace_runtime=workspace_runtime,
            task_store=task_store,
        )
        dispatcher = AcceptanceEffectDispatcher(
            task_store=task_store,
            handlers={"internal.agent.result_commit": accepted_result},
            owner_id=f"{owner_id}-effect",
            action_preview_service=preview_authority,
        )
        canonical_executor = AgentRuntimeExecutor(
            artifact_store=artifacts,
            workspace_runtime=workspace_runtime,
            model_attempt_service=object(),
            prompt_registry=object(),
        )
        return build_governed_internal_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            token_service=token_service,
            result_repository=result_repository,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id=owner_id,
            canonical_executor=canonical_executor,
            runtime_context_application=_RuntimeContextAuthority(
                principal_id=principal_id
            ),
            action_preview_service=preview_authority,
            acceptance_effect_dispatcher=dispatcher,
            artifact_store=artifacts,
            workspace_runtime=workspace_runtime,
            claim_result_store=claim_store,
        )

    work_order = AgentWorkOrder(
        work_order_id="work-order-internal-real-budget-rejected",
        domain="code",
        run_mode="inspect",
        objective="seal one real result before exact budget rejection",
        workspace={
            "strategy": "durable_workspace",
            "workspace_id": workspace_id,
        },
        allowed_actions=["read_files"],
        forbidden_actions=["write_files"],
        verification_plan={
            "required": True,
            "commands": [["git", "status", "--porcelain"]],
        },
        acceptance_criteria=["result executed"],
        review_policy={"review_required": False},
    )
    authority = {
        "principal_id": principal_id,
        "claim_ref": claim_ref,
        "audit_ref": audit_ref,
        "body_sha256": body_sha256,
    }
    try:
        first_store = DurableTaskStore(db_path, clock=clock)
        first_workspace_repository, first_workspace_runtime = build_workspace_runtime(
            owner_id="internal-real-budget-rejected-owner-a"
        )
        first_artifacts = AgentArtifactStore(artifact_root)
        first_runtime = build_runtime(
            owner_id="internal-real-budget-rejected-owner-a",
            task_store=first_store,
            workspace_runtime=first_workspace_runtime,
            artifacts=first_artifacts,
        )
        runtimes.append(first_runtime)
        await first_runtime.activate()

        # Freeze the continuation worker to model an abrupt process stop after
        # the atomic Task terminal bundle but before artifact/workspace cleanup.
        first_terminal_worker = first_runtime._terminal_continuation_worker
        assert first_terminal_worker is not None
        first_terminal_worker.stop()
        first_terminal_worker.join()
        first_runtime._terminal_worker_quiesced = True

        first_service = InternalAgentCommandService(
            runtime=first_runtime,
            intent_engine=intent_engine,
            claim_result_store=claim_store,
        )
        terminal = await first_service.execute(
            work_order=work_order,
            request_id=request_id,
            session_id="session-internal-real-budget-rejected",
            ts_ms=NOW,
            command_authority=authority,
        )
        assert terminal["execution_receipt"]["status"] == "BUDGET_BLOCKED"
        assert terminal["execution_receipt"]["reason_code"] == (
            "GOVERNED_BUDGET_EXCEEDED"
        )
        assert agent_calls == ["work-order-internal-real-budget-rejected"]

        admitted = HostCommandRepository(db_path, clock=clock).get_by_request(
            surface="internal",
            request_id=request_id,
        )
        assert admitted is not None
        command = admitted["command"]
        snapshot = first_store.read_scheduler_snapshot(
            command.graph_id,
            command.graph_revision,
        )
        assert snapshot["graph_record"]["status"] == "BLOCKED"
        assert snapshot["graph_record"]["nodes"][0]["status"] == "FAILED"
        assert snapshot["attempts"][0]["status"] == "FAILED"
        assert snapshot["active_leases"] == []
        assert snapshot["budgets"][0]["status"] == "RELEASED"
        with sqlite3.connect(str(db_path)) as conn:
            assert conn.execute(
                "SELECT status FROM task_runtime_lease WHERE attempt_id = ?",
                (snapshot["attempts"][0]["attempt_id"],),
            ).fetchone()[0] == "RELEASED"
            rejected_usage = conn.execute(
                "SELECT accepted, reason_code, delta_json "
                "FROM task_runtime_budget_usage WHERE accepted = 0"
            ).fetchall()
            assert len(rejected_usage) == 1
            assert rejected_usage[0][0] == 0
            assert rejected_usage[0][1] == "TASK_STORE_BUDGET_EXCEEDED"
            assert json.loads(rejected_usage[0][2])["token_count"] == 101

        staging = first_artifacts.inventory_governed_seals(
            namespace="staging",
            limit=10,
        )
        assert len(staging) == 1
        sealed = staging[0]
        assert sealed.workspace_id == workspace_id
        assert first_artifacts.inventory_governed_seals(
            namespace="accepted",
            limit=10,
        ) == ()
        assert first_workspace_repository.get_lifecycle(workspace_id).state is (
            WorkspaceLifecycleState.ACTIVE
        )
        assert first_workspace_repository.get(workspace_id).status == "ACTIVE"
        assert (managed / workspace_id).is_dir()

        await first_runtime.aclose()
        runtimes.remove(first_runtime)
        clock.advance(60_001)

        reopened_store = DurableTaskStore(db_path, clock=clock)
        reopened_workspace_repository, reopened_workspace_runtime = (
            build_workspace_runtime(
                owner_id="internal-real-budget-rejected-owner-b"
            )
        )
        reopened_artifacts = AgentArtifactStore(artifact_root)
        reopened_runtime = build_runtime(
            owner_id="internal-real-budget-rejected-owner-b",
            task_store=reopened_store,
            workspace_runtime=reopened_workspace_runtime,
            artifacts=reopened_artifacts,
        )
        runtimes.append(reopened_runtime)
        await reopened_runtime.activate()

        assert agent_calls == ["work-order-internal-real-budget-rejected"]
        assert reopened_artifacts.inventory_governed_seals(
            namespace="staging",
            limit=10,
        ) == ()
        assert reopened_artifacts.inventory_governed_seals(
            namespace="accepted",
            limit=10,
        ) == ()
        lifecycle = reopened_workspace_repository.get_lifecycle(workspace_id)
        assert lifecycle.state is WorkspaceLifecycleState.ABANDONED
        assert lifecycle.latest_event is not None
        assert lifecycle.latest_event.reason_code == "TERMINAL_BUDGET_REJECTED"
        tombstone_ref = reopened_artifacts.inspect_governed_tombstone(
            stage_ref=sealed.stage_ref,
            seal_digest=sealed.seal_digest,
            reason_code="TERMINAL_BUDGET_REJECTED",
        )
        assert tombstone_ref == lifecycle.latest_event.terminal_ref
        assert reopened_workspace_repository.get(workspace_id).status == "CLEANED"
        assert not (managed / workspace_id).exists()
        assert reopened_store.list_terminal_reconciliation_inventory_candidates() == ()

        reopened_service = InternalAgentCommandService(
            runtime=reopened_runtime,
            intent_engine=intent_engine,
            claim_result_store=claim_store,
        )
        replay = await reopened_service.execute(
            work_order=work_order,
            request_id=request_id,
            session_id="session-internal-real-budget-rejected",
            ts_ms=NOW + 60_002,
            command_authority=authority,
        )
        assert replay["replayed"] is True
        assert replay["execution_receipt"]["status"] == "BUDGET_BLOCKED"
        assert agent_calls == ["work-order-internal-real-budget-rejected"]
        assert reopened_artifacts.inspect_governed_tombstone_ref(
            tombstone_ref
        )["reason_code"] == "TERMINAL_BUDGET_REJECTED"
    finally:
        for runtime in reversed(runtimes):
            await runtime.aclose()
        claim_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_internal_seal_recovers_projection_after_restart_without_agent_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "recovery-source"
    source.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "seal-recovery@example.invalid"),
        ("config", "user.name", "Seal Recovery Test"),
    ):
        completed = subprocess.run(
            ["git", "-C", str(source), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
    (source / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    for args in (("add", "baseline.txt"), ("commit", "-m", "initial")):
        completed = subprocess.run(
            ["git", "-C", str(source), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr

    db_path = tmp_path / "internal-seal-recovery.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="internal-seal-recovery-migrate",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    request_id = "request-internal-seal-recovery"
    principal_id = "principal-internal-seal-recovery"
    body_sha256 = _sha256("internal-seal-recovery")
    claim_ref = "ingress-claim://internal/seal-recovery"
    audit_ref = "audit://secure-ingress/internal/seal-recovery"
    _insert_secure_authority(
        db_path,
        request_id=request_id,
        principal_id=principal_id,
        body_sha256=body_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    suffix = hashlib.sha256(f"internal\n{request_id}".encode()).hexdigest()[:40]
    graph_id = f"graph-internal-{suffix}"
    workspace_id = DurableWorkspaceRuntime.workspace_id_for(
        graph_id=graph_id,
        graph_revision=1,
    )
    managed = tmp_path / "recovery-worktrees"
    managed.mkdir()
    workspace_repository = WorkspaceRepository(db_path)
    workspace_runtime = DurableWorkspaceRuntime(
        repository=workspace_repository,
        allocator=WorkspaceAllocator(
            repository=workspace_repository,
            managed_roots={
                "recovery-worktrees": ManagedWorkspaceRoot(
                    root_ref="recovery-worktrees",
                    path=managed,
                    isolation_modes=frozenset({"DIRECTORY_FENCE"}),
                )
            },
            git_sources={
                "recovery-source": ManagedGitSource(
                    source_key="recovery-source",
                    source_ref_prefix="git-worktree://recovery-source",
                    repository=source.resolve(),
                )
            },
        ),
        runtime_owner_id="internal-seal-recovery-owner-a",
        managed_root_ref="recovery-worktrees",
        git_source_key="recovery-source",
        lease_ms=300_000,
        heartbeat_interval_ms=1_000,
        token_secret=b"r" * 48,
    )
    artifacts = AgentArtifactStore(tmp_path / "recovery-agent-runs")
    agent_calls: list[str] = []

    class _CanonicalAgentExecutor:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def execute(self, candidate, *, workspace, cancelled=None):  # noqa: ANN001, ANN202
            assert workspace == (managed / workspace_id).resolve()
            assert cancelled is not None and cancelled() is False
            agent_calls.append(candidate.work_order_id)
            return AgentResult(
                status="EXECUTED",
                work_order_id=candidate.work_order_id,
                summary="result executed and recover one exact sealed result",
                result_text="result executed and recover one exact sealed result",
                verification={"status": "PASS"},
                acceptance={"decision": "ACCEPT"},
                review_required=False,
                metadata={
                    "trace": {
                        "schema_version": "nle.trace.redacted.v1",
                        "payload_hash": "sha256:" + "a" * 64,
                        "steps": [],
                    }
                },
            )

    monkeypatch.setattr(
        agent_runtime_executor_module,
        "AgentExecutor",
        _CanonicalAgentExecutor,
    )
    canonical_executor = AgentRuntimeExecutor(
        artifact_store=artifacts,
        workspace_runtime=workspace_runtime,
        model_attempt_service=object(),
        prompt_registry=object(),
    )
    clock = _MutableClock()
    ledger = SQLiteLedger(str(db_path))
    claim_store = SQLiteSecureIngressStore(db_path)
    token_service = TokenService(approval_ttl_ms=180_000)
    intent_engine = _IntentEngine(ledger, token_service)
    preview_authority = _ExactPreviewAuthority()
    result_repository = AgentExecutionResultRepository(db_path)
    projection_delegate = WorkerProjectionStore(db_path, clock=clock)
    crashing_projections = _CrashBeforeProjectionStore(projection_delegate)
    runtimes: list[Any] = []

    def build_runtime(
        *,
        store: Any,
        projections: Any,
        owner_id: str,
    ) -> Any:
        command_repository = HostCommandRepository(db_path, clock=clock)
        event_store = RuntimeEventStore(db_path)
        infrastructure = SimpleNamespace(
            task_store=store,
            review_store=ReviewStore(db_path),
            review_continuation_store=ReviewContinuationStore(db_path),
            command_repository=command_repository,
            outbox_publisher=RuntimeOutboxPublisher(store, event_store),
            worker_projection_store=projections,
        )
        accepted_result = InternalAcceptedResultHandler(
            command_repository=command_repository,
            projection_store=projections,
            result_repository=result_repository,
            artifact_store=artifacts,
            workspace_runtime=workspace_runtime,
            task_store=store,
        )
        dispatcher = AcceptanceEffectDispatcher(
            task_store=store,
            handlers={"internal.agent.result_commit": accepted_result},
            owner_id=f"{owner_id}-effect",
            action_preview_service=preview_authority,
        )
        return build_governed_internal_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            token_service=token_service,
            result_repository=result_repository,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id=owner_id,
            canonical_executor=canonical_executor,
            runtime_context_application=_RuntimeContextAuthority(
                principal_id=principal_id
            ),
            action_preview_service=preview_authority,
            acceptance_effect_dispatcher=dispatcher,
            artifact_store=artifacts,
            workspace_runtime=workspace_runtime,
            claim_result_store=claim_store,
        )

    work_order = AgentWorkOrder(
        work_order_id="work-order-internal-seal-recovery",
        domain="code",
        run_mode="inspect",
        objective="recover one exact sealed result",
        workspace={
            "strategy": "durable_workspace",
            "workspace_id": workspace_id,
        },
        allowed_actions=["read_files"],
        forbidden_actions=["write_files"],
        verification_plan={
            "required": True,
            "commands": [["git", "status", "--porcelain"]],
        },
        review_policy={"review_required": False},
    )
    authority = {
        "principal_id": principal_id,
        "claim_ref": claim_ref,
        "audit_ref": audit_ref,
        "body_sha256": body_sha256,
    }
    try:
        first_store = DurableTaskStore(db_path, clock=clock)
        first_runtime = build_runtime(
            store=first_store,
            projections=crashing_projections,
            owner_id="internal-seal-recovery-owner-a",
        )
        runtimes.append(first_runtime)
        await first_runtime.activate()
        first_service = InternalAgentCommandService(
            runtime=first_runtime,
            intent_engine=intent_engine,
            claim_result_store=claim_store,
        )
        with pytest.raises(_SimulatedProjectionProcessStop):
            await first_service.execute(
                work_order=work_order,
                request_id=request_id,
                session_id="session-internal-seal-recovery",
                ts_ms=NOW,
                command_authority=authority,
            )
        await first_runtime.aclose()
        runtimes.remove(first_runtime)

        assert agent_calls == ["work-order-internal-seal-recovery"]
        assert crashing_projections.stage_calls == 1
        sealed = artifacts.inventory_governed_seals(
            namespace="staging",
            limit=10,
        )
        assert len(sealed) == 1
        admitted = HostCommandRepository(db_path, clock=clock).get_by_request(
            surface="internal",
            request_id=request_id,
        )
        assert admitted is not None
        command = admitted["command"]
        admitted_execution = AdmittedExecution.from_contracts(
            command=command,
            assignment=admitted["assignment"],
            graph=admitted["graph"],
        )
        snapshot = first_store.read_scheduler_snapshot(graph_id, 1)
        original_attempt = snapshot["attempts"][0]
        assert original_attempt["status"] == "RUNNING"
        reservation = first_store.get_operation_reservation(
            admitted_execution.reservation_id
        )
        assert reservation["status"] == "RESERVED"

        clock.advance(120_001)
        reopened_store = DurableTaskStore(db_path, clock=clock)
        recovery_report = reopened_store.recover_startup(
            recovery_id="internal-seal-recovery-startup"
        )
        assert original_attempt["attempt_id"] in recovery_report.recovered_attempt_ids
        response_loss_store = _RecoveredCheckpointResponseLossStore(
            reopened_store
        )
        succeeded_crash_store = _CrashAfterSucceededAttemptStore(
            response_loss_store
        )
        interrupted_runtime = build_runtime(
            store=succeeded_crash_store,
            projections=projection_delegate,
            owner_id="internal-seal-recovery-owner-b",
        )
        with pytest.raises(_SimulatedAttemptSucceededProcessStop):
            await interrupted_runtime.activate()
        assert succeeded_crash_store.crash_count == 1
        interrupted_snapshot = reopened_store.read_scheduler_snapshot(graph_id, 1)
        assert interrupted_snapshot["graph_record"]["status"] == "ACTIVE"
        assert interrupted_snapshot["graph_record"]["nodes"][0]["status"] == "RUNNING"
        assert interrupted_snapshot["attempts"][0]["status"] == "SUCCEEDED"
        assert response_loss_store.commit_calls == 1

        reopened_runtime = build_runtime(
            store=response_loss_store,
            projections=projection_delegate,
            owner_id="internal-seal-recovery-owner-c",
        )
        runtimes.append(reopened_runtime)
        await reopened_runtime.activate()
        assert reopened_runtime.startup_checkpoint_report == (
            {
                "stage_ref": sealed[0].stage_ref,
                "status": "COMPLETED",
                "reason_code": None,
            },
        )
        assert agent_calls == ["work-order-internal-seal-recovery"]
        assert response_loss_store.commit_calls == 1
        committed = reopened_store.get_operation_reservation(
            reservation["reservation_id"]
        )
        assert committed["status"] == "COMMITTED"
        assert str(committed["receipt_ref"]).startswith("worker-projection://")
        final_snapshot = reopened_store.read_scheduler_snapshot(graph_id, 1)
        assert final_snapshot["graph_record"]["status"] == "COMPLETED"
        assert final_snapshot["graph_record"]["nodes"][0]["status"] == "ACCEPTED"
        assert final_snapshot["attempts"][0]["status"] == "SUCCEEDED"
        assert artifacts.inventory_governed_seals(
            namespace="staging",
            limit=10,
        ) == ()
        accepted = artifacts.inventory_governed_seals(
            namespace="accepted",
            limit=10,
        )
        assert len(accepted) == 1
        promotion = artifacts.inspect_governed_promotion(
            stage_ref=accepted[0].stage_ref,
            seal_digest=accepted[0].seal_digest,
        )
        lifecycle = workspace_repository.get_lifecycle(workspace_id)
        assert promotion["accepted_ref"] == lifecycle.latest_event.terminal_ref
        assert workspace_repository.get(workspace_id).status == "CLEANED"
        assert not (managed / workspace_id).exists()
        with sqlite3.connect(str(db_path)) as conn:
            assert conn.execute(
                "SELECT result_ref FROM secure_ingress_idempotency_claim "
                "WHERE claim_ref = ?",
                (claim_ref,),
            ).fetchone()[0] is not None
            assert conn.execute(
                "SELECT COUNT(*) FROM agent_execution_result WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()[0] == 1

        # The first client call after activation is now only an idempotent replay.
        reopened_service = InternalAgentCommandService(
            runtime=reopened_runtime,
            intent_engine=intent_engine,
            claim_result_store=claim_store,
        )
        terminal = await reopened_service.execute(
            work_order=work_order,
            request_id=request_id,
            session_id="session-internal-seal-recovery",
            ts_ms=NOW + 120_001,
            command_authority=authority,
        )

        assert terminal["decision"] == "APPROVE", json.dumps(
            terminal,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        assert terminal["execution_receipt"]["accepted"] is True
        assert agent_calls == ["work-order-internal-seal-recovery"]
        assert response_loss_store.commit_calls == 1
        with sqlite3.connect(str(db_path)) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM task_runtime_worker_projection "
                "WHERE attempt_id = ?",
                (original_attempt["attempt_id"],),
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT COUNT(*) FROM task_runtime_outbox "
                "WHERE aggregate_type = 'OPERATION_RESERVATION' "
                "AND aggregate_id = ? AND event_type = 'OPERATION_COMMITTED'",
                (reservation["reservation_id"],),
            ).fetchone()[0] == 1
        replay = await reopened_service.execute(
            work_order=work_order,
            request_id=request_id,
            session_id="session-internal-seal-recovery",
            ts_ms=NOW + 120_002,
            command_authority=authority,
        )
        assert replay["replayed"] is True
        assert agent_calls == ["work-order-internal-seal-recovery"]
        assert response_loss_store.commit_calls == 1

        historical_authority = dict(accepted[0].execution_authority)
        lineage_kwargs = {
            "graph_id": graph_id,
            "graph_revision": 1,
            "node_id": str(historical_authority["node_id"]),
            "attempt_id": str(historical_authority["attempt_id"]),
            "historical_lease_id": str(historical_authority["lease_id"]),
            "historical_lease_owner": str(
                historical_authority["lease_owner_id"]
            ),
            "historical_fencing_token": int(
                historical_authority["fencing_token"]
            ),
        }
        lineage = reopened_store.resolve_internal_agent_completion_lineage(
            **lineage_kwargs
        )
        assert lineage["recovered"] is True
        final_lease = dict(lineage["completion_lease"])
        historical_lease = dict(lineage["historical_lease"])

        def _tamper_then_restore(
            tamper_sql: str,
            tamper_params: tuple[Any, ...],
            restore_sql: str,
            restore_params: tuple[Any, ...],
        ) -> None:
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute(tamper_sql, tamper_params)
                conn.commit()
            try:
                with pytest.raises(TaskStoreError):
                    reopened_store.resolve_internal_agent_completion_lineage(
                        **lineage_kwargs
                    )
            finally:
                with sqlite3.connect(str(db_path)) as conn:
                    conn.execute(restore_sql, restore_params)
                    conn.commit()

        _tamper_then_restore(
            "UPDATE task_runtime_lease SET fencing_token = ? WHERE lease_id = ?",
            (int(final_lease["fencing_token"]) + 1, final_lease["lease_id"]),
            "UPDATE task_runtime_lease SET fencing_token = ? WHERE lease_id = ?",
            (int(final_lease["fencing_token"]), final_lease["lease_id"]),
        )
        _tamper_then_restore(
            "UPDATE task_runtime_lease SET status = 'RELEASED' WHERE lease_id = ?",
            (historical_lease["lease_id"],),
            "UPDATE task_runtime_lease SET status = 'EXPIRED' WHERE lease_id = ?",
            (historical_lease["lease_id"],),
        )
        _tamper_then_restore(
            "UPDATE task_runtime_lease SET owner_id = ? WHERE lease_id = ?",
            ("tampered-owner", final_lease["lease_id"]),
            "UPDATE task_runtime_lease SET owner_id = ? WHERE lease_id = ?",
            (final_lease["owner_id"], final_lease["lease_id"]),
        )
        _tamper_then_restore(
            "UPDATE host_command_journal SET target_id = ? WHERE command_id = ?",
            ("tampered-target", command.command_id),
            "UPDATE host_command_journal SET target_id = ? WHERE command_id = ?",
            ("agent_runtime", command.command_id),
        )
        projection_id = str(committed["receipt_ref"])
        _tamper_then_restore(
            "UPDATE task_runtime_worker_projection SET target_id = ? "
            "WHERE projection_id = ?",
            ("tampered-target", projection_id),
            "UPDATE task_runtime_worker_projection SET target_id = ? "
            "WHERE projection_id = ?",
            ("agent_runtime", projection_id),
        )

        rogue_lease_id = "lease-internal-seal-recovery-rogue"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """
                INSERT INTO task_runtime_lease(
                    lease_id, attempt_id, owner_id, fencing_token,
                    acquired_ts_ms, heartbeat_ts_ms, expires_ts_ms,
                    released_ts_ms, status, status_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RELEASED', 1)
                """,
                (
                    rogue_lease_id,
                    final_lease["attempt_id"],
                    final_lease["owner_id"],
                    int(final_lease["fencing_token"]) + 1,
                    int(final_lease["released_ts_ms"]) + 1,
                    int(final_lease["released_ts_ms"]) + 1,
                    int(final_lease["released_ts_ms"]) + 2,
                    int(final_lease["released_ts_ms"]) + 1,
                ),
            )
            conn.commit()
        try:
            with pytest.raises(TaskStoreError):
                reopened_store.resolve_internal_agent_completion_lineage(
                    **lineage_kwargs
                )
        finally:
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute(
                    "DELETE FROM task_runtime_lease WHERE lease_id = ?",
                    (rogue_lease_id,),
                )
                conn.commit()
    finally:
        for runtime in reversed(runtimes):
            await runtime.aclose()
        claim_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_internal_work_order_is_governed_durable_and_replay_safe(tmp_path: Path) -> None:
    db_path = tmp_path / "internal-runtime.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw02-internal-runtime",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    request_id = "request-internal-runtime"
    principal_id = "principal-internal-runtime"
    body_sha256 = _sha256("internal-request-body")
    claim_ref = "ingress-claim://internal/runtime"
    audit_ref = "audit://secure-ingress/internal/runtime"
    _insert_secure_authority(
        db_path,
        request_id=request_id,
        principal_id=principal_id,
        body_sha256=body_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )

    ledger = SQLiteLedger(str(db_path))
    runtime = None
    claim_store = SQLiteSecureIngressStore(db_path)
    try:
        task_store = DurableTaskStore(db_path)
        event_store = RuntimeEventStore(db_path)
        review_store = ReviewStore(db_path)
        infrastructure = SimpleNamespace(
            task_store=task_store,
            review_store=review_store,
            review_continuation_store=ReviewContinuationStore(db_path),
            command_repository=HostCommandRepository(db_path),
            outbox_publisher=RuntimeOutboxPublisher(task_store, event_store),
            worker_projection_store=WorkerProjectionStore(db_path),
        )
        result_repository = AgentExecutionResultRepository(db_path)
        calls: list[str] = []
        executor = _Executor(calls)
        def _assert_result_not_published(order: AgentWorkOrder) -> None:
            with sqlite3.connect(str(db_path)) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM agent_execution_result "
                    "WHERE work_order_id = ?",
                    (order.work_order_id,),
                ).fetchone()[0]
            assert count == 0

        executor.before_return = _assert_result_not_published
        token_service = TokenService(approval_ttl_ms=180_000)
        runtime_context = _RuntimeContextAuthority(principal_id=principal_id)
        preview_authority = _ExactPreviewAuthority()
        accepted_result = InternalAcceptedResultHandler(
            command_repository=infrastructure.command_repository,
            projection_store=infrastructure.worker_projection_store,
            result_repository=result_repository,
        )
        acceptance_effect_dispatcher = AcceptanceEffectDispatcher(
            task_store=task_store,
            handlers={"internal.agent.result_commit": accepted_result},
            owner_id="internal-integration-acceptance-effect-owner",
            action_preview_service=preview_authority,
        )
        runtime = build_governed_internal_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            token_service=token_service,
            result_repository=result_repository,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id="internal-owner",
            canonical_executor=executor,
            runtime_context_application=runtime_context,
            action_preview_service=preview_authority,
            acceptance_effect_dispatcher=acceptance_effect_dispatcher,
        )
        await runtime.activate()
        intent_engine = _IntentEngine(ledger, token_service)
        service = InternalAgentCommandService(
            runtime=runtime,
            intent_engine=intent_engine,
            claim_result_store=claim_store,
        )
        work_order = AgentWorkOrder(
            work_order_id="work-order-internal-runtime",
            domain="code",
            run_mode="inspect",
            objective="inspect governed internal runtime",
            input_pack={"input_ref": "input://internal/runtime"},
            allowed_actions=[],
            forbidden_actions=["write_files"],
            acceptance_criteria=["durable result exists"],
            metadata={
                "runtime_assignment": {"executor": "legacy"},
                "confirmed_owner": "caller-controlled",
            },
        )
        authority = {
            "principal_id": principal_id,
            "claim_ref": claim_ref,
            "audit_ref": audit_ref,
            "body_sha256": body_sha256,
        }
        first = await service.execute(
            work_order=work_order,
            request_id=request_id,
            session_id="session-internal-runtime",
            ts_ms=NOW,
            command_authority=authority,
        )
        replay = await service.execute(
            work_order=work_order,
            request_id=request_id,
            session_id="session-internal-runtime",
            ts_ms=NOW + 5_000,
            command_authority=authority,
        )

        assert first["decision"] == "APPROVE"
        assert first["execution_receipt"]["executor"] == "canonical"
        assert first["execution_receipt"]["worker_route"] == "delegate:agent_runtime"
        assert first["execution_receipt"]["status"] == "ACCEPTED"
        assert first["execution_receipt"]["verification_status"] == "PASS"
        assert first["execution_receipt"]["accepted"] is True
        assert first["internal_agent_effect_status"] == "COMMITTED"
        assert first["result"]["result_text"] == "durable internal result"
        assert replay["replayed"] is True
        assert replay["result"]["result_text"] == "durable internal result"
        assert calls == ["work-order-internal-runtime"]
        assert intent_engine.calls == 1
        command_id = first["execution_receipt"]["command_id"]
        durable = result_repository.get_by_command(command_id)
        assert durable is not None
        assert durable["result_status"] == "EXECUTED"
        admitted = HostCommandRepository(db_path).get(command_id)
        assert admitted is not None
        assert isinstance(admitted["work_order"], WorkOrderV3)
        assert admitted["work_order"].budget_policy is not None
        assert (
            admitted["work_order"].budget_policy.on_exceeded
            is BudgetExceededAction.BLOCK
        )
        assert all(
            value > 0
            for value in admitted["work_order"].budget_policy.limits.to_dict().values()
        )
        assert admitted["graph"].nodes[0].route == "delegate:agent_runtime"
        assert "runtime_assignment" not in admitted["work_order"].metadata
        assert "confirmed_owner" not in admitted["work_order"].metadata
        snapshot = task_store.read_scheduler_snapshot(
            admitted["command"].graph_id,
            admitted["command"].graph_revision,
        )
        assert snapshot["graph_record"]["status"] == "COMPLETED"
        assert snapshot["graph_record"]["nodes"][0]["status"] == "ACCEPTED"
        assert len(snapshot["attempts"]) == 1
        assert snapshot["budgets"][0]["status"] == "CONSUMED"
        assert snapshot["budgets"][0]["consumed"]["tool_call_count"] == 1
        child_grants = [
            row
            for row in ledger.trace_request(request_id)["governor_log"]
            if row.get("stage") == "S_TASK_GRAPH_NODE_GRANT"
        ]
        assert len(child_grants) == 1
        child_binding = child_grants[0]["snapshot_refs"]["node_grant"]
        assert child_binding["route"] == "delegate"
        assert child_binding["target_id"] == "agent_runtime"
        assert (
            child_grants[0]["tokens"]["approved_action_binding"]["principal_id"]
            == principal_id
        )
        with sqlite3.connect(db_path) as conn:
            node_events = {
                row[0]
                for row in conn.execute(
                    "SELECT event_type FROM task_runtime_outbox "
                    "WHERE aggregate_type = 'TASK_NODE'"
                ).fetchall()
            }
        assert "TASK_NODE_VERIFIED" in node_events
        assert "TASK_NODE_ACCEPTED" in node_events
        assert task_store.list_pending_outbox(limit=100) == []
        with sqlite3.connect(str(db_path)) as conn:
            completed = conn.execute(
                """
                SELECT request_id, result_ref
                FROM secure_ingress_idempotency_claim
                WHERE claim_ref = ?
                """,
                (claim_ref,),
            ).fetchone()
        assert completed == (
            request_id,
            first["internal_agent_result_receipt_ref"],
        )

        review_request_id = "request-internal-runtime-review"
        review_body_sha256 = _sha256("internal-review-request-body")
        review_claim_ref = "ingress-claim://internal/runtime-review"
        review_audit_ref = "audit://secure-ingress/internal/runtime-review"
        _insert_secure_authority(
            db_path,
            request_id=review_request_id,
            principal_id=principal_id,
            body_sha256=review_body_sha256,
            claim_ref=review_claim_ref,
            audit_ref=review_audit_ref,
        )
        executor.review_required = True
        review_order = AgentWorkOrder(
            work_order_id="work-order-internal-runtime-review",
            domain="code",
            run_mode="inspect",
            objective="require durable post-execution review",
            acceptance_criteria=["durable result exists"],
            review_policy={"review_required": False},
        )
        review_terminal = await service.execute(
            work_order=review_order,
            request_id=review_request_id,
            session_id="session-internal-runtime-review",
            ts_ms=NOW + 10_000,
            command_authority={
                "principal_id": principal_id,
                "claim_ref": review_claim_ref,
                "audit_ref": review_audit_ref,
                "body_sha256": review_body_sha256,
            },
        )
        review_receipt = review_terminal["execution_receipt"]
        assert review_terminal["decision"] == "REJECT"
        assert review_receipt["status"] == "VERIFICATION_FAILED"
        assert review_receipt["verification_status"] == "FAIL"
        assert review_receipt["accepted"] is False
        assert review_receipt["reason_code"] == "INTERNAL_AGENT_INNER_ACCEPTANCE_NOT_ACCEPTED"
        assert "review" not in review_receipt["projection"]
        review_command = HostCommandRepository(db_path).get(
            review_receipt["command_id"]
        )
        assert review_command is not None
        review_node = task_store.get_node(
            review_command["command"].graph_id,
            review_command["command"].graph_revision,
            review_command["work_order"].task_node_id,
        )
        assert review_node["status"] == "VERIFICATION_FAILED"
        open_reviews = review_store.list_open_reviews(limit=10)
        assert open_reviews == []
        assert calls == [
            "work-order-internal-runtime",
            "work-order-internal-runtime-review",
        ]
        assert intent_engine.calls == 2

        pre_request_id = "request-internal-runtime-pre-review"
        pre_body_sha256 = _sha256("internal-pre-review-request-body")
        pre_claim_ref = "ingress-claim://internal/runtime-pre-review"
        pre_audit_ref = "audit://secure-ingress/internal/runtime-pre-review"
        _insert_secure_authority(
            db_path,
            request_id=pre_request_id,
            principal_id=principal_id,
            body_sha256=pre_body_sha256,
            claim_ref=pre_claim_ref,
            audit_ref=pre_audit_ref,
        )
        executor.review_required = False
        pre_terminal = await service.execute(
            work_order=AgentWorkOrder(
                work_order_id="work-order-internal-runtime-pre-review",
                domain="code",
                run_mode="inspect",
                objective="wait for durable pre-execution review",
                review_policy={"review_required": True},
            ),
            request_id=pre_request_id,
            session_id="session-internal-runtime-pre-review",
            ts_ms=NOW + 20_000,
            command_authority={
                "principal_id": principal_id,
                "claim_ref": pre_claim_ref,
                "audit_ref": pre_audit_ref,
                "body_sha256": pre_body_sha256,
            },
        )
        pre_receipt = pre_terminal["execution_receipt"]
        assert pre_receipt["status"] == "WAITING_APPROVAL"
        assert pre_receipt["verification_status"] == "NOT_RUN"
        assert pre_receipt["accepted"] is False
        assert pre_receipt["projection"]["review"]["resume_phase"] == (
            "PRE_EXECUTION"
        )
        assert calls == [
            "work-order-internal-runtime",
            "work-order-internal-runtime-review",
        ]
        assert intent_engine.calls == 3
        open_review_types = {
            row["review"]["review_type"]
            for row in review_store.list_open_reviews(limit=10)
        }
        assert open_review_types == {"PRE_EXECUTION_APPROVAL"}
        with sqlite3.connect(str(db_path)) as conn:
            pre_claim_result = conn.execute(
                "SELECT result_ref FROM secure_ingress_idempotency_claim "
                "WHERE claim_ref = ?",
                (pre_claim_ref,),
            ).fetchone()
        assert pre_claim_result == (None,)
        assert task_store.list_pending_outbox(limit=100) == []
    finally:
        if runtime is not None:
            await runtime.aclose()
        claim_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_write_agent_finish_cannot_create_durable_acceptance(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "internal-write-finish.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw02-write-finish",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()

    request_id = "request-internal-write-finish"
    principal_id = "principal-internal-write-finish"
    body_sha256 = _sha256("internal-write-finish-body")
    claim_ref = "ingress-claim://internal/write-finish"
    audit_ref = "audit://secure-ingress/internal/write-finish"
    _insert_secure_authority(
        db_path,
        request_id=request_id,
        principal_id=principal_id,
        body_sha256=body_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )

    ledger = SQLiteLedger(str(db_path))
    claim_store = SQLiteSecureIngressStore(db_path)
    runtime = None
    attempt_service = _FinishOnlyAttemptService()
    try:
        task_store = DurableTaskStore(db_path)
        review_store = ReviewStore(db_path)
        infrastructure = SimpleNamespace(
            task_store=task_store,
            review_store=review_store,
            review_continuation_store=ReviewContinuationStore(db_path),
            command_repository=HostCommandRepository(db_path),
            outbox_publisher=RuntimeOutboxPublisher(
                task_store,
                RuntimeEventStore(db_path),
            ),
            worker_projection_store=WorkerProjectionStore(db_path),
        )
        result_repository = AgentExecutionResultRepository(db_path)
        token_service = TokenService(approval_ttl_ms=180_000)
        runtime_context = _RuntimeContextAuthority(principal_id=principal_id)
        preview_authority = _ExactPreviewAuthority()
        accepted_result = InternalAcceptedResultHandler(
            command_repository=infrastructure.command_repository,
            projection_store=infrastructure.worker_projection_store,
            result_repository=result_repository,
        )
        acceptance_effect_dispatcher = AcceptanceEffectDispatcher(
            task_store=task_store,
            handlers={"internal.agent.result_commit": accepted_result},
            owner_id="internal-write-acceptance-effect-owner",
            action_preview_service=preview_authority,
        )
        runtime = build_governed_internal_runtime(
            task_runtime_container=infrastructure,
            ledger=ledger,
            token_service=token_service,
            result_repository=result_repository,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id="internal-write-finish-owner",
            canonical_executor=AgentRuntimeExecutor(
                artifact_store=AgentArtifactStore(tmp_path / "agent-runs"),
                model_attempt_service=attempt_service,
                prompt_registry=build_live_production_prompt_registry(),
            ),
            runtime_context_application=runtime_context,
            action_preview_service=preview_authority,
            acceptance_effect_dispatcher=acceptance_effect_dispatcher,
        )
        await runtime.activate()
        service = InternalAgentCommandService(
            runtime=runtime,
            intent_engine=_IntentEngine(ledger, token_service),
            claim_result_store=claim_store,
        )
        terminal = await service.execute(
            work_order=AgentWorkOrder(
                work_order_id="work-order-internal-write-finish",
                domain="code",
                run_mode="worker",
                objective="write a file and claim completion",
                allowed_actions=["write_files", "generate_report"],
                forbidden_actions=["modify_secret_files"],
                review_policy={"review_required": False},
            ),
            request_id=request_id,
            session_id="session-internal-write-finish",
            ts_ms=NOW,
            command_authority={
                "principal_id": principal_id,
                "claim_ref": claim_ref,
                "audit_ref": audit_ref,
                "body_sha256": body_sha256,
            },
        )

        assert terminal["decision"] == "REJECT"
        receipt = terminal["execution_receipt"]
        assert receipt["accepted"] is False
        assert receipt["verification_status"] != "PASS"
        # A rejected proposal never becomes canonical Agent result authority.
        durable_result = result_repository.get_by_command(receipt["command_id"])
        assert durable_result is None
        worker_rows = infrastructure.worker_projection_store
        with sqlite3.connect(str(db_path)) as conn:
            proposed = conn.execute(
                "SELECT projection_json FROM task_runtime_worker_projection "
                "WHERE command_id = ?",
                (receipt["command_id"],),
            ).fetchone()
        assert proposed is not None
        projection = json.loads(str(proposed[0]))
        result_proposal = projection["acceptance_effect_intents"][0]["payload"]["result"]
        assert result_proposal["status"] == "BLOCKED"
        assert result_proposal["reason_code"] == AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED
        assert result_proposal["acceptance"]["decision"] == "NOT_RUN"
        assert attempt_service.calls == 0

        command = HostCommandRepository(db_path).get(receipt["command_id"])
        assert command is not None
        snapshot = task_store.read_scheduler_snapshot(
            command["command"].graph_id,
            command["command"].graph_revision,
        )
        assert snapshot["graph_record"]["nodes"][0]["status"] != "ACCEPTED"
        with sqlite3.connect(str(db_path)) as conn:
            accepted_events = conn.execute(
                "SELECT COUNT(*) FROM task_runtime_outbox "
                "WHERE aggregate_type = 'TASK_NODE' "
                "AND event_type = 'TASK_NODE_ACCEPTED'"
            ).fetchone()[0]
        assert accepted_events == 0
    finally:
        if runtime is not None:
            await runtime.aclose()
        claim_store.close()
        ledger.close()
