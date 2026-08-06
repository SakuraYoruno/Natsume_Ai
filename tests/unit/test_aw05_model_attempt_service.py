from __future__ import annotations

import asyncio

import pytest

from host_brain.model_runtime import (
    AttemptAuditError,
    AttemptStatus,
    CachePolicy,
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ModelBudgetGuard,
    ModelAttemptRequest,
    ModelAttemptService,
    ModelAttemptStreamEvent,
    ModelAttemptStreamEventKind,
    ModelBudgetGuardViolation,
    ModelStreamInterruptedError,
    ModelStreamProtocolError,
    OutputContract,
    PromptRef,
    PromptRegistry,
    ProviderAdapterRegistry,
    ProviderResponse,
    ProviderStreamEvent,
    ProviderTarget,
    RetryDirective,
    RouteDecision,
)


class _CountingRoutePolicy:
    def __init__(self, decision: RouteDecision) -> None:
        self.decision = decision
        self.calls = 0

    async def resolve(self, request, prompt):
        del request, prompt
        self.calls += 1
        return self.decision


def _registry(
    *,
    output_contract: OutputContract | None = None,
    cache_policy: CachePolicy | None = None,
) -> PromptRegistry:
    registry = PromptRegistry()
    registry.register_static(
        "test.prompt",
        "Question: {question}",
        output_contract=output_contract,
        cache_policy=cache_policy,
    )
    return registry


def _request(
    request_id: str = "req-1",
    *,
    max_attempts: int = 1,
    foreground: bool = True,
    retry_evaluator=None,
    model_budget_guard: ModelBudgetGuard | None = None,
    previsibility_transport_retries: int = 0,
) -> ModelAttemptRequest:
    return ModelAttemptRequest(
        request_id=request_id,
        scene_type="chat",
        cognitive_tier="a",
        prompt=PromptRef("test.prompt", {"question": "hello"}),
        deadline_ms=1000,
        max_attempts=max_attempts,
        foreground=foreground,
        retry_evaluator=retry_evaluator,
        model_budget_guard=model_budget_guard,
        previsibility_transport_retries=previsibility_transport_retries,
    )


def _model_budget_guard(
    *,
    max_provider_attempts: int = 1,
    max_token_count: int = 100,
    max_output_tokens: int = 40,
    max_model_cost_micros: int = 1_000,
    required_usage_dimensions: tuple[str, ...] = (
        "token_count",
        "completion_tokens",
        "model_cost_micros",
    ),
) -> ModelBudgetGuard:
    return ModelBudgetGuard(
        budget_id="budget://model-attempt-test",
        graph_id="graph-model-attempt-test",
        graph_revision=1,
        node_id="node-model-attempt-test",
        attempt_id="attempt-model-attempt-test",
        deadline_ms=1_000,
        max_provider_attempts=max_provider_attempts,
        max_token_count=max_token_count,
        max_output_tokens=max_output_tokens,
        max_model_cost_micros=max_model_cost_micros,
        required_usage_dimensions=required_usage_dimensions,
    )


