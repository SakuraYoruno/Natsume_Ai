from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.app.governed_plan_execution_policy import (
    GovernedPlanExecutionPolicyError,
    bind_governed_plan_execution_policy,
    require_governed_plan_execution_policy,
)
from host_brain.app.governed_frozen_skill_runtime import (
    _skill_companion_presentation_allowed,
)
from host_brain.contracts.agent_runtime.plan_execution_policy import (
    PLAN_EXECUTION_POLICY_METADATA_KEY,
    PLAN_NODE_WORKSPACE_EFFECT_MODE,
    PLAN_RESULT_EFFECT_MODE,
)
from host_brain.orchestration.capability_plan_search import (
    CAPABILITY_PLAN_SEARCH_AUTHORITY_INVALID,
    CAPABILITY_PLAN_SEARCH_INVALID,
    CAPABILITY_PLAN_SEARCH_NO_COMPETITION,
    CapabilityPlanSearchError,
    build_capability_plan_search,
    is_capability_plan_intent,
)
from host_brain.orchestration.planning_graph_compiler import PlanningGraphCompiler
from host_brain.contracts.agent_runtime.review_policy import (
    NODE_REVIEW_POLICIES_KEY,
    pre_execution_review_required,
)
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.contracts.skill_execution import (
    SkillBudgetPolicy,
    SkillExecutionPolicy,
)
from host_brain.planning_search.capability_domain import CapabilitySuccessorCatalog
from host_brain.planning_search.capability_domain_models import (
    CAPABILITY_PLANNING_MODE,
    CapabilityPlanningError,
    CapabilityPlanningSnapshot,
    PlanningCapability,
)
from host_brain.planning_search.contracts import SearchCandidate
from host_brain.skills.registry import SkillMeta, SkillRegistry
from host_brain.runtime.context_contracts import WorldFactV2, WorldSnapshotV2
from host_brain.task_runtime.admission import derive_node_operation_fingerprint
from host_brain.task_runtime.acceptance_effects import (
    AcceptanceEffectCompileError,
    compile_graph_acceptance_effects,
)
from host_brain.agent_runtime.workspace_artifact_effect import (
    AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
    AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA,
)
from host_brain.agent_runtime.governed_production_payload import (
    build_governed_production_executor_payload,
)
from host_brain.task_runtime.plan_workspace_acceptance_effect import (
    compile_plan_node_acceptance_effects,
)
from natsume_contracts.task import (
    HostCommandSurface,
    build_host_command,
)


def _handler(_payload, _context):  # noqa: ANN001, ANN201
    return {"status": "ok"}


