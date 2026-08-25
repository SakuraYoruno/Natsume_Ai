"""V2 Evolution Lab execution boundary.

The boundary accepts a deliberately narrow set of Lab-only ports.  It does
not import production composition, activation, token, broker or Edge modules;
an executor must return a sandbox/effect-audit receipt before the runner will
persist a proposal-only V2 receipt.  This module is a structural isolation
boundary, not evidence that a real container has been started.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Protocol

from host_brain.contracts.evolution_lab import (
    DatasetManifestV1,
    ExperimentRunReceiptV2,
    ExperimentSpecV2,
    LabDatasetV1,
    PolicyVariantV1,
    SandboxReceiptV1,
    VerificationReceiptV1,
    canonical_evolution_lab_digest,
)


class EvolutionLabV2RunnerError(RuntimeError):
    """Raised before untrusted or unbound Lab evidence can become durable."""


class V2DatasetReader(Protocol):
    def read(self, *, manifest: DatasetManifestV1) -> LabDatasetV1: ...


class IsolatedLabExecutor(Protocol):
    def execute(
        self,
        *,
        execution_id: str,
        spec: ExperimentSpecV2,
        variant: PolicyVariantV1,
        dataset: LabDatasetV1,
    ) -> "LabSandboxExecutionV1": ...


class V2LabVerifier(Protocol):
    def verify(
        self,
        *,
        execution_id: str,
        spec: ExperimentSpecV2,
        variant: PolicyVariantV1,
        dataset: LabDatasetV1,
        model_output: Mapping[str, Any],
    ) -> VerificationReceiptV1: ...


class ExperimentV2Repository(Protocol):
    def register_spec_v2(self, spec: ExperimentSpecV2) -> str: ...

    def append_execution_and_run(self, receipt: ExperimentRunReceiptV2) -> str: ...


def _nonnegative_mapping(value: Mapping[str, Any], field: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise EvolutionLabV2RunnerError(f"{field} must be a mapping")
    normalized: dict[str, int] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        if not key or len(key) > 120:
            raise EvolutionLabV2RunnerError(f"{field} has an invalid key")
        if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value < 0:
            raise EvolutionLabV2RunnerError(f"{field}[{key}] must be a non-negative integer")
        normalized[key] = raw_value
    return normalized


@dataclass(frozen=True, slots=True)
class LabSandboxExecutionV1:
    """Ephemeral isolated output plus the evidence that bound its execution."""

    execution_id: str
    model_identity: str
    usage: Mapping[str, int]
    cost: Mapping[str, int]
    latency_ms: int
    output: Mapping[str, Any]
    sandbox_receipt: SandboxReceiptV1

    def __post_init__(self) -> None:
        execution_id = str(self.execution_id or "").strip()
        if not execution_id or len(execution_id) > 160:
            raise EvolutionLabV2RunnerError("execution_id must be non-empty and bounded")
        object.__setattr__(self, "execution_id", execution_id)
        model_identity = str(self.model_identity or "").strip()
        if not model_identity or len(model_identity) > 240:
            raise EvolutionLabV2RunnerError("model_identity must be non-empty and bounded")
        object.__setattr__(self, "model_identity", model_identity)
        object.__setattr__(self, "usage", _nonnegative_mapping(self.usage, "usage"))
        object.__setattr__(self, "cost", _nonnegative_mapping(self.cost, "cost"))
        if isinstance(self.latency_ms, bool) or not isinstance(self.latency_ms, int) or self.latency_ms < 0:
            raise EvolutionLabV2RunnerError("latency_ms must be a non-negative integer")
        if not isinstance(self.output, Mapping):
            raise EvolutionLabV2RunnerError("isolated executor output must be a mapping")
        try:
            output = json.loads(
                json.dumps(self.output, ensure_ascii=False, sort_keys=True, allow_nan=False)
            )
        except (TypeError, ValueError) as exc:
            raise EvolutionLabV2RunnerError("isolated executor output must be JSON-safe") from exc
        if not isinstance(output, Mapping):
            raise EvolutionLabV2RunnerError("isolated executor output must remain a mapping")
        object.__setattr__(self, "output", output)
        output_digest = canonical_evolution_lab_digest({"output": output})
        if not isinstance(self.sandbox_receipt, SandboxReceiptV1):
            raise EvolutionLabV2RunnerError("isolated executor requires a SandboxReceiptV1")
        if self.sandbox_receipt.execution_id != execution_id:
            raise EvolutionLabV2RunnerError("sandbox receipt execution identity drift")
        if self.sandbox_receipt.output_digest != output_digest:
            raise EvolutionLabV2RunnerError("sandbox receipt output digest drift")


@dataclass(frozen=True, slots=True)
class EvolutionLabV2PortSet:
    """The only ports exposed to the V2 Lab runner.

    Isolation is enforced by this closed API and the sandbox/effect receipts,
    rather than by introspecting arbitrary port attributes by name.
    """

    dataset_reader: V2DatasetReader
    executor: IsolatedLabExecutor
    verifier: V2LabVerifier
    repository: ExperimentV2Repository

    def validate(self) -> None:
        for name, port in (
            ("dataset_reader", self.dataset_reader),
            ("executor", self.executor),
            ("verifier", self.verifier),
            ("repository", self.repository),
        ):
            if port is None:
                raise EvolutionLabV2RunnerError(f"{name} port is required")


class EvolutionLabV2Runner:
    """Run one V2 Lab arm and durably append only no-effect evidence."""

    def __init__(self, *, ports: EvolutionLabV2PortSet) -> None:
        if not isinstance(ports, EvolutionLabV2PortSet):
            raise TypeError("ports must be EvolutionLabV2PortSet")
        ports.validate()
        self._ports = ports

    def run_variant(self, *, spec: ExperimentSpecV2, variant_id: str) -> ExperimentRunReceiptV2:
        if not isinstance(spec, ExperimentSpecV2):
            raise TypeError("spec must be ExperimentSpecV2")
        try:
            variant = spec.variant(variant_id)
        except ValueError as exc:
            raise EvolutionLabV2RunnerError("variant is not registered in ExperimentSpecV2") from exc
        self._ports.repository.register_spec_v2(spec)

        dataset = self._ports.dataset_reader.read(manifest=spec.dataset_manifest)
        if not isinstance(dataset, LabDatasetV1):
            raise EvolutionLabV2RunnerError("V2 DatasetReader must return LabDatasetV1")
        if (
            dataset.dataset_id != spec.dataset_manifest.dataset_id
            or dataset.dataset_digest != spec.dataset_manifest.dataset_digest
            or len(dataset.records) != spec.dataset_manifest.record_count
        ):
            raise EvolutionLabV2RunnerError("dataset reader result drifts from the frozen manifest")

        execution_id = _execution_id(spec, variant)
        execution = self._ports.executor.execute(
            execution_id=execution_id,
            spec=spec,
            variant=variant,
            dataset=dataset,
        )
        if not isinstance(execution, LabSandboxExecutionV1):
            raise EvolutionLabV2RunnerError("isolated executor must return LabSandboxExecutionV1")
        if execution.execution_id != execution_id:
            raise EvolutionLabV2RunnerError("isolated executor execution identity drift")
        if (
            execution.sandbox_receipt.dataset_digest != spec.dataset_manifest.dataset_digest
            or execution.sandbox_receipt.sandbox_profile_digest != spec.isolation_profile_digest
        ):
            raise EvolutionLabV2RunnerError("sandbox receipt does not bind the frozen dataset/profile")

        verification = self._ports.verifier.verify(
            execution_id=execution_id,
            spec=spec,
            variant=variant,
            dataset=dataset,
            model_output=execution.output,
        )
        if not isinstance(verification, VerificationReceiptV1):
            raise EvolutionLabV2RunnerError("V2 LabVerifier must return VerificationReceiptV1")
        if verification.execution_id != execution_id:
            raise EvolutionLabV2RunnerError("verifier execution identity drift")
        if (
            verification.verifier_identity != spec.verifier_identity
            or verification.verifier_version != spec.verifier_version
            or verification.verifier_digest != spec.verifier_digest
        ):
            raise EvolutionLabV2RunnerError("verifier identity drift from ExperimentSpecV2")

        receipt = ExperimentRunReceiptV2(
            run_id=_run_id(spec, variant),
            execution_id=execution_id,
            spec_digest=spec.spec_digest,
            workspace_id=spec.workspace_id,
            dataset_manifest_digest=spec.dataset_manifest.manifest_digest,
            variant_id=variant.variant_id,
            status=verification.status,
            model_identity=execution.model_identity,
            usage=execution.usage,
            cost=execution.cost,
            latency_ms=execution.latency_ms,
            sandbox_receipt=execution.sandbox_receipt,
            verification_receipt=verification,
        )
        self._ports.repository.append_execution_and_run(receipt)
        return receipt


def _execution_id(spec: ExperimentSpecV2, variant: PolicyVariantV1) -> str:
    seed_digest = canonical_evolution_lab_digest(
        {
            "spec_digest": spec.spec_digest,
            "variant_id": variant.variant_id,
            "seed": spec.seed,
        }
    )
    return f"labv2-exec-{seed_digest[:48]}"


def _run_id(spec: ExperimentSpecV2, variant: PolicyVariantV1) -> str:
    return _execution_id(spec, variant).replace("labv2-exec-", "labv2-run-", 1)


__all__ = [
    "EvolutionLabV2PortSet",
    "EvolutionLabV2Runner",
    "EvolutionLabV2RunnerError",
    "ExperimentV2Repository",
    "IsolatedLabExecutor",
    "LabSandboxExecutionV1",
    "V2DatasetReader",
    "V2LabVerifier",
]
