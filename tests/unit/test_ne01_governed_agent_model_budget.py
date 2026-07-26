from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.agent_runtime.agent_executor import AgentExecutor
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.model_runtime import (
    AttemptStatus,
    GOVERNED_MODEL_BUDGET_EXHAUSTED,
    GOVERNED_MODEL_BUDGET_MEASUREMENT_INVALID,
    GovernedAgentModelBudget,
    GovernedModelBudgetError,
    ModelAttemptRecord,
    ModelAttemptResult,
    ProviderResponse,
    ProviderTarget,
    RouteDecision,
    build_governed_model_budget_guard,
    build_live_production_prompt_registry,
)


def _budget() -> dict:
    return {
        "budget_id": "budget-agent-model",
        "status": "ACTIVE",
        "status_version": 1,
        "remaining": {
            "token_count": 100,
            "tool_call_count": 4,
            "time_ms": 10_000,
            "concurrency": 1,
            "retry_count": 2,
            "model_cost_micros": 1_000,
        },
    }


def _guard():
    return build_governed_model_budget_guard(
        budget=_budget(),
        graph_id="graph-agent-model",
        graph_revision=3,
        node_id="node-agent-model",
        attempt_id="attempt-agent-model",
    )


def _trace(
    ref: str,
    *,
    token_count: int,
    retry_count: int = 0,
    model_cost_micros: int = 10,
) -> dict:
    return {
        "cached": False,
        "budget_measurement": {
            "schema_version": (
                "Natsume.model_runtime.budget_measurement.v1"
            ),
            "measurement_ref": ref,
            "attempt_count": retry_count + 1,
            "retry_count": retry_count,
            "token_count": token_count,
            "model_cost_micros": model_cost_micros,
        },
    }


def _work_order() -> WorkOrderV3:
    order = WorkOrderV3(
        work_order_id="work-order-agent-model",
        domain="code",
        run_mode="inspect",
        objective="inspect without side effects",
        workspace={"strategy": "temp_workspace"},
        input_pack={"user_request": "inspect without side effects"},
        allowed_actions=("read_files", "generate_report"),
        forbidden_actions=("write_files",),
        metadata={"request_id": "request-agent-model"},
        root_work_order_id="work-order-agent-model",
        task_graph_id="graph-agent-model",
        task_node_id="node-agent-model",
        graph_revision=3,
        risk_level=1,
    )
    order.validate()
    return order


