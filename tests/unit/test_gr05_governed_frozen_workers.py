from __future__ import annotations

from typing import Any

import pytest

from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.worker_projection_repository import (
    validate_worker_projection_payload,
)
from host_brain.task_runtime.governed_workers import (
    GOVERNED_NODE_COMMIT_INVALID,
    GOVERNED_NODE_GRANT_INVALID,
    GOVERNED_NODE_INPUT_INVALID,
    GovernedFrozenNodeWorker,
    GovernedNodeWorkerError,
)
from natsume_contracts.task import HostCommandSurface, build_host_command


class _GrantVerifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def verify(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        return {"verified": True, **kwargs}


class _ProjectionStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def stage(self, *, projection_id: str, projection):  # noqa: ANN001
        payload = dict(projection)
        existing = self.rows.get(projection_id)
        if existing is not None and existing != payload:
            raise AssertionError("projection replay drift")
        self.rows[projection_id] = payload
        return payload


class _ValidatingProjectionStore(_ProjectionStore):
    def stage(self, *, projection_id: str, projection):  # noqa: ANN001
        validated = validate_worker_projection_payload(
            projection,
            expected_projection_id=projection_id,
        )
        return super().stage(
            projection_id=projection_id,
            projection=validated,
        )


class _Executor:
    def __init__(self, terminal: dict[str, Any]) -> None:
        self.terminal = terminal
        self.inputs = []

    async def execute_stream(self, frozen):  # noqa: ANN001
        self.inputs.append(frozen)
        yield {"phase": "DELTA", "done": False, "delta": "working"}
        yield dict(self.terminal)


def _active_budget() -> dict[str, Any]:
    """The only budget projection a production frozen worker may consume."""

    return {
        "budget_id": "budget://governed-workers/active",
        "status": "ACTIVE",
        "status_version": 3,
        "remaining": {
            "token_count": 120,
            "time_ms": 1_000,
            "retry_count": 1,
            "model_cost_micros": 4_000,
        },
    }


def _contracts(route: str = "chat", target: str = "surface_reply"):
    graph_id = "graph-governed-workers"
    work_order_id = "wo-governed-workers"
    node_id = "node-governed-workers"
    order = WorkOrderV3(
        work_order_id=work_order_id,
        domain="chat" if route == "chat" else "research",
        run_mode="worker",
        objective="frozen objective",
        input_pack={
            "user_message": "hello",
            "relationship_subject_id": "principal-workers",
            # A caller-owned input pack must never expand the authoritative
            # runtime reservation frozen below.
            "budget": {"remaining": {"token_count": 999_999_999}},
        },
        metadata={
            "planning_lineage": {
                "graph_id": graph_id,
                "root_work_order_id": work_order_id,
                "revision": 1,
                "node_bindings": [
                    {
                        "node_id": node_id,
                        "worker_route": route,
                        "target_id": target,
                        "objective": "answer from frozen input",
                    }
                ],
            }
        },
        root_work_order_id=work_order_id,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=1,
        budget_policy=BudgetPolicy(
            scope="worker-test",
            limits=BudgetAmount(
                token_count=120,
                time_ms=1_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=4_000,
            ),
        ),
        business_idempotency_scope="worker-test",
        operation_fingerprint="sha256:" + "a" * 64,
        approval_ref="governor-grant://parent",
        review_ref="review-waiver://worker-test",
    )
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-workers",
        session_id="session-workers",
        principal_id="principal-workers",
        input_ref="input://sha256/" + "1" * 64,
        input_sha256="sha256:" + "1" * 64,
        governor_grant_ref=order.approval_ref,
        root_work_order_id=work_order_id,
        graph_id=graph_id,
        graph_revision=1,
        route_type=route,
        target_id=target,
        business_scope=order.business_idempotency_scope,
        operation_fingerprint=order.operation_fingerprint,
        created_at_ms=100,
    )
    context = {
        "command_id": command.command_id,
        "work_order_id": work_order_id,
        "graph_id": graph_id,
        "graph_revision": 1,
        "node_id": node_id,
        "attempt_id": "attempt-workers-1",
        "lease_id": "lease-workers-1",
        "lease_owner_id": "worker-governed-workers",
        "fencing_token": 1,
        # Production NodeExecutionRuntime freezes this from the canonical
        # TaskNode.  Keep the direct worker fixture on the same fail-closed
        # execution-context contract.
        "node_risk_level": 3,
        "worker_route": f"{route}:{target}",
        "node_input_refs": ["artifact://input/one"],
        "node_grant_ref": "governor-grant://child/node",
        "reservation_id": "operation-reservation://worker-test",
        "business_scope": order.business_idempotency_scope,
        "operation_fingerprint": order.operation_fingerprint,
        "budget": _active_budget(),
    }
    return command, order, context


