from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_plan_execution_policy import (
    bind_governed_plan_execution_policy,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.contracts.agent_runtime.review_policy import (
    NODE_REVIEW_POLICIES_KEY,
    pre_execution_review_required,
)
from host_brain.contracts.skill_execution import SkillExecutionPolicy
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
from host_brain.skills.registry import SkillMeta, SkillRegistry
from host_brain.task_runtime.review_continuation_worker import (
    ReviewContinuationWorker,
)
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime
from host_brain.task_runtime import GovernedTaskCoordinator
from natsume_contracts.task import HostCommandSurface
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
            skill_name="plan_confirm",
            clearance="MEDIUM",
            risk_level=2,
            scope="host",
            reversible=True,
            require_confirm=True,
            handler=_handler,
            family="research",
            capability_tags=("research", "evidence"),
            fallback_policy="strict_fail",
            execution_policy=SkillExecutionPolicy.INLINE_READ_ONLY,
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
            "planning_max_risk_level": 2,
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
        risk_level=2,
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
    ) -> None:
        self._verifier = verifier
        self._projection_store = projection_store
        self.calls: list[str] = []
        self.grants: list[dict[str, Any]] = []

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
        self.grants.append(verified.to_dict())
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
async def test_plan_confirm_node_waits_and_resumes_with_exact_renewed_grants(
    tmp_path,
) -> None:
    db_path = _migrated_db(tmp_path)
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    try:
        token_service = TokenService(
            approval_ttl_ms=600_000,
            confirm_ttl_ms=600_000,
            undo_ttl_ms=600_000,
        )
        intent, governor = _intent_and_governor(token_service=token_service)
        ledger.log_governor(governor)
        parent_ref = governor_grant_ref(intent["request_id"], governor)
        skill_registry = _registry()
        adapter = build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=skill_registry,
            request_id=intent["request_id"],
            input_refs=("input://sha256/" + "4" * 64,),
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
        worker = _GrantCheckingWorker(grant_verifier, projection_store)
        command_repository = HostCommandRepository(
            db_path,
            clock=lambda: NOW,
        )
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
                verifier=_Verifier(),
                acceptance=_ExactCriteriaAcceptance(),
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
        ] == "ACCEPTED"
        assert worker.calls == [node.node_id for node in graph.nodes]
        assert len(worker.grants) == len(graph.nodes)
        assert worker.grants[0]["review_continuation_authority"] is None
        assert all(
            grant["review_continuation_authority"]["node_id"] == required_node_id
            for grant in worker.grants[1:]
        )
        assert continuations.get_review_continuation(review["review_id"]) is None

        snapshot = DurableTaskStore(
            db_path,
            clock=lambda: NOW,
        ).read_scheduler_snapshot(graph.graph_id, graph.revision)
        assert snapshot["graph_record"]["status"] == "COMPLETED"
        assert all(
            node["status"] == "ACCEPTED"
            for node in snapshot["graph_record"]["nodes"]
        )
        assert len(snapshot["attempts"]) == len(graph.nodes)
        assert all(attempt["status"] == "SUCCEEDED" for attempt in snapshot["attempts"])
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
        ledger.close()