def test_usage_attested_stream_contracts_reject_untyped_terminal_values() -> None:
    with pytest.raises(TypeError, match="ProviderResponse"):
        _ = ProviderStreamEvent.final(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ModelAttemptResult"):
        _ = ModelAttemptStreamEvent(
            kind=ModelAttemptStreamEventKind.FINAL,
            result=object(),  # type: ignore[arg-type]
        )


async def test_complete_resolves_route_once_and_owns_semantic_retry_audit() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-1",
            (ProviderTarget("fake", "fake-provider", "model-a"),),
            "unit-test",
            max_attempts=2,
        )
    )
    provider_calls = []

    async def _complete(attempt):
        provider_calls.append(attempt)
        return f"answer-{attempt.attempt_index}"

    def _retry(response, attempt):
        del response
        if attempt.attempt_index == 1:
            return RetryDirective(retry=True, reason="hard_gate_repair", reuse_target=True)
        return RetryDirective.accept()

    audit = InMemoryAttemptAuditRepository()
    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("fake", complete_fn=_complete)]
        ),
        audit_repository=audit,
    )

    result = await service.complete(_request(max_attempts=1, retry_evaluator=_retry))

    assert result.success is True
    assert result.content == "answer-2"
    assert route.calls == 1
    assert len(provider_calls) == 2
    assert provider_calls[1].retry_reason == "hard_gate_repair"
    assert [record.status for record in result.attempts] == [
        AttemptStatus.REJECTED,
        AttemptStatus.SUCCEEDED,
    ]
    assert [record.status for record in audit.records()] == [
        AttemptStatus.STARTED,
        AttemptStatus.REJECTED,
        AttemptStatus.STARTED,
        AttemptStatus.SUCCEEDED,
    ]


async def test_model_budget_guard_can_only_reduce_route_attempts() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-guarded-attempt-budget",
            (ProviderTarget("fake", "provider", "model"),),
            "guarded-attempt-budget-test",
            max_attempts=2,
        )
    )
    calls: list[int] = []

    async def _complete(attempt):
        calls.append(attempt.attempt_index)
        return ProviderResponse(
            content="retry me",
            usage={
                "total_tokens": 4,
                "completion_tokens": 2,
                "model_cost_micros": 3,
            },
        )

    def _retry(_response, _attempt):
        return RetryDirective(retry=True, reason="forced retry", reuse_target=True)

    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("fake", complete_fn=_complete)]
        ),
        audit_repository=InMemoryAttemptAuditRepository(),
    )

    result = await service.complete(
        _request(
            max_attempts=1,
            retry_evaluator=_retry,
            model_budget_guard=_model_budget_guard(max_provider_attempts=1),
        )
    )

    assert result.success is False
    assert result.error_code == "ATTEMPT_BUDGET_EXHAUSTED"
    assert calls == [1]
    assert len(result.attempts) == 1
    assert result.attempts[0].status is AttemptStatus.REJECTED


async def test_model_budget_guard_rejects_missing_canonical_usage_without_fallback() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-guarded-usage",
            (
                ProviderTarget("first", "provider-a", "model-a"),
                ProviderTarget("second", "provider-b", "model-b"),
            ),
            "guarded-usage-test",
            max_attempts=2,
        )
    )
    calls: list[str] = []

    async def _first(_attempt):
        calls.append("first")
        return ProviderResponse(
            content="unmetered cost",
            usage={"total_tokens": 5, "completion_tokens": 2},
        )

    async def _second(_attempt):
        calls.append("second")
        return ProviderResponse(
            content="must not run",
            usage={
                "total_tokens": 5,
                "completion_tokens": 2,
                "model_cost_micros": 1,
            },
        )

    audit = InMemoryAttemptAuditRepository()
    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [
                CallableProviderAdapter("first", complete_fn=_first),
                CallableProviderAdapter("second", complete_fn=_second),
            ]
        ),
        audit_repository=audit,
    )

    result = await service.complete(
        _request(model_budget_guard=_model_budget_guard(max_provider_attempts=2))
    )

    assert result.success is False
    assert result.error_code == "MODEL_PROVIDER_USAGE_REQUIRED"
    assert calls == ["first"]
    assert [record.status for record in result.attempts] == [AttemptStatus.REJECTED]
    assert [record.status for record in audit.records()] == [
        AttemptStatus.STARTED,
        AttemptStatus.REJECTED,
    ]


