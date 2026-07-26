from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.executor import (
    AgentRuntimeExecutor,
    execute_agent_runtime_payload,
)
from host_brain.agent_runtime.governed_skill_work_order import (
    GOVERNED_AGENT_REVIEW_REQUIRED,
    GOVERNED_AGENT_WORK_ORDER_BINDING_INVALID,
    GovernedAgentWorkOrderBindingError,
    bind_governed_agent_write_order,
)
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.workspace_artifact_effect import (
    AGENT_WORKSPACE_ARTIFACT_EFFECT_SCHEMA,
    AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
    AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA,
    canonical_workspace_artifact_digest,
)
from host_brain.app.governed_task_adapter import (
    GovernedHostRuntime,
    _FrozenSkillWorker,
    _build_contracts,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.task_runtime.acceptance_effects import (
    AcceptanceEffectCompileError,
    compile_graph_acceptance_effects,
)


WORK_ORDER_ID = "work-order-governed-agent-write"


class _WorkspaceRuntime:
    runtime_owner_id = "worker-governed-agent"

    @staticmethod
    def workspace_id_for(*, graph_id: str, graph_revision: int) -> str:
        assert graph_id == "graph-governed-agent"
        assert graph_revision == 2
        return "workspace-governed-agent-revision-2"


def _raw_order() -> dict:
    return {
        "schema_version": "Natsume.agent.work_order.v2",
        "work_order_id": WORK_ORDER_ID,
        "domain": "code",
        "run_mode": "worker",
        "objective": "write one reviewed source change",
        "workspace": {"strategy": "runtime_repo"},
        "input_pack": {"user_request": "write one reviewed source change"},
        "allowed_actions": ["read_files", "write_files"],
        "forbidden_actions": ["modify_secret_files"],
        "acceptance_criteria": ["tests pass"],
        "verification_plan": {"commands": ["python -m pytest -q"]},
        "approval_policy": {"require_confirm": True, "confirmed": True},
        "review_policy": {"review_required": True},
        "metadata": {
            "skill_id": "agent_worker",
            "request_id": "request-governed-agent",
            "caller_note": "semantic metadata only",
        },
    }


def _context(**overrides) -> dict:
    value = {
        "execution_mode": "governed_task_runtime",
        "command_id": "host-command://chat/governed-agent",
        "request_id": "request-governed-agent",
        "principal_id": "person-governed-agent",
        "target_id": "agent_worker",
        "command_target_id": "agent_worker",
        "work_order_id": WORK_ORDER_ID,
        "graph_id": "graph-governed-agent",
        "graph_revision": 2,
        "node_id": "node-governed-agent",
        "attempt_id": "attempt-governed-agent",
        "lease_id": "lease-governed-agent",
        "lease_owner_id": "worker-governed-agent",
        "fencing_token": 7,
        "reservation_id": "reservation-governed-agent",
        "node_grant_ref": "governor-decision://sha256/" + "1" * 64,
        "parent_grant_ref": "governor-decision://sha256/" + "2" * 64,
        "business_scope": "host-skill:agent_worker",
        "operation_fingerprint": "sha256:" + "3" * 64,
        "node_risk_level": 4,
        "pre_execution_review_ref": "review://task-node/governed-agent",
        "budget": {
            "budget_id": "budget-governed-agent",
            "status": "ACTIVE",
            "status_version": 1,
            "remaining": {
                "token_count": 4_096,
                "tool_call_count": 4,
                "time_ms": 60_000,
                "concurrency": 1,
                "retry_count": 1,
                "model_cost_micros": 250_000,
            },
        },
    }
    value.update(overrides)
    return value


def test_binder_rebuilds_all_write_authority_from_governed_context() -> None:
    order = bind_governed_agent_write_order(
        _raw_order(),
        governed_execution_context=_context(),
        workspace_runtime=_WorkspaceRuntime(),  # type: ignore[arg-type]
    )

    assert order.work_order_id == WORK_ORDER_ID
    assert order.root_work_order_id == WORK_ORDER_ID
    assert order.task_graph_id == "graph-governed-agent"
    assert order.graph_revision == 2
    assert order.task_node_id == "node-governed-agent"
    assert order.risk_level == 4
    assert order.workspace == {
        "strategy": "durable_workspace",
        "workspace_id": "workspace-governed-agent-revision-2",
    }
    assert order.approval_ref == _context()["node_grant_ref"]
    assert order.review_ref == _context()["pre_execution_review_ref"]
    assert order.review_policy == {
        "review_required": False,
        "mode": "consumed_task_node_pre_execution_review",
        "review_ref": _context()["pre_execution_review_ref"],
    }
    assert order.metadata["caller_note"] == "semantic metadata only"
    assert order.metadata["attempt_id"] == "attempt-governed-agent"
    assert "git_push" in order.forbidden_actions
    assert "cross_workspace_write" in order.forbidden_actions


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (
            lambda order, _context: order.update(
                {"schema_version": "Natsume.agent.work_order.v3"}
            ),
            "pre-admitted WorkOrderV3",
        ),
        (
            lambda order, _context: order.update({"risk_level": 4}),
            "pre-admitted fields",
        ),
        (
            lambda order, _context: order["metadata"].update(
                {"node_grant_ref": "forged"}
            ),
            "authority fields",
        ),
        (
            lambda order, _context: order.update(
                {
                    "workspace": {
                        "strategy": "durable_workspace",
                        "workspace_id": "caller-selected",
                    }
                }
            ),
            "may not select a durable Workspace",
        ),
        (
            lambda _order, context: context.update({"node_risk_level": 3}),
            "under-classified",
        ),
        (
            lambda _order, context: context.update(
                {"lease_owner_id": "other-worker"}
            ),
            "runtime owner differs",
        ),
    ],
)
def test_binder_rejects_caller_authority_and_underclassified_writes(
    mutate,
    detail: str,
) -> None:
    order = _raw_order()
    context = _context()
    mutate(order, context)

    with pytest.raises(
        GovernedAgentWorkOrderBindingError,
        match=detail,
    ) as raised:
        bind_governed_agent_write_order(
            order,
            governed_execution_context=context,
            workspace_runtime=_WorkspaceRuntime(),  # type: ignore[arg-type]
        )

    assert (
        raised.value.reason_code
        == GOVERNED_AGENT_WORK_ORDER_BINDING_INVALID
    )


