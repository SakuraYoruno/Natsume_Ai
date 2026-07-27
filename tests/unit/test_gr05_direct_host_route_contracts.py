from __future__ import annotations

import json

import pytest

from host_brain.app.governed_delegate_dispatch import GovernedDelegateTarget
from host_brain.contracts.agent_runtime.delegate_usage_attestation import (
    READONLY_DELEGATE_EXECUTION_MODE,
)
from host_brain.contracts.agent_runtime.control_route_execution_policy import (
    CONTROL_ROUTE_EXECUTION_POLICY_KEY,
    CONTROL_ROUTE_INTENT_REVIEW_REQUIRED,
    ControlRouteExecutionPolicyError,
    freeze_control_route_execution_policy,
    require_control_route_execution_policy,
    validate_control_route_execution_policy,
)
from host_brain.orchestration.host_route_contracts import (
    HOST_ROUTE_AUTHORITY_INVALID,
    HOST_ROUTE_UNSUPPORTED,
    HostRouteContractError,
    build_direct_host_route_contracts,
)
from natsume_contracts.task import HostCommandSurface


def _governor(
    route: str,
    target: str = "",
    *,
    request_id: str = "request-direct-route",
) -> dict:
    return {
        "request_id": request_id,
        "intent_id": "intent-direct-route",
        "decision": "APPROVE",
        "reason_code": "APPROVE",
        "risk_level": 2,
        "tokens": {"approval_token_ref": "approval::private"},
        "approved_action_binding": {
            "binding_ref": "governed-action://sha256/" + "a" * 64,
            "route": route,
            "target_id": target,
        },
        "ts_ms": 100,
    }


def _build(
    route: str,
    target: str = "",
    *,
    request_id: str = "request-direct-route",
    delegate_target_policy: dict | None = None,
):
    return build_direct_host_route_contracts(
        surface=HostCommandSurface.CHAT,
        request_id=request_id,
        session_id="session-direct-route",
        principal_id="principal-direct-route",
        ts_ms=100,
        intent_draft={
            "id": "intent-direct-route",
            "route": route,
            "target_id": target,
            "action": "exec_intent",
            "target": "host",
            "task": "answer or execute the frozen request",
            "args": {"query": "safe"},
        },
        governor_result=_governor(route, target, request_id=request_id),
        sanitized_message="hello",
        person_id="person-direct-route",
        emotion_context={"emotion_tag": "NEUTRAL"},
        delegate_target_policy=delegate_target_policy,
    )


def _governed_delegate_policy(target_id: str = "pentest_agent") -> dict:
    return GovernedDelegateTarget(
        target_id=target_id,
        handler=lambda _payload, _context: {},
        adapter_id="readonly-pentest-domain-handoff.v1",
        execution_mode=READONLY_DELEGATE_EXECUTION_MODE,
    ).policy_projection()


def _build_operator_control(
    *,
    command_id: str = "task_graph.cancel",
    target_id: str = "operator_task_control",
):
    policy = freeze_control_route_execution_policy(
        surface=HostCommandSurface.OPERATOR,
        control_command_id=command_id,
        target_id=target_id,
        review_required=False,
        intent_review_required=True,
    )
    contracts = build_direct_host_route_contracts(
        surface=HostCommandSurface.OPERATOR,
        request_id="request-operator-control-policy",
        session_id="session-operator-control-policy",
        principal_id="principal-operator-control-policy",
        ts_ms=100,
        intent_draft={
            "id": "intent-direct-route",
            "route": "delegate",
            "target_id": target_id,
            "action": "operator_task_graph_cancellation",
            "target": "task_runtime",
            "task": "execute one frozen Operator control",
            "args": {"operator_command_id": command_id},
        },
        governor_result=_governor(
            "delegate",
            target_id,
            request_id="request-operator-control-policy",
        ),
        sanitized_message="execute one frozen Operator control",
        person_id="principal-operator-control-policy",
        ingress_claim_ref="ingress-claim://operator/control-policy",
        ingress_audit_ref="audit://operator/control-policy",
        operator_command_id=command_id,
        control_route_execution_policy=policy,
    )
    return contracts, policy