async def test_model_budget_guard_accepts_provider_tokens_when_cost_is_optional() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-token-attested-chat",
            (ProviderTarget("deepseek", "deepseek", "deepseek-chat"),),
            "ordinary companion chat",
            max_attempts=1,
        )
    )

    async def _complete(_attempt):
        return ProviderResponse(
            content="token-attested response",
            usage={
                "prompt_tokens": 8,
                "completion_tokens": 4,
                "total_tokens": 12,
            },
        )

    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("deepseek", complete_fn=_complete)]
        ),
        audit_repository=InMemoryAttemptAuditRepository(),
    )

    result = await service.complete(
        _request(
            model_budget_guard=_model_budget_guard(
                required_usage_dimensions=(
                    "token_count",
                    "completion_tokens",
                )
            )
        )
    )

    assert result.success is True
    measurements = result.attempts[-1].metadata["provider_budget_measurements"]
    assert measurements == {
        "prompt_tokens": 8,
        "completion_tokens": 4,
        "token_count": 12,
    }


@pytest.mark.parametrize(
    ("usage", "guard", "error_code"),
    [
        (
            {
                "total_tokens": 8,
                "completion_tokens": 6,
                "model_cost_micros": 4,
            },
            {"max_output_tokens": 5},
            "MODEL_OUTPUT_BUDGET_EXCEEDED",
        ),
        (
            {
                "total_tokens": 11,
                "completion_tokens": 3,
                "model_cost_micros": 4,
            },
            {"max_token_count": 10, "max_output_tokens": 10},
            "MODEL_TOKEN_BUDGET_EXCEEDED",
        ),
        (
            {
                "total_tokens": 8,
                "completion_tokens": 3,
                "model_cost_micros": 12,
            },
            {"max_model_cost_micros": 11},
            "MODEL_COST_BUDGET_EXCEEDED",
        ),
    ],
)
async def test_model_budget_guard_rejects_output_token_and_cost_overages(
    usage,
    guard,
    error_code,
) -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-guarded-limit",
            (
                ProviderTarget("first", "provider-a", "model-a"),
                ProviderTarget("second", "provider-b", "model-b"),
            ),
            "guarded-limit-test",
            max_attempts=2,
        )
    )
    calls: list[str] = []

    async def _first(_attempt):
        calls.append("first")
        return ProviderResponse(content="over limit", usage=usage)

    async def _second(_attempt):
        calls.append("second")
        return ProviderResponse(
            content="must not run",
            usage={
                "total_tokens": 1,
                "completion_tokens": 1,
                "model_cost_micros": 1,
            },
        )

    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [
                CallableProviderAdapter("first", complete_fn=_first),
                CallableProviderAdapter("second", complete_fn=_second),
            ]
        ),
        audit_repository=InMemoryAttemptAuditRepository(),
    )

    result = await service.complete(
        _request(model_budget_guard=_model_budget_guard(**guard))
    )

    assert result.success is False
    assert result.error_code == error_code
    assert calls == ["first"]
    assert result.attempts[0].status is AttemptStatus.REJECTED


async def test_model_budget_guard_applies_cost_cap_across_semantic_retries() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-guarded-cumulative-cost",
            (ProviderTarget("fake", "provider", "model"),),
            "guarded-cumulative-cost-test",
            max_attempts=2,
        )
    )
    calls: list[int] = []

    async def _complete(attempt):
        calls.append(attempt.attempt_index)
        return ProviderResponse(
            content=f"answer-{attempt.attempt_index}",
            usage={
                "total_tokens": 4,
                "completion_tokens": 2,
                "model_cost_micros": 6,
            },
        )

    def _retry(_response, attempt):
        if attempt.attempt_index == 1:
            return RetryDirective(retry=True, reason="repair", reuse_target=True)
        return RetryDirective.accept()

    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("fake", complete_fn=_complete)]
        ),
        audit_repository=InMemoryAttemptAuditRepository(),
    )

    result = await service.complete(
        _request(
            retry_evaluator=_retry,
            model_budget_guard=_model_budget_guard(
                max_provider_attempts=2,
                max_token_count=10,
                max_output_tokens=4,
                max_model_cost_micros=10,
            ),
        )
    )

    assert result.success is False
    assert result.error_code == "MODEL_COST_BUDGET_EXCEEDED"
    assert calls == [1, 2]
    assert [record.status for record in result.attempts] == [
        AttemptStatus.REJECTED,
        AttemptStatus.REJECTED,
    ]
    assert result.attempts[1].error_code == "MODEL_COST_BUDGET_EXCEEDED"


