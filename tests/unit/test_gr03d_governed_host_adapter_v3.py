from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, get_type_hints

import pytest

from host_brain.app.governed_task_adapter import (
    GOVERNED_HOST_AUTHORITY_INVALID,
    GovernedHostAdapterError,
    GovernedHostRuntime,
    _FrozenSkillWorker,
    _build_contracts,
    _require_fail_closed_direct_skill_work_order,
)
from host_brain.contracts.agent_runtime.budget_policy import BudgetExceededAction
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3


def _contracts(*, request_id: str = "request-gr03d-direct-skill") -> tuple:
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-gr03d-direct-skill",
        "route": "skill",
        "target_id": "echo_skill",
        "task": "execute the admitted direct skill",
        "args": {"text": "preserve me", "nested": {"value": 7}},
    }
    governor = {
        "request_id": request_id,
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "tokens": {},
        "ts_ms": 1_800_000_000_000,
    }
    return _build_contracts(
        request_body={},
        request_id=governor["request_id"],
        session_id="session-gr03d-direct-skill",
        ts_ms=governor["ts_ms"],
        intent_draft=intent,
        governor_result=governor,
        person_id="person-gr03d",
        sanitized_message="execute the admitted direct skill",
        emotion_context={"tone": "calm", "confidence": 0.8},
        render_target_lang="zh",
        command_authority=None,
    )


def test_direct_skill_builds_explicit_fail_closed_work_order_v3() -> None:
    command, order, graph = _contracts()

    assert isinstance(order, WorkOrderV3)
    assert order.schema_version == "Natsume.agent.work_order.v3"
    assert order.budget_policy is not None
    assert order.budget_policy.on_exceeded is BudgetExceededAction.BLOCK
    assert order.budget_policy.metadata["policy_version"] == "governed_direct_skill_budget.v1"
    assert all(value > 0 for value in order.budget_policy.limits.to_dict().values())
    assert order.budget_policy.limits.to_dict() == {
        "token_count": 16_384,
        "tool_call_count": 8,
        "time_ms": 120_000,
        "concurrency": 1,
        "retry_count": 1,
        "model_cost_micros": 500_000,
    }

    assert order.root_work_order_id == command.root_work_order_id == order.work_order_id
    assert order.parent_work_order_id is None
    assert order.task_graph_id == command.graph_id == graph.graph_id
    assert order.task_node_id == graph.nodes[0].node_id
    assert order.graph_revision == command.graph_revision == graph.revision == 1
    assert order.attempt == 0
    assert order.capability_requirements == (command.target_id,)
    assert order.approval_ref == command.governor_grant_ref
    assert order.review_ref.startswith("review-waiver://")
    assert order.review_ref != command.governor_grant_ref
    assert order.business_idempotency_scope == command.business_scope
    assert order.operation_fingerprint == command.operation_fingerprint
    assert order.input_refs == (command.input_ref, command.governor_grant_ref)
    assert order.output_contract == graph.nodes[0].output_contract
    assert graph.nodes[0].route == f"skill:{command.target_id}"
    assert order.metadata["planning_lineage"] == {
        "graph_id": order.task_graph_id,
        "root_work_order_id": order.root_work_order_id,
        "revision": order.graph_revision,
        "parent_revision": None,
        "approval_ref": order.approval_ref,
        "review_ref": order.review_ref,
        "node_bindings": [
            {
                "node_id": order.task_node_id,
                "objective": order.objective,
                "worker_route": "skill",
                "target_id": command.target_id,
            }
        ],
    }
    assert order.metadata["lineage"] == {
        "root_work_order_id": order.root_work_order_id,
        "task_graph_id": order.task_graph_id,
        "task_node_id": order.task_node_id,
        "graph_revision": order.graph_revision,
        "attempt": order.attempt,
        "capability_requirements": list(order.capability_requirements),
        "approval_ref": order.approval_ref,
        "review_ref": order.review_ref,
        "business_idempotency_scope": order.business_idempotency_scope,
        "operation_fingerprint": order.operation_fingerprint,
    }


def test_direct_skill_operation_fingerprint_is_scoped_to_canonical_request() -> None:
    first = _contracts(request_id="request-gr03d-one")[0]
    replay = _contracts(request_id="request-gr03d-one")[0]
    independent = _contracts(request_id="request-gr03d-two")[0]

    assert first.operation_fingerprint == replay.operation_fingerprint
    assert first.operation_fingerprint != independent.operation_fingerprint


