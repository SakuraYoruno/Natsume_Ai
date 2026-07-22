from __future__ import annotations

from dataclasses import replace

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.model_runtime import (
    AttemptStatus,
    LedgerAttemptAuditLookup,
    LedgerAttemptAuditRepository,
    ModelAttemptRecord,
    ModelAttemptResult,
    ProviderResponse,
    ProviderTarget,
    RouteDecision,
)
from host_brain.model_runtime.contracts import sha256_digest
from host_brain.planning_search.attested_external_research_adapter import (
    AttestedExternalResearchAdapter,
)


class _CanonicalModelService:
    def __init__(self, result: ModelAttemptResult) -> None:
        self._result = result

    async def complete(self, request):  # noqa: ANN201
        assert request.request_id == self._result.request_id
        return self._result


def _records() -> tuple[ModelAttemptRecord, ModelAttemptRecord, ModelAttemptResult]:
    target = ProviderTarget("openai-transport", "openai", "gpt-research")
    response = ProviderResponse(
        content="A durable external research answer.",
        metadata={"provider_latency_ms": 37},
    )
    started = ModelAttemptRecord(
        attempt_id="d20c-audit-attempt",
        request_id="d20c-audit-execution",
        route_decision_id="d20c-audit-route",
        attempt_index=1,
        status=AttemptStatus.STARTED,
        retry_reason="initial",
        adapter_id=target.adapter_id,
        provider=target.provider,
        model_id=target.model_id,
        prompt_id="research.prompt",
        prompt_version="1",
        prompt_digest="a" * 64,
        started_at_ms=100,
        metadata={"scene_type": "RESEARCH", "cognitive_tier": "RESEARCH"},
    )
    terminal = replace(
        started,
        status=AttemptStatus.SUCCEEDED,
        ended_at_ms=162,
        latency_ms=62,
        output_digest=sha256_digest(response.content),
        metadata={
            "scene_type": "RESEARCH",
            "cognitive_tier": "RESEARCH",
            "provider_budget_measurements": {
                "token_count": 101,
                "completion_tokens": 33,
                "model_cost_micros": 29,
            },
            "provider_transport_measurements": {"provider_latency_ms": 37},
        },
    )
    result = ModelAttemptResult(
        request_id=started.request_id,
        success=True,
        route_decision=RouteDecision(
            started.route_decision_id,
            (target,),
            "d20c integration test route",
            max_attempts=1,
        ),
        attempts=(terminal,),
        response=response,
        final_target=target,
    )
    return started, terminal, result


async def test_d20c_reopens_sqlite_model_audit_before_external_attestation(tmp_path) -> None:  # noqa: ANN001
    db_path = tmp_path / "d20c-model-audit.sqlite3"
    started, terminal, result = _records()
    ledger = SQLiteLedger(db_path=str(db_path))
    writer = LedgerAttemptAuditRepository(ledger)
    await writer.record_attempt(started)
    await writer.record_attempt(terminal)
    ledger.close()

    reopened = SQLiteLedger(db_path=str(db_path))
    try:
        lookup = LedgerAttemptAuditLookup(reopened)
        assert lookup.records_for_request(result.request_id) == (started, terminal)
        adapter = AttestedExternalResearchAdapter(
            model_attempt_service=_CanonicalModelService(result),
            attempt_audit_lookup=lookup,
        )

        run = await adapter.execute(
            execution_id=result.request_id,
            arm_id="baseline",
            prompt_id="research.prompt",
            messages=({"role": "user", "content": "Use the frozen source."},),
            deadline_ms=1_000,
        )

        assert run.attestation.provider == "openai"
        assert run.attestation.total_tokens == 101
        assert run.attestation.provider_latency_ms == 37
    finally:
        reopened.close()