@pytest.mark.parametrize(
    ("route", "target", "expected_target", "domain"),
    [
        ("chat", "ignored", "surface_reply", "chat"),
        ("delegate", "research_agent", "research_agent", "research"),
    ],
)
def test_direct_chat_and_delegate_compile_same_governed_contracts(
    route: str,
    target: str,
    expected_target: str,
    domain: str,
) -> None:
    first = _build(route, target)
    replay = _build(route, target)

    assert first.command.to_dict() == replay.command.to_dict()
    assert first.work_order.to_dict() == replay.work_order.to_dict()
    assert first.graph.to_dict() == replay.graph.to_dict()
    assert first.command.route_type == route
    assert first.command.target_id == expected_target
    assert first.graph.nodes[0].route == f"{route}:{expected_target}"
    assert first.graph.result_node_id == first.graph.nodes[0].node_id
    assert first.work_order.domain == domain
    assert first.work_order.budget_policy is not None
    assert first.work_order.budget_policy.limits.time_ms > 0
    assert first.work_order.review_policy["mode"] == "governor_grant_review_waiver"
    assert first.work_order.review_ref.startswith("review-waiver://")
    assert first.work_order.review_ref != first.work_order.approval_ref
    binding = first.work_order.metadata["planning_lineage"]["node_bindings"][0]
    assert binding["node_id"] == first.graph.nodes[0].node_id
    assert binding["worker_route"] == route
    assert binding["target_id"] == expected_target
    assert "planned_steps" not in json.dumps(first.work_order.to_dict(), sort_keys=True)
    assert "approval::private" not in json.dumps(first.work_order.to_dict(), sort_keys=True)


def test_enabled_readonly_pentest_delegate_receives_no_model_or_tool_authority() -> None:
    contracts = _build("delegate", "pentest_agent")
    policy = contracts.work_order.budget_policy

    assert policy is not None
    assert policy.limits.to_dict() == {
        "token_count": 0,
        "tool_call_count": 1,
        "time_ms": 300_000,
        "concurrency": 1,
        "retry_count": 1,
        "model_cost_micros": 0,
    }
    assert (
        policy.metadata["measurement_mode"]
        == "PORT_FREE_READ_ONLY_ATTESTED_ZERO"
    )
    assert policy.metadata["enabled_target"] == "pentest_agent"


def test_enabled_delegate_freezes_target_policy_and_requires_durable_review() -> None:
    target_policy = _governed_delegate_policy()
    contracts = _build(
        "delegate",
        "pentest_agent",
        delegate_target_policy=target_policy,
    )

    assert contracts.work_order.input_pack["delegate_target_policy"] == (
        target_policy
    )
    assert contracts.work_order.metadata["delegate_target_policy"] == (
        target_policy
    )
    assert contracts.work_order.review_policy == {
        "review_required": True,
        "durable_review_required": True,
        "mode": "task_node_pre_execution_review",
        "reason": "delegate_target_policy_requires_operator_review",
        "review_ref": contracts.work_order.review_ref,
        "frozen_delegate_policy_ref": target_policy["policy_ref"],
    }
    assert contracts.work_order.review_ref.startswith(
        "review-policy://task-node/delegate/"
    )

    tampered = dict(target_policy)
    tampered["policy_ref"] = (
        "governed-delegate-policy://sha256/" + "0" * 64
    )
    with pytest.raises(HostRouteContractError) as raised:
        _build(
            "delegate",
            "pentest_agent",
            delegate_target_policy=tampered,
        )
    assert raised.value.reason_code == HOST_ROUTE_AUTHORITY_INVALID


def test_operator_control_policy_is_content_addressed_and_bound_to_graph() -> None:
    contracts, policy = _build_operator_control()

    assert validate_control_route_execution_policy(policy) == policy
    assert (
        contracts.work_order.input_pack[CONTROL_ROUTE_EXECUTION_POLICY_KEY]
        == policy
    )
    assert (
        contracts.work_order.metadata[CONTROL_ROUTE_EXECUTION_POLICY_KEY]
        == policy
    )
    assert contracts.work_order.metadata["control_command_id"] == (
        "task_graph.cancel"
    )
    assert policy["review_mode"] == CONTROL_ROUTE_INTENT_REVIEW_REQUIRED
    assert contracts.work_order.review_policy == {
        "review_required": False,
        "durable_review_required": False,
        "mode": "governor_grant_review_waiver",
        "reason": "durable_intent_admission_review",
        "waiver_authority": "governor_grant",
        "waiver_ref": contracts.work_order.review_ref,
        "frozen_control_policy_ref": policy["policy_ref"],
    }
    assert contracts.work_order.budget_policy is not None
    assert contracts.work_order.budget_policy.metadata[
        "control_policy_ref"
    ] == policy["policy_ref"]
    assert require_control_route_execution_policy(
        command=contracts.command,
        work_order=contracts.work_order,
        graph=contracts.graph,
    ) == policy

    tampered = dict(policy)
    tampered["target_id"] = "operator_state_control"
    with pytest.raises(ControlRouteExecutionPolicyError):
        validate_control_route_execution_policy(tampered)