def _registry(
    *,
    include_second: bool = True,
    include_low_risk_alternative: bool = False,
) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(
        SkillMeta(
            skill_name="research_fast",
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
    if include_second:
        registry.register(
            SkillMeta(
                skill_name="research_crosscheck",
                clearance="MEDIUM",
                risk_level=2,
                scope="host",
                reversible=True,
                handler=_handler,
                family="research",
                capability_tags=("research", "evidence", "crosscheck"),
                fallback_policy="strict_fail",
                execution_policy=SkillExecutionPolicy.INLINE_READ_ONLY,
            )
        )
    if include_low_risk_alternative:
        registry.register(
            SkillMeta(
                skill_name="research_safe_crosscheck",
                clearance="LOW",
                risk_level=1,
                scope="host",
                reversible=True,
                handler=_handler,
                family="research",
                capability_tags=("research", "evidence", "crosscheck"),
                parallelizable=True,
                fallback_policy="strict_fail",
                execution_policy=SkillExecutionPolicy.INLINE_READ_ONLY,
            )
        )
    registry.register(
        SkillMeta(
            skill_name="research_confirm_required",
            clearance="HIGH",
            risk_level=3,
            scope="host",
            reversible=True,
            handler=_handler,
            require_confirm=True,
            family="research",
            capability_tags=("research",),
            fallback_policy="strict_fail",
            execution_policy=SkillExecutionPolicy.INLINE_READ_ONLY,
        )
    )
    return registry


def _intent() -> tuple[dict, dict]:
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-ne02-capability-search",
        "request_id": "request-ne02-capability-search",
        "session_id": "session-ne02-capability-search",
        "seq": 12,
        "person_id": "person-ne02-capability-search",
        "principal_id": "person-ne02-capability-search",
        "type": "query",
        "action": "exec_intent",
        "route": "plan",
        "target": "host",
        "target_id": "planning_search",
        "task": "collect and synthesize bounded research evidence",
        "args": {
            "task": "collect and synthesize bounded research evidence",
            "planning_mode": CAPABILITY_PLANNING_MODE,
            "planning_capability_tags": ["research"],
            "planning_max_risk_level": 2,
        },
        "nonce": "nonce-ne02-capability-search",
    }
    governor = {
        "request_id": intent["request_id"],
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "reason_code": "APPROVED",
        "approved_action_binding": build_governed_action_binding(
            intent_envelope=intent,
            approval_token_ref="approval-not-in-search-candidates",
            risk_level=2,
            issued_at_ms=1_000,
            expires_at_ms=61_000,
        ),
        "ts_ms": 1_000,
    }
    return intent, governor


def test_selected_production_capability_is_frozen_as_effect_free_proposal() -> None:
    payload = build_governed_production_executor_payload(
        target_id="agent_frontend_acceptance",
        intent_args={
            "planning_mode": CAPABILITY_PLANNING_MODE,
            "capability_id": "agent_frontend_acceptance",
        },
        request_id="request-plan-proposal",
        session_id="session-plan-proposal",
        person_id="person-plan-proposal",
        user_message="给出前端可用性审查方案",
        work_order_id="work-order-plan-proposal",
        graph_id="graph-plan-proposal",
        node_id="node-plan-proposal",
        graph_revision=1,
        business_scope="plan-proposal",
        operation_fingerprint="sha256:" + "a" * 64,
        approval_ref="governor-decision://plan-proposal",
        review_ref="review-waiver://plan-proposal",
    )

    order = payload["work_order"]
    assert order["allowed_actions"] == ["generate_report"]
    assert order["metadata"]["proposal_only"] is True
    assert (
        order["metadata"]["execution_policy"]
        == "PRE_ACCEPTANCE_PROPOSAL_ONLY"
    )
    assert "Do not request tools" in order["input_pack"]["context"]


def _write_registry(*, two_writers: bool = False) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(
        SkillMeta(
            skill_name="agent_worker",
            clearance="HIGH",
            risk_level=4,
            scope="host",
            reversible=True,
            require_confirm=True,
            handler=_handler,
            family="code",
            capability_tags=("coding", "codex"),
            fallback_policy="strict_fail",
            execution_policy=SkillExecutionPolicy.POST_ACCEPTANCE_WORKSPACE,
            budget_policy=SkillBudgetPolicy.TASK_PRECHARGE_AGENT_MODEL,
        )
    )
    registry.register(
        SkillMeta(
            skill_name=("agent_self_heal" if two_writers else "code_read"),
            clearance=("HIGH" if two_writers else "LOW"),
            risk_level=(4 if two_writers else 1),
            scope="host",
            reversible=True,
            require_confirm=two_writers,
            handler=_handler,
            family="code",
            capability_tags=("coding", "codex"),
            fallback_policy="strict_fail",
            execution_policy=(
                SkillExecutionPolicy.POST_ACCEPTANCE_WORKSPACE
                if two_writers
                else SkillExecutionPolicy.INLINE_READ_ONLY
            ),
            budget_policy=(
                SkillBudgetPolicy.TASK_PRECHARGE_AGENT_MODEL
                if two_writers
                else SkillBudgetPolicy.TASK_PRECHARGE
            ),
        )
    )
    return registry


def _write_intent() -> tuple[dict, dict]:
    intent, governor = _intent()
    intent["id"] = "intent-ne01-plan-workspace"
    intent["request_id"] = "request-ne01-plan-workspace"
    intent["session_id"] = "session-ne01-plan-workspace"
    intent["person_id"] = "person-ne01-plan-workspace"
    intent["principal_id"] = "person-ne01-plan-workspace"
    intent["task"] = "inspect and write one reviewed code change"
    intent["args"] = {
        "task": intent["task"],
        "planning_mode": CAPABILITY_PLANNING_MODE,
        "planning_capability_tags": ["coding"],
        "planning_max_risk_level": 4,
    }
    governor = {
        **governor,
        "request_id": intent["request_id"],
        "intent_id": intent["id"],
        "approved_action_binding": build_governed_action_binding(
            intent_envelope=intent,
            approval_token_ref="approval-plan-workspace",
            risk_level=4,
            issued_at_ms=1_000,
            expires_at_ms=61_000,
        ),
    }
    return intent, governor


def test_registry_capability_search_generates_competing_paths_without_planned_steps() -> None:
    intent, governor = _intent()
    before = deepcopy(intent)
    adapter = build_capability_plan_search(
        intent,
        governor_result=governor,
        skill_registry=_registry(),
        input_refs=("input://sha256/" + "a" * 64,),
        frozen_context={"user_message": intent["task"]},
    )

    result = adapter.search(adapter.request)
    context = adapter.compilation_context_for(result)
    compiled = PlanningGraphCompiler().compile(
        result,
        created_at=1_234,
        compilation_context=context,
    )

    assert intent == before
    assert result.mcts_used is True
    assert result.side_effects_executed is False
    assert len(result.selected_path) == 3
    execution_path = result.selected_path[:-1]
    selected_capability_ids = tuple(
        item.payload["capability_id"] for item in execution_path
    )
    assert len(set(selected_capability_ids)) == 2
    assert [item.candidate_id for item in execution_path] == [
        f"capability.execute.{capability_id}"
        for capability_id in selected_capability_ids
    ]
    assert result.selected_path[-1].candidate_id.startswith("capability.synthesize.")
    assert tuple(result.selected_path[-1].payload["capability_ids"]) == (
        selected_capability_ids
    )
    assert [node.route.split(":", 1)[0] for node in compiled.task_graph.nodes] == [
        "skill",
        "skill",
        "chat",
    ]
    assert compiled.task_graph.nodes[1].dependencies == (
        compiled.task_graph.nodes[0].node_id,
    )
    assert compiled.task_graph.nodes[2].dependencies == (
        compiled.task_graph.nodes[0].node_id,
        compiled.task_graph.nodes[1].node_id,
    )
    assert [node.output_contract["required"] for node in compiled.task_graph.nodes] == [
        ["receipt_ref", "narrative"],
        ["receipt_ref", "narrative"],
        ["receipt_ref", "narrative"],
    ]
    assert compiled.work_order.input_pack["capability_planning"]["catalog_ref"] == (
        adapter.snapshot.catalog_ref
    )
    assert compiled.work_order.input_pack["intent_draft"] == intent
    assert compiled.work_order.input_pack["intent_draft"] is not intent
    assert set(compiled.work_order.input_pack["approved_candidate_inputs"]) == {
        item.candidate_id for item in result.selected_path
    }
    assert compiled.work_order.allowed_actions == (
        *(f"skill:{capability_id}" for capability_id in selected_capability_ids),
        "render_reply",
    )


def test_capability_plan_freezes_and_reopens_execution_budget_and_effect_owner() -> None:
    intent, governor = _intent()
    registry = _registry()
    input_ref = "input://sha256/" + "9" * 64
    adapter = build_capability_plan_search(
        intent,
        governor_result=governor,
        skill_registry=registry,
        input_refs=(input_ref,),
        frozen_context={"user_message": intent["task"]},
    )
    result = adapter.search(adapter.request)
    compiled = PlanningGraphCompiler().compile(
        result,
        created_at=1_234,
        compilation_context=adapter.compilation_context_for(result),
    )
    bound = bind_governed_plan_execution_policy(
        compiled,
        skill_registry=registry,
    )
    order = bound.work_order
    graph = bound.task_graph
    command = build_host_command(
        surface=HostCommandSurface.HOST_INPUT,
        request_id=intent["request_id"],
        session_id=intent["session_id"],
        principal_id=intent["principal_id"],
        ingress_claim_ref=None,
        ingress_audit_ref=None,
        governor_grant_ref=order.approval_ref,
        input_ref=input_ref,
        input_sha256="sha256:" + "9" * 64,
        root_work_order_id=graph.root_work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        route_type="plan",
        target_id="planning_search",
        business_scope=order.business_idempotency_scope,
        operation_fingerprint=order.operation_fingerprint,
        created_at_ms=1_234,
    )

    policy = require_governed_plan_execution_policy(
        command=command,
        work_order=order,
        graph=graph,
        skill_registry=registry,
    )

    assert policy["result_node_id"] == graph.result_node_id
    assert policy["work_order_budget_digest"].startswith("sha256:")
    assert [node["effect_mode"] for node in policy["nodes"][:-1]] == [
        "SPECULATIVE_EFFECT_FREE",
        "SPECULATIVE_EFFECT_FREE",
    ]
    assert policy["nodes"][-1]["effect_mode"] == PLAN_RESULT_EFFECT_MODE
    assert all(
        node["execution_policy"] == "INLINE_READ_ONLY"
        for node in policy["nodes"][:-1]
    )
    assert _skill_companion_presentation_allowed(command) is False
    assert (
        _skill_companion_presentation_allowed(
            replace(command, route_type="skill", target_id="research_fast")
        )
        is True
    )

    tampered_metadata = dict(order.metadata)
    tampered_policy = dict(
        tampered_metadata[PLAN_EXECUTION_POLICY_METADATA_KEY]
    )
    tampered_policy["work_order_budget_digest"] = "sha256:" + "0" * 64
    tampered_metadata[PLAN_EXECUTION_POLICY_METADATA_KEY] = tampered_policy
    with pytest.raises(GovernedPlanExecutionPolicyError):
        require_governed_plan_execution_policy(
            command=command,
            work_order=replace(order, metadata=tampered_metadata),
            graph=graph,
            skill_registry=registry,
        )

    with pytest.raises(AcceptanceEffectCompileError):
        compile_graph_acceptance_effects(
            command=command,
            work_order=order,
            node_id=graph.nodes[0].node_id,
            attempt_id="attempt-plan-non-result",
            worker_commit={
                "decision": "APPROVE",
                "narrative": "must not own effects",
            },
        )

    incomplete_registry = _registry(include_second=False)
    with pytest.raises(GovernedPlanExecutionPolicyError):
        require_governed_plan_execution_policy(
            command=command,
            work_order=order,
            graph=graph,
            skill_registry=incomplete_registry,
        )


def test_capability_plan_freezes_one_reviewed_workspace_effect_owner() -> None:
    intent, governor = _write_intent()
    registry = _write_registry()
    adapter = build_capability_plan_search(
        intent,
        governor_result=governor,
        skill_registry=registry,
        input_refs=("input://sha256/" + "8" * 64,),
        frozen_context={"user_message": intent["task"]},
    )
    result = adapter.search(adapter.request)
    compiled = PlanningGraphCompiler().compile(
        result,
        created_at=1_234,
        compilation_context=adapter.compilation_context_for(result),
    )
    bound = bind_governed_plan_execution_policy(
        compiled,
        skill_registry=registry,
    )
    policy = bound.work_order.metadata[PLAN_EXECUTION_POLICY_METADATA_KEY]
    workspace_nodes = [
        node
        for node in policy["nodes"][:-1]
        if node["effect_mode"] == PLAN_NODE_WORKSPACE_EFFECT_MODE
    ]

    assert len(workspace_nodes) == 1
    assert workspace_nodes[0]["target_id"] == "agent_worker"
    assert workspace_nodes[0]["execution_policy"] == (
        SkillExecutionPolicy.POST_ACCEPTANCE_WORKSPACE.value
    )
    assert workspace_nodes[0]["review_required"] is True
    assert workspace_nodes[0]["risk_level"] == 4
    assert pre_execution_review_required(
        bound.work_order,
        node_id=workspace_nodes[0]["node_id"],
    )
    assert all(
        node["effect_mode"] == "SPECULATIVE_EFFECT_FREE"
        for node in policy["nodes"][:-1]
        if node["node_id"] != workspace_nodes[0]["node_id"]
    )


def test_plan_workspace_proposal_compiles_only_at_graph_acceptance() -> None:
    intent, governor = _write_intent()
    registry = _write_registry()
    input_ref = "input://sha256/" + "6" * 64
    fact = WorldFactV2(
        workspace_id="workspace-plan-unit",
        subject_id=intent["principal_id"],
        key="project.mode",
        value="implementation",
        source_ref="world-observation://plan-unit/1",
        confidence=1.0,
        observed_at_ms=1_234,
        expires_at_ms=61_234,
    )
    world = WorldSnapshotV2(
        workspace_id=fact.workspace_id,
        subject_id=fact.subject_id,
        revision=1,
        facts=(fact,),
    )
    adapter = build_capability_plan_search(
        intent,
        governor_result=governor,
        skill_registry=registry,
        input_refs=(input_ref,),
        frozen_context={
            "runtime_context_schema": "natsume.runtime-context.request.v1",
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
            "user_message": intent["task"],
        },
    )
    result = adapter.search(adapter.request)
    bound = bind_governed_plan_execution_policy(
        PlanningGraphCompiler().compile(
            result,
            created_at=1_234,
            compilation_context=adapter.compilation_context_for(result),
        ),
        skill_registry=registry,
    )
    order = bound.work_order
    graph = bound.task_graph
    command = build_host_command(
        surface=HostCommandSurface.HOST_INPUT,
        request_id=intent["request_id"],
        session_id=intent["session_id"],
        principal_id=intent["principal_id"],
        ingress_claim_ref=None,
        ingress_audit_ref=None,
        governor_grant_ref=order.approval_ref,
        input_ref=input_ref,
        input_sha256="sha256:" + "6" * 64,
        root_work_order_id=graph.root_work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        route_type="plan",
        target_id="planning_search",
        business_scope=order.business_idempotency_scope,
        operation_fingerprint=order.operation_fingerprint,
        created_at_ms=1_234,
    )
    policy = order.metadata[PLAN_EXECUTION_POLICY_METADATA_KEY]
    workspace_policy = next(
        node
        for node in policy["nodes"][:-1]
        if node["effect_mode"] == PLAN_NODE_WORKSPACE_EFFECT_MODE
    )
    speculative_policy = next(
        node
        for node in policy["nodes"][:-1]
        if node["effect_mode"] == "SPECULATIVE_EFFECT_FREE"
    )
    descriptor = {
        "effect_type": AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
        "commit_scope": "NODE",
        "payload": {
            "schema_version": AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA,
            "work_order_id": order.work_order_id,
            "target_id": workspace_policy["target_id"],
            "operation_fingerprint": derive_node_operation_fingerprint(
                order.operation_fingerprint,
                node_id=workspace_policy["node_id"],
                multiple_nodes=True,
            ),
            "protected_artifact": {
                "schema_version": "Natsume.agent.governed_artifact_stage.v1",
                "ref": "agent-stage://workspace-plan/attempt-plan",
                "digest": "sha256:" + "a" * 64,
            },
            "result_digest": "sha256:" + "b" * 64,
        },
    }
    worker_commit = {
        "decision": "APPROVE",
        "request_id": command.request_id,
        "narrative": "reviewed Workspace proposal",
        "receipt_ref": "worker-projection://plan/workspace",
        "audit_refs": {
            "node_grant_ref": "governor-decision://sha256/" + "c" * 64,
            "node_grant_digest": "sha256:" + "d" * 64,
        },
        "acceptance_effect_intents": [descriptor],
    }

    effects = compile_plan_node_acceptance_effects(
        command=command,
        work_order=order,
        node_id=workspace_policy["node_id"],
        attempt_id="attempt-plan-workspace",
        worker_commit=worker_commit,
    )

    assert len(effects) == 1
    assert effects[0].effect_type == AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE
    assert effects[0].node_id == workspace_policy["node_id"]
    assert effects[0].payload["target_id"] == "agent_worker"
    assert effects[0].commit_scope.value == "GRAPH"
    assert compile_plan_node_acceptance_effects(
        command=command,
        work_order=order,
        node_id=speculative_policy["node_id"],
        attempt_id="attempt-plan-speculative-foreground",
        worker_commit={
            **worker_commit,
            "acceptance_effect_intents": [
                {
                    "effect_type": "life.foreground_completed",
                    "commit_scope": "NODE",
                    "payload": {
                        "command_type": "foreground_completed",
                        "payload": {"assistant_reply": "internal only"},
                    },
                }
            ],
        },
    ) == ()
    with pytest.raises(
        AcceptanceEffectCompileError,
        match="speculative Plan node",
    ):
        compile_plan_node_acceptance_effects(
            command=command,
            work_order=order,
            node_id=speculative_policy["node_id"],
            attempt_id="attempt-plan-speculative",
            worker_commit=worker_commit,
        )


def test_capability_plan_rejects_two_workspace_effect_owners() -> None:
    intent, governor = _write_intent()
    registry = _write_registry(two_writers=True)
    adapter = build_capability_plan_search(
        intent,
        governor_result=governor,
        skill_registry=registry,
        input_refs=("input://sha256/" + "7" * 64,),
        frozen_context={"user_message": intent["task"]},
    )
    result = adapter.search(adapter.request)
    compiled = PlanningGraphCompiler().compile(
        result,
        created_at=1_234,
        compilation_context=adapter.compilation_context_for(result),
    )

    with pytest.raises(
        GovernedPlanExecutionPolicyError,
        match="at most one",
    ):
        bind_governed_plan_execution_policy(
            compiled,
            skill_registry=registry,
        )


def test_capability_plan_rejects_a_noncanonical_target_before_search() -> None:
    intent, governor = _intent()
    intent["target_id"] = "arbitrary_target"

    assert is_capability_plan_intent(intent) is False
    with pytest.raises(CapabilityPlanSearchError) as caught:
        build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=_registry(),
            input_refs=("input://sha256/" + "a" * 64,),
        )

    assert caught.value.reason_code == CAPABILITY_PLAN_SEARCH_INVALID


