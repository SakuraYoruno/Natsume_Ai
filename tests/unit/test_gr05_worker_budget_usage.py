from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from host_brain.app.governed_route_executors import (
    FrozenChatRouteExecutor,
    FrozenDelegateRouteExecutor,
    GovernedRouteExecutorError,
)
from host_brain.app.governed_delegate_dispatch import (
    GovernedDelegateTarget,
    READONLY_DELEGATE_EXECUTION_MODE,
)
from host_brain.app import model_provider_composition
from host_brain.model_runtime import (
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ModelAttemptRequest,
    ModelAttemptService,
    PromptRef,
    PromptRegistry,
    ProviderAdapterRegistry,
    ProviderTarget,
    RetryDirective,
    RouteDecision,
    model_attempt_result_trace,
)
from host_brain.task_runtime.governed_workers import FrozenNodeInput
from host_brain.contracts.agent_runtime.delegate_usage_attestation import (
    build_readonly_delegate_usage_attestation,
)
from host_brain.task_runtime.node_execution_records import _precharged_tool_call_count
from host_brain.task_runtime.worker_budget_usage import (
    MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION,
    WorkerBudgetUsage,
    WorkerBudgetUsageError,
    build_worker_budget_usage,
)


class _RoutePolicy:
    async def resolve(self, request, prompt):  # noqa: ANN001
        del request, prompt
        return RouteDecision(
            "route-budget-measurement",
            (ProviderTarget("provider", "provider", "model"),),
            "budget-test",
            max_attempts=2,
        )


def _service(*, second_has_usage: bool = True) -> ModelAttemptService:
    registry = PromptRegistry()
    registry.register_static("budget.prompt", "hello {name}")

    async def complete(attempt):  # noqa: ANN001
        usage = (
            {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "model_cost_micros": 7,
            }
            if attempt.attempt_index == 1 or second_has_usage
            else {}
        )
        return {"content": f"reply-{attempt.attempt_index}", "usage": usage}

    return ModelAttemptService(
        prompt_registry=registry,
        route_policy=_RoutePolicy(),
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("provider", complete_fn=complete)]
        ),
        audit_repository=InMemoryAttemptAuditRepository(),
    )


async def _two_attempt_result(*, second_has_usage: bool = True):  # noqa: ANN202
    def retry(response, attempt):  # noqa: ANN001
        del response
        return (
            RetryDirective(retry=True, reason="repair", reuse_target=True)
            if attempt.attempt_index == 1
            else RetryDirective.accept()
        )

    return await _service(second_has_usage=second_has_usage).complete(
        ModelAttemptRequest(
            request_id="request-budget-measurement",
            scene_type="CHAT",
            cognitive_tier="A",
            prompt=PromptRef("budget.prompt", {"name": "world"}),
            deadline_ms=1_000,
            retry_evaluator=retry,
        )
    )


@pytest.mark.asyncio
async def test_model_attempt_trace_reports_exact_provider_usage_and_retry() -> None:
    result = await _two_attempt_result()

    measurement = model_attempt_result_trace(result)["budget_measurement"]
    assert measurement["schema_version"] == MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION
    assert measurement["attempt_count"] == 2
    assert measurement["retry_count"] == 1
    assert measurement["token_count"] == 12
    assert measurement["model_cost_micros"] == 14
    assert measurement["measurement_ref"].startswith("model-budget://")


@pytest.mark.asyncio
async def test_model_attempt_trace_does_not_invent_incomplete_token_or_cost_totals() -> None:
    result = await _two_attempt_result(second_has_usage=False)

    measurement = model_attempt_result_trace(result)["budget_measurement"]
    assert measurement["attempt_count"] == 2
    assert measurement["retry_count"] == 1
    assert "token_count" not in measurement
    assert "model_cost_micros" not in measurement


