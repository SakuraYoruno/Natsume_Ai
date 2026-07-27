from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping

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
from host_brain.agent_runtime.workspace_runtime import DurableWorkspaceRuntime
from host_brain.app.governed_plan_execution_policy import (
    bind_governed_plan_execution_policy,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.contracts.agent_runtime.review_policy import (
    NODE_REVIEW_POLICIES_KEY,
    pre_execution_review_required,
)
from host_brain.contracts.skill_execution import (
    SkillBudgetPolicy,
    SkillExecutionPolicy,
)
from host_brain.agent_runtime.workspace_artifact_effect import (
    AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
    AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA,
    canonical_workspace_artifact_digest,
)
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.tokens import TokenService
from host_brain.orchestration import GovernedCommandApplication
from host_brain.orchestration.capability_plan_search import (
    build_capability_plan_search,
)
from host_brain.orchestration.governor_grant import governor_grant_ref
from host_brain.orchestration.node_grants import (
    DurableNodeGrantVerifier,
    NodeBoundGrantService,
)
from host_brain.orchestration.planning_admission import (
    GovernedPlanningAdmissionService,
)
from host_brain.orchestration.planning_execution import (
    GovernedPlanningExecutionService,
    PlanningExecutionAuthority,
)
from host_brain.orchestration.planning_graph_compiler import (
    PlanningGraphCompiler,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.secure_ingress_repository import (
    SQLiteSecureIngressStore,
)
from host_brain.persistence.review_continuation_store import (
    ReviewContinuationStore,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.internal_agent_checkpoint_uow import (
    DurableInternalAgentCheckpointUnitOfWork,
)
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    OutboxMessage,
)
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_SCHEMA_VERSION,
    WorkerProjectionStore,
)
from host_brain.runtime.context_contracts import WorldFactV2, WorldSnapshotV2
from host_brain.secure_ingress.graph_cancellation_authority import (
    SecureIngressGraphCancellationAuthorityVerifier,
)
from host_brain.skills.registry import SkillMeta, SkillRegistry
from host_brain.task_runtime.review_continuation_worker import (
    ReviewContinuationWorker,
)
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime
from host_brain.task_runtime import GovernedTaskCoordinator
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)
from natsume_contracts.task import HostCommandSurface
from tests.integration.test_ne01_governed_agent_workspace_acceptance import (
    _ActionPreview,
    _git,
    _runtime as _workspace_runtime,
    _source_repository,
)
from tests.integration.test_aw02_internal_agent_runtime_cutover import (
    _commit_graph_cancellation_authority,
)
from tests.integration.test_gr03a_governed_durable_dag import (
    NOW,
    _Publisher,
    _Verifier,
    _consume_review,
    _migrated_db,
)


def _handler(_payload: Any, _context: Any) -> dict[str, str]:
    return {"status": "ok"}


def _registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(
        SkillMeta(
            skill_name="plan_read",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=True,
            handler=_handler,
            family="research",
            capability_tags=("research", "evidence"),
            parallelizable=True,
            fallback_policy="strict_fail",
            execution_policy=SkillExecutionPolicy.INLINE_READ_ONLY,
        )
    )
    registry.register(
        SkillMeta(
            skill_name="agent_worker",
            clearance="HIGH",
            risk_level=4,
            scope="host",
            reversible=True,
            require_confirm=True,
            handler=_handler,
            family="research",
            capability_tags=("research", "evidence"),
            fallback_policy="strict_fail",
            execution_policy=SkillExecutionPolicy.POST_ACCEPTANCE_WORKSPACE,
            budget_policy=SkillBudgetPolicy.TASK_PRECHARGE_AGENT_MODEL,
        )
    )
    return registry


