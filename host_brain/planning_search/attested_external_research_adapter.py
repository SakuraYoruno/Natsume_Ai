"""Canonical-model adapter for D20c external Research evidence.

This module is deliberately outside the pure Research successor catalog.  It
does not choose a provider or inspect credentials: it asks the already
composed :class:`ModelAttemptService` to execute one fresh foreground request,
then converts only a complete, non-cached single-attempt result into the
immutable D20c attestation contracts.  Missing external configuration remains
an execution failure, not simulated benefit evidence.  Before that call it
reopens the canonical audit and rejects any prior record for the exact
execution identity, so a crash cannot silently reissue an external request.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ExternalProviderRunAttestationV1,
    ResearchModelExecutionReceiptV1,
)
from host_brain.model_runtime.contracts import (
    AttemptAuditLookup,
    AttemptStatus,
    ModelAttemptRecord,
    ModelAttemptRequest,
    ModelAttemptResult,
    PromptRef,
    sha256_digest,
)
from host_brain.model_runtime.model_attempt_service import ModelAttemptService


ATTESTED_EXTERNAL_RESEARCH_ADAPTER_ID = "ATTESTED_EXTERNAL_RESEARCH_ADAPTER_V1"
"""Stable issuer identity for the one D20c canonical model adapter."""


class AttestedExternalResearchError(RuntimeError):
    """A model result cannot be used as D20c external evidence."""


@dataclass(frozen=True, slots=True)
class AttestedExternalResearchRun:
    """Private output plus durable-safe evidence for exactly one Research arm.

    ``output_text`` is handed to the next experiment/verifier step and must
    not be persisted in the public Research ledger.  The attestation and
    model receipt deliberately contain only digests, route identity and usage.
    """

    execution_id: str
    arm_id: str
    output_text: str
    attestation: ExternalProviderRunAttestationV1
    model_receipt: ResearchModelExecutionReceiptV1


class AttestedExternalResearchAdapter:
    """Run one fresh Research arm through canonical model-attempt authority."""

    def __init__(
        self,
        *,
        model_attempt_service: ModelAttemptService,
        attempt_audit_lookup: AttemptAuditLookup,
    ) -> None:
        if not callable(getattr(model_attempt_service, "complete", None)):
            raise TypeError("model_attempt_service must implement complete()")
        if not callable(getattr(attempt_audit_lookup, "records_for_request", None)):
            raise TypeError("attempt_audit_lookup must implement records_for_request()")
        self._model_attempt_service = model_attempt_service
        self._attempt_audit_lookup = attempt_audit_lookup

    async def execute(
        self,
        *,
        execution_id: str,
        arm_id: str,
        prompt_id: str,
        messages: Sequence[Mapping[str, Any]],
        deadline_ms: int,
        cognitive_tier: str = "RESEARCH",
    ) -> AttestedExternalResearchRun:
        """Execute one non-cached external arm or reject it as non-attestable.

        The adapter never takes provider/model/base-url/cache controls from a
        caller.  The prompt registry and route policy remain the sole model
        authority.  Foreground mode prevents global-cache use; a returned
        cache hit or route retry is rejected rather than relabeled as a fresh
        external experiment execution.
        """

        normalized_execution_id = _text(execution_id, field="execution_id", limit=160)
        normalized_arm_id = _text(arm_id, field="arm_id", limit=128)
        normalized_prompt_id = _text(prompt_id, field="prompt_id", limit=240)
        normalized_tier = _text(cognitive_tier, field="cognitive_tier", limit=80).upper()
        if isinstance(messages, (str, bytes, bytearray)):
            raise AttestedExternalResearchError("messages must be an explicit sequence")
        normalized_messages = tuple(dict(item) for item in messages)
        if not normalized_messages:
            raise AttestedExternalResearchError("messages must not be empty")
        if isinstance(deadline_ms, bool) or not isinstance(deadline_ms, int) or deadline_ms <= 0:
            raise AttestedExternalResearchError("deadline_ms must be a positive integer")

        # A completed or merely-started canonical request is already an
        # external-effect ambiguity.  Do not call a provider again with the
        # same D20 execution identity: the durable model audit deliberately
        # excludes response bodies, so a restarted experiment cannot safely
        # reconstruct and verify an interrupted arm.  The paired-run owner
        # must reconcile that audit/ledger boundary before it can progress.
        _assert_no_prior_attempt_audit(
            execution_id=normalized_execution_id,
            lookup=self._attempt_audit_lookup,
        )

        result = await self._model_attempt_service.complete(
            ModelAttemptRequest(
                request_id=normalized_execution_id,
                scene_type="RESEARCH",
                cognitive_tier=normalized_tier,
                prompt=PromptRef(
                    normalized_prompt_id,
                    variables={"messages": normalized_messages},
                ),
                deadline_ms=deadline_ms,
                max_attempts=1,
                foreground=True,
                metadata={
                    "research_external_execution_id": normalized_execution_id,
                    "research_arm_id": normalized_arm_id,
                },
            )
        )
        return _attest_result(
            execution_id=normalized_execution_id,
            arm_id=normalized_arm_id,
            result=result,
            attempt_audit_lookup=self._attempt_audit_lookup,
        )


def _attest_result(
    *,
    execution_id: str,
    arm_id: str,
    result: ModelAttemptResult,
    attempt_audit_lookup: AttemptAuditLookup,
) -> AttestedExternalResearchRun:
    if not isinstance(result, ModelAttemptResult):
        raise AttestedExternalResearchError("canonical model service returned an invalid result")
    if result.request_id != execution_id:
        raise AttestedExternalResearchError("model result execution identity drifted")
    if result.cached:
        raise AttestedExternalResearchError("cached model results are not D20c attestation evidence")
    if not result.success or result.response is None or result.final_target is None:
        raise AttestedExternalResearchError("external model execution did not succeed")
    if len(result.attempts) != 1:
        raise AttestedExternalResearchError("D20c external evidence requires exactly one model attempt")
    attempt = result.attempts[0]
    target = result.final_target
    if (
        attempt.status is not AttemptStatus.SUCCEEDED
        or attempt.request_id != execution_id
        or attempt.route_decision_id != result.route_decision.decision_id
        or attempt.adapter_id != target.adapter_id
        or attempt.provider != target.provider
        or attempt.model_id != target.model_id
        or target.identity()
        not in {candidate.identity() for candidate in result.route_decision.targets}
    ):
        raise AttestedExternalResearchError("model attempt does not bind the selected external route")
    if attempt.latency_ms is None:
        raise AttestedExternalResearchError("external model result is missing total latency")
    response_digest = sha256_digest(result.response.content)
    if attempt.output_digest != response_digest:
        raise AttestedExternalResearchError("model output digest drifted from the terminal attempt")
    measurements = dict(attempt.metadata.get("provider_budget_measurements") or {})
    total_tokens = _non_negative_int(measurements.get("token_count"), field="token_count")
    cost_micros = _non_negative_int(
        measurements.get("model_cost_micros"),
        field="model_cost_micros",
    )
    provider_latency_ms = _non_negative_int(
        result.response.metadata.get("provider_latency_ms"),
        field="provider_latency_ms",
    )
    total_latency_ms = _non_negative_int(attempt.latency_ms, field="total_latency_ms")
    _assert_durable_attempt_audit(
        execution_id=execution_id,
        attempt=attempt,
        provider_latency_ms=provider_latency_ms,
        lookup=attempt_audit_lookup,
    )
    route_digest = sha256_digest(
        {
            "schema": "natsume.research.external-provider-route.v1",
            "execution_id": execution_id,
            "route_decision_id": result.route_decision.decision_id,
            "attempt_id": attempt.attempt_id,
            "target": {
                "adapter_id": target.adapter_id,
                "provider": target.provider,
                "model_id": target.model_id,
            },
        }
    )
    attestation = ExternalProviderRunAttestationV1(
        execution_id=execution_id,
        provider=target.provider,
        model=target.model_id,
        route_digest=route_digest,
        response_digest=response_digest,
        total_tokens=total_tokens,
        cost_micros=cost_micros,
        provider_latency_ms=provider_latency_ms,
        total_latency_ms=total_latency_ms,
    )
    model_receipt = ResearchModelExecutionReceiptV1(
        execution_id=execution_id,
        arm_id=arm_id,
        evidence_class="ATTESTED_EXTERNAL",
        attempt_ids=(attempt.attempt_id,),
        total_tokens=total_tokens,
        cost_micros=cost_micros,
        attestation_ref=attestation.attestation_ref,
        attestation=attestation,
    )
    return AttestedExternalResearchRun(
        execution_id=execution_id,
        arm_id=arm_id,
        output_text=result.response.content,
        attestation=attestation,
        model_receipt=model_receipt,
    )


def _text(value: Any, *, field: str, limit: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise AttestedExternalResearchError(f"{field} must be non-empty and bounded")
    return result


def _non_negative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AttestedExternalResearchError(f"external model result is missing {field}")
    return value


def _assert_durable_attempt_audit(
    *,
    execution_id: str,
    attempt: ModelAttemptRecord,
    provider_latency_ms: int,
    lookup: AttemptAuditLookup,
) -> None:
    """Reopen canonical audit rows before certifying external-model evidence."""

    try:
        records = tuple(lookup.records_for_request(execution_id))
    except Exception as exc:
        raise AttestedExternalResearchError("canonical model attempt audit lookup failed") from exc
    if len(records) != 2:
        raise AttestedExternalResearchError(
            "external evidence requires exactly one durable started/terminal attempt pair"
        )
    if not all(isinstance(record, ModelAttemptRecord) for record in records):
        raise AttestedExternalResearchError("canonical model attempt audit returned an invalid record")
    started = [record for record in records if record.status is AttemptStatus.STARTED]
    terminal = [record for record in records if record.status is AttemptStatus.SUCCEEDED]
    if len(started) != 1 or len(terminal) != 1:
        raise AttestedExternalResearchError(
            "external evidence requires one durable started and succeeded attempt"
        )
    durable_started = started[0]
    durable_terminal = terminal[0]
    if durable_terminal != attempt:
        raise AttestedExternalResearchError("canonical model attempt audit does not match result")
    if (
        durable_started.attempt_id != attempt.attempt_id
        or durable_started.request_id != execution_id
        or durable_started.route_decision_id != attempt.route_decision_id
        or durable_started.attempt_index != attempt.attempt_index
        or durable_started.retry_reason != attempt.retry_reason
        or durable_started.adapter_id != attempt.adapter_id
        or durable_started.provider != attempt.provider
        or durable_started.model_id != attempt.model_id
        or durable_started.prompt_id != attempt.prompt_id
        or durable_started.prompt_version != attempt.prompt_version
        or durable_started.prompt_digest != attempt.prompt_digest
        or durable_started.started_at_ms != attempt.started_at_ms
        or durable_started.ended_at_ms is not None
        or durable_started.latency_ms is not None
        or durable_started.output_digest is not None
    ):
        raise AttestedExternalResearchError("durable started attempt does not bind result")
    transport = durable_terminal.metadata.get("provider_transport_measurements")
    if not isinstance(transport, Mapping) or transport.get("provider_latency_ms") != provider_latency_ms:
        raise AttestedExternalResearchError(
            "canonical model attempt audit is missing provider latency"
        )


def _assert_no_prior_attempt_audit(
    *,
    execution_id: str,
    lookup: AttemptAuditLookup,
) -> None:
    """Block a duplicate external request before its first provider call.

    ``request_id`` is the durable D20 arm execution identity, not a best-effort
    retry token.  Any prior audit row means the caller must reconcile the
    ledger and canonical model trail instead of causing an unbounded external
    duplicate.  A lookup fault is equally unsafe because the adapter cannot
    establish that the execution is fresh.
    """

    try:
        prior = tuple(lookup.records_for_request(execution_id))
    except Exception as exc:
        raise AttestedExternalResearchError(
            "cannot inspect canonical model audit before external execution"
        ) from exc
    if prior:
        raise AttestedExternalResearchError(
            "external execution id already has canonical audit records; reconciliation required"
        )


__all__ = [
    "ATTESTED_EXTERNAL_RESEARCH_ADAPTER_ID",
    "AttestedExternalResearchAdapter",
    "AttestedExternalResearchError",
    "AttestedExternalResearchRun",
]
