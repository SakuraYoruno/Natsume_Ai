from __future__ import annotations

from dataclasses import replace

import pytest

from host_brain.model_runtime.contracts import (
    AttemptStatus,
    ModelAttemptRecord,
    ModelAttemptResult,
    ProviderResponse,
    ProviderTarget,
    RouteDecision,
    sha256_digest,
)
from host_brain.planning_search.attested_external_research_adapter import (
    AttestedExternalResearchAdapter,
    AttestedExternalResearchError,
)


class _AttemptService:
    def __init__(self, result: ModelAttemptResult) -> None:
        self.result = result
        self.requests = []

    async def complete(self, request):  # noqa: ANN201
        self.requests.append(request)
        return self.result


class _AuditLookup:
    def __init__(self, records) -> None:  # noqa: ANN001
        self.records = tuple(records)
        self.request_ids = []

    def records_for_request(self, request_id: str):  # noqa: ANN201
        self.request_ids.append(request_id)
        return self.records


def _result(*, cached: bool = False, provider_latency: int | None = 87) -> ModelAttemptResult:
    target = ProviderTarget("openai-transport", "openai", "gpt-research")
    response = ProviderResponse(
        content="Research output with an auditable citation.",
        metadata=(
            {"provider_latency_ms": provider_latency}
            if provider_latency is not None
            else {}
        ),
    )
    metadata = {
        "provider_budget_measurements": {
            "token_count": 123,
            "completion_tokens": 34,
            "model_cost_micros": 56,
        }
    }
    if provider_latency is not None:
        metadata["provider_transport_measurements"] = {
            "provider_latency_ms": provider_latency
        }
    attempt = ModelAttemptRecord(
        attempt_id="model-attempt-1",
        request_id="research-execution-1",
        route_decision_id="research-route-1",
        attempt_index=1,
        status=AttemptStatus.SUCCEEDED,
        retry_reason="initial",
        adapter_id=target.adapter_id,
        provider=target.provider,
        model_id=target.model_id,
        prompt_id="research.prompt",
        prompt_version="1",
        prompt_digest="a" * 64,
        started_at_ms=1,
        ended_at_ms=121,
        latency_ms=120,
        output_digest=sha256_digest(response.content),
        metadata=metadata,
    )
    return ModelAttemptResult(
        request_id="research-execution-1",
        success=True,
        route_decision=RouteDecision(
            "research-route-1",
            (target,),
            "research-external-test",
            max_attempts=1,
        ),
        attempts=(attempt,),
        response=response,
        final_target=target,
        cached=cached,
    )


def _audit_records(result: ModelAttemptResult):  # noqa: ANN201
    terminal = result.attempts[0]
    started = replace(
        terminal,
        status=AttemptStatus.STARTED,
        ended_at_ms=None,
        latency_ms=None,
        output_digest=None,
        error_code=None,
        error_message=None,
        metadata={"scene_type": "RESEARCH", "cognitive_tier": "RESEARCH"},
    )
    return started, terminal


async def test_d20c_adapter_builds_attested_receipt_from_one_fresh_canonical_attempt() -> None:
    result = _result()
    service = _AttemptService(result)
    audit = _AuditLookup(_audit_records(result))
    adapter = AttestedExternalResearchAdapter(
        model_attempt_service=service,
        attempt_audit_lookup=audit,
    )

    run = await adapter.execute(
        execution_id="research-execution-1",
        arm_id="baseline",
        prompt_id="research.prompt",
        messages=({"role": "user", "content": "Summarize the frozen corpus."},),
        deadline_ms=1_000,
    )

    assert run.model_receipt.benefit_eligible is True
    assert run.attestation.provider == "openai"
    assert run.model_receipt.attestation_ref == run.attestation.attestation_ref
    assert service.requests[0].request_id == "research-execution-1"
    assert service.requests[0].foreground is True
    assert service.requests[0].max_attempts == 1
    assert service.requests[0].metadata == {
        "research_external_execution_id": "research-execution-1",
        "research_arm_id": "baseline",
    }
    assert audit.request_ids == ["research-execution-1"]


async def test_d20c_adapter_rejects_cached_and_incomplete_transport_evidence() -> None:
    cached = AttestedExternalResearchAdapter(
        model_attempt_service=_AttemptService(_result(cached=True)),
        attempt_audit_lookup=_AuditLookup(()),
    )
    missing_latency = AttestedExternalResearchAdapter(
        model_attempt_service=_AttemptService(_result(provider_latency=None)),
        attempt_audit_lookup=_AuditLookup(()),
    )

    with pytest.raises(AttestedExternalResearchError, match="cached"):
        await cached.execute(
            execution_id="research-execution-1",
            arm_id="baseline",
            prompt_id="research.prompt",
            messages=({"role": "user", "content": "x"},),
            deadline_ms=1_000,
        )
    with pytest.raises(AttestedExternalResearchError, match="provider_latency_ms"):
        await missing_latency.execute(
            execution_id="research-execution-1",
            arm_id="baseline",
            prompt_id="research.prompt",
            messages=({"role": "user", "content": "x"},),
            deadline_ms=1_000,
        )


async def test_d20c_adapter_rejects_a_retry_or_route_identity_drift() -> None:
    result = _result()
    second = replace(
        result.attempts[0],
        attempt_id="model-attempt-2",
        attempt_index=2,
    )
    retry_result = replace(result, attempts=(result.attempts[0], second))
    adapter = AttestedExternalResearchAdapter(
        model_attempt_service=_AttemptService(retry_result),
        attempt_audit_lookup=_AuditLookup(()),
    )

    with pytest.raises(AttestedExternalResearchError, match="exactly one"):
        await adapter.execute(
            execution_id="research-execution-1",
            arm_id="baseline",
            prompt_id="research.prompt",
            messages=({"role": "user", "content": "x"},),
            deadline_ms=1_000,
        )

    foreign_target = ProviderTarget("foreign-adapter", "foreign", "foreign-model")
    route_drift = replace(
        result,
        attempts=(
            replace(
                result.attempts[0],
                adapter_id=foreign_target.adapter_id,
                provider=foreign_target.provider,
                model_id=foreign_target.model_id,
            ),
        ),
        final_target=foreign_target,
    )
    drifted = AttestedExternalResearchAdapter(
        model_attempt_service=_AttemptService(route_drift),
        attempt_audit_lookup=_AuditLookup(_audit_records(route_drift)),
    )
    with pytest.raises(AttestedExternalResearchError, match="selected external route"):
        await drifted.execute(
            execution_id="research-execution-1",
            arm_id="baseline",
            prompt_id="research.prompt",
            messages=({"role": "user", "content": "x"},),
            deadline_ms=1_000,
        )