def _intent_and_governor(
    *,
    token_service: TokenService,
) -> tuple[dict[str, Any], dict[str, Any]]:
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-ne01-plan-node-review",
        "request_id": "request-ne01-plan-node-review",
        "session_id": "session-ne01-plan-node-review",
        "seq": 27,
        "person_id": "person-ne01-plan-node-review",
        "principal_id": "person-ne01-plan-node-review",
        "type": "query",
        "action": "exec_intent",
        "route": "plan",
        "target": "host",
        "target_id": "planning_search",
        "task": "collect and synthesize governed evidence",
        "args": {
            "task": "collect and synthesize governed evidence",
            "planning_mode": "CAPABILITY_SEARCH_V1",
            "planning_capability_tags": ["research"],
            "planning_max_risk_level": 4,
        },
        "nonce": "nonce-ne01-plan-node-review",
    }
    token_ref = token_service.issue_approval_token(
        intent_id=intent["id"],
        skill_name="exec_intent",
        session_id=intent["session_id"],
        seq=intent["seq"],
        now_ts_ms=NOW - 100,
    )
    binding = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref=token_ref,
        risk_level=4,
        issued_at_ms=NOW - 100,
        expires_at_ms=NOW + 600_000,
    )
    governor = {
        "request_id": intent["request_id"],
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "reason_code": "APPROVE",
        "rule_trace": [],
        "tokens": {"approval_token_ref": token_ref},
        "approved_action_binding": binding,
        "snapshot_refs": {"plan": "plan://ne01/node-review"},
        "stage": "S_INTENT_OK",
        "ts_ms": NOW - 100,
    }
    return intent, governor