@pytest.mark.parametrize("legacy_field", ("planned_steps", "steps"))
@pytest.mark.parametrize("nested", (False, True))
def test_capability_mode_is_explicit_and_refuses_legacy_preplanned_graphs(
    legacy_field: str,
    nested: bool,
) -> None:
    intent, governor = _intent()
    if nested:
        intent["args"][legacy_field] = [{"candidate_id": "smuggled"}]
    else:
        intent[legacy_field] = [{"candidate_id": "smuggled"}]

    assert is_capability_plan_intent(intent) is False
    with pytest.raises(CapabilityPlanSearchError) as caught:
        build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=_registry(),
            input_refs=("input://sha256/" + "b" * 64,),
        )

    assert caught.value.reason_code == CAPABILITY_PLAN_SEARCH_INVALID
    assert "legacy" in caught.value.detail


def test_registry_requires_two_non_confirming_eligible_capabilities() -> None:
    intent, governor = _intent()

    with pytest.raises(CapabilityPlanSearchError) as caught:
        build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=_registry(include_second=False),
            input_refs=("input://sha256/" + "c" * 64,),
        )

    assert caught.value.reason_code == CAPABILITY_PLAN_SEARCH_NO_COMPETITION


def test_confirm_required_registry_capability_compiles_exact_node_review_policy() -> None:
    intent, _governor = _intent()
    intent["args"]["planning_max_risk_level"] = 3
    governor = {
        "request_id": intent["request_id"],
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "reason_code": "APPROVED",
        "approved_action_binding": build_governed_action_binding(
            intent_envelope=intent,
            approval_token_ref="approval-node-review-policy",
            risk_level=3,
            issued_at_ms=1_000,
            expires_at_ms=61_000,
        ),
        "ts_ms": 1_000,
    }
    adapter = build_capability_plan_search(
        intent,
        governor_result=governor,
        skill_registry=_registry(include_second=False),
        input_refs=("input://sha256/" + "7" * 64,),
    )

    assert {
        item.capability_id: item.requires_review
        for item in adapter.snapshot.capabilities
    } == {
        "research_confirm_required": True,
        "research_fast": False,
    }
    result = adapter.search(adapter.request)
    compiled = PlanningGraphCompiler().compile(
        result,
        created_at=1_234,
        compilation_context=adapter.compilation_context_for(result),
    )
    node_policies = compiled.work_order.review_policy[NODE_REVIEW_POLICIES_KEY]
    assert set(node_policies) == {
        node.node_id for node in compiled.task_graph.nodes
    }
    required_nodes = {
        node.node_id
        for node in compiled.task_graph.nodes
        if pre_execution_review_required(
            compiled.work_order,
            node_id=node.node_id,
        )
    }
    assert len(required_nodes) == 1
    required_node = next(
        node
        for node in compiled.task_graph.nodes
        if node.node_id in required_nodes
    )
    assert required_node.route == "skill:research_confirm_required"
    assert all(
        policy["source_ref"] == adapter.snapshot.catalog_ref
        for policy in node_policies.values()
    )
    assert "candidate_review_policies" not in compiled.work_order.review_policy

    missing_policy = compiled.work_order.to_dict()
    del missing_policy["review_policy"][NODE_REVIEW_POLICIES_KEY][
        required_node.node_id
    ]
    with pytest.raises(ContractViolation):
        pre_execution_review_required(
            WorkOrderV3.from_mapping(missing_policy),
            node_id=required_node.node_id,
        )

    malformed_policy = compiled.work_order.to_dict()
    malformed_policy["review_policy"][NODE_REVIEW_POLICIES_KEY][
        required_node.node_id
    ]["review_required"] = "true"
    with pytest.raises(ContractViolation):
        pre_execution_review_required(
            WorkOrderV3.from_mapping(malformed_policy),
            node_id=required_node.node_id,
        )