def test_worker_usage_aggregates_model_measurements_and_real_agent_tool_steps() -> None:
    observations = {
        "model_attempts": [
            {
                "budget_measurement": {
                    "schema_version": MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION,
                    "measurement_ref": "model-budget://first",
                    "attempt_count": 2,
                    "retry_count": 1,
                    "token_count": 12,
                    "model_cost_micros": 14,
                }
            },
            {
                "budget_measurement": {
                    "schema_version": MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION,
                    "measurement_ref": "model-budget://second",
                    "attempt_count": 1,
                    "retry_count": 0,
                    "token_count": 5,
                    "model_cost_micros": 3,
                }
            },
        ],
        "trace": {
            "schema_version": "nle.trace.redacted.v1",
            "payload_hash": "sha256:trace-one",
            "steps": [
                {"action_type": "tool_call", "tool_id": "agent_read_file"},
                {"action_type": "tool_call", "tool_id": "agent_finish"},
                {"action_type": "finish", "tool_id": "agent_finish"},
            ],
        },
    }

    usage = build_worker_budget_usage(
        usage_id="worker-budget://aggregate-one",
        observations=observations,
        include_agent_tool_calls=True,
    )

    assert usage is not None
    assert usage.measurements == {
        "retry_count": 1,
        "token_count": 17,
        "model_cost_micros": 17,
        "tool_call_count": 1,
    }
    assert usage.to_budget_amount(elapsed_ms=9).to_dict() == {
        "token_count": 17,
        "tool_call_count": 1,
        "time_ms": 9,
        "concurrency": 0,
        "retry_count": 1,
        "model_cost_micros": 17,
    }


def test_worker_usage_contract_rejects_unknown_or_forged_fields() -> None:
    with pytest.raises(WorkerBudgetUsageError):
        WorkerBudgetUsage.from_payload(
            {
                "schema_version": "Natsume.task_runtime.worker_budget_usage.v1",
                "usage_id": "worker-budget://invalid",
                "measurements": {"token_count": 1, "estimated_tokens": 9},
                "evidence_refs": [],
            }
        )


@pytest.mark.asyncio
async def test_frozen_chat_emits_typed_usage_from_canonical_model_trace() -> None:
    measurement = {
        "schema_version": MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION,
        "measurement_ref": "model-budget://chat-one",
        "attempt_count": 2,
        "retry_count": 1,
        "token_count": 33,
    }

    async def stream_chat_with_context(**kwargs):  # noqa: ANN003
        del kwargs
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "measured answer",
            "audit_refs": {
                "trace_stage_meta": {
                    "surface_model_attempt": {"budget_measurement": measurement}
                }
            },
        }

    frozen = FrozenNodeInput(
        command_id="command-budget-chat",
        request_id="request-budget-chat",
        session_id="session-budget-chat",
        principal_id="person-budget-chat",
        work_order_id="work-order-budget-chat",
        root_work_order_id="work-order-budget-chat",
        graph_id="graph-budget-chat",
        graph_revision=1,
        node_id="node-budget-chat",
        attempt_id="attempt-budget-chat",
        lease_id="lease-budget-chat",
        lease_owner_id="owner-budget-chat",
        fencing_token=1,
        node_risk_level=1,
        route="chat",
        target_id="surface_reply",
        objective="answer",
        input_refs=(),
        _input_pack_json=json.dumps({"user_message": "hello"}),
        _input_artifacts_json="[]",
        node_grant_ref="governor-grant://child/chat",
        parent_grant_ref="governor-grant://parent/chat",
    )
    executor = FrozenChatRouteExecutor(
        ports=SimpleNamespace(),
        stream_chat_with_context=stream_chat_with_context,
    )

    events = [event async for event in executor.execute_stream(frozen)]

    assert len(events) == 1
    usage = WorkerBudgetUsage.from_payload(events[0]["budget_usage"])
    assert usage.measurements == {"retry_count": 1, "token_count": 33}
    assert usage.evidence_refs == ("model-budget://chat-one",)


def test_chat_is_not_precharged_as_a_tool_call() -> None:
    assert _precharged_tool_call_count("chat:surface_reply") == 0
    assert _precharged_tool_call_count("skill:file_read") == 1
    assert _precharged_tool_call_count("delegate:intent_agent") == 1
    assert _precharged_tool_call_count("worker:legacy-test") == 1


