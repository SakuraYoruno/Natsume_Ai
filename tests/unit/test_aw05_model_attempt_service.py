from __future__ import annotations

import asyncio

import pytest

from host_brain.model_runtime import (
    AttemptAuditError,
    AttemptStatus,
    CachePolicy,
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ModelAttemptRequest,
    ModelAttemptService,
    OutputContract,
    PromptRef,
    PromptRegistry,
    ProviderAdapterRegistry,
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

    result = await service.complete(_request(max_attempts=2, retry_evaluator=_retry))

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