def test_governor_risk_ceiling_tightens_the_frozen_candidate_catalog() -> None:
    intent, governor = _intent()
    intent["args"]["planning_max_risk_level"] = 3
    governor["approved_action_binding"] = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref="approval-risk-ceiling",
        risk_level=1,
        issued_at_ms=1_000,
        expires_at_ms=61_000,
    )

    adapter = build_capability_plan_search(
        intent,
        governor_result=governor,
        skill_registry=_registry(include_low_risk_alternative=True),
        input_refs=("input://sha256/" + "f" * 64,),
    )

    assert adapter.snapshot.max_risk_level == 1
    assert len(adapter.snapshot.source_refs) == 1
    assert adapter.snapshot.source_refs[0].startswith("governor-decision://sha256/")
    assert adapter.snapshot.source_refs[0] in adapter.snapshot.to_virtual_state().source_refs
    assert {item.capability_id for item in adapter.snapshot.capabilities} == {
        "research_fast",
        "research_safe_crosscheck",
    }
    assert all(item.risk_level <= 1 for item in adapter.snapshot.capabilities)
    assert dict(adapter.base_context.input_pack["capability_plan_request"]) == {
        "route": "plan",
        "planning_mode": CAPABILITY_PLANNING_MODE,
        "task": intent["task"],
        "requested_tags": ("research",),
        "requested_max_risk_level": 3,
        "governor_max_risk_level": 1,
        "effective_max_risk_level": 1,
        "execution_steps": 2,
    }