def test_binder_requires_consumed_durable_review() -> None:
    with pytest.raises(GovernedAgentWorkOrderBindingError) as raised:
        bind_governed_agent_write_order(
            _raw_order(),
            governed_execution_context=_context(
                pre_execution_review_ref="review-waiver://not-consumed"
            ),
            workspace_runtime=_WorkspaceRuntime(),  # type: ignore[arg-type]
        )

    assert raised.value.reason_code == GOVERNED_AGENT_REVIEW_REQUIRED


def test_public_agent_payload_cannot_inject_app_local_workspace_ports() -> None:
    payload = {
        "work_order": deepcopy(_raw_order())
        | {
            "artifact_store": SimpleNamespace(),
            "workspace_runtime": SimpleNamespace(),
        }
    }

    with pytest.raises(GovernedAgentWorkOrderBindingError) as raised:
        execute_agent_runtime_payload(payload)

    assert (
        raised.value.reason_code
        == GOVERNED_AGENT_WORK_ORDER_BINDING_INVALID
    )


def test_governed_read_agent_rebuilds_v3_and_receives_exact_model_budget(
    monkeypatch,
) -> None:  # noqa: ANN001
    raw_order = deepcopy(_raw_order())
    raw_order.update(
        {
            "run_mode": "inspect",
            "allowed_actions": ["read_files", "generate_report"],
            "review_policy": {"review_required": False},
        }
    )
    captured: dict[str, object] = {}

    def fake_execute(
        self,
        work_order,
        *,
        workspace_execution_authority=None,
        projection_recovery_context=None,
        model_budget_guard=None,
    ):  # noqa: ANN001, ANN202
        del self, projection_recovery_context
        captured["work_order"] = work_order
        captured["workspace_execution_authority"] = (
            workspace_execution_authority
        )
        captured["model_budget_guard"] = model_budget_guard
        return {"status": "EXECUTED"}

    monkeypatch.setattr(AgentRuntimeExecutor, "execute", fake_execute)

    result = execute_agent_runtime_payload(
        {"work_order": raw_order},
        model_attempt_service=object(),
        prompt_registry=object(),
        governed_execution_context=_context(),
    )

    order = captured["work_order"]
    guard = captured["model_budget_guard"]
    assert result == {"status": "EXECUTED"}
    assert isinstance(order, WorkOrderV3)
    assert order.task_graph_id == "graph-governed-agent"
    assert order.task_node_id == "node-governed-agent"
    assert order.graph_revision == 2
    assert order.approval_ref == _context()["node_grant_ref"]
    assert captured["workspace_execution_authority"] is None
    assert guard.graph_id == order.task_graph_id
    assert guard.node_id == order.task_node_id
    assert guard.attempt_id == "attempt-governed-agent"
    assert guard.max_token_count == 4_096
    assert guard.max_provider_attempts == 2