async def test_provider_failure_falls_through_route_with_one_global_budget() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-fallback",
            (
                ProviderTarget("first", "provider-a", "model-a"),
                ProviderTarget("second", "provider-b", "model-b"),
            ),
            "fallback-test",
            max_attempts=2,
        )
    )
    calls: list[str] = []

    async def _first(_attempt):
        calls.append("first")
        raise RuntimeError("provider down")

    async def _second(_attempt):
        calls.append("second")
        return "recovered"

    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [
                CallableProviderAdapter("first", complete_fn=_first),
                CallableProviderAdapter("second", complete_fn=_second),
            ]
        ),
        audit_repository=InMemoryAttemptAuditRepository(),
    )

    result = await service.complete(_request(max_attempts=2))

    assert result.success is True
    assert result.final_target is not None
    assert result.final_target.adapter_id == "second"
    assert calls == ["first", "second"]
    assert route.calls == 1
    assert result.attempts[1].retry_reason.startswith("provider_fallback:")


async def test_guarded_realtime_call_retries_transport_before_visibility() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-realtime-previsibility-retry",
            (ProviderTarget("deepseek", "deepseek", "deepseek-v4-flash"),),
            "ordinary companion realtime lane",
            max_attempts=2,
        )
    )
    calls: list[int] = []

    async def _complete(attempt):  # noqa: ANN001
        calls.append(attempt.attempt_index)
        if len(calls) == 1:
            raise TimeoutError("provider stalled before returning a response")
        return ProviderResponse(
            content="我在。先不急着解释。",
            usage={"total_tokens": 16, "completion_tokens": 7},
        )

    audit = InMemoryAttemptAuditRepository()
    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("deepseek", complete_fn=_complete)]
        ),
        audit_repository=audit,
    )

    result = await service.complete(
        _request(
            model_budget_guard=_model_budget_guard(
                max_provider_attempts=2,
                required_usage_dimensions=("token_count", "completion_tokens"),
            ),
            previsibility_transport_retries=1,
        )
    )

    assert result.success is True
    assert result.content == "我在。先不急着解释。"
    assert calls == [1, 2]
    assert [record.status for record in result.attempts] == [
        AttemptStatus.TIMED_OUT,
        AttemptStatus.SUCCEEDED,
    ]
    assert result.attempts[0].error_code == "PROVIDER_TIMEOUT"
    assert result.attempts[1].retry_reason == (
        "previsibility_transport_retry:provider_timeout"
    )
    assert [record.status for record in audit.records()] == [
        AttemptStatus.STARTED,
        AttemptStatus.TIMED_OUT,
        AttemptStatus.STARTED,
        AttemptStatus.SUCCEEDED,
    ]


async def test_provider_error_audit_redacts_credentials() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-secret-error",
            (ProviderTarget("fake", "provider", "model"),),
            "redaction-test",
            max_attempts=1,
        )
    )

    async def _complete(_attempt):
        raise RuntimeError(
            "authorization=Bearer abcdefghijklmnop api_key=sk-super-secret-value"
        )

    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("fake", complete_fn=_complete)]
        ),
        audit_repository=InMemoryAttemptAuditRepository(),
    )

    result = await service.complete(_request())

    message = result.attempts[0].error_message or ""
    assert result.success is False
    assert "sk-super-secret-value" not in message
    assert "abcdefghijklmnop" not in message
    assert "[REDACTED]" in message


