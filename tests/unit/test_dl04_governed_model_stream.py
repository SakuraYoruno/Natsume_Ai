from __future__ import annotations

import pytest

from host_brain.model_runtime import (
    AttemptStatus,
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ModelAttemptRequest,
    ModelAttemptService,
    ModelAttemptStreamEventKind,
    ModelBudgetGuard,
    PromptRef,
    PromptRegistry,
    ProviderAdapterRegistry,
    ProviderTarget,
    RouteDecision,
)


class _Route:
    async def resolve(self, _request, _prompt):  # noqa: ANN001
        return RouteDecision(
            "route-governed-stream",
            (ProviderTarget("attested", "provider", "model"),),
            "unit-test",
            max_attempts=1,
        )


def _guard() -> ModelBudgetGuard:
    return ModelBudgetGuard(
        budget_id="budget://governed-stream",
        graph_id="graph-governed-stream",
        graph_revision=1,
        node_id="node-governed-stream",
        attempt_id="attempt-governed-stream",
        deadline_ms=1_000,
        max_provider_attempts=1,
        max_token_count=100,
        max_output_tokens=50,
        max_model_cost_micros=500,
    )


def _service(stream_events):  # noqa: ANN001
    prompts = PromptRegistry()
    prompts.register_static("governed.stream.prompt", "Question: {question}")
    audit = InMemoryAttemptAuditRepository()
    return (
        ModelAttemptService(
            prompt_registry=prompts,
            route_policy=_Route(),
            provider_adapters=ProviderAdapterRegistry(
                [
                    CallableProviderAdapter(
                        "attested",
                        complete_fn=lambda _request: "unused",
                        stream_events_fn=stream_events,
                    )
                ]
            ),
            audit_repository=audit,
        ),
        audit,
    )


def _request() -> ModelAttemptRequest:
    return ModelAttemptRequest(
        request_id="request-governed-stream",
        scene_type="chat_surface",
        cognitive_tier="a",
        prompt=PromptRef("governed.stream.prompt", {"question": "hello"}),
        deadline_ms=1_000,
        model_budget_guard=_guard(),
        metadata={"model_node_id": "node-governed-stream"},
    )


@pytest.mark.asyncio
async def test_governed_stream_requires_and_records_final_provider_usage() -> None:
    async def stream_events(_request):  # noqa: ANN001
        yield {"kind": "delta", "text": "先缓一下。"}
        yield {
            "kind": "final",
            "usage": {
                "total_tokens": 12,
                "completion_tokens": 5,
                "model_cost_micros": 17,
            },
            "metadata": {
                "provider_latency_ms": 41,
                "transport_identity_digest": "a" * 64,
                "transport_external": True,
            },
        }

    service, audit = _service(stream_events)

    events = [event async for event in service.stream_events(_request())]

    assert [event.kind for event in events] == [
        ModelAttemptStreamEventKind.DELTA,
        ModelAttemptStreamEventKind.TERMINAL,
    ]
    assert events[0].text == "先缓一下。"
    terminal = events[-1].result
    assert terminal is not None and terminal.success is True
    assert terminal.content == "先缓一下。"
    assert terminal.attempts[-1].metadata["provider_budget_measurements"] == {
        "token_count": 12,
        "completion_tokens": 5,
        "model_cost_micros": 17,
    }
    assert [row.status for row in audit.records()] == [
        AttemptStatus.STARTED,
        AttemptStatus.SUCCEEDED,
    ]


@pytest.mark.asyncio
async def test_governed_stream_never_accepts_a_final_frame_without_cost_usage() -> None:
    async def stream_events(_request):  # noqa: ANN001
        yield {"kind": "delta", "text": "我在。"}
        yield {
            "kind": "final",
            "usage": {"total_tokens": 5, "completion_tokens": 2},
        }

    service, audit = _service(stream_events)

    events = [event async for event in service.stream_events(_request())]

    assert events[0].kind is ModelAttemptStreamEventKind.DELTA
    terminal = events[-1].result
    assert terminal is not None and terminal.success is False
    assert terminal.error_code == "MODEL_PROVIDER_USAGE_REQUIRED"
    assert [row.status for row in audit.records()] == [
        AttemptStatus.STARTED,
        AttemptStatus.REJECTED,
    ]


@pytest.mark.asyncio
async def test_governed_stream_requires_terminal_transport_attestation() -> None:
    async def stream_events(_request):  # noqa: ANN001
        yield {"kind": "delta", "text": "我会陪着你。"}
        yield {
            "kind": "final",
            "usage": {
                "total_tokens": 9,
                "completion_tokens": 4,
                "model_cost_micros": 11,
            },
        }

    service, audit = _service(stream_events)

    events = [event async for event in service.stream_events(_request())]

    terminal = events[-1].result
    assert terminal is not None and terminal.success is False
    assert terminal.error_code == "MODEL_PROVIDER_TRANSPORT_UNPROVEN"
    assert [row.status for row in audit.records()] == [
        AttemptStatus.STARTED,
        AttemptStatus.REJECTED,
    ]