@pytest.mark.asyncio
async def test_chat_worker_uses_frozen_binding_and_stages_projection_before_commit() -> None:
    command, order, context = _contracts()
    grants = _GrantVerifier()
    projections = _ProjectionStore()
    executor = _Executor(
        {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "hello from frozen chat",
            "route_type": "chat",
            "target_id": "surface_reply",
            "audit_refs": {
                "path": "surface_reply:surface_main_chain",
            },
            "acceptance_effect_intents": [
                {
                    "effect_type": "life.foreground_completed",
                    "commit_scope": "NODE",
                    "payload": {"command_type": "foreground_completed"},
                }
            ],
        }
    )
    worker = GovernedFrozenNodeWorker(
        route_kind="chat",
        executor=executor,
        node_grants=grants,
        projections=projections,
    )

    events = [
        event
        async for event in worker.execute_stream(
            command=command,
            work_order=order,
            execution_context=context,
        )
    ]

    assert len(events) == 1
    terminal = events[0]
    assert terminal["route_type"] == "chat"
    assert terminal["target_id"] == "surface_reply"
    assert terminal["receipt_ref"] in projections.rows
    assert projections.rows[terminal["receipt_ref"]]["narrative"] == "hello from frozen chat"
    assert terminal["acceptance_effect_intents"][0]["effect_type"] == "life.foreground_completed"
    assert terminal["audit_refs"]["buffered_pre_acceptance_events"] == 1
    assert terminal["audit_refs"]["path"] == "surface_reply:surface_main_chain"
    assert executor.inputs[0].objective == "answer from frozen input"
    assert executor.inputs[0].input_refs == ("artifact://input/one",)
    assert executor.inputs[0].node_risk_level == 3
    assert grants.calls[0]["grant_ref"] == "governor-grant://child/node"
    mutated_pack = dict(executor.inputs[0].input_pack)
    mutated_pack["user_message"] = "tampered"
    assert executor.inputs[0].input_pack["user_message"] == "hello"
    assert executor.inputs[0].budget == _active_budget()
    assert executor.inputs[0].input_pack["budget"]["remaining"]["token_count"] == 999_999_999

    # The frozen JSON snapshot must retain the precharged durable remaining,
    # even if the mutable execution context or a projection copy is changed.
    context["budget"]["remaining"]["token_count"] = 1
    copied_budget = dict(executor.inputs[0].budget)
    copied_budget["remaining"]["token_count"] = 999
    assert executor.inputs[0].budget["remaining"]["token_count"] == 120


@pytest.mark.asyncio
async def test_chat_worker_forwards_only_exact_task_bound_sentence_before_commit() -> None:
    command, order, context = _contracts()
    delivery_id = "taskchat://" + "e" * 64

    class _TaskBoundSentenceExecutor:
        async def execute_stream(self, frozen):  # noqa: ANN001
            yield {
                "phase": "SENTENCE",
                "sentence": "我在。",
                "sentence_index": 0,
                "delivery_id": delivery_id,
                "delivery_status": "TASK_NODE_SENTENCE_STAGED",
                "request_id": frozen.request_id,
                "graph_id": frozen.graph_id,
                "graph_revision": frozen.graph_revision,
                "node_id": frozen.node_id,
                "attempt_id": frozen.attempt_id,
                "lease_id": frozen.lease_id,
                "lease_owner_id": frozen.lease_owner_id,
                "fencing_token": frozen.fencing_token,
                "node_grant_ref": frozen.node_grant_ref,
                "self_id": "natsume_ai",
                "relationship_subject_id": frozen.principal_id,
                "workspace_id": "workspace-workers",
                "affective_turn_plan_id": "plan-workers",
                "synchronization_id": "sync-workers",
                "self_affect_revision": 2,
            }
            yield {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": "我在。",
                "route_type": "chat",
                "target_id": "surface_reply",
                "audit_refs": {},
            }

    projections = _ProjectionStore()
    worker = GovernedFrozenNodeWorker(
        route_kind="chat",
        executor=_TaskBoundSentenceExecutor(),
        node_grants=_GrantVerifier(),
        projections=projections,
    )
    events = [
        event
        async for event in worker.execute_stream(
            command=command,
            work_order=order,
            execution_context=context,
        )
    ]

    assert [event["phase"] for event in events] == ["SENTENCE", "COMMIT"]
    assert events[0]["delivery_id"] == delivery_id
    assert events[1]["audit_refs"]["task_bound_visible_pre_acceptance_events"] == 1
    assert events[1]["audit_refs"]["buffered_pre_acceptance_events"] == 0