def test_work_order_v3_roundtrip_preserves_existing_direct_skill_input_pack() -> None:
    _command, order, _graph = _contracts()
    restored = WorkOrderV3.from_mapping(order.to_dict())

    assert restored.to_dict() == order.to_dict()
    assert restored.input_pack == order.input_pack
    assert restored.input_pack["intent_draft"]["args"] == {
        "text": "preserve me",
        "nested": {"value": 7},
    }
    assert restored.input_pack["emotion_context"] == {
        "tone": "calm",
        "confidence": 0.8,
    }
    _require_fail_closed_direct_skill_work_order(restored)


def test_direct_skill_worker_is_typed_for_v3_and_rejects_missing_budget() -> None:
    _command, order, _graph = _contracts()
    hints = get_type_hints(_FrozenSkillWorker.execute_stream)

    assert hints["work_order"] is WorkOrderV3
    with pytest.raises(GovernedHostAdapterError) as raised:
        _require_fail_closed_direct_skill_work_order(replace(order, budget_policy=None))
    assert raised.value.reason_code == GOVERNED_HOST_AUTHORITY_INVALID
    assert "BudgetPolicy" in raised.value.detail


@pytest.mark.asyncio
async def test_review_renewed_host_skill_worker_verifies_exact_attempt_and_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, order, graph = _contracts()
    node = graph.nodes[0]
    exact_execution = {
        "attempt_id": "attempt::review-renewed-host-skill",
        "lease_id": "lease::review-renewed-host-skill",
        "lease_owner_id": "host-skill-worker-7",
        "fencing_token": 7,
    }
    child_grant_ref = "governor-decision://sha256/" + "b" * 64
    child_intent_id = "node-grant-intent::review-renewed-host-skill"

    class _ExactReviewGrantVerifier:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def verify(self, **kwargs: Any) -> Any:
            self.calls.append(dict(kwargs))
            assert {
                field: kwargs.get(field) for field in exact_execution
            } == exact_execution
            return SimpleNamespace(
                grant_ref=child_grant_ref,
                intent_id=child_intent_id,
                to_dict=lambda: {
                    "grant_ref": child_grant_ref,
                    "intent_id": child_intent_id,
                    "review_continuation_authority": {
                        "review_id": "review-host-skill",
                        "resume_phase": "PRE_EXECUTION",
                    },
                },
            )

    class _Ledger:
        @staticmethod
        def get_event(receipt_ref: str) -> dict[str, Any]:
            assert receipt_ref == "skill-receipt://review-renewed-host-skill"
            return {"result_status": "SUCCESS"}

    monkeypatch.setattr(
        "host_brain.app.governed_frozen_skill_runtime._load_governor_grant",
        lambda **_kwargs: {
            "snapshot_refs": {"node_intent": {"id": child_intent_id}},
            "tokens": {},
        },
    )
    monkeypatch.setattr(
        "host_brain.app.governed_frozen_skill_runtime.execute_frozen_governed_skill",
        lambda **_kwargs: SimpleNamespace(
            payload={
                "receipt_ref": "skill-receipt://review-renewed-host-skill",
                "result": {},
            },
            result_text="review-renewed skill completed",
            success=True,
            error_text=None,
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.governed_frozen_skill_runtime.record_governed_skill_execution",
        lambda **_kwargs: None,
    )
    verifier = _ExactReviewGrantVerifier()
    worker = _FrozenSkillWorker(
        ledger=_Ledger(),
        execution_ports=SimpleNamespace(),
        node_grant_verifier=verifier,  # type: ignore[arg-type]
    )
    execution_context = {
        "command_id": command.command_id,
        "work_order_id": order.work_order_id,
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "node_id": node.node_id,
        "worker_route": node.route,
        "node_input_refs": list(node.input_refs),
        "node_grant_ref": child_grant_ref,
        **exact_execution,
    }

    events = [
        event
        async for event in worker.execute_stream(
            command=command,
            work_order=order,
            execution_context=execution_context,
        )
    ]

    assert len(events) == 1
    assert events[0]["decision"] == "APPROVE"
    assert verifier.calls[0]["grant_ref"] == child_grant_ref


def test_universal_runtime_supports_only_registered_frozen_routes() -> None:
    runtime = GovernedHostRuntime(
        application=SimpleNamespace(),
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=SimpleNamespace(),
        supported_routes=frozenset({"chat", "skill", "delegate"}),
        planning_enabled=True,
    )

    assert runtime.supports({"route": "skill", "target_id": "echo_skill"}) is True
    assert runtime.supports({"route": "skill"}) is False
    assert runtime.supports({"route": "chat"}) is True
    assert runtime.supports({"route": "delegate", "target_id": "intent_agent"}) is True
    assert runtime.supports({"route": "plan", "planned_steps": [{"step": 1}]}) is True
    assert runtime.supports({"route": "plan"}) is False
    assert runtime.supports({"route": "task", "target_id": "echo_skill"}) is False