async def test_terminal_audit_keeps_only_public_identity_and_provider_measurements() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-audit-metadata",
            (ProviderTarget("fake", "provider", "model"),),
            "audit-metadata-test",
            max_attempts=1,
        )
    )

    async def _complete(_attempt):
        return ProviderResponse(
            content="ok",
            usage={
                "prompt_tokens": 7,
                "completion_tokens": 5,
                "total_tokens": 12,
                "model_cost_micros": 31,
                "tool_call_count": 2,
                "untrusted_estimate": 999,
            },
            metadata={
                "provider_latency_ms": 13,
                "transport_identity_digest": "d" * 64,
                "transport_external": True,
                "untrusted_latency_estimate": 99,
            },
        )

    audit = InMemoryAttemptAuditRepository()
    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("fake", complete_fn=_complete)]
        ),
        audit_repository=audit,
    )
    request = ModelAttemptRequest(
        request_id="req-audit-metadata",
        scene_type="chat",
        cognitive_tier="a",
        prompt=PromptRef("test.prompt", {"question": "hello"}),
        deadline_ms=1000,
        max_attempts=1,
        metadata={
            "model_node_id": "core_reasoner_primary",
            "private_context": "must-not-enter-audit",
        },
    )

    result = await service.complete(request)

    assert result.success is True
    started, terminal = audit.records()
    assert started.metadata == {
        "scene_type": "CHAT",
        "cognitive_tier": "A",
        "model_node_id": "core_reasoner_primary",
    }
    assert terminal.metadata == {
        **started.metadata,
        "provider_budget_measurements": {
            "prompt_tokens": 7,
            "completion_tokens": 5,
            "token_count": 12,
            "model_cost_micros": 31,
            "tool_call_count": 2,
        },
        "provider_transport_measurements": {
            "provider_latency_ms": 13,
            "transport_identity_digest": "d" * 64,
            "transport_external": True,
        },
    }
    assert "private_context" not in terminal.metadata
    assert "untrusted_estimate" not in terminal.metadata[
        "provider_budget_measurements"
    ]


async def test_schema_repair_is_an_explicit_attempt_inside_the_same_route() -> None:
    registry = _registry(
        output_contract=OutputContract(
            kind="json_schema",
            schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        )
    )
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-schema",
            (ProviderTarget("fake", "provider", "model"),),
            "schema-test",
            max_attempts=2,
        )
    )
    attempts = []

    async def _complete(attempt):
        attempts.append(attempt)
        return '{"wrong":true}' if attempt.attempt_index == 1 else '{"answer":"ok"}'

    service = ModelAttemptService(
        prompt_registry=registry,
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("fake", complete_fn=_complete)]
        ),
        audit_repository=InMemoryAttemptAuditRepository(),
    )

    result = await service.complete(_request(max_attempts=2))

    assert result.success is True
    assert result.content == '{"answer":"ok"}'
    assert attempts[1].retry_reason == "output_schema_repair"
    assert len(attempts[1].prompt.messages) > len(attempts[0].prompt.messages)
    assert route.calls == 1


async def test_foreground_never_uses_cross_request_global_cache() -> None:
    registry = _registry(cache_policy=CachePolicy(scope="global", ttl_ms=60_000))
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-cache",
            (ProviderTarget("fake", "provider", "model"),),
            "cache-test",
            max_attempts=1,
        )
    )
    calls = 0

    async def _complete(_attempt):
        nonlocal calls
        calls += 1
        return f"call-{calls}"

    service = ModelAttemptService(
        prompt_registry=registry,
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("fake", complete_fn=_complete)]
        ),
        audit_repository=InMemoryAttemptAuditRepository(),
    )

    first = await service.complete(_request("foreground-1"))
    second = await service.complete(_request("foreground-2"))
    background = await service.complete(_request("background-1", foreground=False))
    cached = await service.complete(_request("background-2", foreground=False))

    assert first.content == "call-1"
    assert second.content == "call-2"
    assert background.content == "call-3"
    assert cached.content == "call-3"
    assert cached.cached is True
    assert calls == 3
    assert route.calls == 4


