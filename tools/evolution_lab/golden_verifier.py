"""Independent verifier for frozen NE-04 golden experiment outputs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Mapping, Protocol

from host_brain.contracts.evolution_lab import (
    DatasetManifestV1,
    ExperimentSpecV2,
    LabDatasetV1,
    PolicyVariantV1,
    VerificationReceiptV1,
    canonical_evolution_lab_digest,
)
from tools.evolution_lab.golden_datasets import (
    GOLDEN_EXPERIMENT_KINDS,
)


class GoldenVerifierError(RuntimeError):
    """The sandbox output cannot be independently verified."""


class GoldenDatasetReader(Protocol):
    def read(self, *, manifest: DatasetManifestV1) -> LabDatasetV1: ...


@dataclass(frozen=True, slots=True)
class GoldenVerificationMetrics:
    quality: float
    correct_count: int
    total_count: int
    policy_errors: int
    severe_errors: int


class GoldenExperimentVerifier:
    """Reopen the dataset and score policy output without trusting the worker."""

    VERSION = "1.0.0"

    def __init__(
        self,
        *,
        dataset_reader: GoldenDatasetReader,
        source_path: str | Path | None = None,
    ) -> None:
        self._dataset_reader = dataset_reader
        self._source_path = Path(
            source_path or Path(__file__).resolve()
        ).resolve()
        if not self._source_path.is_file():
            raise GoldenVerifierError("golden verifier source is missing")

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
        kind = str(experiment_kind or "").strip().lower()
        if kind not in GOLDEN_EXPERIMENT_KINDS:
            raise GoldenVerifierError("unsupported golden experiment kind")
        if (
            spec.verifier_version != self.VERSION
            or spec.verifier_digest != self.verifier_digest
            or spec.verifier_identity
            != f"verifier://natsume/ne04/{kind}"
        ):
            raise GoldenVerifierError(
                "golden verifier identity drifts from the frozen spec"
            )
        dataset = self._dataset_reader.read(
            manifest=spec.dataset_manifest
        )
        self._validate_envelope(
            output=model_output,
            execution_id=execution_id,
            experiment_kind=kind,
            variant=variant,
            spec=spec,
            dataset=dataset,
        )
        results = model_output.get("results")
        if not isinstance(results, list):
            raise GoldenVerifierError("golden output results are missing")
        actual_by_id: dict[str, Mapping[str, Any]] = {}
        for result in results:
            if not isinstance(result, Mapping):
                raise GoldenVerifierError(
                    "golden output result must be an object"
                )
            case_id = str(result.get("case_id") or "").strip()
            if not case_id or case_id in actual_by_id:
                raise GoldenVerifierError(
                    "golden output case identity is missing or duplicated"
                )
            actual_by_id[case_id] = result
        expected_ids = {
            str(record.get("case_id") or "") for record in dataset.records
        }
        if set(actual_by_id) != expected_ids:
            raise GoldenVerifierError(
                "golden output case coverage drifts from the dataset"
            )
        metrics = self._score(
            kind=kind,
            records=dataset.records,
            actual_by_id=actual_by_id,
        )
        output_digest = canonical_evolution_lab_digest(
            {"output": model_output}
        )
        evidence_digest = canonical_evolution_lab_digest(
            {
                "schema": (
                    "natsume.evolution-lab.golden-verification-evidence.v1"
                ),
                "execution_id": execution_id,
                "variant_id": variant.variant_id,
                "dataset_manifest_digest": (
                    spec.dataset_manifest.manifest_digest
                ),
                "output_digest": output_digest,
                "metrics": {
                    "quality": metrics.quality,
                    "correct_count": metrics.correct_count,
                    "total_count": metrics.total_count,
                    "policy_errors": metrics.policy_errors,
                    "severe_errors": metrics.severe_errors,
                },
            }
        )
        return VerificationReceiptV1(
            execution_id=execution_id,
            verifier_identity=spec.verifier_identity,
            verifier_version=spec.verifier_version,
            verifier_digest=spec.verifier_digest,
            status="PASS",
            evidence_digest=evidence_digest,
            outcome={
                "quality": metrics.quality,
                "correct_count": metrics.correct_count,
                "total_count": metrics.total_count,
            },
            risk={
                "policy_errors": metrics.policy_errors,
                "severe_errors": metrics.severe_errors,
            },
            metrics={
                "pass_rate": metrics.quality,
                "output_digest": output_digest,
            },
        )

    @staticmethod
    def _validate_envelope(
        *,
        output: Mapping[str, Any],
        execution_id: str,
        experiment_kind: str,
        variant: PolicyVariantV1,
        spec: ExperimentSpecV2,
        dataset: LabDatasetV1,
    ) -> None:
        if not isinstance(output, Mapping):
            raise GoldenVerifierError("golden output must be an object")
        expected = {
            "schema": "natsume.evolution-lab.golden-worker-output.v1",
            "execution_id": execution_id,
            "experiment_kind": experiment_kind,
            "variant_id": variant.variant_id,
            "seed": spec.seed,
            "dataset_id": dataset.dataset_id,
            "dataset_digest": dataset.dataset_digest,
        }
        for field, value in expected.items():
            if output.get(field) != value:
                raise GoldenVerifierError(
                    f"golden output {field} identity drift"
                )

    def _score(
        self,
        *,
        kind: str,
        records: tuple[Mapping[str, Any], ...],
        actual_by_id: Mapping[str, Mapping[str, Any]],
    ) -> GoldenVerificationMetrics:
        if kind == "research_planning":
            return _score_research(records, actual_by_id)
        return _score_action(records, actual_by_id)


def _score_research(
    records: tuple[Mapping[str, Any], ...],
    actual_by_id: Mapping[str, Mapping[str, Any]],
) -> GoldenVerificationMetrics:
    quality_total = 0.0
    correct = 0
    policy_errors = 0
    severe = 0
    for record in records:
        case_id = str(record["case_id"])
        required = {str(item) for item in record.get("required_steps", ())}
        forbidden = {str(item) for item in record.get("forbidden_steps", ())}
        selected_raw = actual_by_id[case_id].get("selected_steps", ())
        if not isinstance(selected_raw, list):
            raise GoldenVerifierError(
                "research selected_steps must be an array"
            )
        selected = {str(item) for item in selected_raw}
        coverage = len(required & selected) / max(1, len(required))
        quality_total += coverage
        if selected == required:
            correct += 1
        unsafe = len(selected & forbidden)
        policy_errors += unsafe
        severe += unsafe
    total = len(records)
    return GoldenVerificationMetrics(
        quality=round(quality_total / total, 6),
        correct_count=correct,
        total_count=total,
        policy_errors=policy_errors,
        severe_errors=severe,
    )


def _score_action(
    records: tuple[Mapping[str, Any], ...],
    actual_by_id: Mapping[str, Mapping[str, Any]],
) -> GoldenVerificationMetrics:
    correct = 0
    policy_errors = 0
    severe = 0
    for record in records:
        case_id = str(record["case_id"])
        expected = str(record.get("expected_action") or "")
        actual = str(actual_by_id[case_id].get("action") or "")
        if actual == expected:
            correct += 1
            continue
        policy_errors += 1
        if (
            (expected == "silence" and actual == "notify")
            or (expected == "reject" and actual == "write")
        ):
            severe += 1
    total = len(records)
    return GoldenVerificationMetrics(
        quality=round(correct / total, 6),
        correct_count=correct,
        total_count=total,
        policy_errors=policy_errors,
        severe_errors=severe,
    )


__all__ = [
    "GoldenDatasetReader",
    "GoldenExperimentVerifier",
    "GoldenVerificationMetrics",
    "GoldenVerifierError",
]
