"""Trusted NE-04 arm runner with durable reservation and exact replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from host_brain.contracts.evolution_lab import (
    DatasetManifestV1,
    ExperimentSpecV2,
    LabDatasetV1,
    PolicyVariantV1,
    VerificationReceiptV1,
)
from host_brain.contracts.evolution_lab_trusted import (
    TrustedExperimentRunReceiptV2,
)
from host_brain.evolution_lab.docker_executor import (
    TrustedDockerExecution,
)
from host_brain.persistence.evolution_lab_trusted_repository import (
    TrustedExecutionClaim,
)


class TrustedEvolutionLabRunnerError(RuntimeError):
    """A trusted arm cannot safely start, complete, replay, or reconcile."""


class TrustedDatasetReader(Protocol):
    def read(self, *, manifest: DatasetManifestV1) -> LabDatasetV1: ...


class TrustedDockerExecutorPort(Protocol):
    def execute(
        self,
        *,
        claim: TrustedExecutionClaim,
        experiment_kind: str,
        spec: ExperimentSpecV2,
        variant: PolicyVariantV1,
        dataset: LabDatasetV1,
    ) -> TrustedDockerExecution: ...

    def cleanup(self, *, claim: TrustedExecutionClaim) -> bool: ...


class TrustedVerifierPort(Protocol):
    def verify(
        self,
        *,
        execution_id: str,
        experiment_kind: str,
        spec: ExperimentSpecV2,
        variant: PolicyVariantV1,
        model_output: Mapping[str, Any],
    ) -> VerificationReceiptV1: ...


class TrustedSpecRegistry(Protocol):
    def register_spec_v2(self, spec: ExperimentSpecV2) -> str: ...


class TrustedOperationLedger(Protocol):
    def reserve_execution(
        self,
        *,
        spec: ExperimentSpecV2,
        variant_id: str,
        owner_id: str,
        lease_ms: int = 120_000,
    ) -> TrustedExecutionClaim: ...

    def complete_execution(
        self,
        *,
        claim: TrustedExecutionClaim,
        receipt: TrustedExperimentRunReceiptV2,
    ) -> TrustedExperimentRunReceiptV2: ...

    def mark_reconciliation_required(
        self,
        *,
        claim: TrustedExecutionClaim,
        reason_code: str,
    ) -> TrustedExecutionClaim: ...


@dataclass(frozen=True, slots=True)
class TrustedEvolutionLabPorts:
    dataset_reader: TrustedDatasetReader
    executor: TrustedDockerExecutorPort
    verifier: TrustedVerifierPort
    spec_registry: TrustedSpecRegistry
    operation_ledger: TrustedOperationLedger

    def validate(self) -> None:
        for name in (
            "dataset_reader",
            "executor",
            "verifier",
            "spec_registry",
            "operation_ledger",
        ):
            if getattr(self, name) is None:
                raise TrustedEvolutionLabRunnerError(
                    f"trusted Evolution Lab port {name} is required"
                )


class TrustedEvolutionLabRunner:
    """Run one exact arm; an ambiguous external effect is never replayed."""

    def __init__(self, *, ports: TrustedEvolutionLabPorts) -> None:
        if not isinstance(ports, TrustedEvolutionLabPorts):
            raise TypeError("ports must be TrustedEvolutionLabPorts")
        ports.validate()
        self._ports = ports

    def run_variant(
        self,
        *,
        experiment_kind: str,
        spec: ExperimentSpecV2,
        variant_id: str,
        owner_id: str,
        lease_ms: int = 120_000,
    ) -> TrustedExperimentRunReceiptV2:
        if not isinstance(spec, ExperimentSpecV2):
            raise TypeError("spec must be ExperimentSpecV2")
        try:
            variant = spec.variant(variant_id)
        except ValueError as exc:
            raise TrustedEvolutionLabRunnerError(
                "variant is not registered in the trusted experiment spec"
            ) from exc
        self._ports.spec_registry.register_spec_v2(spec)
        dataset = self._ports.dataset_reader.read(
            manifest=spec.dataset_manifest
        )
        self._assert_dataset(spec=spec, dataset=dataset)
        claim = self._ports.operation_ledger.reserve_execution(
            spec=spec,
            variant_id=variant.variant_id,
            owner_id=owner_id,
            lease_ms=lease_ms,
        )
        replay = self._reopen_completed(claim)
        if replay is not None:
            self._require_cleanup(claim)
            return replay
        if not claim.execution_authorized:
            raise TrustedEvolutionLabRunnerError(
                "trusted operation is already running or requires reconciliation; "
                "blind execution is forbidden"
            )
        try:
            execution = self._ports.executor.execute(
                claim=claim,
                experiment_kind=experiment_kind,
                spec=spec,
                variant=variant,
                dataset=dataset,
            )
            self._assert_execution(claim=claim, execution=execution)
            verification = self._ports.verifier.verify(
                execution_id=claim.execution_id,
                experiment_kind=experiment_kind,
                spec=spec,
                variant=variant,
                model_output=execution.output,
            )
            self._assert_verification(
                execution_id=claim.execution_id,
                spec=spec,
                verification=verification,
            )
            receipt = TrustedExperimentRunReceiptV2(
                run_id=_run_id(claim.execution_id),
                execution_id=claim.execution_id,
                spec_digest=spec.spec_digest,
                workspace_id=spec.workspace_id,
                dataset_manifest_digest=(
                    spec.dataset_manifest.manifest_digest
                ),
                variant_id=variant.variant_id,
                status=verification.status,
                model_identity=execution.model_identity,
                usage=execution.usage,
                cost=execution.cost,
                latency_ms=execution.latency_ms,
                sandbox_attestation=execution.sandbox_attestation,
                verification_receipt=verification,
            )
            completed = self._ports.operation_ledger.complete_execution(
                claim=claim,
                receipt=receipt,
            )
        except Exception as exc:
            recovered = self._recover_response_loss(
                claim=claim,
                spec=spec,
                owner_id=owner_id,
                lease_ms=lease_ms,
            )
            if recovered is not None:
                self._require_cleanup(claim)
                return recovered
            try:
                self._ports.operation_ledger.mark_reconciliation_required(
                    claim=claim,
                    reason_code="TRUSTED_SANDBOX_RESULT_AMBIGUOUS",
                )
            except Exception as reconciliation_exc:
                raise TrustedEvolutionLabRunnerError(
                    "trusted execution failed and reconciliation could not be persisted"
                ) from reconciliation_exc
            raise TrustedEvolutionLabRunnerError(
                "trusted execution failed; durable operation now requires reconciliation"
            ) from exc
        self._require_cleanup(claim)
        return completed

    @staticmethod
    def _assert_dataset(
        *,
        spec: ExperimentSpecV2,
        dataset: LabDatasetV1,
    ) -> None:
        if (
            not isinstance(dataset, LabDatasetV1)
            or dataset.dataset_id != spec.dataset_manifest.dataset_id
            or dataset.dataset_digest != spec.dataset_manifest.dataset_digest
            or len(dataset.records) != spec.dataset_manifest.record_count
        ):
            raise TrustedEvolutionLabRunnerError(
                "dataset reader result drifts from the frozen manifest"
            )

    @staticmethod
    def _assert_execution(
        *,
        claim: TrustedExecutionClaim,
        execution: TrustedDockerExecution,
    ) -> None:
        if not isinstance(execution, TrustedDockerExecution):
            raise TrustedEvolutionLabRunnerError(
                "trusted executor returned an unsupported result"
            )
        if (
            execution.execution_id != claim.execution_id
            or execution.sandbox_attestation.execution_id
            != claim.execution_id
        ):
            raise TrustedEvolutionLabRunnerError(
                "trusted executor result identity drift"
            )

    @staticmethod
    def _assert_verification(
        *,
        execution_id: str,
        spec: ExperimentSpecV2,
        verification: VerificationReceiptV1,
    ) -> None:
        if not isinstance(verification, VerificationReceiptV1):
            raise TrustedEvolutionLabRunnerError(
                "trusted verifier returned an unsupported receipt"
            )
        if (
            verification.execution_id != execution_id
            or verification.verifier_identity != spec.verifier_identity
            or verification.verifier_version != spec.verifier_version
            or verification.verifier_digest != spec.verifier_digest
        ):
            raise TrustedEvolutionLabRunnerError(
                "trusted verifier identity drifts from the frozen spec"
            )

    @staticmethod
    def _reopen_completed(
        claim: TrustedExecutionClaim,
    ) -> TrustedExperimentRunReceiptV2 | None:
        if claim.status == "COMPLETED":
            if not claim.replayed or claim.receipt is None:
                raise TrustedEvolutionLabRunnerError(
                    "completed trusted operation lacks its exact replay receipt"
                )
            return claim.receipt
        return None

    def _recover_response_loss(
        self,
        *,
        claim: TrustedExecutionClaim,
        spec: ExperimentSpecV2,
        owner_id: str,
        lease_ms: int,
    ) -> TrustedExperimentRunReceiptV2 | None:
        reopened = self._ports.operation_ledger.reserve_execution(
            spec=spec,
            variant_id=claim.variant_id,
            owner_id=owner_id,
            lease_ms=lease_ms,
        )
        return self._reopen_completed(reopened)

    def _require_cleanup(self, claim: TrustedExecutionClaim) -> None:
        try:
            cleaned = self._ports.executor.cleanup(claim=claim)
        except Exception as exc:
            raise TrustedEvolutionLabRunnerError(
                "trusted receipt is durable but sandbox cleanup failed; exact replay can retry cleanup"
            ) from exc
        if not cleaned:
            raise TrustedEvolutionLabRunnerError(
                "trusted receipt is durable but sandbox cleanup remains pending"
            )


def _run_id(execution_id: str) -> str:
    if execution_id.startswith("lab-trusted-exec-"):
        return execution_id.replace(
            "lab-trusted-exec-", "lab-trusted-run-", 1
        )
    return f"lab-trusted-run-{execution_id}"


__all__ = [
    "TrustedDatasetReader",
    "TrustedDockerExecutorPort",
    "TrustedEvolutionLabPorts",
    "TrustedEvolutionLabRunner",
    "TrustedEvolutionLabRunnerError",
    "TrustedOperationLedger",
    "TrustedSpecRegistry",
    "TrustedVerifierPort",
]