class _GrantCheckingWorker:
    def __init__(
        self,
        verifier: DurableNodeGrantVerifier,
        projection_store: WorkerProjectionStore,
        *,
        artifact_store: AgentArtifactStore | None = None,
        workspace_runtime: DurableWorkspaceRuntime | None = None,
    ) -> None:
        self._verifier = verifier
        self._projection_store = projection_store
        self._artifact_store = artifact_store
        self._workspace_runtime = workspace_runtime
        if (artifact_store is None) != (workspace_runtime is None):
            raise ValueError(
                "artifact_store and workspace_runtime must be composed together"
            )
        self.calls: list[str] = []
        self.grants: list[dict[str, Any]] = []
        self.protected_stages: list[dict[str, Any]] = []

    async def execute_stream(
        self,
        *,
        command: Any,
        work_order: Any,
        execution_context: dict[str, Any],
        **_kwargs: Any,
    ):
        route, separator, target_id = str(
            execution_context["worker_route"]
        ).partition(":")
        assert separator == ":"
        verified = self._verifier.verify(
            grant_ref=execution_context["node_grant_ref"],
            request_id=command.request_id,
            session_id=command.session_id,
            principal_id=command.principal_id,
            graph_id=execution_context["graph_id"],
            root_work_order_id=work_order.root_work_order_id,
            graph_revision=execution_context["graph_revision"],
            node_id=execution_context["node_id"],
            route=route,
            target_id=target_id,
            parent_grant_ref=execution_context["parent_grant_ref"],
            attempt_id=execution_context["attempt_id"],
            lease_id=execution_context["lease_id"],
            lease_owner_id=execution_context["lease_owner_id"],
            fencing_token=execution_context["fencing_token"],
        )
        self.calls.append(execution_context["node_id"])
        verified_grant = verified.to_dict()
        self.grants.append(verified_grant)
        node_grant_digest = "sha256:" + hashlib.sha256(
            json.dumps(
                verified_grant,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        body = {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "reason_code": None,
            "command_id": command.command_id,
            "request_id": command.request_id,
            "narrative": f"accepted {execution_context['node_id']}",
            "route_type": route,
            "target_id": target_id,
            "graph_id": execution_context["graph_id"],
            "graph_revision": execution_context["graph_revision"],
            "node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "node_grant_ref": execution_context["node_grant_ref"],
            "audit_refs": {
                "node_grant_ref": execution_context["node_grant_ref"],
                "node_grant_digest": node_grant_digest,
            },
            "budget_usage": {
                "schema_version": (
                    "Natsume.task_runtime.worker_budget_usage.v1"
                ),
                "usage_id": (
                    f"worker-usage://plan-review/"
                    f"{execution_context['node_id']}"
                ),
                "measurements": {
                    "token_count": 1,
                    "retry_count": 0,
                    "model_cost_micros": 1,
                },
                "evidence_refs": [
                    f"model-budget://plan-review/{execution_context['node_id']}"
                ],
            },
        }
        if target_id == "agent_worker":
            if self._artifact_store is not None:
                review_authority = verified_grant.get(
                    "review_continuation_authority"
                )
                assert isinstance(review_authority, Mapping)
                review_ref = (
                    "review://"
                    + str(review_authority.get("review_id") or "").strip()
                )
                assert review_ref.startswith("review://")
                raw_order = {
                    "schema_version": "Natsume.agent.work_order.v2",
                    "work_order_id": work_order.work_order_id,
                    "domain": "code",
                    "run_mode": "worker",
                    "objective": work_order.objective,
                    "workspace": {"strategy": "runtime_repo"},
                    "input_pack": {"user_request": work_order.objective},
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
                        "skill_id": target_id,
                        "request_id": command.request_id,
                    },
                }
                raw_result = execute_agent_runtime_payload(
                    {"work_order": raw_order},
                    model_attempt_service=object(),
                    prompt_registry=object(),
                    artifact_store=self._artifact_store,
                    workspace_runtime=self._workspace_runtime,
                    governed_execution_context={
                        **execution_context,
                        "pre_execution_review_ref": review_ref,
                    },
                )
                assert raw_result["status"] == "EXECUTED", raw_result
                protected = dict(raw_result.pop("_protected_stage"))
                self.protected_stages.append(protected)
                result_digest = canonical_workspace_artifact_digest(raw_result)
            else:
                protected = {
                    "schema_version": (
                        "Natsume.agent.governed_artifact_stage.v1"
                    ),
                    "ref": (
                        "agent-stage://plan-review/"
                        f"{execution_context['attempt_id']}"
                    ),
                    "digest": "sha256:" + "a" * 64,
                }
                result_digest = "sha256:" + "b" * 64
            body["acceptance_effect_intents"] = [
                {
                    "effect_type": AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
                    "commit_scope": "NODE",
                    "payload": {
                        "schema_version": (
                            AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA
                        ),
                        "work_order_id": work_order.work_order_id,
                        "target_id": target_id,
                        "operation_fingerprint": (
                            execution_context["operation_fingerprint"]
                        ),
                        "protected_artifact": protected,
                        "result_digest": result_digest,
                    },
                }
            ]
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        digest = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        projection_id = (
            "worker-projection://plan-review/"
            f"{execution_context['node_id']}/{execution_context['attempt_id']}/"
            f"{digest.removeprefix('sha256:')}"
        )
        self._projection_store.stage(
            projection_id=projection_id,
            projection={
                **body,
                "projection_id": projection_id,
                "payload_digest": digest,
                "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
            },
        )
        yield {**body, "receipt_ref": projection_id}


class _PlanResolver:
    def __init__(self, worker: _GrantCheckingWorker) -> None:
        self._worker = worker

    def resolve(self, *, executor: str, worker_route: str) -> _GrantCheckingWorker:
        assert executor == "canonical"
        assert worker_route.startswith(("skill:", "chat:"))
        return self._worker


class _ExactCriteriaAcceptance:
    def judge(
        self,
        *,
        execution_context: dict[str, Any],
        evidence_refs: tuple[str, ...],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        criteria = tuple(execution_context["node_acceptance_criteria"])
        return {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": f"accept-{execution_context['node_id']}",
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "decision": "ACCEPT",
            "reason_code": None,
            "evidence_refs": list(evidence_refs),
            "criteria": [
                {
                    "criterion_id": f"criterion-{index}",
                    "criterion_text": criterion,
                    "verdict": "PASS",
                    "evidence_refs": list(evidence_refs),
                    "deterministic": True,
                    "reason_code": None,
                    "critic_verdict": None,
                }
                for index, criterion in enumerate(criteria, start=1)
            ],
            "critic_candidate": None,
            "started_at_ms": NOW,
            "finished_at_ms": NOW + 1,
        }


class _SelectiveAcceptance(_ExactCriteriaAcceptance):
    def __init__(self) -> None:
        self.review_node_id: str | None = None

    def judge(
        self,
        *args: Any,
        execution_context: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        judgement = super().judge(
            *args,
            execution_context=execution_context,
            **kwargs,
        )
        if execution_context["node_id"] == self.review_node_id:
            judgement["decision"] = "REVIEW"
            judgement["reason_code"] = "ACCEPTANCE_REVIEW_REQUIRED"
        return judgement


class _SelectiveVerifier:
    def __init__(self) -> None:
        self.failed_node_id: str | None = None

    def verify(
        self,
        *,
        execution_context: dict[str, Any],
        evidence_refs: tuple[str, ...],
        **kwargs: Any,
    ) -> dict[str, Any]:
        return _Verifier(
            "FAIL"
            if execution_context["node_id"] == self.failed_node_id
            else "PASS"
        ).verify(
            execution_context=execution_context,
            evidence_refs=evidence_refs,
            **kwargs,
        )


class _ContinuationRuntime:
    def __init__(
        self,
        *,
        application: GovernedCommandApplication,
        prepared: Any,
    ) -> None:
        self._application = application
        self._prepared = prepared
        self.results: list[dict[str, Any]] = []
        self.errors: list[BaseException] = []

    async def continue_review_continuation(
        self,
        claim: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        receipt = self._prepared.admission
        try:
            result = await self._application.execute_review_continuation(
                command=receipt.command,
                work_order=receipt.work_order,
                assignment=receipt.assignment,
                graph=receipt.graph,
                continuation_claim=dict(claim),
            )
        except BaseException as exc:
            self.errors.append(exc)
            raise
        self.results.append(dict(result))
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_outcome",
    ("success", "downstream-failure", "downstream-cancellation"),
)
async def test_plan_workspace_node_waits_then_stages_effect_only_after_graph_acceptance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    terminal_outcome: str,
) -> None:
    source, source_head = _source_repository(tmp_path)
    db_path = _migrated_db(tmp_path)
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    cancellation_claims = SQLiteSecureIngressStore(db_path, rate_limit=100)
    try:
        token_service = TokenService(
            approval_ttl_ms=600_000,
            confirm_ttl_ms=600_000,
            undo_ttl_ms=600_000,
        )
        intent, governor = _intent_and_governor(token_service=token_service)
        fact = WorldFactV2(
            workspace_id="workspace-ne01-plan-review",
            subject_id=intent["principal_id"],
            key="project.mode",
            value="implementation",
            source_ref="world-observation://ne01/plan-review/1",
            confidence=1.0,
            observed_at_ms=NOW,
            expires_at_ms=NOW + 60_000,
        )
        world = WorldSnapshotV2(
            workspace_id=fact.workspace_id,
            subject_id=fact.subject_id,
            revision=1,
            facts=(fact,),
        )
        ledger.log_governor(governor)
        parent_ref = governor_grant_ref(intent["request_id"], governor)
        skill_registry = _registry()
        adapter = build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=skill_registry,
            request_id=intent["request_id"],
            input_refs=("input://sha256/" + "4" * 64,),
            frozen_context={
                "runtime_context_schema": (
                    "natsume.runtime-context.request.v1"
                ),
                "workspace_id": fact.workspace_id,
                "subject_id": fact.subject_id,
                "world_snapshot_v2": world.to_dict(),
                "scene_identity_v2": {
                    "schema": "natsume.scene-identity.v2",
                    "workspace_id": fact.workspace_id,
                    "subject_id": fact.subject_id,
                    "scene_id": "scene.implementation",
                    "revision": 1,
                    "decision_digest": "d" * 64,
                },
            },
        )
        reviews = ReviewStore(db_path, clock=lambda: NOW)
        continuations = ReviewContinuationStore(db_path, clock=lambda: NOW)
        grant_service = NodeBoundGrantService(
            ledger=ledger,
            token_service=token_service,
            review_store=continuations,
            clock=lambda: NOW,
            child_ttl_ms=60_000,
        )
        grant_verifier = DurableNodeGrantVerifier(
            ledger=ledger,
            review_store=continuations,
            clock=lambda: NOW,
        )
        projection_store = WorkerProjectionStore(
            db_path,
            clock=lambda: NOW,
        )
        task_store = DurableTaskStore(db_path, clock=lambda: NOW)
        workspace_runtime = _workspace_runtime(
            tmp_path,
            db_path=db_path,
            source=source,
            now_ms=NOW,
            runtime_owner_id="ne01-plan-node-review-runtime",
        )
        artifacts = AgentArtifactStore(tmp_path / "agent-runs")

        class _WritingPlanAgent:
            def __init__(self, **_dependencies: Any) -> None:
                pass

            def execute(
                self,
                order,
                *,
                workspace,
                cancelled=None,
                model_budget_guard=None,
            ) -> AgentResult:
                assert callable(cancelled)
                assert not cancelled()
                assert model_budget_guard is not None
                (workspace / "src" / "plan-generated.txt").write_text(
                    "accepted Plan Workspace write\n",
                    encoding="utf-8",
                )
                return AgentResult(
                    status="EXECUTED",
                    work_order_id=order.work_order_id,
                    summary="generated one Plan-owned file",
                    result_text="generated one Plan-owned file",
                    verification={
                        "status": "PASS",
                        "receipt_ref": (
                            "verification://ne01/plan-workspace"
                        ),
                    },
                    acceptance={"decision": "ACCEPT"},
                    review_required=False,
                )

        monkeypatch.setattr(
            executor_module,
            "AgentExecutor",
            _WritingPlanAgent,
        )
        worker = _GrantCheckingWorker(
            grant_verifier,
            projection_store,
            artifact_store=artifacts,
            workspace_runtime=workspace_runtime,
        )
        command_repository = HostCommandRepository(
            db_path,
            clock=lambda: NOW,
        )
        selective_verifier = _SelectiveVerifier()
        selective_acceptance = _SelectiveAcceptance()
        application = GovernedCommandApplication(
            command_repository=command_repository,
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
                worker_resolver=_PlanResolver(worker),
                verifier=selective_verifier,
                acceptance=selective_acceptance,
                projection_reader=projection_store,
                review_runtime=TaskNodeReviewRuntime(
                    reviews,
                    clock=lambda: NOW,
                ),
                node_grant_verifier=grant_verifier,
                acceptance_effects_enabled=True,
                atomic_graph_success_enabled=True,
                owner_id="ne01-plan-node-review-runtime",
            ),
            node_grant_service=grant_service,
            cancellation_authority_verifier=(
                SecureIngressGraphCancellationAuthorityVerifier(
                    cancellation_claims
                )
            ),
        )
        service = GovernedPlanningExecutionService(
            search=adapter,  # type: ignore[arg-type]
            compiler=PlanningGraphCompiler(),
            admission=GovernedPlanningAdmissionService(
                command_repository=command_repository,
                runtime_router=CanonicalRuntimeRouter(),
            ),
            command_application=application,
            compilation_context_resolver=adapter.compilation_context_for,
            compiled_execution_policy_binder=(
                lambda compiled: bind_governed_plan_execution_policy(
                    compiled,
                    skill_registry=skill_registry,
                )
            ),
        )
        prepared = await service.prepare_initial(
            adapter.request,
            authority=PlanningExecutionAuthority(
                surface=HostCommandSurface.HOST_INPUT,
                base_command_request_id=intent["request_id"],
                session_id=intent["session_id"],
                principal_id=intent["principal_id"],
                input_ref="input://sha256/" + "4" * 64,
                input_sha256="sha256:" + "4" * 64,
                governor_grant_ref=parent_ref,
                target_id="planning_search",
            ),
            created_at=NOW,
        )
        graph = prepared.admission.graph
        order = prepared.admission.work_order
        if terminal_outcome == "downstream-failure":
            selective_verifier.failed_node_id = graph.result_node_id
        elif terminal_outcome == "downstream-cancellation":
            selective_acceptance.review_node_id = graph.result_node_id
        required_nodes = {
            node.node_id
            for node in graph.nodes
            if pre_execution_review_required(order, node_id=node.node_id)
        }
        assert len(required_nodes) == 1
        required_node_id = next(iter(required_nodes))
        assert order.review_policy[NODE_REVIEW_POLICIES_KEY][required_node_id][
            "reason_code"
        ] == "SKILL_REGISTRY_CONFIRM_REQUIRED"

        first_events = [event async for event in service.execute_stream(prepared)]
        waiting = first_events[-1]["execution_receipt"]

        assert waiting["status"] == "WAITING_APPROVAL"
        assert waiting["node_id"] == required_node_id
        assert required_node_id not in worker.calls
        assert len(set(worker.calls)) == len(worker.calls)
        review = waiting["projection"]["review"]
        assert review["review_status"] == "PENDING"
        assert review["subject_ref"].startswith(
            f"task-node://{graph.graph_id}/{graph.revision}/{required_node_id}"
        )
        _consume_review(reviews, review)

        continuation_runtime = _ContinuationRuntime(
            application=application,
            prepared=prepared,
        )
        delivered = await ReviewContinuationWorker(
            store=continuations,
            runtime=continuation_runtime,
            worker_id="ne01-plan-node-review-continuation",
            claim_lease_ms=30_000,
            poll_interval_ms=60_000,
            retry_delay_ms=0,
            batch_limit=1,
        ).run_once()

        assert delivered == 1
        if continuation_runtime.errors:
            raise continuation_runtime.errors[0]
        assert continuation_runtime.results[-1]["execution_receipt"][
            "status"
        ] == {
            "success": "ACCEPTED",
            "downstream-failure": "VERIFICATION_FAILED",
            "downstream-cancellation": "WAITING_ACCEPTANCE_REVIEW",
        }[terminal_outcome], continuation_runtime.results[-1]
        assert worker.calls == [node.node_id for node in graph.nodes]
        assert len(worker.grants) == len(graph.nodes)
        assert worker.grants[0]["review_continuation_authority"] is None
        assert all(
            grant["review_continuation_authority"]["node_id"] == required_node_id
            for grant in worker.grants[1:]
        )
        assert continuations.get_review_continuation(review["review_id"]) is None

        snapshot_store = DurableTaskStore(
            db_path,
            clock=lambda: NOW,
        )
        snapshot = snapshot_store.read_scheduler_snapshot(
            graph.graph_id,
            graph.revision,
        )
        if terminal_outcome == "downstream-cancellation":
            assert snapshot["graph_record"]["status"] == "BLOCKED"
            assert {
                node["node_id"]: node["status"]
                for node in snapshot["graph_record"]["nodes"]
            }[graph.result_node_id] == "WAITING_ACCEPTANCE_REVIEW"
            cancel_request_id = "request-ne01-plan-workspace-cancellation"
            cancellation_authority = _commit_graph_cancellation_authority(
                db_path,
                graph_id=graph.graph_id,
                graph_revision=graph.revision,
                request_id=cancel_request_id,
                principal_id="service.operator.plan-workspace-cancellation",
            )
            cancelled = await application.request_graph_cancellation(
                command_id=prepared.admission.command.command_id,
                graph_id=graph.graph_id,
                graph_revision=graph.revision,
                authority=cancellation_authority,
                source="OPERATOR",
                reason_code="OPERATOR_REQUEST",
            )
            assert cancelled["execution_receipt"]["status"] == "CANCELLED"
            cancellation = snapshot_store.get_graph_cancellation_for_graph(
                graph.graph_id,
                graph.revision,
            )
            assert cancellation is not None
            assert cancellation["status"] == "FINALIZED"
            assert cancellation["payload"]["graph_target_status"] == "BLOCKED"
            decision = snapshot_store.get_graph_cancellation_decision_for_intent(
                cancellation["intent_ref"]
            )
            assert decision is not None
            assert decision["payload"]["graph_target_status"] == "CANCELLED"
            snapshot = snapshot_store.read_scheduler_snapshot(
                graph.graph_id,
                graph.revision,
            )
        assert snapshot["graph_record"]["status"] == (
            {
                "success": "COMPLETED",
                "downstream-failure": "FAILED",
                "downstream-cancellation": "CANCELLED",
            }[terminal_outcome]
        )
        node_statuses = {
            node["node_id"]: node["status"]
            for node in snapshot["graph_record"]["nodes"]
        }
        assert node_statuses[required_node_id] == "ACCEPTED"
        if terminal_outcome == "downstream-failure":
            assert node_statuses[graph.result_node_id] == (
                "VERIFICATION_FAILED"
            )
        elif terminal_outcome == "downstream-cancellation":
            assert node_statuses[graph.result_node_id] == "CANCELLED"
        else:
            assert set(node_statuses.values()) == {"ACCEPTED"}
        assert len(snapshot["attempts"]) == len(graph.nodes)
        assert all(attempt["status"] == "SUCCEEDED" for attempt in snapshot["attempts"])
        workspace_effects = [
            effect
            for effect in task_store.list_acceptance_effect_intents(
                graph_id=graph.graph_id,
                graph_revision=graph.revision,
                limit=100,
            )
            if effect["effect_type"]
            == AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE
        ]
        assert len(worker.protected_stages) == 1
        protected = worker.protected_stages[0]
        seal = artifacts.inspect_governed_stage(
            stage_ref=protected["ref"],
            seal_digest=protected["digest"],
        )
        restarted_workspace = _workspace_runtime(
            tmp_path,
            db_path=db_path,
            source=source,
            now_ms=NOW + 6_000,
            runtime_owner_id="ne01-plan-node-review-runtime",
        )
        if terminal_outcome != "success":
            assert workspace_effects == []
            reports = AgentArtifactTerminalReconcilerGroup(
                GovernedSkillArtifactTerminalReconciler(
                    artifact_store=artifacts,
                    workspace_runtime=restarted_workspace,
                    task_store=task_store,
                    command_repository=command_repository,
                    projection_store=projection_store,
                ),
                InternalArtifactTerminalReconciler(
                    artifact_store=artifacts,
                    workspace_runtime=restarted_workspace,
                    task_store=task_store,
                    command_repository=command_repository,
                    projection_store=projection_store,
                ),
            ).run_once()
            assert any(
                item["workspace_id"] == seal.workspace_id
                and item["action"] == "ABANDONED"
                and item["reason_code"]
                == {
                    "downstream-failure": (
                        "TERMINAL_PLAN_GRAPH_FAILED_AFTER_NODE_ACCEPTANCE"
                    ),
                    "downstream-cancellation": (
                        "TERMINAL_PLAN_GRAPH_CANCELLED_AFTER_NODE_ACCEPTANCE"
                    ),
                }[terminal_outcome]
                for item in reports
            ), reports
            assert any(
                item["workspace_id"] == seal.workspace_id
                and item["action"] == "CLEANED"
                for item in reports
            ), reports
        else:
            assert len(workspace_effects) == 1
            assert workspace_effects[0]["node_id"] == required_node_id
            assert workspace_effects[0]["status"] == "PENDING"
            assert workspace_effects[0]["commit_scope"] == "GRAPH"
            dispatched = await AcceptanceEffectDispatcher(
                task_store=task_store,
                handlers={
                    AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE: (
                        AcceptedAgentWorkspaceArtifactHandler(
                            command_repository=command_repository,
                            projection_store=projection_store,
                            artifact_store=artifacts,
                            workspace_runtime=workspace_runtime,
                            task_store=task_store,
                        )
                    )
                },
                owner_id="ne01-plan-workspace-acceptance",
                action_preview_service=_ActionPreview(),
            ).dispatch_effect(workspace_effects[0]["effect_id"])
            assert dispatched is not None
            assert dispatched["status"] == "COMMITTED", dispatched
            assert str(dispatched["receipt_ref"]).startswith(
                "workspace-promotion://"
            )
            lifecycle = workspace_runtime.repository.get_lifecycle(
                seal.workspace_id
            )
            assert lifecycle.state.value == "PROMOTED"
            assert lifecycle.accepted_event is not None
            assert lifecycle.accepted_event.base_commit == source_head
            assert lifecycle.accepted_event.accepted_head != source_head
            cleanup = InternalArtifactTerminalReconciler(
                artifact_store=artifacts,
                workspace_runtime=restarted_workspace,
                task_store=task_store,
                command_repository=command_repository,
                projection_store=projection_store,
            ).run_once()
            assert any(
                item["workspace_id"] == seal.workspace_id
                and item["action"] == "CLEANED"
                for item in cleanup
            ), cleanup

        assert _git(source, "rev-parse", "main") == source_head
        assert not (source / "src" / "plan-generated.txt").exists()
        cleaned = restarted_workspace.repository.get(seal.workspace_id)
        assert cleaned.status == "CLEANED"
        assert not (
            tmp_path / "managed" / cleaned.workspace_relpath
        ).exists()

        replay_workspace = _workspace_runtime(
            tmp_path,
            db_path=db_path,
            source=source,
            now_ms=NOW + 12_000,
            runtime_owner_id="ne01-plan-node-review-runtime",
        )
        replay_reconcilers = [
            InternalArtifactTerminalReconciler(
                artifact_store=artifacts,
                workspace_runtime=replay_workspace,
                task_store=task_store,
                command_repository=command_repository,
                projection_store=projection_store,
            )
        ]
        if terminal_outcome != "success":
            replay_reconcilers.insert(
                0,
                GovernedSkillArtifactTerminalReconciler(
                    artifact_store=artifacts,
                    workspace_runtime=replay_workspace,
                    task_store=task_store,
                    command_repository=command_repository,
                    projection_store=projection_store,
                ),
            )
        assert AgentArtifactTerminalReconcilerGroup(
            *replay_reconcilers
        ).run_once() == ()
        assert replay_workspace.repository.get(seal.workspace_id).status == (
            "CLEANED"
        )
        with sqlite3.connect(str(db_path)) as conn:
            assert conn.execute(
                """
                SELECT COUNT(*) FROM agent_runtime_review
                WHERE task_graph_id = ? AND task_node_id = ?
                  AND status = 'CONSUMED'
                """,
                (graph.graph_id, required_node_id),
            ).fetchone() == (1,)
        child_decisions = [
            row
            for row in ledger.trace_request(intent["request_id"])["governor_log"]
            if row.get("reason_code") == "TASK_GRAPH_NODE_DERIVED_APPROVE"
        ]
        renewal_decisions = [
            row
            for row in ledger.trace_request(intent["request_id"])["governor_log"]
            if row.get("reason_code")
            == "TASK_GRAPH_NODE_REVIEW_CONTINUATION_APPROVE"
        ]
        assert len(child_decisions) == len(graph.nodes)
        assert len(renewal_decisions) == len(graph.nodes)
    finally:
        cancellation_claims.close()
        ledger.close()