def test_capability_plan_rejects_a_missing_or_invalid_governor_risk_ceiling() -> None:
    intent, governor = _intent()
    governor["approved_action_binding"]["risk_level"] = 0

    with pytest.raises(CapabilityPlanSearchError) as caught:
        build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=_registry(),
            input_refs=("input://sha256/" + "1" * 64,),
        )

    assert caught.value.reason_code == CAPABILITY_PLAN_SEARCH_AUTHORITY_INVALID


def test_capability_mode_rejects_non_ascii_requested_tags() -> None:
    intent, governor = _intent()
    intent["args"]["planning_capability_tags"] = ["研究"]
    governor["approved_action_binding"] = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref="approval-not-in-search-candidates",
        risk_level=2,
        issued_at_ms=1_000,
        expires_at_ms=61_000,
    )

    with pytest.raises(CapabilityPlanSearchError) as caught:
        build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=_registry(),
            input_refs=("input://sha256/" + "e" * 64,),
        )

    assert caught.value.reason_code == CAPABILITY_PLAN_SEARCH_INVALID


def test_catalog_rejects_forged_capability_candidate_before_compilation() -> None:
    snapshot = CapabilityPlanningSnapshot(
        task_ref="input://sha256/" + "d" * 64,
        objective="collect bounded research evidence",
        requested_tags=("research",),
        max_risk_level=2,
        capabilities=(
            PlanningCapability(
                capability_id="research_fast",
                family="research",
                capability_tags=("research",),
                risk_level=1,
                reversible=True,
                parallelizable=True,
                estimated_cost=0.1,
            ),
            PlanningCapability(
                capability_id="research_crosscheck",
                family="research",
                capability_tags=("research",),
                risk_level=2,
                reversible=True,
                parallelizable=False,
                estimated_cost=0.2,
            ),
        ),
    )
    catalog = CapabilitySuccessorCatalog(snapshot=snapshot)
    state = snapshot.to_virtual_state()
    forged = SearchCandidate(
        candidate_id="capability.execute.research_fast",
        prior_score=99.0,
        payload={"capability_action": "execute", "capability_id": "research_fast"},
    )

    with pytest.raises(CapabilityPlanningError):
        catalog.transition(
            state=state,
            candidate=forged,
            path=(forged,),
            context={},
        )