async def test_stream_falls_back_only_before_route_commitment() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-stream",
            (
                ProviderTarget("first", "provider-a", "model-a"),
                ProviderTarget("second", "provider-b", "model-b"),
            ),
            "stream-test",
            max_attempts=2,
        )
    )

    async def _failed_stream(_attempt):
        if False:
            yield "never"
        raise RuntimeError("failed before first chunk")

    async def _good_stream(_attempt):
        yield "hello"
        await asyncio.sleep(0)
        yield " world"

    audit = InMemoryAttemptAuditRepository()
    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [
                CallableProviderAdapter(
                    "first",
                    complete_fn=lambda _attempt: "unused",
                    stream_fn=_failed_stream,
                ),
                CallableProviderAdapter(
                    "second",
                    complete_fn=lambda _attempt: "unused",
                    stream_fn=_good_stream,
                ),
            ]
        ),
        audit_repository=audit,
    )

    chunks = [chunk async for chunk in service.stream(_request(max_attempts=2))]

    assert chunks == ["hello", " world"]
    assert route.calls == 1
    assert [record.status for record in audit.records()] == [
        AttemptStatus.STARTED,
        AttemptStatus.FAILED,
        AttemptStatus.STARTED,
        AttemptStatus.SUCCEEDED,
    ]


async def test_failed_start_audit_prevents_provider_side_effect() -> None:
    class _RejectAudit:
        async def record_attempt(self, _record):
            raise RuntimeError("audit unavailable")

    calls = 0

    async def _complete(_attempt):
        nonlocal calls
        calls += 1
        return "must not run"

    route = _CountingRoutePolicy(
        RouteDecision(
            "route-audit",
            (ProviderTarget("fake", "provider", "model"),),
            "audit-test",
            max_attempts=1,
        )
    )
    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("fake", complete_fn=_complete)]
        ),
        audit_repository=_RejectAudit(),
    )

    with pytest.raises(AttemptAuditError, match="audit unavailable"):
        await service.complete(_request())
    assert calls == 0


async def test_usage_attested_stream_ends_with_audited_guarded_result() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-stream-result",
            (ProviderTarget("fake", "provider", "model"),),
            "stream-result-test",
            max_attempts=1,
        )
    )

    async def _stream_result(_attempt):
        yield ProviderStreamEvent.delta("先缓一下。")
        yield ProviderStreamEvent.delta("我在这儿。")
        yield ProviderStreamEvent.final(
            ProviderResponse(
                content="先缓一下。我在这儿。",
                usage={
                    "total_tokens": 11,
                    "completion_tokens": 6,
                    "model_cost_micros": 27,
                },
                metadata={
                    "provider_latency_ms": 12,
                    "transport_identity_digest": "a" * 64,
                    "transport_external": True,
                },
            )
        )

    audit = InMemoryAttemptAuditRepository()
    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [
                CallableProviderAdapter(
                    "fake",
                    complete_fn=lambda _attempt: "unused",
                    stream_result_fn=_stream_result,
                )
            ]
        ),
        audit_repository=audit,
    )

    events = [
        event
        async for event in service.stream_with_result(
            _request(model_budget_guard=_model_budget_guard())
        )
    ]

    assert [event.kind for event in events] == [
        ModelAttemptStreamEventKind.DELTA,
        ModelAttemptStreamEventKind.DELTA,
        ModelAttemptStreamEventKind.FINAL,
    ]
    assert "".join(event.text for event in events[:-1]) == "先缓一下。我在这儿。"
    final = events[-1].result
    assert final is not None and final.success is True
    assert final.content == "先缓一下。我在这儿。"
    assert final.attempts[0].metadata["provider_budget_measurements"] == {
        "completion_tokens": 6,
        "token_count": 11,
        "model_cost_micros": 27,
    }
    assert [record.status for record in audit.records()] == [
        AttemptStatus.STARTED,
        AttemptStatus.SUCCEEDED,
    ]