@pytest.mark.asyncio
async def test_delegate_worker_rejects_executor_reroute_before_projection() -> None:
    command, order, context = _contracts(route="delegate", target="research_agent")
    projections = _ProjectionStore()
    worker = GovernedFrozenNodeWorker(
        route_kind="delegate",
        executor=_Executor(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": "result",
                "route_type": "delegate",
                "target_id": "different_agent",
            }
        ),
        node_grants=_GrantVerifier(),
        projections=projections,
    )

    with pytest.raises(GovernedNodeWorkerError) as raised:
        _ = [
            event
            async for event in worker.execute_stream(
                command=command,
                work_order=order,
                execution_context=context,
            )
        ]

    assert raised.value.reason_code == GOVERNED_NODE_COMMIT_INVALID
    assert projections.rows == {}


@pytest.mark.asyncio
async def test_model_delegate_stages_secret_free_projection_after_budget_guard_use() -> None:
    command, order, context = _contracts(route="delegate", target="translate_agent")
    projections = _ValidatingProjectionStore()
    worker = GovernedFrozenNodeWorker(
        route_kind="delegate",
        executor=_Executor(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": "You worked hard today. Get some rest.",
                "route_type": "delegate",
                "target_id": "translate_agent",
                "audit_refs": {
                    "delegate_model_budget_guard": {
                        "budget_id": "budget://governed-workers/active",
                        "max_token_count": 120,
                    },
                    "delegate_model_attempt": {
                        "success": True,
                        "attempt_ids": ["model-attempt-translate"],
                        "budget_measurement": {
                            "token_count": 24,
                            "retry_count": 0,
                        },
                    },
                },
                "budget_usage": {
                    "schema_version": "Natsume.task_runtime.worker_budget_usage.v1",
                    "usage_id": "worker-budget-usage://delegate/translate/1",
                    "measurements": {"token_count": 24, "retry_count": 0},
                    "evidence_refs": ["model-budget://delegate/translate/1"],
                },
            }
        ),
        node_grants=_GrantVerifier(),
        projections=projections,
    )

    events = [
        event
        async for event in worker.execute_stream(
            command=command,
            work_order=order,
            execution_context=context,
        )
    ]

    assert len(events) == 1
    staged = projections.rows[events[0]["receipt_ref"]]
    assert "delegate_model_budget_guard" not in staged["audit_refs"]
    assert staged["audit_refs"]["delegate_model_attempt"]["success"] is True
    assert staged["budget_usage"]["measurements"]["token_count"] == 24


@pytest.mark.asyncio
async def test_parent_grant_cannot_be_reused_as_node_grant() -> None:
    command, order, context = _contracts()
    context["node_grant_ref"] = order.approval_ref
    worker = GovernedFrozenNodeWorker(
        route_kind="chat",
        executor=_Executor({}),
        node_grants=_GrantVerifier(),
        projections=_ProjectionStore(),
    )

    with pytest.raises(GovernedNodeWorkerError) as raised:
        _ = [
            event
            async for event in worker.execute_stream(
                command=command,
                work_order=order,
                execution_context=context,
            )
        ]

    assert raised.value.reason_code == GOVERNED_NODE_GRANT_INVALID


@pytest.mark.asyncio
async def test_worker_rejects_missing_authoritative_budget_before_grant_or_execution() -> None:
    command, order, context = _contracts()
    context.pop("budget")
    grants = _GrantVerifier()
    executor = _Executor({})
    worker = GovernedFrozenNodeWorker(
        route_kind="chat",
        executor=executor,
        node_grants=grants,
        projections=_ProjectionStore(),
    )

    with pytest.raises(GovernedNodeWorkerError) as raised:
        _ = [
            event
            async for event in worker.execute_stream(
                command=command,
                work_order=order,
                execution_context=context,
            )
        ]

    assert raised.value.reason_code == GOVERNED_NODE_INPUT_INVALID
    assert grants.calls == []
    assert executor.inputs == []