def test_control_surface_rejects_missing_or_mismatched_execution_policy() -> None:
    policy = freeze_control_route_execution_policy(
        surface=HostCommandSurface.OPERATOR,
        control_command_id="task_graph.cancel",
        target_id="operator_task_control",
        review_required=False,
        intent_review_required=True,
    )
    for candidate in (None, {**policy, "control_command_id": "task_review.resume"}):
        with pytest.raises(HostRouteContractError) as raised:
            build_direct_host_route_contracts(
                surface=HostCommandSurface.OPERATOR,
                request_id="request-operator-policy-rejected",
                session_id="session-operator-policy-rejected",
                principal_id="principal-operator-policy-rejected",
                ts_ms=100,
                intent_draft={
                    "route": "delegate",
                    "target_id": "operator_task_control",
                    "action": "operator_task_graph_cancellation",
                    "target": "task_runtime",
                    "task": "reject missing policy",
                    "args": {"operator_command_id": "task_graph.cancel"},
                },
                governor_result=_governor(
                    "delegate",
                    "operator_task_control",
                    request_id="request-operator-policy-rejected",
                ),
                sanitized_message="reject missing policy",
                person_id="principal-operator-policy-rejected",
                ingress_claim_ref="ingress-claim://operator/policy-rejected",
                ingress_audit_ref="audit://operator/policy-rejected",
                operator_command_id="task_graph.cancel",
                control_route_execution_policy=candidate,
            )
        assert raised.value.reason_code == HOST_ROUTE_AUTHORITY_INVALID


def test_control_policy_rejects_two_independent_review_authorities() -> None:
    with pytest.raises(ControlRouteExecutionPolicyError):
        freeze_control_route_execution_policy(
            surface=HostCommandSurface.OPERATOR,
            control_command_id="task_graph.cancel",
            target_id="operator_task_control",
            review_required=True,
            intent_review_required=True,
        )


@pytest.mark.parametrize(("route", "target"), [("chat", ""), ("delegate", "intent_agent")])
def test_operation_fingerprint_is_stable_for_request_replay_but_not_cross_request(
    route: str,
    target: str,
) -> None:
    first = _build(route, target, request_id="request-fingerprint-one")
    replay = _build(route, target, request_id="request-fingerprint-one")
    independent = _build(route, target, request_id="request-fingerprint-two")

    assert first.command.operation_fingerprint == replay.command.operation_fingerprint
    assert first.command.operation_fingerprint != independent.command.operation_fingerprint


def test_direct_builder_rejects_plan_and_skill_so_they_cannot_bypass_dedicated_paths() -> None:
    for route in ("plan", "skill"):
        with pytest.raises(HostRouteContractError) as raised:
            _build(route, "target")
        assert raised.value.reason_code == HOST_ROUTE_UNSUPPORTED


def test_sensitive_intent_args_are_rejected_before_durable_admission() -> None:
    with pytest.raises(HostRouteContractError) as raised:
        build_direct_host_route_contracts(
            surface=HostCommandSurface.CHAT,
            request_id="request-direct-route",
            session_id="session-direct-route",
            principal_id="principal-direct-route",
            ts_ms=100,
            intent_draft={
                "route": "delegate",
                "target_id": "research_agent",
                "action": "exec_intent",
                "target": "host",
                "args": {"nested": {"apiKey": "secret"}},
            },
            governor_result=_governor("delegate", "research_agent"),
            sanitized_message="hello",
            person_id="person",
        )
    assert raised.value.reason_code == HOST_ROUTE_AUTHORITY_INVALID


def test_direct_chat_freezes_prepared_context_without_delivery_authority() -> None:
    contracts = build_direct_host_route_contracts(
        surface=HostCommandSurface.CHAT,
        request_id="request-direct-route",
        session_id="session-direct-route",
        principal_id="principal-direct-route",
        ts_ms=100,
        intent_draft={
            "id": "intent-direct-route",
            "route": "chat",
            "action": "exec_intent",
            "target": "host",
            "task": "reply",
        },
        governor_result=_governor("chat", ""),
        sanitized_message="hello",
        person_id="person-direct-route",
        frozen_context={
            "emotion_snapshot": {"mood_state": "calm"},
            "retrieval_summary": {"status": "OK"},
            "retrieval_evidence_items": [{"evidence_ref": "memory://one"}],
            "llm_runtime": {"available": True, "provider_name": "mock"},
            "request_body": {"response_form": "text", "attachments": []},
            "delivery_context": {"reply_channel": "gateway", "reply_target": "device-1"},
        },
    )

    pack = contracts.work_order.input_pack
    assert pack["emotion_snapshot"] == {"mood_state": "calm"}
    assert pack["retrieval_evidence_items"] == [{"evidence_ref": "memory://one"}]
    assert pack["llm_runtime"]["provider_name"] == "mock"
    assert pack["delivery_context"] == {
        "reply_channel": "gateway",
        "reply_target": "device-1",
    }
    assert "governor_result" not in pack
    assert "delivery_authorization" not in json.dumps(pack, sort_keys=True)