def test_direct_agent_worker_admission_freezes_risk_and_durable_review() -> None:
    intent = {
        "id": "intent-governed-agent-write",
        "route": "skill",
        "target_id": "agent_worker",
        "task": "write one governed source change",
        "args": {"task": "write one governed source change"},
    }
    command, order, graph = _build_contracts(
        request_body={},
        request_id="request-governed-agent-write",
        session_id="session-governed-agent-write",
        ts_ms=1_900_000_000_000,
        intent_draft=intent,
        governor_result={
            "request_id": "request-governed-agent-write",
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "tokens": {},
            "ts_ms": 1_900_000_000_000,
        },
        person_id="person-governed-agent-write",
        sanitized_message="write one governed source change",
        emotion_context={},
        render_target_lang="zh",
        command_authority=None,
        skill_risk_level=4,
        skill_requires_durable_review=True,
    )

    assert command.target_id == "agent_worker"
    assert order.risk_level == graph.nodes[0].risk_level == 4
    assert order.review_policy == {
        "review_required": True,
        "durable_review_required": True,
        "mode": "task_node_pre_execution_review",
        "reason": "skill_requires_durable_confirmation",
        "review_ref": order.review_ref,
    }
    assert order.review_ref.startswith("review-policy://task-node/")


def test_runtime_reads_direct_skill_policy_from_its_app_local_registry() -> None:
    registry = SimpleNamespace(
        require=lambda target: SimpleNamespace(
            skill_name=target,
            risk_level=4,
            require_confirm=True,
        )
    )
    runtime = GovernedHostRuntime(
        application=SimpleNamespace(),
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=SimpleNamespace(),
        supported_routes=frozenset({"skill"}),
        planning_skill_registry=registry,
    )

    assert runtime._direct_skill_execution_policy(
        {"route": "skill", "target_id": "agent_worker"}
    ) == (4, True)


