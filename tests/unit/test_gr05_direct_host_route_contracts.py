from __future__ import annotations

import json

import pytest

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
            "task": "answer or execute the frozen request",
            "args": {"query": "safe"},
        },
        governor_result=_governor(route, target, request_id=request_id),
        sanitized_message="hello",
        person_id="person-direct-route",
        emotion_context={"emotion_tag": "NEUTRAL"},
    )


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
        intent_draft={"id": "intent-direct-route", "route": "chat", "task": "reply"},
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
