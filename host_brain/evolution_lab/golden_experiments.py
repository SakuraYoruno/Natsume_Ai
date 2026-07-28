"""Orchestration for the three mandatory trusted NE-04 experiments."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Mapping, Protocol

from host_brain.contracts.evolution_lab import (
    CounterfactualPairV2,
    EvolutionProposalV2,
    ExperimentSpecV2,
)
from host_brain.contracts.evolution_lab_trusted import (
    GoldenExperimentSuiteReceiptV1,
    TrustedExperimentReportV2,
    TrustedExperimentRunReceiptV2,
)
from host_brain.core.ids import now_ms
from host_brain.evolution_lab.docker_executor import (
    TrustedDockerSandboxExecutor,
)
from host_brain.evolution_lab.golden_datasets import (
    GOLDEN_EXPERIMENT_KINDS,
    build_golden_spec,
)
from host_brain.evolution_lab.golden_verifier import (
    GoldenExperimentVerifier,
)
from host_brain.evolution_lab.trusted_runner import (
    TrustedEvolutionLabRunner,
)


_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_MIGRATION_HEAD = "0140#evolution_lab_redacted_dataset"
_TARGET_KIND = {
    "research_planning": "research_planning_policy",
    "proactive": "proactive_policy",
    "memory_write": "memory_admission_policy",
}


class GoldenExperimentServiceError(RuntimeError):
    """The complete trusted suite cannot become durable proposal evidence."""


class GoldenEvidenceLedger(Protocol):
    def append_report(self, report: TrustedExperimentReportV2) -> str: ...

    def append_proposal(self, proposal: EvolutionProposalV2) -> str: ...

    def append_golden_suite(
        self, receipt: GoldenExperimentSuiteReceiptV1
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class GoldenExperimentSuiteResult:
    reports: Mapping[str, TrustedExperimentReportV2]
    proposals: Mapping[str, EvolutionProposalV2]
    receipt: GoldenExperimentSuiteReceiptV1


class GoldenExperimentService:
    """Run independent Docker arms, compare them, and emit proposals only."""

    def __init__(
        self,
        *,
        runner: TrustedEvolutionLabRunner,
        executor: TrustedDockerSandboxExecutor,
        verifier: GoldenExperimentVerifier,
        ledger: GoldenEvidenceLedger,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        if not isinstance(runner, TrustedEvolutionLabRunner):
            raise TypeError("runner must be TrustedEvolutionLabRunner")
        if not isinstance(executor, TrustedDockerSandboxExecutor):
            raise TypeError("executor must be TrustedDockerSandboxExecutor")
        if not isinstance(verifier, GoldenExperimentVerifier):
            raise TypeError("verifier must be GoldenExperimentVerifier")
        self._runner = runner
        self._executor = executor
        self._verifier = verifier
        self._ledger = ledger
        self._clock = clock

    def run_suite(
        self,
        *,
        workspace_id: str,
        commit_sha: str,
        owner_id: str,
    ) -> GoldenExperimentSuiteResult:
        commit = str(commit_sha or "").strip().lower()
        if _COMMIT_SHA.fullmatch(commit) is None:
            raise GoldenExperimentServiceError(
                "golden suite requires a full lowercase Git commit SHA"
            )
        reports: dict[str, TrustedExperimentReportV2] = {}
        proposals: dict[str, EvolutionProposalV2] = {}
        for kind in GOLDEN_EXPERIMENT_KINDS:
            spec = build_golden_spec(
                experiment_kind=kind,
                workspace_id=workspace_id,
                code_version=commit,
                isolation_profile_digest=(
                    self._executor.sandbox_profile_digest
                ),
                verifier_digest=self._verifier.verifier_digest,
            )
            runs = tuple(
                self._runner.run_variant(
                    experiment_kind=kind,
                    spec=spec,
                    variant_id=variant.variant_id,
                    owner_id=f"{owner_id}:{kind}:{variant.variant_id}",
                )
                for variant in spec.variants
            )
            report = _build_report(
                experiment_kind=kind,
                spec=spec,
                runs=runs,
            )
            self._ledger.append_report(report)
            _require_candidate_benefit(report)
            proposal = EvolutionProposalV2(
                proposal_id=f"lab-trusted-proposal-{report.report_digest[:48]}",
                report_digest=report.report_digest,
                target_kind=_TARGET_KIND[kind],
                target_version=spec.variant("candidate").policy_version,
                rationale=(
                    "可信 Docker 双臂实验显示候选策略提高质量且未产生"
                    "严重策略错误；仅提交 Review，不直接修改生产状态。"
                ),
            )
            self._ledger.append_proposal(proposal)
            reports[kind] = report
            proposals[kind] = proposal
        all_runs = tuple(
            run for report in reports.values() for run in report.runs
        )
        engine_identities = {
            run.sandbox_attestation.engine_identity for run in all_runs
        }
        image_references = {
            run.sandbox_attestation.image_reference for run in all_runs
        }
        if len(engine_identities) != 1 or len(image_references) != 1:
            raise GoldenExperimentServiceError(
                "golden suite arms drift across Docker engine or image"
            )
        created_at_ms = max(
            (
                run.sandbox_attestation.finished_at_ms
                for run in all_runs
            ),
            default=int(self._clock()),
        )
        suite = GoldenExperimentSuiteReceiptV1(
            suite_id=(
                "ne04-golden-suite-"
                f"{commit[:12]}-{self._executor.sandbox_profile_digest[:12]}"
            ),
            commit_sha=commit,
            migration_head=_MIGRATION_HEAD,
            engine_identity=next(iter(engine_identities)),
            image_reference=next(iter(image_references)),
            report_digests={
                kind: reports[kind].report_digest
                for kind in GOLDEN_EXPERIMENT_KINDS
            },
            run_receipt_digests=tuple(
                run.receipt_digest for run in all_runs
            ),
            created_at_ms=created_at_ms,
        )
        self._ledger.append_golden_suite(suite)
        return GoldenExperimentSuiteResult(
            reports=dict(reports),
            proposals=dict(proposals),
            receipt=suite,
        )


def _build_report(
    *,
    experiment_kind: str,
    spec: ExperimentSpecV2,
    runs: tuple[TrustedExperimentRunReceiptV2, ...],
) -> TrustedExperimentReportV2:
    baseline_variant = next(
        item.variant_id for item in spec.variants if item.is_baseline
    )
    baseline = next(
        item for item in runs if item.variant_id == baseline_variant
    )
    comparisons = tuple(
        _compare(baseline, alternative)
        for alternative in runs
        if alternative.variant_id != baseline_variant
    )
    return TrustedExperimentReportV2(
        report_id=f"lab-trusted-report-{spec.spec_digest[:48]}",
        experiment_kind=experiment_kind,
        spec=spec,
        runs=runs,
        comparisons=comparisons,
    )


def _compare(
    baseline: TrustedExperimentRunReceiptV2,
    candidate: TrustedExperimentRunReceiptV2,
) -> CounterfactualPairV2:
    baseline_verification = baseline.verification_receipt
    candidate_verification = candidate.verification_receipt
    return CounterfactualPairV2(
        baseline_run_id=baseline.run_id,
        alternative_run_id=candidate.run_id,
        outcome_delta={
            "quality": _number(
                candidate_verification.outcome.get("quality")
            )
            - _number(baseline_verification.outcome.get("quality"))
        },
        evidence_delta={
            "trusted_receipts": float(
                int(candidate.benefit_eligible)
                - int(baseline.benefit_eligible)
            )
        },
        cost_delta={
            "microusd": float(
                candidate.cost.get("microusd", 0)
                - baseline.cost.get("microusd", 0)
            )
        },
        latency_delta={
            "milliseconds": float(
                candidate.latency_ms - baseline.latency_ms
            )
        },
        risk_delta={
            "policy_errors": _number(
                candidate_verification.risk.get("policy_errors")
            )
            - _number(
                baseline_verification.risk.get("policy_errors")
            ),
            "severe_errors": _number(
                candidate_verification.risk.get("severe_errors")
            )
            - _number(
                baseline_verification.risk.get("severe_errors")
            ),
        },
        verification_delta={
            "pass_rate": _number(
                candidate_verification.metrics.get("pass_rate")
            )
            - _number(
                baseline_verification.metrics.get("pass_rate")
            )
        },
    )


def _require_candidate_benefit(
    report: TrustedExperimentReportV2,
) -> None:
    baseline_variant = next(
        item.variant_id for item in report.spec.variants if item.is_baseline
    )
    baseline = next(
        item for item in report.runs if item.variant_id == baseline_variant
    )
    candidate = next(
        item for item in report.runs if item.variant_id != baseline_variant
    )
    baseline_quality = _number(
        baseline.verification_receipt.outcome.get("quality")
    )
    candidate_quality = _number(
        candidate.verification_receipt.outcome.get("quality")
    )
    candidate_risk = candidate.verification_receipt.risk
    if (
        not all(run.benefit_eligible for run in report.runs)
        or candidate_quality - baseline_quality < 0.2
        or _number(candidate_risk.get("policy_errors")) != 0
        or _number(candidate_risk.get("severe_errors")) != 0
    ):
        raise GoldenExperimentServiceError(
            "candidate does not satisfy the frozen golden benefit gate"
        )


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GoldenExperimentServiceError(
            "golden metric is missing or non-numeric"
        )
    return float(value)


__all__ = [
    "GoldenEvidenceLedger",
    "GoldenExperimentService",
    "GoldenExperimentServiceError",
    "GoldenExperimentSuiteResult",
]
