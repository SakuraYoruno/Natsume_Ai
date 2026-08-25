"""Trusted Lab experiment for one versioned companion expression policy.

The experiment is deliberately small.  It compares the currently active
expression-preference payload with one proposed target payload inside the
existing no-network Docker Lab.  It does not activate either version; the
result can only become an ``EvolutionProposalV2`` for the existing Review and
version-activation authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Mapping, Protocol

from host_brain.contracts.evolution_lab import (
    CounterfactualPairV2,
    DatasetManifestV1,
    EvolutionProposalV2,
    ExperimentSpecV2,
    LabDatasetV1,
    PolicyVariantV1,
    VerificationReceiptV1,
    canonical_evolution_lab_digest,
)
from host_brain.contracts.evolution_lab_trusted import (
    TrustedExperimentReportV2,
    TrustedExperimentRunReceiptV2,
)
from tools.evolution_lab.trusted_runner import TrustedEvolutionLabRunner


EXPERIMENT_KIND = "companion_expression"
VERIFIER_IDENTITY = "verifier://natsume/ne06/companion-expression"
VERIFIER_VERSION = "1.0.0"

_INSTRUCTIONS = {
    "ai_tone": "避免机械 AI 腔",
    "template_like": "避免模板化分段",
    "out_of_persona": "保持既定人格语气",
    "logic_break": "先核对意图和因果",
    "too_helpful": "不要过度提供建议",
    "in_persona": "保持当前自然人格表达",
}


class CompanionExpressionExperimentError(RuntimeError):
    """The focused expression experiment cannot produce trusted evidence."""


class CompanionExpressionEvidenceLedger(Protocol):
    def append_report(self, report: TrustedExperimentReportV2) -> str: ...

    def append_proposal(self, proposal: EvolutionProposalV2) -> str: ...


@dataclass(frozen=True, slots=True)
class CompanionExpressionExperimentResult:
    report: TrustedExperimentReportV2
    proposal: EvolutionProposalV2


class BoundCompanionExpressionDatasetReader:
    """Reopen exactly one content-addressed synthetic policy comparison."""

    def __init__(self, dataset: LabDatasetV1) -> None:
        if not isinstance(dataset, LabDatasetV1):
            raise TypeError("dataset must be LabDatasetV1")
        self._dataset = dataset

    def read(self, *, manifest: DatasetManifestV1) -> LabDatasetV1:
        if not isinstance(manifest, DatasetManifestV1):
            raise CompanionExpressionExperimentError(
                "expression dataset manifest is invalid"
            )
        dataset = self._dataset
        if (
            manifest.source_kind != "synthetic"
            or manifest.redaction_receipt_digest is not None
            or manifest.dataset_id != dataset.dataset_id
            or manifest.dataset_digest != dataset.dataset_digest
            or manifest.record_count != len(dataset.records)
        ):
            raise CompanionExpressionExperimentError(
                "expression dataset drifts from the frozen manifest"
            )
        return dataset


class CompanionExpressionVerifier:
    """Score Docker output against the target payload reopened independently."""

    VERSION = VERIFIER_VERSION

    def __init__(
        self,
        *,
        dataset_reader: BoundCompanionExpressionDatasetReader,
        source_path: str | Path | None = None,
    ) -> None:
        self._dataset_reader = dataset_reader
        self._source_path = Path(
            source_path or Path(__file__).resolve()
        ).resolve()
        if not self._source_path.is_file():
            raise CompanionExpressionExperimentError(
                "expression verifier source is missing"
            )

    @property
    def verifier_digest(self) -> str:
        return hashlib.sha256(self._source_path.read_bytes()).hexdigest()

    def verify(
        self,
        *,
        execution_id: str,
        experiment_kind: str,
        spec: ExperimentSpecV2,
        variant: PolicyVariantV1,
        model_output: Mapping[str, Any],
    ) -> VerificationReceiptV1:
        if str(experiment_kind or "").strip().lower() != EXPERIMENT_KIND:
            raise CompanionExpressionExperimentError(
                "unsupported companion expression experiment kind"
            )
        if (
            spec.verifier_identity != VERIFIER_IDENTITY
            or spec.verifier_version != self.VERSION
            or spec.verifier_digest != self.verifier_digest
        ):
            raise CompanionExpressionExperimentError(
                "expression verifier identity drifts from the frozen spec"
            )
        dataset = self._dataset_reader.read(
            manifest=spec.dataset_manifest
        )
        _validate_output_envelope(
            output=model_output,
            execution_id=execution_id,
            variant=variant,
            spec=spec,
            dataset=dataset,
        )
        record = _single_record(dataset)
        expected = _policy_hints(record.get("target_policy"))
        raw_hints = model_output.get("hints")
        if not isinstance(raw_hints, list):
            raise CompanionExpressionExperimentError(
                "expression worker hints are missing"
            )
        actual = [str(item) for item in raw_hints]
        exact = actual == expected
        output_digest = canonical_evolution_lab_digest(
            {"output": model_output}
        )
        evidence = {
            "schema": (
                "natsume.evolution-lab.companion-expression-evidence.v1"
            ),
            "execution_id": execution_id,
            "variant_id": variant.variant_id,
            "dataset_manifest_digest": (
                spec.dataset_manifest.manifest_digest
            ),
            "output_digest": output_digest,
            "expected_hints": expected,
            "actual_hints": actual,
            "exact": exact,
        }
        return VerificationReceiptV1(
            execution_id=execution_id,
            verifier_identity=spec.verifier_identity,
            verifier_version=spec.verifier_version,
            verifier_digest=spec.verifier_digest,
            status="PASS",
            evidence_digest=canonical_evolution_lab_digest(evidence),
            outcome={"quality": 1.0 if exact else 0.0},
            risk={"policy_errors": 0, "severe_errors": 0},
            metrics={
                "pass_rate": 1.0 if exact else 0.0,
                "output_digest": output_digest,
            },
        )


def build_companion_expression_dataset(
    *,
    baseline_policy: Mapping[str, Any],
    target_policy: Mapping[str, Any],
) -> LabDatasetV1:
    baseline = _policy_payload(baseline_policy)
    target = _policy_payload(target_policy)
    if baseline == target:
        raise CompanionExpressionExperimentError(
            "expression experiment requires a changed target policy"
        )
    identity = canonical_evolution_lab_digest(
        {"baseline_policy": baseline, "target_policy": target}
    )[:24]
    return LabDatasetV1(
        dataset_id=f"ne06-companion-expression-{identity}",
        records=(
            {
                "case_id": "expression-preference-resolution",
                "baseline_policy": baseline,
                "target_policy": target,
            },
        ),
    )


def activation_review_subject_version(expected_generation: int) -> int:
    """Bind Review to the projection generation the transition will create."""

    if isinstance(expected_generation, bool) or int(expected_generation) < 0:
        raise CompanionExpressionExperimentError(
            "expected activation generation must be non-negative"
        )
    return int(expected_generation) + 1


def build_companion_expression_spec(
    *,
    workspace_id: str,
    code_version: str,
    isolation_profile_digest: str,
    verifier_digest: str,
    baseline_version: str,
    target_version: str,
    baseline_policy: Mapping[str, Any],
    target_policy: Mapping[str, Any],
) -> tuple[LabDatasetV1, ExperimentSpecV2]:
    dataset = build_companion_expression_dataset(
        baseline_policy=baseline_policy,
        target_policy=target_policy,
    )
    schema_digest = canonical_evolution_lab_digest(
        {
            "schema": (
                "natsume.evolution-lab.companion-expression-dataset.v1"
            ),
            "record_keys": sorted(dataset.records[0]),
        }
    )
    manifest = DatasetManifestV1(
        workspace_id=workspace_id,
        dataset_id=dataset.dataset_id,
        dataset_digest=dataset.dataset_digest,
        schema_digest=schema_digest,
        record_count=1,
        source_kind="synthetic",
    )
    spec = ExperimentSpecV2(
        experiment_id=(
            "ne06-companion-expression-"
            f"{dataset.dataset_digest[:24]}-{str(code_version)[:12]}"
        ),
        workspace_id=workspace_id,
        dataset_manifest=manifest,
        seed=20_260_808,
        budget={
            "max_runs": 2,
            "max_wall_time_ms": 60_000,
            "memory_limit_bytes": 268_435_456,
            "pids_limit": 64,
        },
        variants=(
            PolicyVariantV1(
                variant_id="baseline",
                policy_version=baseline_version,
                code_version=code_version,
                config_digest=canonical_evolution_lab_digest(
                    _policy_payload(baseline_policy)
                ),
                is_baseline=True,
            ),
            PolicyVariantV1(
                variant_id="candidate",
                policy_version=target_version,
                code_version=code_version,
                config_digest=canonical_evolution_lab_digest(
                    _policy_payload(target_policy)
                ),
                is_baseline=False,
            ),
        ),
        verifier_identity=VERIFIER_IDENTITY,
        verifier_version=VERIFIER_VERSION,
        verifier_digest=verifier_digest,
        isolation_profile_digest=isolation_profile_digest,
    )
    return dataset, spec


def run_companion_expression_experiment(
    *,
    runner: TrustedEvolutionLabRunner,
    ledger: CompanionExpressionEvidenceLedger,
    spec: ExperimentSpecV2,
    target_kind: str,
    owner_id: str,
) -> CompanionExpressionExperimentResult:
    runs = tuple(
        runner.run_variant(
            experiment_kind=EXPERIMENT_KIND,
            spec=spec,
            variant_id=variant.variant_id,
            owner_id=f"{owner_id}:{variant.variant_id}",
        )
        for variant in spec.variants
    )
    baseline_variant = next(
        item.variant_id for item in spec.variants if item.is_baseline
    )
    baseline = next(
        item for item in runs if item.variant_id == baseline_variant
    )
    candidate = next(
        item for item in runs if item.variant_id != baseline_variant
    )
    comparison = _comparison(baseline, candidate)
    report = TrustedExperimentReportV2(
        report_id=(
            "lab-trusted-report-companion-expression-"
            f"{spec.spec_digest[:40]}"
        ),
        experiment_kind=EXPERIMENT_KIND,
        spec=spec,
        runs=runs,
        comparisons=(comparison,),
    )
    baseline_quality = _quality(baseline)
    candidate_quality = _quality(candidate)
    if (
        not all(run.benefit_eligible for run in runs)
        or candidate_quality - baseline_quality < 0.2
        or candidate_quality != 1.0
    ):
        raise CompanionExpressionExperimentError(
            "candidate expression policy does not satisfy the benefit gate"
        )
    ledger.append_report(report)
    candidate_version = spec.variant("candidate").policy_version
    proposal = EvolutionProposalV2(
        proposal_id=(
            "lab-trusted-proposal-companion-expression-"
            f"{report.report_digest[:40]}"
        ),
        report_digest=report.report_digest,
        target_kind=target_kind,
        target_version=candidate_version,
        rationale=(
            "隔离 Docker 双臂确认目标表达偏好可确定性解析，"
            "仅提交 Review，不直接修改下一轮对话。"
        ),
    )
    ledger.append_proposal(proposal)
    return CompanionExpressionExperimentResult(
        report=report,
        proposal=proposal,
    )


def _comparison(
    baseline: TrustedExperimentRunReceiptV2,
    candidate: TrustedExperimentRunReceiptV2,
) -> CounterfactualPairV2:
    return CounterfactualPairV2(
        baseline_run_id=baseline.run_id,
        alternative_run_id=candidate.run_id,
        outcome_delta={
            "quality": _quality(candidate) - _quality(baseline)
        },
        evidence_delta={"trusted_receipts": 0.0},
        cost_delta={
            "microusd": float(
                candidate.cost.get("microusd", 0)
                - baseline.cost.get("microusd", 0)
            )
        },
        latency_delta={
            "milliseconds": float(candidate.latency_ms - baseline.latency_ms)
        },
        risk_delta={"policy_errors": 0.0, "severe_errors": 0.0},
        verification_delta={
            "pass_rate": _quality(candidate) - _quality(baseline)
        },
    )


def _quality(run: TrustedExperimentRunReceiptV2) -> float:
    value = run.verification_receipt.outcome.get("quality")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompanionExpressionExperimentError(
            "expression quality evidence is missing"
        )
    return float(value)


def _single_record(dataset: LabDatasetV1) -> Mapping[str, Any]:
    if len(dataset.records) != 1:
        raise CompanionExpressionExperimentError(
            "expression dataset must contain one policy comparison"
        )
    return dataset.records[0]


def _policy_payload(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompanionExpressionExperimentError(
            "expression policy must be a mapping"
        )
    raw_scores = value.get("tag_scores")
    if not isinstance(raw_scores, Mapping):
        raise CompanionExpressionExperimentError(
            "expression policy tag_scores must be a mapping"
        )
    return {
        "schema": str(value.get("schema") or "").strip(),
        "positive_feedback_count": int(
            value.get("positive_feedback_count", 0)
        ),
        "negative_feedback_count": int(
            value.get("negative_feedback_count", 0)
        ),
        "tag_scores": {
            str(key): int(item)
            for key, item in sorted(raw_scores.items())
        },
    }


def _policy_hints(value: Mapping[str, Any] | Any) -> list[str]:
    policy = _policy_payload(value)
    scores = policy["tag_scores"]
    ranked: list[tuple[int, str]] = []
    for tag, instruction in _INSTRUCTIONS.items():
        score = int(scores.get(tag, 0))
        if (
            (tag == "in_persona" and score > 0)
            or (tag != "in_persona" and score < 0)
        ):
            ranked.append((abs(score), instruction))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [instruction for _score, instruction in ranked[:3]]


def _validate_output_envelope(
    *,
    output: Mapping[str, Any],
    execution_id: str,
    variant: PolicyVariantV1,
    spec: ExperimentSpecV2,
    dataset: LabDatasetV1,
) -> None:
    expected = {
        "schema": (
            "natsume.evolution-lab.companion-expression-worker-output.v1"
        ),
        "execution_id": execution_id,
        "experiment_kind": EXPERIMENT_KIND,
        "variant_id": variant.variant_id,
        "seed": spec.seed,
        "dataset_id": dataset.dataset_id,
        "dataset_digest": dataset.dataset_digest,
    }
    if not isinstance(output, Mapping) or any(
        output.get(field) != value for field, value in expected.items()
    ):
        raise CompanionExpressionExperimentError(
            "expression worker output identity drift"
        )


__all__ = [
    "BoundCompanionExpressionDatasetReader",
    "CompanionExpressionExperimentError",
    "CompanionExpressionExperimentResult",
    "CompanionExpressionVerifier",
    "EXPERIMENT_KIND",
    "activation_review_subject_version",
    "build_companion_expression_dataset",
    "build_companion_expression_spec",
    "run_companion_expression_experiment",
]