@pytest.mark.asyncio
async def test_frozen_chat_budget_projection_ignores_large_memory_and_injected_measurement() -> None:
    trusted = {
        "schema_version": MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION,
        "measurement_ref": "model-budget://chat-trusted",
        "attempt_count": 1,
        "retry_count": 0,
        "token_count": 17,
    }
    injected = {
        "schema_version": MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION,
        "measurement_ref": "model-budget://memory-injected",
        "attempt_count": 1,
        "retry_count": 0,
        "token_count": 999_999,
    }

    async def stream_chat_with_context(**kwargs):  # noqa: ANN003
        del kwargs
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "bounded answer",
            "audit_refs": {
                "trace_stage_meta": {
                    "surface_model_attempt": {"budget_measurement": trusted},
                    "surface_memory_recall_packet": {
                        "facts": [
                            *({"text": f"memory-{index}"} for index in range(2_000)),
                            {"budget_measurement": injected},
                        ]
                    },
                }
            },
        }

    executor = FrozenChatRouteExecutor(
        ports=SimpleNamespace(),
        stream_chat_with_context=stream_chat_with_context,
    )
    events = [
        event
        async for event in executor.execute_stream(
            _budget_frozen(route="chat", target_id="surface_reply")
        )
    ]

    usage = WorkerBudgetUsage.from_payload(events[0]["budget_usage"])
    assert usage.measurements == {"retry_count": 0, "token_count": 17}
    assert usage.evidence_refs == ("model-budget://chat-trusted",)


@pytest.mark.asyncio
async def test_frozen_chat_rejects_retired_secondary_model_measurement() -> None:
    surface = {
        "schema_version": MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION,
        "measurement_ref": "model-budget://surface",
        "attempt_count": 2,
        "retry_count": 1,
        "token_count": 21,
        "model_cost_micros": 8,
    }
    spark = {
        "schema_version": MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION,
        "measurement_ref": "model-budget://spark",
        "attempt_count": 1,
        "retry_count": 0,
        "token_count": 5,
        "model_cost_micros": 2,
    }

    async def stream_chat_with_context(**kwargs):  # noqa: ANN003
        del kwargs
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "judged answer",
            "audit_refs": {
                "trace_stage_meta": {
                    "surface_model_attempt": {"budget_measurement": surface},
                    "codex_spark_reply_judge": {
                        "model_attempt": {"budget_measurement": spark}
                    },
                }
            },
        }

    executor = FrozenChatRouteExecutor(
        ports=SimpleNamespace(),
        stream_chat_with_context=stream_chat_with_context,
    )
    with pytest.raises(
        GovernedRouteExecutorError,
        match="retired secondary model",
    ):
        _ = [
            event
            async for event in executor.execute_stream(
                _budget_frozen(route="chat", target_id="surface_reply")
            )
        ]


@pytest.mark.asyncio
async def test_frozen_delegate_budget_projection_trusts_only_bound_zero_attestation() -> None:
    trusted = {
        "schema_version": MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION,
        "measurement_ref": "model-budget://delegate-trusted",
        "attempt_count": 1,
        "retry_count": 0,
        "token_count": 13,
    }
    injected = {
        **trusted,
        "measurement_ref": "model-budget://delegate-injected",
        "token_count": 888_888,
    }

    async def dispatch(**kwargs):  # noqa: ANN003
        frozen = kwargs["frozen"]
        invocation_ref = "governed-delegate://sha256/" + "a" * 64
        policy = dict(frozen.input_pack["delegate_target_policy"])
        return {
            "route_type": "delegate",
            "target_id": "intent_agent",
            "success": True,
            "result_text": "delegated answer",
            "payload": {
                    "audit_refs": {
                        "delegate_invocation_ref": invocation_ref,
                        "delegate_adapter": "test-port-free-readonly.v1",
                        "delegate_execution_mode": "PORT_FREE_READ_ONLY",
                        "delegate_target_policy_ref": policy["policy_ref"],
                        "delegate_review_mode": policy["review_mode"],
                        "delegate_budget_mode": policy["budget_mode"],
                        "delegate_effect_mode": policy["effect_mode"],
                        "side_effects_executed": False,
                        "delegate_runtime_usage_attestation": (
                        build_readonly_delegate_usage_attestation(
                            command_id=frozen.command_id,
                            graph_id=frozen.graph_id,
                            graph_revision=frozen.graph_revision,
                            node_id=frozen.node_id,
                            attempt_id=frozen.attempt_id,
                            node_grant_ref=frozen.node_grant_ref,
                            target_id=frozen.target_id,
                            adapter_id="test-port-free-readonly.v1",
                            invocation_ref=invocation_ref,
                        )
                    ),
                    "intent_agent_trace": {
                        "model_attempt": {"budget_measurement": trusted}
                    },
                    "agent_context": {
                        "memory": [
                            *({"text": f"context-{index}"} for index in range(2_000)),
                            {"budget_measurement": injected},
                        ]
                    },
                }
            },
        }

    executor = FrozenDelegateRouteExecutor(
        ports=SimpleNamespace(),
        dispatch_to_sub_agent=dispatch,
    )
    events = [
        event
        async for event in executor.execute_stream(
            _budget_frozen(route="delegate", target_id="intent_agent")
        )
    ]

    usage = WorkerBudgetUsage.from_payload(events[0]["budget_usage"])
    assert usage.measurements == {
        "model_cost_micros": 0,
        "retry_count": 0,
        "token_count": 0,
        "tool_call_count": 0,
    }
    assert len(usage.evidence_refs) == 1
    assert usage.evidence_refs[0].startswith("delegate-usage://sha256/")


