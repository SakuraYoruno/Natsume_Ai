from __future__ import annotations

import hashlib
import json

import pytest

from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ExternalProviderRunAttestationV1,
    ResearchModelExecutionReceiptV1,
)
from host_brain.planning_search.attested_external_research_adapter import (
    AttestedExternalResearchAdapter,
    AttestedExternalResearchRun,
)
from host_brain.planning_search.research_paired_execution import (
    ResearchTransientArmOutputV1,
)
from host_brain.planning_search.research_quality_verifier import (
    AttestedResearchQualityVerifier,
    ResearchQualityVerifierError,
    research_quality_profile_digest,
    research_quality_rubric_digest,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _run(*, execution_id: str, arm_id: str, output_text: str, prompt: str) -> AttestedExternalResearchRun:
    attestation = ExternalProviderRunAttestationV1(
        execution_id=execution_id,
        provider="deepseek",
        model="deepseek-v4-flash",
        route_digest=_digest(f"route:{execution_id}"),
        response_digest=_digest(output_text),
        total_tokens=37,
        cost_micros=8,
        provider_latency_ms=410,
        total_latency_ms=425,
        transport_identity_digest=_digest("https://api.deepseek.com"),
        transport_external=True,
    )
    receipt = ResearchModelExecutionReceiptV1(
        execution_id=execution_id,
        arm_id=arm_id,
        evidence_class="ATTESTED_EXTERNAL",
        attempt_ids=(f"attempt:{execution_id}",),
        prompt_digest=_digest(prompt),
        tool_call_count=0,
        total_tokens=37,
        cost_micros=8,
        attestation_ref=attestation.attestation_ref,
        attestation=attestation,
    )
    return AttestedExternalResearchRun(
        execution_id=execution_id,
        arm_id=arm_id,
        output_text=output_text,
        attestation=attestation,
        model_receipt=receipt,
    )


class _ExternalAdapter(AttestedExternalResearchAdapter):
    def __init__(self, *, verifier_output: str) -> None:
        self.verifier_output = verifier_output
        self.calls: list[dict[str, object]] = []

    async def execute(self, **kwargs):  # noqa: ANN201
        self.calls.append(dict(kwargs))
        return _run(
            execution_id=str(kwargs["execution_id"]),
            arm_id=str(kwargs["arm_id"]),
            output_text=self.verifier_output,
            prompt="hidden-quality-prompt",
        )


@pytest.mark.asyncio
async def test_hidden_verifier_binds_fresh_external_evidence_without_arm_identity() -> None:
    candidate_text = json.dumps(
        {
            "answer": "冻结事实表明需要独立验证。",
            "citations": [
                {
                    "source_id": "source-a",
                    "start_byte": 0,
                    "end_byte": 12,
                    "span_digest": "a" * 64,
                }
            ],
        },
        ensure_ascii=False,
    )
    candidate = ResearchTransientArmOutputV1(
        role="SELECTED",
        run=_run(
            execution_id="research-arm-selected",
            arm_id="selected",
            output_text=candidate_text,
            prompt="selected-prompt",
        ),
    )
    adapter = _ExternalAdapter(
        verifier_output=json.dumps(
            {
                "quality_score": 0.75,
                "severe_policy_errors": 0,
                "severe_conflict_errors": 0,
            }
        )
    )

    receipt = await AttestedResearchQualityVerifier(
        external_adapter=adapter
    ).verify(
        run_id="research-run-1",
        arm_output=candidate,
        objective="判断方案是否满足冻结事实。",
        expected_points=("使用独立验证",),
        frozen_source_facts=("source-a：结论必须经过独立验证。",),
    )

    assert receipt.arm_role == "SELECTED"
    assert receipt.evaluated_response_digest == _digest(candidate_text)
    assert receipt.quality_score == 0.75
    assert receipt.verifier_profile_digest == research_quality_profile_digest()
    assert receipt.rubric_digest == research_quality_rubric_digest()
    assert receipt.verifier_prompt_digest == _digest("hidden-quality-prompt")
    assert receipt.benefit_eligible is True
    messages = adapter.calls[0]["messages"]
    serialized = json.dumps(messages, ensure_ascii=False)
    assert "BASELINE" not in serialized
    assert "SELECTED" not in serialized
    assert candidate_text not in receipt.to_dict().values()


@pytest.mark.asyncio
async def test_hidden_verifier_rejects_candidate_output_shape_drift() -> None:
    adapter = _ExternalAdapter(
        verifier_output='{"quality_score":1,"severe_policy_errors":0,"severe_conflict_errors":0}'
    )
    candidate = ResearchTransientArmOutputV1(
        role="BASELINE",
        run=_run(
            execution_id="research-arm-baseline",
            arm_id="baseline",
            output_text='{"answer":"missing citations"}',
            prompt="baseline-prompt",
        ),
    )

    with pytest.raises(ResearchQualityVerifierError, match="shape drifted"):
        await AttestedResearchQualityVerifier(external_adapter=adapter).verify(
            run_id="research-run-2",
            arm_output=candidate,
            objective="验证答案。",
            expected_points=("一个覆盖点",),
            frozen_source_facts=("一个冻结事实",),
        )

    assert adapter.calls == []


@pytest.mark.asyncio
async def test_hidden_verifier_rejects_unbounded_or_malformed_score() -> None:
    adapter = _ExternalAdapter(
        verifier_output='{"quality_score":1.2,"severe_policy_errors":0,"severe_conflict_errors":0}'
    )
    candidate = ResearchTransientArmOutputV1(
        role="BASELINE",
        run=_run(
            execution_id="research-arm-score",
            arm_id="baseline-score",
            output_text='{"answer":"答案","citations":[]}',
            prompt="score-prompt",
        ),
    )

    with pytest.raises(ResearchQualityVerifierError, match="within"):
        await AttestedResearchQualityVerifier(external_adapter=adapter).verify(
            run_id="research-run-3",
            arm_output=candidate,
            objective="验证答案。",
            expected_points=("一个覆盖点",),
            frozen_source_facts=("一个冻结事实",),
        )
