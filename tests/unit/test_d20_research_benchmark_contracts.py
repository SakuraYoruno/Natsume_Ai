from __future__ import annotations

import pytest

from host_brain.planning_search.research_benchmark_contracts import (
    ExternalProviderRunAttestationV1,
    PairedResearchRunReceiptV1,
    ResearchBenchmarkContractError,
    ResearchCorpusBindingV1,
    ResearchExecutionBudgetV1,
    ResearchExecutionProfileV1,
    ResearchModelExecutionReceiptV1,
    ResearchVerifierReceiptV1,
)


def _model(execution_id: str, arm_id: str) -> ResearchModelExecutionReceiptV1:
    return ResearchModelExecutionReceiptV1(
        execution_id=execution_id,
        arm_id=arm_id,
        evidence_class="SCRIPTED_REPLAY",
        attempt_ids=(f"attempt::{execution_id}",),
    )


def _verifier(execution_id: str) -> ResearchVerifierReceiptV1:
    return ResearchVerifierReceiptV1(
        execution_id=execution_id,
        citation_count=2,
        cited_span_count=2,
        citation_coverage=1.0,
        citation_validity=1.0,
        evidence_digest="a" * 64,
    )


def test_d20b_binding_and_budget_are_digest_bound() -> None:
    binding = ResearchCorpusBindingV1(
        snapshot_digest="1" * 64,
        corpus_ref="artifact://research/corpus/d20",
        manifest_digest="2" * 64,
        source_ids=("primary", "secondary"),
        source_refs=("artifact://research/primary", "artifact://research/secondary"),
        source_digests=("3" * 64, "4" * 64),
        authority_tiers=("primary", "secondary"),
    )
    budget = ResearchExecutionBudgetV1(
        arm_max_tokens=1_000,
        arm_max_tool_calls=4,
        arm_max_wall_time_ms=10_000,
        pair_max_tokens=2_000,
        pair_max_wall_time_ms=20_000,
        declared_only_fields=("arm_max_tool_calls",),
    )

    assert len(binding.binding_digest) == 64
    assert len(budget.budget_digest) == 64
    assert budget.to_dict()["declared_only_fields"] == ["arm_max_tool_calls"]


def test_d20b_scripted_pair_is_structural_only_and_never_benefit_eligible() -> None:
    profile = ResearchExecutionProfileV1(
        prompt_digest="a" * 64,
        profile_digest="b" * 64,
        output_schema_digest="c" * 64,
    )
    pair = PairedResearchRunReceiptV1(
        corpus_binding_digest="1" * 64,
        profile_digest=profile.profile_digest,
        budget_digest="2" * 64,
        baseline_execution_id="baseline-run",
        selected_execution_id="selected-run",
        baseline_model=_model("baseline-run", "baseline"),
        selected_model=_model("selected-run", "selected"),
        baseline_verifier=_verifier("baseline-run"),
        selected_verifier=_verifier("selected-run"),
        randomized_arm_order=True,
    )

    assert pair.benefit_eligible is False
    assert pair.to_dict()["production_influence_enabled"] is False


def test_d20b_rejects_cache_and_forged_external_attestation() -> None:
    with pytest.raises(ResearchBenchmarkContractError):
        ResearchExecutionProfileV1(
            prompt_digest="a" * 64,
            profile_digest="b" * 64,
            output_schema_digest="c" * 64,
            cache_policy="READ_WRITE",
        )
    with pytest.raises(ResearchBenchmarkContractError):
        ResearchModelExecutionReceiptV1(
            execution_id="run",
            arm_id="arm",
            evidence_class="ATTESTED_EXTERNAL",
            attempt_ids=("attempt",),
        )


def test_d20c_external_attestation_requires_fresh_real_usage() -> None:
    attestation = ExternalProviderRunAttestationV1(
        execution_id="external-run",
        provider="openai",
        model="research-model",
        route_digest="a" * 64,
        response_digest="b" * 64,
        total_tokens=321,
        cost_micros=42,
        provider_latency_ms=120,
        total_latency_ms=180,
    )
    assert attestation.to_dict()["fresh_request"] is True
    with pytest.raises(ResearchBenchmarkContractError):
        ExternalProviderRunAttestationV1(
            execution_id="cached-run",
            provider="localhost-mock",
            model="test",
            route_digest="a" * 64,
            response_digest="b" * 64,
            total_tokens=1,
            cost_micros=1,
            provider_latency_ms=1,
            total_latency_ms=1,
        )