def _agent_acceptance_fixture():
    intent = {
        "id": "intent-agent-acceptance-effect",
        "route": "skill",
        "target_id": "agent_worker",
        "task": "write an accepted source change",
        "args": {"task": "write an accepted source change"},
    }
    command, order, graph = _build_contracts(
        request_body={},
        request_id="request-agent-acceptance-effect",
        session_id="session-agent-acceptance-effect",
        ts_ms=1_900_000_000_100,
        intent_draft=intent,
        governor_result={
            "request_id": "request-agent-acceptance-effect",
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "tokens": {},
            "ts_ms": 1_900_000_000_100,
        },
        person_id="person-agent-acceptance-effect",
        sanitized_message="write an accepted source change",
        emotion_context={},
        render_target_lang="zh",
        command_authority=None,
        skill_risk_level=4,
        skill_requires_durable_review=True,
    )
    protected = {
        "schema_version": "Natsume.agent.governed_artifact_stage.v1",
        "ref": "agent-stage://sha256/" + "4" * 64,
        "digest": "sha256:" + "5" * 64,
    }
    result_digest = "sha256:" + "6" * 64
    commit = {
        "decision": "APPROVE",
        "request_id": command.request_id,
        "narrative": "accepted governed source change",
        "route_type": "skill",
        "target_id": command.target_id,
        "receipt_ref": "worker-projection://sha256/" + "7" * 64,
        "acceptance_effect_intents": [
            {
                "effect_type": AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
                "commit_scope": "NODE",
                "payload": {
                    "schema_version": (
                        AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA
                    ),
                    "work_order_id": order.work_order_id,
                    "target_id": command.target_id,
                    "operation_fingerprint": (
                        order.operation_fingerprint
                    ),
                    "protected_artifact": protected,
                    "result_digest": result_digest,
                },
            }
        ],
    }
    return command, order, graph, commit


def test_acceptance_compiler_promotes_artifact_before_visible_effects() -> None:
    command, order, graph, commit = _agent_acceptance_fixture()
    specs = compile_graph_acceptance_effects(
        command=command,
        work_order=order,
        node_id=graph.nodes[0].node_id,
        attempt_id="attempt-agent-acceptance-effect",
        worker_commit=commit,
    )

    assert specs[0].effect_type == AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE
    assert specs[1].effect_type == "conversation.user_turn"
    assert specs[0].payload == {
        "schema_version": AGENT_WORKSPACE_ARTIFACT_EFFECT_SCHEMA,
        "command_id": command.command_id,
        "request_id": command.request_id,
        "work_order_id": order.work_order_id,
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "node_id": graph.nodes[0].node_id,
        "attempt_id": "attempt-agent-acceptance-effect",
        "target_id": "agent_worker",
        "operation_fingerprint": order.operation_fingerprint,
        "worker_projection_ref": commit["receipt_ref"],
        "protected_artifact": (
            commit["acceptance_effect_intents"][0]["payload"][
                "protected_artifact"
            ]
        ),
        "result_digest": (
            commit["acceptance_effect_intents"][0]["payload"][
                "result_digest"
            ]
        ),
    }


def test_acceptance_compiler_rejects_worker_artifact_lineage_tamper() -> None:
    command, order, graph, commit = _agent_acceptance_fixture()
    commit["acceptance_effect_intents"][0]["payload"][
        "operation_fingerprint"
    ] = "sha256:" + "9" * 64

    with pytest.raises(
        AcceptanceEffectCompileError,
        match="differs from admitted Task authority",
    ):
        compile_graph_acceptance_effects(
            command=command,
            work_order=order,
            node_id=graph.nodes[0].node_id,
            attempt_id="attempt-agent-acceptance-effect",
            worker_commit=commit,
        )