def _result(
    *,
    request_id: str,
    output: str,
    token_count: int,
    model_cost_micros: int,
) -> ModelAttemptResult:
    target = ProviderTarget("adapter", "provider", "model")
    decision = RouteDecision(
        decision_id=f"route-{request_id}",
        targets=(target,),
        reason="test",
        max_attempts=1,
    )
    record = ModelAttemptRecord(
        attempt_id=f"attempt-{request_id}",
        request_id=request_id,
        route_decision_id=decision.decision_id,
        attempt_index=1,
        status=AttemptStatus.SUCCEEDED,
        retry_reason="initial",
        adapter_id=target.adapter_id,
        provider=target.provider,
        model_id=target.model_id,
        prompt_id="agent.react",
        prompt_version="1",
        prompt_digest="a" * 64,
        started_at_ms=1,
        ended_at_ms=2,
        latency_ms=1,
        metadata={
            "provider_budget_measurements": {
                "token_count": token_count,
                "completion_tokens": max(1, token_count // 2),
                "model_cost_micros": model_cost_micros,
            }
        },
    )
    return ModelAttemptResult(
        request_id=request_id,
        success=True,
        route_decision=decision,
        attempts=(record,),
        response=ProviderResponse(
            content=output,
            usage={
                "token_count": token_count,
                "completion_tokens": max(1, token_count // 2),
                "model_cost_micros": model_cost_micros,
            },
        ),
        final_target=target,
    )


def test_agent_budget_projects_strictly_smaller_guards_and_exact_once_usage() -> None:
    budget = GovernedAgentModelBudget(_guard(), monotonic=lambda: 10.0)

    first = budget.next_guard()
    budget.consume_trace(
        _trace(
            "model-budget://agent/first",
            token_count=30,
            retry_count=1,
            model_cost_micros=100,
        )
    )
    second = budget.next_guard()

    assert first.max_token_count == 100
    assert first.max_provider_attempts == 3
    assert second.max_token_count == 70
    assert second.max_provider_attempts == 2
    assert second.max_model_cost_micros == 900
    assert budget.consumed == {
        "token_count": 30,
        "retry_count": 1,
        "model_cost_micros": 100,
    }
    with pytest.raises(GovernedModelBudgetError) as raised:
        budget.consume_trace(
            _trace(
                "model-budget://agent/first",
                token_count=30,
                retry_count=1,
                model_cost_micros=100,
            )
        )
    assert (
        raised.value.reason_code
        == GOVERNED_MODEL_BUDGET_MEASUREMENT_INVALID
    )


def test_agent_budget_fails_closed_on_missing_measurement_or_overage() -> None:
    budget = GovernedAgentModelBudget(_guard(), monotonic=lambda: 10.0)

    with pytest.raises(GovernedModelBudgetError) as missing:
        budget.consume_trace({"cached": False})
    assert (
        missing.value.reason_code
        == GOVERNED_MODEL_BUDGET_MEASUREMENT_INVALID
    )

    with pytest.raises(GovernedModelBudgetError) as exceeded:
        budget.consume_trace(
            _trace(
                "model-budget://agent/over",
                token_count=101,
                model_cost_micros=10,
            )
        )
    assert exceeded.value.reason_code == GOVERNED_MODEL_BUDGET_EXHAUSTED


def test_agent_executor_shares_remaining_budget_across_model_steps(
    tmp_path: Path,
) -> None:
    class _ModelAttempts:
        def __init__(self) -> None:
            self.guards = []

        def complete_sync(self, request):  # noqa: ANN001, ANN202
            self.guards.append(request.model_budget_guard)
            if len(self.guards) == 1:
                return _result(
                    request_id=request.request_id,
                    output=(
                        '{"thought":"continue","tool_id":"not_allowed",'
                        '"tool_args":{}}'
                    ),
                    token_count=30,
                    model_cost_micros=100,
                )
            return _result(
                request_id=request.request_id,
                output=(
                    '{"thought":"done","tool_id":"agent_finish",'
                    '"final_answer":"finished within budget"}'
                ),
                token_count=20,
                model_cost_micros=50,
            )

    service = _ModelAttempts()
    result = AgentExecutor(
        model_attempt_service=service,
        prompt_registry=build_live_production_prompt_registry(),
    ).execute(
        _work_order(),
        workspace=tmp_path,
        model_budget_guard=_guard(),
    )

    assert result.status == "EXECUTED"
    assert result.result_text == "finished within budget"
    assert len(service.guards) == 2
    assert service.guards[0].max_token_count == 100
    assert service.guards[1].max_token_count == 70
    assert service.guards[1].max_model_cost_micros == 900
    assert len(result.metadata["model_attempts"]) == 2


def test_agent_executor_stops_before_second_call_after_budget_exhaustion(
    tmp_path: Path,
) -> None:
    class _ModelAttempts:
        def __init__(self) -> None:
            self.calls = 0

        def complete_sync(self, request):  # noqa: ANN001, ANN202
            self.calls += 1
            return _result(
                request_id=request.request_id,
                output=(
                    '{"thought":"continue","tool_id":"not_allowed",'
                    '"tool_args":{}}'
                ),
                token_count=101,
                model_cost_micros=100,
            )

    service = _ModelAttempts()
    result = AgentExecutor(
        model_attempt_service=service,
        prompt_registry=build_live_production_prompt_registry(),
    ).execute(
        _work_order(),
        workspace=tmp_path,
        model_budget_guard=_guard(),
    )

    assert service.calls == 1
    assert result.status == "DEGRADED"
    assert result.reason_code == "AGENT_GOVERNED_MODEL_BUDGET_EXHAUSTED"
    assert "agent_model_budget" in result.risk_flags
    assert len(result.metadata["model_attempts"]) == 1
    assert (
        result.metadata["model_attempts"][0]["budget_measurement"][
            "token_count"
        ]
        == 101
    )