async def test_guarded_stream_without_final_usage_protocol_never_calls_provider() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-stream-unsupported",
            (ProviderTarget("fake", "provider", "model"),),
            "stream-unsupported-test",
            max_attempts=1,
        )
    )
    calls = 0

    async def _complete(_attempt):
        nonlocal calls
        calls += 1
        return "must not run"

    audit = InMemoryAttemptAuditRepository()
    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("fake", complete_fn=_complete)]
        ),
        audit_repository=audit,
    )

    with pytest.raises(ModelBudgetGuardViolation) as raised:
        _ = [
            event
            async for event in service.stream_with_result(
                _request(model_budget_guard=_model_budget_guard())
            )
        ]

    assert raised.value.code == "MODEL_BUDGET_STREAM_UNSUPPORTED"
    assert calls == 0
    assert audit.records() == ()


async def test_unguarded_stream_without_final_usage_protocol_exposes_stable_capability_code() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-stream-unsupported-unguarded",
            (ProviderTarget("fake", "provider", "model"),),
            "stream-unsupported-unguarded-test",
            max_attempts=1,
        )
    )
    audit = InMemoryAttemptAuditRepository()
    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [CallableProviderAdapter("fake", complete_fn=lambda _attempt: "unused")]
        ),
        audit_repository=audit,
    )

    with pytest.raises(ModelStreamProtocolError) as raised:
        _ = [event async for event in service.stream_with_result(_request())]

    assert raised.value.code == "MODEL_STREAM_RESULT_UNSUPPORTED"
    assert audit.records() == ()


async def test_guarded_stream_missing_terminal_cost_is_audited_and_never_falls_back() -> None:
    route = _CountingRoutePolicy(
        RouteDecision(
            "route-stream-missing-cost",
            (
                ProviderTarget("first", "provider-a", "model-a"),
                ProviderTarget("second", "provider-b", "model-b"),
            ),
            "stream-missing-cost-test",
            max_attempts=2,
        )
    )
    calls: list[str] = []

    async def _first(_attempt):
        calls.append("first")
        yield ProviderStreamEvent.delta("这句已经产生。")
        yield ProviderStreamEvent.final(
            ProviderResponse(
                content="这句已经产生。",
                usage={"total_tokens": 7, "completion_tokens": 4},
            )
        )

    async def _second(_attempt):
        calls.append("second")
        yield ProviderStreamEvent.delta("不能选择我。")
        yield ProviderStreamEvent.final(
            ProviderResponse(
                content="不能选择我。",
                usage={
                    "total_tokens": 7,
                    "completion_tokens": 4,
                    "model_cost_micros": 9,
                },
            )
        )

    audit = InMemoryAttemptAuditRepository()
    service = ModelAttemptService(
        prompt_registry=_registry(),
        route_policy=route,
        provider_adapters=ProviderAdapterRegistry(
            [
                CallableProviderAdapter(
                    "first",
                    complete_fn=lambda _attempt: "unused",
                    stream_result_fn=_first,
                ),
                CallableProviderAdapter(
                    "second",
                    complete_fn=lambda _attempt: "unused",
                    stream_result_fn=_second,
                ),
            ]
        ),
        audit_repository=audit,
    )

    received: list[str] = []
    with pytest.raises(ModelStreamInterruptedError):
        async for event in service.stream_with_result(
            _request(model_budget_guard=_model_budget_guard(max_provider_attempts=2))
        ):
            received.append(event.text)

    assert received == ["这句已经产生。"]
    assert calls == ["first"]
    assert [record.status for record in audit.records()] == [
        AttemptStatus.STARTED,
        AttemptStatus.REJECTED,
    ]
    assert audit.records()[-1].error_code == "MODEL_PROVIDER_USAGE_REQUIRED"
