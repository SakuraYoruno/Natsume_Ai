"""Canonical-model adapter for D20c external Research evidence.

This module is deliberately outside the pure Research successor catalog.  It
does not choose a provider or inspect credentials: it asks the already
composed :class:`ModelAttemptService` to execute one fresh foreground request,
then converts only a complete, non-cached single-attempt result into the
immutable D20c attestation contracts.  Missing external configuration remains
an execution failure, not simulated benefit evidence.
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
    AttemptStatus,
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

    def __init__(self, *, model_attempt_service: ModelAttemptService) -> None:
        if not callable(getattr(model_attempt_service, "complete", None)):
            raise TypeError("model_attempt_service must implement complete()")
        self._model_attempt_service = model_attempt_service

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
        )


def _attest_result(
    *,
    execution_id: str,
    arm_id: str,
    result: ModelAttemptResult,
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


__all__ = [
    "ATTESTED_EXTERNAL_RESEARCH_ADAPTER_ID",
    "AttestedExternalResearchAdapter",
    "AttestedExternalResearchError",
    "AttestedExternalResearchRun",
]