@pytest.mark.asyncio
async def test_frozen_chat_rejects_malformed_measurement_on_trusted_trace_path() -> None:
    async def stream_chat_with_context(**kwargs):  # noqa: ANN003
        del kwargs
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "must fail closed",
            "audit_refs": {
                "trace_stage_meta": {
                    "surface_model_attempt": {
                        "budget_measurement": {
                            "schema_version": MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION,
                            "measurement_ref": "model-budget://malformed",
                            "attempt_count": 1,
                            "retry_count": 0,
                            "forged_tokens": 9,
                        }
                    }
                }
            },
        }

    executor = FrozenChatRouteExecutor(
        ports=SimpleNamespace(),
        stream_chat_with_context=stream_chat_with_context,
    )
    with pytest.raises(WorkerBudgetUsageError, match="unknown fields"):
        _ = [
            event
            async for event in executor.execute_stream(
                _budget_frozen(route="chat", target_id="surface_reply")
            )
        ]


def test_application_model_transport_returns_usage_from_the_same_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invoke(**kwargs):  # noqa: ANN003
        del kwargs
        model_provider_composition._set_last_provider_usage(  # noqa: SLF001
            {"input_tokens": 3, "output_tokens": 2}
        )
        return "provider answer"

    monkeypatch.setattr(model_provider_composition, "invoke_llm_text", invoke)

    result = model_provider_composition.invoke_model_provider_transport(model="ignored")

    assert result == {
        "content": "provider answer",
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }


def _budget_frozen(*, route: str, target_id: str) -> FrozenNodeInput:
    input_pack = {"user_message": "hello"}
    if route == "delegate":
        input_pack["delegate_target_policy"] = GovernedDelegateTarget(
            target_id=target_id,
            handler=lambda _payload, _context: {},
            adapter_id="test-port-free-readonly.v1",
            execution_mode=READONLY_DELEGATE_EXECUTION_MODE,
        ).policy_projection()
    return FrozenNodeInput(
        command_id=f"command-budget-{route}",
        request_id=f"request-budget-{route}",
        session_id=f"session-budget-{route}",
        principal_id=f"person-budget-{route}",
        work_order_id=f"work-order-budget-{route}",
        root_work_order_id=f"work-order-budget-{route}",
        graph_id=f"graph-budget-{route}",
        graph_revision=1,
        node_id=f"node-budget-{route}",
        attempt_id=f"attempt-budget-{route}",
        lease_id=f"lease-budget-{route}",
        lease_owner_id=f"owner-budget-{route}",
        fencing_token=1,
        node_risk_level=3 if route == "delegate" else 1,
        route=route,
        target_id=target_id,
        objective="measure canonical budget usage",
        input_refs=(),
        _input_pack_json=json.dumps(input_pack),
        _input_artifacts_json="[]",
        node_grant_ref=f"governor-grant://child/{route}",
        parent_grant_ref=f"governor-grant://parent/{route}",
    )