@pytest.mark.asyncio
async def test_frozen_worker_requires_consumed_review_and_emits_bounded_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, order, graph, _commit = _agent_acceptance_fixture()
    node = graph.nodes[0]
    child_grant_ref = "governor-decision://sha256/" + "a" * 64
    child_intent_id = "intent-agent-child-grant"
    protected = {
        "schema_version": "Natsume.agent.governed_artifact_stage.v1",
        "ref": "agent-stage://sha256/" + "b" * 64,
        "digest": "sha256:" + "c" * 64,
    }
    public_result = {
        "status": "EXECUTED",
        "work_order_id": order.work_order_id,
        "result_text": "safe public result",
    }
    captured_context = {}

    class _GrantVerifier:
        @staticmethod
        def verify(**_kwargs):
            return SimpleNamespace(
                grant_ref=child_grant_ref,
                intent_id=child_intent_id,
                to_dict=lambda: {
                    "grant_ref": child_grant_ref,
                    "intent_id": child_intent_id,
                },
            )

    class _Ledger:
        @staticmethod
        def get_event(ref: str):
            assert ref == "skill-receipt://agent-write"
            return {"result_status": "SUCCESS"}

    class _ReviewStore:
        @staticmethod
        def list_reviews(**_kwargs):
            return [
                {
                    "task_graph_id": graph.graph_id,
                    "work_order_id": order.work_order_id,
                    "task_node_id": node.node_id,
                    "review": {
                        "review_id": "review-agent-write",
                        "review_type": "PRE_EXECUTION_APPROVAL",
                        "subject_ref": (
                            f"task-node://{graph.graph_id}/1/{node.node_id}"
                            f"?root={order.work_order_id}&attempt=not-started"
                        ),
                        "requester": command.principal_id,
                        "risk_level": 4,
                        "side_effects": list(order.allowed_actions),
                        "status": "CONSUMED",
                        "resume_phase": "PRE_EXECUTION",
                        "resume_count": 1,
                        "decision_actor": "operator-reviewer",
                        "token_ref": "review-token://agent-write",
                        "resume_result_ref": "review-resume://agent-write",
                    },
                }
            ]

    class _ProjectionStore:
        @staticmethod
        def stage(*, projection_id: str, projection: dict):
            assert projection["acceptance_effect_intents"][0][
                "effect_type"
            ] == AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE
            return {
                "projection_id": projection_id,
                "payload_digest": projection["payload_digest"],
            }

    monkeypatch.setattr(
        "host_brain.app.governed_frozen_skill_runtime._load_governor_grant",
        lambda **_kwargs: {
            "snapshot_refs": {"node_intent": {"id": child_intent_id}},
            "tokens": {},
        },
    )

    def _execute(**kwargs):
        captured_context.update(kwargs["governed_execution_context"])
        return SimpleNamespace(
            payload={
                "receipt_ref": "skill-receipt://agent-write",
                "result": {**public_result, "_protected_stage": protected},
            },
            result_text="accepted governed write",
            success=True,
            error_text=None,
        )

    monkeypatch.setattr(
        "host_brain.app.governed_frozen_skill_runtime.execute_frozen_governed_skill",
        _execute,
    )
    monkeypatch.setattr(
        "host_brain.app.governed_frozen_skill_runtime.record_governed_skill_execution",
        lambda **_kwargs: None,
    )
    worker = _FrozenSkillWorker(
        ledger=_Ledger(),
        execution_ports=SimpleNamespace(),
        node_grant_verifier=_GrantVerifier(),  # type: ignore[arg-type]
        projections=_ProjectionStore(),
        review_store=_ReviewStore(),
    )
    execution_context = {
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
        "attempt_id": "attempt-agent-acceptance-effect",
        "lease_id": "lease-agent-acceptance-effect",
        "lease_owner_id": "worker-agent-acceptance-effect",
        "fencing_token": 4,
        "reservation_id": "reservation-agent-acceptance-effect",
        "node_grant_ref": child_grant_ref,
        "parent_grant_ref": command.governor_grant_ref,
        "business_scope": command.business_scope,
        "operation_fingerprint": command.operation_fingerprint,
        "worker_route": node.route,
        "node_input_refs": list(node.input_refs),
        "node_risk_level": 4,
    }

    events = [
        event
        async for event in worker.execute_stream(
            command=command,
            work_order=order,
            execution_context=execution_context,
        )
    ]

    assert captured_context["pre_execution_review_ref"] == (
        "review://review-agent-write"
    )
    proposal = events[-1]["acceptance_effect_intents"][0]
    assert proposal["commit_scope"] == "NODE"
    assert proposal["payload"]["protected_artifact"] == protected
    assert proposal["payload"]["result_digest"] == (
        canonical_workspace_artifact_digest(public_result)
    )
    assert "_protected_stage" not in str(events[-1])
    assert events[-1]["receipt_ref"].startswith(
        "worker-projection://skill/"
    )
